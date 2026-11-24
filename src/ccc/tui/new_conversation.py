from __future__ import annotations

import threading
from pathlib import Path

from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.widgets import Button, Checkbox, DirectoryTree, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from ccc import agents, folder_browse as fb, keymap, mcp_servers, plugins, profiles
from ccc.i18n import _
from ccc.launch_options import (
    AGENT_PREFIX, AGENT_SELECT, FORK, GROUPS, MCP_PREFIX, MCP_SELECT, PLUGIN_PREFIX, PLUGINS_SELECT, SESSION_CONTINUE,
    SESSION_FROM_PR, SESSION_NEW, SESSION_RESUME, group_of, is_agent, is_mcp_server, is_plugin, session_mode, toggle,
)
from ccc.orchestrator import start_conversation
from ccc.presets import (
    LaunchChoice, default_preset_name, list_presets, opening_choice, save_last, save_preset, set_default_preset,
)
from ccc.storage import Storage
from ccc.transcript import list_sessions_for_directory
from ccc.tui.layout import CccModal, MODAL_CSS

RECENT_SHOWN = 10  # folders listed under "Recent": the ones that still exist
PROJECT_SEARCH_SECONDS = 20.0
PROJECT_SEARCH_SHOWN = 200
NARROW_COLUMNS = 100  # a window narrower than this (phone) stacks the columns


class _FolderTree(DirectoryTree):
    """The folders of a disk (#337): hidden ones and the system's pseudo
    folders left out, → opens, ← closes (and, at the top, goes up a level)."""

    BINDINGS = [
        keymap.binding("window.tree_open", "open_node", show=False),
        keymap.binding("window.tree_back", "back", show=False),
    ]

    def filter_paths(self, paths):
        return [
            p for p in paths
            if not fb.is_hidden(p.name) and p.is_dir()
            and not (str(p.parent) == "/" and p.name in fb.SKIPPED_ROOT_FOLDERS)
        ]

    def action_open_node(self) -> None:
        node = self.cursor_node
        if node is not None and node.allow_expand and not node.is_expanded:
            node.expand()

    def action_back(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.is_expanded and node.allow_expand and node is not self.root:
            node.collapse()
        elif node.parent is not None and node.parent is not self.root:
            self.move_cursor(node.parent)
        else:
            self.screen.browse_up()


class _PathInput(Input):
    """The folder: Tab completes the path (and moves on when there's nothing to complete)."""

    BINDINGS = [keymap.binding("window.complete", "complete", show=False, priority=True)]

    def action_complete(self) -> None:
        self.screen.complete_path()


def default_browse_root() -> Path:
    dev = Path.home() / "DEV"
    return dev if dev.is_dir() else Path.home()


def _is_git_repo(path: str) -> bool:
    folder = Path(path)
    return bool(path) and any((p / ".git").exists() for p in (folder, *folder.parents))


class PresetNameModal(CccModal):
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


class ProjectSearchModal(CccModal):
    """"Find project…" (#337): looks for folders with a .git by name on the home
    folder and the other disks — only while this window is open, with a depth
    and a time limit — and dismisses with the chosen folder, or None."""

    DEFAULT_CSS = MODAL_CSS.format(cls="ProjectSearchModal") + """
    ProjectSearchModal > Container {
        width: 90%;
        max-width: 100;
        height: 80%;
    }
    ProjectSearchModal #ps-results {
        height: 1fr;
        border: solid $ccc-line;
        background: $ccc-background;
    }
    ProjectSearchModal #ps-status {
        color: $ccc-text-muted;
    }
    """
    BINDINGS = [keymap.binding("window.cancel", "cancel", _("Cancel"))]

    def __init__(self, roots: list[str] | None = None):
        super().__init__()
        self._roots = roots
        self._stop = threading.Event()
        self._generation = 0
        self._found = 0

    def compose(self):
        box = Container(
            Static(_("Part of the project's name"), classes="modal-label"),
            Input(placeholder=_("e.g. ccc"), id="ps-query"),
            OptionList(id="ps-results"),
            Static("", id="ps-status"),
        )
        box.border_title = _("Find project")
        yield box

    def on_mount(self) -> None:
        self.query_one("#ps-query", Input).focus()
        self._search("")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "ps-query":
            self._search(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        results = self.query_one("#ps-results", OptionList)
        if results.option_count:
            highlighted = results.highlighted if results.highlighted is not None else 0
            self.dismiss(results.get_option_at_index(highlighted).id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def _search(self, query: str) -> None:
        self._stop.set()  # the earlier search ends
        self._stop = stop = threading.Event()
        self._generation += 1
        generation = self._generation
        self._found = 0
        self.query_one("#ps-results", OptionList).clear_options()
        self.query_one("#ps-status", Static).update(_("Searching…"))
        roots = self._roots if self._roots is not None else fb.search_roots()

        def work() -> None:
            found = 0
            for path in fb.find_projects(query, roots, seconds=PROJECT_SEARCH_SECONDS, stop=stop.is_set):
                if stop.is_set() or found >= PROJECT_SEARCH_SHOWN:
                    break
                found += 1
                self._post(self._add_result, generation, path)
            self._post(self._finish, generation, stop.is_set())

        self.run_worker(work, thread=True, group="project-search")

    def _post(self, callback, *args) -> None:
        try:
            self.app.call_from_thread(callback, *args)
        except Exception:  # the window closed meanwhile
            pass

    def _add_result(self, generation: int, path: str) -> None:
        if generation != self._generation:
            return
        self._found += 1
        results = self.query_one("#ps-results", OptionList)
        results.add_option(Option(Content(fb.short(path)), id=path))
        if results.highlighted is None:
            results.highlighted = 0

    def _finish(self, generation: int, stopped: bool) -> None:
        if generation != self._generation or stopped:
            return
        self.query_one("#ps-status", Static).update(
            _("{n} found").format(n=self._found) if self._found else _("Nothing found."))

    def on_unmount(self) -> None:
        self._stop.set()

    def action_cancel(self) -> None:
        self._stop.set()
        self.dismiss(None)


# The option groups' colours (#337, the design): by the option's group.
GROUP_COLOURS = {"permissions": "#d78787", "model": "#5f87ff", "effort": "#d7af5f"}


class NewConversationModal(CccModal):
    """The design's window (#250, #337): name and preset on top, the folder (Tab
    completes it, "Find project…" looks for it, the recent ones and a tree of
    the disks to pick from), the session with what each choice means, every
    option of Claude Code, ccc's own switches, Start."""

    DEFAULT_CSS = MODAL_CSS.format(cls="NewConversationModal") + """
    NewConversationModal > Container {
        width: 95%;
        max-width: 130;
        height: 95%;
        padding: 0;
        border: solid $ccc-text;
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
    NewConversationModal #nc-find {
        margin-right: 2;
    }
    NewConversationModal #nc-top {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 1fr;
        grid-rows: 4;
        grid-gutter: 0 2;
        height: 4;
    }
    NewConversationModal .nc-cell {
        height: 100%;
    }
    NewConversationModal #launch-name {
        margin: 0;
    }
    NewConversationModal #preset-row {
        height: 3;
    }
    NewConversationModal #preset-select {
        width: 1fr;
    }
    NewConversationModal #preset-save, NewConversationModal #preset-default {
        margin-left: 1;
        min-width: 0;
        width: auto;
    }
    NewConversationModal #launch-path {
        margin-bottom: 0;
        border: tall $ccc-accent;
    }
    NewConversationModal #nc-complete {
        height: auto;
        min-height: 1;
        color: $ccc-text-dim;
        margin-bottom: 1;
    }
    NewConversationModal #nc-places {
        height: 14;
        margin-bottom: 1;
    }
    NewConversationModal #nc-recent-box, NewConversationModal #nc-browse {
        width: 1fr;
        height: 100%;
    }
    NewConversationModal #nc-recent-box {
        margin-right: 2;
    }
    NewConversationModal #recent-dirs {
        height: 1fr;
        border: solid $ccc-line;
        background: $ccc-background;
    }
    NewConversationModal #nc-crumb {
        color: $ccc-warning;
        height: 1;
        padding: 0 1;
    }
    NewConversationModal #dir-tree {
        height: 1fr;
        border: solid $ccc-line;
        background: $ccc-background;
    }
    NewConversationModal #nc-session-hint {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        color: $ccc-text-muted;
        background: $ccc-background;
        border-left: thick $ccc-accent;
    }
    NewConversationModal .nc-opt-row {
        height: auto;
    }
    NewConversationModal .nc-opt-row Checkbox {
        width: 34;
    }
    NewConversationModal .nc-desc {
        width: 1fr;
        color: $ccc-text-dim;
    }
    NewConversationModal #resume-sessions {
        height: 8;
        margin-left: 4;
    }
    NewConversationModal #from-pr-input {
        margin-left: 4;
    }
    NewConversationModal .nc-box {
        height: auto;
        border: solid $ccc-line;
        padding: 0 1;
        margin-bottom: 1;
    }
    NewConversationModal .nc-columns {
        height: auto;
    }
    NewConversationModal .nc-column {
        width: 1fr;
        height: auto;
    }
    NewConversationModal .group-title {
        text-style: bold;
        color: $ccc-text-muted;
        margin-top: 1;
    }
    NewConversationModal #mcp-servers, NewConversationModal #plugin-list, NewConversationModal #agent-list {
        height: auto;
        margin-left: 4;
    }
    NewConversationModal #nc-ccc {
        height: auto;
        margin-bottom: 1;
    }
    NewConversationModal #nc-ccc Checkbox {
        width: auto;
        margin-right: 3;
    }
    /* A narrow window (phone): the columns one under another. */
    NewConversationModal.-narrow #nc-places, NewConversationModal.-narrow .nc-columns {
        layout: vertical;
    }
    NewConversationModal.-narrow #nc-places {
        height: 28;
    }
    NewConversationModal.-narrow #nc-recent-box, NewConversationModal.-narrow #nc-browse {
        width: 100%;
        height: 13;
        margin-right: 0;
    }
    NewConversationModal.-narrow .nc-opt-row {
        layout: vertical;
    }
    NewConversationModal.-narrow .nc-opt-row Checkbox {
        width: 100%;
    }
    NewConversationModal.-narrow #nc-ccc {
        layout: vertical;
    }
    NewConversationModal.-narrow #nc-top {
        layout: vertical;
        grid-size: 1;
        height: 8;
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
        keymap.binding("window.start", "start_now", _("Start"), show=False),
    ]

    def __init__(self, storage: Storage, browse_root: Path | None = None):
        super().__init__()
        self.storage = storage
        self.browse_root = browse_root or default_browse_root()
        # What it opens with: the default preset, else what was used last (the session always "new").
        choice, self._preset_name = opening_choice(storage)
        self.selection = choice.selection
        self._initial = choice
        self._sessions_cache: dict[str, list] = {}
        self._sessions_for: str | None = None
        self._session_ids: list[str] = []
        self._mcp_shown: list[str] | None = None  # server names currently listed
        self._mcp_generation = 0  # keeps widget ids unique across rebuilds
        self._plugins_shown: list[str] | None = None  # plugin ids currently listed
        self._plugin_generation = 0
        self._agents_shown: list[str] | None = None  # agent names currently listed
        self._agent_generation = 0
        self._suggest_seq = 0

    def _preset_options(self) -> list[tuple[str, str]]:
        default = default_preset_name(self.storage)
        return [(("★ " if name == default else "") + name, name) for name in sorted(list_presets(self.storage))]

    @staticmethod
    def _label_row(label: str, hint: str = "", *extra, id: str | None = None) -> Horizontal:
        return Horizontal(Static(label.upper(), classes="nc-label"), *extra, Static(hint, classes="nc-hint"),
                          classes="nc-label-row", id=id)

    def _descriptions(self) -> dict[str, str]:
        """What each way of choosing the session means (the design's right column)."""
        return {
            SESSION_NEW: _("starts from zero, Claude knows nothing about the project"),
            SESSION_CONTINUE: _("continues the last conversation in this folder"),
            SESSION_RESUME: _("pick an earlier conversation from the list"),
            SESSION_FROM_PR: _("continues the conversation the PR came from"),
            FORK: _("the original conversation stays untouched · only when you continue something"),
        }

    def _group_widgets(self, group, colour: str | None = None, title: bool = True) -> list:
        widgets = []
        if title and group.title:
            label = Static(_(group.title), classes="group-title")
            if colour:
                label.styles.color = colour
            widgets.append(label)
        for option in group.options:
            widgets.append(Checkbox(_(option.label), value=option.key in self.selection, id=f"opt-{option.key}"))
            if option.key == MCP_SELECT:
                widgets.append(Vertical(id="mcp-servers"))
            elif option.key == PLUGINS_SELECT:
                widgets.append(Vertical(id="plugin-list"))
            elif option.key == AGENT_SELECT:
                widgets.append(Vertical(id="agent-list"))
        return widgets

    def _session_rows(self) -> list:
        descriptions = self._descriptions()
        rows = []
        options = [o for g in GROUPS if g.key in ("session", "session_extra") for o in g.options]
        for option in options:
            rows.append(Horizontal(
                Checkbox(_(option.label), value=option.key in self.selection, id=f"opt-{option.key}"),
                Static(descriptions[option.key], id=f"desc-{option.key}", classes="nc-desc"),
                classes="nc-opt-row"))
            if option.key == SESSION_RESUME:
                rows.append(OptionList(id="resume-sessions"))
            elif option.key == SESSION_FROM_PR:
                rows.append(Input(placeholder=_("PR number or URL"), id="from-pr-input"))
        return rows

    def compose(self):
        by_key = {g.key: g for g in GROUPS}
        recent = [p for p in self.storage.recent_project_paths(RECENT_SHOWN * 3) if Path(p).is_dir()][:RECENT_SHOWN]
        keys = keymap.current(self.storage)
        preset_value = {"value": self._preset_name} if self._preset_name in list_presets(self.storage) else {}
        profile_widgets = []
        # Only when accounts were added (`ccc profile add`): nobody else sees a change.
        if profiles.list_profiles():
            profile_widgets = [
                Static(_("Claude account"), classes="group-title"),
                Select([(_("(default)"), "")] + [(f"{p.name} (API)" if p.is_command else p.name, p.name)
                                                 for p in profiles.list_profiles()],
                       allow_blank=False, value="", id="profile-select"),
            ]
            if any(p.is_command for p in profiles.list_profiles()):
                profile_widgets.append(Static(_(profiles.API_LIMITS_NOTICE), classes="hint"))
        box = Container(
            VerticalScroll(
                Container(
                    Vertical(self._label_row(_("Name (optional)"), _("optional")),
                             Input(placeholder=_("defaults to the directory name"), id="launch-name"), classes="nc-cell"),
                    Vertical(self._label_row(_("Preset"), _("fills in the whole window")),
                             Horizontal(Select(self._preset_options(), prompt=_("Choose a preset…"), **preset_value,
                                               id="preset-select"),
                                        Button(_("Save…"), id="preset-save"),
                                        Button("★", id="preset-default"), id="preset-row"),
                             classes="nc-cell"),
                    id="nc-top",
                ),
                self._label_row(_("Directory"), _("required · tab completes the path"),
                                Button(_("Find project…"), id="nc-find", classes="nc-small")),
                _PathInput(placeholder=_("/path/to/project"), id="launch-path"),
                Static("", id="nc-complete"),
                Horizontal(
                    Vertical(self._label_row(_("Recent directories"), "↑↓ ↵"),
                             OptionList(*[Option(Content(fb.short(p)), id=p) for p in recent], id="recent-dirs"),
                             id="nc-recent-box"),
                    Vertical(self._label_row(_("Browse the disk"), "→ " + _("open") + "  ← " + _("back") + "  ↵ " + _("choose")),
                             Static("", id="nc-crumb"),
                             _FolderTree(str(fb.nearest_existing("", str(self.browse_root))), id="dir-tree"),
                             id="nc-browse"),
                    id="nc-places",
                ),
                self._label_row(_("Session"), _("one of the options")),
                Static(Content.assemble(
                    ("ⓘ ", "bold"), _("A session is Claude Code's memory: the whole history of the conversation in this folder. "
                                     "A new one starts with a clean head, the others continue from what Claude already knows.")),
                    id="nc-session-hint"),
                *self._session_rows(),
                self._label_row(_("Claude Code options"), _("space toggles · nothing = default")),
                Vertical(
                    Horizontal(
                        Vertical(*self._group_widgets(by_key["permissions"], GROUP_COLOURS["permissions"]), classes="nc-column"),
                        Vertical(*self._group_widgets(by_key["model"], GROUP_COLOURS["model"]), classes="nc-column"),
                        Vertical(*self._group_widgets(by_key["effort"], GROUP_COLOURS["effort"]), classes="nc-column"),
                        classes="nc-columns"),
                    Horizontal(
                        Vertical(Static(_("Limit"), classes="group-title"),
                                 *self._group_widgets(by_key["mcp"], title=False),
                                 *self._group_widgets(by_key["plugins"], title=False),
                                 *self._group_widgets(by_key["agent"], title=False), classes="nc-column"),
                        Vertical(*self._group_widgets(by_key["other"]), classes="nc-column"),
                        classes="nc-columns"),
                    *profile_widgets,
                    classes="nc-box",
                ),
                self._label_row("ccc", _("how ccc behaves, not Claude Code")),
                Horizontal(
                    # Not `claude` flags: ccc's own behaviour.
                    Checkbox(_("Auto-continue after usage limit"), value=self._initial.auto_continue, id="auto-continue"),
                    Checkbox(_("Remote control (phone / claude.ai)"), value=self._initial.remote_control, id="remote-control"),
                    Checkbox(_("Own git worktree (to work in parallel)"), value=False, id="own-worktree"),
                    id="nc-ccc"),
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
        box.border_title = _("New conversation")  # the red bar (CccModal)
        yield box

    # --- the window ------------------------------------------------------

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < NARROW_COLUMNS, "-narrow")

    def on_mount(self) -> None:
        self.set_class(self.size.width < NARROW_COLUMNS, "-narrow")
        has_recent = self.query_one("#recent-dirs", OptionList).option_count > 0
        self.query_one("#nc-recent-box").display = has_recent  # no empty box (#337)
        self._show_crumb()
        self._sync_session_widgets()
        self._refresh_mcp()
        self._refresh_plugins()
        self._refresh_agents()
        self.query_one("#launch-path", Input).focus()

    def action_start_now(self) -> None:
        self.query_one("#launch-start", Button).press()

    def action_save_preset(self) -> None:
        self.app.push_screen(PresetNameModal(), self._save_preset)

    # --- directory ------------------------------------------------------

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "recent-dirs":
            self._choose_folder(event.option.id)

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self._choose_folder(str(event.path))

    def _choose_folder(self, path: str) -> None:
        self.query_one("#launch-path", Input).value = fb.short(path)

    def _show_crumb(self) -> None:
        tree = self.query_one("#dir-tree", _FolderTree)
        self.query_one("#nc-crumb", Static).update(fb.breadcrumb(tree.path))

    def _set_tree_root(self, path: str | Path) -> None:
        tree = self.query_one("#dir-tree", _FolderTree)
        if Path(str(tree.path)) != Path(str(path)):
            tree.path = Path(str(path))
        self._show_crumb()

    def browse_up(self) -> None:
        """← at the top of the tree: one folder up, as far as /."""
        tree = self.query_one("#dir-tree", _FolderTree)
        parent = Path(str(tree.path)).parent
        if parent != Path(str(tree.path)):
            self._set_tree_root(parent)

    def complete_path(self) -> None:
        """Tab in the folder: complete the path, or go on to the next field."""
        field = self.query_one("#launch-path", Input)
        text, options = fb.complete(field.value)
        if not options:
            self.focus_next()
            return
        if text != field.value:
            field.value = text
            field.cursor_position = len(text)
        self._show_suggestions(options, self._suggest_seq)

    def _suggest(self, value: str) -> None:
        """The choices under the folder field — read in a worker, so a slow disk
        can't hold the window."""
        self._suggest_seq += 1
        seq = self._suggest_seq

        def work() -> None:
            options = fb.completions(value)
            try:
                self.app.call_from_thread(self._show_suggestions, options, seq)
            except Exception:  # the window closed meanwhile
                pass

        self.run_worker(work, thread=True, exclusive=True, group="suggest")

    def _show_suggestions(self, options: list[str], seq: int) -> None:
        if seq != self._suggest_seq:
            return
        self.query_one("#nc-complete", Static).update("↳ " + "   ".join(options) if options else "")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "launch-path":
            self._follow_typed_path(event.value)
            self._suggest(event.value)
            self._sync_session_widgets()
            self._refresh_mcp()
            self._refresh_agents()

    def _follow_typed_path(self, value: str) -> None:
        """A folder typed with its closing slash opens in the tree, so the disk
        can be browsed from anywhere (#337)."""
        value = value.strip()
        if not value.endswith("/"):
            return
        folder = Path(fb.expand(value))
        if folder.is_dir():
            self._set_tree_root(folder)

    def _path(self) -> str:
        return str(Path(self.query_one("#launch-path", Input).value.strip()).expanduser())

    def _existing_path(self) -> str:
        """The chosen folder if there is one, else ""."""
        value = self.query_one("#launch-path", Input).value.strip()
        return self._path() if value and Path(self._path()).is_dir() else ""

    def _sessions(self, path: str) -> list:
        """The earlier conversations of the folder (read once per folder)."""
        if not path:
            return []
        if path not in self._sessions_cache:
            self._sessions_cache[path] = list_sessions_for_directory(path)
        return self._sessions_cache[path]

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
        self._refresh_availability()
        mode = session_mode(self.selection)
        self.query_one("#from-pr-input", Input).display = mode == SESSION_FROM_PR
        sessions = self.query_one("#resume-sessions", OptionList)
        sessions.display = mode == SESSION_RESUME
        path = self._existing_path()
        if mode == SESSION_RESUME and path != self._sessions_for:
            self._sessions_for = path
            sessions.clear_options()
            found = self._sessions(path)
            self._session_ids = [s.session_id for s in found]
            sessions.add_options([
                Option(Content(_("{time} · {n} msgs · {prompt}").format(
                    time=f"{s.modified:%Y-%m-%d %H:%M}", n=s.message_count, prompt=s.first_prompt)))
                for s in found
            ])
            if found:
                sessions.highlighted = 0

    def _refresh_availability(self) -> None:
        """Switch off what makes no sense for the chosen folder, saying why next
        to it (#337): no earlier conversation to continue or resume, nothing to
        copy from a new session, no git repository for a worktree."""
        descriptions = self._descriptions()
        path = self._existing_path()
        has_sessions = bool(self._sessions(path))
        for key in (SESSION_CONTINUE, SESSION_RESUME):
            self.query_one(f"#opt-{key}", Checkbox).disabled = not has_sessions
            self.query_one(f"#desc-{key}", Static).update(
                descriptions[key] if has_sessions else _("no earlier conversation in this folder"))
        if not has_sessions and session_mode(self.selection) in (SESSION_CONTINUE, SESSION_RESUME):
            self.selection = toggle(self.selection, SESSION_NEW, True)
            self._sync_checkboxes()
        self.query_one(f"#opt-{FORK}", Checkbox).disabled = session_mode(self.selection) == SESSION_NEW
        worktree = self.query_one("#own-worktree", Checkbox)
        git = _is_git_repo(path)
        worktree.disabled = not git
        if not git and worktree.value:
            worktree.value = False
        label = _("Own git worktree (to work in parallel)")
        worktree.label = label if git or not path else label + " · " + _("this folder is not a git repository")

    def _sync_checkboxes(self) -> None:
        for checkbox in self.query(Checkbox):
            if checkbox.id and checkbox.id.startswith("opt-"):
                wanted = checkbox.id.removeprefix("opt-") in self.selection
                if checkbox.value != wanted:
                    checkbox.value = wanted

    # --- buttons --------------------------------------------------------

    def _current_choice(self) -> LaunchChoice:
        return LaunchChoice(
            self.selection,
            self.query_one("#auto-continue", Checkbox).value,
            self.query_one("#remote-control", Checkbox).value,
        )

    def _apply_choice(self, choice: LaunchChoice) -> None:
        self.selection = choice.selection
        self._sync_checkboxes()
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
        if event.select.id != "preset-select" or event.value is Select.NULL or event.value == self._preset_name:
            return
        choice = list_presets(self.storage).get(event.value)
        if choice is not None:
            self._preset_name = event.value
            self._apply_choice(choice)

    def _found_project(self, path: str | None) -> None:
        if path:
            self._choose_folder(path)
            self.query_one("#launch-path", Input).focus()

    def _save_preset(self, name: str | None) -> None:
        if not name:
            return
        save_preset(self.storage, name, self._current_choice())
        self._preset_name = name  # what is shown is what's now in the window: no re-applying
        select = self.query_one("#preset-select", Select)
        select.set_options(self._preset_options())
        select.value = name

    def _toggle_default_preset(self) -> None:
        """★: the shown preset opens the window from now on (again: none does,
        and the last used choice comes back)."""
        error = self.query_one("#launch-error", Static)
        name = self._preset_name
        if not name or name not in list_presets(self.storage):
            error.update(_("Choose or save a preset first."))
            return
        error.update("")
        set_default_preset(self.storage, None if default_preset_name(self.storage) == name else name)
        select = self.query_one("#preset-select", Select)
        select.set_options(self._preset_options())
        select.value = name

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "nc-find":
            self.app.push_screen(ProjectSearchModal(), self._found_project)
            return
        if event.button.id == "preset-default":
            self._toggle_default_preset()
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
        # Enter in the folder only confirms it and moves on (Tab completes, so a
        # half-typed path must not start anything); in the name it starts.
        if event.input.id == "launch-path":
            event.stop()
            self.focus_next()
        elif event.input.id == "launch-name":
            event.stop()
            self.query_one("#launch-start", Button).press()

    def action_cancel(self) -> None:
        self.dismiss(None)
