"""Reconcile loop: compares each Promise with the behaviour recorded in its world's event log.

Run with: kopf run promise_operator.py --all-namespaces
"""
import kopf

from db import DB, world_db_path


@kopf.on.startup()
def open_dbs(memo, **_):
    memo.dbs = {}


@kopf.timer("society.local", "v1", "promises", interval=10)
def reconcile(spec, namespace, patch, memo, **_):
    if namespace not in memo.dbs:
        memo.dbs[namespace] = DB(world_db_path(namespace))
    db = memo.dbs[namespace]
    n = db.violations(spec["promiser"], spec["refrains"], spec["tick"])
    patch.status.update({"state": "broken" if n else "kept", "violations": n, "checkedTick": db.get("tick", 0)})
