"""The Subagents view of a conversation (#286): in the place of Claude Code, a tree of what its subagents did laid
over a timeline (runs that overlap ran at once), and under it the selected run — prompt, steps, result. Read from
Claude Code's transcripts by ccc.subagent_runs, redrawn while it runs (the screen calls `refresh_runs`)."""
from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import DataTable, Static

from ccc import subagent_runs
from ccc.i18n import _
from ccc.subagent_runs import Run

BAR_WIDTH = 28
STATUS_MARK = {subagent_runs.DONE: ("✓", "green"), subagent_runs.ERROR: ("✗", "red"),
               subagent_runs.STOPPED: ("✗", "red"), subagent_runs.RUNNING: ("●", "yellow")}
TABS = ("prompt", "steps", "result")


def duration(ms: int) -> str:
    seconds = max(int(ms / 1000), 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def tokens(count: int) -> str:
    return f"{count / 1000:.0f}k" if count >= 10000 else f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def tree_order(runs: list[Run], collapsed: set[str]) -> list[tuple[Run, str]]:
    """The runs depth first, each with the lines that draw its place in the tree ("├─ ", "│  └─ ")."""
    children: dict[str, list[Run]] = {}
    known = {r.id for r in runs}
    for run in runs:
        children.setdefault(run.parent_id if run.parent_id in known else "", []).append(run)
    out: list[tuple[Run, str]] = []

    def walk(parent: str, indent: str) -> None:
        siblings = children.get(parent, [])
        for number, run in enumerate(siblings):
            last = number == len(siblings) - 1
            out.append((run, indent + ("└─ " if last else "├─ ") if parent or indent else ""))
            if run.id not in collapsed:
                walk(run.id, indent + ("   " if last else "│  ") if parent or indent else "")

    walk("", "")
    return out


def timeline(run: Run, begin: datetime, span_ms: int, window_from_ms: int = 0, width: int = BAR_WIDTH) -> Text:
    """The run's bar on a line of `width` cells that covers span_ms from `begin` (+ window_from_ms)."""
    empty = Text("·" * width, style="grey37")
    if run.started is None or span_ms <= 0:
        return empty
    start = (run.started - begin).total_seconds() * 1000 - window_from_ms
    end = start + max(run.duration_ms, 1)
    if end < 0 or start > span_ms:
        return empty
    first = max(int(start / span_ms * width), 0)
    last = min(max(int(end / span_ms * width), first), width - 1)
    colour = {"done": "green", "error": "red", "stopped": "red", "running": "yellow"}.get(run.status, "green")
    bar = Text("·" * first, style="grey37")
    bar.append("█" * (last - first + 1), style=colour)
    bar.append("·" * (width - last - 1), style="grey37")
    return bar


class SubagentsPanel(Vertical):
    """Closed is posted by the screen's own Back (Esc) — see ConversationsScreen.action_back."""

    class Closed(Message):
        pass

    DEFAULT_CSS = """
    SubagentsPanel { width: 1fr; height: 1fr; border: round $ccc-frame; border-title-color: $ccc-title; }
    SubagentsPanel #sa-summary { height: auto; padding: 0 1; color: $text-muted; }
    SubagentsPanel #sa-table { height: 1fr; min-height: 4; }
    SubagentsPanel #sa-detail-head { height: auto; padding: 0 1; border-top: solid $ccc-frame; }
    SubagentsPanel #sa-tabs { height: 1; padding: 0 1; }
    SubagentsPanel #sa-detail { height: 1fr; min-height: 4; }
    SubagentsPanel #sa-steps { height: 1fr; min-height: 4; display: none; }
    SubagentsPanel.-steps #sa-steps { display: block; }
    SubagentsPanel.-steps #sa-detail { display: none; }
    SubagentsPanel #sa-empty { padding: 1 2; color: $text-muted; }
    """

    def __init__(self, session_id: str, name: str):
        super().__init__(id="subagents-panel")
        self.session_id = session_id
        self.conversation_name = name
        self.runs: list[Run] = []
        self.rows: list[Run] = []
        self.collapsed: set[str] = set()
        self.tab = "prompt"
        self.zoom = 1
        self.selected: str | None = None
        self._drawn: object = None

    def compose(self) -> ComposeResult:
        yield Static(id="sa-summary")
        yield Static(_("This conversation has not started any subagent."), id="sa-empty")
        yield DataTable(id="sa-table", cursor_type="row", zebra_stripes=False)
        yield Static(id="sa-detail-head")
        yield Static(id="sa-tabs")
        yield VerticalScroll(Static(id="sa-body"), id="sa-detail")
        yield DataTable(id="sa-steps", cursor_type="row")

    def on_mount(self) -> None:
        self.border_title = _("{name} · Subagents").format(name=self.conversation_name)
        table = self.query_one("#sa-table", DataTable)
        for label in (_("SUBAGENT"), _("TIMELINE"), "", _("TIME"), _("TOKENS")):
            table.add_column(label)
        steps = self.query_one("#sa-steps", DataTable)
        for label in ("#", _("AT"), _("TOOL"), _("ARGUMENT"), _("TOOK"), ""):
            steps.add_column(label)
        self.refresh_runs()
        table.focus()

    # --- data -------------------------------------------------------------------------------

    def refresh_runs(self) -> None:
        self.show_runs(subagent_runs.read_runs(self.session_id))

    def show_runs(self, runs: list[Run]) -> None:
        """Show these runs; redraw only when something changed."""
        self.runs = runs
        key = [(r.id, r.status, r.duration_ms, len(r.steps), r.tokens) for r in self.runs]
        if key != self._drawn:
            self._drawn = key
            self._draw()

    def _span(self) -> tuple[datetime | None, int]:
        starts = [r.started for r in self.runs if r.started]
        if not starts:
            return None, 0
        begin = min(starts)
        ends = [r.ended for r in self.runs if r.ended]
        finish = max(ends) if ends else begin
        return begin, max(int((finish - begin).total_seconds() * 1000), 1)

    def _draw(self) -> None:
        table = self.query_one("#sa-table", DataTable)
        self.query_one("#sa-empty").display = not self.runs
        table.display = bool(self.runs)
        running = sum(r.status == subagent_runs.RUNNING for r in self.runs)
        errors = sum(r.status in (subagent_runs.ERROR, subagent_runs.STOPPED) for r in self.runs)
        begin, span = self._span()
        self.query_one("#sa-summary", Static).update(Text(" · ".join([
            _("{n} runs").format(n=len(self.runs)), _("{n} running").format(n=running),
            _("{n} errors").format(n=errors), duration(span),
            _("{n} tokens").format(n=tokens(sum(r.tokens for r in self.runs if not r.parent_id))),
            *(["● " + _("live")] if running else [])])))
        keep = self.selected
        self.rows = [run for run, _prefix in tree_order(self.runs, self.collapsed)]
        prefixes = [prefix for _run, prefix in tree_order(self.runs, self.collapsed)]
        table.clear()
        window_from = span - span // self.zoom if self.zoom > 1 else 0
        window = span // self.zoom if self.zoom > 1 else span
        for run, prefix in zip(self.rows, prefixes):
            mark, colour = STATUS_MARK.get(run.status, ("?", "white"))
            folded = "▸ " if run.id in self.collapsed else ""
            table.add_row(
                Text.assemble((prefix, "grey50"), folded, run.description or run.kind),
                timeline(run, begin, window, window_from) if begin else "",
                Text(mark, style=colour), duration(run.duration_ms), tokens(run.tokens), key=run.id)
        if self.rows:
            index = next((i for i, r in enumerate(self.rows) if r.id == keep), 0)
            table.move_cursor(row=index)
            self.selected = self.rows[index].id
        self._draw_detail()

    def selected_run(self) -> Run | None:
        return next((r for r in self.runs if r.id == self.selected), None)

    def _draw_detail(self) -> None:
        run = self.selected_run()
        head = self.query_one("#sa-detail-head", Static)
        if run is None:
            head.update("")
            self.query_one("#sa-tabs", Static).update("")
            self.query_one("#sa-body", Static).update("")
            return
        mark, colour = STATUS_MARK.get(run.status, ("?", "white"))
        parent = next((r for r in self.runs if r.id == run.parent_id), None)
        head.update(Text.assemble(
            (run.description or run.kind, "bold"), "  ", (f"{mark} {_(run.status)}", colour), "\n",
            (_("by {who} · started {at} · took {took} · {model} · {tools} tools · {tokens} tokens").format(
                who=(parent.description or parent.kind) if parent else _("the conversation"),
                at=run.started.astimezone().strftime("%H:%M:%S") if run.started else "–", took=duration(run.duration_ms),
                model=run.model or "–", tools=run.tool_count, tokens=tokens(run.tokens)), "grey62")))
        self.query_one("#sa-tabs", Static).update(Text("   ").join(
            Text(label, style="bold reverse" if key == self.tab else "grey62")
            for key, label in (("prompt", _("Prompt")), ("steps", _("Steps")), ("result", _("Result")))))
        self.set_class(self.tab == "steps", "-steps")
        body = self.query_one("#sa-body", Static)
        if self.tab == "prompt":
            body.update(Text(run.prompt or "–"))
        elif self.tab == "result":
            body.update(Text(run.result or _("(no result yet)")))
        else:
            steps = self.query_one("#sa-steps", DataTable)
            steps.clear()
            for step in run.steps:
                steps.add_row(
                    str(step.index + 1), f"+{duration(step.offset_ms)}", Text(step.tool, style="yellow" if step.agent_id else ""),
                    Text(step.arg), "…" if step.duration_ms is None else duration(step.duration_ms),
                    Text(("→ " + step.agent_id[:8]) if step.agent_id else ("✗ " + step.error if step.error else ""),
                         style="red" if step.error else "yellow"), key=str(step.index))

    # --- keys -------------------------------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "sa-table" and event.row_key is not None and event.row_key.value != self.selected:
            self.selected = event.row_key.value
            self._draw_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a step that started a nested subagent: go to that run."""
        if event.data_table.id != "sa-steps":
            return
        run = self.selected_run()
        step = run.steps[int(event.row_key.value)] if run and event.row_key.value is not None else None
        if step and step.agent_id in {r.id for r in self.runs}:
            self.tab = "prompt"
            self.selected = step.agent_id
            table = self.query_one("#sa-table", DataTable)
            if step.agent_id in {r.id for r in self.rows}:
                table.move_cursor(row=[r.id for r in self.rows].index(step.agent_id))
            self._draw_detail()
            table.focus()

    # The view's own keys (arrows, Tab, + and −) are fixed; b and Esc are the screen's (Subagents, Back).
    KEYS = {"left": "action_fold", "right": "action_unfold", "tab": "action_next_tab", "plus": "action_zoom_in",
            "equals_sign": "action_zoom_in", "minus": "action_zoom_out"}

    def on_key(self, event) -> None:
        action = self.KEYS.get(event.key)
        if action is not None:
            event.stop()
            event.prevent_default()
            getattr(self, action)()

    def action_fold(self) -> None:
        if self.selected and any(r.parent_id == self.selected for r in self.runs):
            self.collapsed.add(self.selected)
            self._drawn = None
            self.refresh_runs()

    def action_unfold(self) -> None:
        if self.selected in self.collapsed:
            self.collapsed.discard(self.selected)
            self._drawn = None
            self.refresh_runs()

    def action_next_tab(self) -> None:
        self.tab = TABS[(TABS.index(self.tab) + 1) % len(TABS)]
        self._draw_detail()
        (self.query_one("#sa-steps") if self.tab == "steps" else self.query_one("#sa-table")).focus()

    def action_zoom_in(self) -> None:
        self.zoom = min(self.zoom * 2, 16)
        self._drawn = None
        self.refresh_runs()

    def action_zoom_out(self) -> None:
        self.zoom = max(self.zoom // 2, 1)
        self._drawn = None
        self.refresh_runs()
