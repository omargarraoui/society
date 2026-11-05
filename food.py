"""Food: the dawn reckoning, hunger, and food changing hands."""
import config
from state import patch_status


def shares(total, hours):
    """total split in proportion to hours, in whole units that add up to total (largest remainder first)."""
    worked = sum(hours.values())
    if not worked:
        return dict.fromkeys(hours, 0)
    exact = {name: total * h / worked for name, h in hours.items()}
    result = {name: int(x) for name, x in exact.items()}
    by_remainder = sorted(exact, key=lambda name: (exact[name] - result[name], hours[name]), reverse=True)
    for name in by_remainder[:total - sum(result.values())]:
        result[name] += 1
    return result


def feed(db, api, namespace, agents, tick):
    """At dawn: the common harvest is shared by yesterday's hours of work, then everyone eats."""
    if tick < config.TICKS_PER_DAY:
        return  # the world's first dawn has no day before it
    living = [a for a in agents.values() if a["alive"]]
    hours = {a["name"]: db.one("select count(*) as n from events where agent=? and kind='work' and tick>=? "
                               "and tick<?", (a["name"], tick - config.TICKS_PER_DAY, tick))["n"] for a in living}
    total = config.HARVEST_PER_PERSON * len(living)
    given = shares(total, hours)
    db.log("harvest", "world", total=total, shares={n: s for n, s in given.items() if s}, target="all")
    for agent in living:
        food = agent["food"] + given[agent["name"]] - config.UPKEEP_PER_DAY
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
