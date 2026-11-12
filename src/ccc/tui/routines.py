"""The Routines window (o anywhere): every routine with its schedule and
next run — add, edit, turn on / off, run now, delete. The daemon runs
them (ccc.routines.tick); this only edits the table."""
from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Input, Select, Static, TextArea

from ccc import keymap, routines
from ccc.i18n import _
from ccc.models import Routine, RoutineTarget
from ccc.schedule import format_when
from ccc.tui.layout import CccModal, ConfirmModal, MODAL_CSS

TARGETS = [
    (RoutineTarget.SAME, _("In this conversation (Claude remembers earlier runs)")),
    (RoutineTarget.FRESH, _("A new conversation every run (clean start, archived when done)")),
]


class RoutineEditModal(CccModal):
    """Add or edit one routine. Dismisses with the routine's id, or None."""

    DEFAULT_CSS = MODAL_CSS.format(cls="RoutineEditModal") + """
    RoutineEditModal > Container {
        width: 90%;
        max-width: 110;
    }
    RoutineEditModal Select {
        margin-bottom: 1;
    }
    RoutineEditModal #routine-schedule {
        height: auto;
    }
    RoutineEditModal #routine-kind {
        width: 1fr;
    }
    RoutineEditModal #routine-value {
        width: 1fr;
        margin-left: 1;
    }
    RoutineEditModal #routine-next {
        color: $text-muted;
        margin-bottom: 1;
    }
    RoutineEditModal .modal-hint {
        color: $text-muted;
        width: 1fr;
        padding-top: 1;
    }
    """

    BINDINGS = [
        keymap.binding("window.save", "save", _("Save"), priority=True),
        keymap.binding("window.cancel", "cancel", _("Cancel"), priority=True),
    ]

    def __init__(self, storage, routine: Routine | None = None, queue_id: int | None = None):
        super().__init__()
        self.storage = storage
        self.routine = routine
        self.queue_id = routine.queue_id if routine else queue_id

    def compose(self):
        r = self.routine
        conversations = [(q.name, q.id) for q in self.storage.list_queues()
                         if not q.archived and q.routine_id is None]
        kind = r.schedule_kind if r else routines.DAILY
        box = Container(
            Static(_("Name"), classes="modal-label"),
            Input(r.name if r else "", placeholder=_("e.g. Nightly review"), id="routine-name"),
            Static(_("Conversation"), classes="modal-label"),
            Select(conversations, value=self.queue_id if self.queue_id in {qid for _n, qid in conversations}
                   else Select.NULL, prompt=_("choose…"), id="routine-queue"),
            Static(_("Where it runs"), classes="modal-label"),
            Select([(label, target.value) for target, label in TARGETS],
                   value=(r.target if r else RoutineTarget.SAME).value, allow_blank=False, id="routine-target"),
            Static(_("When"), classes="modal-label"),
            Horizontal(
                Select([(_(label), k) for k, label, _hint in routines.KINDS], value=kind, allow_blank=False,
                       id="routine-kind"),
                Input(r.schedule_value if r else "18:00", id="routine-value"),
                id="routine-schedule",
            ),
            Static("", id="routine-next"),
            Static(_("Prompt"), classes="modal-label"),
            TextArea(r.prompt_text if r else "", id="routine-prompt"),
            Static("", id="routine-error", classes="modal-error"),
            Horizontal(
                Static(_("Ctrl+S save  ·  Esc cancel"), classes="modal-hint"),
                Button(_("Cancel"), id="routine-cancel", classes="modal-cancel"),
                Button(_("Save"), id="routine-save", classes="modal-submit"),
                classes="modal-buttons",
            ),
        )
        box.border_title = _("Edit routine") if r else _("New routine")
        yield box

    def on_mount(self) -> None:
        self._show_hint()
        self._show_next()
        self.query_one("#routine-name" if not self.routine else "#routine-prompt").focus()

    def _kind(self) -> str:
        return self.query_one("#routine-kind", Select).value

    def _show_hint(self) -> None:
        hints = {k: _(hint) for k, _label, hint in routines.KINDS}
        self.query_one("#routine-value", Input).placeholder = hints[self._kind()]

    def _show_next(self) -> None:
        """The next run as you type (or why the schedule won't do)."""
        value = self.query_one("#routine-value", Input).value
        label = self.query_one("#routine-next", Static)
        try:
            cron = routines.to_cron(self._kind(), value)
        except ValueError as error:
            label.update(Text(str(error), style="red"))
            return
        at = routines.next_run(cron, datetime.now())
        label.update(_("{schedule} · next run {at}").format(
            schedule=routines.describe(self._kind(), value), at=format_when(at, datetime.now())))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "routine-kind":
            self._show_hint()
            self._show_next()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "routine-value":
            self._show_next()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "routine-save":
            self.action_save()
        elif event.button.id == "routine-cancel":
            self.action_cancel()

    def action_save(self) -> None:
        queue_id = self.query_one("#routine-queue", Select).value
        error = self.query_one("#routine-error", Static)
        if queue_id is Select.NULL:
            error.update(_("Choose the conversation."))
            return
        values = dict(
            queue_id=queue_id, name=self.query_one("#routine-name", Input).value,
            prompt_text=self.query_one("#routine-prompt", TextArea).text, kind=self._kind(),
            value=self.query_one("#routine-value", Input).value,
            target=RoutineTarget(self.query_one("#routine-target", Select).value),
        )
        try:
            if self.routine:
                routines.update(self.storage, self.routine.id, **values)
                routine_id = self.routine.id
            else:
                routine_id = routines.add(self.storage, **values).id
        except ValueError as problem:
            error.update(str(problem))
            return
        self.dismiss(routine_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RoutinesModal(CccModal):
    DEFAULT_CSS = MODAL_CSS.format(cls="RoutinesModal") + """
    RoutinesModal > Container {
        width: 95%;
        max-width: 140;
        height: 85%;
    }
    RoutinesModal #routines-table {
        height: 1fr;
        margin-bottom: 1;
    }
    RoutinesModal #routines-table > .datatable--cursor {
        background: $ccc-selection;
        color: white;
        text-style: bold;
    }
    RoutinesModal #routines-detail {
        height: auto;
        min-height: 2;
        color: $text-muted;
    }
    RoutinesModal .routines-actions {
        height: 3;
    }
    RoutinesModal .routines-actions Button {
        height: 3;
        margin-right: 1;
    }
    """

    BINDINGS = [
        keymap.binding("routines.add", "add", _("Add")),
        keymap.binding("routines.edit", "edit", _("Edit")),
        keymap.binding("routines.toggle", "toggle", _("On / off")),
        keymap.binding("routines.run", "run_now", _("Run now")),
        keymap.binding("routines.delete", "delete", _("Delete")),
        keymap.binding("window.cancel", "close", _("Close")),
    ]

    def __init__(self, storage, queue_id: int | None = None):
        super().__init__()
        self.storage = storage
        self.queue_id = queue_id  # preselected for a new routine
        self._ids: list[int] = []

    def compose(self):
        box = Container(
            DataTable(id="routines-table", cursor_type="row"),
            Static("", id="routines-detail"),
            Horizontal(
                Button(_("Add"), id="routines-add"),
                Button(_("Edit"), id="routines-edit"),
                Button(_("On / off"), id="routines-toggle"),
                Button(_("Run now"), id="routines-run"),
                Button(_("Delete"), id="routines-delete", classes="routines-danger"),
                classes="routines-actions",
            ),
        )
        box.border_title = _("Routines — prompts on a schedule")
        yield box

    def on_mount(self) -> None:
        for button in self.query(".routines-actions Button"):
            button.can_focus = False
        table = self.query_one("#routines-table", DataTable)
        table.add_columns(_("On"), _("Routine"), _("Conversation"), _("When"), _("Next run"), _("Last run"))
        self._refresh()
        table.focus()

    def _refresh(self, select_id: int | None = None) -> None:
        table = self.query_one("#routines-table", DataTable)
        previous = table.cursor_row
        table.clear()
        now = datetime.now()
        names = {q.id: q.name for q in self.storage.list_queues()}
        items = self.storage.list_routines()
        self._ids = [r.id for r in items]
        for r in items:
            fresh = " ✚" if r.target == RoutineTarget.FRESH else ""
            table.add_row(
                Text("●", style="green") if r.enabled else Text("○", style="dim"),
                r.name + fresh, names.get(r.queue_id, "?"), routines.describe(r.schedule_kind, r.schedule_value),
                format_when(r.next_run_at, now) if r.enabled else "—",
                format_when(r.last_run_at, now) or "—",
            )
        if not items:
            table.add_row("", Text(_("no routines yet — Add one"), style="dim"), "", "", "", "")
        elif select_id in self._ids:
            table.move_cursor(row=self._ids.index(select_id))
        elif previous is not None:
            table.move_cursor(row=min(previous, len(items) - 1))
        self._show_detail()

    def selected(self) -> Routine | None:
        row = self.query_one("#routines-table", DataTable).cursor_row
        if row is None or row >= len(self._ids):
            return None
        return self.storage.find_routine(self._ids[row])

    def _show_detail(self) -> None:
        r = self.selected()
        detail = self.query_one("#routines-detail", Static)
        if r is None:
            detail.update(_("A routine queues its prompt on a schedule — the daemon runs it even with ccc closed."))
            return
        last = _(routines.STATUS_LABELS[r.last_status]) if r.last_status in routines.STATUS_LABELS else ""
        parts = [r.prompt_text.splitlines()[0][:120]]
        if last:
            parts.append(_("last: {status}").format(status=last))
        if r.skipped:
            parts.append(_("skipped {n}×").format(n=r.skipped))
        detail.update(" · ".join(parts))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_edit() if self._ids else self.action_add()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"routines-add": self.action_add, "routines-edit": self.action_edit, "routines-toggle": self.action_toggle,
         "routines-run": self.action_run_now, "routines-delete": self.action_delete}.get(event.button.id, lambda: None)()

    def action_add(self) -> None:
        self.app.push_screen(RoutineEditModal(self.storage, queue_id=self.queue_id),
                             lambda routine_id: self._refresh(routine_id))

    def action_edit(self) -> None:
        r = self.selected()
        if r is not None:
            self.app.push_screen(RoutineEditModal(self.storage, r), lambda _id: self._refresh(r.id))

    def action_toggle(self) -> None:
        r = self.selected()
        if r is not None:
            routines.set_enabled(self.storage, r.id, not r.enabled)
            self._refresh(r.id)

    def action_run_now(self) -> None:
        r = self.selected()
        if r is None:
            return
        status = routines.fire(self.storage, r)
        self.notify(_(routines.STATUS_LABELS[status]), title=r.name,
                    severity="warning" if status in (routines.STATUS_SKIPPED, routines.STATUS_ARCHIVED) else "information")
        self._refresh(r.id)

    def action_delete(self) -> None:
        r = self.selected()
        if r is None:
            return

        def answered(yes: bool) -> None:
            if yes:
                self.storage.delete_routine(r.id)
                self._refresh()

        self.app.push_screen(ConfirmModal(_("Delete routine"), _("Delete the routine “{name}”? Tasks it already "
                                                                  "queued stay.").format(name=r.name)), answered)

    def action_close(self) -> None:
        self.dismiss()
