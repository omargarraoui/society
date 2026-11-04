"""A world is a k8s namespace (state) plus a SQLite event log (history).

  python world.py genesis NAMESPACE
  python world.py tick NAMESPACE [--ticks N] [--bias groq=0.8 ...]
"""
import argparse
import os
import random

import kubernetes

import config
from db import DB, world_db_path

GROUP = "society.local"
# Arbitrary mock frequencies, chosen so a few simulated days contain every kind of interaction.
MOCK_WEIGHTS = {"work": 40, "speak": 25, "give": 10, "steal": 10, "propose_marriage": 5, "assess": 10}


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
    """Stands in for the LLM until llm.py exists."""
    if me["promise"] is None:
        return "promise", {"refrains": "steal", "body": "I will not steal."}
    if proposer is not None and rng.random() < 0.5:
        return "accept_marriage", {"target": proposer["name"]}
    kind = rng.choices(list(MOCK_WEIGHTS), weights=list(MOCK_WEIGHTS.values()))[0]
    candidates = {
        "work": [],
        "propose_marriage": [] if me["spouse"] else [o for o in others if o["sex"] != me["sex"] and not o["spouse"]],
        "assess": [o for o in others if o["promise"] is not None],
    }.get(kind, others)
    if not candidates:
        return "work", {}
    return kind, {"target": pick(rng, me, candidates, bias)["name"]}


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
    if a["sex"] == b["sex"] or a["spouse"] or b["spouse"]:
        raise ValueError(f"invalid marriage {a['name']} + {b['name']}")
    for x, y in ((a, b), (b, a)):
        api.patch_namespaced_custom_object_status(**crd("agents", namespace), name=x["name"],
                                                  body={"status": {"spouse": y["name"]}})
        x["spouse"] = y["name"]


def run_tick(db, api, namespace, bias):
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
        kind, data = mock_action(rng, me, others, pending_proposer(db, me, agents), bias)
        if kind == "promise":
            me["promise"] = api.create_namespaced_custom_object(**crd("promises", namespace), body={
                "apiVersion": f"{GROUP}/v1", "kind": "Promise",
                "metadata": {"generateName": f"{name}-"},
                "spec": {"promiser": name, "tick": tick, **data},
            })
        elif kind == "accept_marriage":
            marry(api, namespace, me, agents[data["target"]])
        elif kind == "assess":
            promise = agents[data["target"]]["promise"]
            data["kept"] = db.violations(data["target"], promise["spec"]["refrains"], promise["spec"]["tick"]) == 0
            api.create_namespaced_custom_object(**crd("assessments", namespace), body={
                "apiVersion": f"{GROUP}/v1", "kind": "Assessment",
                "metadata": {"generateName": f"{name}-"},
                "spec": {"assessor": name, "promiser": data["target"], "promise": promise["metadata"]["name"],
                         "kept": data["kept"], "tick": tick},
            })
        db.log(kind, name, **data)
    db.set("tick", tick + 1)


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
    sub.add_parser("genesis").add_argument("namespace")
    tick = sub.add_parser("tick")
    tick.add_argument("namespace")
    tick.add_argument("--ticks", type=int, default=1)
    tick.add_argument("--bias", nargs="*", default=[], metavar="MODEL=P")
    args = parser.parse_args()

    kubernetes.config.load_kube_config()
    api = kubernetes.client.CustomObjectsApi()
    if args.cmd == "genesis":
        founders = [{"name": n.lower(), "sex": s, "model": m, "group": m} for n, s, m in config.FOUNDERS]
        create_world(kubernetes.client.CoreV1Api(), api, args.namespace, founders, seed=0)
    else:
        db = DB(world_db_path(args.namespace))
        for _ in range(args.ticks):
            run_tick(db, api, args.namespace, parse_bias(args.bias))


if __name__ == "__main__":
    main()
