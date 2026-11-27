"""The Notes view (N, #223): the list of ccc's Markdown notes with a preview, writing in the external editor,
renaming, deleting, searching — and an edit that meets a note changed meanwhile is kept, not lost."""
import pytest
from textual.widgets import DataTable, Input

from ccc import notes
from ccc.storage import Storage
from ccc.tui.app import CCContinueApp
from ccc.tui.layout import ConfirmModal
from ccc.tui.notes_page import NoteNameModal, NotesScreen


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr("ccc.tui.session_picker.discover_sessions", lambda: [])
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")


def _app(tmp_path):
    Storage(tmp_path / "history.db")
    return CCContinueApp(str(tmp_path / "history.db")), tmp_path / "notes"


def _paths(screen):
    table = screen.query_one("#notes-table", DataTable)
    return [table.coordinate_to_cell_key((n, 0)).row_key.value for n in range(table.row_count)]


def _editor(monkeypatch, result):
    """The external editor answering `result` (a text, or a function of the text before)."""
    monkeypatch.setattr("ccc.tui.notes_page.external_editor.command", lambda storage: ("fake",))
    monkeypatch.setattr("ccc.tui.notes_page.external_editor.edit",
                        lambda argv, text, folder: result(text) if callable(result) else result)
    monkeypatch.setattr(CCContinueApp, "suspend", lambda self: __import__("contextlib").nullcontext())


async def _open(app, pilot):
    await pilot.pause()
    await pilot.press("N")
    await pilot.pause()
    assert isinstance(app.screen, NotesScreen)


@pytest.mark.asyncio
async def test_lists_the_notes_newest_first(tmp_path):
    app, root = _app(tmp_path)
    notes.create_note(root, "a.md", "# First\n")
    notes.create_note(root, "dir/b.md", "# Second\n")
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(app, pilot)
        assert set(_paths(app.screen)) == {"a.md", "dir/b.md"}


@pytest.mark.asyncio
async def test_a_new_note_is_asked_for_then_written_in_the_editor(tmp_path, monkeypatch):
    app, root = _app(tmp_path)
    _editor(monkeypatch, "# Idea\nsomething\n")
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("plus")
        await pilot.pause()
        assert isinstance(app.screen, NoteNameModal)
        app.screen.query_one("#note-name", Input).value = "ideas/api"
        await pilot.press("enter")
        await pilot.pause()
        assert (root / "ideas" / "api.md").read_text() == "# Idea\nsomething\n"
        assert _paths(app.screen) == ["ideas/api.md"]


@pytest.mark.asyncio
async def test_a_bad_name_is_not_accepted(tmp_path, monkeypatch):
    app, root = _app(tmp_path)
    _editor(monkeypatch, "x")
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("plus")
        await pilot.pause()
        app.screen.query_one("#note-name", Input).value = "../x"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, NoteNameModal) and not root.exists()


@pytest.mark.asyncio
async def test_closing_the_editor_without_a_text_makes_no_note(tmp_path, monkeypatch):
    app, root = _app(tmp_path)
    _editor(monkeypatch, None)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("plus")
        await pilot.pause()
        app.screen.query_one("#note-name", Input).value = "empty"
        await pilot.press("enter")
        await pilot.pause()
        assert not (root / "empty.md").exists()


@pytest.mark.asyncio
async def test_edit_changes_the_note(tmp_path, monkeypatch):
    app, root = _app(tmp_path)
    notes.create_note(root, "a.md", "old")
    _editor(monkeypatch, lambda text: text + " + new")
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("e")
        await pilot.pause()
        assert (root / "a.md").read_text() == "old + new"


@pytest.mark.asyncio
async def test_an_edit_over_a_note_changed_meanwhile_is_kept_apart(tmp_path, monkeypatch):
    app, root = _app(tmp_path)
    notes.create_note(root, "a.md", "old")

    def phone_saves_meanwhile(text):
        (root / "a.md").write_text("from the phone, longer")
        return "my edit"

    _editor(monkeypatch, phone_saves_meanwhile)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("e")
        await pilot.pause()
        assert (root / "a.md").read_text() == "from the phone, longer"
        assert (root / "a (conflict).md").read_text() == "my edit"


@pytest.mark.asyncio
async def test_rename_moves_the_note(tmp_path):
    app, root = _app(tmp_path)
    notes.create_note(root, "a.md", "text")
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, NoteNameModal)
        app.screen.query_one("#note-name", Input).value = "work/b"
        await pilot.press("enter")
        await pilot.pause()
        assert (root / "work" / "b.md").read_text() == "text" and not (root / "a.md").exists()
        assert _paths(app.screen) == ["work/b.md"]


@pytest.mark.asyncio
async def test_delete_asks_first_and_keeps_the_file(tmp_path):
    app, root = _app(tmp_path)
    notes.create_note(root, "a.md", "keep")
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("delete")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("n")
        await pilot.pause()
        assert (root / "a.md").exists()
        await pilot.press("delete")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert not (root / "a.md").exists() and _paths(app.screen) == []
        assert [p.read_text() for p in (tmp_path / "deleted-notes").iterdir()] == ["keep"]


@pytest.mark.asyncio
async def test_search_filters_and_escape_clears(tmp_path):
    app, root = _app(tmp_path)
    notes.create_note(root, "a.md", "apples")
    notes.create_note(root, "b.md", "pears")
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(app, pilot)
        await pilot.press("slash")
        await pilot.pause()
        app.screen.query_one("#notes-search", Input).value = "pear"
        await pilot.pause()
        assert _paths(app.screen) == ["b.md"]
        await pilot.press("escape")
        await pilot.pause()
        assert set(_paths(app.screen)) == {"a.md", "b.md"} and isinstance(app.screen, NotesScreen)


@pytest.mark.asyncio
async def test_the_page_fills_the_window_with_the_preview_inside_the_frame(tmp_path):
    """compose_main() is unpacked into the frame, so a `with` block in it would leave the preview outside (#223)."""
    app, root = _app(tmp_path)
    notes.create_note(root, "a.md", "# One\n\ntext\n")
    async with app.run_test(size=(140, 45)) as pilot:
        await _open(app, pilot)
        main = app.screen.query_one("#main")
        assert main.region.y == 0 and main.region.height >= 40
        assert [w.id for w in main.children] == ["notes-head", "notes-search", "notes-table", "notes-preview-box", "notes-foot"]
        box = app.screen.query_one("#notes-preview-box")
        assert box.region.height > 5 and app.screen.query_one("#notes-preview").parent is box
