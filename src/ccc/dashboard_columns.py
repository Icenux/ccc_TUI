"""Which columns the Dashboard shows — one choice in Settings, whatever the
terminal's width. The conversation's name (Project) is always there."""
from __future__ import annotations

from ccc.i18n import N_

STATUS, GOAL, PENDING, TOKENS, GIT, PATH = "status", "goal", "pending", "tokens", "git", "path"
CONTEXT = "context"
# In table order (the design's, #250), with the heading each has. The path
# isn't a column of its own: it's shown under the conversation's name.
COLUMNS: tuple[tuple[str, str], ...] = (
    (PATH, N_("Path")),
    (GIT, N_("Branch")),
    (PENDING, N_("Queued")),
    (CONTEXT, N_("Ctx")),
    (STATUS, N_("Status")),
    (GOAL, N_("Goal")),
    (TOKENS, N_("Tokens")),
)
IDS = tuple(column for column, _title in COLUMNS)
KEY = "dashboard_columns"
DEFAULT = IDS


def shown(storage) -> list[str]:
    """The chosen column ids in table order; all until chosen
    (saved empty = none, so Project alone)."""
    raw = storage.get_setting(KEY)
    chosen = DEFAULT if raw is None else tuple(part for part in raw.split(",") if part)
    return [column for column in IDS if column in chosen]


def save(storage, columns: list[str]) -> None:
    storage.set_setting(KEY, ",".join(column for column in IDS if column in columns))
