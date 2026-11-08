"""ccc's user guide: one Markdown file per topic, shown by ? in the TUI
(tui.help.HelpScreen) and readable on GitHub, where README.md in this
folder is the table of contents. Write it for someone who has just
installed ccc. The Czech guide is the same files in cs/."""
from __future__ import annotations

import re
from importlib.resources import files

from ccc.i18n import N_, _, language

# (slug, title) in reading order; the file is <slug>.md next to this one.
TOPICS: list[tuple[str, str]] = [
    ("getting-started", N_("Getting started")),
    ("dashboard", N_("Dashboard")),
    ("conversations", N_("Conversations")),
    ("new-conversation", N_("Starting a conversation")),
    ("tasks", N_("Task queue")),
    ("routines", N_("Routines")),
    ("agents", N_("Agents")),
    ("review", N_("Reviews")),
    ("goal", N_("Goal loop")),
    ("workflows", N_("Workflows")),
    ("archive", N_("Archive")),
    ("limits", N_("Usage limits & blocked queues")),
    ("approvals", N_("Approving tool calls")),
    ("remote-control", N_("Remote control (phone)")),
    ("android-api", N_("Android app & API")),
    ("live-sessions", N_("Live sessions")),
    ("analytics", N_("Analytics")),
    ("settings", N_("Settings & notifications")),
    ("git", N_("Git: auto-commit & diffs")),
    ("command-line", N_("Command line")),
    ("troubleshooting", N_("Troubleshooting & how it works")),
    ("keys", N_("Keyboard shortcuts")),
]

# The topic ? opens on each screen (SidebarScreen.VIEW).
VIEW_TOPICS = {
    "dashboard": "dashboard",
    "conversations": "conversations",
    "archive": "archive",
    "sessions": "live-sessions",
    "analytics": "analytics",
}

_IMAGE_LINE = re.compile(r"^\s*(!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>)\s*$")


def index_markdown() -> str:
    """README.md of this folder (cs/README.md in Czech): the table of
    contents on GitHub."""
    other = "[Česky](cs/README.md)" if language() == "en" else "[English](../README.md)"
    lines = [
        "# " + _("ccc help"),
        "",
        _("The user guide of ccc. Inside ccc press `?` — the same pages, opened on\n"
          "the screen you're on."),
        "",
        other,
        "",
    ]
    lines += [f"{n}. [{_(title)}]({slug}.md)" for n, (slug, title) in enumerate(TOPICS, start=1)]
    return "\n".join(lines) + "\n"


def title(slug: str) -> str:
    return _(dict(TOPICS)[slug])


def read(slug: str) -> str:
    """The topic in the current language (English where it isn't translated)."""
    here = files(__package__)
    if language() != "en":
        page = here.joinpath(language(), f"{slug}.md")
        if page.is_file():
            return page.read_text(encoding="utf-8")
    return here.joinpath(f"{slug}.md").read_text(encoding="utf-8")


def for_terminal(markdown: str) -> str:
    """The topic without its screenshots: they're for GitHub — in the
    terminal you're looking at the real thing."""
    return "\n".join(line for line in markdown.splitlines() if not _IMAGE_LINE.match(line)) + "\n"
