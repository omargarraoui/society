import random

from db import DB
from world import awake, mock_action, pick

NAMES = ["adam", "eve"]
NOON = 48
MIDNIGHT = 0
PROMISE = {"metadata": {"name": "p"}}


def agent(name, model, sex="M", promise=PROMISE, spouse=None):
    return {"name": name, "model": model, "sex": sex, "promise": promise, "spouse": spouse}


def test_everyone_is_awake_at_noon():
    assert awake(NAMES, NOON, random.Random(0)) == NAMES


def test_at_most_one_awake_at_night():
    counts = {len(awake(NAMES, MIDNIGHT, random.Random(seed))) for seed in range(500)}
    assert counts == {0, 1}


def test_first_action_is_the_promise():
    me = agent("adam", "groq", promise=None)
    assert mock_action(random.Random(0), me, [agent("eve", "gemini")], None, {}) == (
        "promise", {"refrains": "steal", "body": "I will not steal."})


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
