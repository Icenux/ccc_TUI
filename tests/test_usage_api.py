import json
import time
from datetime import datetime

from ccc import usage_api
from ccc.usage_api import (
    ModelUsage,
    ProUsage,
    WindowUsage,
    _parse_per_model,
    get_pro_usage,
    parse_window,
)


def test_parse_window_valid():
    window = parse_window({"utilization": 30.0, "resets_at": "2026-09-03T23:29:59.981152+00:00"})
    assert window.utilization_pct == 30.0
    assert window.resets_at.year == 2026


def test_parse_window_missing_fields_returns_none():
    assert parse_window(None) is None
    assert parse_window({}) is None
    assert parse_window({"utilization": 30.0}) is None  # no resets_at


def test_parse_per_model_from_limits_array():
    data = {
        "limits": [
            {"kind": "session", "group": "session", "percent": 30, "scope": None, "resets_at": "2026-09-03T23:29:59+00:00"},
            {
                "kind": "weekly_scoped",
                "group": "weekly",
                "percent": 62,
                "scope": {"model": {"display_name": "Opus"}},
                "resets_at": "2026-09-04T12:59:59+00:00",
            },
        ]
    }
    models = _parse_per_model(data)
    assert len(models) == 1
    assert models[0].model == "Opus"
    assert models[0].utilization_pct == 62


def test_parse_per_model_falls_back_to_named_fields_when_no_scoped_limits():
    data = {
        "limits": [{"kind": "weekly_all", "percent": 48, "scope": None, "resets_at": "2026-09-04T12:59:59+00:00"}],
        "seven_day_opus": {"utilization": 10.0, "resets_at": "2026-09-04T12:59:59+00:00"},
        "seven_day_sonnet": None,
    }
    models = _parse_per_model(data)
    assert len(models) == 1
    assert models[0].model == "Opus"
    assert models[0].utilization_pct == 10.0


def test_parse_per_model_empty_when_nothing_populated():
    data = {"limits": [{"kind": "weekly_all", "percent": 48, "scope": None, "resets_at": "x"}]}
    assert _parse_per_model(data) == []


def test_get_pro_usage_no_token_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(usage_api, "CACHE_PATH", tmp_path / "usage_cache.json")
    monkeypatch.setattr(usage_api, "_read_oauth_token", lambda: None)
    assert get_pro_usage() is None


def test_get_pro_usage_fetches_and_caches(monkeypatch, tmp_path):
    cache_path = tmp_path / "usage_cache.json"
    monkeypatch.setattr(usage_api, "CACHE_PATH", cache_path)
    monkeypatch.setattr(usage_api, "_read_oauth_token", lambda: "fake-token")

    calls = {"n": 0}

    def fake_fetch(token):
        calls["n"] += 1
        return ProUsage(
            five_hour=WindowUsage(30.0, datetime(2026, 9, 3, 23, 29)),
            seven_day=WindowUsage(47.0, datetime(2026, 9, 4, 12, 59)),
            per_model=[ModelUsage("Opus", 62.0, datetime(2026, 9, 4, 12, 59))],
        )

    monkeypatch.setattr(usage_api, "fetch_live_usage", fake_fetch)

    usage = get_pro_usage()
    assert usage.five_hour.utilization_pct == 30.0
    assert usage.per_model[0].model == "Opus"
    assert calls["n"] == 1
    assert cache_path.exists()

    # second call within TTL should hit the cache, not fetch again
    usage2 = get_pro_usage()
    assert usage2.five_hour.utilization_pct == 30.0
    assert usage2.per_model[0].model == "Opus"
    assert calls["n"] == 1


def test_get_pro_usage_expired_cache_refetches(monkeypatch, tmp_path):
    cache_path = tmp_path / "usage_cache.json"
    monkeypatch.setattr(usage_api, "CACHE_PATH", cache_path)
    monkeypatch.setattr(usage_api, "_read_oauth_token", lambda: "fake-token")
    cache_path.write_text(
        json.dumps(
            {
                "timestamp": time.time() - 999,
                "data": {
                    "five_hour": {"utilization": 1.0, "resets_at": "2026-09-03T23:29:59+00:00"},
                    "seven_day": None,
                    "per_model": [],
                },
            }
        )
    )

    calls = {"n": 0}

    def fake_fetch(token):
        calls["n"] += 1
        return ProUsage(five_hour=WindowUsage(99.0, datetime(2026, 9, 3, 23, 29)), seven_day=None, per_model=[])

    monkeypatch.setattr(usage_api, "fetch_live_usage", fake_fetch)

    usage = get_pro_usage()
    assert usage.five_hour.utilization_pct == 99.0
    assert calls["n"] == 1


def test_get_pro_usage_force_refresh_bypasses_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "usage_cache.json"
    monkeypatch.setattr(usage_api, "CACHE_PATH", cache_path)
    monkeypatch.setattr(usage_api, "_read_oauth_token", lambda: "fake-token")
    cache_path.write_text(
        json.dumps(
            {
                "timestamp": time.time(),
                "data": {
                    "five_hour": {"utilization": 1.0, "resets_at": "2026-09-03T23:29:59+00:00"},
                    "seven_day": None,
                    "per_model": [],
                },
            }
        )
    )

    def fake_fetch(token):
        return ProUsage(five_hour=WindowUsage(77.0, datetime(2026, 9, 3, 23, 29)), seven_day=None, per_model=[])

    monkeypatch.setattr(usage_api, "fetch_live_usage", fake_fetch)

    usage = get_pro_usage(force_refresh=True)
    assert usage.five_hour.utilization_pct == 77.0


def test_failed_live_query_falls_back_to_the_last_known_reset_times(tmp_path, monkeypatch):
    """#analytics: no live answer must not hide when the limits reset."""
    import json
    from datetime import datetime, timedelta, timezone

    from ccc import usage_api

    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cache = tmp_path / "usage.json"
    cache.write_text(json.dumps({"timestamp": 0, "data": {
        "five_hour": {"utilization": 50, "resets_at": soon},
        "seven_day": {"utilization": 20, "resets_at": past}, "per_model": []}}))
    monkeypatch.setattr(usage_api, "CACHE_PATH", cache)
    monkeypatch.setattr(usage_api, "_read_oauth_token", lambda: "t")
    monkeypatch.setattr(usage_api, "fetch_live_usage", lambda token: None)

    usage = usage_api.get_pro_usage()

    assert usage is not None and usage.five_hour is not None and usage.seven_day is None
