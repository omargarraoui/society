"""One world tick: wakes the agents, takes one action from each, records it.

Run with: python world.py
"""
import random

import kubernetes

import config
from db import DB

PROMISES = {"group": "society.local", "version": "v1", "namespace": "default", "plural": "promises"}


def awake(names, tick, rng):
    hour = (tick % config.TICKS_PER_DAY) * 24 // config.TICKS_PER_DAY
    if config.NIGHT_END_HOUR <= hour < config.NIGHT_START_HOUR:
        return list(names)
    up = [n for n in names if rng.random() < config.NIGHT_AWAKE_PROB]
    return rng.sample(up, min(len(up), config.NIGHT_AWAKE_MAX))


def mock_action(rng, others, promised):
    """Stands in for the LLM until llm.py exists."""
    if not promised:
        return "promise", {"refrains": "steal", "body": "I will not steal."}
    # 0.2 is arbitrary: high enough that a few ticks show broken promises.
    if rng.random() < 0.2:
        return "steal", {"target": rng.choice(others)}
    return "work", {}


def run_tick(db, api):
    # A new world starts at dawn, otherwise the first tick finds everyone asleep.
    tick = db.get("tick", config.NIGHT_END_HOUR * config.TICKS_PER_DAY // 24)
    db.set("tick", tick)
    # Seeded by the tick so a run can be replayed.
    rng = random.Random(tick)
    names = [name for name, _, _ in config.FOUNDERS]
    promised = {p["spec"]["promiser"] for p in api.list_namespaced_custom_object(**PROMISES)["items"]}
    for name in awake(names, tick, rng):
        kind, data = mock_action(rng, [n for n in names if n != name], name in promised)
        if kind == "promise":
            api.create_namespaced_custom_object(**PROMISES, body={
                "apiVersion": "society.local/v1",
                "kind": "Promise",
                "metadata": {"generateName": f"{name.lower()}-"},
                "spec": {"promiser": name, "tick": tick, **data},
            })
        db.log(kind, name, **data)
    db.set("tick", tick + 1)


if __name__ == "__main__":
    kubernetes.config.load_kube_config()
    run_tick(DB(config.DB_PATH), kubernetes.client.CustomObjectsApi())
