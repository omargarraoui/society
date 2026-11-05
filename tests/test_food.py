import json

from fakes import FakeK8s, at

import food
from db import DB
from llm import describe, parse, prompt
from world import invalid_reason, visible_events

DAWN_DAY_1 = at(1, 7)
DAWN_DAY_2 = at(2, 7)


def person(name, sex="M", food_=5):
    return {"name": name, "sex": sex, "model": "groq", "spouse": None, "promises": [], "alive": True, "role": None,
            "exiled_until": -1, "marks": [], "warnings": [], "food": food_, "children_banned_until": -1,
            "parents": [], "born": None, "generation": 0}


def worked(db, name, turns, day_start=DAWN_DAY_1):
    for t in range(turns):
        db.set("tick", day_start + t)
        db.log("work", name)


def test_dawn_reckoning(tmp_path):
    db = DB(str(tmp_path / "w.db"))
    agents = {"adam": person("adam"), "eve": person("eve", "F"), "abel": person("abel", food_=-1)}
    worked(db, "adam", 9)
    worked(db, "eve", 17)
    food.feed(db, FakeK8s(), "w", agents, DAWN_DAY_2)
    # Harvest 3 * 3 = 9, by hours 9/26 and 17/26: 3.12 and 5.88 -> 3 and 5, the spare unit to Eve -> 3 and 6.
    # Then everyone eats 2: adam 5 + 3 - 2 = 6, eve 5 + 6 - 2 = 9, abel -1 + 0 - 2 = -3, below -2.
    assert [agents[n]["food"] for n in ("adam", "eve", "abel")] == [6, 9, -3]
    assert agents["abel"]["alive"] is False
    harvest = db.one("select data from events where kind='harvest'")["data"]
    assert json.loads(harvest) == {"total": 9, "shares": {"adam": 3, "eve": 6}, "target": "all"}


def test_shares_add_up_to_the_harvest():
    assert food.shares(6, {"eve": 17, "adam": 9}) == {"eve": 4, "adam": 2}
    assert food.shares(6, {"eve": 0, "adam": 0}) == {"eve": 0, "adam": 0}
    assert food.shares(5, {"a": 1, "b": 1, "c": 1}) == {"a": 2, "b": 2, "c": 1}
    assert sum(food.shares(9, {"a": 3, "b": 4, "c": 9}).values()) == 9


def test_no_reckoning_at_the_first_dawn(tmp_path):
    agents = {"adam": person("adam")}
    food.feed(DB(str(tmp_path / "w.db")), FakeK8s(), "w", agents, at(1, 7))
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
    assert ("You have 3 food. Each dawn the world yields 3 food per person alive, shared among those who worked "
            "the day before in proportion to their hours; then everyone eats 2. Below -2 food you die of hunger."
            ) in text
    assert "food" not in text.split("People:")[1].split("You have")[0]  # others' food stays private


def test_the_record_shows_what_was_really_taken():
    e = {"tick": at(1, 10), "kind": "steal", "agent": "cain", "data": json.dumps({"target": "abel", "amount": 5,
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


def test_everyone_sees_how_the_harvest_was_shared():
    e = {"tick": DAWN_DAY_2, "kind": "harvest", "agent": "world",
         "data": json.dumps({"total": 6, "shares": {"eve": 4, "adam": 2}, "target": "all"})}
    assert describe(e, "adam") == "Week 1, day 2, 07:00: The harvest of 6 food was shared by hours worked: Eve 4, you 2"
