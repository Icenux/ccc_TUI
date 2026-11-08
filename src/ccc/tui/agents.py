"""The Agents window (A anywhere): the Claude Code subagents of the
selected conversation's folder and of the user (ccc.agents). They are
helpers Claude calls on its own, each with one job; this window shows what
is there and why a broken file would be skipped."""
from __future__ import annotations

from rich.text import Text
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from ccc import agents, keymap
from ccc.i18n import N_, _
from ccc.tui.layout import MODAL_CSS

SCOPE_LABELS = {agents.SCOPE_PROJECT: N_("project"), agents.SCOPE_USER: N_("all projects")}
PROMPT_LINES = 4


def tools_label(agent: agents.Agent) -> str:
    return ", ".join(agent.tools) if agent.tools else _("all tools")


class AgentsModal(ModalScreen):
    DEFAULT_CSS = MODAL_CSS.format(cls="AgentsModal") + """
    AgentsModal > Container {
        width: 95%;
        max-width: 140;
        height: 85%;
    }
    AgentsModal #agents-table {
        height: 1fr;
        margin-bottom: 1;
    }
    AgentsModal #agents-table > .datatable--cursor {
        background: $ccc-selection;
        color: white;
        text-style: bold;
    }
    AgentsModal #agents-detail {
        height: auto;
        min-height: 6;
        color: $text-muted;
    }
    """

    BINDINGS = [
        keymap.binding("window.cancel", "close", _("Close")),
    ]

    def __init__(self, project_path: str = ""):
        super().__init__()
        self.project_path = project_path
        self._agents: list[agents.Agent] = []

    def compose(self):
        box = Container(
            DataTable(id="agents-table", cursor_type="row"),
            Static("", id="agents-detail"),
        )
        box.border_title = _("Agents — helpers Claude Code can call")
        yield box

    def on_mount(self) -> None:
        table = self.query_one("#agents-table", DataTable)
        table.add_columns(_("Agent"), _("Where"), _("Model"), _("What it is for"))
        self._refresh()
        table.focus()

    def _refresh(self) -> None:
        table = self.query_one("#agents-table", DataTable)
        self._agents = agents.list_agents(self.project_path)
        table.clear()
        for a in self._agents:
            name = Text(a.name, style="red" if a.problem else ("dim" if a.overridden else ""))
            if a.problem:
                name.append("  ⚠", style="red")
            table.add_row(name, _(SCOPE_LABELS[a.scope]), a.model or "—", a.description[:80])
        if not self._agents:
            table.add_row(Text(_("no agents yet"), style="dim"), "", "", "")
        self._show_detail()

    def selected(self) -> agents.Agent | None:
        row = self.query_one("#agents-table", DataTable).cursor_row
        return self._agents[row] if row is not None and row < len(self._agents) else None

    def _show_detail(self) -> None:
        a = self.selected()
        detail = self.query_one("#agents-detail", Static)
        if a is None:
            detail.update(_("An agent is a helper with one job. Claude Code calls it on its own when the task "
                            "fits its description; its files are in .claude/agents (this project) or "
                            "~/.claude/agents (all projects)."))
            return
        lines = [Text(agents.display_path(a), style="bold")]
        if a.problem:
            lines.append(Text(_("Claude Code skips this file: {problem}").format(problem=_(a.problem)), style="red"))
        elif a.overridden:
            lines.append(Text(_("An agent of the same name closer to the project wins; this one is not used."),
                              style="yellow"))
        lines.append(Text(_("Tools: {tools}").format(tools=tools_label(a))))
        prompt = [line for line in a.prompt.splitlines() if line.strip()][:PROMPT_LINES]
        if prompt:
            lines.append(Text("\n".join(line[:140] for line in prompt), style="dim"))
        detail.update(Text("\n").join(lines))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail()

    def action_close(self) -> None:
        self.dismiss()
