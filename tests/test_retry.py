from datetime import datetime

from ccc.retry import (
    MAX_BACKOFF_SECONDS,
    MIN_BACKOFF_SECONDS,
    next_backoff_seconds,
    parse_reset_time,
)


def test_parse_reset_time_24h_future_today():
    now = datetime(2026, 9, 3, 10, 0, 0)
    result = parse_reset_time("Claude usage limit reached. Resets at 15:30.", now=now)
    assert result == datetime(2026, 9, 3, 15, 30, 0)


def test_parse_reset_time_rolls_to_tomorrow_when_past():
    now = datetime(2026, 9, 3, 16, 0, 0)
    result = parse_reset_time("resets at 15:30", now=now)
    assert result == datetime(2026, 9, 4, 15, 30, 0)


def test_parse_reset_time_12h_pm():
    now = datetime(2026, 9, 3, 10, 0, 0)
    result = parse_reset_time("Your limit resets at 3:30pm today.", now=now)
    assert result == datetime(2026, 9, 3, 15, 30, 0)


def test_parse_reset_time_no_match_returns_none():
    assert parse_reset_time("No conversation found with session ID: xyz") is None


def test_parse_reset_time_real_message_no_at_no_space_before_meridiem():
    # Real production wording, 2026-09-03: no "at", no space before "pm".
    now = datetime(2026, 9, 3, 15, 49, 0)
    result = parse_reset_time("You've hit your session limit · resets 8:30pm (Europe/Prague)", now=now)
    assert result == datetime(2026, 9, 3, 20, 30, 0)


def test_backoff_progression():
    assert next_backoff_seconds(None) == MIN_BACKOFF_SECONDS
    step2 = next_backoff_seconds(MIN_BACKOFF_SECONDS)
    assert step2 == MIN_BACKOFF_SECONDS * 2
    step3 = next_backoff_seconds(step2)
    assert step3 == MAX_BACKOFF_SECONDS
    assert next_backoff_seconds(MAX_BACKOFF_SECONDS) == MAX_BACKOFF_SECONDS


def test_parse_reset_time_hour_only_claude_code_message():
    now = datetime(2026, 11, 11, 19, 30)
    assert parse_reset_time("You've hit your 5-hour limit · resets 8pm (Europe/Prague)", now=now) == datetime(2026, 11, 11, 20, 0)
