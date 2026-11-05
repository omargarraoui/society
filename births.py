"""Children: who may ask for one, how one is born, growing up, and kinship."""
import config
from state import GROUP, crd, patch_status


def adult(agent, tick):
    return agent["born"] is None or tick - agent["born"] >= config.CHILDHOOD_DAYS * config.TICKS_PER_DAY


def kin_forbids(a, b):
    """Parent and child never marry; siblings only in the first generation (the founders' children)."""
    if a["name"] in b["parents"] or b["name"] in a["parents"]:
        return True
    return bool(set(a["parents"]) & set(b["parents"])) and not a["generation"] == b["generation"] == 1


def free_name(sex, everyone):
    """Names are never reused, not even those of the dead."""
    return next((n.lower() for n in (config.NAMES_M if sex == "M" else config.NAMES_F) if n.lower() not in everyone),
                None)


def why_no_child(parent, living, everyone, tick):
    """None when this couple may have a child now."""
    spouse = living.get(parent["spouse"])
    if spouse is None:
        return "not married to a living spouse"
    if not (adult(parent, tick) and adult(spouse, tick)):
        return "both parents must be adults"
    if len(living) >= config.MAX_AGENTS:
        return "the world is full"
    if parent["food"] + spouse["food"] < config.MIN_FOOD_FOR_BIRTH:
        return "not enough food"
    if max(parent["children_banned_until"], spouse["children_banned_until"]) > tick:
        return "children forbidden by God"
    if free_name("M", everyone) is None or free_name("F", everyone) is None:
        return "no names left"
    return None


def birth(db, api, namespace, parent, spouse, everyone, tick, rng):
    """The child inherits the hidden model (and group) of one parent, chosen at random."""
    sex = rng.choice("MF")
    name = free_name(sex, everyone)
    heir = rng.choice([parent, spouse])
    parents = sorted([parent["name"], spouse["name"]])
    api.create_namespaced_custom_object(**crd("agents", namespace), body={
        "apiVersion": f"{GROUP}/v1", "kind": "Agent", "metadata": {"name": name},
        "spec": {"sex": sex, "model": heir["model"], "group": heir["group"], "parents": parents, "born": tick,
                 "generation": max(parent["generation"], spouse["generation"]) + 1},
    })
    # The child enters the in-memory world at the next tick; only its food is set now.
    patch_status(api, namespace, {"name": name}, food=2 * config.FOOD_FROM_EACH_PARENT)
    for p in (parent, spouse):
        patch_status(api, namespace, p, food=p["food"] - config.FOOD_FROM_EACH_PARENT)
    db.log("birth", name, parents=parents, sex=sex)
    return name
