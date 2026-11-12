"""The workflow window (w in Conversations / queue detail): run a chain
of agents on the conversation's task — plan, your approval, build, check,
parallel reviews, arbiter — and follow every step (ccc.workflows; the
daemon runs it). At a gate you approve or reject here."""
from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Input, Select, Static, TextArea

from ccc import keymap, workflows
from ccc.i18n import N_, _
from ccc.models import WorkflowRun, WorkflowStatus
from ccc.schedule import format_when
from ccc.tui.layout import CccModal, MODAL_CSS

REFRESH_SECONDS = 2.0
OUTPUT_CHARS = 2000
STATUS_STYLES = {WorkflowStatus.RUNNING: "bold yellow", WorkflowStatus.WAITING: "bold magenta",
                 WorkflowStatus.PASSED: "bold green", WorkflowStatus.FAILED: "bold red",
                 WorkflowStatus.STOPPED: "dim", WorkflowStatus.ERROR: "bold red"}
VERDICT_STYLES = {"PASS": "green", "FAIL": "red", "PARTIAL": "yellow", "APPROVED": "green", "REJECTED": "red"}
EVENT_LABELS = {"running": N_("running…"), "done": N_("done"), "error": N_("error"), "waiting": N_("waits for you")}


def status_text(run: WorkflowRun) -> Text:
    return Text(_(workflows.STATUS_LABELS[run.status]), style=STATUS_STYLES[run.status])


def graph(run: WorkflowRun) -> Text:
    """The run's steps with where it is: ✓ done · ▶ now · ⏸ waits · ○ to come."""
    parts = []
    for n, step in enumerate(run.template["steps"]):
        label = _(workflows.KIND_LABELS[step["type"]])
        if step["type"] == "review":
            label += f" ×{len(step['reviewers'])}"
        if n < run.step or run.status == WorkflowStatus.PASSED:
            parts.append(Text(f"✓ {label}", style="green"))
        elif n == run.step and run.status == WorkflowStatus.WAITING:
            parts.append(Text(f"⏸ {label}", style="bold magenta"))
        elif n == run.step and run.status == WorkflowStatus.RUNNING:
            parts.append(Text(f"▶ {label}", style="bold yellow"))
        elif n == run.step:
            parts.append(Text(f"✖ {label}", style="red"))
        else:
            parts.append(Text(f"○ {label}", style="dim"))
    return Text("  →  ").join(parts)


class WorkflowModal(CccModal):
    DEFAULT_CSS = MODAL_CSS.format(cls="WorkflowModal") + """
    WorkflowModal > Container {
        width: 98%;
        max-width: 150;
        height: 98%;
    }
    WorkflowModal TextArea {
        height: 3;
    }
    WorkflowModal Select {
        margin-bottom: 0;
    }
    WorkflowModal #workflow-preview {
        color: $text-muted;
        margin-bottom: 1;
    }
    WorkflowModal .workflow-row {
        height: auto;
    }
    WorkflowModal #workflow-check {
        width: 1fr;
    }
    WorkflowModal #workflow-rounds {
        width: 10;
        margin-left: 1;
    }
    WorkflowModal .workflow-rounds-label {
        width: auto;
        padding: 1 0 0 1;
        color: $text-muted;
    }
    WorkflowModal #workflow-table {
        height: 5;
        margin-bottom: 1;
    }
    WorkflowModal #workflow-table > .datatable--cursor {
        background: $ccc-selection;
        color: white;
        text-style: bold;
    }
    WorkflowModal #workflow-detail-box {
        height: 1fr;
        border: solid $ccc-line;
        padding: 0 1;
    }
    WorkflowModal .workflow-actions {
        height: 3;
        margin-top: 1;
    }
    WorkflowModal .workflow-actions Button {
        height: 3;
        margin-right: 1;
    }
    WorkflowModal #workflow-note {
        width: 1fr;
        margin-bottom: 0;
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
        found = workflows.templates(self.storage)
        box = Container(
            Select([(workflows.title(name, d), name) for name, d in found.items()], value=next(iter(found)),
                   allow_blank=False, id="workflow-template"),
            Static("", id="workflow-preview"),
            TextArea(id="workflow-task"),
            Horizontal(
                Input(placeholder=_("Check command for its check step, e.g. pytest -q (optional)"),
                      id="workflow-check"),
                Input("", id="workflow-rounds", type="integer"),
                Static(_("rounds"), classes="workflow-rounds-label"),
                classes="workflow-row",
            ),
            DataTable(id="workflow-table", cursor_type="row"),
            VerticalScroll(Static("", id="workflow-detail"), id="workflow-detail-box"),
            Horizontal(
                Button(_("Start"), id="workflow-start"),
                Input(placeholder=_("Note for the next step (optional)"), id="workflow-note"),
                Button(_("Approve"), id="workflow-approve", classes="workflow-ok"),
                Button(_("Reject"), id="workflow-reject", classes="workflow-danger"),
                Button(_("Stop"), id="workflow-stop", classes="workflow-danger"),
                classes="workflow-actions",
            ),
        )
        box.border_title = _("Workflow — {name}").format(name=queue.name)
        yield box

    def on_mount(self) -> None:
        for button in self.query(".workflow-actions Button"):
            button.can_focus = False
        self.query_one("#workflow-task", TextArea).placeholder = _(
            "The task, e.g. Add a CSV export of orders with a test")
        self.query_one("#workflow-table", DataTable).add_columns(
            _("Started"), _("Workflow"), _("Round"), _("State"), _("Task"))
        self._show_preview()
        self._refresh()
        self.set_interval(REFRESH_SECONDS, self._refresh)
        self.query_one("#workflow-task", TextArea).focus()

    def _template(self) -> tuple[str, dict]:
        name = self.query_one("#workflow-template", Select).value
        return name, workflows.templates(self.storage)[name]

    def _show_preview(self) -> None:
        name, definition = self._template()
        definition = workflows.validate(definition)
        preview = workflows.describe(definition)
        if name not in workflows.BUILT_IN:
            preview += "\n" + workflows.details(definition)  # your own template: everything it says and runs
        self.query_one("#workflow-preview", Static).update(Text(preview))  # plain text, never markup
        self.query_one("#workflow-rounds", Input).value = str(definition.get("max_rounds", 3))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "workflow-template":
            self._show_preview()

    def _refresh(self) -> None:
        table = self.query_one("#workflow-table", DataTable)
        previous = self.selected()
        table.clear()
        items = self.storage.list_workflow_runs(self.queue_id)
        self._ids = [r.id for r in items]
        now = datetime.now()
        for r in items:
            table.add_row(format_when(r.created_at, now), r.name, f"{r.round}/{r.max_rounds}", status_text(r),
                          Text(r.task.splitlines()[0][:70]))
        if not items:
            table.add_row("", "", "", "", Text(_("no workflows yet"), style="dim"))
        elif previous is not None and previous.id in self._ids:
            table.move_cursor(row=self._ids.index(previous.id))
        running = workflows.active(self.storage, self.queue_id)
        self.query_one("#workflow-start", Button).disabled = running is not None
        self.query_one("#workflow-stop", Button).disabled = running is None
        waiting = running is not None and running.status == WorkflowStatus.WAITING
        for button in ("#workflow-approve", "#workflow-reject"):
            self.query_one(button, Button).disabled = not waiting
        self._show_detail()

    def selected(self) -> WorkflowRun | None:
        row = self.query_one("#workflow-table", DataTable).cursor_row
        if row is None or row >= len(self._ids):
            return None
        return self.storage.find_workflow_run(self._ids[row])

    def _show_detail(self) -> None:
        run = self.selected()
        detail = self.query_one("#workflow-detail", Static)
        if run is None:
            detail.update(_("A workflow sends the task through a chain of agents; only the build step changes code, "
                            "the others read. Pick a template above to see its steps."))
            return
        parts = [graph(run), Text(run.task)]
        if run.status == WorkflowStatus.WAITING:
            parts.append(Text(_("Waits for your answer: Approve or Reject below."), style="bold magenta"))
        if run.last_reason and run.status in workflows.FINISHED:
            parts.append(Text(_("Why: {reason}").format(reason=run.last_reason)))
        for event in reversed(self.storage.workflow_events(run.id)):
            head = Text.assemble(
                (f"{_(workflows.KIND_LABELS.get(event.kind, event.kind))} · {event.name}", "bold"),
                f"  ({_('round')} {event.round})  ",
                (event.verdict, VERDICT_STYLES.get(event.verdict, "")) if event.verdict else
                (_(EVENT_LABELS.get(event.status, event.status)),
                 {"error": "red", "done": "green"}.get(event.status, "yellow")),
            )
            parts.append(head)
            if event.output:
                parts.append(Text(event.output[:OUTPUT_CHARS] + (" …" if len(event.output) > OUTPUT_CHARS else ""),
                                  style="dim" if event.kind == "code" else ""))
        detail.update(Text("\n\n").join(parts))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"workflow-start": self.action_start, "workflow-approve": lambda: self._answer(True),
         "workflow-reject": lambda: self._answer(False), "workflow-stop": self.action_stop}.get(
            event.button.id, lambda: None)()

    def action_start(self) -> None:
        name, _definition = self._template()
        rounds = self.query_one("#workflow-rounds", Input).value
        try:
            workflows.start(self.storage, self.queue_id, name, self.query_one("#workflow-task", TextArea).text,
                            self.query_one("#workflow-check", Input).value, int(rounds) if rounds.isdigit() else None)
        except ValueError as error:
            self.notify(str(error), severity="warning")
            return
        self.notify(_("Workflow started — the daemon runs its steps."))
        self._refresh()
        self.query_one("#workflow-table", DataTable).move_cursor(row=0)

    def _answer(self, approve: bool) -> None:
        note = self.query_one("#workflow-note", Input)
        if workflows.answer_gate(self.storage, self.queue_id, approve, note.value):
            note.value = ""
            self.notify(_("Approved — the workflow goes on.") if approve else _("Rejected — the workflow stops."))
        self._refresh()

    def action_stop(self) -> None:
        if workflows.stop(self.storage, self.queue_id):
            self.notify(_("Workflow stopped."))
        self._refresh()

    def action_close(self) -> None:
        self.dismiss()
