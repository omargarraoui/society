"""Telegram bot: the user's private window on one world. It obeys only TELEGRAM_CHAT_ID.

  python bot.py run WORLD     # long-polls Telegram, sends alerts and the evening digest
  python bot.py chat-id       # prints the chat ids that wrote to the bot (setup only)
"""
import argparse
import datetime
import json
import os
import time
import urllib.parse
import urllib.request
import zoneinfo
from collections import Counter

import kubernetes

import config
import llm
from db import DB, quota_db_path, world_db_path
from state import load_agents, load_k8s_config

HELP = "Comandi: /stato, /pausa, /riprendi, /annulla <numero della sentenza>"
SKIP_ALERT_SECONDS = 3600


def telegram(token, method, **params):
    # The token is in the URL: urllib's errors carry only status and reason, never the URL.
    url = f"https://api.telegram.org/bot{token}/{method}"
    with urllib.request.urlopen(url, data=urllib.parse.urlencode(params).encode(), timeout=70) as response:
        body = json.load(response)
    if not body["ok"]:
        raise RuntimeError(f"telegram {method} failed: {body.get('description')}")
    return body["result"]


def status_text(world, db, quota, agents):
    tick = db.get("tick", 0)
    lines = [f"Mondo {world}: {llm.clock(tick)} (tick {tick})" + (", IN PAUSA" if db.get("paused", False) else "")]
    for a in agents.values():
        spouse = f", sposato con {a['spouse'].capitalize()}" if a["spouse"] else ""
        lines.append(f"- {a['name'].capitalize()} ({a['model']}){spouse}, promesse: {len(a['promises'])}")
    lines.append("Quote di oggi:")
    for model in sorted({a["model"] for a in agents.values()}):
        requests, tokens = quota.usage(model)
        lines.append(f"- {model}: {requests}/{config.PROVIDERS[model]['daily_requests']} richieste, {tokens} token")
    recent = db.q("select tick, kind, agent, data from events order by id desc limit 5")[::-1]
    lines.append("Ultimi eventi:")
    lines += [f"- {llm.describe(e, '')}" if e["kind"] in llm.VERBS else f"- {e['agent']}: {e['kind']}"
              for e in recent]
    return "\n".join(lines)


def digest_text(world, db, since_id):
    rows = db.q("select id, tick, kind, agent, data from events where id>? order by id", (since_id,))
    if not rows:
        return f"Digest {world}: nessun evento.", since_id
    kinds = Counter(r["kind"] for r in rows)
    lines = [f"Digest {world}, da {llm.clock(rows[0]['tick'])} a {llm.clock(rows[-1]['tick'])}",
             "Azioni: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common())]
    for r in rows:
        if r["kind"] in ("promise", "accept_marriage", "assess", "petition", *config.GOD_EVENTS):
            lines.append(f"- {llm.describe(r, '')}")
    return "\n".join(lines), rows[-1]["id"]


def alert_texts(rows):
    """Events worth a notification right away. Skips are counted, not listed."""
    texts = []
    for r in rows:
        data = json.loads(r["data"])
        if r["kind"] == "accept_marriage":
            texts.append(f"Matrimonio: {r['agent'].capitalize()} e {data['target'].capitalize()}")
        elif r["kind"] == "steal":
            texts.append(f"Furto: {r['agent'].capitalize()} ha rubato a {data['target'].capitalize()}")
        elif r["kind"] == "appoint":
            texts.append(f"Dio ha nominato rappresentante {data['target'].capitalize()}")
        elif r["kind"] == "sentence" and "decision" in data:
            length = f" per {data['days']} giorni" if data.get("days") else ""
            texts.append(f"Sentenza #{data['decision']}: {data['target'].capitalize()}, {data['penalty']}{length}. "
                         f"Motivo: {data['reason']}\nDiventa effettiva tra 24 ore. Per annullarla: "
                         f"/annulla {data['decision']}")
        elif r["kind"] == "sentence":
            texts.append(f"Dio a {data['target'].capitalize()}: {data['penalty']}. Motivo: {data['reason']}")
        elif r["kind"] == "penalty":
            texts.append(f"Eseguita la sentenza #{data['decision']}: {data['target'].capitalize()}, {data['penalty']}")
        elif r["kind"] == "anomaly":
            texts.append(f"ALLARME: {data['reason']}. Proposta: {data['sentence']}. /riprendi per ripartire.")
    skips = Counter(json.loads(r["data"])["reason"] for r in rows if r["kind"] == "skip")
    return texts, skips


def pardon(db, decision):
    row = db.one("select target, kind, status from decisions where id=?", (decision,))
    if row is None or row["status"] != "pending":
        return f"Nessuna sentenza #{decision} in attesa."
    db.x("update decisions set status='vetoed' where id=?", (decision,))
    db.log("pardon", "god", target=row["target"], penalty=row["kind"], decision=decision)
    return f"Sentenza #{decision} annullata: {row['target'].capitalize()} non subira' {row['kind']}."


def handle(text, world, db, quota, agents):
    words = text.strip().split()
    command = words[0].split("@")[0] if words else ""
    if command == "/annulla":
        if len(words) != 2 or not words[1].lstrip("#").isdigit():
            return "Uso: /annulla <numero della sentenza>, per esempio /annulla 3"
        return pardon(db, int(words[1].lstrip("#")))
    if command == "/stato":
        return status_text(world, db, quota, agents)
    if command == "/pausa":
        db.set("paused", True)
        return "Mondo in pausa: il prossimo tick non parte. /riprendi per ripartire."
    if command == "/riprendi":
        db.set("paused", False)
        return "Mondo ripartito."
    return HELP


def digest_due(db, now):
    return now.hour >= config.DIGEST_HOUR and db.get("digest_day") != now.strftime("%Y-%m-%d")


def run(world, token, chat_id):
    load_k8s_config()
    api = kubernetes.client.CustomObjectsApi()
    db, quota = DB(world_db_path(world)), DB(quota_db_path())
    if db.get("alert_event_id") is None:
        # First start: alert only on what happens from now on.
        db.set("alert_event_id", (db.one("select max(id) as m from events")["m"] or 0))

    def send(text):
        telegram(token, "sendMessage", chat_id=chat_id, text=text[:4000])

    offset = 0
    while True:
        for update in telegram(token, "getUpdates", timeout=50, offset=offset):
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            if str(message.get("chat", {}).get("id")) == chat_id:
                send(handle(message.get("text", ""), world, db, quota, load_agents(api, world)))

        rows = db.q("select id, tick, kind, agent, data from events where id>? order by id",
                    (db.get("alert_event_id"),))
        if rows:
            texts, skips = alert_texts(rows)
            for text in texts:
                send(text)
            if skips and time.time() - db.get("skip_alert_at", 0) > SKIP_ALERT_SECONDS:
                send("Turni saltati: " + ", ".join(f"{n} ({reason})" for reason, n in skips.items()))
                db.set("skip_alert_at", time.time())
            db.set("alert_event_id", rows[-1]["id"])

        now = datetime.datetime.now(zoneinfo.ZoneInfo(config.DIGEST_TZ))
        if digest_due(db, now):
            text, last_id = digest_text(world, db, db.get("digest_event_id", 0))
            send(text)
            db.set("digest_event_id", last_id)
            db.set("digest_day", now.strftime("%Y-%m-%d"))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").add_argument("world")
    sub.add_parser("chat-id")
    args = parser.parse_args()
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    if args.cmd == "chat-id":
        for update in telegram(token, "getUpdates"):
            chat = update.get("message", {}).get("chat", {})
            print(chat.get("id"), chat.get("first_name"), chat.get("username"))
    else:
        run(args.world, token, os.environ["TELEGRAM_CHAT_ID"])


if __name__ == "__main__":
    main()
