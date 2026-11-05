import json

from fakes import FakeK8s

import food
from db import DB
from llm import describe, parse, prompt
from world import invalid_reason, visible_events

DAWN_DAY_2 = 96 + 28


def person(name, sex="M", food_=5):
    return {"name": name, "sex": sex, "model": "groq", "spouse": None, "promises": [], "alive": True, "role": None,
            "exiled_until": -1, "marks": [], "warnings": [], "food": food_, "children_banned_until": -1,
            "parents": [], "born": None, "generation": 0}


def worked(db, name, turns, day_start=28):
    for t in range(turns):
        db.set("tick", day_start + t)
        db.log("work", name)


def test_dawn_reckoning(tmp_path):
    db = DB(str(tmp_path / "w.db"))
    agents = {"adam": person("adam"), "eve": person("eve", "F"), "abel": person("abel", food_=-2)}
    worked(db, "adam", 8)
    worked(db, "eve", 7)
    food.feed(db, FakeK8s(), "w", agents, DAWN_DAY_2)
    # adam: 5 - 1 + 2; eve: 5 - 1 (7 turns are not enough); abel: -2 - 1 = -3, below -2.
    assert [agents[n]["food"] for n in ("adam", "eve", "abel")] == [6, 4, -3]
    assert agents["abel"]["alive"] is False
    assert db.q("select kind, agent from events where kind='starve'")[0]["agent"] == "abel"


def test_no_reckoning_at_the_first_dawn(tmp_path):
    agents = {"adam": person("adam")}
    food.feed(DB(str(tmp_path / "w.db")), FakeK8s(), "w", agents, 28)
    assert agents["adam"]["food"] == 5


def test_the_dead_do_not_eat(tmp_path):
    agents = {"abel": {**person("abel"), "alive": False}}
    food.feed(DB(str(tmp_path / "w.db")), FakeK8s(), "w", agents, DAWN_DAY_2)
    assert agents["abel"]["food"] == 5


def test_stealing_takes_at_most_what_the_victim_has():
    api = FakeK8s()
    cain, abel = person("cain", food_=1), person("abel", food_=2)
    assert food.steal(api, "w", cain, abel, 5) == 2
    assert (cain["food"], abel["food"]) == (3, 0)
    assert food.steal(api, "w", cain, abel, 1) == 0
    assert api.status == {"abel": {"food": 0}, "cain": {"food": 3}}


def test_give_and_steal_rules():
    adam, eve = person("adam", food_=2), person("eve", "F")
    living = {"adam": adam, "eve": eve}
    assert invalid_reason("give", {"target": "eve", "amount": 3}, adam, living, None, 40, living) == "not enough food"
    assert invalid_reason("give", {"target": "eve", "amount": 2}, adam, living, None, 40, living) is None
    assert invalid_reason("steal", {"target": "eve"}, adam, living, None, 40, living) == "amount must be at least 1"
    assert invalid_reason("steal", {"target": "eve", "amount": 0}, adam, living, None, 40, living) == \
        "amount must be at least 1"


def test_agents_know_their_food_and_the_rule():
    text = prompt(person("adam", food_=3), [person("eve", "F")], None, [], 40)[1]["content"]
    assert ("You have 3 food. Each dawn everyone eats 1; whoever worked at least 8 turns the day before also "
            "earns 2. Below -2 food you die of hunger.") in text
    assert "food" not in text.split("People:")[1].split("You have")[0]  # others' food stays private


def test_the_record_shows_what_was_really_taken():
    e = {"tick": 40, "kind": "steal", "agent": "cain", "data": json.dumps({"target": "abel", "amount": 5,
                                                                           "taken": 2})}
    assert describe(e, "abel") == "Week 1, day 1, 10:00: Cain stole food from you (2 food)"


def test_amount_is_kept_only_for_give_and_steal():
    base = {"target": "Eve", "message": None, "refrains": None, "promise": None, "kept": None, "amount": 2}
    assert parse({**base, "action": "give"}) == ("give", {"target": "eve", "amount": 2})
    assert parse({**base, "action": "observe"}) == ("observe", {"target": "eve"})


def test_everyone_learns_who_died_of_hunger(tmp_path):
    db = DB(str(tmp_path / "w.db"))
    db.set("tick", DAWN_DAY_2)
    db.log("starve", "abel", food=-3)
    assert [r["kind"] for r in visible_events(db, "adam", DAWN_DAY_2)] == ["starve"]
