"""Pairing a phone by QR code: `ccc api pair` (or the Devices window) makes
a one-time code that is good for a few minutes; the QR holds it together
with the API's address. The app scans it and trades the code for a token of
its own (POST /pair, ccc.api) — nothing to type, and the token never goes
through the QR."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import segno

from ccc import api
from ccc.models import Device
from ccc.storage import Storage

CODE_KEY = "api_pair_code"  # "<sha256 of the code>|<expires, ISO>": one code at a time
SERVER_KEY = "api_public_url"
TTL = timedelta(minutes=5)
QUIET_ZONE = 4  # light modules around the code, as the spec says (2 made some scanners miss it, #131)


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def new_code(storage: Storage, now: datetime | None = None) -> tuple[str, datetime]:
    """A new code (the one before it stops working) and when it runs out; the
    phone that redeems it becomes a device. Only its SHA-256 is kept, like the tokens."""
    code = secrets.token_urlsafe(16)
    expires = (now or datetime.now()) + TTL
    storage.set_setting(CODE_KEY, f"{_digest(code)}|{expires.isoformat()}")
    return code, expires


def redeem(storage: Storage, code: str, name: str, now: datetime | None = None) -> tuple[Device, str] | None:
    """Trade a good code for a new device and its token; None when the code
    is wrong, used or run out. A wrong guess leaves the good code alone."""
    stored = storage.get_setting(CODE_KEY) or ""
    digest, _sep, rest = stored.partition("|")
    expires = rest.partition("|")[0]  # a code made while roles existed has "|<role>" after it: ignored
    if not code or not digest or digest != _digest(code):
        return None
    # The delete is the one-time part: of two phones scanning at once, one wins.
    if not storage.delete_setting_if(CODE_KEY, stored):
        return None
    if datetime.fromisoformat(expires) < (now or datetime.now()):
        return None
    return api.add_device(storage, name)


def server_url(storage: Storage) -> str | None:
    """Where the phone reaches the API: the address set in api_public_url,
    else the one `tailscale serve` has for it, else None."""
    chosen = (storage.get_setting(SERVER_KEY) or "").strip()
    return chosen.rstrip("/") or tailscale_url()


def tailscale_url() -> str | None:
    try:
        out = subprocess.run(["tailscale", "serve", "status", "--json"], capture_output=True, text=True,
                             timeout=5, check=False).stdout
        web = json.loads(out).get("Web") or {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    for hostport, config in web.items():
        for handler in (config.get("Handlers") or {}).values():
            if str(handler.get("Proxy", "")).rstrip("/").endswith(f":{api.DEFAULT_PORT}"):
                return "https://" + hostport.removesuffix(":443")
    return None


def payload(server: str, code: str) -> str:
    """What the QR holds; the app (ccc_android: …/Pairing.kt) reads exactly this."""
    return f"ccc://pair?server={quote(server, safe='')}&code={quote(code, safe='')}"


def save_png(text: str, path: str | Path, scale: int = 12) -> None:
    """The QR as a PNG picture (black on white, with the quiet zone): big and crisp for a scanner,
    unlike the terminal's half blocks."""
    # The picture holds a one-time pairing code: only its owner may read the file (also an older one it replaces).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        segno.make(text, error="l", micro=False).save(handle, kind="png", scale=scale, border=QUIET_ZONE)


def qr_lines(text: str) -> list[str]:
    """The QR as lines of half blocks for dark-on-light output (print them
    black on white): two module rows per line."""
    rows = [list(map(bool, row)) for row in segno.make(text, error="l", micro=False).matrix]
    width = len(rows[0]) + 2 * QUIET_ZONE
    blank = [False] * width
    grid = [blank] * QUIET_ZONE + [[False] * QUIET_ZONE + row + [False] * QUIET_ZONE for row in rows] + [blank] * QUIET_ZONE
    if len(grid) % 2:
        grid.append(blank)
    glyphs = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}
    return ["".join(glyphs[(top[x], bottom[x])] for x in range(width)) for top, bottom in zip(grid[::2], grid[1::2])]
