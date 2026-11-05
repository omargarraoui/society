import datetime
from collections import Counter

import pytest

import config
from db import DB, seconds_to_reset
from world import acting_models, turn_tokens, turns_left, world_days_per_day

SECONDS_PER_DAY = 86400


def pause(speed):
    return SECONDS_PER_DAY / (speed * config.TICKS_PER_DAY)


DAY = 24 * 60 * 60
GROQ_TURNS = 0.9 * 200_000 / 790  # tokens bind before requests: 227.8 turns a day


def test_two_groq_agents_are_limited_by_tokens():
    # A fresh day: 227.8 turns left, 24 hours to spend them. Turns per world day: 2 * 16 + 8 = 40.
    speed = world_days_per_day(Counter(groq=2), {"groq": GROQ_TURNS}, {"groq": DAY})
    assert speed == pytest.approx(GROQ_TURNS / 40, rel=1e-3)
    # The same real wait per turn as with 15-minute turns: a turn costs the same, it just covers an hour.
    assert pause(speed) == pytest.approx(632, abs=1)


def test_half_the_quota_left_for_half_the_day_keeps_the_speed():
    assert world_days_per_day(Counter(groq=2), {"groq": GROQ_TURNS / 2}, {"groq": DAY / 2}) == pytest.approx(
        world_days_per_day(Counter(groq=2), {"groq": GROQ_TURNS}, {"groq": DAY}))


def test_after_a_burst_the_rest_of_the_day_slows_down():
    # 3/4 of the quota spent in the first half of the day: half the speed for the second half.
    full = world_days_per_day(Counter(groq=2), {"groq": GROQ_TURNS}, {"groq": DAY})
    after = world_days_per_day(Counter(groq=2), {"groq": GROQ_TURNS / 4}, {"groq": DAY / 2})
    assert after == pytest.approx(full / 2)


def test_spent_quota_stops_the_world_until_the_reset():
    assert world_days_per_day(Counter(groq=2), {"groq": 0.0}, {"groq": DAY / 3}) == 0


def test_the_slowest_provider_sets_the_pace():
    # Each model: 4*16 + 8*4/12 = 66.67 turns per world day.
    # groq 227.8 -> 3.42; gemini 500 * 0.9 = 450 -> 6.75; cloudflare 200 * 0.9 = 180 -> 2.7.
    left = {"groq": GROQ_TURNS, "gemini": 450, "cloudflare": 180}
    speed = world_days_per_day(Counter(groq=4, gemini=4, cloudflare=4), left, dict.fromkeys(left, DAY))
    assert speed == pytest.approx(180 / 66.67, rel=1e-3)


def test_speed_never_exceeds_the_cap():
    assert world_days_per_day(Counter(gemini=1), {"gemini": 1_000_000}, {"gemini": DAY}) == config.MAX_SPEED


def test_turns_left_count_what_was_used_today(tmp_path):
    quota = DB(str(tmp_path / "q.db"))
    assert turns_left(quota, "gemini") == pytest.approx(450)
    for _ in range(50):
        quota.add_usage("gemini", 500)
    assert turns_left(quota, "gemini") == pytest.approx(400)
    for _ in range(10):
        quota.add_usage("groq", 18_000)  # 180K tokens = 90% of the free 200K
    assert turns_left(quota, "groq") == 0


def test_quotas_reset_at_midnight_in_the_providers_time_zone():
    now = datetime.datetime(2026, 11, 5, 23, 0, tzinfo=datetime.UTC)
    assert seconds_to_reset("groq", now) == 3600  # UTC
    assert seconds_to_reset("gemini", now) == (24 - 15) * 3600  # 15:00 in Los Angeles


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
