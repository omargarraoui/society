import random

from db import DB
from promise_operator import assess
from world import awake, mock_action

NAMES = ["Adam", "Eve"]
NOON = 48
MIDNIGHT = 0


def test_everyone_is_awake_at_noon():
    assert awake(NAMES, NOON, random.Random(0)) == NAMES


def test_at_most_one_awake_at_night():
    counts = {len(awake(NAMES, MIDNIGHT, random.Random(seed))) for seed in range(500)}
    assert counts == {0, 1}


def test_first_action_is_the_promise():
    assert mock_action(random.Random(0), ["Eve"], promised=False) == (
        "promise", {"refrains": "steal", "body": "I will not steal."})


def test_after_promising_the_agent_works_or_steals():
    kinds = {mock_action(random.Random(seed), ["Eve"], promised=True)[0] for seed in range(100)}
    assert kinds == {"work", "steal"}


def _db_with_steal(tmp_path, agent, tick):
    db = DB(str(tmp_path / "t.db"))
    db.set("tick", tick)
    db.log("steal", agent, target="Eve")
    return db


def test_steal_after_promise_breaks_it(tmp_path):
    db = _db_with_steal(tmp_path, "Adam", tick=30)
    assert assess(db, "Adam", "steal", since_tick=29) == {"state": "broken", "violations": 1, "checkedTick": 30}


def test_steal_before_promise_does_not_count(tmp_path):
    db = _db_with_steal(tmp_path, "Adam", tick=30)
    assert assess(db, "Adam", "steal", since_tick=31)["state"] == "kept"


def test_someone_elses_steal_does_not_count(tmp_path):
    db = _db_with_steal(tmp_path, "Eve", tick=30)
    assert assess(db, "Adam", "steal", since_tick=0)["violations"] == 0
