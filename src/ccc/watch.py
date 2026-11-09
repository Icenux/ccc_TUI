"""What the daemon notices by itself and tells Telegram / Discord (#146): a
device signed out (from the app, the command line
or a new token), a failed backup, a new warning of `ccc api doctor`, the
routines that fired, and a daily summary. Each is a category the user can
switch per service (ccc.notify.CATEGORIES); every tick returns the
messages it sent, for the tests."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ccc.i18n import _
from ccc.notify import CAT_DEVICES, CAT_DIGEST, CAT_HEALTH, CAT_ROUTINES, Notifier

DEVICES_KEY = "notify_devices_revoked"  # {id: revoked_by} as of the last look
HEALTH_KEY = "notify_health_seen"  # warnings already told, one per line
DIGEST_KEY = "notify_digest_day"  # the day the last summary was made
DIGEST_HOUR = 8  # the summary comes in the first look after this hour
HEALTH_LISTED = 5


def devices_tick(storage, notifier: Notifier) -> list[str]:
    """Tell which devices were signed out since the last look.
    The first look only remembers; a new device is the pairing alarm's business."""
    from ccc import api

    current = {str(d.id): d.revoked_by or ("?" if d.revoked_at else "")
               for d in storage.list_devices(include_revoked=True)}
    raw = storage.get_setting(DEVICES_KEY)
    try:
        before = json.loads(raw) if raw else None
    except ValueError:
        before = None
    if current == before:
        return []
    storage.set_setting(DEVICES_KEY, json.dumps(current, sort_keys=True))
    if not isinstance(before, dict):
        return []
    devices = {str(d.id): d for d in storage.list_devices(include_revoked=True)}
    sent = []
    for key, revoked in current.items():
        if key not in before:
            continue
        old = before[key]
        device = devices[key]
        if revoked and not old:
            sent.append(_("ccc API: {name} was signed out ({how}).").format(
                name=device.name, how=api.revoked_label(device, short=True) or _("signed out")))
    for text in sent:
        notifier.send_now(text, CAT_DEVICES)
    return sent


def routine_notice(routine, status: str) -> str | None:
    """The message for a routine that fired; None when nothing is worth telling."""
    from ccc import routines

    if status in (routines.STATUS_QUEUED, routines.STATUS_STARTED, routines.STATUS_SKIPPED):
        return _("🔁 Routine “{name}”: {what}.").format(name=routine.name, what=_(routines.STATUS_LABELS[status]))
    return None


def routines_tell(notifier: Notifier, fired) -> list[str]:
    sent = []
    for routine, status in fired:
        text = routine_notice(routine, status)
        if text:
            notifier.send_now(text, CAT_ROUTINES)
            sent.append(text)
    return sent


def backup_failed(notifier: Notifier, error: Exception) -> str:
    text = _("💾 The daily database backup failed ({error}).").format(error=type(error).__name__)
    notifier.send_now(text, CAT_HEALTH)
    return text


def health_tick(storage, notifier: Notifier, db_path: str | Path, checks=None) -> list[str]:
    """Tell the warnings of `ccc api doctor` that were not told before; what got
    fixed is forgotten, so it is told again if it comes back."""
    from ccc import api_doctor

    if checks is None:
        if not notifier.configured(CAT_HEALTH):
            return []
        checks = api_doctor.run_checks(storage, db_path)
    problems = [c for c in checks if c.level in (api_doctor.WARN, api_doctor.FAIL)]
    seen = set((storage.get_setting(HEALTH_KEY) or "").split("\n")) - {""}
    now = {c.text.replace("\n", " ") for c in problems}
    storage.set_setting(HEALTH_KEY, "\n".join(sorted(now)))
    fresh = [c for c in problems if c.text.replace("\n", " ") not in seen]
    if not fresh:
        return []
    lines = [("⛔ " if c.level == api_doctor.FAIL else "⚠️ ") + c.text for c in fresh[:HEALTH_LISTED]]
    if len(fresh) > HEALTH_LISTED:
        lines.append(_("… and {n} more (ccc api doctor).").format(n=len(fresh) - HEALTH_LISTED))
    text = _("🩺 ccc check-up:") + "\n" + "\n".join(lines)
    notifier.send_now(text, CAT_HEALTH)
    return [text]


def digest_text(storage, since: datetime) -> str | None:
    summary = storage.run_summary_since(since)
    if not summary["runs"]:
        return None
    text = _("📋 ccc, the last 24 hours: {runs} task(s) in {queues} conversation(s), {ok} done, {failed} failed, "
             "${cost:.2f}.").format(runs=summary["runs"], queues=summary["queues"], ok=summary["success"],
                                     failed=summary["runs"] - summary["success"], cost=summary["cost"])
    return text


def digest_tick(storage, notifier: Notifier, now: datetime | None = None) -> str | None:
    """Once a day, after DIGEST_HOUR, if a service wants it and anything ran."""
    now = now or datetime.now()
    today = now.date().isoformat()
    if now.hour < DIGEST_HOUR or storage.get_setting(DIGEST_KEY) == today or not notifier.configured(CAT_DIGEST):
        return None
    storage.set_setting(DIGEST_KEY, today)
    text = digest_text(storage, now - timedelta(days=1))
    if text:
        notifier.send_now(text, CAT_DIGEST)
    return text
