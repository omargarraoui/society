"""Segregation experiment: for each seed, a real world (group = hidden model) and a
control world (one model for everyone, the same group labels shuffled at random).
The control gives the segregation that shows up by chance.

  python experiment.py NAME --seeds 5 --days 3 [--bias groq=0.8 ...]
"""
import argparse
import json
import os
import random
from statistics import mean, stdev

import kubernetes

import config
from db import DB, world_db_path
from metrics import event_rows, segregation
from state import load_k8s_config
from world import create_world, mock_brain, parse_bias, run_tick

MODELS = config.AGENT_MODELS


def population(world, seed, control_model):
    half = config.MAX_AGENTS // 2
    names = [n for pair in zip(config.NAMES_M[:half], config.NAMES_F[:half], strict=True) for n in pair]
    # Alternating sexes plus round-robin models gives every model 2 men and 2 women.
    models = [MODELS[i % len(MODELS)] for i in range(len(names))]
    groups = models if world == "real" else random.Random(seed).sample(models, len(models))
    if world == "control":
        models = [control_model] * len(names)
    return [{"name": n.lower(), "sex": "MF"[i % 2], "model": m, "group": g}
            for i, (n, m, g) in enumerate(zip(names, models, groups, strict=True))]


def run(core, api, name, world, seed, days, bias, control_model):
    namespace = f"{name}-{world}-{seed}"
    agents = population(world, seed, control_model)
    create_world(core, api, namespace, agents, seed, labels={"society.local/experiment": name})
    db = DB(world_db_path(namespace))
    for _ in range(days * config.TICKS_PER_DAY):
        run_tick(db, api, namespace, mock_brain(bias, db))
    return segregation(event_rows(db), agents)


def summarize(runs):
    def stats(key):
        values = [r[key] for r in runs if r[key] is not None]
        if not values:
            return None
        return {"mean": mean(values), "sd": stdev(values) if len(values) > 1 else 0.0, "n": len(values)}
    return {key: stats(key) for key in ("in_group_ratio", "trust_gap", "in_group_marriages", "all_marriages")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="experiment name, used as namespace prefix")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--bias", nargs="*", default=[], metavar="MODEL=P")
    parser.add_argument("--control-model", default="groq", choices=MODELS)
    args = parser.parse_args()

    load_k8s_config()
    core, api = kubernetes.client.CoreV1Api(), kubernetes.client.CustomObjectsApi()
    bias = parse_bias(args.bias)
    runs = {world: [run(core, api, args.name, world, seed, args.days, bias, args.control_model)
                    for seed in range(args.seeds)]
            for world in ("real", "control")}
    result = {"args": vars(args), "runs": runs, "summary": {w: summarize(r) for w, r in runs.items()}}
    os.makedirs("results", exist_ok=True)
    with open(f"results/{args.name}.json", "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
