"""The keyboard shortcuts window (Settings → Keyboard shortcuts): every
action with its key; pick one and press its new key (or type its name).
keymap.problems() checks the key first — a collision is never taken,
the window says why. Save keeps the whole keymap (keymap.save, checked
again) and the app switches to it right away (CCContinueApp.apply_keymap).
Export / Import write / read ccc-keys.json in any folder."""
from __future__ import annotations

from rich.text import Text
from textual import events
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Input, Static

from ccc import keymap
from ccc.export_paths import export_folder
from ccc.i18n import _
from ccc.tui.layout import CccModal, MODAL_CSS


def _section(action_id: str) -> str:
    from ccc.tui.help import section_of

    if action_id.startswith("window."):
        return _("Windows")  # every window, not just the first one listed
    section = section_of(action_id)
    return _(section) if section else ""


class KeysModal(CccModal):
    DEFAULT_CSS = MODAL_CSS.format(cls="KeysModal") + """
    KeysModal > Container {
        width: 95%;
        max-width: 120;
        height: 95%;
    }
    KeysModal #keys-table {
        height: 1fr;
        margin-bottom: 1;
    }
    KeysModal #keys-table > .datatable--cursor {
        background: $ccc-selection;
        color: white;
        text-style: bold;
    }
    KeysModal #keys-status {
        height: auto;
        min-height: 2;
    }
    KeysModal .keys-row {
        height: 3;
        margin-bottom: 1;
    }
    KeysModal .keys-row Input {
        width: 1fr;
        margin-bottom: 0;
    }
    KeysModal .keys-row Button {
        height: 3;
        margin-left: 1;
    }
    KeysModal .keys-hint {
        color: $text-muted;
    }
    KeysModal .modal-hint {
        color: $text-muted;
        width: 1fr;
        padding-top: 1;
    }
    """

    BINDINGS = [
        keymap.binding("window.save", "save", _("Save"), priority=True),
        keymap.binding("window.cancel", "cancel", _("Cancel"), priority=True),
    ]

    def __init__(self, storage):
        super().__init__()
        self.storage = storage
        # The keymap being edited (every action), saved only on Save.
        self.keys = keymap.current(storage)
        self.capturing = False

    def compose(self):
        box = Container(
            Static(_("Pick a shortcut, then “Press the key” and press its new key — or type its name "
                     "(Ctrl+P, Alt+S, F5, +) and “Set”. A key that collides with another one is refused, "
                     "with the reason."), classes="keys-hint"),
            DataTable(id="keys-table", cursor_type="row"),
            Static("", id="keys-status"),
            Horizontal(
                Input(placeholder=_("Key name, e.g. Ctrl+O or Alt+S"), id="keys-input"),
                Button(_("Set"), id="keys-set"),
                Button(_("Press the key"), id="keys-capture"),
                Button(_("Default"), id="keys-default"),
                Button(_("All defaults"), id="keys-all-defaults"),
                classes="keys-row",
            ),
            Horizontal(
                Input(value=str(export_folder(self.storage)), placeholder=_("Folder (or a ccc-keys.json file)"),
                      id="keys-folder"),
                Button(_("Export"), id="keys-export"),
                Button(_("Import"), id="keys-import"),
                classes="keys-row",
            ),
            Horizontal(
                Static(_("Ctrl+S save  ·  Esc cancel"), classes="modal-hint"),
                Button(_("Cancel"), id="keys-cancel", classes="modal-cancel"),
                Button(_("Save"), id="keys-save", classes="modal-submit"),
                classes="modal-buttons",
            ),
        )
        box.border_title = _("Keyboard shortcuts")
        yield box

    def on_mount(self) -> None:
        # Keyboard stays in the list and the two fields (Enter there acts);
        # the buttons are for the mouse.
        for button in self.query(".keys-row Button"):
            button.can_focus = False
        table = self.query_one("#keys-table", DataTable)
        table.add_columns(_("Where"), _("Action"), _("Key"))
        for action in keymap.ACTIONS:
            table.add_row(_section(action.id), keymap.label(action), self._key_cell(action.id), key=action.id)
        table.focus()

    # --- helpers -----------------------------------------------------------

    def _key_cell(self, action_id: str) -> Text:
        key = self.keys[action_id]
        changed = key != keymap.BY_ID[action_id].default
        return Text(keymap.pretty(key), style="bold yellow" if changed else "")

    def selected(self) -> str | None:
        table = self.query_one("#keys-table", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def _status(self, text: str, style: str = "") -> None:
        self.query_one("#keys-status", Static).update(Text(text, style=style))

    def _refresh_row(self, action_id: str) -> None:
        table = self.query_one("#keys-table", DataTable)
        table.update_cell(action_id, table.ordered_columns[2].key, self._key_cell(action_id))

    def set_key(self, action_id: str, key: str) -> bool:
        """Give `action_id` the `key` if it's free; says why not otherwise."""
        action = keymap.BY_ID[action_id]
        found = keymap.problems(action_id, key, self.keys)
        if found:
            self._status(_("Not changed — “{action}”: {why}").format(action=keymap.label(action), why=" ".join(found)),
                         "bold red")
            return False
        self.keys[action_id] = key
        self._refresh_row(action_id)
        self._status(_("“{action}” is now {key} — free. Save to keep it.").format(
            action=keymap.label(action), key=keymap.pretty(key)), "green")
        return True

    def _set_all(self, keys: dict[str, str]) -> None:
        self.keys = dict(keys)
        for action in keymap.ACTIONS:
            self._refresh_row(action.id)

    # --- capturing a key -------------------------------------------------

    def _start_capture(self, action_id: str) -> None:
        self.capturing = True
        self.app.capturing_key = True
        self.set_focus(None)  # no widget may take the key (the list would)
        self._status(_("Press the new key for “{action}”… (any key, Esc too; click anywhere to stop)").format(
            action=keymap.label(keymap.BY_ID[action_id])), "bold yellow")

    def _stop_capture(self) -> None:
        self.capturing = False
        self.app.capturing_key = False
        self.query_one("#keys-table", DataTable).focus()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        return not self.capturing

    def on_key(self, event: events.Key) -> None:
        if not self.capturing:
            return
        event.stop()
        event.prevent_default()
        action_id = self.selected()
        self._stop_capture()
        if action_id is not None:
            self.set_key(action_id, event.key)

    def on_click(self, event: events.Click) -> None:
        if self.capturing:
            self._stop_capture()
            self._status(_("Nothing changed."))

    def on_unmount(self) -> None:
        self.app.capturing_key = False

    # --- buttons -------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self._start_capture(event.row_key.value)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        action = keymap.BY_ID[event.row_key.value]
        default = keymap.pretty(action.default)
        where = _("works from the Claude Code pane too") if action.tmux else ""
        self._status(" · ".join(p for p in (_("default: {key}").format(key=default), where) if p), "dim")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "keys-input":
            self._set_typed()
        elif event.input.id == "keys-folder":
            self._import()

    def _set_typed(self) -> None:
        action_id = self.selected()
        typed = self.query_one("#keys-input", Input).value
        if action_id is None or not typed.strip():
            return
        key = keymap.parse(typed)
        if key is None:
            self._status(_("“{key}” isn't a key ccc knows.").format(key=typed.strip()), "bold red")
            return
        if self.set_key(action_id, key):
            self.query_one("#keys-input", Input).value = ""

    def _export(self) -> None:
        folder = self.query_one("#keys-folder", Input).value.strip()
        if not folder:
            return
        try:
            path = keymap.export(self.keys, folder)
        except OSError as error:
            self._status(_("Export failed: {error}").format(error=error.strerror or error), "bold red")
            return
        self._status(_("Exported to {path}").format(path=path), "green")

    def _import(self) -> None:
        path = self.query_one("#keys-folder", Input).value.strip()
        if not path:
            return
        try:
            keys = keymap.read_export(path)
        except ValueError as error:
            self._status(str(error), "bold red")
            return
        found = keymap.check(keys)
        if found:
            lines = [f"{keymap.label(keymap.BY_ID[i])}: {' '.join(p)}" for i, p in found.items()]
            self._status(_("Not imported — the shortcuts in it collide:") + "\n" + "\n".join(lines), "bold red")
            return
        self._set_all(keys)
        self._status(_("Imported — Save to use them."), "green")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button.id
        action_id = self.selected()
        if button == "keys-set":
            self._set_typed()
        elif button == "keys-capture" and action_id is not None:
            self._start_capture(action_id)
        elif button == "keys-default" and action_id is not None:
            self.set_key(action_id, keymap.BY_ID[action_id].default)
        elif button == "keys-all-defaults":
            self._set_all(keymap.defaults())
            self._status(_("All shortcuts are back to their defaults — Save to keep it."), "green")
        elif button == "keys-export":
            self._export()
        elif button == "keys-import":
            self._import()
        elif button == "keys-save":
            self.action_save()
        elif button == "keys-cancel":
            self.action_cancel()

    def action_save(self) -> None:
        try:
            keymap.save(self.storage, self.keys)
        except keymap.KeymapError as error:
            self._status(_("Not saved:") + "\n" + str(error), "bold red")
            return
        if hasattr(self.app, "apply_keymap"):
            self.app.apply_keymap()
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
