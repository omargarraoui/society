"""Reconcile loop: compares each Promise with the behaviour recorded in its world's event log.

Run with: kopf run promise_operator.py --all-namespaces
"""
import threading

import kopf
import kubernetes

from db import DB, world_db_path
from state import crd, load_k8s_config


def verdicts(assessments):
    """What the Promise's status says about the judgments: the agents' are counted, God's latest stands."""
    agents = [a for a in assessments if a["assessor"] != "god"]
    status = {"judgedKept": sum(a["kept"] for a in agents), "judgedBroken": sum(not a["kept"] for a in agents)}
    by_god = [a for a in assessments if a["assessor"] == "god"]
    if by_god:
        last = max(by_god, key=lambda a: a["tick"])
        status["godVerdict"] = {"kept": last["kept"], "reason": last["reason"], "tick": last["tick"]}
    return status


@kopf.on.startup()
def open_dbs(memo, **_):
    memo.dbs = {}
    # kopf runs sync handlers in parallel threads; a sqlite3 connection must not be used by two at once
    # (seen live: "bad parameter or other API misuse").
    memo.lock = threading.Lock()
    load_k8s_config()  # kopf has its own login; tally() uses the kubernetes client


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


# Recounts every judgment of the promise, so a retried or replayed event writes the same status. A promise
# deleted with its world fails here; retries keep kopf from trying forever.
@kopf.on.create("society.local", "v1", "assessments", retries=3)
def tally(spec, namespace, **_):
    api = kubernetes.client.CustomObjectsApi()
    judged = [a["spec"] for a in api.list_namespaced_custom_object(**crd("assessments", namespace))["items"]
              if a["spec"]["promise"] == spec["promise"]]
    api.patch_namespaced_custom_object_status(**crd("promises", namespace), name=spec["promise"],
                                              body={"status": verdicts(judged)})
