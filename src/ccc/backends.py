"""Other agents than Claude Code (#226): a command profile runs any program
that takes a prompt and answers on stdout — an agent for an OpenAI-style API
(opencode, aider, goose, …), a script, a local model — so ccc works without
Claude Code and without an Anthropic account.

What ccc knows about such an agent is what the profile says: the command
lines (ccc.profiles), the environment (URL, key, model) and this contract:
exit code 0 = done, stdout = the answer; anything else is an error, and
the usual wording of a rate limit (runner.RATE_LIMIT_PATTERNS) makes the
conversation wait and retry like it does for Claude Code. Cost, tokens and
transcripts are not known (shown as zero), the agent keeps its own history.
"""
from __future__ import annotations

import shlex

from ccc.models import RunStatus
from ccc.profiles import Profile, fill


def _values(profile: Profile, session_id: str, project: str, prompt: str = "") -> dict[str, str]:
    return {
        "{prompt}": prompt, "{model}": profile.model, "{url}": profile.url,
        "{session_id}": session_id, "{project}": project,
    }


def _argv(template: str, values: dict[str, str]) -> list[str]:
    # Split first, fill after: a prompt with spaces or quotes stays one argument.
    return [fill(word, values) for word in shlex.split(template)]


def run_command(profile: Profile, session_id: str, prompt: str, resume: bool, project: str) -> list[str]:
    template = (profile.resume if resume and profile.resume else profile.run)
    return _argv(template, _values(profile, session_id, project, prompt))


def interactive_command(profile: Profile, session_id: str, project: str) -> list[str]:
    """The command of the interactive session; the headless one when the
    profile has no interactive command (an agent that is only a script)."""
    template = profile.interactive or profile.run
    return _argv(template, _values(profile, session_id, project))


def classify(stdout: str, stderr: str, exit_code: int, session_id: str):
    from ccc.runner import RATE_LIMIT_PATTERNS, RunResult

    answer = stdout.strip()
    if exit_code == 0:
        return RunResult(status=RunStatus.SUCCESS, result_text=answer, session_id=session_id)
    combined = f"{stdout}\n{stderr}".lower()
    status = RunStatus.RATE_LIMITED if any(p in combined for p in RATE_LIMIT_PATTERNS) else RunStatus.ERROR
    return RunResult(
        status=status, error_text=stderr.strip() or answer or f"exit code {exit_code}", session_id=session_id,
    )
