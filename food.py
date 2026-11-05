"""Food: the dawn reckoning, hunger, and food changing hands."""
import config
from state import patch_status


def feed(db, api, namespace, agents, tick):
    """At dawn: everyone pays UPKEEP_PER_DAY; who worked enough the day before earns WORK_GAIN."""
    if tick < config.TICKS_PER_DAY:
        return  # the world's first dawn has no day before it
    for agent in agents.values():
        if not agent["alive"]:
            continue
        worked = db.one("select count(*) as n from events where agent=? and kind='work' and tick>=? and tick<?",
                        (agent["name"], tick - config.TICKS_PER_DAY, tick))["n"]
        gain = config.WORK_GAIN if worked >= config.WORK_TURNS_FOR_FOOD else 0
        food = agent["food"] - config.UPKEEP_PER_DAY + gain
        if food < config.STARVE_BELOW:
            patch_status(api, namespace, agent, food=food, alive=False)
            db.log("starve", agent["name"], food=food)
        else:
            patch_status(api, namespace, agent, food=food)


def steal(api, namespace, thief, victim, amount):
    """Stealing from someone with nothing takes nothing, but it is still a theft in the record."""
    taken = min(amount, max(victim["food"], 0))
    move(api, namespace, victim, thief, taken)
    return taken


def move(api, namespace, giver, receiver, amount):
    patch_status(api, namespace, giver, food=giver["food"] - amount)
    patch_status(api, namespace, receiver, food=receiver["food"] + amount)
