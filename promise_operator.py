"""Reconcile loop: compares each Promise with the behaviour recorded in its world's event log.

Run with: kopf run promise_operator.py --all-namespaces
"""
import threading

import kopf

from db import DB, world_db_path


@kopf.on.startup()
def open_dbs(memo, **_):
    memo.dbs = {}
    # kopf runs sync handlers in parallel threads; a sqlite3 connection must not be used by two at once
    # (seen live: "bad parameter or other API misuse").
    memo.lock = threading.Lock()


# Promises without refrains have nothing the code can observe: only other agents judge them.
@kopf.timer("society.local", "v1", "promises", interval=10, when=lambda spec, **_: "refrains" in spec)
def reconcile(spec, namespace, patch, memo, **_):
    with memo.lock:
        if namespace not in memo.dbs:
            memo.dbs[namespace] = DB(world_db_path(namespace))
        db = memo.dbs[namespace]
        n = db.violations(spec["promiser"], spec["refrains"], spec["tick"])
        tick = db.get("tick", 0)
    patch.status.update({"state": "broken" if n else "kept", "violations": n, "checkedTick": tick})
