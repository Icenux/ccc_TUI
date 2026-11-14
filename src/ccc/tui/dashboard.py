from __future__ import annotations

import os
from datetime import date, datetime

from rich.text import Text
from textual import events
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Static
from textual.widgets.data_table import ColumnKey

from ccc import activity, attention, dashboard_columns, gitops, keymap
from ccc.i18n import _
from ccc.launch_options import describe_flags
from ccc.models import QueueStatus
from ccc.storage import Storage
from ccc.tui import theme
from ccc.tui.layout import (
    CccModal,
    ConfirmModal,
    SidebarScreen,
    panel,
    queue_label,
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


def _home(path: str) -> str:
    """~ for the home folder, as in the design (~/projects/shop-api)."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home not in ("", "/") and (path == home or path.startswith(home + "/")) else path


class ConversationMenu(CccModal):
    """What a long press on a Dashboard row offers: archive or delete.
    Dismisses "archive", "delete" or None."""

    DEFAULT_CSS = """
    ConversationMenu {
        align: center middle;
        background: $ccc-background 60%;
    }
    ConversationMenu > Vertical {
        width: 50;
        height: auto;
        border: solid $ccc-text;
        background: $ccc-panel;
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


# Status words as the Dashboard shows them (#250), and their colours.
STATUS_WORDS = {
    QueueStatus.RUNNING: (_("RUNNING"), "bold " + theme.ACCENT),
    QueueStatus.BLOCKED_ERROR: (_("BLOCKED"), "bold " + theme.ERROR),
    QueueStatus.WAITING_RETRY: (_("WAITING"), "bold " + theme.WARNING),
    QueueStatus.PAUSED_LIMIT: (_("LIMIT"), "bold " + theme.WARNING),
    QueueStatus.IDLE: (_("IDLE"), theme.TEXT_MUTED),
}
# The filter over the table (the row of chips; the filter key goes round them).
FILTERS = ("all", "running", "pending", "blocked")
# Activity kinds → colour.
ACTIVITY_STYLES = {
    activity.TASK: theme.ACCENT, activity.QUEUE: theme.ACCENT, activity.REVIEW: theme.ACCENT,
    activity.LIMIT: theme.WARNING, activity.APPROVE: theme.WARNING, activity.ERROR: theme.ERROR,
}
ACTIVITY_SECONDS = 5.0


def status_word(status: QueueStatus) -> Text:
    word, style = STATUS_WORDS.get(status, (status.value.upper(), ""))
    return Text(word, style=style)


def _ago(when: datetime) -> str:
    seconds = max(0, int((datetime.now() - when).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


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
        keymap.binding("dashboard.filter", "next_filter", _("Filter"), show=False),
    ]

    DEFAULT_CSS = """
    DashboardScreen #main {
        padding: 0;
    }
    DashboardScreen .dash-section {
        height: auto;
        border-bottom: solid $ccc-line;
        padding: 0 1;
    }
    DashboardScreen #dashboard-queues {
        height: auto;
        max-height: 60%;
    }
    DashboardScreen #dashboard-head, DashboardScreen #dashboard-filters, DashboardScreen .dash-head {
        height: 1;
    }
    DashboardScreen #dashboard-head {
        height: 3;
        padding: 1 0;
    }
    DashboardScreen #dashboard-title {
        width: 1fr;
    }
    DashboardScreen .dash-right {
        width: auto;
        color: $ccc-text-muted;
    }
    DashboardScreen #dashboard-filters {
        border-top: solid $ccc-line;
        height: 3;
        padding: 0 0 1 0;
    }
    DashboardScreen .dash-filter {
        width: auto;
        padding: 0 1;
        color: $ccc-text;
    }
    DashboardScreen .dash-filter:hover {
        background: $ccc-line;
    }
    DashboardScreen .dash-filter.-on {
        background: $ccc-accent;
        color: $ccc-background;
        text-style: bold;
    }
    DashboardScreen .dash-sep {
        width: auto;
        color: $ccc-line;
        padding: 0 1;
    }
    DashboardScreen #dashboard-tokens {
        width: auto;
        color: $ccc-text-muted;
        padding: 0 1;
    }
    DashboardScreen #dashboard-table {
        height: auto;
        max-height: 100%;
        background: transparent;
    }
    DashboardScreen #dashboard-table > .datatable--cursor {
        background: $ccc-selection;
        color: $ccc-accent;
        text-style: bold;
    }
    DashboardScreen #dashboard-table-foot {
        color: $ccc-text-muted;
        border-top: solid $ccc-line;
        height: 2;
    }
    DashboardScreen #dashboard-detail-box {
        background: $ccc-sidebar;
        padding: 1 1 0 1;
    }
    DashboardScreen .dash-row {
        height: auto;
    }
    DashboardScreen #dashboard-detail-left, DashboardScreen #dashboard-detail-stats {
        width: 1fr;
    }
    DashboardScreen #dashboard-activity-box {
        height: 1fr;
        border-bottom: none;
        padding: 1 1 0 1;
    }
    DashboardScreen #dashboard-activity-title {
        width: 1fr;
        color: $ccc-text-muted;
        text-style: bold;
    }
    DashboardScreen #dashboard-activity {
        height: 1fr;
        padding-top: 1;
    }
    DashboardScreen #dashboard-welcome {
        margin: 1 1;
    }
    """

    def __init__(self, storage: Storage):
        super().__init__(storage)
        self._filter = "all"
        self._shown = []  # the queues in the table, in its order
        self._commits: dict[int, gitops.Commit | None] = {}

    def compose_main(self):
        yield Vertical(
            Horizontal(Static(id="dashboard-title"), Static(id="dashboard-new", classes="dash-right"),
                       id="dashboard-head"),
            Horizontal(
                Static(_("all"), id="filter-all", classes="dash-filter"),
                Static(id="filter-running", classes="dash-filter"),
                Static("│", classes="dash-sep"),
                Static(id="filter-pending", classes="dash-filter"),
                Static("│", classes="dash-sep"),
                Static(id="filter-blocked", classes="dash-filter"),
                Static("│", classes="dash-sep"),
                Static(id="dashboard-tokens"),
                id="dashboard-filters",
            ),
            DataTable(id="dashboard-table"),
            Static(id="dashboard-table-foot"),
            id="dashboard-queues", classes="dash-section",
        )
        yield Vertical(
            Horizontal(Static(id="dashboard-detail-left"), Static(id="dashboard-detail-keys", classes="dash-right"),
                       classes="dash-row"),
            Horizontal(Static(id="dashboard-detail-stats"), Static(id="dashboard-detail-commit", classes="dash-right"),
                       classes="dash-row"),
            id="dashboard-detail-box", classes="dash-section",
        )
        yield panel(Static(GETTING_STARTED), title=_("Getting started"), id="dashboard-welcome")
        yield Vertical(
            Horizontal(Static(_("ACTIVITY"), id="dashboard-activity-title"),
                       Static(_("daemon log"), classes="dash-right"), classes="dash-head"),
            VerticalScroll(Static(id="dashboard-activity")),
            id="dashboard-activity-box", classes="dash-section",
        )

    _columns: list[str] | None = None  # the columns the table has now
    _with_path = True  # the path under each name (two-row rows)

    def on_mount(self) -> None:
        table = self.query_one("#dashboard-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = False
        self._git_status: dict[int, gitops.WorkStatus | None] = {}
        self.refresh_table()
        # Again once the table has its width: Project takes the rest of it.
        self.call_after_refresh(self.refresh_table)
        self.set_interval(2.0, self.refresh_table)
        self._refresh_git_status()
        self.set_interval(GIT_STATUS_SECONDS, self._refresh_git_status)
        self.refresh_activity()
        self.set_interval(ACTIVITY_SECONDS, self.refresh_activity)

    def _refresh_git_status(self) -> None:
        """Only where Claude may run commands (ccc runs git only there, see
        gitops); the other conversations show a dash. The selected one's
        last commit too (the detail under the table)."""
        queues = [q for q in self._visible_queues() if gitops.claude_can_run_commands(q)]
        selected = self._cursor_queue()
        want_git = dashboard_columns.GIT in (self._columns or [])

        def work() -> None:
            by_path: dict[str, gitops.WorkStatus | None] = {}
            result = {}
            for q in queues:
                if not want_git and (selected is None or q.id != selected.id):
                    continue
                if q.project_path not in by_path:
                    by_path[q.project_path] = gitops.work_status(q.project_path)
                result[q.id] = by_path[q.project_path]
            commits = {}
            if selected is not None and gitops.claude_can_run_commands(selected):
                commits[selected.id] = gitops.last_commit(selected.project_path)
            self.app.call_from_thread(self._set_git_status, result, commits)

        self.run_worker(work, thread=True, group="dashboard-git", exclusive=True)

    def _set_git_status(self, result: dict, commits: dict) -> None:
        self._git_status = result
        self._commits.update(commits)
        self._refresh_detail()

    def _set_columns(self, table: DataTable, columns: list[str]) -> None:
        """Rebuild the table's columns: the cursor's mark, Project, then the chosen ones."""
        table.clear(columns=True)
        table.add_column(" ", key="mark")
        table.add_column(_("PROJECT"), key="project")
        titles = dict(dashboard_columns.COLUMNS)
        for column in columns:
            if column != dashboard_columns.PATH:
                table.add_column(_(titles[column]).upper(), key=column)
        self._columns = columns
        self._with_path = dashboard_columns.PATH in columns
        # A column that just appeared has nothing to show until its worker ran.
        self._refresh_git_status()

    def _path_width(self, table: DataTable) -> int:
        """Room for the path under the name: what the table leaves for the Project column."""
        if not table.size.width:
            return PATH_COLUMN_WIDTH
        others = sum(col.get_render_width(table) for col in table.columns.values() if col.key.value != "project")
        return max(MIN_PATH_WIDTH, table.size.width - others - CELL_PADDING - 2)

    def _git_cell(self, queue) -> Text:
        status = self._git_status.get(queue.id)
        if status is None:
            return Text("–", style="dim")
        cell = Text()
        if status.branch:
            branch = status.branch if len(status.branch) <= BRANCH_WIDTH else status.branch[:BRANCH_WIDTH - 1] + "…"
            cell.append(branch)
        if status.short():
            cell.append((" " if cell else "") + status.short(), style=theme.WARNING)
        return cell

    def _goal_cell(self, goal) -> Text:
        """Its latest goal loop: running (round / rounds) or how it ended in the last day; `–` otherwise."""
        from ccc import goals
        from ccc.tui.goal import STATUS_STYLES

        if not goals.shown(goal):
            return Text("–", style="dim")
        return Text(goals.short_text(goal), style=STATUS_STYLES[goal.status])

    @staticmethod
    def _context_cell(queue) -> Text:
        if queue.context_percent is None:
            return Text("–", style="dim")
        percent = round(queue.context_percent)
        style = theme.ERROR if percent >= 90 else theme.WARNING if percent >= 80 else ""
        return Text(f"{percent}%", style=style)

    def _matches(self, queue, pending: int) -> bool:
        return (self._filter == "all"
                or self._filter == "running" and queue.status == QueueStatus.RUNNING
                or self._filter == "pending" and pending > 0
                or self._filter == "blocked" and queue.status == QueueStatus.BLOCKED_ERROR)

    def refresh_table(self) -> None:
        table = self.query_one("#dashboard-table", DataTable)
        columns = dashboard_columns.shown(self.storage)
        if columns != self._columns:
            self._set_columns(table, columns)
        # Keep the cursor on the same conversation across the periodic rebuild.
        current = self._cursor_queue()
        cursor_row = table.cursor_row
        table.clear()
        total_pending = total_tokens = blocked = running = pending_queues = 0
        queues = self._visible_queues()
        unseen = self.storage.unseen_queue_ids()
        waiting = self.storage.queues_waiting_for_approval()
        latest_goals = self.storage.latest_goals()
        shown = []
        rows = []
        for queue in queues:
            pending = self.storage.count_pending_items(queue.id)
            today_tokens = self.storage.queue_token_total(queue.id, day=date.today())
            total_pending += pending
            pending_queues += pending > 0
            total_tokens += today_tokens
            blocked += queue.status == QueueStatus.BLOCKED_ERROR
            running += queue.status == QueueStatus.RUNNING
            if not self._matches(queue, pending):
                continue
            shown.append(queue)
            rows.append((queue, queue_label(queue, queue.id in unseen, needs_approval=queue.id in waiting), {
                dashboard_columns.STATUS: status_word(queue.status),
                dashboard_columns.GOAL: self._goal_cell(latest_goals.get(queue.id)),
                dashboard_columns.PENDING: Text(str(pending), style=theme.ACCENT if pending else "dim"),
                dashboard_columns.CONTEXT: self._context_cell(queue),
                dashboard_columns.TOKENS: Text(f"{today_tokens:,}", style="" if today_tokens else "dim"),
                dashboard_columns.GIT: self._git_cell(queue),
            }))
        self._shown = shown
        path_width = self._path_width(table)
        # Project takes what the other columns leave (the table spans the panel).
        project_column = table.columns.get(ColumnKey("project"))
        if project_column is not None and table.size.width:
            project_column.auto_width = False
            project_column.width = path_width
        for queue, label, cells in rows:
            project = label
            if self._with_path:
                project = Text.assemble(label, "\n", (_shorten_path(_home(queue.project_path), path_width), "dim"))
            table.add_row(Text(""), project,
                          *[cells[c] for c in self._columns if c != dashboard_columns.PATH],
                          height=2 if self._with_path else 1, key=str(queue.id))
        if shown:
            ids = [q.id for q in shown]
            row = ids.index(current.id) if current is not None and current.id in ids else min(cursor_row or 0, len(shown) - 1)
            table.move_cursor(row=row)
            self._mark_cursor()

        # Archived ones count too: by then you know your way around.
        self.query_one("#dashboard-welcome").display = not self.storage.list_queues()
        self.query_one("#dashboard-detail-box").display = bool(shown)
        self.query_one("#dashboard-title", Static).update(Text.assemble(
            (_("DASHBOARD"), "bold " + theme.ACCENT), "   ",
            (_("{n} queues").format(n=len(queues)), theme.TEXT_MUTED)))
        self.query_one("#dashboard-new", Static).update(
            Text(f"[{keymap.pretty(self._keys()['views.new_conversation'])}] " + _("new conversation")))
        for name, label in (("running", _("running {n}").format(n=running)),
                            ("pending", _("pending {n}").format(n=total_pending)),
                            ("blocked", _("blocked {n}").format(n=blocked))):
            self.query_one(f"#filter-{name}", Static).update(label)
        for name in FILTERS:
            self.query_one(f"#filter-{name}").set_class(name == self._filter, "-on")
        # What the Alt+N key and the Claude Code pane's title bar count too.
        needs_you = len(attention.waiting(self.storage))
        tokens = Text(_("{tokens} tokens today").format(tokens=f"{total_tokens:,}"))
        if needs_you:
            tokens.append("   ")
            tokens.append(_("{n} waiting for you").format(n=needs_you), style="bold " + theme.WARNING)
        self.query_one("#dashboard-tokens", Static).update(tokens)
        keys = self._keys()
        self.query_one("#dashboard-table-foot", Static).update(Text.assemble(
            _("{shown} of {total}").format(shown=len(shown), total=len(queues)), "     ",
            ("↑↓", "bold"), " ", _("move"), "     ", ("↵", "bold"), " ", _("open"), "     ",
            (keymap.pretty(keys["dashboard.filter"]), "bold"), " ", _("filter"),
            style=theme.TEXT_MUTED))
        self._refresh_detail()

    def _mark_cursor(self) -> None:
        """">" before the selected row, nothing before the others."""
        table = self.query_one("#dashboard-table", DataTable)
        for index, queue in enumerate(self._shown):
            mark = Text(">", style="bold " + theme.ACCENT) if index == table.cursor_row else Text("")
            try:
                table.update_cell(str(queue.id), "mark", mark)
            except Exception:  # the row went with a rebuild meanwhile
                pass

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "dashboard-table":
            return
        self._mark_cursor()
        self._refresh_detail()
        queue = self._cursor_queue()
        if queue is not None and queue.id not in self._commits:
            self._refresh_git_status()

    def _refresh_detail(self) -> None:
        """The selected conversation under the table: folder, options,
        counters, its newest commit."""
        queue = self._cursor_queue()
        if queue is None:
            return
        queue = self.storage.find_queue(queue.id) or queue
        auto = _("auto-continue on") if queue.auto_continue else _("auto-continue off")
        self.query_one("#dashboard-detail-left", Static).update(Text.assemble(
            (">", "bold " + theme.ACCENT), " ", (queue.name, "bold " + theme.ACCENT), "  ",
            (f"{_home(queue.project_path)}  ·  {describe_flags(queue.launch_flags)}  ·  {auto}", theme.TEXT_MUTED)))
        keys = self._keys()
        self.query_one("#dashboard-detail-keys", Static).update(
            Text(f"[↵] {_('open')}   [{keymap.pretty(keys['views.tasks'])}] {_('tasks')}"))
        pending = self.storage.count_pending_items(queue.id)
        tokens = self.storage.queue_token_total(queue.id, day=date.today())
        stats = Text.assemble(
            (_("queued"), theme.TEXT_MUTED), f" {pending}     ",
            (_("context"), theme.TEXT_MUTED), " " + ("–" if queue.context_percent is None else f"{round(queue.context_percent)} %"),
            "     ", (_("tokens"), theme.TEXT_MUTED), f" {tokens:,}")
        status = self._git_status.get(queue.id)
        if status is not None and status.branch:
            stats.append(f"     {status.branch}")
            if status.short():
                stats.append(" " + status.short(), style=theme.WARNING)
        self.query_one("#dashboard-detail-stats", Static).update(stats)
        commit = self._commits.get(queue.id)
        self.query_one("#dashboard-detail-commit", Static).update(
            Text.assemble((commit.sha, theme.TEXT_MUTED), "  ", commit.subject[:40], "  ", (_ago(commit.when), "dim"))
            if commit is not None else "")

    def refresh_activity(self) -> None:
        def work():
            return activity.recent(self.storage)

        self.run_worker(work, thread=True, group="dashboard-activity", exclusive=True, name="dashboard-activity")

    def on_worker_state_changed(self, event) -> None:
        if event.worker.name == "dashboard-activity" and event.worker.state.name == "SUCCESS":
            events = event.worker.result or []
            width = max([len(e.project) for e in events] + [0])
            lines = Text()
            for item in events:
                lines.append(f"{item.when:%H:%M:%S}  ", style="dim")
                lines.append(f"{item.kind:<8}", style="bold " + ACTIVITY_STYLES.get(item.kind, ""))
                lines.append(f"{item.project:<{width}}  ")
                lines.append(item.message + "\n")
            self.query_one("#dashboard-activity", Static).update(lines if events else Text(_("Nothing yet."), style="dim"))

    def on_click(self, event) -> None:
        widget_id = getattr(event.widget, "id", None) or ""
        if widget_id.startswith("filter-"):
            self._set_filter(widget_id.removeprefix("filter-"))
            return
        if widget_id == "dashboard-new":
            self.action_new_conversation()
            return
        super().on_click(event)

    def _set_filter(self, name: str) -> None:
        if name in FILTERS:
            self._filter = name
            self.refresh_table()

    def action_next_filter(self) -> None:
        self._set_filter(FILTERS[(FILTERS.index(self._filter) + 1) % len(FILTERS)])

    def selected_queue_id(self) -> int | None:
        queue = self._cursor_queue()
        return queue.id if queue is not None else None

    def default_project_path(self) -> str:
        queue = self._cursor_queue()
        return queue.project_path if queue is not None else ""

    def _visible_queues(self):
        return [q for q in self.storage.list_queues() if not q.archived]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # DataTable owns the "enter" key itself (action select_cursor),
        # so listen for its message instead of a screen-level binding.
        from ccc.tui.queue_detail import QueueDetailScreen

        if event.cursor_row >= len(self._shown):
            return
        queue = self._shown[event.cursor_row]
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
        queues = self._shown
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
        queues = self._shown
        if not queues or table.cursor_row is None or table.cursor_row >= len(queues):
            return None
        return queues[table.cursor_row]

    def action_archive_conversation(self) -> None:
        queue = self._cursor_queue()
        if queue is None:
            return

        def answered(yes: bool) -> None:
            if yes:
                self.storage.set_queue_archived(queue.id, True)
                self.refresh_table()

        self.app.push_screen(
            ConfirmModal(
                _("Archive conversation"),
                _("Archive '{name}'? Its Claude Code is stopped and no task of it runs.\n"
                  "Restore it from the Archive (h) with x.").format(name=queue.name),
            ),
            answered,
        )

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
