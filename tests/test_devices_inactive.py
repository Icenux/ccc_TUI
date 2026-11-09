import sqlite3
from datetime import datetime, timedelta

from ccc import api, watch
from ccc.models import INACTIVE_DAYS, REVOKED_BY_INACTIVE, STREAM_DROPPED_MINUTES

from test_api import storage  # noqa: F401  (fixture)


def _seen(storage, device_id: int, days_ago: int | None) -> None:
    moment = None if days_ago is None else (datetime.now() - timedelta(days=days_ago)).isoformat()
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute("UPDATE devices SET last_seen_at = ?, created_at = ? WHERE id = ?",
                     (moment, (datetime.now() - timedelta(days=100)).isoformat(), device_id))


def test_a_device_not_seen_for_long_moves_to_the_history(storage):  # noqa: F811
    quiet = api.add_device(storage, "Uninstalled")[0]
    active = api.add_device(storage, "Active")[0]
    _seen(storage, quiet.id, INACTIVE_DAYS + 1)
    _seen(storage, active.id, 1)
    assert storage.revoke_inactive_devices(INACTIVE_DAYS, REVOKED_BY_INACTIVE) == [quiet.id]
    assert [d.name for d in storage.list_devices()] == ["Active"]
    gone = storage.device_by_id(quiet.id)
    assert gone.revoked_by == REVOKED_BY_INACTIVE and gone.revoked_at is not None
    assert storage.revoke_inactive_devices(INACTIVE_DAYS, REVOKED_BY_INACTIVE) == []


def test_a_device_never_seen_counts_from_its_pairing(storage):  # noqa: F811
    device = api.add_device(storage, "Never")[0]
    _seen(storage, device.id, None)
    assert storage.revoke_inactive_devices(INACTIVE_DAYS, REVOKED_BY_INACTIVE) == [device.id]


def test_the_watch_signs_out_the_silent_device_and_tells_it(storage):  # noqa: F811
    device = api.add_device(storage, "Phone")[0]
    api.add_device(storage, "Other")
    sent = []

    class Notifier:
        def send_now(self, text, category):
            sent.append(text)

    watch.devices_tick(storage, Notifier())  # only remembers
    _seen(storage, device.id, INACTIVE_DAYS + 5)
    told = watch.devices_tick(storage, Notifier())
    assert len(told) == 1 and "Phone" in told[0] and sent == told


def test_a_device_whose_events_connection_dropped_is_signed_out_in_minutes(storage):  # noqa: F811
    gone = api.add_device(storage, "Uninstalled")[0]
    live = api.add_device(storage, "Open")[0]
    rest_only = api.add_device(storage, "RestOnly")[0]
    for device in (gone, live):
        storage.mark_streaming(device.id, "1.2.3.4")
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute("UPDATE devices SET last_seen_at = ? WHERE id IN (?, ?)",
                     ((datetime.now() - timedelta(minutes=10)).isoformat(), gone.id, rest_only.id))
    storage.touch_device(live.id, "1.2.3.4")  # the open connection keeps refreshing this
    assert storage.revoke_dropped_streams(STREAM_DROPPED_MINUTES, REVOKED_BY_INACTIVE) == [gone.id]
    assert {d.name for d in storage.list_devices()} == {"Open", "RestOnly"}  # no stream: only the long silence counts
