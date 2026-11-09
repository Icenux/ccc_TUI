"""#146: what each service is told, and the notices the daemon watches for."""
import asyncio
import json
from datetime import datetime, timedelta

import pytest

from ccc import api, api_doctor, routines, watch
from ccc.models import Routine
from ccc.notify import (
    CAT_DEVICES, CAT_DIGEST, CAT_HEALTH, CAT_QUEUE, CAT_STARTED, CATEGORIES, DISCORD_WEBHOOK_KEY, TELEGRAM_CHAT_KEY,
    TELEGRAM_TOKEN_KEY, Notifier, category_enabled, category_states, set_category_states,
)
from ccc.storage import Storage

WEBHOOK = "https://discord.com/api/webhooks/1/abc"


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "h.db")


@pytest.fixture
def both(storage):
    storage.set_setting(TELEGRAM_TOKEN_KEY, "123:abc")
    storage.set_setting(TELEGRAM_CHAT_KEY, "42")
    storage.set_setting(DISCORD_WEBHOOK_KEY, WEBHOOK)
    telegram, discord = [], []
    notifier = Notifier(storage, sender=lambda token, chat, text: telegram.append(text),
                        discord_sender=lambda url, text: discord.append(text))
    return notifier, telegram, discord


def test_defaults_keep_the_old_behaviour_and_new_kinds_are_off(storage):
    states = category_states(storage, "telegram")
    assert set(states) == {key for key, *_rest in CATEGORIES}
    assert states[CAT_QUEUE] and states[CAT_DEVICES]
    assert not states[CAT_STARTED] and not states[CAT_DIGEST] and not states[CAT_HEALTH]


def test_each_service_has_its_own_choice(storage, both):
    notifier, telegram, discord = both
    set_category_states(storage, "telegram", {CAT_QUEUE: False})
    assert notifier.send_now("done", CAT_QUEUE) is True
    assert (telegram, discord) == ([], ["done"])
    set_category_states(storage, "discord", {CAT_QUEUE: False})
    assert notifier.send_now("again", CAT_QUEUE) is False
    assert notifier.configured(CAT_QUEUE) is False and notifier.configured() is True


def test_a_message_without_category_goes_everywhere(storage, both):
    notifier, telegram, discord = both
    set_category_states(storage, "telegram", {key: False for key, *_rest in CATEGORIES})
    notifier.send_now("test")
    assert (telegram, discord) == (["test"], ["test"])


def test_bad_saved_value_falls_back_to_defaults(storage):
    storage.set_setting("notify_categories_telegram", "not json")
    assert category_enabled(storage, "telegram", CAT_QUEUE)
    storage.set_setting("notify_categories_telegram", json.dumps({CAT_QUEUE: "no", "bogus": False}))
    assert category_states(storage, "telegram")[CAT_QUEUE] is True


def test_api_shows_and_changes_the_choice(storage):
    endpoint = api.Api(storage)
    shown = endpoint.settings()
    assert [c["key"] for c in shown["notify_categories"]] == [key for key, *_rest in CATEGORIES]
    assert shown["notify_telegram"][CAT_QUEUE] is True
    after = endpoint.change_settings({"notify_discord": {CAT_QUEUE: False, CAT_DIGEST: True}})
    assert after["notify_discord"][CAT_QUEUE] is False and after["notify_discord"][CAT_DIGEST] is True
    assert after["notify_telegram"][CAT_QUEUE] is True  # the other service is untouched
    for bad in ({"nope": True}, {CAT_QUEUE: "yes"}, ["queue"]):
        with pytest.raises(api.ApiError) as error:
            endpoint.change_settings({"notify_telegram": bad})
        assert error.value.status == 400


def test_an_older_app_may_still_send_the_retired_context_category(storage):
    endpoint = api.Api(storage)
    after = endpoint.change_settings({"notify_telegram": {"context": True, CAT_QUEUE: False}})
    assert after["notify_telegram"][CAT_QUEUE] is False
    assert "context" not in after["notify_telegram"]


def test_a_device_signed_out_is_told(storage, both):
    notifier, telegram, _discord = both
    first, _ = api.add_device(storage, "Phone")
    assert watch.devices_tick(storage, notifier) == []  # only remembers
    storage.revoke_device(first.id, "computer")
    told = watch.devices_tick(storage, notifier)
    assert len(told) == 1 and "signed out" in told[0]
    assert len(telegram) == 1
    new, _ = api.add_device(storage, "Tablet")  # a new device is the pairing alarm's business
    assert watch.devices_tick(storage, notifier) == []


def test_devices_category_can_be_off(storage, both):
    notifier, telegram, discord = both
    set_category_states(storage, "telegram", {CAT_DEVICES: False})
    set_category_states(storage, "discord", {CAT_DEVICES: False})
    device, _ = api.add_device(storage, "Phone")
    watch.devices_tick(storage, notifier)
    storage.revoke_device(device.id, "app")
    watch.devices_tick(storage, notifier)
    assert telegram == [] and discord == []


def test_routine_notices(storage):
    routine = Routine(1, 1, "Nightly", "go", "daily", "02:00", "0 2 * * *")
    assert "Nightly" in watch.routine_notice(routine, routines.STATUS_STARTED)
    assert "skipped" in watch.routine_notice(routine, routines.STATUS_SKIPPED)
    assert watch.routine_notice(routine, routines.STATUS_ARCHIVED) is None


def test_health_tells_each_warning_once(storage, both):
    notifier, telegram, _discord = both
    set_category_states(storage, "telegram", {CAT_HEALTH: True})
    checks = [api_doctor.Check(api_doctor.OK, "fine"), api_doctor.Check(api_doctor.WARN, "old device")]
    assert len(watch.health_tick(storage, notifier, "x.db", checks)) == 1
    assert watch.health_tick(storage, notifier, "x.db", checks) == []
    assert watch.health_tick(storage, notifier, "x.db", checks[:1]) == []  # fixed: forgotten
    assert len(watch.health_tick(storage, notifier, "x.db", checks)) == 1  # and told again
    assert len(telegram) == 2 and "old device" in telegram[0]


def test_backup_failure_is_told(storage, both):
    notifier, telegram, _discord = both
    set_category_states(storage, "telegram", {CAT_HEALTH: True})
    assert "OSError" in watch.backup_failed(notifier, OSError("disk"))
    assert len(telegram) == 1


def test_digest_comes_once_a_day_after_eight_and_only_if_something_ran(storage, both):
    notifier, telegram, _discord = both
    set_category_states(storage, "telegram", {CAT_DIGEST: True})
    morning = datetime(2026, 11, 1, 9, 0)
    assert watch.digest_tick(storage, notifier, datetime(2026, 11, 1, 7, 0)) is None
    assert watch.digest_tick(storage, notifier, morning) is None  # nothing ran
    assert telegram == []
    queue = storage.create_queue("A", "/tmp", "sid")
    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        for status, cost in (("success", 0.5), ("error", 0.25)):
            conn.execute("INSERT INTO prompt_runs (queue_id, prompt_text, started_at, finished_at, status, cost_usd) "
                         "VALUES (?, 'p', ?, ?, ?, ?)", (queue.id, (morning - timedelta(hours=3)).isoformat(),
                                                         (morning - timedelta(hours=2)).isoformat(), status, cost))
    storage.set_setting(watch.DIGEST_KEY, "")
    text = watch.digest_tick(storage, notifier, morning + timedelta(hours=1))
    assert "2 task(s)" in text and "1 done" in text and "1 failed" in text and "$0.75" in text
    assert telegram == [text]
    assert watch.digest_tick(storage, notifier, morning + timedelta(hours=2)) is None


@pytest.mark.asyncio
async def test_notify_respects_the_category(storage, both):
    notifier, telegram, discord = both
    set_category_states(storage, "telegram", {CAT_QUEUE: False})
    set_category_states(storage, "discord", {CAT_QUEUE: False})
    notifier.notify("quiet", CAT_QUEUE)
    await notifier.drain()
    assert (telegram, discord) == ([], [])
    notifier.notify("loud")
    await notifier.drain()
    assert telegram == ["loud"]
