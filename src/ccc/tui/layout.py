"""Shared look for every ccc screen.

The conversations view (left sidebar: app title, rule, section label,
selectable list, coloured action buttons at the bottom; right: content in
round-bordered boxes) is the reference design. Every top-level screen
subclasses SidebarScreen so they all share that frame, and adds its own
sidebar section / main content / buttons through the hooks below.
"""
from __future__ import annotations

from dataclasses import dataclass

from rich.markup import escape
from rich.text import Text
from textual.css.query import NoMatches
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Static

from ccc import keymap
from ccc.i18n import _
from ccc.models import Queue, QueueStatus


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


def queue_marker(queue: Queue, unseen: bool, needs_approval: bool = False) -> tuple[str, str]:
    """(icon, style) shown before a conversation's name. Waiting for your
    approval first, then current activity, then idle: "new reply" or
    already looked at (an archived conversation has no marker)."""
    if needs_approval:
        return "?", "bold yellow"
    if queue.status == QueueStatus.RUNNING or queue.live_busy:
        return "●", "bold green"
    if queue.status == QueueStatus.BLOCKED_ERROR:
        return "✖", "bold red"
    if queue.status in (QueueStatus.PAUSED_LIMIT, QueueStatus.WAITING_RETRY):
        return "‖", "bold magenta"
    if queue.paused:
        return "‖", "bold yellow"
    if unseen:
        return "◆", "bold cyan"
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


class ConfirmModal(ModalScreen):
    """Yes/no question. Dismisses True only when confirmed; with
    can_confirm=False it's just a notice (any key closes it, False)."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal > Vertical {
        width: 70;
        height: auto;
        border: round red;
        border-title-color: red;
        border-title-style: bold;
        background: $surface;
        padding: 1 2;
    }
    ConfirmModal .confirm-hint {
        color: $text-muted;
        margin-top: 1;
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
# whatever their specificity): flat, one colour per role, and the same
# size and colour on hover, Tab focus and click. Textual's default turned
# a clicked button black and two rows shorter (#55). Not blue: blue marks
# the focused field and the selected row, so a blue button looked focused.
# The colours sit on the xterm 256-colour cube: in a 256-colour terminal
# (tmux) Textual rounds to the nearest one, and the old teal #00796b and
# green #2e7d32 both became #00875f, so two buttons looked like one (#76).
BUTTON_CSS = """
$ccc-button: #008787;
$ccc-button-ok: #008700;
$ccc-button-danger: #af0000;
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
Button.modal-cancel, Button.settings-danger, Button.review-danger, Button.goal-danger,
Button.routines-danger, Button.workflow-danger, Button.sidebar-button-red {
    background: $ccc-button-danger;
}
/* sidebar buttons are 3D (#102): light top edge, dark bottom edge, swapped while pressed */
Button.sidebar-button {
    border-top: tall #5fd7d7;
    border-bottom: tall #005f5f;
}
Button.sidebar-button-green {
    border-top: tall #5fd75f;
    border-bottom: tall #005f00;
}
Button.sidebar-button-red {
    border-top: tall #ff5f5f;
    border-bottom: tall #5f0000;
}
Button.sidebar-button.-active {
    border-top: tall #005f5f;
    border-bottom: tall #5fd7d7;
}
Button.sidebar-button-green.-active {
    border-top: tall #005f00;
    border-bottom: tall #5fd75f;
}
Button.sidebar-button-red.-active {
    border-top: tall #5f0000;
    border-bottom: tall #ff5f5f;
}
/* The main menu (Dashboard … Analytics): 3D like the buttons below, each its own colour,
   directly under each other, half as high (the text row and the dark lower edge); the open
   view is lighter. In a short window (SidebarScreen.-short) one row each, so the list and
   the buttons still fit. */
Button.nav-button {
    width: 100%;
    height: 2;
    border: none;
    text-style: bold;
}
Button.nav-button.-current {
    background-tint: white 25%;
    text-style: bold underline;
}
Button.nav-dashboard { background: #005fff; border-top: none; border-bottom: tall #00005f; }
Button.nav-conversations { background: #8700d7; border-top: none; border-bottom: tall #5f0087; }
Button.nav-archive { background: #af5f00; border-top: none; border-bottom: tall #5f2f00; }
Button.nav-sessions { background: #af005f; border-top: none; border-bottom: tall #5f005f; }
Button.nav-analytics { background: #af8700; border-top: none; border-bottom: tall #5f5f00; }
SidebarScreen.-short Button.nav-button {
    height: 1;
    border: none;
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
Button.sidebar-button:disabled {
    border-top: tall $panel;
    border-bottom: tall $panel;
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
    border: round $ccc-frame;
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
}
SidebarScreen #restart-state {
    padding: 0 1;
    color: yellow;
}
SidebarScreen #restart-state:hover {
    text-style: underline;
}
SidebarScreen #update-state {
    padding: 0 1;
    color: yellow;
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
    height: 3;
    border: none;
    text-style: bold;
}
SidebarScreen #main {
    width: 1fr;
    padding: 0 1;
}
SidebarScreen .panel {
    border: round $ccc-frame;
    border-title-color: $ccc-frame;
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
    color: white;
    text-style: bold;
}
SidebarScreen .muted {
    color: $text-muted;
}
"""

# Same frame for modal dialogs: a round green box with the title in the
# border, coloured Submit/Cancel buttons matching the sidebar ones.
MODAL_CSS = """
{cls} {{
    align: center middle;
}}
{cls} > Container {{
    width: 84;
    height: auto;
    border: round $ccc-title;
    border-title-color: $ccc-title;
    border-title-style: bold;
    background: $surface;
    padding: 1 2;
}}
{cls} Input {{
    margin-bottom: 1;
}}
{cls} TextArea {{
    height: 8;
    margin-bottom: 1;
}}
{cls} .modal-label {{
    text-style: bold;
}}
{cls} .modal-error {{
    color: red;
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
    """A round-bordered content box with its title drawn in the border —
    the main-pane equivalent of the conversation message boxes."""
    box = Vertical(*children, id=id, classes="panel panel-fill" if fill else "panel")
    box.border_title = title
    return box


class SidebarScreen(Screen):
    """Base for all top-level screens. Subclasses set VIEW (which sidebar
    nav item is highlighted), SUB_TITLE, and override compose_main() and
    optionally compose_sidebar_section() / sidebar_buttons()."""

    VIEW: str = ""

    DEFAULT_CSS = SHARED_CSS

    # The footer only shows what the sidebar doesn't: views and the
    # sidebar buttons have their hotkey printed right next to them.
    # The buttons are only the extra controls of the current view —
    # switching views is the nav list's job, not a button's. A view's own
    # keys (like "+" for its Add prompt button) live in that view only.
    BINDINGS = [
        *[keymap.binding(f"views.{view}", f"goto('{view}')", label, show=False) for view, label in VIEWS],
        keymap.binding("views.new_conversation", "new_conversation", _("New conversation"), show=False),
        keymap.binding("views.help", "key_help", _("Help")),
        keymap.binding("views.routines", "routines", _("Routines")),
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
        nav_items = []
        for key, label in VIEWS:
            item = Button(self._nav_text(key, label), id=f"nav-{key}", classes=f"nav-button nav-{key}")
            item.can_focus = False  # like the other sidebar buttons: their keys do it
            if key == self.VIEW:
                item.add_class("-current")
            nav_items.append(item)
        yield Horizontal(
            Vertical(
                Static("", id="daemon-state"),
                Static("", id="restart-state"),
                Static("", id="update-state"),
                Vertical(*nav_items, id="sidebar-nav"),
                Vertical(*self.compose_sidebar_section(), id="sidebar-section"),
                *[self._make_button(b) for b in self.sidebar_buttons()],
                id="sidebar",
            ),
            Vertical(*self.compose_main(), id="main"),
        )
        yield Footer()

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
        target = self.retry_target()
        button.display = target is not None and target.status in RETRYABLE

    def on_mount(self) -> None:
        # Textual dispatches on_mount along the MRO, so this runs in
        # addition to each screen's own on_mount.
        self.refresh_daemon_state()
        self.set_interval(2.0, self.refresh_daemon_state)
        if getattr(self.app, "footer_strip", False):
            # The keys are in the strip under the whole window (footer_strip),
            # and tmux draws the frame (see SHARED_CSS: the menu's border).
            self.query_one(Footer).display = False
            self.add_class("-tmux-frame")

    def refresh_daemon_state(self) -> None:
        from ccc.heartbeat import daemon_alive

        # Always shown, above the menu: running (green) or the problem (red).
        if daemon_alive(self.storage):
            text = Text(_("● daemon running"), style="bold green")
        else:
            text = Text(_("✖ daemon not running — queues are stopped"), style="bold red")
        self.query_one("#daemon-state", Static).update(text)
        self._refresh_restart_state()
        self._refresh_update_state()
        self.sync_retry_button()

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

    def _nav_text(self, view: str, label: str) -> str:
        return f"{label}  [dim]{escape(keymap.pretty(self._keys()[f'views.{view}']))}[/dim]"

    def refresh_key_labels(self) -> None:
        """The nav list's and buttons' keys again (shortcuts changed)."""
        keys = self._keys()
        for view, label in VIEWS:
            for item in self.query(f"#nav-{view}"):
                item.label = self._nav_text(view, label)
        for spec in [*self.top_sidebar_buttons(), *self.sidebar_buttons()]:
            for button in self.query(f"#{spec.id}"):
                button.label = spec.text(keys)

    def _make_button(self, spec: SidebarButton) -> Button:
        button = Button(spec.text(self._keys()), id=spec.id, classes=f"sidebar-button sidebar-button-{spec.color}")
        # Every button has its own hotkey; keeping them out of the focus
        # chain means initial focus (and Enter) lands on the screen's list,
        # not on whichever button happens to come first in the sidebar.
        button.can_focus = False
        return button

    SHORT_WINDOW = 36  # rows: below it the main menu's buttons are one row each

    def on_resize(self, event) -> None:
        self.set_class(event.size.height < self.SHORT_WINDOW, "-short")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("nav-button") and event.button.id:
            event.stop()
            self.action_goto(event.button.id.removeprefix("nav-"))
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
