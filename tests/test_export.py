from db import DB
from export import snapshot, versioned

PROMISE = {"metadata": {"name": "eve-p1"}, "spec": {"body": "I will not steal.", "refrains": "steal", "tick": 30},
           "status": {"state": "kept", "violations": 0}}


def person(name, sex, model, alive=True, promises=(), born=None, parents=()):
    return {"name": name, "sex": sex, "model": model, "group": model, "alive": alive, "food": 5, "spouse": None,
            "role": None, "parents": list(parents), "born": born, "generation": 0, "exiled_until": -1,
            "warnings": [], "marks": [], "children_banned_until": -1, "promises": list(promises)}


def world(tmp_path):
    db = DB(str(tmp_path / "w.db"))
    for tick, kind, agent, data in [
        (30, "work", "adam", {}), (31, "speak", "adam", {"target": "eve", "message": "Hi"}),
        (32, "observe", "adam", {"target": "eve", "saw": [{"tick": 30, "kind": "work"}]}),
        (33, "assess", "adam", {"target": "eve", "promise": "eve-p1", "kept": True}),
        (34, "skip", "eve", {"reason": "gemini: quota exhausted"}),
    ]:
        db.set("tick", tick)
        db.log(kind, agent, **data)
    agents = {"adam": person("adam", "M", "groq"), "eve": person("eve", "F", "gemini", promises=[PROMISE]),
              "abel": person("abel", "M", "groq", alive=False),
              "seth": person("seth", "M", "groq", born=30, parents=["adam", "eve"])}
    return snapshot("genesis", db, agents, "2026-11-05T12:00:00+00:00", speed=2.9)


def test_the_public_page_shows_models_and_promise_verdicts(tmp_path):
    s = world(tmp_path)
    eve = next(p for p in s["people"] if p["name"] == "eve")
    assert eve["model"] == "gemini"
    assert eve["promises"] == [{"id": "eve-p1", "body": "I will not steal.", "refrains": "steal", "tick": 30,
                                "state": "kept", "violations": 0, "judged_kept": 1, "judged_broken": 0}]


def test_the_feed_leaves_out_routine_and_what_was_seen(tmp_path):
    feed = world(tmp_path)["feed"]
    assert [e["kind"] for e in feed] == ["assess", "observe", "speak"]
    assert feed[1]["data"] == {"target": "eve"}


def test_groups_count_only_the_living_and_children_are_flagged(tmp_path):
    s = world(tmp_path)
    assert s["groups"] == {"groq": 2, "gemini": 1}
    assert [p["name"] for p in s["people"] if p["child"]] == ["seth"]
    assert s["totals"]["work"] == 1


def test_assets_get_a_version_that_changes_with_their_content():
    index = '<link href="style.css"><script src="app.js"></script>'
    one = versioned(index, {"app.js": b"a", "style.css": b"s"})
    assert one == '<link href="style.css?v=043a7187"><script src="app.js?v=ca978112"></script>'
    assert versioned(index, {"app.js": b"b", "style.css": b"s"}) != one


def test_the_page_rules_come_from_config(tmp_path):
    rules = world(tmp_path)["rules"]
    assert (rules["turns_per_day"], rules["turn_length"], rules["work_turns"], rules["starve_at"]) == (
        24, "world hour", 2, -3)


def test_each_person_shows_what_they_did(tmp_path):
    adam = next(p for p in world(tmp_path)["people"] if p["name"] == "adam")
    assert adam["did"] == {"work": 1, "speak": 1, "observe": 1, "assess": 1}
