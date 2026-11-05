"""The world's state in k8s: one namespace per world, Agents, Promises and Assessments."""
import os

import kubernetes

import config

GROUP = "society.local"


def load_k8s_config():
    # Inside a pod k8s sets KUBERNETES_SERVICE_HOST; on a laptop the kubeconfig is used.
    if "KUBERNETES_SERVICE_HOST" in os.environ:
        kubernetes.config.load_incluster_config()
    else:
        kubernetes.config.load_kube_config()


def crd(plural, namespace):
    return {"group": GROUP, "version": "v1", "namespace": namespace, "plural": plural}


# Agent status in k8s (camelCase) -> key in the in-memory agent, with the value before anything happened.
STATUS = {"spouse": ("spouse", None), "alive": ("alive", True), "role": ("role", None),
          "exiledUntil": ("exiled_until", -1), "marks": ("marks", []), "warnings": ("warnings", []),
          "food": ("food", config.START_FOOD), "childrenBannedUntil": ("children_banned_until", -1)}
# Founders' spec lacks the family fields.
FOUNDER = {"parents": [], "born": None, "generation": 0}


def load_agents(api, namespace):
    promises = {}
    for p in sorted(api.list_namespaced_custom_object(**crd("promises", namespace))["items"],
                    key=lambda p: p["spec"]["tick"]):
        promises.setdefault(p["spec"]["promiser"], []).append(p)
    agents = {}
    for a in api.list_namespaced_custom_object(**crd("agents", namespace))["items"]:
        name = a["metadata"]["name"]
        status = a.get("status", {})
        agents[name] = {"name": name, **FOUNDER, **a["spec"], "promises": promises.get(name, []),
                        **{key: status.get(field, default) for field, (key, default) in STATUS.items()}}
    return agents


def patch_status(api, namespace, agent, **fields):
    """Writes fields (k8s names) into the Agent's status and into the in-memory agent, for the rest of the tick."""
    api.patch_namespaced_custom_object_status(**crd("agents", namespace), name=agent["name"],
                                              body={"status": fields})
    agent.update({STATUS[field][0]: value for field, value in fields.items()})
