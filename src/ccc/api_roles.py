"""What a device may do (#160). Every device has a role (models.ROLES): a
stolen phone or token should not be a way into the whole computer, so only
what a device needs is open to it. Weakest first:

read     only looks: every GET (lists, conversations, the events stream)
operate  also steers the conversations that exist: messages, approvals, queue,
         tasks, reviews, goals, workflows
admin    also what reaches beyond them: new conversations (any folder), keys
         typed into a terminal, prompts to live sessions, routines,
         settings, backup, updating ccc and building the app, other devices

The rules are in one table, checked before any handler runs; a path the table
doesn't know needs admin (closed by default)."""
from __future__ import annotations

import re

from ccc.models import ROLE_ADMIN, ROLE_OPERATE, ROLE_READ, ROLES

# (method or "*", path after /api/v1, role the request needs); the first match counts.
RULES: tuple[tuple[str, str, str], ...] = (
    ("*", r"/(app/build|update|update/check|restart|backup)", ROLE_ADMIN),
    ("*", r"/settings(/.*)?", ROLE_ADMIN),
    ("GET", r"/audit", ROLE_ADMIN),
    ("POST", r"/conversations", ROLE_ADMIN),
    ("*", r"/conversations/\d+/terminal", ROLE_ADMIN),
    ("POST", r"/live-sessions/[^/]+/prompt", ROLE_ADMIN),
    ("*", r"/routines(/.*)?", ROLE_ADMIN),
    ("POST", r"/(prompt-presets|launch-presets(/delete)?)", ROLE_ADMIN),  # global configuration, not steering
    ("*", r"/devices/\d+", ROLE_ADMIN),  # a device's own rename and sign-out are let through in required_role()
    ("GET", r"/.*", ROLE_READ),
    ("*", r"/(conversations/\d+/(messages|retry|seen|archive|group|tasks|reviews|reviews/stop|goals|goals/stop|"
          r"workflows|workflows/answer|workflows/stop)|tasks/\d+(/move)?|reviews/\d+/apply|"
          r"groups(/rename|/delete)?|permissions/\d+|analytics/archive)", ROLE_OPERATE),
)
_COMPILED = tuple((method, re.compile(pattern), role) for method, pattern, role in RULES)
_OWN_DEVICE = re.compile(r"/devices/(\d+)")


def required_role(method: str, rest: str, device_id: int | None = None) -> str:
    """The weakest role that may make this request (`rest` is the path after /api/v1).
    A device may rename and sign out itself whatever its role."""
    rest = rest.rstrip("/") or "/"
    own = _OWN_DEVICE.fullmatch(rest)
    if own and device_id is not None and int(own.group(1)) == device_id and method in ("POST", "DELETE"):
        return ROLE_READ
    for rule_method, pattern, role in _COMPILED:
        if rule_method in ("*", method) and pattern.fullmatch(rest):
            return role
    return ROLE_ADMIN


def allows(role: str, needed: str) -> bool:
    """Does a device with `role` reach what needs `needed`? An unknown role counts as the weakest."""
    return ROLES.index(needed) <= (ROLES.index(role) if role in ROLES else 0)


# Answer fields that hand over control of a conversation (a link that drives its Claude Code from elsewhere):
# a device that only looks does not get them.
CONTROL_KEYS = frozenset({"remote_control_url"})


def strip_for(role: str, result):
    """`result` without the CONTROL_KEYS unless the role is operate or better."""
    if allows(role, ROLE_OPERATE):
        return result
    if isinstance(result, dict):
        return {k: strip_for(role, v) for k, v in result.items() if k not in CONTROL_KEYS}
    if isinstance(result, list):
        return [strip_for(role, v) for v in result]
    return result
