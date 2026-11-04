"""Reconcile loop: compares each Promise with the behaviour recorded in the event log.

Run with: kopf run promise_operator.py
"""
import kopf

import config
from db import DB


def assess(db, promiser, refrains, since_tick):
    n = db.one("select count(*) as n from events where agent=? and kind=? and tick>=?",
               (promiser, refrains, since_tick))["n"]
    return {"state": "broken" if n else "kept", "violations": n, "checkedTick": db.get("tick", 0)}


@kopf.on.startup()
def open_db(memo, **_):
    memo.db = DB(config.DB_PATH)


@kopf.timer("society.local", "v1", "promises", interval=10)
def reconcile(spec, patch, memo, **_):
    patch.status.update(assess(memo.db, spec["promiser"], spec["refrains"], spec["tick"]))
