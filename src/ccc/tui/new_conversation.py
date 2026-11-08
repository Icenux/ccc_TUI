from __future__ import annotations

from pathlib import Path

from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DirectoryTree, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from ccc import keymap, mcp_servers, plugins, profiles
from ccc.i18n import _
from ccc.launch_options import (
    DEFAULT_SELECTION, FORK, GROUPS, MCP_PREFIX, MCP_SELECT, PLUGIN_PREFIX, PLUGINS_SELECT, SESSION_FROM_PR, SESSION_NEW, SESSION_RESUME,
    is_mcp_server, is_plugin, session_mode, toggle,
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


class NewConversationModal(ModalScreen):
    DEFAULT_CSS = MODAL_CSS.format(cls="NewConversationModal") + """
    NewConversationModal > Container {
        width: 90%;
        max-width: 150;
        height: 90%;
    }
    NewConversationModal #new-conv-columns {
        height: 1fr;
    }
    NewConversationModal #new-conv-left {
        width: 1fr;
        margin-right: 2;
    }
    NewConversationModal #new-conv-right {
        width: 1fr;
    }
    NewConversationModal #recent-dirs {
        height: 7;
        margin-bottom: 1;
    }
    NewConversationModal #dir-tree {
        height: 1fr;
        margin-bottom: 1;
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
    NewConversationModal #preset-row {
        height: 3;
    }
    NewConversationModal #preset-select {
        width: 1fr;
    }
    NewConversationModal #preset-save {
        margin-left: 1;
    }
    NewConversationModal .group-title {
        text-style: bold;
        color: $ccc-title;
        margin-top: 1;
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
        background: green;
        text-style: bold;
    }
    NewConversationModal Checkbox.-on > .toggle--label {
        text-style: bold;
    }
    NewConversationModal Checkbox:disabled > .toggle--label {
        color: $text-disabled;
    }
    """

    BINDINGS = [keymap.binding("window.cancel", "cancel", _("Cancel"))]

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

    def _preset_options(self) -> list[tuple[str, str]]:
        return [(name, name) for name in sorted(list_presets(self.storage))]

    def compose(self):
        right = [
            Static(_("Preset"), classes="group-title"),
            Horizontal(
                Select(self._preset_options(), prompt=_("Choose a preset…"), id="preset-select"),
                Button(_("Save as preset"), id="preset-save"),
                id="preset-row",
            ),
        ]
        for group in GROUPS:
            if group.title:
                right.append(Static(_(group.title), classes="group-title"))
            for option in group.options:
                right.append(Checkbox(_(option.label), value=option.key in self.selection, id=f"opt-{option.key}"))
                if option.key == MCP_SELECT:
                    right.append(Vertical(id="mcp-servers"))
                elif option.key == PLUGINS_SELECT:
                    right.append(Vertical(id="plugin-list"))
                elif option.key == SESSION_RESUME:
                    right.append(OptionList(id="resume-sessions"))
                elif option.key == SESSION_FROM_PR:
                    right.append(Input(placeholder=_("PR number or URL"), id="from-pr-input"))
        # Only when accounts were added (`ccc profile add`): nobody else sees a change.
        if profiles.list_profiles():
            right.append(Static(_("Claude account"), classes="group-title"))
            right.append(Select(
                [(_("(default)"), "")] + [(f"{p.name} (API)" if p.is_command else p.name, p.name) for p in profiles.list_profiles()],
                allow_blank=False, value="", id="profile-select",
            ))
            if any(p.is_command for p in profiles.list_profiles()):
                right.append(Static(_(profiles.API_LIMITS_NOTICE), classes="hint"))
        # Not a `claude` flag: ccc's own behaviour at usage limits.
        right.append(Static("ccc", classes="group-title"))
        right.append(Checkbox(_("Auto-continue after usage limit"), value=self._initial.auto_continue, id="auto-continue"))
        right.append(Checkbox(_("Remote control (phone / claude.ai)"), value=self._initial.remote_control, id="remote-control"))
        right.append(Checkbox(_("Own git worktree (a separate checkout and branch — to work in parallel)"),
                              value=False, id="own-worktree"))
        recent = [Option(Content(p), id=p) for p in self.storage.recent_project_paths()]
        box = Container(
            Horizontal(
                Vertical(
                    Static(_("Recent directories"), classes="modal-label"),
                    OptionList(*recent, id="recent-dirs"),
                    Static(_("Browse"), classes="modal-label"),
                    _DirectoriesOnly(str(self.browse_root), id="dir-tree"),
                    Static(_("Path"), classes="modal-label"),
                    Input(placeholder=_("/path/to/project"), id="launch-path"),
                    id="new-conv-left",
                ),
                VerticalScroll(*right, id="new-conv-right"),
                id="new-conv-columns",
            ),
            Static(_("Name (optional)"), classes="modal-label"),
            Input(placeholder=_("defaults to the directory name"), id="launch-name"),
            Static("", id="launch-error", classes="modal-error"),
            Horizontal(
                Button(_("Start"), id="launch-start", classes="modal-submit"),
                Button(_("Cancel"), id="launch-cancel", classes="modal-cancel"),
                classes="modal-buttons",
            ),
        )
        box.border_title = _("New conversation")
        yield box

    def on_mount(self) -> None:
        self._sync_session_widgets()
        self._refresh_mcp()
        self._refresh_plugins()

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

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
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

    def _sync_session_widgets(self) -> None:
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

    def action_cancel(self) -> None:
        self.dismiss(None)
