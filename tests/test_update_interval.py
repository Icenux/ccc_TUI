from datetime import datetime, timedelta

from ccc import updates
from ccc.storage import Storage


def test_updates_are_looked_for_every_24_hours_counted_from_the_last_look(tmp_path):
    # #306: once a day, also across daemon restarts.
    storage = Storage(tmp_path / "h.db")
    assert updates.CHECK_SECONDS == 24 * 3600
    assert updates.seconds_until_next_check(storage) == 0
    now = datetime(2026, 11, 12, 12, 0, 0)
    updates.save(storage, updates.UpdateState(checked_at=(now - timedelta(hours=20)).isoformat(timespec="seconds")))
    assert updates.seconds_until_next_check(storage, now) == 4 * 3600
    updates.save(storage, updates.UpdateState(checked_at=(now - timedelta(hours=30)).isoformat(timespec="seconds")))
    assert updates.seconds_until_next_check(storage, now) == 0


def test_the_daemon_waits_for_the_next_daily_look(tmp_path, monkeypatch):
    import asyncio

    from ccc import daemon

    storage = Storage(tmp_path / "h.db")
    updates.save(storage, updates.UpdateState(
        checked_at=(datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")))
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(daemon.asyncio, "sleep", sleep)
    try:
        asyncio.run(daemon.update_loop(storage, notifier=None))
    except asyncio.CancelledError:
        pass
    assert 22.9 * 3600 < slept[0] <= 23 * 3600  # not 2 minutes after a restart
