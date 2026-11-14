"""Interactive conversations show their Claude Code CLI in a tmux pane
next to ccc (tui.cli_pane is faked here)."""
import pytest
from textual.app import App
from textual.screen import Screen
from textual.widgets import DataTable

from ccc import managed_sessions
from ccc.models import DeliveryMode
from ccc.storage import Storage
from ccc.tui import cli_pane
from ccc.tui.conversations import ConversationsScreen
from ccc.tui.layout import ConfirmModal


class FakePane:
    def __init__(self):
        self.shown = None  # queue id shown in the pane, None = no pane
        self.bar = False  # the bar under it
        self.focused = 0
        self.closed = 0
        self.session_running = True  # Claude Code's own session

    def find(self):
        return ("%1", self.shown) if self.shown is not None else None

    def open(self, queue_id, title="", db_path=None):
        self.db_path = db_path
        self.shown = queue_id
        self.bar = self.bar or db_path is not None
        return "%1"

    def close(self):
        if self.shown is not None:
            self.closed += 1
        self.shown = None
        self.bar = False

    def find_bar(self):
        return "%2" if self.bar else None

    def focus(self):
        self.focused += 1


@pytest.fixture
def pane(monkeypatch, tmp_path):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    fake = FakePane()
    monkeypatch.setattr(cli_pane, "available", lambda: True)
    for name in ("find", "open", "close", "focus", "find_bar"):
        monkeypatch.setattr(cli_pane, name, getattr(fake, name))
    monkeypatch.setattr(managed_sessions, "is_running", lambda queue_id: fake.session_running)
    # A dialog zooms the window to ccc (tmux); not here.
    monkeypatch.setattr(cli_pane, "zoom_ccc", lambda: False)
    monkeypatch.setattr(cli_pane, "unzoom_ccc", lambda zoomed: None)
    return fake


@pytest.fixture
def storage(tmp_path):
    storage = Storage(tmp_path / "h.db")
    storage.create_queue("cli", str(tmp_path), "s-cli", delivery_mode=DeliveryMode.INTERACTIVE)
    storage.create_queue("headless", str(tmp_path), "s-headless")
    return storage


def _ids(storage):
    return {q.name: q.id for q in storage.list_queues()}


def _host(screen):
    class Host(App):
        async def on_mount(self):
            await self.push_screen(screen)

    return Host()


@pytest.mark.asyncio
async def test_pane_follows_the_selected_conversation(storage, pane):
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert pane.shown == ids["cli"]
        assert app.screen.has_class("-cli-open")
        assert pane.focused == 1  # just opened for it: keyboard goes to Claude
        await app.screen._show_queue(ids["headless"])
        assert pane.shown is None and not app.screen.has_class("-cli-open")
        await app.screen._show_queue(ids["cli"])
        await pilot.pause()  # the pane opens after the repaint (_sync_cli_pane's call_after_refresh)
        assert pane.shown == ids["cli"]
    assert pane.shown is None  # leaving the screen closes it


@pytest.mark.asyncio
async def test_enter_focuses_claude_and_closed_pane_stays_closed(storage, pane):
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.screen._show_queue(ids["cli"])
        pane.shown = None  # you pressed Ctrl+] in the pane
        await app.screen._refresh_current()
        assert pane.shown is None and not app.screen.has_class("-cli-open")
        await app.screen._refresh_current()
        assert pane.shown is None  # not reopened behind your back
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        table.focus()
        table.move_cursor(row=app.screen._queue_ids.index(ids["cli"]))
        focused = pane.focused
        await pilot.press("enter")
        await pilot.pause()
        assert pane.shown == ids["cli"] and pane.focused == focused + 1


@pytest.mark.asyncio
async def test_pane_lost_with_its_session_opens_again(storage, pane):
    """#267: Claude's session ended or restarted (not Ctrl+]): the pane
    comes back, the transcript doesn't stay on screen."""
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.screen._show_queue(ids["cli"])
        pane.shown = None
        pane.session_running = False
        await app.screen._refresh_current()
        await pilot.pause()
        assert pane.shown == ids["cli"] and app.screen.has_class("-cli-open")


@pytest.mark.asyncio
async def test_dialog_on_top_keeps_the_chat_and_zooms_ccc(storage, pane, monkeypatch):
    """#235: a dialog doesn't change the chat behind it; it gets the whole
    window by zooming ccc (the pane is hidden by tmux, not closed)."""
    zoom = []
    monkeypatch.setattr(cli_pane, "zoom_ccc", lambda: zoom.append("zoom") or True)
    monkeypatch.setattr(cli_pane, "unzoom_ccc", lambda zoomed: zoomed and zoom.append("unzoom"))
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert pane.shown == ids["cli"]
        app.push_screen(ConfirmModal("t", "x"))
        await pilot.pause()
        conversations = next(s for s in app.screen_stack if isinstance(s, ConversationsScreen))
        assert pane.shown == ids["cli"] and conversations.has_class("-cli-open")
        assert zoom == ["zoom"]
        await conversations._refresh_current()  # the 2-second refresh, under the dialog
        assert pane.shown == ids["cli"] and pane.closed == 0
        await pilot.press("escape")
        await pilot.pause()
        assert pane.shown == ids["cli"] and zoom == ["zoom", "unzoom"]


@pytest.mark.asyncio
async def test_another_view_on_top_closes_the_pane(storage, pane):
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert pane.shown == ids["cli"]
        await app.push_screen(Screen())  # not a dialog
        await pilot.pause()
        assert pane.shown is None


@pytest.mark.asyncio
async def test_outside_tmux_enter_opens_full_screen(storage, monkeypatch, tmp_path):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(cli_pane, "available", lambda: False)
    opened = []
    monkeypatch.setattr(ConversationsScreen, "_open_cli_full_screen", lambda self, queue_id: opened.append(queue_id))
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        table.focus()
        table.move_cursor(row=app.screen._queue_ids.index(ids["cli"]))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert opened == [ids["cli"]]
        # A headless conversation: Enter goes to the message box as before.
        table.move_cursor(row=app.screen._queue_ids.index(ids["headless"]))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert opened == [ids["cli"]]
        assert app.focused.id == "message-input"


@pytest.mark.asyncio
async def test_alt_digit_types_preset_into_claude(storage, pane, monkeypatch):
    applied = []
    monkeypatch.setattr("ccc.cli_input.apply_preset", lambda s, q, n: applied.append((q, n)) or None)
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("alt+2")
        await pilot.pause()
        assert applied == [(ids["cli"], 2)]
        assert app.screen.query_one("Footer").display  # ccc's frame stays the same in every view


@pytest.mark.asyncio
async def test_menu_keeps_its_size_with_claude_open(storage, pane):
    # Key hints live in the bar above Claude Code, never in the menu.
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["headless"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#conversation-queue-list")
        closed = table.region
        await app.screen._show_queue(ids["cli"])
        await pilot.pause()
        assert app.screen.has_class("-cli-open")
        assert table.region == closed
        assert not app.screen.query("#cli-hint")


@pytest.mark.asyncio
async def test_archiving_closes_the_bar_left_by_the_ended_pane(storage, pane):
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert pane.shown == ids["cli"] and pane.bar
        app.screen.action_toggle_archive()
        await pilot.press("y")  # asks first
        pane.shown = None  # the daemon stopped Claude: its pane ended
        await app.screen._refresh_current()
        await pilot.pause()
        assert not pane.bar  # no bar left over the whole column
        assert app.screen._current_queue_id == ids["headless"]  # the next one
        assert not app.screen.has_class("-no-conversation")


@pytest.mark.asyncio
async def test_archive_view_shows_an_archived_interactive_transcript(storage, pane):
    from ccc.tui.archive import ArchiveScreen

    ids = _ids(storage)
    storage.set_queue_archived(ids["cli"], True)
    app = _host(ArchiveScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert pane.shown is None  # nothing runs for an archived one
        assert not app.screen.has_class("-no-conversation")
        assert app.screen.query_one("#conversation-transcript").display
        assert not app.screen.query_one("#message-input").display
        header = str(app.screen.query_one("#conversation-header").render())
        assert "x restores it" in header and "Claude Code CLI" not in header


@pytest.mark.asyncio
async def test_archiving_the_last_conversation_shows_no_conversation(storage, pane):
    ids = _ids(storage)
    storage.set_queue_archived(ids["headless"], True)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert pane.shown == ids["cli"] and not app.screen.has_class("-no-conversation")
        app.screen.action_toggle_archive()  # the list is empty now
        await pilot.press("y")
        pane.shown = None  # the daemon stopped Claude: its pane ended
        await app.screen._refresh_current()
        await pilot.pause()
        assert app.screen._current_queue_id is None
        assert not pane.bar
        assert app.screen.has_class("-no-conversation")
        banner = app.screen.query_one("#no-conversation")
        assert banner.display and banner.region.width > 0
        assert "█" in str(banner.render())
        # Only the banner in the main column (the presets row too is hidden).
        main = app.screen.query_one("#main")
        assert [w.id for w in main.children if w.display] == ["no-conversation"]


@pytest.mark.asyncio
async def test_restore_from_archive_keeps_the_claude_pane_open(storage, pane):
    from ccc.tui.archive import ArchiveScreen

    ids = _ids(storage)
    storage.set_queue_archived(ids["cli"], True)
    app = _host(ArchiveScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen.action_restore_and_open()
        for _ in range(5):
            await pilot.pause()
        assert type(app.screen) is ConversationsScreen
        # The Archive screen going away must not close Conversations' pane.
        assert pane.shown == ids["cli"] and pane.closed == 0
        await app.screen._refresh_current()
        assert pane.shown == ids["cli"]


@pytest.mark.asyncio
async def test_claude_keys_are_in_the_key_strip_while_claude_is_open(storage, pane, monkeypatch):
    from ccc.tui.footer_strip import footer_items

    ids = _ids(storage)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["headless"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        keys = [shown for shown, _, _ in footer_items(app)]
        assert "Alt+0" not in keys and "Ctrl+]" not in keys
        await app.screen._show_queue(ids["cli"])
        await pilot.pause()
        items = {shown: key for shown, _, key in footer_items(app)}
        assert items["Alt+0"] == "M-0" and items["Ctrl+]"] == "C-]"
        focused = pane.focused
        app.screen.action_focus_claude()
        assert pane.focused == focused + 1
        app.screen.action_close_claude()
        await app.screen._refresh_current()
        assert pane.shown is None  # and it stays closed
        assert "Alt+0" not in [shown for shown, _, _ in footer_items(app)]


@pytest.mark.asyncio
async def test_app_start_closes_a_pane_left_by_the_previous_process(storage, pane, monkeypatch):
    from ccc.tui.app import CCContinueApp
    from ccc.tui.dashboard import DashboardScreen

    monkeypatch.setattr(cli_pane, "style_window", lambda keys=None: None)
    monkeypatch.setattr(cli_pane, "find_strip", lambda: None)
    pane.shown, pane.bar = _ids(storage)["cli"], True  # left by a killed ccc
    app = CCContinueApp(str(storage.db_path))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        assert pane.shown is None and pane.bar is False


@pytest.mark.asyncio
async def test_archiving_asks_first_and_no_keeps_it(storage, pane):
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen.action_toggle_archive()
        await pilot.pause()
        assert not storage.get_queue(ids["cli"]).archived  # nothing yet
        await pilot.press("n")
        await pilot.pause()
        assert not storage.get_queue(ids["cli"]).archived


@pytest.mark.asyncio
async def test_stop_claude_asks_then_flags_and_ends_it(storage, pane, monkeypatch):
    ended = []
    monkeypatch.setattr(managed_sessions, "end", lambda queue_id: ended.append(queue_id))
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen.action_stop_claude()
        await pilot.pause()
        assert not storage.get_queue(ids["cli"]).claude_stopped and not ended  # asks first
        await pilot.press("y")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert storage.get_queue(ids["cli"]).claude_stopped
        assert ended == [ids["cli"]]


@pytest.mark.asyncio
async def test_stop_claude_no_changes_nothing(storage, pane, monkeypatch):
    ended = []
    monkeypatch.setattr(managed_sessions, "end", lambda queue_id: ended.append(queue_id))
    ids = _ids(storage)
    app = _host(ConversationsScreen(storage, select_queue_id=ids["cli"]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen.action_stop_claude()
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert not storage.get_queue(ids["cli"]).claude_stopped and not ended
