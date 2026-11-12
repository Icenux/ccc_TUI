from __future__ import annotations

from rich.text import Text
from textual.containers import Container, Horizontal
from textual.content import Content
from textual.widgets import Button, DataTable, Static, TextArea

from ccc import keymap
from ccc.i18n import _
from ccc.orchestrator import attach_to_session
from ccc.session_discovery import DiscoveredSession, discover_sessions
from ccc.storage import Storage
from ccc.tui.layout import CccModal, ADD_PROMPT_BUTTON, MODAL_CSS, SidebarButton, SidebarScreen, panel, set_live_sessions


REFRESH_BUTTON = SidebarButton("sidebar-refresh", _("Reload session list ({key})"), "teal", "refresh",
                              "sessions.refresh")

SESSION_STATUS_STYLES = {"idle": "green", "busy": "yellow", "blocked": "bold red"}
# Claude Code's own status / kind words, as shown in the table.
SESSION_WORDS = {
    "idle": _("idle"), "busy": _("busy"), "blocked": _("blocked"),
    "interactive": _("interactive"), "background": _("background"),
}


class SessionPickerScreen(SidebarScreen):
    """Lists live Claude Code sessions on this machine (via `claude agents
    --json`) so you can queue a prompt for a *specific* one — including
    when several share the same working directory, which project-path-only
    queues can't distinguish."""

    VIEW = "sessions"
    SUB_TITLE = _("Live sessions")

    BINDINGS = [
        keymap.binding("screen.back", "back", _("Back"), show=False),
        keymap.binding("sessions.refresh", "refresh", _("Reload session list"), show=False),
        keymap.binding("screen.add_prompt", "add_prompt", _("Add prompt"), show=False),
    ]

    def __init__(self, storage: Storage):
        super().__init__(storage)
        self.sessions: list[DiscoveredSession] = []

    def sidebar_buttons(self):
        return [ADD_PROMPT_BUTTON, REFRESH_BUTTON]

    def compose_main(self):
        yield panel(
            Static(
                _("Enter on a session to send it a prompt — typed into its own terminal once it's idle."),
                classes="muted",
            ),
            DataTable(id="session-picker-table"),
            title=_("Live Claude Code sessions on this machine"),
            fill=True,
        )

    def on_mount(self) -> None:
        table = self.query_one("#session-picker-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(_("Name"), _("Kind"), _("Status"), _("Directory"))
        self.refresh_sessions()

    def refresh_sessions(self) -> None:
        self.sessions = discover_sessions()
        set_live_sessions(len(self.sessions))
        self.refresh_nav_counts()
        table = self.query_one("#session-picker-table", DataTable)
        table.clear()
        for session in self.sessions:
            status = session.state if session.state == "blocked" else (session.status or "")
            style = SESSION_STATUS_STYLES.get(status)
            status_cell = Text(SESSION_WORDS.get(status, status), style=style or "")
            table.add_row(Text(session.name), SESSION_WORDS.get(session.kind, session.kind), status_cell,
                          Text(session.cwd, style="dim"))

    def action_refresh(self) -> None:
        self.refresh_sessions()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # DataTable owns the "enter" key itself (action select_cursor),
        # so listen for its message instead of a screen-level binding.
        if event.cursor_row >= len(self.sessions):
            return
        session = self.sessions[event.cursor_row]
        self.app.push_screen(AttachSessionModal(self.storage, session))


class AttachSessionModal(CccModal):
    DEFAULT_CSS = MODAL_CSS.format(cls="AttachSessionModal") + """
    AttachSessionModal .attach-prompt-warning {
        color: $warning;
        margin-bottom: 1;
    }
    """

    BINDINGS = [keymap.binding("window.cancel", "cancel", _("Cancel"))]

    def __init__(self, storage: Storage, session: DiscoveredSession):
        super().__init__()
        self.storage = storage
        self.session = session

    def compose(self):
        box = Container(
            Static(Content.assemble((self.session.name, "bold"), "  ", (self.session.cwd, "dim"))),
            Static(
                _("Delivered by typing into that session's own terminal once it's idle "
                  "and not rate-limited — the response appears there, not in ccc. "
                  "If you're mid-typing something else in that terminal at the exact "
                  "moment this fires, the two can interleave."),
                classes="attach-prompt-warning",
            ),
            Static(_("Prompt"), classes="modal-label"),
            TextArea(id="attach-prompt-text"),
            Static("", id="attach-prompt-error", classes="modal-error"),
            Horizontal(
                Button(_("Send"), id="attach-prompt-submit", classes="modal-submit"),
                Button(_("Cancel"), id="attach-prompt-cancel", classes="modal-cancel"),
                classes="modal-buttons",
            ),
        )
        box.border_title = _("Send prompt to live session")
        yield box

    def on_mount(self) -> None:
        self.query_one("#attach-prompt-text", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "attach-prompt-submit":
            prompt = self.query_one("#attach-prompt-text", TextArea).text.strip()
            if not prompt:
                self.query_one("#attach-prompt-error", Static).update("Prompt is required.")
                return
            attach_to_session(
                self.storage, self.session.session_id, self.session.cwd, self.session.name, prompt
            )
            self.dismiss()
        elif event.button.id == "attach-prompt-cancel":
            self.dismiss()

    def action_cancel(self) -> None:
        self.dismiss()
