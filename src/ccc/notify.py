"""Notifications from the daemon (queue finished, needs you, error, usage
limit / reset) to Telegram and/or Discord. Telegram: `ccc config
telegram-token` + `telegram-chat`; Discord: `ccc config discord-webhook`.
Without any of them nothing is sent.

Every text and caption goes through `ccc.redact` first, so an API key or a
password quoted in a conversation never reaches the chat.

The Telegram bot token and the Discord webhook URL are secrets: they're only
ever put into the request URL, never into a log message or an exception text
we log."""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from ccc.i18n import N_
from ccc.redact import redact

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN_KEY = "telegram_token"
TELEGRAM_CHAT_KEY = "telegram_chat"
TELEGRAM_MAX_CHARS = 4096
TELEGRAM_MAX_CAPTION = 1024
TELEGRAM_MAX_FILE_BYTES = 50 * 1024 * 1024  # bots upload up to 50 MB

DISCORD_WEBHOOK_KEY = "discord_webhook"
DISCORD_MAX_CHARS = 2000
DISCORD_MAX_FILE_BYTES = 20 * 1024 * 1024  # default per-file limit, 20 MiB
# Discord (Cloudflare) rejects urllib's default "Python-urllib/x.y" agent.
USER_AGENT = "ccc-notifier (https://github.com/Icenux/ccc_TUI, 1.0)"
_DISCORD_WEBHOOK_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?(?:discord|discordapp)\.com/api/webhooks/\S+$")

# What can be switched on and off, per service (#146). A message belongs to one
# category; `None` (a test message, an export) is never filtered.
CAT_QUEUE, CAT_LIMITS, CAT_ATTENTION, CAT_RESULTS = "queue", "limits", "attention", "results"
CAT_UPDATES, CAT_STARTED, CAT_ROUTINES = "updates", "started", "routines"
CAT_DEVICES, CAT_SECURITY, CAT_HEALTH, CAT_DIGEST = "devices", "security", "health", "digest"
# (key, label, what it covers, on until the user switches it on)
CATEGORIES = (
    (CAT_QUEUE, N_("Queue"), N_("a queue finished, or stopped on an error"), True),
    (CAT_LIMITS, N_("Usage limits"), N_("waiting for the limit to reset, and continuing"), True),
    (CAT_ATTENTION, N_("Needs you"), N_("a tool to approve, a workflow gate, an answer to a question"), True),
    (CAT_RESULTS, N_("Results"), N_("a workflow, a goal or a review ended"), True),
    (CAT_UPDATES, N_("Updates"), N_("a new ccc version is available"), True),
    (CAT_STARTED, N_("Conversation started"), N_("a conversation starts working after a quiet time"), False),
    (CAT_ROUTINES, N_("Routines"), N_("a routine started, or was skipped"), False),
    (CAT_DEVICES, N_("Devices"), N_("a new device paired or signed out"), True),
    (CAT_SECURITY, N_("Security"), N_("a wrong pairing code or token, a forbidden request"), True),
    (CAT_HEALTH, N_("Health"), N_("a failed backup, a new warning in `ccc api doctor`"), False),
    (CAT_DIGEST, N_("Daily summary"), N_("one message a day with what happened"), False),
)
CATEGORY_KEYS = tuple(c[0] for c in CATEGORIES)
# Categories that no longer exist; apps from before still send them (ignored).
RETIRED_CATEGORIES = ("context",)
CHANNELS = ("telegram", "discord")


def _channel_key(channel: str) -> str:
    return f"notify_categories_{channel}"


def category_states(storage, channel: str) -> dict[str, bool]:
    """Every category -> on / off for the service; what was never set has its default."""
    try:
        saved = json.loads(storage.get_setting(_channel_key(channel)) or "{}")
    except ValueError:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    return {key: saved[key] if isinstance(saved.get(key), bool) else default
            for key, _label, _hint, default in CATEGORIES}


def set_category_states(storage, channel: str, states: dict[str, bool]) -> None:
    """Only the known categories are kept."""
    saved = {key: bool(states[key]) for key in CATEGORY_KEYS if key in states}
    storage.set_setting(_channel_key(channel), json.dumps(saved, sort_keys=True))


def category_enabled(storage, channel: str, category: str | None) -> bool:
    return category is None or category_states(storage, channel).get(category, True)


Sender = Callable[[str, str, str], None]
DiscordSender = Callable[[str, str], None]


def send_telegram(token: str, chat_id: str, text: str) -> None:
    body = json.dumps({"chat_id": chat_id, "text": text[:TELEGRAM_MAX_CHARS], "disable_web_page_preview": True})
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


def _multipart(fields: dict[str, str], file_field: str, path: Path) -> tuple[bytes, str]:
    """multipart/form-data body with text fields and one file; returns
    (body, Content-Type header)."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    filename = path.name.replace('"', "")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    )
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def send_telegram_file(token: str, chat_id: str, path: Path, caption: str) -> None:
    body, content_type = _multipart(
        {"chat_id": chat_id, "caption": caption[:TELEGRAM_MAX_CAPTION]}, "document", path
    )
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        response.read()


def is_discord_webhook(url: str) -> bool:
    return bool(_DISCORD_WEBHOOK_RE.match(url or ""))


def send_discord(webhook_url: str, text: str) -> None:
    # No mentions: text quoted from Claude must never ping @everyone.
    body = json.dumps({"content": text[:DISCORD_MAX_CHARS], "allowed_mentions": {"parse": []}})
    request = urllib.request.Request(
        webhook_url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


def send_discord_file(webhook_url: str, path: Path, text: str) -> None:
    payload = json.dumps({"content": text[:DISCORD_MAX_CHARS], "allowed_mentions": {"parse": []}})
    body, content_type = _multipart({"payload_json": payload}, "files[0]", path)
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": content_type, "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        response.read()


def known_secrets(storage) -> list[str | None]:
    """ccc's own secrets, masked wherever text leaves ccc."""
    return [storage.get_setting(TELEGRAM_TOKEN_KEY), storage.get_setting(DISCORD_WEBHOOK_KEY)]


class Notifier:
    def __init__(self, storage, sender: Sender | None = None, discord_sender: DiscordSender | None = None):
        self.storage = storage
        # None = the module's send_telegram / send_discord, looked up at send time.
        self.sender = sender
        self.discord_sender = discord_sender
        self._tasks: set[asyncio.Task] = set()

    def _telegram_config(self) -> tuple[str, str] | None:
        token = self.storage.get_setting(TELEGRAM_TOKEN_KEY)
        chat = self.storage.get_setting(TELEGRAM_CHAT_KEY)
        return (token, chat) if token and chat else None

    def _discord_webhook(self) -> str | None:
        url = self.storage.get_setting(DISCORD_WEBHOOK_KEY)
        return url if url and is_discord_webhook(url) else None

    def telegram_configured(self) -> bool:
        return self._telegram_config() is not None

    def discord_configured(self) -> bool:
        return self._discord_webhook() is not None

    def configured(self, category: str | None = None) -> bool:
        """At least one set-up service wants messages of the category (any, when None)."""
        return ((self.telegram_configured() and category_enabled(self.storage, "telegram", category))
                or (self.discord_configured() and category_enabled(self.storage, "discord", category)))

    def _clean(self, text: str) -> str:
        return redact(text, known_secrets(self.storage))

    def send_telegram_now(self, text: str) -> bool:
        config = self._telegram_config()
        if config is None:
            return False
        try:
            (self.sender or send_telegram)(config[0], config[1], self._clean(text))
            return True
        except Exception as exc:  # network down, bad token, ...
            # Only the exception type: its text may contain the URL (token).
            logger.warning("Telegram notification failed (%s)", type(exc).__name__)
            return False

    def send_discord_now(self, text: str) -> bool:
        url = self._discord_webhook()
        if url is None:
            return False
        try:
            (self.discord_sender or send_discord)(url, self._clean(text))
            return True
        except Exception as exc:
            # Only the exception type: its text may contain the webhook URL.
            logger.warning("Discord notification failed (%s)", type(exc).__name__)
            return False

    def send_file_telegram_now(self, path: Path, caption: str) -> bool:
        config = self._telegram_config()
        if config is None or path.stat().st_size > TELEGRAM_MAX_FILE_BYTES:
            return False
        try:
            send_telegram_file(config[0], config[1], path, self._clean(caption))
            return True
        except Exception as exc:
            logger.warning("Telegram file upload failed (%s)", type(exc).__name__)
            return False

    def send_file_discord_now(self, path: Path, text: str) -> bool:
        url = self._discord_webhook()
        if url is None or path.stat().st_size > DISCORD_MAX_FILE_BYTES:
            return False
        try:
            send_discord_file(url, path, self._clean(text))
            return True
        except Exception as exc:
            logger.warning("Discord file upload failed (%s)", type(exc).__name__)
            return False

    def send_now(self, text: str, category: str | None = None) -> bool:
        """Send to every configured service that wants the category; True if at least one got it."""
        telegram = category_enabled(self.storage, "telegram", category) and self.send_telegram_now(text)
        discord = category_enabled(self.storage, "discord", category) and self.send_discord_now(text)
        return telegram or discord

    def notify(self, text: str, category: str | None = None) -> None:
        """Fire and forget from the daemon's event loop; never blocks it."""
        if not self.configured(category):
            return
        task = asyncio.get_running_loop().create_task(asyncio.to_thread(self.send_now, text, category))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
