"""The Agents window (A anywhere): the Claude Code subagents of the
selected conversation's folder and of the user (ccc.agents). They are
helpers Claude calls on its own, each with one job; this window shows what
is there and why a broken file would be skipped."""
from __future__ import annotations

from rich.text import Text
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select, Static, TextArea

from ccc import agent_templates, agent_wizard, agents, keymap
from ccc.i18n import N_, _
from ccc.tui.layout import MODAL_CSS

SCOPE_LABELS = {agents.SCOPE_PROJECT: N_("project"), agents.SCOPE_USER: N_("all projects")}
PROMPT_LINES = 4


def tools_label(agent: agents.Agent) -> str:
    return ", ".join(agent.tools) if agent.tools else _("all tools")


CUSTOM = "_custom"  # the first "Start from" entry: the user's own description


class AgentCreateModal(ModalScreen):
    """A new agent: from a template, or from the user's own description (three plain
    questions; the prompt is an outline or written by Claude). Shows what will be
    written. Dismisses with the new file's path, or None."""

    DEFAULT_CSS = MODAL_CSS.format(cls="AgentCreateModal") + """
    AgentCreateModal > Container {
        width: 95%;
        max-width: 120;
        max-height: 95%;
        overflow-y: auto;
    }
    AgentCreateModal Select {
        margin-bottom: 1;
    }
    AgentCreateModal #agent-about, AgentCreateModal #agent-status {
        color: $text-muted;
        margin-bottom: 1;
    }
    AgentCreateModal #agent-preview {
        height: 14;
    }
    AgentCreateModal #agent-prompt {
        height: 10;
    }
    AgentCreateModal #agent-custom {
        height: auto;
    }
    AgentCreateModal #agent-generate {
        margin-bottom: 1;
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
        self._outline = ""  # the prompt text while it is still the untouched outline
        self._generating = False

    def compose(self):
        scopes = ([(_("This project (.claude/agents)"), agents.SCOPE_PROJECT)] if self.has_project else []) + [
            (_("All projects (~/.claude/agents)"), agents.SCOPE_USER)]
        choices = [(_("Describe it myself…"), CUSTOM)] + [(_(t.title), t.key) for t in agent_templates.TEMPLATES]
        box = Container(
            Static(_("Start from"), classes="modal-label"),
            Select(choices, value=CUSTOM, allow_blank=False, id="agent-template"),
            Static("", id="agent-about"),
            Container(
                Static(_("What is it for? Say what it does and when Claude should use it."), classes="modal-label"),
                Input(placeholder=_("e.g. Checks that every new text has an English and a Czech version"),
                      id="agent-describe"),
                Static(_("May it change anything?"), classes="modal-label"),
                Select([(_(c.label), c.key) for c in agent_wizard.ACCESS], value=agent_wizard.DEFAULT_ACCESS,
                       allow_blank=False, id="agent-access"),
                Static(_("How thorough?"), classes="modal-label"),
                Select([(_(c.label), c.key) for c in agent_wizard.EFFORT], value=agent_wizard.DEFAULT_EFFORT,
                       allow_blank=False, id="agent-effort"),
                Static(_("Its instructions (the prompt)"), classes="modal-label"),
                TextArea("", id="agent-prompt"),
                Button(_("Write the instructions with Claude"), id="agent-generate"),
                Static("", id="agent-status"),
                id="agent-custom",
            ),
            Static(_("Name (letters, digits, hyphens)"), classes="modal-label"),
            Input("my-agent", id="agent-name"),
            Static(_("Kept in"), classes="modal-label"),
            Select(scopes, value=scopes[0][1], allow_blank=False, id="agent-scope"),
            Static(_("The file"), classes="modal-label", id="agent-preview-label"),
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
        self._sync_mode()
        self._refresh_outline()
        self._show()
        self.query_one("#agent-describe", Input).focus()

    # --- what is chosen -------------------------------------------------------------

    def _is_custom(self) -> bool:
        return self.query_one("#agent-template", Select).value == CUSTOM

    def _template(self) -> agent_templates.Template:
        return agent_templates.BY_KEY[self.query_one("#agent-template", Select).value]

    def _fields(self) -> tuple[str, tuple[str, ...], str, str]:
        """(description, tools, model, prompt) of the agent as it stands."""
        if not self._is_custom():
            return self._template().parts()
        access = agent_wizard.BY_ACCESS[self.query_one("#agent-access", Select).value]
        effort = agent_wizard.BY_EFFORT[self.query_one("#agent-effort", Select).value]
        return (self.query_one("#agent-describe", Input).value, access.tools, effort.model,
                self.query_one("#agent-prompt", TextArea).text)

    def _sync_mode(self) -> None:
        custom = self._is_custom()
        self.query_one("#agent-custom").display = custom
        self.query_one("#agent-preview").display = not custom
        self.query_one("#agent-preview-label").display = not custom
        self.query_one("#agent-about").display = not custom

    def _refresh_outline(self) -> None:
        """Keep the starting prompt in step with the description until the user writes their own."""
        if not self._is_custom():
            return
        prompt = self.query_one("#agent-prompt", TextArea)
        if prompt.text in ("", self._outline):
            self._outline = agent_wizard.outline_prompt(self.query_one("#agent-describe", Input).value,
                                                        self.query_one("#agent-access", Select).value)
            prompt.text = self._outline

    def _show(self) -> None:
        error = self.query_one("#agent-error", Static)
        try:
            description, tools, model, prompt = self._fields()
            text = agents.render(self.query_one("#agent-name", Input).value.strip(), description, tools, model, prompt)
            error.update("")
        except ValueError as problem:
            text = ""
            error.update(str(problem) if not self._is_custom() or self.query_one("#agent-describe", Input).value.strip()
                         else "")
        if not self._is_custom():
            t = self._template()
            self.query_one("#agent-about", Static).update(_("Tools: {tools} · model: {model}").format(
                tools=", ".join(t.tools), model=t.model or _("the session's")))
            self.query_one("#agent-preview", TextArea).text = text

    # --- events ---------------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "agent-template":
            # A name still equal to the previous suggestion follows the choice; one the user typed stays.
            name = self.query_one("#agent-name", Input)
            if name.value in agent_templates.BY_KEY or name.value == "my-agent":
                name.value = "my-agent" if event.value == CUSTOM else event.value
            self._sync_mode()
        self._refresh_outline()
        self._show()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "agent-describe":
            self._refresh_outline()
        if event.input.id in ("agent-name", "agent-describe"):
            self._show()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "agent-create":
            self.action_save()
        elif event.button.id == "agent-cancel":
            self.action_cancel()
        elif event.button.id == "agent-generate":
            self._generate()

    def _generate(self) -> None:
        """Ask Claude for the prompt; the answer replaces the text for the user to read and change."""
        if self._generating:
            return
        job = self.query_one("#agent-describe", Input).value
        tools = agent_wizard.BY_ACCESS[self.query_one("#agent-access", Select).value].tools
        status = self.query_one("#agent-status", Static)
        if not job.strip():
            status.update(Text(_("Describe what the agent is for first."), style="red"))
            return
        self._generating = True
        self.query_one("#agent-generate", Button).disabled = True
        status.update(_("Claude is writing the instructions…"))
        self.run_worker(self._generate_prompt(job, tools), exclusive=True)

    async def _generate_prompt(self, job: str, tools: tuple[str, ...]) -> None:
        try:
            text = await agent_wizard.generate_prompt(job, tools)
            message, style = _("Written by Claude — read it and change what you want."), ""
        except ValueError as problem:
            text, message, style = "", str(problem), "red"
        if not self.is_attached:
            return
        self._generating = False
        self.query_one("#agent-generate", Button).disabled = False
        if text:
            self.query_one("#agent-prompt", TextArea).text = text
            self._outline = ""  # no longer the outline: not replaced when the description changes
        self.query_one("#agent-status", Static).update(Text(message, style=style))

    def action_save(self) -> None:
        description, tools, model, prompt = self._fields()
        error = self.query_one("#agent-error", Static)
        if self._is_custom() and not prompt.strip():
            error.update(_("The agent needs instructions: write them, or let Claude write them."))
            return
        try:
            path, new_folder = agents.write_agent(
                self.query_one("#agent-name", Input).value.strip(), description, tools, model, prompt,
                self.query_one("#agent-scope", Select).value, self.project_path)
        except ValueError as problem:
            error.update(str(problem))
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
