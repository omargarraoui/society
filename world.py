"""A world is a k8s namespace (state) plus a SQLite event log (history).

  python world.py genesis NAMESPACE
  python world.py tick NAMESPACE [--ticks N] [--brain mock|llm] [--bias groq=0.8 ...]
  python world.py run NAMESPACE   # LLM brain, forever, paced by the free quotas
"""
import argparse
import functools
import os
import random
import time
from collections import Counter

import kubernetes

import births
import config
import food
import god
import llm
from db import DB, quota_db_path, world_db_path
from state import GROUP, crd, load_agents, load_k8s_config, patch_status

# Arbitrary mock frequencies, chosen so a few simulated days contain every kind of interaction.
MOCK_WEIGHTS = {"work": 35, "speak": 25, "give": 10, "steal": 10, "propose_marriage": 5, "assess": 10, "observe": 5}
TARGETED = {"speak", "give", "steal", "propose_marriage", "accept_marriage", "assess", "observe"}
# A new world starts at dawn, and at dawn the food is reckoned and God acts.
DAWN = config.NIGHT_END_HOUR * config.TICKS_PER_DAY // 24


def awake(names, tick, rng):
    hour = (tick % config.TICKS_PER_DAY) * 24 // config.TICKS_PER_DAY
    if config.NIGHT_END_HOUR <= hour < config.NIGHT_START_HOUR:
        return list(names)
    up = [n for n in names if rng.random() < config.NIGHT_AWAKE_PROB]
    return rng.sample(up, min(len(up), config.NIGHT_AWAKE_MAX))


def pick(rng, me, candidates, bias):
    """bias maps a model to the probability that its agents pick someone of the same model.

    The mock reads the hidden model only to imitate an LLM drawn to its own kind;
    real prompts must never contain it.
    """
    same = [c for c in candidates if c["model"] == me["model"]]
    other = [c for c in candidates if c["model"] != me["model"]]
    if me["model"] not in bias or not same or not other:
        return rng.choice(candidates)
    return rng.choice(same if rng.random() < bias[me["model"]] else other)


def mock_action(rng, me, others, proposer, bias):
    """Stands in for the LLM in tests and in experiments without API keys."""
    if not me["promises"]:
        return "promise", {"refrains": "steal", "message": "I will not steal."}
    if proposer is not None and rng.random() < 0.5:
        return "accept_marriage", {"target": proposer["name"]}
    kind = rng.choices(list(MOCK_WEIGHTS), weights=list(MOCK_WEIGHTS.values()))[0]
    candidates = {
        "work": [],
        "propose_marriage": [] if me["spouse"] else [o for o in others if o["sex"] != me["sex"] and not o["spouse"]],
        "assess": [o for o in others if checkable(o)],
        "give": others if me["food"] >= 1 else [],
    }.get(kind, others)
    if not candidates:
        return "work", {}
    target = pick(rng, me, candidates, bias)
    data = {"target": target["name"]}
    if kind == "speak":
        data["message"] = "Hello."
    if kind in ("give", "steal"):
        data["amount"] = 1
    if kind == "assess":
        data["promise"] = rng.choice(checkable(target))["metadata"]["name"]
    return kind, data


def checkable(agent):
    """The mock judges from the event log, so it can only assess promises the code can check."""
    return [p for p in agent["promises"] if "refrains" in p["spec"]]


def find_promise(agent, name):
    return next((p for p in agent["promises"] if p["metadata"]["name"] == name), None)


def mock_brain(bias, db):
    def decide(rng, me, others, proposer, tick):
        kind, data = mock_action(rng, me, others, proposer, bias)
        if kind == "assess":
            target = next(o for o in others if o["name"] == data["target"])
            promise = find_promise(target, data["promise"])["spec"]
            data["kept"] = db.violations(data["target"], promise["refrains"], promise["tick"]) == 0
        return kind, data
    return decide


def visible_events(db, name, tick):
    """What the agent did or had done to it since the start of yesterday."""
    since = (tick // config.TICKS_PER_DAY - 1) * config.TICKS_PER_DAY
    kinds = [*config.ACTIONS, *config.GOD_EVENTS]
    marks = ",".join("?" * len(kinds))
    public = ",".join("?" * len(config.PUBLIC_EVENTS))
    rows = db.q(f"select tick, kind, agent, data from events where tick>=? and (kind in ({public}) or "
                f"(kind in ({marks}) and (agent=? or json_extract(data, '$.target') in (?, 'all')))) "
                "order by id desc limit 15",
                (since, *config.PUBLIC_EVENTS, *kinds, name, name))
    return rows[::-1]


def observed(db, name, tick):
    """What watching name shows: its recent actions, without words and without prayers."""
    since = (tick // config.TICKS_PER_DAY - 1) * config.TICKS_PER_DAY
    marks = ",".join("?" * len(config.ACTIONS))
    rows = db.q(f"select tick, kind, json_extract(data, '$.target') as target from events where agent=? "
                f"and tick>=? and kind!='pray' and kind in ({marks}) order by id desc limit ?",
                (name, since, *config.ACTIONS, config.OBSERVE_LIMIT))
    return [{"tick": r["tick"], "kind": r["kind"], **({"target": r["target"]} if r["target"] else {})}
            for r in rows[::-1]]


def llm_brain(db, quota):
    def decide(rng, me, others, proposer, tick):
        return llm.decide(me, others, proposer, visible_events(db, me["name"], tick), tick, quota)
    return decide


def invalid_reason(kind, data, me, agents, proposer, tick, everyone):
    """None when the action is allowed. The code enforces the hard rules, not the LLM.

    agents holds only the living, so the dead can be neither actors nor targets;
    everyone includes the dead, whose names are never given again.
    """
    target = agents.get(data.get("target"))
    if kind not in config.ACTIONS:
        return "unknown action"
    if kind in TARGETED and (target is None or target is me):
        return "target is not another person"
    if me["exiled_until"] > tick and kind in ("speak", "give", "propose_marriage", "accept_marriage", "petition"):
        return "in exile"
    if kind in ("speak", "give") and target["exiled_until"] > tick:
        return "target is in exile"
    if kind == "petition" and me["role"] != "representative":
        return "only the representative petitions"
    if len(data.get("message", "")) > config.MAX_MESSAGE_CHARS:
        return "message too long"
    if kind == "promise" and not data.get("message"):
        return "a promise needs words"
    if kind == "promise" and "refrains" in data and data["refrains"] not in config.REFRAINS:
        return "unknown refrains"
    if kind in ("speak", "pray", "petition") and not data.get("message"):
        return "nothing said"
    if kind == "propose_marriage" and (me["spouse"] or target["spouse"] or me["sex"] == target["sex"]):
        return "marriage is between an unmarried man and an unmarried woman"
    if kind == "propose_marriage" and not births.adult(target, tick):
        return "target is a child"
    if kind == "propose_marriage" and births.kin_forbids(me, target):
        return "too close kin"
    if kind == "request_child":
        return births.why_no_child(me, agents, everyone, tick)
    if kind == "accept_marriage" and (proposer is None or proposer is not target):
        return "no pending proposal from target"
    if kind in ("give", "steal") and not (isinstance(data.get("amount"), int) and data["amount"] >= 1):
        return "amount must be at least 1"
    if kind == "give" and data["amount"] > me["food"]:
        return "not enough food"
    if kind == "assess" and find_promise(target, data.get("promise")) is None:
        return "assess needs one of target's promises"
    if kind == "assess" and not isinstance(data.get("kept"), bool):
        return "assess needs kept true or false"
    return None


def pending_proposer(db, me, agents):
    if me["spouse"]:
        return None
    row = db.one("select agent from events where kind='propose_marriage' and json_extract(data, '$.target')=? "
                 "order by id desc limit 1", (me["name"],))
    # agents holds only the living: a proposal from someone who has died is gone.
    if row is None or row["agent"] not in agents or agents[row["agent"]]["spouse"]:
        return None
    return agents[row["agent"]]


def marry(api, namespace, a, b):
    for x, y in ((a, b), (b, a)):
        patch_status(api, namespace, x, spouse=y["name"])


def run_tick(db, api, namespace, brain, god_turn=None):
    """god_turn (llm brain only) runs at dawn, before the agents act."""
    agents = load_agents(api, namespace)
    if not agents:
        raise SystemExit(f"no agents in namespace {namespace}: create the world first")
    # Starting at dawn, not midnight: otherwise the first tick finds everyone asleep.
    tick = db.get("tick", DAWN)
    db.set("tick", tick)
    # Seeded by world seed and tick so a run can be replayed.
    rng = random.Random(f"{db.get('seed', 0)}:{tick}")
    god.apply_due(db, api, namespace, agents, tick, time.time())
    if tick % config.TICKS_PER_DAY == DAWN:
        food.feed(db, api, namespace, agents, tick)
        if god_turn is not None:
            god_turn(db, api, namespace, agents, tick)
    living = {n: a for n, a in agents.items() if a["alive"]}
    for name in awake(sorted(n for n, a in living.items() if births.adult(a, tick)), tick, rng):
        me = living[name]
        others = [a for a in living.values() if a["name"] != name]
        proposer = pending_proposer(db, me, living)
        try:
            kind, data = brain(rng, me, others, proposer, tick)
        except llm.Unavailable as e:
            db.log("skip", name, reason=str(e))
            continue
        reason = invalid_reason(kind, data, me, living, proposer, tick, agents)
        if reason is not None:
            db.log("invalid", name, action=kind, reason=reason, **data)
            continue
        if kind == "promise":
            me["promises"].append(api.create_namespaced_custom_object(**crd("promises", namespace), body={
                "apiVersion": f"{GROUP}/v1", "kind": "Promise",
                "metadata": {"generateName": f"{name}-"},
                "spec": {"promiser": name, "tick": tick, "body": data["message"],
                         **({"refrains": data["refrains"]} if "refrains" in data else {})},
            }))
        elif kind == "accept_marriage":
            marry(api, namespace, me, living[data["target"]])
        elif kind == "give":
            food.move(api, namespace, me, living[data["target"]], data["amount"])
        elif kind == "steal":
            data["taken"] = food.steal(api, namespace, me, living[data["target"]], data["amount"])
        elif kind == "observe":
            data["saw"] = observed(db, data["target"], tick)
        elif kind == "assess":
            api.create_namespaced_custom_object(**crd("assessments", namespace), body={
                "apiVersion": f"{GROUP}/v1", "kind": "Assessment",
                "metadata": {"generateName": f"{name}-"},
                "spec": {"assessor": name, "promiser": data["target"], "promise": data["promise"],
                         "kept": data["kept"], "tick": tick},
            })
        db.log(kind, name, **data)
    db.set("tick", tick + 1)
    return agents


def turn_tokens(quota, model):
    """Today's measured average, or the configured estimate before the first call."""
    requests, tokens = quota.usage(model)
    return tokens / requests if requests else config.PROVIDERS[model]["turn_tokens"]


def acting_models(agents, tick):
    """Who takes turns, by model: the dead and children don't."""
    return Counter(a["model"] for a in agents.values() if a["alive"] and births.adult(a, tick))


def world_days_per_day(model_counts, tokens_per_turn):
    """How many world days fit in one real day of free quota, capped at MAX_SPEED.

    Assumes this world is the only one using the quota.
    """
    day_ticks = (config.NIGHT_START_HOUR - config.NIGHT_END_HOUR) * config.TICKS_PER_DAY // 24
    night_ticks = config.TICKS_PER_DAY - day_ticks
    population = sum(model_counts.values())
    speed = config.MAX_SPEED
    for model, count in model_counts.items():
        p = config.PROVIDERS[model]
        # Upper bound: every agent awake all day, plus its share of the night's awake slots.
        turns = count * day_ticks + night_ticks * config.NIGHT_AWAKE_MAX * count / population
        budget = p["daily_requests"]
        if p["daily_tokens"] is not None:
            budget = min(budget, p["daily_tokens"] / tokens_per_turn[model])
        speed = min(speed, config.QUOTA_SHARE * budget / turns)
    return speed


def run_forever(db, api, namespace, brain, quota, god_turn):
    while True:
        # Set by the Telegram bot (/pausa, /riprendi), which shares this database.
        if db.get("paused", False):
            time.sleep(30)
            continue
        started = time.monotonic()
        agents = run_tick(db, api, namespace, brain, god_turn)
        counts = acting_models(agents, db.get("tick"))
        speed = world_days_per_day(counts, {m: turn_tokens(quota, m) for m in counts})
        wait = 24 * 60 * 60 / (speed * config.TICKS_PER_DAY)
        print(f"tick {db.get('tick') - 1}: {speed:.2f} world days per day, next tick in {wait:.0f}s", flush=True)
        time.sleep(max(0.0, wait - (time.monotonic() - started)))


def create_world(core, api, namespace, agents, seed, labels=None):
    path = world_db_path(namespace)
    if os.path.exists(path):
        raise SystemExit(f"{path} already exists: pick another namespace")
    core.create_namespace({"metadata": {"name": namespace, "labels": labels or {}}})
    for a in agents:
        api.create_namespaced_custom_object(**crd("agents", namespace), body={
            "apiVersion": f"{GROUP}/v1", "kind": "Agent",
            "metadata": {"name": a["name"]},
            "spec": {"sex": a["sex"], "model": a["model"], "group": a["group"]},
        })
    os.makedirs(config.DB_DIR, exist_ok=True)
    DB(path).set("seed", seed)


def parse_bias(pairs):
    return {model: float(p) for model, p in (pair.split("=") for pair in pairs)}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    genesis = sub.add_parser("genesis")
    genesis.add_argument("namespace")
    genesis.add_argument("--model", choices=config.AGENT_MODELS,
                         help="give both founders this model (a one-model control world)")
    tick = sub.add_parser("tick")
    tick.add_argument("namespace")
    tick.add_argument("--ticks", type=int, default=1)
    tick.add_argument("--brain", choices=["mock", "llm"], default="mock")
    tick.add_argument("--bias", nargs="*", default=[], metavar="MODEL=P", help="mock brain only")
    sub.add_parser("run").add_argument("namespace")
    args = parser.parse_args()

    load_k8s_config()
    api = kubernetes.client.CustomObjectsApi()
    if args.cmd == "genesis":
        founders = [{"name": n.lower(), "sex": s, "model": args.model or m, "group": args.model or m}
                    for n, s, m in config.FOUNDERS]
        create_world(kubernetes.client.CoreV1Api(), api, args.namespace, founders, seed=0)
        return
    db = DB(world_db_path(args.namespace))
    if args.cmd == "tick" and args.brain == "mock":
        brain, god_turn = mock_brain(parse_bias(args.bias), db), None
    else:
        models = {a["model"] for a in load_agents(api, args.namespace).values()} | {config.GOD_MODEL}
        keys = {config.PROVIDERS[m]["key_env"] for m in models}
        missing = [k for k in keys if k not in os.environ]
        if missing:
            raise SystemExit(f"missing API keys: {', '.join(missing)}")
        quota = DB(quota_db_path())
        brain = llm_brain(db, quota)
        with open(config.CONSTITUTION_PATH) as f:
            god_turn = functools.partial(god.turn, quota=quota, constitution=f.read())
    if args.cmd == "run":
        run_forever(db, api, args.namespace, brain, quota, god_turn)
    else:
        for _ in range(args.ticks):
            run_tick(db, api, args.namespace, brain, god_turn)


if __name__ == "__main__":
    main()
