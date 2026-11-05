import json
import random

import pytest
from fakes import FakeK8s

import births
import config
import god
from db import DB
from llm import describe, prompt
from world import invalid_reason

DAY = config.TICKS_PER_DAY
TICK = 10 * DAY + 28


def person(name, sex, spouse=None, food=5, parents=(), born=None, generation=0, model="groq", alive=True):
    return {"name": name, "sex": sex, "model": model, "group": model, "spouse": spouse, "promises": [],
            "alive": alive, "role": None, "exiled_until": -1, "marks": [], "warnings": [], "food": food,
            "children_banned_until": -1, "parents": list(parents), "born": born, "generation": generation}


def couple(**eve):
    adam = person("adam", "M", spouse="eve")
    return {"adam": adam, "eve": person("eve", "F", spouse="adam", model="gemini", **eve)}


def test_children_grow_up_after_seven_days():
    child = person("seth", "M", born=100)
    assert births.adult(person("adam", "M"), 0)
    assert not births.adult(child, 100 + 7 * DAY - 1)
    assert births.adult(child, 100 + 7 * DAY)


def test_kinship():
    eve = person("eve", "F")
    seth = person("seth", "M", parents=["adam", "eve"], generation=1)
    adah = person("adah", "F", parents=["adam", "eve"], generation=1)
    enos = person("enos", "M", parents=["adah", "seth"], generation=2)
    milcah = person("milcah", "F", parents=["adah", "seth"], generation=2)
    assert births.kin_forbids(eve, seth)  # parent and child
    assert not births.kin_forbids(seth, adah)  # siblings of the first generation
    assert births.kin_forbids(enos, milcah)  # siblings later on
    assert not births.kin_forbids(enos, person("zillah", "F", parents=["cain", "naamah"], generation=2))


@pytest.mark.parametrize("change, reason", [
    (lambda a: a.pop("eve"), "not married to a living spouse"),
    (lambda a: a["eve"].update(born=TICK - DAY), "both parents must be adults"),
    (lambda a: a.update({f"x{i}": person(f"x{i}", "M") for i in range(10)}), "the world is full"),
    (lambda a: (a["adam"].update(food=1), a["eve"].update(food=1)), "not enough food"),
    (lambda a: a["eve"].update(children_banned_until=TICK + 1), "children forbidden by God"),
    (lambda a: None, None),
])
def test_who_may_ask_for_a_child(change, reason):
    agents = couple()
    change(agents)
    assert births.why_no_child(agents["adam"], agents, agents, TICK) == reason


def test_birth(tmp_path):
    db, api, agents = DB(str(tmp_path / "w.db")), FakeK8s(), couple()
    agents["seth"] = person("seth", "M", alive=False)  # dead: his name is never given again
    name = births.birth(db, api, "w", agents["adam"], agents["eve"], agents, TICK, random.Random(1))
    child = api.created[0]
    assert child["metadata"]["name"] == name and name not in agents
    assert child["spec"]["parents"] == ["adam", "eve"] and child["spec"]["generation"] == 1
    assert child["spec"]["model"] in ("groq", "gemini") and child["spec"]["born"] == TICK
    assert (api.status[name]["food"], agents["adam"]["food"], agents["eve"]["food"]) == (2, 4, 4)
    assert db.one("select kind, agent from events")["kind"] == "birth"


def test_the_child_inherits_each_parents_model_about_half_the_time(tmp_path):
    models = []
    for seed in range(200):
        api, agents = FakeK8s(), couple()
        births.birth(DB(":memory:"), api, "w", agents["adam"], agents["eve"], agents, TICK, random.Random(seed))
        models.append(api.created[0]["spec"]["model"])
    assert 80 < models.count("groq") < 120


def god_answer(tmp_path, answers, agents, asked=("adam",)):
    db, api = DB(str(tmp_path / f"w{len(list(tmp_path.iterdir()))}.db")), FakeK8s()
    for a in asked:
        db.set("tick", TICK - 10)
        db.log("request_child", a, target=agents[a]["spouse"])
    god.answer_children(db, api, "w", agents, TICK, answers)
    return [(r["kind"], json.loads(r["data"])) for r in db.q("select kind, data from events where id>?",
                                                              (len(asked),))], api


def test_god_approves_a_request(tmp_path):
    events, api = god_answer(tmp_path, [{"parent": "Eve", "approve": True, "reason": "Be fruitful."}], couple())
    assert [k for k, _ in events] == ["birth"] and len(api.created) == 1


def test_god_refuses_a_request(tmp_path):
    events, _ = god_answer(tmp_path, [{"parent": "adam", "approve": False, "reason": "Not yet."}], couple())
    assert events == [("child_denied", {"target": "adam", "reason": "Not yet."})]


def test_god_cannot_give_an_unasked_child_or_two_in_a_day(tmp_path):
    answers = [{"parent": "adam", "approve": True, "reason": "x"}, {"parent": "eve", "approve": True, "reason": "x"}]
    events, api = god_answer(tmp_path, answers, couple(), asked=("adam", "eve"))
    assert [k for k, _ in events] == ["birth", "invalid"] and len(api.created) == 1
    events, _ = god_answer(tmp_path, [{"parent": "adam", "approve": True, "reason": "x"}], couple(), asked=())
    assert events[0][1]["reason"] == "no request from this parent"


def test_marriage_rules_for_children_and_kin():
    eve = person("eve", "F")
    seth = person("seth", "M", parents=["adam", "eve"], born=TICK - DAY, generation=1)
    living = {"eve": eve, "seth": seth}
    assert invalid_reason("propose_marriage", {"target": "seth"}, eve, living, None, TICK, living) == \
        "target is a child"
    seth["born"] = TICK - 8 * DAY
    assert invalid_reason("propose_marriage", {"target": "seth"}, eve, living, None, TICK, living) == "too close kin"


def test_agents_see_their_family_and_births():
    seth = person("seth", "M", parents=["adam", "eve"], generation=1)
    text = prompt(seth, [person("eve", "F", spouse="adam")], None, [], TICK)[1]["content"]
    assert "Your parents: Adam and Eve." in text
    text = prompt(person("adam", "M", spouse="eve"), [seth], None, [], TICK)[1]["content"]
    assert "- Seth, man, child of you and Eve" in text
    e = {"tick": TICK, "kind": "birth", "agent": "seth", "data": json.dumps({"parents": ["adam", "eve"], "sex": "M"})}
    assert describe(e, "adam").endswith("Seth was born to you and Eve")
