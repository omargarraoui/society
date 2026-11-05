"""Segregation by group, computed from the event log and the agent list only."""
from collections import Counter
from statistics import mean

INTERACTIONS = {"speak", "give", "propose_marriage", "assess", "observe"}


def event_rows(db):
    return db.q("select agent, kind, json_extract(data, '$.target') as target, "
                "json_extract(data, '$.kept') as kept from events")


def segregation(events, agents):
    """events: rows with agent, kind, target, kept. agents: dicts with name, sex, group.

    The baseline picks a target uniformly among the other agents (opposite sex for
    proposals). It ignores who was married at the time; the control world absorbs that.
    """
    group = {a["name"]: a["group"] for a in agents}
    sex = {a["name"]: a["sex"] for a in agents}
    labels = sorted(set(group.values()))
    observed, expected, marriages = Counter(), Counter(), Counter()
    trust = {g: {True: [], False: []} for g in labels}

    for e in events:
        if e["kind"] == "accept_marriage":
            marriages[tuple(sorted((group[e["agent"]], group[e["target"]])))] += 1
        if e["kind"] not in INTERACTIONS:
            continue
        mine = group[e["agent"]]
        observed[mine, group[e["target"]]] += 1
        pool = [n for n in group if n != e["agent"] and (e["kind"] != "propose_marriage" or sex[n] != sex[e["agent"]])]
        for n in pool:
            expected[mine, group[n]] += 1 / len(pool)
        if e["kind"] == "assess":
            trust[mine][group[e["target"]] == mine].append(e["kept"])

    in_group = [(g, g) for g in labels]
    in_expected = sum(expected[p] for p in in_group)
    same =[k for t in trust.values() for k in t[True]]
    different = [k for t in trust.values() for k in t[False]]
    return {
        "ratio": {f"{a}->{b}": observed[a, b] / expected[a, b] for a in labels for b in labels if expected[a, b]},
        # None, not 0: with no interactions there is no ratio to report.
        "in_group_ratio": sum(observed[p] for p in in_group) / in_expected if in_expected else None,
        "marriages": {"+".join(pair): n for pair, n in marriages.items()},
        "in_group_marriages": sum(marriages[p] for p in in_group),
        "all_marriages": sum(marriages.values()),
        "trust_gap": mean(same) - mean(different) if same and different else None,
        "trust_gap_by_group": {g: mean(t[True]) - mean(t[False]) for g, t in trust.items() if t[True] and t[False]},
    }
