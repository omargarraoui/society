import pytest

from metrics import segregation

AGENTS = [
    {"name": "a1", "sex": "M", "group": "g1"},
    {"name": "a2", "sex": "F", "group": "g1"},
    {"name": "b1", "sex": "M", "group": "g2"},
    {"name": "b2", "sex": "F", "group": "g2"},
]


def ev(agent, kind, target, kept=None):
    return {"agent": agent, "kind": kind, "target": target, "kept": kept}


def test_ratio_against_uniform_choice():
    # Each speak by a1 expects 1/3 toward g1 (a2) and 2/3 toward g2 (b1, b2).
    # 4 speaks: expected g1 = 4/3, g2 = 8/3; observed 2 and 2.
    m = segregation([ev("a1", "speak", "a2")] * 2 + [ev("a1", "speak", "b1"), ev("a1", "speak", "b2")], AGENTS)
    assert m["ratio"]["g1->g1"] == pytest.approx(1.5)
    assert m["ratio"]["g1->g2"] == pytest.approx(0.75)
    assert m["in_group_ratio"] == pytest.approx(1.5)


def test_proposals_expect_only_opposite_sex():
    # a1 can propose to a2 or b2: expected 1/2 each, so one proposal to a2 gives ratio 2.
    m = segregation([ev("a1", "propose_marriage", "a2")], AGENTS)
    assert m["ratio"]["g1->g1"] == pytest.approx(2.0)


def test_marriages_by_group_pair():
    m = segregation([ev("a2", "accept_marriage", "a1"), ev("b2", "accept_marriage", "a1")], AGENTS)
    assert m["marriages"] == {"g1+g1": 1, "g1+g2": 1}
    assert m["in_group_marriages"] == 1


def test_trust_gap_is_in_group_minus_out_group():
    events = [ev("a1", "assess", "a2", kept=1), ev("a1", "assess", "b1", kept=0), ev("a1", "assess", "b2", kept=1)]
    m = segregation(events, AGENTS)
    assert m["trust_gap"] == pytest.approx(0.5)
    assert m["trust_gap_by_group"] == {"g1": pytest.approx(0.5)}


def test_no_trust_gap_without_both_sides():
    assert segregation([ev("a1", "assess", "a2", kept=1)], AGENTS)["trust_gap"] is None
