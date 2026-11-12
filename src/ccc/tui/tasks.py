"""Task queue editor for one conversation: the pending prompts it will
work through one after another, plus its pause / auto-continue switches."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rich.text import Text
from textual.containers import Container
from textual.content import Content
from textual.widgets import DataTable, Input, Static, TextArea

from ccc import keymap
from ccc.i18n import _
from ccc.limits import RESET_BUFFER
from ccc.schedule import format_when, parse_when
from ccc.storage import ArchivedConversationError, Storage
from ccc.usage_api import get_pro_usage
from ccc.tui.layout import CccModal, MODAL_CSS
from ccc.tui.mentions import MentionArea

PREVIEW_CHARS = 100


@dataclass
class TaskEdit:
    text: str
    not_before: datetime | None


def _next_five_hour_reset() -> datetime | None:
    usage = get_pro_usage()
    if usage is None or usage.five_hour is None:
        return None
    return usage.five_hour.resets_at.astimezone().replace(tzinfo=None) + RESET_BUFFER


class TaskEditModal(CccModal):
    """Multi-line editor for one task plus when it may start. Dismisses
    with a TaskEdit, or None when cancelled (or left empty)."""

    DEFAULT_CSS = MODAL_CSS.format(cls="TaskEditModal") + """
    TaskEditModal TextArea {
        height: 14;
    }
    TaskEditModal .modal-hint {
        color: $text-muted;
    }
    TaskEditModal #task-when {
        margin-bottom: 0;
    }
    TaskEditModal #task-error {
        color: red;
    }
    """

    BINDINGS = [
        # priority: TextArea would otherwise swallow these keys.
        keymap.binding("window.save", "save", _("Save"), priority=True),
        keymap.binding("window.cancel", "cancel", _("Cancel"), priority=True),
    ]

    def __init__(self, text: str = "", title: str | None = None, not_before: datetime | None = None,
                 queue=None):
        super().__init__()
        # Whose files @ offers in the text (tui.mentions); None: off.
        self.queue = queue
        self.initial_text = text
        self.box_title = title or _("New task")
        self.initial_when = not_before.strftime("%Y-%m-%d %H:%M") if not_before else ""

    def compose(self):
        box = Container(
            MentionArea(self.initial_text, id="task-text"),
            Static(_("Run at (empty = as soon as possible; HH:MM · YYYY-MM-DD HH:MM · reset)"), classes="modal-label"),
            Input(self.initial_when, placeholder=_("as soon as possible"), id="task-when"),
            Static("", id="task-error"),
            Static(_("Ctrl+S save  ·  Esc cancel"), classes="modal-hint"),
        )
        box.border_title = self.box_title
        yield box

    def on_mount(self) -> None:
        from ccc import gitops

        box = self.query_one("#task-text", MentionArea)
        if self.queue is not None:
            box.set_mention_source(self.queue.project_path, gitops.claude_can_run_commands(self.queue))
        box.focus()

    def action_save(self) -> None:
        text = self.query_one("#task-text", TextArea).text.strip()
        if not text:
            self.dismiss(None)
            return
        when_text = self.query_one("#task-when", Input).value
        reset_at = _next_five_hour_reset() if when_text.strip().lower() == "reset" else None
        try:
            not_before = parse_when(when_text, datetime.now(), reset_at=reset_at)
        except ValueError as exc:
            self.query_one("#task-error", Static).update(Text(str(exc)))
            return
        self.dismiss(TaskEdit(text, not_before))

    def action_cancel(self) -> None:
        # Esc is a priority key here: first it closes the @ file list.
        if self.query_one("#task-text", MentionArea).close_mentions():
            return
        self.dismiss(None)


class TasksModal(CccModal):
    DEFAULT_CSS = MODAL_CSS.format(cls="TasksModal") + """
    TasksModal > Container {
        width: 90%;
        max-width: 130;
        height: 80%;
    }
    TasksModal #tasks-state {
        margin-bottom: 1;
    }
    TasksModal #tasks-table {
        height: 1fr;
    }
    TasksModal #tasks-table > .datatable--cursor {
        background: $ccc-selection;
        color: white;
        text-style: bold;
    }
    TasksModal .modal-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        keymap.binding("tasks.add", "add", _("Add")),
        keymap.binding("tasks.edit", "edit", _("Edit")),
        keymap.binding("tasks.remove", "delete", _("Delete")),
        keymap.binding("tasks.up", "move(-1)", _("Move up")),
        keymap.binding("tasks.down", "move(1)", _("Move down")),
        keymap.binding("tasks.pause", "toggle_pause", _("Pause/resume")),
        keymap.binding("tasks.auto", "toggle_auto", _("Auto-continue")),
        keymap.binding("window.cancel", "close", _("Close")),
    ]

    def __init__(self, storage: Storage, queue_id: int):
        super().__init__()
        self.storage = storage
        self.queue_id = queue_id
        self._item_ids: list[int] = []

    def compose(self):
        queue = self.storage.get_queue(self.queue_id)
        box = Container(
            Static(id="tasks-state"),
            DataTable(id="tasks-table"),
            Static(
                _("a add  ·  e/Enter edit  ·  Del delete  ·  Ctrl+↑/↓ move  ·  p pause/resume  ·  "
                  "o auto-continue  ·  Esc close"),
                classes="modal-hint",
            ),
        )
        box.border_title = _("Tasks — {name}").format(name=queue.name)
        yield box

    def on_mount(self) -> None:
        table = self.query_one("#tasks-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("#", _("When"), _("Task"))
        self._refresh()
        table.focus()

    def _refresh(self, select_item_id: int | None = None) -> None:
        queue = self.storage.get_queue(self.queue_id)
        auto = _("on") if queue.auto_continue else _("off")
        state = _("paused") if queue.paused else _("running")
        self.query_one("#tasks-state", Static).update(
            Content.assemble(
                (_("Auto-continue: "), "bold"), (auto, "green" if queue.auto_continue else "yellow"), " (o)   ·   ",
                (_("Queue: "), "bold"), (state, "yellow" if queue.paused else "green"), " (p)",
            )
        )
        table = self.query_one("#tasks-table", DataTable)
        previous_row = table.cursor_row
        table.clear()
        items = self.storage.list_pending_items(self.queue_id)
        self._item_ids = [i.id for i in items]
        for n, item in enumerate(items, start=1):
            lines = item.prompt_text.strip().splitlines() or [""]
            preview = lines[0][:PREVIEW_CHARS] + (" …" if len(lines) > 1 or len(lines[0]) > PREVIEW_CHARS else "")
            if item.routine_id is not None:
                preview = "↻ " + preview  # queued by a routine
            table.add_row(str(n), format_when(item.not_before, datetime.now()) or "—", Text(preview))
        if not items:
            return
        if select_item_id in self._item_ids:
            table.move_cursor(row=self._item_ids.index(select_item_id))
        elif previous_row is not None:
            table.move_cursor(row=min(previous_row, len(items) - 1))

    def _selected_item_id(self) -> int | None:
        row = self.query_one("#tasks-table", DataTable).cursor_row
        if row is None or row >= len(self._item_ids):
            return None
        return self._item_ids[row]

    def action_add(self) -> None:
        def added(edit: TaskEdit | None) -> None:
            if edit:
                try:
                    self.storage.ensure_not_archived(self.queue_id)
                except ArchivedConversationError as exc:
                    self.notify(str(exc), severity="error")
                    return
                item = self.storage.add_queue_item(self.queue_id, edit.text, not_before=edit.not_before)
                self._refresh(select_item_id=item.id)

        self.app.push_screen(TaskEditModal(queue=self.storage.find_queue(self.queue_id)), added)

    def action_edit(self) -> None:
        item_id = self._selected_item_id()
        if item_id is None:
            return
        current = next(i for i in self.storage.list_pending_items(self.queue_id) if i.id == item_id)

        def edited(edit: TaskEdit | None) -> None:
            if edit:
                self.storage.update_item_text(item_id, edit.text)
                self.storage.update_item_schedule(item_id, edit.not_before)
                self._refresh(select_item_id=item_id)

        self.app.push_screen(
            TaskEditModal(current.prompt_text, title=_("Edit task"), not_before=current.not_before,
                          queue=self.storage.find_queue(self.queue_id)), edited
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_edit()

    def action_delete(self) -> None:
        item_id = self._selected_item_id()
        if item_id is not None:
            self.storage.skip_item(item_id)
            self._refresh()

    def action_move(self, delta: int) -> None:
        item_id = self._selected_item_id()
        if item_id is not None and self.storage.move_item(item_id, delta):
            self._refresh(select_item_id=item_id)

    def action_toggle_pause(self) -> None:
        queue = self.storage.get_queue(self.queue_id)
        self.storage.set_queue_paused(self.queue_id, not queue.paused)
        self._refresh()

    def action_toggle_auto(self) -> None:
        queue = self.storage.get_queue(self.queue_id)
        self.storage.set_queue_auto_continue(self.queue_id, not queue.auto_continue)
        self._refresh()

    def action_close(self) -> None:
        self.dismiss(None)
