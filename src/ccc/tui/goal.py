"""The goal window (g in Conversations / queue detail): give the
conversation a goal and let it work in rounds — task, check, fresh
review — until a reviewer says it's met (ccc.goals; the daemon runs it)."""
from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Input, Static, TextArea

from ccc import goals, keymap
from ccc.i18n import _
from ccc.models import Goal, GoalStatus
from ccc.schedule import format_when
from ccc.tui.layout import CccModal, MODAL_CSS

REFRESH_SECONDS = 2.0
STATUS_STYLES = {GoalStatus.RUNNING: "bold yellow", GoalStatus.PASSED: "bold green", GoalStatus.STUCK: "bold red",
                 GoalStatus.LIMIT: "bold red", GoalStatus.STOPPED: "dim", GoalStatus.ERROR: "bold red"}


def status_text(goal: Goal) -> Text:
    return Text(_(goals.STATUS_LABELS[goal.status]), style=STATUS_STYLES[goal.status])


class GoalModal(CccModal):
    DEFAULT_CSS = MODAL_CSS.format(cls="GoalModal") + """
    GoalModal > Container {
        width: 95%;
        max-width: 140;
        height: 95%;
    }
    GoalModal TextArea {
        height: 4;
    }
    GoalModal .goal-row {
        height: auto;
    }
    GoalModal #goal-check {
        width: 1fr;
    }
    GoalModal #goal-rounds {
        width: 12;
        margin-left: 1;
    }
    GoalModal .goal-rounds-label {
        width: auto;
        padding-left: 1;
    }
    GoalModal #goal-table {
        height: 7;
        margin-bottom: 1;
    }
    GoalModal #goal-table > .datatable--cursor {
        background: $ccc-selection;
        color: white;
        text-style: bold;
    }
    GoalModal #goal-detail-box {
        height: 1fr;
        border: solid $ccc-line;
        padding: 0 1;
    }
    GoalModal .goal-actions {
        height: 3;
        margin-top: 1;
    }
    GoalModal .goal-actions Button {
        height: 3;
        margin-right: 1;
    }
    GoalModal .modal-hint {
        color: $text-muted;
        width: 1fr;
        padding-top: 1;
    }
    """

    BINDINGS = [
        keymap.binding("window.save", "start", _("Start"), priority=True),
        keymap.binding("window.cancel", "close", _("Close"), priority=True),
    ]

    def __init__(self, storage, queue_id: int):
        super().__init__()
        self.storage = storage
        self.queue_id = queue_id
        self._ids: list[int] = []

    def compose(self):
        queue = self.storage.get_queue(self.queue_id)
        box = Container(
            Static(_("Describe once what done looks like. Claude works in rounds; after each, the check runs and a "
                     "fresh reviewer judges the code against the goal — until it's met."), classes="modal-hint"),
            Static(_("Goal"), classes="modal-label"),
            TextArea(id="goal-text"),
            Horizontal(
                Input(placeholder=_("Check command after each round, e.g. pytest -q (optional)"), id="goal-check"),
                Input(str(goals.DEFAULT_ROUNDS), id="goal-rounds", type="integer", tooltip=_("at most this many rounds")),
                Static(_("rounds"), classes="modal-hint goal-rounds-label"),
                classes="goal-row",
            ),
            DataTable(id="goal-table", cursor_type="row"),
            VerticalScroll(Static("", id="goal-detail"), id="goal-detail-box"),
            Horizontal(
                Button(_("Start"), id="goal-start"),
                Button(_("Stop"), id="goal-stop", classes="goal-danger"),
                Static(_("Ctrl+S start  ·  Esc close"), classes="modal-hint"),
                classes="goal-actions",
            ),
        )
        box.border_title = _("Goal — {name}").format(name=queue.name)
        yield box

    def on_mount(self) -> None:
        for button in self.query(".goal-actions Button"):
            button.can_focus = False
        self.query_one("#goal-text", TextArea).placeholder = _(
            "e.g. The auth middleware rejects expired tokens, with a test that proves it")
        self.query_one("#goal-table", DataTable).add_columns(_("Started"), _("Round"), _("State"), _("Goal"))
        self._refresh()
        self.set_interval(REFRESH_SECONDS, self._refresh)
        self.query_one("#goal-text", TextArea).focus()

    def _refresh(self) -> None:
        table = self.query_one("#goal-table", DataTable)
        previous = self.selected()
        table.clear()
        items = self.storage.list_goals(self.queue_id)
        self._ids = [g.id for g in items]
        now = datetime.now()
        for g in items:
            table.add_row(format_when(g.created_at, now), f"{g.round}/{g.max_rounds}", status_text(g),
                          Text(g.goal.splitlines()[0][:80]))
        if not items:
            table.add_row("", "", "", Text(_("no goals yet"), style="dim"))
        elif previous is not None and previous.id in self._ids:
            table.move_cursor(row=self._ids.index(previous.id))
        running = goals.active(self.storage, self.queue_id) is not None
        self.query_one("#goal-start", Button).disabled = running
        self.query_one("#goal-stop", Button).disabled = not running
        self._show_detail()

    def selected(self) -> Goal | None:
        row = self.query_one("#goal-table", DataTable).cursor_row
        if row is None or row >= len(self._ids):
            return None
        return self.storage.find_goal(self._ids[row])

    def _show_detail(self) -> None:
        g = self.selected()
        detail = self.query_one("#goal-detail", Static)
        if g is None:
            detail.update("")
            return
        parts = [Text(g.goal)]
        if g.check_command:
            parts.append(Text(_("Check: {command}").format(command=g.check_command), style="dim"))
        if g.status == GoalStatus.RUNNING:
            parts.append(Text(_(goals.PHASE_LABELS[g.phase]).format(n=g.round), style="yellow"))
        else:
            parts.append(status_text(g))
        if g.last_reason:
            label = _("Last round:") if g.status == GoalStatus.RUNNING else _("Why:")
            parts.append(Text(f"{label} {g.last_reason}"))
        detail.update(Text("\n\n").join(parts))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"goal-start": self.action_start, "goal-stop": self.action_stop}.get(event.button.id, lambda: None)()

    def action_start(self) -> None:
        rounds_text = self.query_one("#goal-rounds", Input).value
        try:
            goals.start(self.storage, self.queue_id, self.query_one("#goal-text", TextArea).text,
                        self.query_one("#goal-check", Input).value,
                        int(rounds_text) if rounds_text.isdigit() else goals.DEFAULT_ROUNDS)
        except ValueError as error:
            self.notify(str(error), severity="warning")
            return
        self.notify(_("Goal set — its first task is in the conversation's queue."))
        self._refresh()
        self.query_one("#goal-table", DataTable).move_cursor(row=0)

    def action_stop(self) -> None:
        if goals.stop(self.storage, self.queue_id):
            self.notify(_("Goal stopped."))
        self._refresh()

    def action_close(self) -> None:
        self.dismiss()
