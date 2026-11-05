"""The public dashboard: a snapshot of one world as data.json, published to a GitHub Pages repo.

  python export.py data WORLD [--out FILE]   # FILE for a local preview; without it, push to GITHUB_REPO
  python export.py site                      # push site/ (after changes to the page)
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import pathlib
import urllib.error
import urllib.request
from collections import Counter

import kubernetes

import births
import config
from db import DB, quota_db_path, world_db_path
from metrics import event_rows, segregation
from state import load_agents, load_k8s_config
from world import acting_models, turn_tokens, world_days_per_day

FEED_LENGTH = 400


def promise_view(promise, judgments):
    kept = judgments.get(promise["metadata"]["name"], [])
    status = promise.get("status", {})
    return {"id": promise["metadata"]["name"], "body": promise["spec"]["body"],
            "refrains": promise["spec"].get("refrains"), "tick": promise["spec"]["tick"],
            "state": status.get("state"), "violations": status.get("violations"),
            "judged_kept": kept.count(1), "judged_broken": kept.count(0)}


def snapshot(world, db, agents, now, speed):
    """speed: world days per real day, as the world loop paces itself."""
    tick = db.get("tick", 0)
    judgments = {}
    for r in db.q("select json_extract(data, '$.promise') as p, json_extract(data, '$.kept') as k from events "
                  "where kind='assess'"):
        judgments.setdefault(r["p"], []).append(r["k"])
    people = [{
        "name": a["name"], "sex": a["sex"], "model": a["model"], "alive": a["alive"], "food": a["food"],
        "spouse": a["spouse"], "role": a["role"], "parents": a["parents"], "born": a["born"],
        "child": not births.adult(a, tick), "exiled": a["exiled_until"] > tick,
        "warnings": a["warnings"], "marks": a["marks"],
        "promises": [promise_view(p, judgments) for p in a["promises"]],
    } for a in sorted(agents.values(), key=lambda a: (a["born"] or 0, a["name"]))]
    marks = ",".join("?" * len(config.ROUTINE_EVENTS))
    feed = [{"id": r["id"], "tick": r["tick"], "kind": r["kind"], "agent": r["agent"],
             "data": {k: v for k, v in json.loads(r["data"]).items() if k != "saw"}}
            for r in db.q(f"select id, tick, kind, agent, data from events where kind not in ({marks}) "
                          "order by id desc limit ?", (*config.ROUTINE_EVENTS, FEED_LENGTH))]
    totals = Counter(r["kind"] for r in db.q("select kind from events"))
    living = [a for a in agents.values() if a["alive"]]
    return {
        "world": world, "updated": now, "tick": tick, "paused": db.get("paused", False), "speed": speed,
        "calendar": {"ticks_per_day": config.TICKS_PER_DAY, "days_per_week": config.DAYS_PER_WEEK,
                     "rest_day": config.REST_DAY},
        "people": people,
        "feed": feed,
        "totals": dict(totals),
        "groups": dict(Counter(a["group"] for a in living)),
        "segregation": segregation(event_rows(db), list(agents.values())),
        "decisions": [dict(r) for r in db.q("select tick, kind, target, reason, status from decisions order by id")],
    }


def versioned(index, files):
    """GitHub Pages lets browsers cache files for 10 minutes: a content hash in the URL makes a changed
    app.js or style.css load at once."""
    for name, content in files.items():
        index = index.replace(f'"{name}"', f'"{name}?v={hashlib.sha256(content).hexdigest()[:8]}"')
    return index


def github(token, method, url, body=None):
    request = urllib.request.Request(f"https://api.github.com{url}", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {token}",
                                              "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response) if response.status != 204 else None


def put_file(repo, token, path, content, message):
    try:
        sha = github(token, "GET", f"/repos/{repo}/contents/{path}")["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        sha = None  # a new file
    github(token, "PUT", f"/repos/{repo}/contents/{path}", {
        "message": message, "content": base64.b64encode(content).decode(), **({"sha": sha} if sha else {})})


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    data = sub.add_parser("data")
    data.add_argument("world")
    data.add_argument("--out")
    sub.add_parser("site")
    args = parser.parse_args()

    if args.cmd == "site":
        repo, token = os.environ["GITHUB_REPO"], os.environ["GITHUB_TOKEN"]
        # A local preview leaves a data.json in site/; the real one comes only from the world.
        site = {p.name: p.read_bytes() for p in pathlib.Path("site").iterdir() if p.name != "data.json"}
        site["index.html"] = versioned(site["index.html"].decode(), {n: site[n] for n in ("app.js", "style.css")}
                                       ).encode()
        for name, content in sorted(site.items()):
            put_file(repo, token, name, content, f"Pagina: {name}")
        # Pages is switched on by hand once (Settings > Pages): doing it through the API needs a token with
        # repository administration rights, too much for a token that lives on the server.
        print(f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/")
        return

    load_k8s_config()
    agents = load_agents(kubernetes.client.CustomObjectsApi(), args.world)
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    quota = DB(quota_db_path())
    db = DB(world_db_path(args.world))
    models = acting_models(agents, db.get("tick", 0))
    speed = world_days_per_day(models, {m: turn_tokens(quota, m) for m in models})
    content = json.dumps(snapshot(args.world, db, agents, now, speed),
                         ensure_ascii=False)
    if args.out:
        pathlib.Path(args.out).write_text(content)
    else:
        put_file(os.environ["GITHUB_REPO"], os.environ["GITHUB_TOKEN"], "data.json", content.encode(),
                 f"Dati: {args.world}, tick {json.loads(content)['tick']}")


if __name__ == "__main__":
    main()
