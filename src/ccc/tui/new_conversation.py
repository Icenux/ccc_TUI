from __future__ import annotations

from pathlib import Path

from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DirectoryTree, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from ccc import agents, keymap, mcp_servers, plugins, profiles
from ccc.i18n import _
from ccc.launch_options import (
    AGENT_PREFIX, AGENT_SELECT, DEFAULT_SELECTION, FORK, GROUPS, MCP_PREFIX, MCP_SELECT, PLUGIN_PREFIX, PLUGINS_SELECT, SESSION_FROM_PR, SESSION_GROUP_KEYS, SESSION_NEW, SESSION_RESUME,
    group_of, is_agent, is_mcp_server, is_plugin, session_mode, toggle,
)
from ccc.orchestrator import start_conversation
from ccc.presets import LaunchChoice, list_presets, load_last, save_last, save_preset
from ccc.storage import Storage
from ccc.transcript import list_sessions_for_directory
from ccc.tui.layout import MODAL_CSS


class _DirectoriesOnly(DirectoryTree):
    def filter_paths(self, paths):
        return [p for p in paths if p.is_dir() and not p.name.startswith(".")]


def default_browse_root() -> Path:
    dev = Path.home() / "DEV"
    return dev if dev.is_dir() else Path.home()


class PresetNameModal(ModalScreen):
    """Asks for a preset name. Dismisses with the name, or None."""

    DEFAULT_CSS = MODAL_CSS.format(cls="PresetNameModal")
    BINDINGS = [keymap.binding("window.cancel", "cancel", _("Cancel"))]

    def compose(self):
        box = Container(
            Static(_("Preset name (an existing one is overwritten)"), classes="modal-label"),
            Input(placeholder=_("e.g. opus-deep"), id="preset-name"),
        )
        box.border_title = _("Save as preset")
        yield box

    def on_mount(self) -> None:
        self.query_one("#preset-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        self.dismiss(name or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# The option chips' colours (#250): by the option's group, as in the design.
CHIP_COLOURS = {"permissions": "#af5f5f", "model": "#005fd7", "effort": "#af8700"}
CHIP_OTHER = "#3a3a3a"


class NewConversationModal(ModalScreen):
    """The design's window (#250): a red title bar, the folder, the recent
    ones, the chosen options as chips, name / session / preset / ccc, Start.
    "+ option" opens every option (and the folder tree to browse)."""

    DEFAULT_CSS = MODAL_CSS.format(cls="NewConversationModal") + """
    NewConversationModal > Container {
        width: 90%;
        max-width: 110;
        height: 90%;
        padding: 0;
        border: solid $ccc-text;
    }
    NewConversationModal #nc-titlebar {
        height: 3;
        padding: 1 2;
        background: #af5f5f;
    }
    NewConversationModal #nc-title {
        width: 1fr;
        color: white;
        text-style: bold;
    }
    NewConversationModal #nc-esc {
        width: auto;
        color: white;
    }
    NewConversationModal #nc-body {
        height: 1fr;
        padding: 1 2 0 2;
    }
    NewConversationModal .nc-label-row {
        height: 1;
    }
    NewConversationModal .nc-label {
        width: 1fr;
        color: $ccc-text-muted;
        text-style: bold;
    }
    NewConversationModal .nc-hint {
        width: auto;
        color: $ccc-text-dim;
    }
    NewConversationModal #launch-path {
        margin-bottom: 1;
        border: tall $ccc-accent;
    }
    NewConversationModal #recent-dirs {
        height: 6;
        margin-bottom: 1;
        border: solid $ccc-line;
        background: $ccc-background;
    }
    NewConversationModal #nc-chips {
        height: auto;
        margin-bottom: 1;
    }
    NewConversationModal .nc-chip {
        width: auto;
        height: 1;
        padding: 0 1;
        margin-right: 1;
        color: white;
        text-style: bold;
    }
    NewConversationModal .nc-chip:hover {
        background-tint: white 15%;
    }
    NewConversationModal #nc-more {
        display: none;
        height: auto;
        border: solid $ccc-line;
        padding: 0 1;
        margin-bottom: 1;
    }
    NewConversationModal.-more #nc-more {
        display: block;
    }
    NewConversationModal #nc-more-columns {
        height: auto;
    }
    NewConversationModal #nc-browse {
        width: 1fr;
        margin-right: 2;
    }
    NewConversationModal #new-conv-right {
        width: 1fr;
        height: auto;
    }
    NewConversationModal #dir-tree {
        height: 16;
        margin-bottom: 1;
    }
    NewConversationModal #nc-grid {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 1fr;
        /* a label and a field; ccc's three switches in theirs */
        grid-rows: 4 6;
        grid-gutter: 1 2;
        height: 11;
    }
    NewConversationModal .nc-cell {
        height: 100%;
    }
    NewConversationModal #launch-name {
        margin: 0;
    }
    NewConversationModal #nc-ccc {
        height: auto;
        border: tall $ccc-line;
        background: $ccc-background;
        padding: 0 1;
    }
    NewConversationModal #resume-sessions {
        height: 8;
        margin-left: 4;
    }
    NewConversationModal #from-pr-input {
        margin-left: 4;
    }
    NewConversationModal #mcp-servers {
        height: auto;
        margin-left: 4;
    }
    NewConversationModal #plugin-list {
        height: auto;
        margin-left: 4;
    }
    NewConversationModal #agent-list {
        height: auto;
        margin-left: 4;
    }
    NewConversationModal #preset-row {
        height: 3;
    }
    NewConversationModal #preset-select {
        width: 1fr;
    }
    NewConversationModal #preset-save {
        margin-left: 1;
        min-width: 0;
        width: auto;
    }
    NewConversationModal .group-title {
        text-style: bold;
        color: $ccc-text-muted;
        margin-top: 1;
    }
    NewConversationModal #nc-buttons {
        height: 3;
        align-horizontal: right;
        padding: 0 2;
        margin-bottom: 1;
    }
    NewConversationModal #nc-buttons Button {
        margin-left: 1;
    }
    NewConversationModal #launch-cancel {
        min-width: 14;
    }
    NewConversationModal #launch-start {
        min-width: 24;
    }
    NewConversationModal #launch-error {
        padding: 0 2;
    }
    NewConversationModal #nc-keys {
        height: 1;
        padding: 0 2;
        border-top: solid $ccc-line;
        height: 2;
    }
    NewConversationModal #nc-keys-left {
        width: 1fr;
        color: $ccc-text-muted;
    }
    NewConversationModal #nc-keys-right {
        width: auto;
        color: $ccc-text-muted;
    }
    NewConversationModal Checkbox {
        border: none;
        padding: 0;
        height: 1;
        background: transparent;
    }
    /* Textual's ToggleButton:focus adds a tall border; on these 1-row
       checkboxes it covers the whole row — a grey box, the label gone.
       Keep them borderless and mark focus on the label instead. */
    NewConversationModal Checkbox:focus {
        border: none;
        background-tint: transparent;
    }
    NewConversationModal Checkbox:focus > .toggle--label {
        color: $text;
        background: $boost;
        text-style: underline;
    }
    NewConversationModal Checkbox > .toggle--button {
        color: $surface-lighten-2;
        background: $surface-lighten-1;
    }
    NewConversationModal Checkbox.-on > .toggle--button {
        color: black;
        background: $ccc-accent;
        text-style: bold;
    }
    NewConversationModal Checkbox.-on > .toggle--label {
        text-style: bold;
    }
    NewConversationModal Checkbox:disabled > .toggle--label {
        color: $text-disabled;
    }
    """

    BINDINGS = [
        keymap.binding("window.cancel", "cancel", _("Cancel")),
        keymap.binding("window.save", "save_preset", _("Save as preset"), priority=True),
    ]

    def __init__(self, storage: Storage, browse_root: Path | None = None):
        super().__init__()
        self.storage = storage
        self.browse_root = browse_root or default_browse_root()
        # Prefill with what was used last time (session group always "new").
        last = load_last(storage) or LaunchChoice(DEFAULT_SELECTION)
        self.selection = last.selection
        self._initial = last
        self._sessions_for: str | None = None
        self._session_ids: list[str] = []
        self._mcp_shown: list[str] | None = None  # server names currently listed
        self._mcp_generation = 0  # keeps widget ids unique across rebuilds
        self._plugins_shown: list[str] | None = None  # plugin ids currently listed
        self._plugin_generation = 0
        self._agents_shown: list[str] | None = None  # agent names currently listed
        self._agent_generation = 0
        self._chips_shown: list[tuple[str, str]] | None = None
        self._chip_generation = 0

    def _preset_options(self) -> list[tuple[str, str]]:
        return [(name, name) for name in sorted(list_presets(self.storage))]

    @staticmethod
    def _label_row(label: str, hint: str = "") -> Horizontal:
        return Horizontal(Static(label.upper(), classes="nc-label"), Static(hint, classes="nc-hint"),
                          classes="nc-label-row")

    def compose(self):
        options = []
        for group in GROUPS:
            if group.title:
                options.append(Static(_(group.title), classes="group-title"))
            for option in group.options:
                options.append(Checkbox(_(option.label), value=option.key in self.selection, id=f"opt-{option.key}"))
                if option.key == MCP_SELECT:
                    options.append(Vertical(id="mcp-servers"))
                elif option.key == PLUGINS_SELECT:
                    options.append(Vertical(id="plugin-list"))
                elif option.key == AGENT_SELECT:
                    options.append(Vertical(id="agent-list"))
                elif option.key == SESSION_RESUME:
                    options.append(OptionList(id="resume-sessions"))
                elif option.key == SESSION_FROM_PR:
                    options.append(Input(placeholder=_("PR number or URL"), id="from-pr-input"))
        # Only when accounts were added (`ccc profile add`): nobody else sees a change.
        if profiles.list_profiles():
            options.append(Static(_("Claude account"), classes="group-title"))
            options.append(Select(
                [(_("(default)"), "")] + [(f"{p.name} (API)" if p.is_command else p.name, p.name) for p in profiles.list_profiles()],
                allow_blank=False, value="", id="profile-select",
            ))
            if any(p.is_command for p in profiles.list_profiles()):
                options.append(Static(_(profiles.API_LIMITS_NOTICE), classes="hint"))
        recent = [Option(Content(p), id=p) for p in self.storage.recent_project_paths()]
        session_button = Button("", id="nc-session", classes="nc-field")
        session_button.can_focus = True
        keys = keymap.current(self.storage)
        box = Container(
            Horizontal(Static(_("New conversation").upper(), id="nc-title"),
                       Static(f"{keymap.pretty(keys['window.cancel']).lower()} {_('cancel')}", id="nc-esc"),
                       id="nc-titlebar"),
            VerticalScroll(
                self._label_row(_("Directory"), _("required")),
                Input(placeholder=_("/path/to/project"), id="launch-path"),
                self._label_row(_("Recent directories"), "↑↓ " + _("choose")),
                OptionList(*recent, id="recent-dirs"),
                self._label_row(_("Options"), _("space toggle")),
                Horizontal(Button("+ " + _("option"), id="nc-more-button", classes="nc-small"), id="nc-chips"),
                Vertical(
                    Horizontal(
                        Vertical(
                            Static(_("Browse"), classes="group-title"),
                            _DirectoriesOnly(str(self.browse_root), id="dir-tree"),
                            id="nc-browse",
                        ),
                        Vertical(*options, id="new-conv-right"),
                        id="nc-more-columns",
                    ),
                    id="nc-more",
                ),
                Container(
                    Vertical(self._label_row(_("Name (optional)")),
                             Input(placeholder=_("defaults to the directory name"), id="launch-name"), classes="nc-cell"),
                    Vertical(self._label_row(_("Session")), session_button, classes="nc-cell"),
                    Vertical(self._label_row(_("Preset")),
                             Horizontal(Select(self._preset_options(), prompt=_("Choose a preset…"), id="preset-select"),
                                        Button(_("Save…"), id="preset-save"), id="preset-row"),
                             classes="nc-cell"),
                    Vertical(self._label_row("ccc", _("space")),
                             Vertical(
                                 # Not `claude` flags: ccc's own behaviour.
                                 Checkbox(_("Auto-continue after usage limit"), value=self._initial.auto_continue,
                                          id="auto-continue"),
                                 Checkbox(_("Remote control (phone / claude.ai)"), value=self._initial.remote_control,
                                          id="remote-control"),
                                 Checkbox(_("Own git worktree (to work in parallel)"),
                                          value=False, id="own-worktree"),
                                 id="nc-ccc"),
                             classes="nc-cell"),
                    id="nc-grid",
                ),
                id="nc-body",
            ),
            Static("", id="launch-error", classes="modal-error"),
            Horizontal(
                Button(_("Cancel"), id="launch-cancel", classes="modal-cancel"),
                Button(_("Start") + "  ↵", id="launch-start", classes="modal-submit"),
                id="nc-buttons",
            ),
            Horizontal(
                Static(Content.assemble(
                    ("tab", "bold"), " ", _("next"), "   ", ("shift+tab", "bold"), " ", _("prev"), "   ",
                    ("space", "bold"), " ", _("toggle"), "   ", ("↵", "bold"), " ", _("start")), id="nc-keys-left"),
                Static(_("Save as preset: {key}").format(key=keymap.pretty(keys["window.save"])), id="nc-keys-right"),
                id="nc-keys",
            ),
        )
        yield box

    # --- the chips and "+ option" ---------------------------------------

    def _chips(self) -> list[tuple[str, str]]:
        """(option key, label) of every chosen option worth a chip: not the
        session (it has its own field), the default "new session" least of all."""
        chips = []
        for group in GROUPS:
            if group.key in SESSION_GROUP_KEYS:
                continue
            for option in group.options:
                if option.key in self.selection:
                    chips.append((option.key, _(option.short)))
        return chips

    def _refresh_chips(self) -> None:
        chips = self._chips()
        if chips == self._chips_shown:
            return
        self._chips_shown = chips
        self._chip_generation += 1
        row = self.query_one("#nc-chips", Horizontal)
        for chip in list(row.query(".nc-chip")):
            chip.remove()
        more = self.query_one("#nc-more-button", Button)
        for i, (key, label) in enumerate(chips):
            chip = Static(Content(f"{label}  ×"), id=f"chip-{self._chip_generation}-{i}", classes="nc-chip")
            chip.option_key = key
            chip.styles.background = CHIP_COLOURS.get(group_of(key).key, CHIP_OTHER)
            row.mount(chip, before=more)

    def _refresh_session_field(self) -> None:
        mode = session_mode(self.selection)
        label = next((_(o.label) for g in GROUPS for o in g.options if o.key == mode), mode)
        if FORK in self.selection and mode != SESSION_NEW:
            label += "  ·  " + _("fork")
        self.query_one("#nc-session", Button).label = Content.assemble(label, "   ", ("[" + _("change") + "]", "dim"))

    def on_click(self, event) -> None:
        widget = event.widget
        key = getattr(widget, "option_key", None)
        if key is not None:
            event.stop()
            # After the click: dropping the option removes this very chip.
            self.call_after_refresh(self._set_option, key, False)

    def _set_option(self, key: str, value: bool) -> None:
        checkbox = self.query(f"#opt-{key}")
        if checkbox:
            checkbox.first(Checkbox).value = value

    def action_toggle_more(self, show: bool | None = None) -> None:
        self.set_class(not self.has_class("-more") if show is None else show, "-more")

    def action_save_preset(self) -> None:
        self.app.push_screen(PresetNameModal(), self._save_preset)

    def on_mount(self) -> None:
        self._sync_session_widgets()
        self._refresh_mcp()
        self._refresh_plugins()
        self._refresh_agents()
        self.query_one("#launch-path", Input).focus()

    # --- directory ------------------------------------------------------

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "recent-dirs":
            self.query_one("#launch-path", Input).value = event.option.id

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.query_one("#launch-path", Input).value = str(event.path)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "launch-path":
            self._sync_session_widgets()
            self._refresh_mcp()
            self._refresh_agents()

    def _path(self) -> str:
        return str(Path(self.query_one("#launch-path", Input).value.strip()).expanduser())

    # --- attributes -----------------------------------------------------

    def _chosen_mcp(self) -> list[str]:
        return sorted(k.removeprefix(MCP_PREFIX) for k in self.selection if is_mcp_server(k))

    def _refresh_mcp(self) -> None:
        """List the MCP servers configured for the chosen directory (plus
        ticked ones that are gone) under "Only selected MCP servers"."""
        box = self.query_one("#mcp-servers", Vertical)
        box.display = MCP_SELECT in self.selection
        path = self._path() if self.query_one("#launch-path", Input).value.strip() else ""
        found = mcp_servers.names(path if Path(path).is_dir() else "")
        names = sorted(set(found) | set(self._chosen_mcp()))
        if names != self._mcp_shown:
            self._mcp_shown = names
            self._mcp_generation += 1
            box.remove_children()
            for i, name in enumerate(names):
                label = name if name in found else _("{name} (not found here)").format(name=name)
                checkbox = Checkbox(Content(label), id=f"mcp-srv-{self._mcp_generation}-{i}")
                checkbox.mcp_name = name
                box.mount(checkbox)
        for checkbox in box.query(Checkbox):
            wanted = f"{MCP_PREFIX}{checkbox.mcp_name}" in self.selection
            if checkbox.value != wanted:
                checkbox.value = wanted

    def _chosen_plugins(self) -> list[str]:
        return sorted(k.removeprefix(PLUGIN_PREFIX) for k in self.selection if is_plugin(k))

    def _refresh_plugins(self) -> None:
        """List the installed plugins (plus ticked ones that are gone) under
        "Only selected plugins"."""
        box = self.query_one("#plugin-list", Vertical)
        box.display = PLUGINS_SELECT in self.selection
        found = plugins.installed()
        ids = sorted(set(found) | set(self._chosen_plugins()))
        if ids != self._plugins_shown:
            self._plugins_shown = ids
            self._plugin_generation += 1
            box.remove_children()
            for i, pid in enumerate(ids):
                label = pid if pid in found else _("{name} (not installed)").format(name=pid)
                checkbox = Checkbox(Content(label), id=f"plugin-{self._plugin_generation}-{i}")
                checkbox.plugin_id = pid
                box.mount(checkbox)
        for checkbox in box.query(Checkbox):
            wanted = f"{PLUGIN_PREFIX}{checkbox.plugin_id}" in self.selection
            if checkbox.value != wanted:
                checkbox.value = wanted

    def _chosen_agents(self) -> list[str]:
        return sorted(k.removeprefix(AGENT_PREFIX) for k in self.selection if is_agent(k))

    def _refresh_agents(self) -> None:
        """List the agents Claude Code finds for the chosen directory (the
        project's and the user's) under "Whole conversation as one agent"; a
        ticked one that is gone stays, marked."""
        box = self.query_one("#agent-list", Vertical)
        box.display = AGENT_SELECT in self.selection
        path = self._path() if self.query_one("#launch-path", Input).value.strip() else ""
        found = sorted({a.name for a in agents.list_agents(path if Path(path).is_dir() else "")
                        if not a.problem and not a.overridden and is_agent(AGENT_PREFIX + a.name)})
        names = sorted(set(found) | set(self._chosen_agents()))
        if names != self._agents_shown:
            self._agents_shown = names
            self._agent_generation += 1
            box.remove_children()
            for i, name in enumerate(names):
                label = name if name in found else _("{name} (not found here)").format(name=name)
                checkbox = Checkbox(Content(label), id=f"agent-{self._agent_generation}-{i}")
                checkbox.agent_name = name
                box.mount(checkbox)
            box.mount(Static(_("Its prompt replaces Claude Code's own instructions.") if names else
                             _("No agents here. Press A for Agents; they are files in .claude/agents."),
                             classes="hint"))
        for checkbox in box.query(Checkbox):
            wanted = f"{AGENT_PREFIX}{checkbox.agent_name}" in self.selection
            if checkbox.value != wanted:
                checkbox.value = wanted

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        agent = getattr(event.checkbox, "agent_name", None)
        if agent is not None:
            key = f"{AGENT_PREFIX}{agent}"
            if event.value != (key in self.selection):
                self.selection = toggle(self.selection, key, event.value)
                self.query_one(f"#opt-{AGENT_SELECT}", Checkbox).value = AGENT_SELECT in self.selection
                self._refresh_agents()
            return
        pid = getattr(event.checkbox, "plugin_id", None)
        if pid is not None:
            key = f"{PLUGIN_PREFIX}{pid}"
            if event.value != (key in self.selection):
                self.selection = toggle(self.selection, key, event.value)
                self.query_one(f"#opt-{PLUGINS_SELECT}", Checkbox).value = PLUGINS_SELECT in self.selection
            return
        name = getattr(event.checkbox, "mcp_name", None)
        if name is not None:
            key = f"{MCP_PREFIX}{name}"
            if event.value != (key in self.selection):
                self.selection = toggle(self.selection, key, event.value)
                self.query_one(f"#opt-{MCP_SELECT}", Checkbox).value = MCP_SELECT in self.selection
            return
        if not event.checkbox.id.startswith("opt-"):
            return  # e.g. auto-continue: independent of the flag catalogue
        key = event.checkbox.id.removeprefix("opt-")
        if event.value == (key in self.selection):
            return  # echo of our own sync below
        self.selection = toggle(self.selection, key, event.value)
        if key == PLUGINS_SELECT and event.value and not self._chosen_plugins():
            # Start from what is on now, so the user only unticks.
            for pid in plugins.enabled_by_default():
                self.selection = toggle(self.selection, f"{PLUGIN_PREFIX}{pid}", True)
        for checkbox in self.query(Checkbox):
            if not checkbox.id.startswith("opt-"):
                continue
            wanted = checkbox.id.removeprefix("opt-") in self.selection
            if checkbox.value != wanted:
                checkbox.value = wanted
        self._sync_session_widgets()
        self._refresh_mcp()
        self._refresh_plugins()
        self._refresh_agents()

    def _sync_session_widgets(self) -> None:
        self._refresh_chips()
        self._refresh_session_field()
        mode = session_mode(self.selection)
        self.query_one(f"#opt-{FORK}", Checkbox).disabled = mode == SESSION_NEW
        self.query_one("#from-pr-input", Input).display = mode == SESSION_FROM_PR
        sessions = self.query_one("#resume-sessions", OptionList)
        sessions.display = mode == SESSION_RESUME
        path = self._path() if self.query_one("#launch-path", Input).value.strip() else ""
        if mode == SESSION_RESUME and path != self._sessions_for:
            self._sessions_for = path
            sessions.clear_options()
            found = list_sessions_for_directory(path) if path and Path(path).is_dir() else []
            self._session_ids = [s.session_id for s in found]
            sessions.add_options([
                Option(Content(_("{time} · {n} msgs · {prompt}").format(
                    time=f"{s.modified:%Y-%m-%d %H:%M}", n=s.message_count, prompt=s.first_prompt)))
                for s in found
            ])
            if found:
                sessions.highlighted = 0

    # --- buttons --------------------------------------------------------

    def _current_choice(self) -> LaunchChoice:
        return LaunchChoice(
            self.selection,
            self.query_one("#auto-continue", Checkbox).value,
            self.query_one("#remote-control", Checkbox).value,
        )

    def _apply_choice(self, choice: LaunchChoice) -> None:
        self.selection = choice.selection
        for checkbox in self.query(Checkbox):
            if checkbox.id.startswith("opt-"):
                wanted = checkbox.id.removeprefix("opt-") in self.selection
                if checkbox.value != wanted:
                    checkbox.value = wanted
        self._refresh_mcp()
        self._refresh_plugins()
        self._refresh_agents()
        self.query_one("#auto-continue", Checkbox).value = choice.auto_continue
        self.query_one("#remote-control", Checkbox).value = choice.remote_control
        self._sync_session_widgets()

    def _profile(self) -> str:
        found = self.query("#profile-select")
        return str(found.first(Select).value) if found else ""

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "preset-select" or event.value is Select.NULL:
            return
        choice = list_presets(self.storage).get(event.value)
        if choice is not None:
            self._apply_choice(choice)

    def _save_preset(self, name: str | None) -> None:
        if not name:
            return
        save_preset(self.storage, name, self._current_choice())
        select = self.query_one("#preset-select", Select)
        select.set_options(self._preset_options())
        select.value = name

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "nc-more-button":
            self.action_toggle_more()
            return
        if event.button.id == "nc-session":
            # The session is chosen among all the options (+ option).
            self.action_toggle_more(True)
            self.query_one(f"#opt-{session_mode(self.selection)}", Checkbox).focus()
            return
        if event.button.id == "preset-save":
            self.app.push_screen(PresetNameModal(), self._save_preset)
            return
        if event.button.id == "launch-cancel":
            self.dismiss(None)
            return
        if event.button.id != "launch-start":
            return
        error = self.query_one("#launch-error", Static)
        if not self.query_one("#launch-path", Input).value.strip():
            error.update(_("Choose a directory."))
            return
        for name in self._chosen_agents():
            if agents.find(name, self._path()) is None:
                error.update(_("There is no agent “{name}” for this folder.").format(name=name))
                return
        session_id = None
        if session_mode(self.selection) == SESSION_RESUME:
            highlighted = self.query_one("#resume-sessions", OptionList).highlighted
            if highlighted is not None and highlighted < len(self._session_ids):
                session_id = self._session_ids[highlighted]
        try:
            queue = start_conversation(
                self.storage,
                self._path(),
                self.selection,
                name=self.query_one("#launch-name", Input).value,
                session_id=session_id,
                pr=self.query_one("#from-pr-input", Input).value,
                auto_continue=self.query_one("#auto-continue", Checkbox).value,
                remote_control=self.query_one("#remote-control", Checkbox).value,
                worktree=self.query_one("#own-worktree", Checkbox).value,
                profile=self._profile(),
            )
        except ValueError as exc:
            error.update(Content(str(exc)))
            return
        save_last(self.storage, self._current_choice())
        self.dismiss(queue.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in the folder or the name starts it (as the design's "↵ start").
        if event.input.id in ("launch-path", "launch-name"):
            event.stop()
            self.query_one("#launch-start", Button).press()

    def action_cancel(self) -> None:
        self.dismiss(None)
