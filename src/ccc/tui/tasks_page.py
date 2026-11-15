"""The Tasks & Routines view (t): every task still waiting in any conversation
and every routine, as two tables one under the other (#312). A finished task
isn't kept here (the page is what's still to do); a routine stays until you
delete it. One conversation's own queue is the window on T (ccc.tui.tasks).

Tab goes from one table to the other; the keys act on the table that has the
cursor: + add, e / Enter edit, Del delete — a task or a routine; Ctrl+↑/↓ move
a task in its queue; Space turns a routine on / off, r runs it now. The daemon
runs routines (ccc.routines.tick), this only edits."""
from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from ccc import keymap, routines
from ccc.i18n import _
from ccc.models import ItemStatus, QueueItem, Routine, RoutineTarget
from ccc.schedule import format_when
from ccc.storage import ArchivedConversationError, Storage
from ccc.tui import theme
from ccc.tui.layout import ConfirmModal, SidebarScreen

PREVIEW_CHARS = 80
REFRESH_SECONDS = 2.0


class TasksRoutinesScreen(SidebarScreen):
    VIEW = "tasks"
    SUB_TITLE = _("Tasks & Routines")

    BINDINGS = [
        keymap.binding("screen.back", "back", _("Back"), show=False),
        keymap.binding("tasks_page.add", "add", _("Add")),
        keymap.binding("tasks_page.edit", "edit", _("Edit")),
        keymap.binding("tasks_page.delete", "delete", _("Delete")),
        keymap.binding("tasks_page.toggle", "toggle", _("Routine on / off")),
        keymap.binding("tasks_page.run", "run_now", _("Run routine now")),
        keymap.binding("tasks_page.up", "move(-1)", _("Move up"), show=False),
        keymap.binding("tasks_page.down", "move(1)", _("Move down"), show=False),
    ]

    DEFAULT_CSS = """
    TasksRoutinesScreen #main {
        padding: 0;
    }
    TasksRoutinesScreen .tr-section {
        height: 1fr;
        border-bottom: solid $ccc-line;
        padding: 0 1;
    }
    TasksRoutinesScreen .tr-head {
        height: 2;
        padding: 1 0 0 0;
    }
    TasksRoutinesScreen .tr-title {
        width: 1fr;
        color: $ccc-text-muted;
        text-style: bold;
    }
    TasksRoutinesScreen .tr-section:focus-within .tr-title {
        color: $ccc-accent;
    }
    TasksRoutinesScreen .tr-right {
        width: auto;
        color: $ccc-text-muted;
    }
    TasksRoutinesScreen .tr-table {
        height: 1fr;
        background: transparent;
    }
    TasksRoutinesScreen .tr-table > .datatable--cursor {
        background: $ccc-selection;
        color: $ccc-accent;
        text-style: bold;
    }
    TasksRoutinesScreen .tr-foot {
        height: auto;
        color: $ccc-text-muted;
    }
    """

    def __init__(self, storage: Storage):
        super().__init__(storage)
        self._items: list[QueueItem] = []  # the tasks in the table, in its order
        self._routines: list[Routine] = []

    def compose_main(self):
        yield Vertical(
            Horizontal(Static(id="tr-tasks-title", classes="tr-title"),
                       Static(id="tr-tasks-right", classes="tr-right"), classes="tr-head"),
            DataTable(id="tr-tasks-table", classes="tr-table"),
            Static(id="tr-tasks-foot", classes="tr-foot"),
            id="tr-tasks-box", classes="tr-section",
        )
        yield Vertical(
            Horizontal(Static(id="tr-routines-title", classes="tr-title"),
                       Static(id="tr-routines-right", classes="tr-right"), classes="tr-head"),
            DataTable(id="tr-routines-table", classes="tr-table"),
            Static(id="tr-routines-foot", classes="tr-foot"),
            id="tr-routines-box", classes="tr-section",
        )

    def on_mount(self) -> None:
        tasks = self.query_one("#tr-tasks-table", DataTable)
        routines_table = self.query_one("#tr-routines-table", DataTable)
        for table in (tasks, routines_table):
            table.cursor_type = "row"
            table.zebra_stripes = False
        tasks.add_column(" ", key="mark")
        for title in (_("CONVERSATION"), _("TASK"), _("RUN AT"), _("STATE")):
            tasks.add_column(title)
        routines_table.add_column(" ", key="mark")
        for title in (_("ON"), _("ROUTINE"), _("CONVERSATION"), _("WHEN"), _("NEXT RUN"), _("LAST RUN")):
            routines_table.add_column(title)
        self.refresh_page()
        tasks.focus()
        self.set_interval(REFRESH_SECONDS, self.refresh_page)

    # --- the tables ------------------------------------------------------

    def refresh_page(self) -> None:
        self._refresh_tasks()
        self._refresh_routines()

    def _restore_cursor(self, table: DataTable, ids: list[int], keep: int | None, row: int | None) -> None:
        if not ids:
            return
        if keep in ids:
            table.move_cursor(row=ids.index(keep))
        elif row is not None:
            table.move_cursor(row=min(row, len(ids) - 1))
        self._mark_cursor(table, ids)

    @staticmethod
    def _mark_cursor(table: DataTable, ids: list[int]) -> None:
        """">" before the row with the cursor, nothing before the others."""
        for index, row_id in enumerate(ids):
            mark = Text(">", style="bold " + theme.ACCENT) if index == table.cursor_row else Text("")
            try:
                table.update_cell(str(row_id), "mark", mark)
            except Exception:  # the row went with a rebuild meanwhile
                pass

    def _refresh_tasks(self, select_item_id: int | None = None) -> None:
        table = self.query_one("#tr-tasks-table", DataTable)
        current = self._task_at_cursor()
        keep = select_item_id if select_item_id is not None else (current.id if current else None)
        row = table.cursor_row
        table.clear()
        now = datetime.now()
        queues = {q.id: q for q in self.storage.list_queues()}
        self._items = self.storage.list_open_items()
        for item in self._items:
            queue = queues.get(item.queue_id)
            lines = item.prompt_text.strip().splitlines() or [""]
            preview = lines[0][:PREVIEW_CHARS] + (" …" if len(lines) > 1 or len(lines[0]) > PREVIEW_CHARS else "")
            if item.routine_id is not None:
                preview = "↻ " + preview  # queued by a routine
            if item.status == ItemStatus.RUNNING:
                state = Text(_("RUNNING"), style="bold " + theme.ACCENT)
            elif queue is not None and queue.paused:
                state = Text(_("PAUSED"), style="bold " + theme.WARNING)
            else:
                state = Text(_("QUEUED"), style=theme.TEXT_MUTED)
            table.add_row(Text(""), queue.name if queue else "?", Text(preview),
                          format_when(item.not_before, now) or "—", state, key=str(item.id))
        if not self._items:
            table.add_row("", "", Text(_("nothing waiting — {key} adds a task").format(key=keymap.pretty(self._keys()["tasks_page.add"])), style="dim"), "", "")
        self._restore_cursor(table, [i.id for i in self._items], keep, row)
        self.query_one("#tr-tasks-title", Static).update(Text.assemble(
            (_("TASKS"), "bold"), "   ", (_("{n} waiting").format(n=len(self._items)), theme.TEXT_MUTED)))
        keys = self._keys()
        self.query_one("#tr-tasks-right", Static).update(Text(
            f"[{keymap.pretty(keys['conversations.tasks'])}] " + _("one conversation's queue")))
        self.query_one("#tr-tasks-foot", Static).update(Text.assemble(
            ("↑↓", "bold"), " ", _("move"), "     ", ("↵", "bold"), " ", _("edit"), "     ",
            (keymap.pretty(keys["tasks_page.up"]) + "/" + keymap.pretty(keys["tasks_page.down"]), "bold"), " ",
            _("reorder"), "     ", ("Tab", "bold"), " ", _("routines"), style=theme.TEXT_MUTED))

    def _refresh_routines(self, select_id: int | None = None) -> None:
        table = self.query_one("#tr-routines-table", DataTable)
        current = self._routine_at_cursor()
        keep = select_id if select_id is not None else (current.id if current else None)
        row = table.cursor_row
        table.clear()
        now = datetime.now()
        names = {q.id: q.name for q in self.storage.list_queues()}
        self._routines = self.storage.list_routines()
        for r in self._routines:
            fresh = " ✚" if r.target == RoutineTarget.FRESH else ""
            table.add_row(
                Text(""),
                Text("●", style="green") if r.enabled else Text("○", style="dim"),
                r.name + fresh, names.get(r.queue_id, "?"), routines.describe(r.schedule_kind, r.schedule_value),
                format_when(r.next_run_at, now) if r.enabled else "—",
                format_when(r.last_run_at, now) or "—", key=str(r.id),
            )
        if not self._routines:
            table.add_row("", "", Text(_("no routines yet — {key} adds one").format(key=keymap.pretty(self._keys()["tasks_page.add"])), style="dim"), "", "", "", "")
        self._restore_cursor(table, [r.id for r in self._routines], keep, row)
        self.query_one("#tr-routines-title", Static).update(Text.assemble(
            (_("ROUTINES"), "bold"), "   ",
            (_("{n} · {on} on").format(n=len(self._routines), on=sum(r.enabled for r in self._routines)),
             theme.TEXT_MUTED)))
        self.query_one("#tr-routines-right", Static).update(Text(_("the daemon runs them, ccc may be closed")))
        self._show_routine_detail()

    def _show_routine_detail(self) -> None:
        r = self._routine_at_cursor()
        foot = self.query_one("#tr-routines-foot", Static)
        if r is None:
            foot.update(Text(_("A routine queues its prompt on a schedule."), style=theme.TEXT_MUTED))
            return
        last = _(routines.STATUS_LABELS[r.last_status]) if r.last_status in routines.STATUS_LABELS else ""
        parts = [r.prompt_text.splitlines()[0][:120] if r.prompt_text.strip() else ""]
        if last:
            parts.append(_("last: {status}").format(status=last))
        if r.skipped:
            parts.append(_("skipped {n}×").format(n=r.skipped))
        active = self.storage.routine_items_active(r.id)
        if active:
            parts.append(_("{n} of its tasks waiting").format(n=active))
        keys = self._keys()
        foot.update(Text.assemble(
            " · ".join(p for p in parts if p), "\n",
            (keymap.pretty(keys["tasks_page.toggle"]), "bold"), " ", _("on / off"), "     ",
            (keymap.pretty(keys["tasks_page.run"]), "bold"), " ", _("run now"), "     ",
            ("↵", "bold"), " ", _("edit"), style=theme.TEXT_MUTED))

    # --- what the cursor is on -------------------------------------------

    def _task_at_cursor(self) -> QueueItem | None:
        row = self.query_one("#tr-tasks-table", DataTable).cursor_row
        return self._items[row] if row is not None and 0 <= row < len(self._items) else None

    def _routine_at_cursor(self) -> Routine | None:
        row = self.query_one("#tr-routines-table", DataTable).cursor_row
        return self._routines[row] if row is not None and 0 <= row < len(self._routines) else None

    def _in_routines(self) -> bool:
        focused = self.focused
        return focused is not None and focused.id == "tr-routines-table"

    def selected_queue_id(self) -> int | None:
        """The conversation the cursor is on (T opens its window; a new routine's default)."""
        if self._in_routines():
            routine = self._routine_at_cursor()
            if routine is not None:
                return routine.queue_id
        item = self._task_at_cursor()
        return item.queue_id if item is not None else None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "tr-tasks-table":
            self._mark_cursor(event.data_table, [i.id for i in self._items])
        elif event.data_table.id == "tr-routines-table":
            self._mark_cursor(event.data_table, [r.id for r in self._routines])
            self._show_routine_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        rows = self._routines if event.data_table.id == "tr-routines-table" else self._items
        self.action_edit() if rows else self.action_add()

    # --- actions ---------------------------------------------------------

    def action_add(self) -> None:
        if self._in_routines():
            from ccc.tui.routines import RoutineEditModal

            self.app.push_screen(RoutineEditModal(self.storage, queue_id=self.selected_queue_id()),
                                 lambda routine_id: self._refresh_routines(routine_id))
            return
        queue_id = self.selected_queue_id()
        if queue_id is None:
            active = [q for q in self.storage.list_queues() if not q.archived]
            if len(active) != 1:
                self.notify(_("Select a task of the conversation first (or add it from the conversation: {key}).")
                            .format(key=keymap.pretty(self._keys()["conversations.tasks"])), severity="warning")
                return
            queue_id = active[0].id
        from ccc.tui.tasks import TaskEdit, TaskEditModal

        def added(edit: TaskEdit | None) -> None:
            if edit is None:
                return
            try:
                self.storage.ensure_not_archived(queue_id)
            except ArchivedConversationError as exc:
                self.notify(str(exc), severity="error")
                return
            item = self.storage.add_queue_item(queue_id, edit.text, not_before=edit.not_before)
            self._refresh_tasks(select_item_id=item.id)

        self.app.push_screen(TaskEditModal(queue=self.storage.find_queue(queue_id)), added)

    def action_edit(self) -> None:
        if self._in_routines():
            from ccc.tui.routines import RoutineEditModal

            r = self._routine_at_cursor()
            if r is not None:
                self.app.push_screen(RoutineEditModal(self.storage, r), lambda _id: self._refresh_routines(r.id))
            return
        item = self._task_at_cursor()
        if item is None:
            return
        if item.status == ItemStatus.RUNNING:
            self.notify(_("This task is being handed to Claude right now."), severity="warning")
            return
        from ccc.tui.tasks import TaskEdit, TaskEditModal

        def edited(edit: TaskEdit | None) -> None:
            if edit is None:
                return
            self.storage.update_item_text(item.id, edit.text)
            self.storage.update_item_schedule(item.id, edit.not_before)
            self._refresh_tasks(select_item_id=item.id)

        self.app.push_screen(
            TaskEditModal(item.prompt_text, title=_("Edit task"), not_before=item.not_before,
                          queue=self.storage.find_queue(item.queue_id)), edited)

    def action_delete(self) -> None:
        if self._in_routines():
            r = self._routine_at_cursor()
            if r is None:
                return

            def answered(yes: bool) -> None:
                if yes:
                    self.storage.delete_routine(r.id)
                    self._refresh_routines()

            self.app.push_screen(ConfirmModal(_("Delete routine"), _("Delete the routine “{name}”? Tasks it already "
                                                                      "queued stay.").format(name=r.name)), answered)
            return
        item = self._task_at_cursor()
        if item is None:
            return
        if item.status == ItemStatus.RUNNING:
            self.notify(_("This task is being handed to Claude right now."), severity="warning")
            return
        self.storage.skip_item(item.id)
        self._refresh_tasks()

    def action_move(self, delta: int) -> None:
        item = self._task_at_cursor()
        if not self._in_routines() and item is not None and self.storage.move_item(item.id, delta):
            self._refresh_tasks(select_item_id=item.id)

    def action_toggle(self) -> None:
        r = self._routine_at_cursor() if self._in_routines() else None
        if r is None:
            self._routines_hint()
            return
        routines.set_enabled(self.storage, r.id, not r.enabled)
        self._refresh_routines(r.id)

    def action_run_now(self) -> None:
        r = self._routine_at_cursor() if self._in_routines() else None
        if r is None:
            self._routines_hint()
            return
        status = routines.fire(self.storage, r)
        self.notify(_(routines.STATUS_LABELS[status]), title=r.name,
                    severity="warning" if status in (routines.STATUS_SKIPPED, routines.STATUS_ARCHIVED) else "information")
        self.refresh_page()

    def _routines_hint(self) -> None:
        self.notify(_("Move to the routines (Tab) first."), severity="warning")
