import asyncio
from datetime import datetime, timedelta

import pytest
from textual.app import App

from ccc.daemon import heartbeat_loop
from ccc.heartbeat import HEARTBEAT_KEY, daemon_alive
from ccc.storage import Storage
from ccc.tui.dashboard import DashboardScreen


def test_daemon_alive_reads_recent_heartbeat(tmp_path):
    storage = Storage(tmp_path / "h.db")
    now = datetime(2026, 10, 13, 12, 0, 0)
    assert daemon_alive(storage, now) is False
    storage.set_setting(HEARTBEAT_KEY, (now - timedelta(seconds=5)).isoformat())
    assert daemon_alive(storage, now) is True
    storage.set_setting(HEARTBEAT_KEY, (now - timedelta(seconds=100)).isoformat())
    assert daemon_alive(storage, now) is False
    storage.set_setting(HEARTBEAT_KEY, "garbage")
    assert daemon_alive(storage, now) is False


async def test_heartbeat_loop_writes_periodically(tmp_path):
    storage = Storage(tmp_path / "h.db")
    task = asyncio.create_task(heartbeat_loop(storage, interval=0.02))
    await asyncio.sleep(0.1)
    task.cancel()
    assert daemon_alive(storage, datetime.now())


def _host(screen):
    class Host(App):
        async def on_mount(self):
            await self.push_screen(screen)

    return Host()


@pytest.mark.asyncio
async def test_sidebar_shows_daemon_state(tmp_path):
    storage = Storage(tmp_path / "h.db")
    app = _host(DashboardScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "daemon not running" in str(app.screen.query_one("#daemon-state").render())
        storage.set_setting(HEARTBEAT_KEY, datetime.now().isoformat())
        app.screen.refresh_daemon_state()
        assert "daemon running" in str(app.screen.query_one("#daemon-state").render())


def test_the_heartbeat_stamps_the_version_the_daemon_runs(tmp_path, monkeypatch):
    from ccc import heartbeat

    storage = Storage(tmp_path / "h.db")
    monkeypatch.setattr("ccc.__version__", "0.3.0")
    heartbeat.beat(storage)
    assert storage.get_setting(heartbeat.VERSION_KEY) == "0.3.0"
