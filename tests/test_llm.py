import json
import time
import types
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import openai
import pytest
from fakes import at

import config
from db import DB, quota_day
from llm import QuotaExhausted, call, clock, describe, parse, prompt

PROMISE = {"metadata": {"name": "p"}, "spec": {"body": "I will not steal."}}
NOTHING_FROM_GOD = {"alive": True, "role": None, "exiled_until": -1, "marks": [], "warnings": [], "food": 5,
                    "children_banned_until": -1, "parents": [], "born": None, "generation": 0}
ADAM = {"name": "adam", "sex": "M", "model": "groq", "group": "groq", "spouse": "eve", "promises": [PROMISE],
        **NOTHING_FROM_GOD}
EVE = {"name": "eve", "sex": "F", "model": "gemini", "group": "gemini", "spouse": "adam", "promises": [],
       **NOTHING_FROM_GOD}


def event(tick, kind, agent, **data):
    return {"tick": tick, "kind": kind, "agent": agent, "data": json.dumps(data)}


def test_clock():
    assert clock(0) == "week 1, day 1, 00:00"
    assert clock(at(1, 7)) == "week 1, day 1, 07:00"
    assert clock(at(2, 12)) == "week 1, day 2, 12:00"
    assert clock(at(7, 10)) == "week 1, day 7, the day of rest, 10:00"
    assert clock(at(8, 0)) == "week 2, day 1, 00:00"


def test_describe_from_the_agent_point_of_view():
    said = describe(event(at(1, 8), "speak", "eve", target="adam", message="Hi"), "adam")
    judged = describe(event(at(1, 9), "assess", "adam", target="eve", kept=False), "adam")
    assert said == 'Week 1, day 1, 08:00: Eve said to you: "Hi"'
    assert judged == "Week 1, day 1, 09:00: you judged the promise of Eve (broken)"


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
    rest = {"metadata": {"name": "eve-q1"}, "spec": {"body": "I will rest on the seventh day."}}
    share = {"metadata": {"name": "eve-q2"}, "spec": {"body": "I will share my bread."}}
    text = prompt(ADAM, [{**EVE, "promises": [rest, share]}], None, [], 40)[1]["content"]
    assert ('- Eve, woman, married to you\n  promised [eve-q1]: "I will rest on the seventh day."\n'
            '  promised [eve-q2]: "I will share my bread."') in text


def test_parse_strips_the_brackets_around_a_promise_id():
    answer = {"action": "assess", "target": "Eve", "message": None, "refrains": None, "promise": "[eve-q1]",
              "kept": True}
    assert parse(answer) == ("assess", {"target": "eve", "promise": "eve-q1", "kept": True})


def test_parse_drops_fields_the_action_does_not_use():
    # Seen live on Groq: a promise with the promise-id field filled with text.
    answer = {"action": "promise", "target": None, "message": "I will work hard.", "refrains": None,
              "promise": "I will work hard.", "kept": False}
    assert parse(answer) == ("promise", {"message": "I will work hard."})


def test_the_observer_sees_what_was_seen_and_the_observed_only_that_it_happened():
    e = event(at(1, 10), "observe", "adam", target="eve", saw=[{"tick": at(1, 8), "kind": "work"},
                                                              {"tick": at(1, 9), "kind": "steal", "target": "adam"}])
    assert describe(e, "adam") == ("Week 1, day 1, 10:00: you watched Eve: week 1, day 1, 08:00 worked; "
                                   "week 1, day 1, 09:00 stole food from you")
    assert describe(e, "eve") == "Week 1, day 1, 10:00: Adam watched you"


class RateLimitedThenOk:
    """Stands in for the provider's API: refuses `refusals` times with a 429, then answers."""

    def __init__(self, refusals):
        self.refusals = refusals
        self.chat = self
        self.completions = self

    def create(self, **_):
        if self.refusals:
            self.refusals -= 1
            response = types.SimpleNamespace(request=None, status_code=429, headers={})
            raise openai.RateLimitError("429", response=response, body=None)
        return types.SimpleNamespace(usage=types.SimpleNamespace(total_tokens=10), choices=[
            types.SimpleNamespace(message=types.SimpleNamespace(content='{"answer": "ok"}'))])


@pytest.mark.parametrize("refusals, result", [(2, {"answer": "ok"}), (3, QuotaExhausted)])
def test_a_per_minute_limit_is_waited_out(tmp_path, monkeypatch, refusals, result):
    api, waits = RateLimitedThenOk(refusals), []
    monkeypatch.setattr(openai, "OpenAI", lambda **_: api)
    monkeypatch.setattr(time, "sleep", waits.append)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    quota = DB(str(tmp_path / "q.db"))
    if result is QuotaExhausted:
        with pytest.raises(QuotaExhausted):
            call("gemini", [], quota)
    else:
        assert call("gemini", [], quota) == result
    assert waits == [60] * min(refusals, 2)


def test_parse_trims_the_message():
    # Seen live on Gemma and Gemini: messages ending with a space.
    answer = {"action": "pray", "target": None, "message": " Guide us. ", "refrains": None, "promise": None,
              "kept": None, "amount": None}
    assert parse(answer) == ("pray", {"message": "Guide us."})


def test_agents_see_who_talked_and_what_others_did_all_day():
    talked = event(at(1, 9), "speak", "eve", target="seth")
    text = prompt(ADAM, [EVE], None, [talked], at(1, 12), {"adam": {"work": 3}, "eve": {"work": 5, "rest": 1}}
                  )[1]["content"]
    assert "Week 1, day 1, 09:00: Eve talked to Seth" in text
    assert "Since yesterday: you worked 3 hours and rested 0; Eve worked 5 hours and rested 1." in text
