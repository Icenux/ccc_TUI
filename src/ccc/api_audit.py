"""What the API's devices did, and the alarms (#160). If a token is ever
misused you need two things: to see afterwards who did what, and to hear of
it while it happens. So the API keeps a log (`ccc api log`, the Devices
window, GET /audit for an admin) and tells the notification channels
(Telegram / Discord, as ccc does for approvals) about what is out of the
ordinary: a new device paired, a wrong pairing code, requests with a wrong
token, a device asking for what is not allowed.

Logged: everything that changes something (not GET), every refusal (401 /
403) and every pairing — never the query, the body or typed keys (they can
hold secrets); only the path and, for a new conversation, its folder."""
from __future__ import annotations

import re
import threading
import time
from http import HTTPStatus

from ccc.i18n import _
from ccc.notify import CAT_DEVICES, CAT_SECURITY

ALARM_GAP_SECONDS = 300.0  # one alarm of a kind per this long: a scan must not flood the phone
_last_alarm: dict[str, float] = {}
_lock = threading.Lock()


def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def worth_logging(method: str, status: int) -> bool:
    return method != "GET" or status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")  # control and invisible/bidi characters


def clean(text: str, limit: int = 300) -> str:
    """Printable text only, and short: a path or a device name comes from outside and ends up in a terminal and in Rich markup."""
    return _CONTROL.sub("?", text)[:limit]


def record(storage, device, ip: str, method: str, path: str, status: int, detail: str = "") -> None:
    """Never raises: a full disk must not stop the API."""
    try:
        if worth_logging(method, status):
            storage.add_audit(device.id if device else None, clean(device.name, 60) if device else "", clean(ip, 64), method,
                              clean(path), int(status), clean(detail))
    except Exception:  # noqa: BLE001
        pass


_seen_folders: dict[tuple, float] = {}


def record_folder_denied(storage, device, ip: str, method: str, path: str, folder: str, now: float | None = None) -> bool:
    """Log a request for a folder outside the allowed roots, with the reason; the same device only once
    per ALARM_GAP_SECONDS (a phone asks again at every letter typed). True when logged."""
    moment = time.monotonic() if now is None else now
    if method == "GET":  # only reads are collapsed: a refused change (POST) is always logged
        key = (device.id if device else None,)  # not the folder: typing a path asks for every prefix of it
        with _lock:
            if moment - _seen_folders.get(key, -1e9) < ALARM_GAP_SECONDS:
                return False
            if len(_seen_folders) > 500:
                _seen_folders.clear()
            _seen_folders[key] = moment
    record(storage, device, ip, method, path, HTTPStatus.FORBIDDEN, "folder not allowed: " + folder)
    return True


def alarm_folder_denied(storage, device, folder: str) -> None:
    if device is not None:
        alarm(storage, f"folder-{device.id}", _("ccc API: {name} asked for the folder {folder}, which is not allowed. If it is yours, review it in `ccc api log` and allow it on the computer.").format(
            name=clean(device.name, 60), folder=clean(folder)))


def alarm(storage, kind: str, text: str, now: float | None = None, category: str = CAT_SECURITY) -> bool:
    """Tell the notification channels, at most once per ALARM_GAP_SECONDS for the `kind`; True when sent.
    Sent in a thread of its own: a slow Telegram must not hold up a request."""
    from ccc.notify import Notifier

    moment = time.monotonic() if now is None else now
    with _lock:
        if moment - _last_alarm.get(kind, -1e9) < ALARM_GAP_SECONDS:
            return False
        _last_alarm[kind] = moment
    notifier = Notifier(storage)
    if not notifier.configured(category):
        return False
    _spawn(notifier.send_now, text, category)
    return True


def alarm_for(storage, status: int, method: str, path: str, ip: str, device) -> None:
    """The alarm, if any, for a request that ended with `status`."""
    if status == HTTPStatus.UNAUTHORIZED and path.endswith("/pair"):
        alarm(storage, "pair-failed", _("ccc API: a wrong pairing code from {ip}.").format(ip=ip))
    elif status == HTTPStatus.UNAUTHORIZED:
        alarm(storage, "bad-token", _("ccc API: a request with a wrong token from {ip}.").format(ip=ip))
    elif status == HTTPStatus.FORBIDDEN and device is not None:
        alarm(storage, f"forbidden-{device.id}", _("ccc API: {name} tried {method} {path}, which is not allowed.").format(
            name=device.name, method=method, path=path))
    elif status == HTTPStatus.CREATED and path.endswith("/pair") and device is not None:
        alarm(storage, f"paired-{device.id}", _("ccc API: a new device was paired: {name} from {ip}.").format(
            name=device.name, ip=ip), category=CAT_DEVICES)
