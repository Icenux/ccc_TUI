from __future__ import annotations

from datetime import date

from rich.text import Text
from textual import events
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from ccc import attention, dashboard_columns, gitops, keymap
from ccc.i18n import _
from ccc.models import QueueStatus
from ccc.storage import Storage
from ccc.tui.layout import (
    ConfirmModal,
    SidebarScreen,
    panel,
    queue_label,
    styled_status,
)


PATH_COLUMN_WIDTH = 48  # until the table's width is known
MIN_PATH_WIDTH = 14
CELL_PADDING = 2  # a DataTable column has a space on each side of its content
BRANCH_WIDTH = 20
# How often the git folders are looked at, in a thread, so the 2 s refresh
# of the table never waits for them.
GIT_STATUS_SECONDS = 15.0
# How long a row is held down for the Archive / Delete menu (like the
# long press in the phone app).
LONG_PRESS_SECONDS = 0.6

# Instead of an empty table, until the first conversation exists.
GETTING_STARTED = _("""\
[b]No conversations yet.[/b] Three steps to your first one:

  [b cyan]1.[/]  Press [b]n[/b] (or New conversation in Conversations), pick a project folder,
      tick the Claude Code options you want, Start.
  [b cyan]2.[/]  Claude Code opens beside ccc in Conversations — talk to it as in a terminal.
  [b cyan]3.[/]  Queue more work with [b]t[/b]. ccc carries on when you close it (Ctrl+Q)
      and waits out usage limits by itself.

Press [b]?[/b] for the guide — every screen, every key.""")


def _shorten_path(path: str, width: int = PATH_COLUMN_WIDTH) -> str:
    """Cut a long path from the front, so the folder Claude works in (the
    last one) always stays: …/projects/shop-api. Only a last folder that is
    itself too long loses its beginning."""
    if len(path) <= width:
        return path
    *parents, last = path.split("/")
    if len(last) + 1 >= width:
        return "…" + last[-(width - 1):]
    kept = last
    for part in reversed(parents):
        if len(kept) + len(part) + 3 > width:  # "…/" and the "/" before kept
            break
        kept = f"{part}/{kept}"
    return f"…/{kept}"


class ConversationMenu(ModalScreen):
    """What a long press on a Dashboard row offers: archive or delete.
    Dismisses "archive", "delete" or None."""

    DEFAULT_CSS = """
    ConversationMenu {
        align: center middle;
    }
    ConversationMenu > Vertical {
        width: 50;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    ConversationMenu Button {
        width: 100%;
        margin-top: 1;
    }
    """

    BINDINGS = [keymap.binding("window.cancel", "close", _("Close"))]

    def __init__(self, name: str):
        super().__init__()
        self.conversation_name = name

    def compose(self):
        box = Vertical(
            Button(_("Archive"), id="menu-archive"),
            Button(_("Delete"), id="menu-delete", classes="-danger"),
            Button(_("Cancel"), id="menu-cancel"),
        )
        box.border_title = self.conversation_name
        yield box

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss({"menu-archive": "archive", "menu-delete": "delete"}.get(event.button.id))

    def action_close(self) -> None:
        self.dismiss(None)


class DashboardScreen(SidebarScreen):
    VIEW = "dashboard"
    SUB_TITLE = _("Dashboard")

    BINDINGS = [
        # Only an overview: a blocked queue is retried in Conversations or
        # its queue detail (Enter). Ctrl+Q closes ccc everywhere.
        keymap.binding("dashboard.quit", "quit", _("Close"), show=False),
        # Also by a long press on a row (ConversationMenu).
        keymap.binding("dashboard.archive", "archive_conversation", _("Archive"), show=False),
        keymap.binding("dashboard.delete", "delete_conversation", _("Delete"), show=False),
    ]

    def __init__(self, storage: Storage):
        super().__init__(storage)

    def compose_main(self):
        yield panel(Static(id="dashboard-overview"), title=_("Overview"))
        yield panel(Static(GETTING_STARTED), title=_("Getting started"), id="dashboard-welcome")
        yield panel(DataTable(id="dashboard-table"), title=_("Queues"), fill=True)

    _columns: list[str] | None = None  # the columns the table has now

    def on_mount(self) -> None:
        table = self.query_one("#dashboard-table", DataTable)
        table.cursor_type = "row"
        self._git_status: dict[int, gitops.WorkStatus | None] = {}
        self.refresh_table()
        self.set_interval(2.0, self.refresh_table)
        self._refresh_git_status()
        self.set_interval(GIT_STATUS_SECONDS, self._refresh_git_status)

    def _refresh_git_status(self) -> None:
        """Only where Claude may run commands (ccc runs git only there, see
        gitops); the other conversations show a dash."""
        if dashboard_columns.GIT not in (self._columns or []):
            return
        queues = [q for q in self._visible_queues() if gitops.claude_can_run_commands(q)]

        def work() -> None:
            by_path: dict[str, gitops.WorkStatus | None] = {}
            result = {}
            for q in queues:
                if q.project_path not in by_path:
                    by_path[q.project_path] = gitops.work_status(q.project_path)
                result[q.id] = by_path[q.project_path]
            self.app.call_from_thread(self._set_git_status, result)

        self.run_worker(work, thread=True, group="dashboard-git", exclusive=True)

    def _set_git_status(self, result: dict) -> None:
        self._git_status = result

    def _set_columns(self, table: DataTable, columns: list[str]) -> None:
        """Rebuild the table's columns: Project, then the chosen ones."""
        table.clear(columns=True)
        table.add_column(_("Project"))
        titles = dict(dashboard_columns.COLUMNS)
        for column in columns:
            table.add_column(_(titles[column]))
        self._columns = columns
        # A column that just appeared has nothing to show until its worker ran.
        self._refresh_git_status()

    @staticmethod
    def _path_width(table: DataTable, columns: list[str], rows: list) -> int:
        """What is left of the table's width for the path, once the other
        columns have what they need (a path column is last)."""
        if dashboard_columns.PATH not in columns or not table.size.width:
            return PATH_COLUMN_WIDTH
        titles = dict(dashboard_columns.COLUMNS)
        used = max([Text(_("Project")).cell_len, *[row[0].cell_len for row in rows]]) + CELL_PADDING
        for column in columns:
            if column != dashboard_columns.PATH:
                used += max([len(_(titles[column])), *[row[1][column].cell_len for row in rows]]) + CELL_PADDING
        # The scrollbar's column and the path column's own padding.
        return max(MIN_PATH_WIDTH, table.size.width - used - CELL_PADDING - 2)

    def _git_cell(self, queue) -> Text:
        status = self._git_status.get(queue.id)
        if status is None:
            return Text("–", style="dim")
        cell = Text()
        if status.branch:
            branch = status.branch if len(status.branch) <= BRANCH_WIDTH else status.branch[:BRANCH_WIDTH - 1] + "…"
            cell.append(branch, style="cyan")
        if status.short():
            cell.append((" " if cell else "") + status.short(), style="yellow")
        return cell

    def _goal_cell(self, goal) -> Text:
        """Its latest goal loop: running (round / rounds) or how it ended in the last day; `–` otherwise."""
        from ccc import goals
        from ccc.tui.goal import STATUS_STYLES

        if not goals.shown(goal):
            return Text("–", style="dim")
        return Text(goals.short_text(goal), style=STATUS_STYLES[goal.status])

    def refresh_table(self) -> None:
        table = self.query_one("#dashboard-table", DataTable)
        columns = dashboard_columns.shown(self.storage)
        if columns != self._columns:
            self._set_columns(table, columns)
        # Keep the cursor where it was across the periodic rebuild.
        cursor_row = table.cursor_row
        table.clear()
        total_pending = total_tokens = blocked = running = 0
        queues = self._visible_queues()
        unseen = self.storage.unseen_queue_ids()
        waiting = self.storage.queues_waiting_for_approval()
        latest_goals = self.storage.latest_goals()
        rows = []
        for queue in queues:
            pending = self.storage.count_pending_items(queue.id)
            today_tokens = self.storage.queue_token_total(queue.id, day=date.today())
            total_pending += pending
            total_tokens += today_tokens
            blocked += queue.status == QueueStatus.BLOCKED_ERROR
            running += queue.status == QueueStatus.RUNNING
            rows.append((
                queue_label(queue, queue.id in unseen, needs_approval=queue.id in waiting),
                {
                    dashboard_columns.STATUS: Text.from_markup(styled_status(queue.status)),
                    dashboard_columns.GOAL: self._goal_cell(latest_goals.get(queue.id)),
                    dashboard_columns.PENDING: Text(str(pending)),
                    dashboard_columns.TOKENS: Text(f"{today_tokens:,}"),
                    dashboard_columns.GIT: self._git_cell(queue),
                },
                queue.project_path,
            ))
        path_width = self._path_width(table, columns, rows)
        for label, cells, path in rows:
            cells[dashboard_columns.PATH] = Text(_shorten_path(path, path_width), style="dim")
            table.add_row(label, *[cells[column] for column in columns])
        if queues and cursor_row is not None:
            table.move_cursor(row=min(cursor_row, len(queues) - 1))

        # Archived ones count too: by then you know your way around.
        self.query_one("#dashboard-welcome").display = not self.storage.list_queues()
        blocked_text = _("{n} blocked").format(n=blocked)
        running_text = _("{n} running").format(n=running)
        if blocked:
            blocked_text = f"[bold red]{blocked_text}[/]"
        if running:
            running_text = f"[bold green]{running_text}[/]"
        # What the Alt+N key and the Claude Code pane's title bar count too.
        needs_you = len(attention.waiting(self.storage))
        needs_text = _("{n} waiting for you").format(n=needs_you)
        if needs_you:
            needs_text = f"[bold yellow]{needs_text}[/]"
        self.query_one("#dashboard-overview", Static).update(
            _("[b]{queues}[/b] queues  ·  {running}  ·  [b]{pending}[/b] pending  ·  {blocked}  ·  "
              "{needs}  ·  [b]{tokens}[/b] tokens today").format(
                queues=len(queues), running=running_text, pending=total_pending,
                blocked=blocked_text, needs=needs_text, tokens=f"{total_tokens:,}",
            )
        )

    def _visible_queues(self):
        return [q for q in self.storage.list_queues() if not q.archived]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # DataTable owns the "enter" key itself (action select_cursor),
        # so listen for its message instead of a screen-level binding.
        from ccc.tui.queue_detail import QueueDetailScreen

        queues = self._visible_queues()
        if event.cursor_row >= len(queues):
            return
        queue = queues[event.cursor_row]
        self.app.push_screen(QueueDetailScreen(self.storage, queue.id))

    # --- archive / delete: keys and a long press on a row -----------------

    _press_timer = None
    _long_pressed = False

    def _row_of(self, event: events.MouseEvent) -> int | None:
        meta = event.style.meta if event.style else {}
        row = meta.get("row")
        return row if isinstance(row, int) and row >= 0 else None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._cancel_press()
        row = self._row_of(event)
        if row is None or event.button != 1:
            return
        self._press_timer = self.set_timer(LONG_PRESS_SECONDS, lambda: self._long_press(row))

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self._cancel_press()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        # Dragging is no long press.
        if self._press_timer is not None and self._row_of(event) is None:
            self._cancel_press()

    def _cancel_press(self) -> None:
        if self._press_timer is not None:
            self._press_timer.stop()
            self._press_timer = None

    def _long_press(self, row: int) -> None:
        self._press_timer = None
        queues = self._visible_queues()
        if row >= len(queues):
            return
        queue = queues[row]
        self.query_one("#dashboard-table", DataTable).move_cursor(row=row)

        def chosen(action: str | None) -> None:
            if action == "archive":
                self.action_archive_conversation()
            elif action == "delete":
                self.action_delete_conversation()

        self.app.push_screen(ConversationMenu(queue.name), chosen)

    def _cursor_queue(self):
        table = self.query_one("#dashboard-table", DataTable)
        queues = self._visible_queues()
        if not queues or table.cursor_row is None or table.cursor_row >= len(queues):
            return None
        return queues[table.cursor_row]

    def action_archive_conversation(self) -> None:
        queue = self._cursor_queue()
        if queue is None:
            return
        self.storage.set_queue_archived(queue.id, True)
        self.refresh_table()

    def action_delete_conversation(self) -> None:
        queue = self._cursor_queue()
        if queue is None:
            return
        if queue.status == QueueStatus.RUNNING:
            self.app.push_screen(
                ConfirmModal(
                    _("Can't delete"),
                    _("Claude is working on '{name}' right now — wait or pause it first (t, p).").format(name=queue.name),
                    can_confirm=False,
                )
            )
            return

        def answered(yes: bool) -> None:
            if yes:
                self.storage.delete_queue(queue.id)
                self.refresh_table()

        self.app.push_screen(
            ConfirmModal(
                _("Delete conversation"),
                _("Delete '{name}' with its queued tasks and run history from ccc?\n"
                  "Claude Code's own transcript stays, so it can still be resumed later.").format(name=queue.name),
            ),
            answered,
        )

    def action_view_conversations(self) -> None:
        self.action_goto("conversations")

    def action_view_analytics(self) -> None:
        self.action_goto("analytics")

    async def action_quit(self) -> None:
        await self.app.action_quit()
