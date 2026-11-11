import pytest
from textual.app import App
from textual.widgets import DataTable

from ccc.storage import Storage
from ccc.tui.conversations import ConversationsScreen
from ccc.tui.dashboard import DashboardScreen
from ccc.tui.permissions import PermissionModal


def _host(screen):
    class Host(App):
        async def on_mount(self):
            await self.push_screen(screen)

    return Host()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    return Storage(tmp_path / "h.db")


async def _open_with_request(storage):
    q = storage.create_queue("conv", "/tmp/c", "s1")
    req = storage.create_permission_request(q.id, "Bash", {"command": "rm -rf build/ [x]"})
    app = _host(ConversationsScreen(storage, select_queue_id=q.id))
    return q, req, app


@pytest.mark.asyncio
@pytest.mark.parametrize("key,status,always", [("y", "allowed", False), ("a", "allowed", True), ("n", "denied", False)])
async def test_p_opens_request_and_keys_decide(storage, key, status, always):
    q, req, app = await _open_with_request(storage)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert "approval" in str(app.screen.query_one("#conversation-status").render())
        names = [str(app.screen.query_one("#conversation-queue-list", DataTable).get_row(k)[0])
                 for k in app.screen.query_one("#conversation-queue-list", DataTable).rows]
        assert names[0].startswith("● conv")
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, PermissionModal)
        assert "rm -rf build/ [x]" in str(app.screen.query_one("#permission-input").render())
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, ConversationsScreen)
    assert storage.get_permission_request(req.id).status == status
    assert storage.is_tool_always_allowed(q.id, "Bash") is always


@pytest.mark.asyncio
async def test_escape_leaves_request_pending(storage):
    q, req, app = await _open_with_request(storage)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert storage.get_permission_request(req.id).status == "pending"


@pytest.mark.asyncio
async def test_p_without_request_notifies(storage):
    storage.create_queue("conv", "/tmp/c", "s1")
    app = _host(ConversationsScreen(storage))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, ConversationsScreen)


@pytest.mark.asyncio
async def test_dashboard_marks_waiting_for_approval(storage):
    q = storage.create_queue("conv", "/tmp/c", "s1")
    storage.create_permission_request(q.id, "Write", {"file_path": "/x"})
    app = _host(DashboardScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#dashboard-table", DataTable)
        assert str(table.get_row_at(0)[0]).startswith("● conv")
