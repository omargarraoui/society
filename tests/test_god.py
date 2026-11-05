import json

import pytest
from fakes import FakeK8s

import config
import god
from db import DB
from world import invalid_reason, visible_events

TICK = 28  # dawn of day 1
NOW = 1_000_000.0
DAY = 24 * 60 * 60


def person(name, sex, role=None):
    return {"name": name, "sex": sex, "model": "groq", "spouse": None, "promises": [], "alive": True, "role": role,
            "exiled_until": -1, "marks": [], "warnings": [], "food": 5, "children_banned_until": -1,
            "parents": [], "born": None, "generation": 0}


def world(tmp_path):
    db = DB(str(tmp_path / "w.db"))
    db.set("tick", TICK)
    return db, FakeK8s(), {"adam": person("adam", "M", role="representative"), "eve": person("eve", "F")}


def sentence(target, penalty, days=None, amount=None, victim=None, reason="x"):
    return {"target": target, "penalty": penalty, "days": days, "amount": amount, "victim": victim, "reason": reason}


def answer(revelations=(), representative=None, penalties=()):
    return {"revelations": list(revelations), "representative": representative, "penalties": list(penalties)}


def kinds(db):
    return [(r["kind"], json.loads(r["data"])) for r in db.q("select kind, data from events order by id")]


def test_revelations_to_a_person_or_to_all_and_unknown_names_rejected(tmp_path):
    db, api, agents = world(tmp_path)
    god.apply(db, api, "w", agents, TICK, answer([{"to": "Eve", "message": "Rest."}, {"to": "all", "message": "Peace."},
                                                  {"to": "Noah", "message": "Hello."}]), NOW)
    assert kinds(db)[:2] == [("revelation", {"target": "eve", "message": "Rest."}),
                             ("revelation", {"target": "all", "message": "Peace."})]
    assert kinds(db)[2][0] == "invalid"


def test_appointing_moves_the_role(tmp_path):
    db, api, agents = world(tmp_path)
    god.apply(db, api, "w", agents, TICK, answer(representative="Eve"), NOW)
    assert (agents["adam"]["role"], agents["eve"]["role"]) == (None, "representative")
    assert api.status == {"adam": {"role": None}, "eve": {"role": "representative"}}


def test_warning_is_immediate(tmp_path):
    db, api, agents = world(tmp_path)
    god.apply(db, api, "w", agents, TICK, answer(penalties=[sentence("adam", "warning", reason="He stole from Eve.")]),
              NOW)
    assert agents["adam"]["warnings"] == ["He stole from Eve."]
    assert db.one("select status from decisions")["status"] == "applied"


def test_exile_waits_for_the_veto_window_then_applies(tmp_path):
    db, api, agents = world(tmp_path)
    god.apply(db, api, "w", agents, TICK, answer(penalties=[sentence("eve", "exile", days=2, reason="She lied.")]), NOW)
    god.apply_due(db, api, "w", agents, TICK + 1, NOW + DAY - 1)
    assert agents["eve"]["exiled_until"] == -1
    god.apply_due(db, api, "w", agents, TICK + 1, NOW + DAY)
    assert agents["eve"]["exiled_until"] == TICK + 1 + 2 * config.TICKS_PER_DAY
    assert kinds(db)[-1] == ("penalty", {"target": "eve", "penalty": "exile", "decision": 1})


def test_a_pardoned_sentence_is_never_applied(tmp_path):
    db, api, agents = world(tmp_path)
    god.apply(db, api, "w", agents, TICK, answer(penalties=[sentence("eve", "death")]), NOW)
    db.x("update decisions set status='vetoed' where id=1")
    god.apply_due(db, api, "w", agents, TICK, NOW + 2 * DAY)
    assert agents["eve"]["alive"] is True


@pytest.mark.parametrize("days", [None, 0, config.MAX_PENALTY_DAYS + 1])
def test_exile_needs_a_valid_length(tmp_path, days):
    db, api, agents = world(tmp_path)
    god.apply(db, api, "w", agents, TICK, answer(penalties=[sentence("eve", "exile", days=days)]), NOW)
    assert kinds(db)[0][0] == "invalid"
    assert db.one("select count(*) as n from decisions")["n"] == 0


def test_a_second_death_in_a_day_pauses_the_world(tmp_path):
    db, api, agents = world(tmp_path)
    deaths = [sentence(t, "death") for t in ("adam", "eve")]
    god.apply(db, api, "w", agents, TICK, answer(penalties=deaths), NOW)
    assert db.get("paused") is True
    assert [k for k, _ in kinds(db)] == ["sentence", "anomaly"]
    assert db.one("select count(*) as n from decisions")["n"] == 1


def test_god_sees_neither_model_nor_group(tmp_path):
    agents = world(tmp_path)[2]
    text = json.dumps(god.prompt("Constitution.", agents, TICK, [], []))
    assert "groq" not in text and "Adam, man; food 5; the representative" in text


def test_exile_and_role_rules_for_agents():
    adam, eve = person("adam", "M"), person("eve", "F", role="representative")
    living = {"adam": adam, "eve": eve}
    assert invalid_reason("petition", {"message": "Rain."}, adam, living, None, TICK, living) == \
        "only the representative petitions"
    assert invalid_reason("petition", {"message": "Rain."}, eve, living, None, TICK, living) is None
    eve["exiled_until"] = TICK + 10
    assert invalid_reason("speak", {"target": "eve", "message": "Hi"}, adam, living, None, TICK, living) == \
        "target is in exile"
    assert invalid_reason("speak", {"target": "adam", "message": "Hi"}, eve, living, None, TICK, living) == "in exile"
    assert invalid_reason("work", {}, eve, living, None, TICK, living) is None


def test_agents_see_revelations_to_all_and_to_them_only(tmp_path):
    db = world(tmp_path)[0]
    db.log("revelation", "god", target="all", message="Peace.")
    db.log("revelation", "god", target="eve", message="Rest.")
    seen = [json.loads(r["data"])["message"] for r in visible_events(db, "adam", TICK)]
    assert seen == ["Peace."]


def judge(tmp_path, *penalties, steals=()):
    db, api, agents = world(tmp_path)
    for thief, victim, taken in steals:
        db.log("steal", thief, target=victim, amount=taken, taken=taken)
    god.apply(db, api, "w", agents, TICK, answer(penalties=list(penalties)), NOW)
    return db, agents, [(k, d.get("reason")) for k, d in kinds(db) if k in ("sentence", "invalid")]


def test_fine(tmp_path):
    _, agents, outcomes = judge(tmp_path, sentence("adam", "fine", amount=2), sentence("eve", "fine", amount=6))
    assert agents["adam"]["food"] == 3
    assert outcomes == [("sentence", "x"), ("invalid", "a fine is 1-5 food")]


def test_restitution_gives_back_at_most_what_was_stolen(tmp_path):
    _, agents, outcomes = judge(tmp_path, sentence("adam", "restitution", amount=2, victim="Eve"),
                                sentence("adam", "restitution", amount=2, victim="eve"), steals=[("adam", "eve", 3)])
    assert (agents["adam"]["food"], agents["eve"]["food"]) == (3, 7)
    assert outcomes == [("sentence", "x"), ("invalid", "restitution is at most what the record shows was stolen")]


def test_no_children_and_lose_role(tmp_path):
    _, agents, outcomes = judge(tmp_path, sentence("eve", "no_children", days=3), sentence("adam", "lose_role"),
                                sentence("eve", "lose_role"))
    assert agents["eve"]["children_banned_until"] == TICK + 3 * config.TICKS_PER_DAY
    assert agents["adam"]["role"] is None
    assert outcomes == [("sentence", "x"), ("sentence", "x"), ("invalid", "target has no role")]
