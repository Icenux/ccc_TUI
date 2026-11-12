"""Shared look for every ccc screen (#250).

The frame: on the left the menu — the daemon's state, then the coloured
3D buttons of the views (and of Agents, Routines and Tasks, which open a
window), each with its key and a count; on the right the screen's content
in thin-lined panels; at the bottom one status bar with the screen's keys. Every top-level screen subclasses SidebarScreen so they all
share that frame, and adds its own sidebar section / main content /
buttons through the hooks below.
"""
from __future__ import annotations

import time

import sqlite3
from dataclasses import dataclass

from rich.cells import cell_len
from rich.markup import escape
from rich.text import Text
from textual.css.query import NoMatches
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Static

from ccc import keymap
from ccc.i18n import _
from ccc.models import Queue, QueueStatus
from ccc.tui import theme

# The number of live Claude Code sessions, for the menu: one count for all
# screens ("count", None until known; "at": when it was last asked for).
LIVE_SESSIONS_SECONDS = 30.0
_LIVE_SESSIONS: dict[str, float | int | None] = {}


def set_live_sessions(count: int) -> None:
    """The Live sessions view found this many (or the menu's own look)."""
    _LIVE_SESSIONS["count"] = count


@dataclass(frozen=True)
class SidebarButton:
    id: str
    # Fixed, hardcoded labels only, with {key} for the action's current
    # key — Button labels are parsed as Rich markup, so use "({key})"
    # rather than "[{key}]" (brackets get silently eaten).
    label: str
    color: str  # "green" | "teal" | "red" | "strip" — see BUTTON_CSS
    action: str  # action name on the screen, without the "action_" prefix
    key_id: str  # keymap action id whose key the label shows

    def text(self, keys: dict[str, str]) -> str:
        key = keymap.pretty(keys[self.key_id])
        if self.color == "strip":  # like the key list at the bottom: the key in its colour
            key = f"[b $footer-key-foreground]{escape(key)}[/]"
        return self.label.format(key=key)


# (view key, label) in sidebar order; each one's hotkey is keymap's
# views.<view>.
VIEWS: list[tuple[str, str]] = [
    ("dashboard", _("Dashboard")),
    ("conversations", _("Conversations")),
    ("archive", _("Archive")),
    ("sessions", _("Live sessions")),
    ("analytics", _("Analytics")),
]


@dataclass(frozen=True)
class NavItem:
    """One button of the menu: a view (VIEWS), or a window it opens."""

    key: str  # nav-<key>: its id and colour class
    label: str
    action: str  # action name on the screen, without the "action_" prefix
    key_id: str  # keymap action id whose key the button shows
    view: bool = False  # a view: highlighted while it's the one shown


# The menu, top to bottom. Agents (managing Claude Code's helpers) sits
# under Live sessions (#250).
NAV: list[NavItem] = [
    NavItem("dashboard", _("Dashboard"), "goto('dashboard')", "views.dashboard", view=True),
    NavItem("conversations", _("Conversations"), "goto('conversations')", "views.conversations", view=True),
    NavItem("archive", _("Archive"), "goto('archive')", "views.archive", view=True),
    NavItem("sessions", _("Live sessions"), "goto('sessions')", "views.sessions", view=True),
    NavItem("agents", _("Agents"), "agents", "views.agents"),
    NavItem("analytics", _("Analytics"), "goto('analytics')", "views.analytics", view=True),
    NavItem("routines", _("Routines"), "routines", "views.routines"),
    NavItem("tasks", _("Tasks"), "tasks", "views.tasks"),
]
# Where Tasks has the conversation's own key (keymap conversations.tasks).
_CONVERSATION_VIEWS = ("conversations", "archive")


# The 3D buttons of the menu and the sidebar (#250): (face, light top edge, dark bottom edge),
# the design's colours on the xterm 256-colour cube (#76). The first class a button has wins.
BEVEL_COLOURS: dict[str, tuple[str, str, str]] = {
    "nav-dashboard": ("#005fd7", "#5f87ff", "#005f87"),
    "nav-conversations": ("#875fff", "#af87ff", "#5f00af"),
    "nav-archive": ("#af8700", "#d7af5f", "#875f00"),
    "nav-sessions": ("#af5f87", "#d787af", "#87005f"),
    "nav-agents": ("#0087af", "#5fafd7", "#005f87"),
    "nav-analytics": ("#00af5f", "#5fd787", "#005f5f"),
    "nav-routines": ("#d75f00", "#ff875f", "#875f00"),
    "nav-tasks": ("#5f5f87", "#87afaf", "#005f5f"),
    "sidebar-button-green": ("#00af5f", "#5fd787", "#005f5f"),
    "sidebar-button-red": ("#af5f5f", "#d78787", "#5f0000"),
    "sidebar-button": ("#3a3a3a", "#626262", "#1c1c1c"),
}


def _sidebar_ground() -> str:
    return theme.VARIABLES["ccc-sidebar"]


def _next_bevel(widget) -> "BevelButton | None":
    """The 3D button right under `widget` (the next shown sibling), if any."""
    parent = widget.parent
    if parent is None:
        return None
    siblings = list(parent.children)
    for sibling in siblings[siblings.index(widget) + 1:]:
        if sibling.display:
            return sibling if isinstance(sibling, BevelButton) else None
    return None


class BevelButton(Button):
    """A 3D button of the menu or the sidebar, low as in the design: the
    label row, then one row split in half — this button's dark bottom
    edge over the light top edge of the button under it (or the menu's
    ground under the last one). The first one of a group has a BevelCap
    above it: its light top edge. In a short window just the label row."""

    def on_mount(self) -> None:
        # The edge rows go from side to side (CSS refuses "line-pad: 0").
        self.styles.line_pad = 0

    def colours(self) -> tuple[str, str, str]:
        return next((c for cls, c in BEVEL_COLOURS.items() if self.has_class(cls)), BEVEL_COLOURS["sidebar-button"])

    def label_line(self, width: int) -> Content:
        return self.label.first_line.truncate(width, ellipsis=True)

    def render(self):
        width = self.content_size.width
        if width <= 0:
            return self.label
        line = self.label_line(width)
        if self.size.height < 2 or self.disabled:
            return line
        _face, _light, dark = self.colours()
        below = _next_bevel(self)
        ground = below.colours()[1] if below is not None else _sidebar_ground()
        return Content("\n").join([line, Content.styled("▀" * width, f"{dark} on {ground}")])


class BevelCap(Static):
    """The light top edge of the first 3D button of a group (the lower half of a row)."""

    DEFAULT_CSS = """
    BevelCap {
        height: 1;
        width: 100%;
    }
    SidebarScreen.-short BevelCap {
        display: none;
    }
    """

    def render(self):
        below = _next_bevel(self)
        width = self.content_size.width
        if below is None or width <= 0:
            return ""
        return Content.styled("▄" * width, f"{below.colours()[1]} on {_sidebar_ground()}")


class NavButton(BevelButton):
    """A menu button: "> Label ……… key count", the key and the count at
    the right edge, whatever the menu's width."""

    # Its size also where ccc's app CSS (BUTTON_CSS, the colours) is missing:
    # a host app in tests; in ccc BUTTON_CSS sets the same.
    DEFAULT_CSS = """
    NavButton {
        width: 100%;
        height: 2;
        min-width: 0;
        border: none;
        padding: 0;
    }
    SidebarScreen.-short NavButton {
        height: 1;
    }
    """

    def __init__(self, item: NavItem, key: str, current: bool):
        self.item, self.key_text, self.count = item, key, None
        super().__init__(self._plain(), id=f"nav-{item.key}", classes=f"nav-button nav-{item.key}")
        self.can_focus = False  # like the other sidebar buttons: their keys do it
        self.set_class(current, "-current")

    def _plain(self) -> str:
        count = "" if self.count is None else f" {self.count}"
        return f"{escape(self.item.label)}  {escape(self.key_text)}{count}"

    def set_key(self, key: str) -> None:
        self.key_text = key
        self.label = self._plain()

    def set_count(self, count: int | None) -> None:
        if count != self.count:
            self.count = count
            self.label = self._plain()

    def label_line(self, width: int) -> Content:
        width -= 2  # a space at each edge
        left = Text.assemble(("> " if self.has_class("-current") else "  "), self.item.label)
        right = Text.assemble((self.key_text, "dim"))
        if self.count is not None:
            right.append(f" {self.count}", style="bold")
        gap = max(1, width - left.cell_len - right.cell_len)
        return Content.from_text(Text.assemble(" ", left, " " * gap, right, " "))


class StatusBar(Footer):
    """The one bar at the bottom: the screen's keys (Textual's Footer:
    clickable, kept up to date). Which screen is open the menu shows."""

    # The keys wrap into more rows when they don't fit (#305).
    DEFAULT_CSS = """
    StatusBar {
        layout: vertical;
        height: auto;
        max-height: 6;
    }
    StatusBar .status-row {
        height: 1;
        width: 100%;
        layout: horizontal;
    }
    """

    def __init__(self):
        super().__init__(show_command_palette=False, compact=True)
        self._wrapped_width = 0

    @staticmethod
    def key_width(widget) -> int:
        """Cells a key takes in the bar: its key, a space, its description, a gap."""
        return cell_len(str(widget.key_display)) + 1 + cell_len(str(widget.description)) + 1

    def compose(self):
        if not self._bindings_ready:
            return
        width = self.size.width or self.app.size.width
        self._wrapped_width = width
        row, used = [], 0
        for widget in super().compose():
            if hasattr(widget, "description") and isinstance(widget.description, str):
                widget.description = widget.description.lower()
            cost = self.key_width(widget)
            if row and used + cost > width:
                yield Horizontal(*row, classes="status-row")
                row, used = [], 0
            row.append(widget)
            used += cost
        if row:
            yield Horizontal(*row, classes="status-row")

    def on_resize(self, event) -> None:
        if event.size.width != self._wrapped_width:
            self.call_after_refresh(self.recompose)

ADD_PROMPT_BUTTON = SidebarButton("sidebar-add-prompt", _("Add prompt ({key})"), "green", "add_prompt",
                                  "screen.add_prompt")
RETRY_BUTTON = SidebarButton("sidebar-retry", _("Retry blocked queue ({key})"), "red", "retry_queue", "screen.retry")
# What r (and the retry button) can get going again.
RETRYABLE = (QueueStatus.BLOCKED_ERROR, QueueStatus.PAUSED_LIMIT)
NEW_CONVERSATION_BUTTON = SidebarButton(
    "sidebar-new-conversation", _("New conversation ({key})"), "green", "new_conversation",
    "views.new_conversation",
)

# Above the conversation list: opens the filter (f); a strip like the key list at the bottom.
FILTER_BUTTON = SidebarButton("sidebar-filter", _("{key} Filter"), "strip", "filter", "conversations.filter")
# Next to the filter: renames the selected conversation (F2).
RENAME_BUTTON = SidebarButton("sidebar-rename", _("{key} Rename"), "strip", "rename", "conversations.rename")
# Under New conversation: archives the selected conversation (x).
ARCHIVE_BUTTON = SidebarButton("sidebar-archive", _("Archive ({key})"), "teal", "toggle_archive",
                               "conversations.archive")

STATUS_STYLES = {
    QueueStatus.IDLE: "dim",
    QueueStatus.RUNNING: "bold green",
    QueueStatus.WAITING_RETRY: "bold yellow",
    QueueStatus.BLOCKED_ERROR: "bold red",
    QueueStatus.PAUSED_LIMIT: "bold magenta",
}


def status_text(status: QueueStatus) -> str:
    """A queue status as shown to the user."""
    return {
        QueueStatus.IDLE: _("idle"),
        QueueStatus.RUNNING: _("running"),
        QueueStatus.WAITING_RETRY: _("waiting_retry"),
        QueueStatus.BLOCKED_ERROR: _("blocked_error"),
        QueueStatus.PAUSED_LIMIT: _("paused_limit"),
    }.get(status, status.value)


def styled_status(status: QueueStatus) -> str:
    """Rich markup for a queue status cell (status texts are fixed, so no
    escaping needed)."""
    style = STATUS_STYLES.get(status, "")
    return f"[{style}]{status_text(status)}[/]" if style else status_text(status)


# The "working" dot turns clockwise: a gold dot with a dark wedge going round. A terminal has no glyph
# with a narrow wedge (◐◓◑◒ are half circles, 180°): a braille cell is a ring of 8 dots, so one dot
# missing is a wedge of 45° — the narrowest there is — and the missing one walks round the ring.
_RING = (0x01, 0x08, 0x10, 0x20, 0x80, 0x40, 0x04, 0x02)  # braille dots clockwise from the top left
WORKING_FRAMES = tuple(chr(0x2800 + 0xFF - dot) for dot in _RING)
WORKING_STYLE = "bold #ffd700"
FRAME_SECONDS = 0.2
DONE_STYLE, ERROR_STYLE, ATTENTION_STYLE = "bold green", "bold red", "bold dodger_blue1"


def working_frame(now: float | None = None) -> str:
    """The turning "working" dot at this moment."""
    return WORKING_FRAMES[int((time.monotonic() if now is None else now) / FRAME_SECONDS) % len(WORKING_FRAMES)]


def queue_marker(queue: Queue, unseen: bool, needs_approval: bool = False) -> tuple[str, str]:
    """(icon, style) shown before a conversation's name. Waiting for your
    approval first, then current activity, then idle: "new reply" or
    already looked at (an archived conversation has no marker)."""
    if needs_approval:
        return "●", ATTENTION_STYLE  # blue: needs you
    if queue.status == QueueStatus.RUNNING or queue.live_busy:
        return working_frame(), WORKING_STYLE  # gold, turning: working
    if queue.status == QueueStatus.BLOCKED_ERROR:
        return "●", ERROR_STYLE  # red: error
    if queue.status in (QueueStatus.PAUSED_LIMIT, QueueStatus.WAITING_RETRY):
        return "‖", "bold magenta"
    if queue.paused:
        return "‖", "bold yellow"
    if unseen:
        return "●", DONE_STYLE  # green: finished, not looked at yet
    if queue.archived:
        return " ", ""
    return "○", "dim"


def queue_label(queue: Queue, unseen: bool, pending: int = 0, needs_approval: bool = False) -> Text:
    """List label: marker, name (dim when archived, bold with a new reply), pending count."""
    icon, style = queue_marker(queue, unseen, needs_approval)
    label = Text.assemble((icon, style), " ", (queue.name, "dim" if queue.archived else "bold" if unseen else ""))
    if pending:
        label.append(f" ·{pending}", style="dim")
    return label


MODAL_TITLE_BACKGROUND = "#af5f5f"


class ModalTitle(Static):
    """The red title bar at the top of a window (the New conversation
    design, #250), a row and a half high: the title row, then the lower
    edge of the bar in the upper half of the next row."""

    DEFAULT_CSS = """
    ModalTitle {
        dock: top;
        width: 100%;
        height: 2;
    }
    """

    def __init__(self, title: str):
        super().__init__(id="modal-title")
        self.title_text = title

    def set_title(self, title: str) -> None:
        self.title_text = title
        self.refresh()

    def render(self):
        width = self.content_size.width
        if width <= 0:
            return ""
        ground = self.parent.styles.background if self.parent is not None else None
        ground = ground.hex if ground is not None and ground.a > 0 else theme.VARIABLES["ccc-panel"]
        title = Content(f"  {self.title_text.upper()}").truncate(width, ellipsis=True)
        title = title.extend_right(width - title.cell_length)
        return Content("\n").join([
            title.stylize(f"bold white on {MODAL_TITLE_BACKGROUND}"),
            Content.styled("▀" * width, f"{MODAL_TITLE_BACKGROUND} on {ground}"),
        ])


class CccModal(ModalScreen):
    """Base of every ccc window: the window's box (the screen's first
    child) gets the New conversation design's frame (#250) — its
    border_title becomes the red ModalTitle bar at the top, flush with
    the frame whatever the box's padding."""

    def on_mount(self) -> None:
        # Textual dispatches on_mount along the MRO: this runs before each window's own.
        box = next(iter(self.children), None)
        if box is None or not box.border_title:
            return
        title = str(box.border_title)
        box.border_title = None
        # Textual clips children to the box's padding: the bar reaches the
        # frame only when the box has none, so its sides move to the content.
        top, right, bottom, left = box.styles.padding
        if right or left:
            for child in box.children:
                m = child.styles.margin
                child.styles.margin = (m.top, m.right + right, m.bottom, m.left + left)
        box.styles.padding = (0, 0, bottom, 0)
        bar = ModalTitle(title)
        bar.styles.margin = (0, 0, top, 0)
        box.mount(bar, before=0)


class ConfirmModal(CccModal):
    """Yes/no question. Dismisses True only when confirmed; with
    can_confirm=False it's just a notice (any key closes it, False)."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
        background: $ccc-background 60%;
    }
    ConfirmModal > Vertical {
        width: 70;
        height: auto;
        border: solid $ccc-text;
        background: $ccc-panel;
        padding: 1 2 0 2;
    }
    ConfirmModal .confirm-hint {
        color: $ccc-text-muted;
        margin-top: 1;
        height: 2;
        border-top: solid $ccc-line;
    }
    """

    BINDINGS = [
        keymap.binding("answer.yes", "answer(True)", _("Yes")),
        keymap.binding("confirm.enter", "answer(True)", _("Yes")),
        keymap.binding("answer.no", "answer(False)", _("No")),
        keymap.binding("window.cancel", "answer(False)", _("No")),
    ]

    def __init__(self, title: str, text: str, can_confirm: bool = True):
        super().__init__()
        self.box_title = title
        self.text = text
        self.can_confirm = can_confirm

    def compose(self):
        hint = _("y / Enter yes  ·  n / Esc no") if self.can_confirm else _("Esc close")
        box = Vertical(Static(Text(self.text), id="confirm-text"), Static(hint, classes="confirm-hint"))
        box.border_title = self.box_title
        yield box

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes and self.can_confirm)


# Every ccc button (CCContinueApp.CSS — app CSS beats Textual's defaults
# whatever their specificity): flat, one colour per role — grey, green
# (Start, Save), red (delete) as in the design (#250) — and the same
# size and colour on hover, Tab focus and click. Textual's default turned
# a clicked button black and two rows shorter (#55).
# The colours sit on the xterm 256-colour cube: in a 256-colour terminal
# (tmux) Textual rounds to the nearest one, and the old teal #00796b and
# green #2e7d32 both became #00875f, so two buttons looked like one (#76).
BUTTON_CSS = """
$ccc-button: #3a3a3a;
$ccc-button-ok: #00af5f;
$ccc-button-danger: #af5f5f;
Button {
    border: none;
    height: 3;
    text-style: bold;
    color: white;
    background: $ccc-button;
    tint: transparent;
}
Button:hover {
    background-tint: white 15%;
}
Button:focus {
    text-style: bold underline;
    background-tint: white 15%;
}
Button.-active {
    background-tint: black 25%;
}
Button.modal-submit, Button.workflow-ok, Button.sidebar-button-green {
    background: $ccc-button-ok;
}
Button.settings-danger, Button.review-danger, Button.goal-danger,
Button.routines-danger, Button.workflow-danger, Button.sidebar-button-red {
    background: $ccc-button-danger;
}
/* The 3D buttons of the menu and the sidebar (#102, #250): BevelButton draws the edges itself —
   the label row, then a row split in half: its dark bottom edge over the light top edge of the
   button under it (BEVEL_COLOURS; a BevelCap above the first one). Low, as in the design. The
   menu's buttons are all the same size; the open view is marked only by ">". A short window
   (SidebarScreen.-short) keeps just the label rows. */
Button.nav-button, Button.sidebar-button {
    width: 100%;
    height: 2;
    border: none;
    padding: 0;
    text-style: bold;
    color: white;
}
Button.nav-button {
    text-align: left;
    content-align: left top;
}
Button.sidebar-button {
    text-align: center;
    content-align: center top;
}
Button.nav-button:hover {
    background-tint: white 10%;
}
Button.nav-dashboard { background: #005fd7; }
Button.nav-conversations { background: #875fff; }
Button.nav-archive { background: #af8700; }
Button.nav-sessions { background: #af5f87; }
Button.nav-agents { background: #0087af; }
Button.nav-analytics { background: #00af5f; }
Button.nav-routines { background: #d75f00; }
Button.nav-tasks { background: #5f5f87; }
SidebarScreen.-short Button.nav-button, SidebarScreen.-short Button.sidebar-button {
    height: 1;
}
/* Filter: a strip like the key list at the bottom (one row, blue, orange key) */
#sidebar-strip-row {
    height: 1;
    background: $footer-background;
}
#sidebar-strip-row Button.sidebar-button-strip {
    width: auto;
    min-width: 0;
}
Button.sidebar-button-strip {
    height: 1;
    border: none;
    border-top: none;
    border-bottom: none;
    background: $footer-background;
    color: $footer-description-foreground;
    text-style: none;
    text-align: left;
    content-align: left middle;
    padding: 0 1;
}
/* New conversation (#250): "+ option" is a one-row chip, the session a field like the inputs */
Button.nc-small {
    height: 1;
    min-width: 0;
    width: auto;
    padding: 0 1;
    background: $ccc-background;
    color: $ccc-text-muted;
    text-style: none;
    border: none;
}
Button.nc-field {
    width: 100%;
    height: 3;
    content-align: left middle;
    text-align: left;
    text-style: none;
    color: $ccc-text;
    background: $ccc-background;
    border: tall $ccc-line;
}
Button.nc-field:focus {
    border: tall $ccc-accent;
    text-style: none;
}
Button:disabled {
    background: $panel;
    color: $text-muted;
    background-tint: transparent;
}
"""

SHARED_CSS = """
SidebarScreen {
    background: $ccc-background;
}
SidebarScreen #sidebar {
    width: 32;
    background: $ccc-sidebar;
    border: solid $ccc-frame;
}
/* In ccc's tmux the top and bottom of the frame are tmux's; the menu adds
   its left line, and its right one — none while Claude Code is open, tmux's
   line is the one. */
SidebarScreen.-tmux-frame #sidebar {
    border: none;
    border-left: solid $ccc-frame;
    border-right: solid $ccc-frame;
}
SidebarScreen.-tmux-frame.-cli-open #sidebar {
    border-right: none;
}
SidebarScreen #daemon-state {
    padding: 0 1;
    margin-bottom: 1;
}
SidebarScreen.-short #daemon-state {
    margin-bottom: 0;
}
SidebarScreen #restart-state {
    padding: 0 1;
    color: $ccc-warning;
}
SidebarScreen #restart-state:hover {
    text-style: underline;
}
SidebarScreen #update-state {
    padding: 0 1;
    color: $ccc-warning;
}
SidebarScreen #update-state:hover {
    text-style: underline;
}
SidebarScreen .sidebar-label {
    padding: 0 1 1 1;
    text-style: bold;
    color: $text-muted;
}
SidebarScreen #sidebar-nav {
    height: auto;
}
SidebarScreen #sidebar-section {
    height: 1fr;
    margin-top: 1;
}
SidebarScreen .sidebar-list {
    height: 1fr;
    background: $ccc-sidebar;
    scrollbar-background: $ccc-sidebar;
    scrollbar-color: $ccc-line;
}
SidebarScreen #sidebar DataTable.sidebar-list > .datatable--cursor {
    background: $ccc-selection;
    color: white;
    text-style: bold;
}
SidebarScreen #sidebar Button.sidebar-button {
    width: 100%;
    height: 2;
    border: none;
    text-style: bold;
}
SidebarScreen.-short #sidebar Button.sidebar-button {
    height: 1;
}
SidebarScreen #main {
    width: 1fr;
    margin-left: 1;
    background: $ccc-panel;
    border: solid $ccc-frame;
    padding: 0 1;
}
SidebarScreen.-tmux-frame #main {
    border: none;
    margin-left: 0;
}
/* A section of the content: a thin grey line round it, its title in the
   line (upper case, muted), as the design's ACTIVITY. */
SidebarScreen .panel {
    border: solid $ccc-frame;
    border-title-color: $ccc-text-muted;
    border-title-style: bold;
    padding: 0 1;
    margin-bottom: 1;
    height: auto;
}
SidebarScreen .panel-fill {
    height: 1fr;
}
SidebarScreen .panel DataTable {
    height: auto;
    max-height: 100%;
    background: transparent;
}
SidebarScreen .panel-fill DataTable {
    height: 1fr;
}
SidebarScreen .panel DataTable > .datatable--cursor {
    background: $ccc-selection;
    color: $ccc-accent;
    text-style: bold;
}
SidebarScreen DataTable > .datatable--header {
    background: transparent;
    color: $ccc-text-dim;
    text-style: bold;
}
SidebarScreen .muted {
    color: $ccc-text-muted;
}
"""

# Same frame for every window (#250), the New conversation design's: a light
# thin line round a dark box, the title in a red bar at the top (CccModal,
# ModalTitle), fields with a thin line that turns green with the focus,
# muted labels, a green Submit and a grey Cancel button.
MODAL_CSS = """
{cls} {{
    align: center middle;
    background: $ccc-background 60%;
}}
{cls} > Container {{
    width: 84;
    height: auto;
    border: solid $ccc-text;
    background: $ccc-panel;
    padding: 1 2;
}}
{cls} Input {{
    margin-bottom: 1;
    background: $ccc-background;
    border: tall $ccc-line;
}}
{cls} Input:focus {{
    border: tall $ccc-accent;
}}
{cls} TextArea {{
    height: 8;
    margin-bottom: 1;
}}
{cls} .modal-label {{
    text-style: bold;
    color: $ccc-text-muted;
}}
{cls} .modal-error {{
    color: $ccc-error;
}}
{cls} .modal-buttons {{
    height: 3;
    align-horizontal: right;
}}
{cls} .modal-buttons Button {{
    margin-left: 1;
}}
"""


def panel(*children, title: str, id: str | None = None, fill: bool = False) -> Vertical:
    """A thin-lined content box with its title (upper case, as the design's
    ACTIVITY) drawn in the line."""
    box = Vertical(*children, id=id, classes="panel panel-fill" if fill else "panel")
    box.border_title = title.upper()
    return box


class SidebarScreen(Screen):
    """Base for all top-level screens. Subclasses set VIEW (which sidebar
    nav item is highlighted), SUB_TITLE (its name), and
    override compose_main() and optionally compose_sidebar_section() /
    sidebar_buttons()."""

    VIEW: str = ""

    DEFAULT_CSS = SHARED_CSS

    SUB_TITLE: str = ""

    # The status bar only shows what the menu doesn't: the views, Agents and
    # Tasks have their key printed on their menu button (Routines is in both,
    # as in the design), the sidebar buttons next to them. A view's own keys
    # (like "+" for its Add prompt button) live in that view only.
    BINDINGS = [
        *[keymap.binding(f"views.{view}", f"goto('{view}')", label, show=False) for view, label in VIEWS],
        keymap.binding("views.new_conversation", "new_conversation", _("New conversation"), show=False),
        keymap.binding("views.help", "key_help", _("Help")),
        keymap.binding("views.routines", "routines", _("Routines")),
        keymap.binding("views.agents", "agents", _("Agents"), show=False),
        keymap.binding("views.tasks", "tasks", _("Tasks"), show=False),
        keymap.binding("views.update", "update", _("Update"), show=False),
    ]

    def __init__(self, storage):
        super().__init__()
        self.storage = storage

    # --- hooks -----------------------------------------------------------

    def compose_sidebar_section(self):
        """Extra widgets between the nav list and the buttons."""
        return []

    def compose_main(self):
        return []

    def sidebar_buttons(self) -> list[SidebarButton]:
        return []

    def top_sidebar_buttons(self) -> list[SidebarButton]:
        """Buttons a screen puts itself, in compose_sidebar_section (above its list)."""
        return []

    def retry_target(self) -> Queue | None:
        """The queue the retry button acts on (the selected one)."""
        return None

    # --- frame -----------------------------------------------------------

    def compose(self):
        nav_items = [NavButton(item, self._nav_key(item), item.view and item.key == self.VIEW) for item in NAV]
        yield Horizontal(
            Vertical(
                Static("", id="daemon-state"),
                Static("", id="restart-state"),
                Static("", id="update-state"),
                Vertical(BevelCap(), *nav_items, id="sidebar-nav"),
                Vertical(*self.compose_sidebar_section(), id="sidebar-section"),
                *([BevelCap()] if self.sidebar_buttons() else []),
                *[self._make_button(b) for b in self.sidebar_buttons()],
                id="sidebar",
            ),
            Vertical(*self.compose_main(), id="main"),
        )
        yield StatusBar()

    def on_screen_resume(self) -> None:
        self.refresh_daemon_state()

    def sync_retry_button(self) -> None:
        """Show the retry button only while there is something to retry."""
        # query_one stops at the first match; a query() walks every widget
        # of the screen — a long transcript has thousands.
        try:
            button = self.query_one(f"#{RETRY_BUTTON.id}")
        except NoMatches:
            return
        try:
            target = self.retry_target()
        except sqlite3.OperationalError:
            # The DB is locked by another process: keep the button as it is
            # instead of crashing the whole TUI; the next refresh retries.
            return
        shown = target is not None and target.status in RETRYABLE
        if button.display != shown:
            button.display = shown
            # The button above it draws the edge between them.
            for sibling in button.parent.children:
                sibling.refresh()

    def on_mount(self) -> None:
        # Textual dispatches on_mount along the MRO, so this runs in
        # addition to each screen's own on_mount.
        self.refresh_daemon_state()
        self.set_interval(2.0, self.refresh_daemon_state)
        if getattr(self.app, "footer_strip", False):
            # The keys are in the strip under the whole window (footer_strip),
            # and tmux draws the frame (see SHARED_CSS: the menu's border).
            self.query_one(StatusBar).display = False
            self.add_class("-tmux-frame")
        self._refresh_live_sessions()
        self.set_interval(LIVE_SESSIONS_SECONDS, self._refresh_live_sessions)

    def refresh_daemon_state(self) -> None:
        from ccc.heartbeat import daemon_alive

        # Always shown, above the menu: running (green) or the problem (red).
        if daemon_alive(self.storage):
            text = Text.assemble(("● ", "bold " + theme.ACCENT), (_("daemon running"), theme.ACCENT))
        else:
            text = Text(_("✖ daemon not running — queues are stopped"), style=f"bold {theme.ERROR}")
        self.query_one("#daemon-state", Static).update(text)
        self._refresh_restart_state()
        self._refresh_update_state()
        self.sync_retry_button()
        self.refresh_nav_counts()

    def refresh_nav_counts(self) -> None:
        """The counts on the menu's buttons: active conversations, live
        sessions (once known), routines, waiting tasks."""
        try:
            queues = [q for q in self.storage.list_queues() if not q.archived]
            counts = {
                "conversations": len(queues),
                "sessions": _LIVE_SESSIONS.get("count"),
                "routines": len(self.storage.list_routines()),
                "tasks": self.storage.count_all_pending_items(),
            }
        except sqlite3.OperationalError:
            return  # the DB is locked: the next refresh tries again
        for key, count in counts.items():
            try:
                self.query_one(f"#nav-{key}", NavButton).set_count(count)
            except NoMatches:
                pass

    def _refresh_live_sessions(self) -> None:
        """How many live Claude Code sessions there are (for the menu) —
        `claude agents`, in a thread, at most every LIVE_SESSIONS_SECONDS
        for all screens; not in tests (headless)."""
        if self.app.is_headless or time.monotonic() - _LIVE_SESSIONS.get("at", -LIVE_SESSIONS_SECONDS) < LIVE_SESSIONS_SECONDS:
            return
        _LIVE_SESSIONS["at"] = time.monotonic()

        def work() -> None:
            from ccc.session_discovery import discover_sessions

            try:
                set_live_sessions(len(discover_sessions()))
            except Exception:  # noqa: BLE001 — only a count; the view itself shows the error
                return
            self.app.call_from_thread(self.refresh_nav_counts)

        self.run_worker(work, thread=True, group="live-sessions", exclusive=True)

    def _refresh_restart_state(self) -> None:
        """ccc was upgraded while it ran (ccc.updates.restart_needed): a line under the daemon's,
        a click restarts the services — when no task runs."""
        from ccc import updates

        needed = updates.restart_needed(self.storage)
        line = self.query_one("#restart-state", Static)
        line.update(Text(_("↻ restart needed — click") if needed else ""))
        line.display = needed

    def _restart_ccc(self) -> None:
        from ccc import updates

        ok, message = updates.restart(self.storage)
        self.app.notify(message, severity="information" if ok else "warning")

    def _refresh_update_state(self) -> None:
        """A new release on GitHub (ccc.updates, found by the daemon):
        a line under the title; the key (or a click) opens the Update window."""
        from ccc import updates

        found = updates.load(self.storage)
        shown = found.available and updates.check_enabled(self.storage)
        line = self.query_one("#update-state", Static)
        line.update(Text(_("⬆ Update: {version} — {key}").format(
            version=found.version, key=keymap.pretty(self._keys()["views.update"])) if shown else ""))
        line.display = shown

    def on_click(self, event) -> None:
        widget_id = getattr(event.widget, "id", None)
        if widget_id == "update-state":
            self.action_update()
        elif widget_id == "restart-state":
            self._restart_ccc()

    def action_update(self) -> None:
        from ccc.tui.update import UpdateModal

        self.app.push_screen(UpdateModal(self.storage))

    def _keys(self) -> dict[str, str]:
        return keymap.current(self.storage)

    def _nav_key(self, item: NavItem, keys: dict[str, str] | None = None) -> str:
        key_id = item.key_id
        if item.key == "tasks" and self.VIEW in _CONVERSATION_VIEWS:
            key_id = "conversations.tasks"
        return keymap.pretty((keys or self._keys())[key_id])

    def refresh_key_labels(self) -> None:
        """The nav list's and buttons' keys again (shortcuts changed)."""
        keys = self._keys()
        for item in NAV:
            for button in self.query(f"#nav-{item.key}"):
                button.set_key(self._nav_key(item, keys))
        for spec in [*self.top_sidebar_buttons(), *self.sidebar_buttons()]:
            for button in self.query(f"#{spec.id}"):
                button.label = spec.text(keys)

    def _make_button(self, spec: SidebarButton) -> Button:
        kind = Button if spec.color == "strip" else BevelButton
        button = kind(spec.text(self._keys()), id=spec.id, classes=f"sidebar-button sidebar-button-{spec.color}")
        # Every button has its own hotkey; keeping them out of the focus
        # chain means initial focus (and Enter) lands on the screen's list,
        # not on whichever button happens to come first in the sidebar.
        button.can_focus = False
        return button

    # Rows of the window: below MEDIUM_WINDOW the menu's buttons lose their
    # top edge (two rows each), below SHORT_WINDOW they are one row each.
    MEDIUM_WINDOW = 46
    SHORT_WINDOW = 34

    def on_resize(self, event) -> None:
        self.set_class(event.size.height < self.SHORT_WINDOW, "-short")
        self.set_class(self.SHORT_WINDOW <= event.size.height < self.MEDIUM_WINDOW, "-medium")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if isinstance(event.button, NavButton):
            event.stop()
            self.call_later(self.run_action, event.button.item.action)
            return
        for button in [*self.top_sidebar_buttons(), *self.sidebar_buttons()]:
            if event.button.id == button.id:
                event.stop()
                getattr(self, f"action_{button.action}")()
                return

    # --- navigation ------------------------------------------------------

    def _make_view(self, view: str, **kwargs) -> Screen:
        if view == "dashboard":
            from ccc.tui.dashboard import DashboardScreen

            return DashboardScreen(self.storage)
        if view == "conversations":
            from ccc.tui.conversations import ConversationsScreen

            return ConversationsScreen(self.storage, **kwargs)
        if view == "archive":
            from ccc.tui.archive import ArchiveScreen

            return ArchiveScreen(self.storage, **kwargs)
        if view == "sessions":
            from ccc.tui.session_picker import SessionPickerScreen

            return SessionPickerScreen(self.storage)
        if view == "analytics":
            from ccc.tui.analytics import AnalyticsScreen

            return AnalyticsScreen(self.storage)
        raise ValueError(f"unknown view: {view}")

    def action_goto(self, view: str) -> None:
        if view == self.VIEW and not getattr(self, "IS_DETAIL", False):
            return
        self._navigate(view, self._make_view(view))

    def _navigate(self, view: str, screen: Screen) -> None:
        """Show another top-level view, keeping the stack shallow:
        dashboard is the home screen at the bottom, at most one other view
        sits on top of it (plus a detail screen pushed from that view)."""
        from ccc.tui.dashboard import DashboardScreen

        stack = self.app.screen_stack
        home_index = next((i for i, s in enumerate(stack) if isinstance(s, DashboardScreen)), None)
        if home_index is not None:
            for _screen in range(len(stack) - 1 - home_index):
                self.app.pop_screen()
            if view != "dashboard":
                self.app.push_screen(screen)
        else:
            self.app.switch_screen(screen)

    def action_key_help(self) -> None:
        from ccc.help import VIEW_TOPICS
        from ccc.tui.help import HelpScreen

        self.app.push_screen(HelpScreen(VIEW_TOPICS.get(self.VIEW, "getting-started")))

    def action_new_conversation(self) -> None:
        from ccc.tui import cli_pane
        from ccc.tui.new_conversation import NewConversationModal

        # With Claude Code open ccc is only the sidebar's width: too narrow
        # for the dialog, so it gets the whole window meanwhile.
        zoomed = cli_pane.zoom_ccc()

        def opened(queue_id: int | None) -> None:
            cli_pane.unzoom_ccc(zoomed)
            if queue_id is not None:
                self.open_conversation(queue_id)

        self.app.push_screen(NewConversationModal(self.storage), opened)

    def open_conversation(self, queue_id: int) -> None:
        self._navigate("conversations", self._make_view("conversations", select_queue_id=queue_id))

    def selected_queue_id(self) -> int | None:
        """The conversation this screen is about (a new routine's default)."""
        target = self.retry_target()
        return target.id if target is not None else None

    def action_review(self) -> None:
        """Review the conversation this screen is about (ccc.reviews)."""
        from ccc.tui.review import ReviewModal

        queue_id = self.selected_queue_id()
        if queue_id is None:
            self.notify(_("Select a conversation first."), severity="warning")
            return
        self.app.push_screen(ReviewModal(self.storage, queue_id))

    def action_goal(self) -> None:
        """A goal loop for the conversation this screen is about (ccc.goals)."""
        from ccc.tui.goal import GoalModal

        queue_id = self.selected_queue_id()
        if queue_id is None:
            self.notify(_("Select a conversation first."), severity="warning")
            return
        self.app.push_screen(GoalModal(self.storage, queue_id))

    def action_workflow(self) -> None:
        """A multi-agent workflow on the conversation's task (ccc.workflows)."""
        from ccc.tui.workflow import WorkflowModal

        queue_id = self.selected_queue_id()
        if queue_id is None:
            self.notify(_("Select a conversation first."), severity="warning")
            return
        self.app.push_screen(WorkflowModal(self.storage, queue_id))

    def action_routines(self) -> None:
        from ccc.tui.routines import RoutinesModal

        self.app.push_screen(RoutinesModal(self.storage, self.selected_queue_id()))

    def action_tasks(self) -> None:
        """The task queue of the conversation this screen is about (the menu's Tasks)."""
        from ccc.tui.tasks import TasksModal

        queue_id = self.selected_queue_id()
        if queue_id is None:
            self.notify(_("Select a conversation first."), severity="warning")
            return
        self.app.push_screen(TasksModal(self.storage, queue_id))

    def action_agents(self) -> None:
        from ccc.tui.agents import AgentsModal

        queue_id = self.selected_queue_id()
        queue = self.storage.find_queue(queue_id) if queue_id is not None else None
        self.app.push_screen(AgentsModal(queue.project_path if queue else self.default_project_path()))

    def action_add_prompt(self) -> None:
        from ccc.tui.queue_detail import AddPromptModal

        self.app.push_screen(AddPromptModal(self.storage, self.default_project_path()))

    def default_project_path(self) -> str:
        """Prefill for the Add-prompt dialog: the currently selected queue's
        project, where the screen has one."""
        return ""

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()
