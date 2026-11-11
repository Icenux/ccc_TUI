from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
CACHE_PATH = Path.home() / ".local" / "share" / "ccc" / "usage_cache.json"
CACHE_TTL_SECONDS = 60
USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"


@dataclass
class WindowUsage:
    utilization_pct: float
    resets_at: datetime


@dataclass
class ModelUsage:
    model: str
    utilization_pct: float
    resets_at: datetime


@dataclass
class ProUsage:
    five_hour: WindowUsage | None
    seven_day: WindowUsage | None
    per_model: list[ModelUsage]


# Anthropic's plan or account may additionally expose a weekly limit scoped
# to one specific model (seen as top-level "seven_day_<model>" keys, and/or
# "weekly_scoped" entries in the "limits" array). Only some plans populate
# this — on a plain Pro plan these are typically all null, and only the
# combined five_hour/seven_day windows are meaningful.
KNOWN_MODEL_KEYS = ("opus", "sonnet", "haiku", "fable")


def _read_oauth_token() -> str | None:
    try:
        with open(CREDENTIALS_PATH) as f:
            creds = json.load(f)
        return creds.get("claudeAiOauth", {}).get("accessToken")
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def parse_window(data: dict | None) -> WindowUsage | None:
    if not data:
        return None
    pct = data.get("utilization")
    resets_at = data.get("resets_at")
    if pct is None or not resets_at:
        return None
    try:
        dt = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return WindowUsage(utilization_pct=pct, resets_at=dt)


def _parse_model_usage_from_named_fields(data: dict) -> list[ModelUsage]:
    found = []
    for model in KNOWN_MODEL_KEYS:
        window = parse_window(data.get(f"seven_day_{model}"))
        if window is not None:
            found.append(ModelUsage(model=model.capitalize(), utilization_pct=window.utilization_pct, resets_at=window.resets_at))
    return found


def _parse_model_usage_from_limits(data: dict) -> list[ModelUsage]:
    found = []
    for entry in data.get("limits") or []:
        if not isinstance(entry, dict) or entry.get("kind") != "weekly_scoped":
            continue
        scope = entry.get("scope") or {}
        model_name = (scope.get("model") or {}).get("display_name")
        percent = entry.get("percent")
        resets_at = entry.get("resets_at")
        if not model_name or percent is None or not resets_at:
            continue
        window = parse_window({"utilization": percent, "resets_at": resets_at})
        if window is not None:
            found.append(ModelUsage(model=model_name, utilization_pct=window.utilization_pct, resets_at=window.resets_at))
    return found


def _parse_per_model(data: dict) -> list[ModelUsage]:
    by_limits = _parse_model_usage_from_limits(data)
    if by_limits:
        return by_limits
    return _parse_model_usage_from_named_fields(data)


def fetch_live_usage(token: str) -> ProUsage | None:
    """Query the same OAuth usage endpoint Claude Code's own status line
    uses. Token is passed to curl via stdin (--config -), never as a CLI
    arg, so it never appears in `ps` output. Returns None on any failure
    (offline, non-OAuth auth, API error) — never raises."""
    try:
        result = subprocess.run(
            [
                "curl",
                "-s",
                "-f",
                "--config",
                "-",
                "-H",
                "Accept: application/json",
                "-H",
                "Content-Type: application/json",
                "-H",
                "anthropic-beta: oauth-2025-04-20",
                USAGE_ENDPOINT,
            ],
            input=f'header = "Authorization: Bearer {token}"\n',
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None

    return ProUsage(
        five_hour=parse_window(data.get("five_hour")),
        seven_day=parse_window(data.get("seven_day")),
        per_model=_parse_per_model(data),
    )


def _window_to_cache(window: WindowUsage | None) -> dict | None:
    if window is None:
        return None
    return {"utilization": window.utilization_pct, "resets_at": window.resets_at.isoformat()}


def _usage_to_cache(usage: ProUsage) -> dict:
    return {
        "five_hour": _window_to_cache(usage.five_hour),
        "seven_day": _window_to_cache(usage.seven_day),
        "per_model": [
            {"model": m.model, "utilization": m.utilization_pct, "resets_at": m.resets_at.isoformat()}
            for m in usage.per_model
        ],
    }


def _cache_to_usage(data: dict) -> ProUsage:
    return ProUsage(
        five_hour=parse_window(data.get("five_hour")),
        seven_day=parse_window(data.get("seven_day")),
        per_model=[
            ModelUsage(
                model=m["model"],
                utilization_pct=m["utilization"],
                resets_at=datetime.fromisoformat(m["resets_at"]),
            )
            for m in data.get("per_model", [])
        ],
    )


def _stale_cached_usage() -> ProUsage | None:
    """The last known usage when the live query fails (offline, rate-limited,
    expired login): the reset times in it stay true until they pass, so it's
    better than showing nothing. Windows that already reset are dropped."""
    try:
        with open(CACHE_PATH) as f:
            usage = _cache_to_usage(json.load(f)["data"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None
    now = datetime.now(timezone.utc)
    alive = lambda w: w if w is not None and w.resets_at > now else None  # noqa: E731
    five, seven = alive(usage.five_hour), alive(usage.seven_day)
    per_model = [m for m in usage.per_model if m.resets_at > now]
    if five is None and seven is None and not per_model:
        return None
    return ProUsage(five_hour=five, seven_day=seven, per_model=per_model)


def get_pro_usage(force_refresh: bool = False) -> ProUsage | None:
    """Current Claude Pro/Max usage-limit status (5-hour and 7-day
    windows). Cached on disk for CACHE_TTL_SECONDS to avoid hammering the
    API every time the analytics screen renders. Returns None when
    unavailable (API-key auth instead of OAuth login, offline, etc.) —
    callers should treat that as "unknown", not an error."""
    if not force_refresh:
        try:
            with open(CACHE_PATH) as f:
                cache = json.load(f)
            if time.time() - cache.get("timestamp", 0) < CACHE_TTL_SECONDS:
                return _cache_to_usage(cache["data"])
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    token = _read_oauth_token()
    usage = fetch_live_usage(token) if token else None
    if usage is None:
        return _stale_cached_usage()

    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump({"timestamp": time.time(), "data": _usage_to_cache(usage)}, f)
    except OSError:
        pass

    return usage
