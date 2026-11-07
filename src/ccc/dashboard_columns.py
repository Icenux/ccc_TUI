"""Which columns the Dashboard shows — one choice in Settings, whatever the
terminal's width. The conversation's name (Project) is always there."""
from __future__ import annotations

from ccc.i18n import N_

STATUS, GOAL, PENDING, TOKENS, GIT, PATH = "status", "goal", "pending", "tokens", "git", "path"
# In table order, with the heading each has.
COLUMNS: tuple[tuple[str, str], ...] = (
    (STATUS, N_("Status")),
    (GOAL, N_("Goal")),
    (PENDING, N_("Pending")),
    (TOKENS, N_("Today's tokens")),
    (GIT, N_("Git")),
    (PATH, N_("Path")),
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
