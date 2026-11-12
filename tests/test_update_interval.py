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
