"""A world is a k8s namespace (state) plus a SQLite event log (history).

  python world.py genesis NAMESPACE
  python world.py tick NAMESPACE [--ticks N] [--brain mock|llm] [--bias groq=0.8 ...]
  python world.py run NAMESPACE   # LLM brain, forever, paced by the free quotas
"""
import argparse
import os
import random
import time
from collections import Counter

import kubernetes

import config
import llm
from db import DB, world_db_path

GROUP = "society.local"
# Arbitrary mock frequencies, chosen so a few simulated days contain every kind of interaction.
MOCK_WEIGHTS = {"work": 40, "speak": 25, "give": 10, "steal": 10, "propose_marriage": 5, "assess": 10}
TARGETED = {"speak", "give", "steal", "propose_marriage", "accept_marriage", "assess"}


def crd(plural, namespace):
    return {"group": GROUP, "version": "v1", "namespace": namespace, "plural": plural}


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
    if me["promise"] is None:
        return "promise", {"refrains": "steal", "message": "I will not steal."}
    if proposer is not None and rng.random() < 0.5:
        return "accept_marriage", {"target": proposer["name"]}
    kind = rng.choices(list(MOCK_WEIGHTS), weights=list(MOCK_WEIGHTS.values()))[0]
    candidates = {
        "work": [],
        "propose_marriage": [] if me["spouse"] else [o for o in others if o["sex"] != me["sex"] and not o["spouse"]],
        # The mock judges from the event log, so it can only assess promises the code can check.
        "assess": [o for o in others if o["promise"] is not None and "refrains" in o["promise"]["spec"]],
    }.get(kind, others)
    if not candidates:
        return "work", {}
    data = {"target": pick(rng, me, candidates, bias)["name"]}
    if kind == "speak":
        data["message"] = "Hello."
    return kind, data


def mock_brain(bias, db):
    def decide(rng, me, others, proposer, tick):
        kind, data = mock_action(rng, me, others, proposer, bias)
        if kind == "assess":
            promise = next(o for o in others if o["name"] == data["target"])["promise"]["spec"]
            data["kept"] = db.violations(data["target"], promise["refrains"], promise["tick"]) == 0
        return kind, data
    return decide


def visible_events(db, name, tick):
    """What the agent did or had done to it since the start of yesterday."""
    since = (tick // config.TICKS_PER_DAY - 1) * config.TICKS_PER_DAY
    marks = ",".join("?" * len(config.ACTIONS))
    rows = db.q(f"select tick, kind, agent, data from events where kind in ({marks}) and tick>=? "
                "and (agent=? or json_extract(data, '$.target')=?) order by id desc limit 15",
                (*config.ACTIONS, since, name, name))
    return rows[::-1]


def llm_brain(db, quota):
    def decide(rng, me, others, proposer, tick):
        return llm.decide(me, others, proposer, visible_events(db, me["name"], tick), tick, quota)
    return decide


def invalid_reason(kind, data, me, agents, proposer):
    """None when the action is allowed. The code enforces the hard rules, not the LLM."""
    target = agents.get(data.get("target"))
    if kind not in config.ACTIONS:
        return "unknown action"
    if kind in TARGETED and (target is None or target is me):
        return "target is not another person"
    if len(data.get("message", "")) > config.MAX_MESSAGE_CHARS:
        return "message too long"
    if kind == "promise" and me["promise"] is not None:
        return "already promised"
    if kind == "promise" and not data.get("message"):
        return "a promise needs words"
    if kind == "promise" and "refrains" in data and data["refrains"] != "steal":
        return "steal is the only action a promise can refrain from"
    if kind in ("speak", "pray") and not data.get("message"):
        return "nothing said"
    if kind == "propose_marriage" and (me["spouse"] or target["spouse"] or me["sex"] == target["sex"]):
        return "marriage is between an unmarried man and an unmarried woman"
    if kind == "accept_marriage" and (proposer is None or proposer is not target):
        return "no pending proposal from target"
    if kind == "assess" and (target["promise"] is None or not isinstance(data.get("kept"), bool)):
        return "assess needs a target with a promise and kept true or false"
    return None


def pending_proposer(db, me, agents):
    if me["spouse"]:
        return None
    row = db.one("select agent from events where kind='propose_marriage' and json_extract(data, '$.target')=? "
                 "order by id desc limit 1", (me["name"],))
    if row is None or agents[row["agent"]]["spouse"]:
        return None
    return agents[row["agent"]]


def load_agents(api, namespace):
    items = api.list_namespaced_custom_object(**crd("promises", namespace))["items"]
    promises = {p["spec"]["promiser"]: p for p in items}
    agents = {}
    for a in api.list_namespaced_custom_object(**crd("agents", namespace))["items"]:
        name = a["metadata"]["name"]
        agents[name] = {"name": name, **a["spec"], "spouse": a.get("status", {}).get("spouse"),
                        "promise": promises.get(name)}
    return agents


def marry(api, namespace, a, b):
    for x, y in ((a, b), (b, a)):
        api.patch_namespaced_custom_object_status(**crd("agents", namespace), name=x["name"],
                                                  body={"status": {"spouse": y["name"]}})
        x["spouse"] = y["name"]


def run_tick(db, api, namespace, brain):
    agents = load_agents(api, namespace)
    if not agents:
        raise SystemExit(f"no agents in namespace {namespace}: create the world first")
    # A new world starts at dawn, otherwise the first tick finds everyone asleep.
    tick = db.get("tick", config.NIGHT_END_HOUR * config.TICKS_PER_DAY // 24)
    db.set("tick", tick)
    # Seeded by world seed and tick so a run can be replayed.
    rng = random.Random(f"{db.get('seed', 0)}:{tick}")
    for name in awake(sorted(agents), tick, rng):
        me = agents[name]
        others = [a for a in agents.values() if a["name"] != name]
        proposer = pending_proposer(db, me, agents)
        try:
            kind, data = brain(rng, me, others, proposer, tick)
        except llm.Unavailable as e:
            db.log("skip", name, reason=str(e))
            continue
        reason = invalid_reason(kind, data, me, agents, proposer)
        if reason is not None:
            db.log("invalid", name, action=kind, reason=reason, **data)
            continue
        if kind == "promise":
            me["promise"] = api.create_namespaced_custom_object(**crd("promises", namespace), body={
                "apiVersion": f"{GROUP}/v1", "kind": "Promise",
                "metadata": {"generateName": f"{name}-"},
                "spec": {"promiser": name, "tick": tick, "body": data["message"],
                         **({"refrains": data["refrains"]} if "refrains" in data else {})},
            })
        elif kind == "accept_marriage":
            marry(api, namespace, me, agents[data["target"]])
        elif kind == "assess":
            promise = agents[data["target"]]["promise"]
            api.create_namespaced_custom_object(**crd("assessments", namespace), body={
                "apiVersion": f"{GROUP}/v1", "kind": "Assessment",
                "metadata": {"generateName": f"{name}-"},
                "spec": {"assessor": name, "promiser": data["target"], "promise": promise["metadata"]["name"],
                         "kept": data["kept"], "tick": tick},
            })
        db.log(kind, name, **data)
    db.set("tick", tick + 1)
    return agents


def turn_tokens(quota, model):
    """Today's measured average, or the configured estimate before the first call."""
    requests, tokens = quota.usage(model)
    return tokens / requests if requests else config.PROVIDERS[model]["turn_tokens"]


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


def run_forever(db, api, namespace, brain, quota):
    while True:
        started = time.monotonic()
        agents = run_tick(db, api, namespace, brain)
        counts = Counter(a["model"] for a in agents.values())
        speed = world_days_per_day(counts, {m: turn_tokens(quota, m) for m in counts})
        pause = 24 * 60 * 60 / (speed * config.TICKS_PER_DAY)
        print(f"tick {db.get('tick') - 1}: {speed:.2f} world days per day, next tick in {pause:.0f}s", flush=True)
        time.sleep(max(0.0, pause - (time.monotonic() - started)))


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
    genesis.add_argument("--model", choices=list(config.PROVIDERS),
                         help="give both founders this model (a one-model control world)")
    tick = sub.add_parser("tick")
    tick.add_argument("namespace")
    tick.add_argument("--ticks", type=int, default=1)
    tick.add_argument("--brain", choices=["mock", "llm"], default="mock")
    tick.add_argument("--bias", nargs="*", default=[], metavar="MODEL=P", help="mock brain only")
    sub.add_parser("run").add_argument("namespace")
    args = parser.parse_args()

    kubernetes.config.load_kube_config()
    api = kubernetes.client.CustomObjectsApi()
    if args.cmd == "genesis":
        founders = [{"name": n.lower(), "sex": s, "model": args.model or m, "group": args.model or m}
                    for n, s, m in config.FOUNDERS]
        create_world(kubernetes.client.CoreV1Api(), api, args.namespace, founders, seed=0)
        return
    db = DB(world_db_path(args.namespace))
    if args.cmd == "tick" and args.brain == "mock":
        brain = mock_brain(parse_bias(args.bias), db)
    else:
        models = {a["model"] for a in load_agents(api, args.namespace).values()}
        keys = [config.PROVIDERS[m]["key_env"] for m in sorted(models)]
        missing = [k for k in keys if k not in os.environ]
        if missing:
            raise SystemExit(f"missing API keys: {', '.join(missing)}")
        # Quotas belong to the account, not to a world, so all worlds share one counter.
        quota = DB(os.path.join(config.DB_DIR, "quota.db"))
        brain = llm_brain(db, quota)
    if args.cmd == "run":
        run_forever(db, api, args.namespace, brain, quota)
    else:
        for _ in range(args.ticks):
            run_tick(db, api, args.namespace, brain)


if __name__ == "__main__":
    main()
