import pytest
from textual.widgets import Button, Input, TabbedContent

from ccc.storage import Storage
from ccc.tui.analytics import AnalyticsScreen
from ccc.tui.app import CCContinueApp
from ccc.tui.conversations import ConversationsScreen
from ccc.tui.dashboard import DashboardScreen
from ccc.tui.layout import SidebarScreen
from ccc.tui.queue_detail import AddPromptModal, QueueDetailScreen
from ccc.tui.session_picker import SessionPickerScreen


@pytest.fixture(autouse=True)
def _no_external_calls(monkeypatch, tmp_path):
    # Sessions view shells out to `claude agents`, analytics hits the usage
    # API, conversations reads ~/.claude transcripts — keep tests offline.
    monkeypatch.setattr("ccc.tui.session_picker.discover_sessions", lambda: [])
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")


def _app(tmp_path):
    db_path = tmp_path / "history.db"
    storage = Storage(db_path)
    storage.create_queue("proj-a", "/tmp/proj-a", "sess-a")
    return CCContinueApp(str(db_path))


def _views_above_home(app):
    return [type(s) for s in app.screen_stack if isinstance(s, SidebarScreen)]


@pytest.mark.asyncio
async def test_hotkeys_switch_views_without_growing_the_stack(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)

        for key, expected in [
            ("c", ConversationsScreen),
            ("a", AnalyticsScreen),
            ("s", SessionPickerScreen),
        ]:
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.screen, expected)
            # home + exactly one view on top, no matter how many switches
            assert _views_above_home(app) == [DashboardScreen, expected]

        await pilot.press("d")
        await pilot.pause()
        assert _views_above_home(app) == [DashboardScreen]


@pytest.mark.asyncio
async def test_every_view_highlights_its_own_nav_item(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        for key, view in [("d", "dashboard"), ("c", "conversations"), ("s", "sessions"), ("a", "analytics")]:
            await pilot.press(key)
            await pilot.pause()
            active = [w.id for w in app.screen.query(".nav-button.-current")]
            assert active == [f"nav-{view}"]


@pytest.mark.asyncio
async def test_clicking_nav_item_switches_view(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#nav-analytics")
        await pilot.pause()
        assert isinstance(app.screen, AnalyticsScreen)


@pytest.mark.asyncio
async def test_escape_returns_to_dashboard_but_never_leaves_it(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_queue_detail_nav_goes_home(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, QueueDetailScreen)
        await pilot.press("d")
        await pilot.pause()
        assert _views_above_home(app) == [DashboardScreen]


@pytest.mark.asyncio
async def test_sidebar_buttons_stay_out_of_focus_chain(tmp_path):
    # Otherwise the first button grabs initial focus and Enter on the
    # dashboard opens "Add prompt" instead of the selected queue.
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")  # the dashboard has no buttons
        await pilot.pause()
        buttons = list(app.screen.query("#sidebar Button.sidebar-button"))
        assert buttons
        assert not any(b.can_focus for b in buttons)
        assert not isinstance(app.focused, Button)


@pytest.mark.asyncio
async def test_sidebar_buttons_are_only_the_views_own_actions(tmp_path):
    # Switching views is the nav list's job — no "Live sessions" button,
    # and no "Add prompt" on screens without a selected project.
    app = _app(tmp_path)
    expected = {
        "d": [],  # only an overview
        "s": ["Add prompt (+)", "Reload session list (r)"],
        "a": [],
        "h": ["f Filter", "F2 Rename"],  # Archive: nothing to start from there, only the filter
        "c": ["f Filter", "F2 Rename", "New conversation (n)", "Archive (x)", "Retry blocked queue (r)"],
    }
    async with app.run_test() as pilot:
        await pilot.pause()
        for key, labels in expected.items():
            await pilot.press(key)
            await pilot.pause()
            assert [str(b.label) for b in app.screen.query("#sidebar Button.sidebar-button")] == labels, key


@pytest.mark.asyncio
async def test_add_prompt_prefills_queue_detail_path(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, QueueDetailScreen)
        await pilot.press("plus")
        await pilot.pause()
        assert isinstance(app.screen, AddPromptModal)
        assert app.screen.query_one("#add-prompt-path", Input).value == "/tmp/proj-a"
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, QueueDetailScreen)


@pytest.mark.asyncio
async def test_plus_works_only_in_views_with_add_prompt(tmp_path):
    # Keys belong to the view that uses them: "+" is Live sessions' and
    # queue detail's Add prompt, nothing on the other views.
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        for view, screen_type in (("d", DashboardScreen), ("a", AnalyticsScreen), ("c", ConversationsScreen)):
            await pilot.press(view)
            await pilot.pause()
            if screen_type is ConversationsScreen:
                app.screen.set_focus(None)
            await pilot.press("plus")
            await pilot.pause()
            assert isinstance(app.screen, screen_type), view
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("plus")
        await pilot.pause()
        assert isinstance(app.screen, AddPromptModal)


@pytest.mark.asyncio
async def test_n_opens_new_conversation_and_lands_in_conversations(tmp_path):
    from ccc.tui.new_conversation import NewConversationModal
    app = _app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, NewConversationModal)
        app.screen.query_one("#launch-path").value = str(tmp_path)
        await pilot.click("#launch-start")
        await pilot.pause()
        assert isinstance(app.screen, ConversationsScreen)
        newest = max(q.id for q in app.screen.storage.list_queues())
        assert app.screen._current_queue_id == newest


@pytest.mark.asyncio
async def test_alt_s_takes_a_screenshot_on_any_view(tmp_path, monkeypatch):
    # Not Ctrl+T: that's Claude Code's to-do list (#31).
    taken = []
    monkeypatch.setattr(CCContinueApp, "action_take_screenshot", lambda self: taken.append(type(self.screen)))
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        for view in ("d", "a", "c"):
            await pilot.press(view)
            await pilot.pause()
            await pilot.press("alt+s")
            await pilot.pause()
    assert taken == [DashboardScreen, AnalyticsScreen, ConversationsScreen]


@pytest.mark.asyncio
async def test_buttons_keep_size_and_colour_on_hover_focus_and_click(tmp_path):
    # #55: Textual's default turned a clicked button black and shorter.
    from textual.color import Color
    from textual.widgets import Button

    from ccc.tui.app import CCContinueApp
    from ccc.tui.dashboard import DashboardScreen
    from ccc.tui.settings import SettingsModal

    class Host(CCContinueApp):
        def initial_screens(self):
            return [DashboardScreen(self.storage), SettingsModal(self.storage)]

    app = Host(str(tmp_path / "h.db"))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one(TabbedContent).active = "settings-tab-notifications"
        await pilot.pause()
        button = app.screen.query_one("#settings-telegram-test", Button)
        button.scroll_visible(animate=False)
        await pilot.pause()
        normal = (button.region.height, button.styles.background)
        assert normal[1] == Color.parse("#3a3a3a")
        assert normal[0] == 3
        for state in ("hover", "focus", "active"):
            if state == "hover":
                await pilot.hover("#settings-telegram-test")
            elif state == "focus":
                button.focus()
            else:
                button.add_class("-active")
            await pilot.pause()
            assert (button.region.height, button.styles.background) == normal, state
            assert button.styles.border_top[0] in ("", "none"), state
        assert app.screen.query_one("#settings-save", Button).styles.background == Color.parse("#00af5f")
        # Cancel is grey, as in the design (#250); red is for deleting.
        assert app.screen.query_one("#settings-cancel", Button).styles.background == Color.parse("#3a3a3a")


def test_button_colours_stay_distinct_in_a_256_colour_terminal():
    # #76: in tmux Textual rounds to the 256-colour palette, and the old
    # teal and green both became #00875f — two sidebar buttons looked like one.
    import re

    from rich.color import Color, ColorSystem

    from ccc.tui.layout import BUTTON_CSS

    colours = re.findall(r"^\$ccc-button[\w-]*: (#[0-9a-f]{6});", BUTTON_CSS, re.M)
    assert len(colours) == 3
    downgraded = {Color.parse(c).downgrade(ColorSystem.EIGHT_BIT).number for c in colours}
    assert len(downgraded) == 3
    standard = {Color.parse(c).downgrade(ColorSystem.STANDARD).number for c in colours}
    assert len(standard) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [24, 40, 50])
async def test_menu_items_are_all_the_same_size_the_open_view_marked_by_an_arrow(tmp_path, rows):
    # The open view had a white frame that took a column on each side: it looked narrower.
    app = _app(tmp_path)
    async with app.run_test(size=(140, rows)) as pilot:
        await pilot.pause()
        for key in ("d", "a"):
            await pilot.press(key)
            await pilot.pause()
            buttons = list(app.screen.query(".nav-button"))
            assert len({(b.region.width, b.region.height, b.content_region.width) for b in buttons}) == 1, key
            marked = [b.id for b in buttons if str(b.render()).lstrip().startswith(">")]
            assert marked == [f"nav-{ {'d': 'dashboard', 'a': 'analytics'}[key]}"]
