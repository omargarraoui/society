"""The agent-God: once a world day, at dawn, it reads the record and decides within the constitution.

Like the agents' actions, its answer passes through hard rules in code: only the penalties in
config.PENALTIES, exile and death delayed by VETO_SECONDS (the user can pardon), and the world
paused when a decision looks anomalous.
"""
import random
import time

import births
import config
import food
import llm
from state import patch_status

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["revelations", "representative", "penalties", "children"],
    "properties": {
        "revelations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["to", "message"],
            "properties": {"to": {"type": "string"}, "message": {"type": "string"}}}},
        "representative": {"type": ["string", "null"]},
        "penalties": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["target", "penalty", "days", "amount", "victim", "reason"],
            "properties": {"target": {"type": "string"}, "penalty": {"type": "string", "enum": config.PENALTIES},
                           "days": {"type": ["integer", "null"]}, "amount": {"type": ["integer", "null"]},
                           "victim": {"type": ["string", "null"]}, "reason": {"type": "string"}}}},
        "children": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["parent", "approve", "reason"],
            "properties": {"parent": {"type": "string"}, "approve": {"type": "boolean"},
                           "reason": {"type": "string"}}}},
    },
}

INSTRUCTIONS = """Reply with JSON:
- revelations: what you say, each to one person (their name) or to "all". An empty list is silence.
- representative: the name of the person to appoint, or null to keep the current one.
- penalties: each with target, penalty and reason. days: for exile and no_children. amount: food for fine, and
  for restitution, which gives back to victim what target stole from them. Unused fields are null.
- children: your answer to each request for a child since yesterday: the parent who asked, approve, reason."""


def describe_person(agent, tick):
    parts = [f"{agent['name'].capitalize()}, {'man' if agent['sex'] == 'M' else 'woman'}", f"food {agent['food']}"]
    if agent["spouse"]:
        parts.append(f"married to {agent['spouse'].capitalize()}")
    if agent["role"]:
        parts.append(f"the {agent['role']}")
    if agent["exiled_until"] > tick:
        parts.append(f"in exile until {llm.clock(agent['exiled_until'])}")
    if agent["born"] is not None:
        parents = " and ".join(p.capitalize() for p in agent["parents"])
        parts.append(f"born {llm.clock(agent['born'])}, child of {parents}"
                     + ("" if births.adult(agent, tick) else ", still a child"))
    parts += [f"warned: {w}" for w in agent["warnings"]] + [f"marked: {m}" for m in agent["marks"]]
    parts += [f'promised "{p["spec"]["body"]}"' + (" (broken by the record)" if p.get("status", {}).get("state")
                                                   == "broken" else "") for p in agent["promises"]]
    return "- " + "; ".join(parts)


def prompt(constitution, agents, tick, events, decisions):
    commandments = "\n".join(f"{i}. {c}" for i, c in enumerate(config.COMMANDMENTS, 1))
    system = f"{constitution}\n\nThe commandments you revealed:\n{commandments}\n\n{INSTRUCTIONS}"
    people = "\n".join(describe_person(a, tick) for a in agents.values() if a["alive"])
    past = "\n".join(f"- {d['kind']} for {d['target'].capitalize()} ({d['status']}): {d['reason']}"
                     for d in decisions) or "none"
    record = "\n".join(llm.describe(e, "") for e in events) or "nothing"
    user = (f"It is {llm.clock(tick)}.\n\nThe people:\n{people}\n\nSince yesterday:\n{record}"
            f"\n\nYour recent decisions:\n{past}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def record(db, tick):
    """Yesterday's prayers, petitions and wrongs, and God's own recent decisions."""
    events = db.q("select tick, kind, agent, data from events where tick>=? and (kind in ('pray', 'petition', "
                  "'steal', 'request_child') "
                  "or (kind='assess' and json_extract(data, '$.kept')=0)) order by id",
                  (tick - config.TICKS_PER_DAY,))
    decisions = db.q("select kind, target, reason, status from decisions order by id desc limit 10")[::-1]
    return events, decisions


def owed(db, thief, victim):
    """Food the thief took from the victim and has not yet been made to give back."""
    taken = db.one("select coalesce(sum(json_extract(data, '$.taken')), 0) as n from events where kind='steal' "
                   "and agent=? and json_extract(data, '$.target')=?", (thief, victim))["n"]
    returned = db.one("select coalesce(sum(json_extract(data, '$.amount')), 0) as n from events where kind='sentence' "
                      "and json_extract(data, '$.penalty')='restitution' and json_extract(data, '$.target')=? "
                      "and json_extract(data, '$.victim')=?", (thief, victim))["n"]
    return taken - returned


def penalty_problem(p, target, living, db):
    """None when the code allows this penalty."""
    penalty, days, amount = p["penalty"], p["days"], p["amount"]
    victim = (p["victim"] or "").strip().lower()
    if target not in living:
        return "unknown person"
    if penalty in ("exile", "no_children") and not (days and 1 <= days <= config.MAX_PENALTY_DAYS):
        return f"{penalty} needs 1-{config.MAX_PENALTY_DAYS} days"
    if penalty == "fine" and not (amount and 1 <= amount <= config.MAX_FINE):
        return f"a fine is 1-{config.MAX_FINE} food"
    if penalty == "restitution" and (victim not in living or victim == target):
        return "restitution needs another living victim"
    if penalty == "restitution" and not (amount and 1 <= amount <= owed(db, target, victim)):
        return "restitution is at most what the record shows was stolen"
    if penalty == "lose_role" and not living[target]["role"]:
        return "target has no role"
    return None


def deaths_today(db, now):
    return db.one("select count(*) as n from decisions where kind='death' and status!='vetoed' and created>?",
                  (now - 24 * 60 * 60,))["n"]


def apply(db, api, namespace, agents, tick, answer, now):
    living = {n: a for n, a in agents.items() if a["alive"]}

    for r in answer["revelations"]:
        to = r["to"].strip().lower()
        if (to != "all" and to not in living) or not r["message"] or len(r["message"]) > config.MAX_MESSAGE_CHARS:
            db.log("invalid", "god", action="revelation", reason="unknown person or bad message", **r)
            continue
        db.log("revelation", "god", target=to, message=r["message"])

    if answer["representative"] is not None:
        chosen = answer["representative"].strip().lower()
        if chosen not in living:
            db.log("invalid", "god", action="appoint", reason="unknown person", target=chosen)
        elif living[chosen]["role"] != "representative":
            for a in living.values():
                if a["role"] == "representative":
                    patch_status(api, namespace, a, role=None)
            patch_status(api, namespace, living[chosen], role="representative")
            db.log("appoint", "god", target=chosen)

    for p in answer["penalties"]:
        target, penalty = p["target"].strip().lower(), p["penalty"]
        problem = penalty_problem(p, target, living, db)
        if problem is not None:
            db.log("invalid", "god", action="sentence", reason=problem, sentence=p)
            continue
        if penalty == "death" and deaths_today(db, now) >= config.MAX_DEATHS_PER_DAY:
            db.set("paused", True)
            db.log("anomaly", "god", reason="too many deaths in a day: world paused", sentence=p)
            continue
        days = p["days"] if penalty in ("exile", "no_children") else None
        if penalty in config.VETO_PENALTIES:
            decision = db.x("insert into decisions(created, due, tick, kind, target, days, reason, status) "
                            "values(?, ?, ?, ?, ?, ?, ?, 'pending')",
                            (now, now + config.VETO_SECONDS, tick, penalty, target, days, p["reason"]))
            db.log("sentence", "god", target=target, penalty=penalty, reason=p["reason"], decision=decision,
                   **({"days": days} if days else {}))
            continue
        agent, extra = living[target], {}
        if penalty in ("warning", "mark"):
            field = {"warning": "warnings", "mark": "marks"}[penalty]
            patch_status(api, namespace, agent, **{field: [*agent[field], p["reason"]]})
        elif penalty == "fine":
            patch_status(api, namespace, agent, food=agent["food"] - p["amount"])
            extra = {"amount": p["amount"]}
        elif penalty == "restitution":
            victim = p["victim"].strip().lower()
            food.move(api, namespace, agent, living[victim], p["amount"])
            extra = {"amount": p["amount"], "victim": victim}
        elif penalty == "no_children":
            patch_status(api, namespace, agent, childrenBannedUntil=tick + days * config.TICKS_PER_DAY)
            extra = {"days": days}
        else:
            patch_status(api, namespace, agent, role=None)
        db.x("insert into decisions(created, due, tick, kind, target, days, reason, status) "
             "values(?, ?, ?, ?, ?, ?, ?, 'applied')", (now, now, tick, penalty, target, days, p["reason"]))
        db.log("sentence", "god", target=target, penalty=penalty, reason=p["reason"], **extra)


def apply_due(db, api, namespace, agents, tick, now):
    """Carries out exile and death once the veto window has passed without a pardon."""
    for d in db.q("select id, kind, target, days from decisions where status='pending' and due<=?", (now,)):
        agent = agents.get(d["target"])
        if agent is not None and agent["alive"]:
            if d["kind"] == "exile":
                patch_status(api, namespace, agent, exiledUntil=tick + d["days"] * config.TICKS_PER_DAY)
            else:
                patch_status(api, namespace, agent, alive=False)
            db.log("penalty", "god", target=d["target"], penalty=d["kind"], decision=d["id"])
            db.x("update decisions set status='applied' where id=?", (d["id"],))
        else:
            db.x("update decisions set status='moot' where id=?", (d["id"],))


def answer_children(db, api, namespace, agents, tick, answers):
    """God only approves or refuses: the code checks the couple again before anyone is born."""
    living = {n: a for n, a in agents.items() if a["alive"]}
    couples, newborns = set(), []
    for c in answers:
        parent = living.get(c["parent"].strip().lower())
        asked = parent is not None and db.one(
            "select count(*) as n from events where kind='request_child' and agent in (?, ?) and tick>=?",
            (parent["name"], parent["spouse"], tick - config.TICKS_PER_DAY))["n"] > 0
        if not asked:
            db.log("invalid", "god", action="birth", reason="no request from this parent", answer=c)
            continue
        couple = frozenset((parent["name"], parent["spouse"]))
        if not c["approve"]:
            db.log("child_denied", "god", target=parent["name"], reason=c["reason"])
            continue
        # Today's newborns count for the 12-agent cap and take their names.
        why = "one child per couple per day" if couple in couples else births.why_no_child(
            parent, {**living, **dict.fromkeys(newborns)}, {**agents, **dict.fromkeys(newborns)}, tick)
        if why is not None:
            db.log("invalid", "god", action="birth", reason=why, answer=c)
            continue
        couples.add(couple)
        newborns.append(births.birth(db, api, namespace, parent, living[parent["spouse"]],
                                     {**agents, **dict.fromkeys(newborns)}, tick,
                                     random.Random(f"{tick}:{parent['name']}")))


def turn(db, api, namespace, agents, tick, quota, constitution):
    events, decisions = record(db, tick)
    try:
        answer = llm.call(config.GOD_MODEL, prompt(constitution, agents, tick, events, decisions), quota,
                          schema=SCHEMA)
    except llm.Unavailable as e:
        db.log("skip", "god", reason=str(e))
        return
    apply(db, api, namespace, agents, tick, answer, time.time())
    answer_children(db, api, namespace, agents, tick, answer["children"])
