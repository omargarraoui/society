from collections import Counter

import pytest

import config
from db import DB
from world import turn_tokens, world_days_per_day

SECONDS_PER_DAY = 86400


def pause(speed):
    return SECONDS_PER_DAY / (speed * config.TICKS_PER_DAY)


def test_two_groq_agents_are_limited_by_tokens():
    # Budget: min(1000 requests, 200000 / 790 = 253.2 turns) * 0.9 = 227.8 turns per real day.
    # Turns per world day: 2 agents * 64 day ticks + 32 night slots = 160.
    speed = world_days_per_day(Counter(groq=2), {"groq": 790})
    assert speed == pytest.approx(227.85 / 160, rel=1e-3)
    assert pause(speed) == pytest.approx(632, abs=1)


def test_the_slowest_provider_sets_the_pace():
    # Each model: 4*64 + 32*4/12 = 266.7 turns per world day.
    # groq budget 227.8 -> 0.854; gemini 500 * 0.9 = 450 -> 1.69; cloudflare 200 * 0.9 = 180 -> 0.675.
    speed = world_days_per_day(Counter(groq=4, gemini=4, cloudflare=4), {"groq": 790, "gemini": None,
                                                                        "cloudflare": None})
    assert speed == pytest.approx(180 / 266.67, rel=1e-3)


def test_speed_never_exceeds_the_cap(monkeypatch):
    monkeypatch.setitem(config.PROVIDERS["gemini"], "daily_requests", 1_000_000)
    assert world_days_per_day(Counter(gemini=1), {"gemini": None}) == config.MAX_SPEED


def test_turn_tokens_uses_todays_average_once_measured(tmp_path):
    quota = DB(str(tmp_path / "q.db"))
    assert turn_tokens(quota, "groq") == config.PROVIDERS["groq"]["turn_tokens"]
    quota.add_usage("groq", 900)
    quota.add_usage("groq", 1100)
    assert turn_tokens(quota, "groq") == 1000
