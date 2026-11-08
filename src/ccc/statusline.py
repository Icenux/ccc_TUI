"""`ccc statusline`: Claude Code's status line command that tells ccc how
full a conversation's context window is.

Claude Code runs the command from `statusLine` in its settings and gives it
a JSON on stdin — `session_id` and `context_window` (`used_percentage`,
`total_input_tokens`, `context_window_size`). ccc keeps the numbers with
the conversation of that session (`GET /conversations` → `context`).

Set it up in ~/.claude/settings.json:

    "statusLine": {"type": "command", "command": "ccc statusline"}

and to keep your own status line, hand it over — it gets the same input
and its output is what Claude Code shows:

    "statusLine": {"type": "command", "command": "ccc statusline --then '~/.claude/my-line.sh'"}

Must never break the status line: every failure is swallowed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ccc.storage import Storage

THEN_TIMEOUT_SECONDS = 5


def parse(raw: str) -> tuple[str, float, int | None, int | None] | None:
    """(session id, percent, tokens, window) of a status line input, None when it carries no context fill."""
    try:
        data = json.loads(raw)
        session_id = data["session_id"]
        context = data["context_window"]
        percent = context["used_percentage"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(session_id, str) or isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return None
    return session_id, float(percent), _count(context.get("total_input_tokens")), _count(context.get("context_window_size"))


def _count(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def run(db_path: str, raw: str, then: str | None = None) -> int:
    parsed = parse(raw)
    if parsed is not None and Path(db_path).exists():
        try:
            Storage(db_path).set_context(*parsed)
        except Exception:  # noqa: BLE001 - the status line must stay quiet
            pass
    if then:
        try:
            done = subprocess.run(then, shell=True, input=raw, capture_output=True, text=True,
                                  timeout=THEN_TIMEOUT_SECONDS)
            sys.stdout.write(done.stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    elif parsed is not None:
        sys.stdout.write(f"ctx {parsed[1]:.0f} %\n")
    return 0
