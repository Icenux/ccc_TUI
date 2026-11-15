"""The window that adds or edits one routine (the Routines table is on the
Tasks & Routines page, ccc.tui.tasks_page). The daemon runs routines
(ccc.routines.tick)."""
from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.containers import Container, Horizontal
from textual.widgets import Button, Input, Select, Static, TextArea

from ccc import keymap, routines
from ccc.i18n import _
from ccc.models import Routine, RoutineTarget
from ccc.schedule import format_when
from ccc.tui.layout import CccModal, MODAL_CSS

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
