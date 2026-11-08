from __future__ import annotations

import asyncio
import os
import json
from dataclasses import dataclass, field

from ccc import mcp_servers, plugins, profiles
from ccc.i18n import _
from ccc.models import RunStatus

RATE_LIMIT_PATTERNS = (
    "usage limit",
    "session limit",
    "limit reached",
    "resets ",
    "rate limit",
    "quota",
)

# HTTP 429 (Too Many Requests) is Claude Code's own signal for a rate/usage
# limit — far more reliable than matching the human-readable message text,
# which has changed wording before ("usage limit reached" vs "session
# limit ... resets 8:30pm" observed in production on 2026-09-03).
RATE_LIMIT_API_ERROR_STATUS = 429


@dataclass
class RunResult:
    status: RunStatus
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    duration_ms: int = 0
    num_turns: int = 0
    result_text: str | None = None
    error_text: str | None = None
    session_id: str | None = None
    # {canonical_model_name: {input_tokens, output_tokens, cache_read_tokens,
    # cache_creation_tokens}}, from Claude Code's "modelUsage" field — lets a
    # run be attributed to the specific model(s) it actually used.
    model_usage: dict[str, dict[str, int]] = field(default_factory=dict)
    # The run failed because the account must sign in again (not a limit, not a bug).
    login_expired: bool = False


# Claude Code's wording when the saved login no longer works ("Login expired
# · Please run /login", errors page of its docs) or there is none.
LOGIN_EXPIRED_PATTERNS = (
    "login expired",
    "please run /login",
    "not logged in",
    "invalid authentication credentials",
    "oauth token has expired",
)
AUTH_API_ERROR_STATUS = 401


def is_login_expired(text: str | None, data: dict | None = None) -> bool:
    if data is not None and data.get("api_error_status") == AUTH_API_ERROR_STATUS:
        return True
    lowered = (text or "").lower()
    return any(p in lowered for p in LOGIN_EXPIRED_PATTERNS)


def login_expired_message(profile: str = "") -> str:
    """What to tell the user, with the exact way to sign in again."""
    if profile:
        return _("The login of account '{profile}' expired. Sign in again: ccc profile login {profile}").format(profile=profile)
    return _("The Claude login expired. Sign in again: run claude and type /login")


def _is_rate_limited(data: dict | None, combined_lower: str) -> bool:
    if data is not None and data.get("api_error_status") == RATE_LIMIT_API_ERROR_STATUS:
        return True
    return any(p in combined_lower for p in RATE_LIMIT_PATTERNS)


def classify_output(stdout: str, stderr: str, exit_code: int) -> RunResult:
    combined = f"{stdout}\n{stderr}".lower()

    # Claude Code prints a JSON result envelope on stdout even when it's
    # reporting an error (rate limit, API error, ...) with a non-zero exit
    # code — parse it whenever stdout looks like JSON, regardless of
    # exit_code, so fields like api_error_status/result/session_id are
    # available for classification instead of falling back to raw text.
    data: dict | None = None
    if stdout.strip():
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = None

    if data is not None:
        if data.get("is_error"):
            if not _is_rate_limited(data, combined) and is_login_expired(f"{data.get('result') or ''}\n{stderr}", data):
                return RunResult(status=RunStatus.ERROR, error_text=login_expired_message(), session_id=data.get("session_id"),
                                 login_expired=True)
            status = RunStatus.RATE_LIMITED if _is_rate_limited(data, combined) else RunStatus.ERROR
            return RunResult(
                status=status,
                error_text=data.get("result") or stderr,
                session_id=data.get("session_id"),
            )

        usage = data.get("usage", {})
        model_usage = {
            model_name: {
                "input_tokens": mu.get("inputTokens", 0),
                "output_tokens": mu.get("outputTokens", 0),
                "cache_read_tokens": mu.get("cacheReadInputTokens", 0),
                "cache_creation_tokens": mu.get("cacheCreationInputTokens", 0),
            }
            for model_name, mu in (data.get("modelUsage") or {}).items()
            if isinstance(mu, dict)
        }
        return RunResult(
            status=RunStatus.SUCCESS,
            cost_usd=data.get("total_cost_usd", 0.0),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
            duration_ms=data.get("duration_ms", 0),
            num_turns=data.get("num_turns", 0),
            result_text=data.get("result"),
            session_id=data.get("session_id"),
            model_usage=model_usage,
        )

    if _is_rate_limited(None, combined):
        return RunResult(status=RunStatus.RATE_LIMITED, error_text=stderr.strip() or stdout.strip())

    if is_login_expired(combined):
        return RunResult(status=RunStatus.ERROR, error_text=login_expired_message(), login_expired=True)

    if exit_code == 0 and stdout.strip():
        return RunResult(status=RunStatus.ERROR, error_text=f"unparseable stdout: {stdout[:500]}")

    return RunResult(
        status=RunStatus.ERROR,
        error_text=stderr.strip() or stdout.strip() or f"exit code {exit_code}",
    )


# MCP call timeout for the permission prompt tool: a week, i.e. "until
# the user answers" in practice.
PERMISSION_TOOL_TIMEOUT_MS = 7 * 24 * 3600 * 1000

# Flags used for queues created before launch attributes existed (NULL
# launch_flags) — exactly what every run used before.
LEGACY_FLAGS = ["--dangerously-skip-permissions"]

# First-run flags that themselves say which session to talk to; the
# queue's own (placeholder) session id must not be passed alongside them.
_SESSION_SELECTING_FLAGS = ("--resume", "--from-pr")


def build_command(
    session_id: str,
    prompt: str,
    resume: bool,
    launch_flags: list[str] | None = None,
    first_run_flags: list[str] | None = None,
    project_path: str | None = None,
    profile: str = "",
) -> list[str]:
    first = list(first_run_flags or [])
    if any(flag in first for flag in _SESSION_SELECTING_FLAGS):
        session_flag: list[str] = []
    else:
        session_flag = ["--resume", session_id] if resume else ["--session-id", session_id]
    flags = LEGACY_FLAGS if launch_flags is None else list(launch_flags)
    if project_path is not None:
        flags = plugins.resolve_flags(mcp_servers.resolve_flags(flags, project_path, profile), profile)
    return ["claude", *session_flag, *first, "-p", prompt, "--output-format", "json", *flags]


async def _execute_command(agent, project_path, session_id, prompt, resume, env) -> RunResult:
    """One headless run of a command profile (ccc.backends)."""
    from ccc import backends

    try:
        proc = await asyncio.create_subprocess_exec(
            *backends.run_command(agent, session_id, prompt, resume, project_path),
            cwd=project_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
    except OSError as exc:
        return RunResult(status=RunStatus.ERROR, error_text=str(exc), session_id=session_id)
    stdout_bytes, stderr_bytes = await proc.communicate()
    return backends.classify(
        stdout_bytes.decode("utf-8", errors="replace"), stderr_bytes.decode("utf-8", errors="replace"),
        proc.returncode or 0, session_id,
    )


async def execute(
    project_path: str,
    session_id: str,
    prompt: str,
    resume: bool,
    launch_flags: list[str] | None = None,
    first_run_flags: list[str] | None = None,
    profile: str = "",
) -> RunResult:
    env = profiles.env_for(profile)
    agent = profiles.get(profile)
    if agent is not None and agent.is_command:
        return await _execute_command(agent, project_path, session_id, prompt, resume, env)
    if launch_flags and "--permission-prompt-tool" in launch_flags:
        # The permission tool waits for the user without a time limit;
        # don't let Claude Code give up on the MCP call first.
        env = {**(env if env is not None else os.environ), "MCP_TOOL_TIMEOUT": str(PERMISSION_TOOL_TIMEOUT_MS)}
    proc = await asyncio.create_subprocess_exec(
        *build_command(session_id, prompt, resume, launch_flags, first_run_flags, project_path, profile),
        cwd=project_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    result = classify_output(
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )
    if result.session_id is None:
        result.session_id = session_id
    if result.login_expired and profile:
        result.error_text = login_expired_message(profile)
    return result
