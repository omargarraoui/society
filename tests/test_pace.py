from collections import Counter

import pytest

import config
from db import DB
from world import acting_models, turn_tokens, world_days_per_day

SECONDS_PER_DAY = 86400


def pause(speed):
    return SECONDS_PER_DAY / (speed * config.TICKS_PER_DAY)


def test_two_groq_agents_are_limited_by_tokens():
    # Budget: min(1000 requests, 200000 / 790 = 253.2 turns) * 0.9 = 227.8 turns per real day.
    # Turns per world day: 2 agents * 16 day ticks + 8 night slots = 40.
    speed = world_days_per_day(Counter(groq=2), {"groq": 790})
    assert speed == pytest.approx(227.85 / 40, rel=1e-3)
    # The same real wait per turn as with 15-minute turns: a turn costs the same, it just covers an hour.
    assert pause(speed) == pytest.approx(632, abs=1)


def test_the_slowest_provider_sets_the_pace():
    # Each model: 4*16 + 8*4/12 = 66.67 turns per world day.
    # groq budget 227.8 -> 3.42; gemini 500 * 0.9 = 450 -> 6.75; cloudflare 200 * 0.9 = 180 -> 2.7.
    speed = world_days_per_day(Counter(groq=4, gemini=4, cloudflare=4), {"groq": 790, "gemini": None,
                                                                        "cloudflare": None})
    assert speed == pytest.approx(180 / 66.67, rel=1e-3)


def test_speed_never_exceeds_the_cap(monkeypatch):
    monkeypatch.setitem(config.PROVIDERS["gemini"], "daily_requests", 1_000_000)
    assert world_days_per_day(Counter(gemini=1), {"gemini": None}) == config.MAX_SPEED


def test_turn_tokens_uses_todays_average_once_measured(tmp_path):
    quota = DB(str(tmp_path / "q.db"))
    assert turn_tokens(quota, "groq") == config.PROVIDERS["groq"]["turn_tokens"]
    quota.add_usage("groq", 900)
    quota.add_usage("groq", 1100)
    assert turn_tokens(quota, "groq") == 1000


def test_only_living_adults_take_turns():
    agents = {
        "adam": {"model": "groq", "alive": True, "born": None},
        "abel": {"model": "groq", "alive": False, "born": None},
        "seth": {"model": "gemini", "alive": True, "born": 100},
    }
    assert acting_models(agents, tick=101) == {"groq": 1}
    assert acting_models(agents, tick=100 + 7 * config.TICKS_PER_DAY) == {"groq": 1, "gemini": 1}
