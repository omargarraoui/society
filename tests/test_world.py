import random

import pytest
from fakes import at

from db import DB
from world import awake, invalid_reason, mock_action, mock_brain, observed, pick

NAMES = ["adam", "eve"]
NOON = at(1, 12)
MIDNIGHT = 0
PROMISE = {"metadata": {"name": "p"}, "spec": {"refrains": "steal", "tick": 0}}


TICK = 40


def agent(name, model, sex="M", promises=(PROMISE,), spouse=None, role=None, exiled_until=-1):
    return {"name": name, "model": model, "sex": sex, "promises": list(promises), "spouse": spouse, "alive": True,
            "role": role, "exiled_until": exiled_until, "marks": [], "warnings": [], "food": 5,
            "children_banned_until": -1, "parents": [], "born": None, "generation": 0}


def test_everyone_is_awake_at_noon():
    assert awake(NAMES, NOON, random.Random(0)) == NAMES


def test_at_most_one_awake_at_night():
    counts = {len(awake(NAMES, MIDNIGHT, random.Random(seed))) for seed in range(500)}
    assert counts == {0, 1}


def test_first_action_is_the_promise():
    me = agent("adam", "groq", promises=())
    assert mock_action(random.Random(0), me, [agent("eve", "gemini")], None, {}) == (
        "promise", {"refrains": "steal", "message": "I will not steal."})


def test_a_pending_proposal_is_sometimes_accepted():
    eve = agent("eve", "gemini", sex="F")
    kinds = {mock_action(random.Random(s), agent("adam", "groq"), [eve], eve, {})[0] for s in range(50)}
    assert "accept_marriage" in kinds


def test_married_agents_never_propose():
    me = agent("adam", "groq", spouse="eve")
    others = [agent("eve", "gemini", sex="F", spouse="adam"), agent("adah", "groq", sex="F")]
    kinds = {mock_action(random.Random(s), me, others, None, {})[0] for s in range(300)}
    assert "propose_marriage" not in kinds


def test_full_bias_always_picks_own_model():
    me = agent("adam", "groq")
    candidates = [agent("seth", "groq"), agent("enos", "gemini"), agent("cain", "cloudflare")]
    picked = {pick(random.Random(s), me, candidates, {"groq": 1.0})["model"] for s in range(200)}
    assert picked == {"groq"}


def test_zero_bias_never_picks_own_model():
    me = agent("adam", "groq")
    candidates = [agent("seth", "groq"), agent("enos", "gemini")]
    picked = {pick(random.Random(s), me, candidates, {"groq": 0.0})["model"] for s in range(200)}
    assert picked == {"gemini"}


def test_bias_of_another_model_does_not_apply():
    me = agent("adam", "gemini")
    candidates = [agent("seth", "groq"), agent("enos", "gemini")]
    picked = {pick(random.Random(s), me, candidates, {"groq": 1.0})["model"] for s in range(200)}
    assert picked == {"groq", "gemini"}


def _db_with_steal(tmp_path, agent_name, tick):
    db = DB(str(tmp_path / "t.db"))
    db.set("tick", tick)
    db.log("steal", agent_name, target="eve")
    return db


def test_steal_after_promise_is_a_violation(tmp_path):
    assert _db_with_steal(tmp_path, "adam", tick=30).violations("adam", "steal", since_tick=29) == 1


def test_steal_before_promise_does_not_count(tmp_path):
    assert _db_with_steal(tmp_path, "adam", tick=30).violations("adam", "steal", since_tick=31) == 0


def test_someone_elses_steal_does_not_count(tmp_path):
    assert _db_with_steal(tmp_path, "eve", tick=30).violations("adam", "steal", since_tick=0) == 0


ADAM = agent("adam", "groq")
EVE = agent("eve", "gemini", sex="F")
SETH = agent("seth", "groq")
AGENTS = {"adam": ADAM, "eve": EVE, "seth": SETH}


@pytest.mark.parametrize("kind, data, proposer, reason", [
    ("speak", {"target": "eve", "message": "hello"}, None, None),
    ("speak", {"target": "adam", "message": "hello"}, None, "target is not another person"),
    ("speak", {"target": "noah", "message": "hello"}, None, "target is not another person"),
    ("speak", {"target": "eve"}, None, "nothing said"),
    ("speak", {"target": "eve", "message": "x" * 301}, None, "message too long"),
    ("promise", {"refrains": "steal", "message": "I will not steal."}, None, None),  # a second promise
    ("propose_marriage", {"target": "seth"}, None,
     "marriage is between an unmarried man and an unmarried woman"),
    ("propose_marriage", {"target": "eve"}, None, None),
    ("accept_marriage", {"target": "eve"}, None, "no pending proposal from target"),
    ("accept_marriage", {"target": "eve"}, EVE, None),
    ("assess", {"target": "eve", "kept": False}, None, "assess needs one of target's promises"),
    ("assess", {"target": "eve", "promise": "zzz", "kept": False}, None, "assess needs one of target's promises"),
    ("assess", {"target": "eve", "promise": "p"}, None, "assess needs kept true or false"),
    ("assess", {"target": "eve", "promise": "p", "kept": False}, None, None),
    ("fly", {}, None, "unknown action"),
])
def test_invalid_reason(kind, data, proposer, reason):
    assert invalid_reason(kind, data, ADAM, AGENTS, proposer, TICK, AGENTS) == reason


def test_first_promise_is_valid():
    me = agent("adam", "groq", promises=())
    data = {"refrains": "steal", "message": "I will not steal."}
    living = {"adam": me, "eve": EVE}
    assert invalid_reason("promise", data, me, living, None, TICK, living) is None


def test_mock_only_produces_valid_actions(tmp_path):
    others = [EVE, SETH, agent("adah", "gemini", sex="F", promises=())]
    decide = mock_brain({}, DB(str(tmp_path / "t.db")))
    for seed in range(300):
        kind, data = decide(random.Random(seed), ADAM, others, EVE, tick=40)
        living = {a["name"]: a for a in [ADAM, *others]}
        assert invalid_reason(kind, data, ADAM, living, EVE, TICK, living) is None, (kind, data)


@pytest.mark.parametrize("data, reason", [
    ({"message": "I promise to keep the day of rest holy."}, None),
    ({"message": "I will not steal.", "refrains": "steal"}, None),
    ({"message": "I will not kill.", "refrains": "kill"}, "unknown refrains"),
    ({"message": "I will rest on the seventh day.", "refrains": "work_on_day_of_rest"}, None),
    ({"refrains": "steal"}, "a promise needs words"),
])
def test_free_promises_are_accepted(data, reason):
    me = agent("adam", "groq", promises=())
    assert invalid_reason("promise", data, me, {"adam": me, "eve": EVE}, None, TICK, {"adam": me, "eve": EVE}) == reason


def test_mock_never_assesses_a_promise_it_cannot_check(tmp_path):
    free = {"metadata": {"name": "f"}, "spec": {"body": "I will rest on the seventh day.", "tick": 0}}
    others = [agent("eve", "gemini", sex="F", promises=[free])]
    decide = mock_brain({}, DB(str(tmp_path / "t.db")))
    kinds = {decide(random.Random(s), ADAM, others, None, tick=40)[0] for s in range(300)}
    assert "assess" not in kinds


def test_working_on_the_day_of_rest_breaks_the_rest_promise(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    for tick, kind in [(at(6, 10), "work"), (at(7, 10), "work"), (at(7, 11), "rest"), (at(14, 10), "work")]:
        db.set("tick", tick)
        db.log(kind, "eve")
    # Day 6 is a normal day; days 7 and 14 are days of rest: 2 violations.
    assert db.violations("eve", "work_on_day_of_rest", since_tick=0) == 2
    assert db.violations("eve", "work_on_day_of_rest", since_tick=at(8, 0)) == 1


def test_observe_shows_recent_actions_without_prayers_or_words(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    log = [(at(1, 8), "work", {}), (at(1, 9), "pray", {"message": "secret"}),
           (at(1, 10), "speak", {"target": "adam", "message": "hi"}), (at(1, 11), "steal", {"target": "adam"})]
    for tick, kind, data in log:
        db.set("tick", tick)
        db.log(kind, "eve", **data)
    assert observed(db, "eve", tick=at(1, 12)) == [
        {"tick": at(1, 8), "kind": "work"}, {"tick": at(1, 10), "kind": "speak", "target": "adam"},
        {"tick": at(1, 11), "kind": "steal", "target": "adam"}]


def test_observe_shows_at_most_the_limit_and_nothing_older_than_yesterday(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    for tick in [at(1, 2), *range(at(2, 6), at(2, 6) + 12)]:  # one turn on day 1, twelve on day 2
        db.set("tick", tick)
        db.log("work", "eve")
    seen = observed(db, "eve", tick=at(3, 6))  # day 3: yesterday = day 2
    assert [s["tick"] for s in seen] == list(range(at(2, 10), at(2, 18)))


def test_mock_never_gives_food_it_does_not_have(tmp_path):
    poor = {**agent("adam", "groq"), "food": 0}
    others = [EVE, SETH]
    decide = mock_brain({}, DB(str(tmp_path / "t.db")))
    kinds = {decide(random.Random(s), poor, others, None, tick=TICK)[0] for s in range(300)}
    assert "give" not in kinds
