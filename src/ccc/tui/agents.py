"""The Agents window (A anywhere): the Claude Code subagents of the
selected conversation's folder and of the user (ccc.agents). They are
helpers Claude calls on its own, each with one job; this window shows what
is there and why a broken file would be skipped."""
from __future__ import annotations

from rich.text import Text
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select, Static, TextArea

from ccc import agent_templates, agents, keymap
from ccc.i18n import N_, _
from ccc.tui.layout import MODAL_CSS

SCOPE_LABELS = {agents.SCOPE_PROJECT: N_("project"), agents.SCOPE_USER: N_("all projects")}
PROMPT_LINES = 4


def tools_label(agent: agents.Agent) -> str:
    return ", ".join(agent.tools) if agent.tools else _("all tools")


class AgentCreateModal(ModalScreen):
    """A new agent from a template: pick one, name it, say where it is kept —
    the file is shown as it will be written. Dismisses with the new file's
    path, or None."""

    DEFAULT_CSS = MODAL_CSS.format(cls="AgentCreateModal") + """
    AgentCreateModal > Container {
        width: 95%;
        max-width: 120;
    }
    AgentCreateModal Select {
        margin-bottom: 1;
    }
    AgentCreateModal #agent-about {
        color: $text-muted;
        margin-bottom: 1;
    }
    AgentCreateModal #agent-preview {
        height: 14;
    }
    AgentCreateModal .modal-hint {
        color: $text-muted;
        width: 1fr;
        padding-top: 1;
    }
    """

    BINDINGS = [
        keymap.binding("window.save", "save", _("Create"), priority=True),
        keymap.binding("window.cancel", "cancel", _("Cancel"), priority=True),
    ]

    def __init__(self, project_path: str = ""):
        super().__init__()
        self.project_path = project_path
        self.has_project = bool(project_path) and agents.folder_exists(project_path)

    def compose(self):
        scopes = ([(_("This project (.claude/agents)"), agents.SCOPE_PROJECT)] if self.has_project else []) + [
            (_("All projects (~/.claude/agents)"), agents.SCOPE_USER)]
        first = agent_templates.TEMPLATES[0]
        box = Container(
            Static(_("Start from"), classes="modal-label"),
            Select([(_(t.title), t.key) for t in agent_templates.TEMPLATES], value=first.key, allow_blank=False,
                   id="agent-template"),
            Static("", id="agent-about"),
            Static(_("Name (letters, digits, hyphens)"), classes="modal-label"),
            Input(first.key, id="agent-name"),
            Static(_("Kept in"), classes="modal-label"),
            Select(scopes, value=scopes[0][1], allow_blank=False, id="agent-scope"),
            Static(_("The file"), classes="modal-label"),
            TextArea("", read_only=True, id="agent-preview"),
            Static("", id="agent-error", classes="modal-error"),
            Horizontal(
                Static(_("Ctrl+S create  ·  Esc cancel"), classes="modal-hint"),
                Button(_("Cancel"), id="agent-cancel", classes="modal-cancel"),
                Button(_("Create"), id="agent-create", classes="modal-submit"),
                classes="modal-buttons",
            ),
        )
        box.border_title = _("New agent")
        yield box

    def on_mount(self) -> None:
        self._show()
        self.query_one("#agent-template", Select).focus()

    def _template(self) -> agent_templates.Template:
        return agent_templates.BY_KEY[self.query_one("#agent-template", Select).value]

    def _file_text(self) -> str:
        description, tools, model, prompt = self._template().parts()
        return agents.render(self.query_one("#agent-name", Input).value.strip(), description, tools, model, prompt)

    def _show(self) -> None:
        """The chosen template's tools and model, and the file as it will be written (or why it can't be)."""
        t = self._template()
        about = _("Tools: {tools} · model: {model}").format(
            tools=", ".join(t.tools), model=t.model or _("the session's"))
        self.query_one("#agent-about", Static).update(about)
        error = self.query_one("#agent-error", Static)
        try:
            self.query_one("#agent-preview", TextArea).text = self._file_text()
            error.update("")
        except ValueError as problem:
            error.update(str(problem))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "agent-template":
            # A name still equal to some template's key follows the choice; a name the user typed stays.
            name = self.query_one("#agent-name", Input)
            if name.value in agent_templates.BY_KEY:
                name.value = event.value
        self._show()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "agent-name":
            self._show()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "agent-create":
            self.action_save()
        elif event.button.id == "agent-cancel":
            self.action_cancel()

    def action_save(self) -> None:
        description, tools, model, prompt = self._template().parts()
        try:
            path, new_folder = agents.write_agent(
                self.query_one("#agent-name", Input).value.strip(), description, tools, model, prompt,
                self.query_one("#agent-scope", Select).value, self.project_path)
        except ValueError as problem:
            self.query_one("#agent-error", Static).update(str(problem))
            return
        if new_folder:
            self.app.notify(_("Claude Code only watches agent folders that existed when it started: restart it "
                              "where it is already running to load this agent."), severity="warning", timeout=12)
        self.dismiss(str(path))

    def action_cancel(self) -> None:
        self.dismiss(None)


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
    AgentsModal .agents-actions {
        height: 3;
    }
    AgentsModal .agents-actions Button {
        height: 3;
    }
    AgentsModal #agents-detail {
        height: auto;
        min-height: 6;
        color: $text-muted;
    }
    """

    BINDINGS = [
        keymap.binding("agents.add", "add", _("Add")),
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
            Horizontal(Button(_("Add from a template"), id="agents-add"), classes="agents-actions"),
        )
        box.border_title = _("Agents — helpers Claude Code can call")
        yield box

    def on_mount(self) -> None:
        for button in self.query(".agents-actions Button"):
            button.can_focus = False
        table = self.query_one("#agents-table", DataTable)
        table.add_columns(_("Agent"), _("Where"), _("Model"), _("What it is for"))
        self._refresh()
        table.focus()

    def _refresh(self, select_path: str | None = None) -> None:
        table = self.query_one("#agents-table", DataTable)
        self._agents = agents.list_agents(self.project_path)
        table.clear()
        for a in self._agents:
            name = Text(a.name, style="red" if a.problem else ("dim" if a.overridden else ""))
            if a.problem:
                name.append("  ⚠", style="red")
            table.add_row(name, _(SCOPE_LABELS[a.scope]), a.model or "—", a.description[:80])
        if not self._agents:
            table.add_row(Text(_("no agents yet — Add one"), style="dim"), "", "", "")
        for row, a in enumerate(self._agents):
            if select_path and str(a.path) == select_path:
                table.move_cursor(row=row)
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "agents-add":
            self.action_add()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail()

    def action_add(self) -> None:
        def created(path: str | None) -> None:
            if path:
                self._refresh(select_path=path)

        self.app.push_screen(AgentCreateModal(self.project_path), created)

    def action_close(self) -> None:
        self.dismiss()
