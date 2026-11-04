import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

import config
from db import DB, quota_day
from llm import QuotaExhausted, call, clock, describe, parse, prompt

PROMISE = {"metadata": {"name": "p"}, "spec": {"body": "I will not steal."}}
ADAM = {"name": "adam", "sex": "M", "model": "groq", "group": "groq", "spouse": "eve", "promise": PROMISE}
EVE = {"name": "eve", "sex": "F", "model": "gemini", "group": "gemini", "spouse": "adam", "promise": None}


def event(tick, kind, agent, **data):
    return {"tick": tick, "kind": kind, "agent": agent, "data": json.dumps(data)}


def test_clock():
    assert clock(0) == "day 1, 00:00"
    assert clock(28) == "day 1, 07:00"
    assert clock(96 + 49) == "day 2, 12:15"


def test_describe_from_the_agent_point_of_view():
    said = describe(event(30, "speak", "eve", target="adam", message="Hi"), "adam")
    judged = describe(event(31, "assess", "adam", target="eve", kept=False), "adam")
    assert said == 'Day 1, 07:30: Eve said to you: "Hi"'
    assert judged == "Day 1, 07:45: you judged the promise of Eve (broken)"


def test_prompt_never_reveals_model_or_group():
    events = [event(30, "speak", "eve", target="adam", message="Hi")]
    text = json.dumps(prompt(ADAM, [EVE], EVE, events, 40))
    for hidden in [*config.PROVIDERS, *(p["model"] for p in config.PROVIDERS.values())]:
        assert hidden.lower() not in text.lower()


def test_prompt_contains_what_the_agent_needs():
    text = prompt(ADAM, [EVE], EVE, [], 40)[1]["content"]
    assert "Eve, woman, married to you" in text
    assert 'You promised: "I will not steal."' in text
    assert "Eve has asked you to marry them." in text


def test_exhausted_quota_stops_before_any_request(tmp_path):
    quota = DB(str(tmp_path / "q.db"))
    for _ in range(config.PROVIDERS["groq"]["daily_requests"]):
        quota.add_usage("groq", 1)
    with pytest.raises(QuotaExhausted):
        call("groq", [], quota)


def test_usage_counts_requests_and_tokens(tmp_path):
    quota = DB(str(tmp_path / "q.db"))
    quota.add_usage("groq", 700)
    quota.add_usage("groq", 300)
    assert quota.usage("groq") == (2, 1000)


def test_parse_maps_the_displayed_name_back_to_the_id():
    # Seen live on Groq: the model answers with the capitalized name it was shown.
    answer = {"action": "speak", "target": "Eve", "message": "Good morning", "refrains": None, "kept": None}
    assert parse(answer) == ("speak", {"target": "eve", "message": "Good morning"})


def test_gemini_quota_day_follows_pacific_time():
    now_pacific = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    now_utc = datetime.now(UTC).strftime("%Y-%m-%d")
    assert quota_day("gemini") == now_pacific
    assert quota_day("groq") == now_utc


def test_prompt_shows_what_others_promised():
    eve = {**EVE, "promise": {"metadata": {"name": "q"}, "spec": {"body": "I will rest on the seventh day."}}}
    text = prompt(ADAM, [eve], None, [], 40)[1]["content"]
    assert 'Eve, woman, married to you, promised: "I will rest on the seventh day."' in text
