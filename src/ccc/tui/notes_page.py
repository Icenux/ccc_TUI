"""The Notes view (N): ccc's own notes (#223), Markdown files in ccc's folder (ccc.notes) that the Android app reads
and writes through the API. A list on top, the selected note's text rendered below it.

Notes are written in the external editor chosen in Settings (ccc.external_editor): ccc steps aside, and what is saved
comes back as the note. A note changed meanwhile (say, from the phone) is never overwritten: the edit is kept as a
note of its own next to it. + new, e / Enter edit, r rename or move, Del delete, / search."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual.containers import Container, VerticalScroll
from textual.widgets import DataTable, Input, Markdown, Static

from ccc import external_editor, keymap, notes
from ccc.export_paths import home_short
from ccc.i18n import _
from ccc.tui import theme
from ccc.tui.layout import MODAL_CSS, CccModal, ConfirmModal, SidebarScreen

REFRESH_SECONDS = 2.0


def with_extension(raw: str) -> str:
    """What was typed as a note's path, with .md when it has no extension."""
    raw = raw.strip()
    return raw if raw.lower().endswith(".md") or not raw else raw + ".md"


class NoteNameModal(CccModal):
    """Asks for a note's path (folders with /, the name ends in .md — added when missing). Dismisses the clean path,
    or None when cancelled."""

    DEFAULT_CSS = MODAL_CSS.format(cls="NoteNameModal")
    BINDINGS = [keymap.binding("window.cancel", "cancel", _("Cancel"))]

    def __init__(self, title: str, value: str = ""):
        super().__init__()
        self.box_title = title
        self.value = value

    def compose(self):
        box = Container(
            Static(_("Name (a folder can come first: ideas/api)"), classes="modal-label"),
            Input(value=self.value, id="note-name"),
            Static("", id="note-name-error", classes="modal-label"),
        )
        box.border_title = self.box_title
        yield box

    def on_mount(self) -> None:
        self.query_one("#note-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        try:
            self.dismiss(notes.clean_path(with_extension(event.value)))
        except notes.NoteError as error:
            self.query_one("#note-name-error", Static).update(Text(error.message, style=theme.ERROR))

    def action_cancel(self) -> None:
        self.dismiss(None)


class SearchInput(Input):
    """The search box: Esc empties it and hands the keys back to the list."""

    async def _on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.value = ""
            self.screen.query_one("#notes-table", DataTable).focus()
            return
        await super()._on_key(event)


class NotesScreen(SidebarScreen):
    VIEW = "notes"
    SUB_TITLE = _("Notes")

    BINDINGS = [
        keymap.binding("screen.back", "back", _("Back"), show=False),
        keymap.binding("notes.add", "add", _("New")),
        keymap.binding("notes.edit", "edit", _("Edit")),
        keymap.binding("notes.rename", "rename", _("Rename")),
        keymap.binding("notes.delete", "delete", _("Delete")),
        keymap.binding("notes.search", "search", _("Search")),
    ]

    DEFAULT_CSS = """
    NotesScreen #main {
        padding: 0 1;
    }
    NotesScreen #notes-head {
        height: 2;
        padding: 1 0 0 0;
        color: $ccc-text-muted;
        text-style: bold;
    }
    NotesScreen #notes-search {
        display: none;
        height: 3;
    }
    NotesScreen #notes-search.-shown {
        display: block;
    }
    NotesScreen #notes-table {
        height: 40%;
        background: transparent;
    }
    NotesScreen #notes-table > .datatable--cursor {
        background: $ccc-selection;
        color: $ccc-accent;
        text-style: bold;
    }
    NotesScreen #notes-preview-box {
        height: 1fr;
        border-top: solid $ccc-line;
        padding: 0 1;
    }
    NotesScreen #notes-foot {
        height: auto;
        color: $ccc-text-muted;
    }
    """

    def __init__(self, storage):
        super().__init__(storage)
        self._rows: list[dict] = []
        self._shown = ""  # path and version of the note in the preview

    @property
    def root(self) -> Path:
        return notes.notes_dir(self.storage.db_path)

    def compose_main(self):
        yield Static(id="notes-head")
        yield SearchInput(placeholder=_("Search the notes"), id="notes-search")
        yield DataTable(id="notes-table")
        with VerticalScroll(id="notes-preview-box"):
            yield Markdown(id="notes-preview")
        yield Static(id="notes-foot")

    def on_mount(self) -> None:
        table = self.query_one("#notes-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = False
        for title in (_("NOTE"), _("PATH"), _("CHANGED")):
            table.add_column(title)
        self.refresh_page()
        table.focus()
        self.set_interval(REFRESH_SECONDS, self.refresh_page)

    # --- the list --------------------------------------------------------

    def _query(self) -> str:
        return self.query_one("#notes-search", Input).value

    def selected_path(self) -> str | None:
        table = self.query_one("#notes-table", DataTable)
        if not self._rows or not 0 <= table.cursor_row < len(self._rows):
            return None
        return self._rows[table.cursor_row]["path"]

    def refresh_page(self, select: str | None = None) -> None:
        found = notes.list_notes(self.root, self._query())
        if select is None and [(n["path"], n["version"]) for n in found] == [(n["path"], n["version"]) for n in self._rows]:
            return  # nothing changed: the cursor and the preview stay as they are
        table = self.query_one("#notes-table", DataTable)
        keep = select or self.selected_path()
        row = table.cursor_row
        table.clear()
        self._rows = found
        for note in found:
            changed = datetime.fromisoformat(note["modified"]).strftime("%Y-%m-%d %H:%M")
            table.add_row(note["title"], Text(note["path"], style=theme.TEXT_MUTED), changed, key=note["path"])
        paths = [n["path"] for n in found]
        if keep in paths:
            table.move_cursor(row=paths.index(keep))
        elif found:
            table.move_cursor(row=min(row, len(found) - 1))
        self._show_preview()
        keys = self._keys()
        self.query_one("#notes-head", Static).update(Text.assemble(
            (_("NOTES"), "bold"), "   ", (_("{n} in {folder}").format(n=len(found), folder=home_short(self.root)), theme.TEXT_MUTED)))
        self.query_one("#notes-foot", Static).update(Text.assemble(
            ("↑↓", "bold"), " ", _("move"), "     ", ("↵", "bold"), " ", _("edit"), "     ",
            (keymap.pretty(keys["notes.add"]), "bold"), " ", _("new"), "     ",
            (keymap.pretty(keys["notes.search"]), "bold"), " ", _("search"), style=theme.TEXT_MUTED))

    def _show_preview(self) -> None:
        path = self.selected_path()
        preview = self.query_one("#notes-preview", Markdown)
        if path is None:
            self._shown = ""
            hint = _("No notes yet — {key} writes the first one.") if not self._query() else _("No note matches.")
            preview.update(hint.format(key=keymap.pretty(self._keys()["notes.add"])))
            return
        note = next(n for n in self._rows if n["path"] == path)
        marker = f"{path}\0{note['version']}"
        if marker == self._shown:
            return
        self._shown = marker
        try:
            preview.update(notes.read_note(self.root, path)["content"])
        except notes.NoteError as error:
            preview.update(error.message)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_preview()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_edit()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "notes-search":
            self.refresh_page(select=self.selected_path() or "")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "notes-search":
            self.query_one("#notes-table", DataTable).focus()

    def action_search(self) -> None:
        box = self.query_one("#notes-search", Input)
        box.add_class("-shown")
        box.focus()

    def action_back(self) -> None:
        box = self.query_one("#notes-search", Input)
        if box.has_class("-shown") and box.value:
            box.value = ""
            return
        box.remove_class("-shown")
        super().action_back()

    # --- writing ---------------------------------------------------------

    def _edit(self, text: str) -> str | None:
        """The text after the editor (None: unchanged, or no editor to be had)."""
        argv = external_editor.command(self.storage)
        if argv is None:
            self.notify(_("No editor found — install one (e.g. nano) or set $EDITOR."), severity="warning")
            return None
        try:
            with self.app.suspend():
                return external_editor.edit(argv, text, self.root.parent)
        except Exception as exc:  # SuspendNotSupported (textual serve), the editor failing to start
            self.notify(_("Can't open the editor here ({error}).").format(error=type(exc).__name__), severity="error")
            return None

    def action_add(self) -> None:
        self.app.push_screen(NoteNameModal(_("New note")), self._create)

    def _create(self, path: str | None) -> None:
        if not path:
            return
        text = self._edit("")
        if text is None:
            return
        try:
            notes.create_note(self.root, path, text)
        except notes.NoteError as error:
            self.notify(error.message, severity="error")
            return
        self.refresh_page(select=path)

    def action_edit(self) -> None:
        path = self.selected_path()
        if path is None:
            return
        try:
            note = notes.read_note(self.root, path)
        except notes.NoteError as error:
            self.notify(error.message, severity="error")
            return
        text = self._edit(note["content"])
        if text is None:
            return
        try:
            notes.update_note(self.root, path, text, note["version"])
        except notes.NoteError as error:
            if error.kind != notes.CONFLICT:
                self.notify(error.message, severity="error")
                return
            self._keep_apart(path, text)
        self.refresh_page(select=path)

    def _keep_apart(self, path: str, text: str) -> None:
        """The note changed while it was being edited: the edit becomes a note of its own, nothing is lost."""
        stem = path[:-3]
        for number in range(1, 100):
            copy = f"{stem} (conflict{'' if number == 1 else ' ' + str(number)}).md"
            try:
                notes.create_note(self.root, copy, text)
            except notes.NoteError as error:
                if error.kind == notes.EXISTS:
                    continue
                self.notify(error.message, severity="error")
                return
            self.notify(_("The note changed meanwhile. Your edit is saved as {name}.").format(name=copy), severity="warning")
            return

    def action_rename(self) -> None:
        path = self.selected_path()
        if path is None:
            return

        def renamed(new: str | None) -> None:
            if not new or new == path:
                return
            try:
                notes.rename_note(self.root, path, new)
            except notes.NoteError as error:
                self.notify(error.message, severity="error")
                return
            self.refresh_page(select=new)

        self.app.push_screen(NoteNameModal(_("Rename note"), path[:-3]), renamed)

    def action_delete(self) -> None:
        path = self.selected_path()
        if path is None:
            return

        def answered(yes: bool | None) -> None:
            if not yes:
                return
            try:
                notes.delete_note(self.root, path)
            except notes.NoteError as error:
                self.notify(error.message, severity="error")
                return
            self.refresh_page(select="")

        self.app.push_screen(ConfirmModal(_("Delete note"), _("Delete the note “{name}”? The file moves to ccc's folder "
                                                              "deleted-notes, it is not erased.").format(name=path)), answered)

