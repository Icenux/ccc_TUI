"""The views part of ccc's HTTP API (see ccc.api): routines (o in the
TUI), live Claude Code sessions (s), analytics (a) and settings (Ctrl+P)
— what the TUI's views and windows show and change.

GET  /api/v1/routines                      every routine and the schedule kinds; POST adds one
POST /api/v1/routines/{id}                 change it; DELETE removes it; POST …/enabled {"enabled"}, …/run
GET  /api/v1/live-sessions                 Claude Code sessions running on the computer
POST /api/v1/live-sessions/{id}/prompt     {"prompt"}: queue a prompt for one
GET  /api/v1/analytics?year=&month=        tokens per day, model and conversation; the months there are
GET  /api/v1/settings                      ccc's settings with their choices; POST changes some
POST /api/v1/settings/telegram-test, telegram-off, discord-test, discord-off
POST /api/v1/prompt-presets                {"presets": [{"name", "text", "send_now"}]}: all of them, in order
POST /api/v1/launch-presets                {"name", "options", …} save one; POST launch-presets/delete {"name"}
POST /api/v1/backup                        back the database up now
GET  /api/v1/conversations/{id}/terminal   what its Claude Code shows (an interactive conversation)
POST /api/v1/conversations/{id}/terminal   {"paste", "submit"} / {"keys": [tmux names]} / {"type": "y"}
POST /api/v1/analytics/archive             archive finished months into the analytics folder now
"""
from __future__ import annotations

from datetime import date, datetime
from http import HTTPStatus
from pathlib import Path

from ccc import api_scope, groups, i18n, routines
from ccc.api_common import ApiError, int_of, iso, route, text_of
from ccc.i18n import _
from ccc.models import RoutineTarget

TELEGRAM_TEST = "✅ ccc: Telegram notifications work."
DISCORD_TEST = "✅ ccc: Discord notifications work."


def _folder(value: str) -> str:
    return str(Path(value.strip()).expanduser()) if value.strip() else ""


def _session_word(word: str | None) -> str | None:
    """What `claude agents` calls a session's kind / status / state, in the request's language; a word
    ccc doesn't know stays as Claude Code wrote it."""
    if not word:
        return word
    known = {
        "interactive": _("interactive"), "background": _("background"), "idle": _("idle"), "busy": _("busy"),
        "working": _("working"), "waiting": _("waiting"), "blocked": _("blocked"),
    }
    return known.get(word, word)


class ViewEndpoints:
    storage: object

    def _view_routes(self) -> list:
        ok = route
        return [
            ok(r"/routines", lambda ids, q, b: self.routines()),
            ok(r"/routines", lambda ids, q, b: self.add_routine(b), "POST", HTTPStatus.CREATED),
            ok(r"/routines/(\d+)", lambda ids, q, b: self.edit_routine(*ids, b), "POST"),
            ok(r"/routines/(\d+)", lambda ids, q, b: self.delete_routine(*ids), "DELETE"),
            ok(r"/routines/(\d+)/enabled", lambda ids, q, b: self.enable_routine(*ids, b), "POST"),
            ok(r"/routines/(\d+)/run", lambda ids, q, b: self.run_routine(*ids), "POST"),
            ok(r"/live-sessions", lambda ids, q, b: self.live_sessions()),
            ok(r"/live-sessions/([0-9A-Za-z-]+)/prompt", lambda ids, q, b: self.prompt_live_session(*ids, b), "POST",
               HTTPStatus.CREATED),
            ok(r"/analytics", lambda ids, q, b: self.analytics(int_of(q, "year"), int_of(q, "month"))),
            ok(r"/analytics/archive", lambda ids, q, b: self.archive_analytics(), "POST"),
            ok(r"/settings", lambda ids, q, b: self.settings()),
            ok(r"/settings", lambda ids, q, b: self.change_settings(b), "POST"),
            ok(r"/settings/telegram-test", lambda ids, q, b: self.test_telegram(), "POST"),
            ok(r"/settings/telegram-off", lambda ids, q, b: self.notifications_off("telegram"), "POST"),
            ok(r"/settings/discord-test", lambda ids, q, b: self.test_discord(), "POST"),
            ok(r"/settings/discord-off", lambda ids, q, b: self.notifications_off("discord"), "POST"),
            ok(r"/prompt-presets", lambda ids, q, b: self.save_prompt_presets(b), "POST"),
            ok(r"/launch-presets", lambda ids, q, b: self.save_launch_preset(b), "POST"),
            ok(r"/launch-presets/delete", lambda ids, q, b: self.delete_launch_preset(b), "POST"),
            ok(r"/backup", lambda ids, q, b: self.backup(), "POST"),
            ok(r"/conversations/(\d+)/terminal", lambda ids, q, b: self.terminal(*ids)),
            ok(r"/conversations/(\d+)/terminal", lambda ids, q, b: self.terminal_input(*ids, b), "POST"),
        ]

    # --- routines ----------------------------------------------------------------------

    def routines(self) -> dict:
        names = {q.id: q.name for q in self.storage.list_queues()}
        return {
            "kinds": [{"kind": kind, "label": _(label), "hint": _(hint)} for kind, label, hint in routines.KINDS],
            "routines": [
                {
                    "id": r.id,
                    "conversation_id": r.queue_id,
                    "conversation": names.get(r.queue_id, "?"),
                    "name": r.name,
                    "prompt": r.prompt_text,
                    "kind": r.schedule_kind,
                    "value": r.schedule_value,
                    "schedule": routines.describe(r.schedule_kind, r.schedule_value),
                    "fresh": r.target == RoutineTarget.FRESH,
                    "enabled": r.enabled,
                    "next_run_at": iso(r.next_run_at),
                    "last_run_at": iso(r.last_run_at),
                    "last_status": _(routines.STATUS_LABELS[r.last_status]) if r.last_status in routines.STATUS_LABELS
                    else "",
                    "skipped": r.skipped,
                }
                for r in self.storage.list_routines()
            ],
        }

    def _routine_fields(self, body: dict) -> dict:
        queue_id = body.get("conversation_id")
        if not isinstance(queue_id, int) or isinstance(queue_id, bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, _("Choose the routine's conversation."))
        self._queue(queue_id)
        kind = text_of(body, "kind")
        if kind not in routines.KIND_IDS:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("Choose how often the routine runs."))
        return {
            "queue_id": queue_id, "name": text_of(body, "name"), "prompt_text": text_of(body, "prompt"),
            "kind": kind, "value": text_of(body, "value"),
            "target": RoutineTarget.FRESH if body.get("fresh") is True else RoutineTarget.SAME,
        }

    def add_routine(self, body: dict) -> dict:
        """{"conversation_id", "name", "prompt", "kind", "value", "fresh"} — as the Routines window."""
        fields = self._routine_fields(body)
        try:
            routines.add(self.storage, fields["queue_id"], fields["name"], fields["prompt_text"], fields["kind"],
                         fields["value"], fields["target"])
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        return self.routines()

    def _routine(self, routine_id: int):
        found = self.storage.find_routine(routine_id)
        if found is None:
            raise ApiError(HTTPStatus.NOT_FOUND, _("No such routine."))
        return found

    def edit_routine(self, routine_id: int, body: dict) -> dict:
        self._routine(routine_id)
        try:
            routines.update(self.storage, routine_id, **self._routine_fields(body))
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        return self.routines()

    def delete_routine(self, routine_id: int) -> dict:
        self._routine(routine_id)
        self.storage.delete_routine(routine_id)
        return self.routines()

    def enable_routine(self, routine_id: int, body: dict) -> dict:
        self._routine(routine_id)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, _("{name} must be true or false.").format(name="enabled"))
        routines.set_enabled(self.storage, routine_id, enabled)
        return self.routines()

    def run_routine(self, routine_id: int) -> dict:
        """Run it now (its next time stays); what it did, in words."""
        status = routines.fire(self.storage, self._routine(routine_id))
        return {"status": status, "label": _(routines.STATUS_LABELS[status])}

    # --- live sessions -------------------------------------------------------------------

    def live_sessions(self) -> list[dict]:
        """Claude Code sessions running on the computer (`claude agents`)."""
        from ccc.session_discovery import discover_sessions

        return [
            {"session_id": s.session_id, "name": s.name, "cwd": s.cwd, "kind": s.kind, "status": s.status,
             "state": s.state, "kind_label": _session_word(s.kind), "status_label": _session_word(s.status),
             "state_label": _session_word(s.state)}
            for s in discover_sessions()
        ]

    def prompt_live_session(self, session_id: str, body: dict) -> dict:
        """{"prompt"}: queued for the session; typed into it once it's idle."""
        from ccc.orchestrator import attach_to_session
        from ccc.session_discovery import discover_sessions

        prompt = text_of(body, "prompt").strip()
        if not prompt:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("The message is empty."))
        session = next((s for s in discover_sessions() if s.session_id == session_id), None)
        if session is None:
            raise ApiError(HTTPStatus.NOT_FOUND, _("That session isn't running any more."))
        api_scope.check_folder(self.storage, session.cwd)
        queue = attach_to_session(self.storage, session.session_id, session.cwd, session.name, prompt)
        return {"conversation_id": queue.id}

    # --- analytics ------------------------------------------------------------------------

    def analytics(self, year: int | None, month: int | None) -> dict:
        """One calendar month (this one by default): tokens per day, model and conversation."""
        from ccc.orchestrator import usage_reserve
        from ccc.token_stats import month_stats, months

        today = date.today()
        year, month = year or today.year, month or today.month
        if not 1 <= month <= 12:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("month must be 1 to 12."))
        stats = month_stats(self.storage, year, month)
        return {
            "year": year,
            "month": month,
            "title": i18n.month_title(year, month),
            "total": stats.total,
            "by_day": [{"day": day.isoformat(), "tokens": tokens} for day, tokens in sorted(stats.by_day.items())],
            "by_model": [{"model": model, "tokens": tokens} for model, tokens in stats.by_model],
            "by_conversation": [
                {"id": c.queue_id, "name": c.name, "deleted": c.deleted, "tokens": c.tokens, "success": c.success,
                 "errors": c.errors, "rate_limited": c.rate_limited}
                for c in stats.by_conversation
            ],
            "months": [{"year": y, "month": m, "tokens": t} for y, m, t in months(self.storage)],
            "blocked_minutes": round(self.storage.blocked_seconds_total() / 60, 1),
            "reserve": usage_reserve(self.storage),
        }

    def archive_analytics(self) -> dict:
        from ccc.analytics_archive import archive, configured_folder

        folder = configured_folder(self.storage)
        if folder is None:
            raise ApiError(HTTPStatus.CONFLICT, _("Fill in a folder first."))
        done = archive(self.storage, folder)
        return {"archived": [{"year": y, "month": m} for y, m in done], "folder": str(folder)}

    # --- settings ---------------------------------------------------------------------------

    def settings(self) -> dict:
        """ccc's settings as the Settings window has them, with the choices for each."""
        from ccc.analytics_archive import FOLDER_KEY as ARCHIVE_FOLDER_KEY
        from ccc.export_paths import FOLDER_KEY as EXPORT_FOLDER_KEY, SEND_CHOICES, SEND_KEY as EXPORT_SEND_KEY
        from ccc.notify import (
            CATEGORIES, DISCORD_WEBHOOK_KEY, TELEGRAM_CHAT_KEY, TELEGRAM_TOKEN_KEY, category_states, is_discord_webhook,
        )
        from ccc.orchestrator import DEFAULT_CONTINUE_TEXT, RESERVE_CHOICES, usage_reserve
        from ccc.presets import list_presets
        from ccc.reviews import MODEL_CHOICES, MODEL_KEY

        s = self.storage
        token_set = bool(s.get_setting(TELEGRAM_TOKEN_KEY))
        chat = s.get_setting(TELEGRAM_CHAT_KEY) or ""
        return {
            "language": s.get_setting(i18n.LANGUAGE_KEY) or "auto",
            "languages": [{"value": "auto", "label": _("Auto (system)")}]
            + [{"value": code, "label": name} for code, name in i18n.LANGUAGES],
            "groups": groups.mode(s),
            "groups_choices": [{"value": groups.BY_PROJECT, "label": _("By project, and your own groups")},
                               {"value": groups.OWN_ONLY, "label": _("Only your own groups")}],
            "continue_text": s.get_setting("continue_text", DEFAULT_CONTINUE_TEXT) or DEFAULT_CONTINUE_TEXT,
            "usage_reserve": usage_reserve(s),
            "reserve_choices": RESERVE_CHOICES,
            "review_model": s.get_setting(MODEL_KEY) or "",
            "review_models": [{"value": v, "label": _(label) if not v else label} for v, label in MODEL_CHOICES],
            "export_folder": s.get_setting(EXPORT_FOLDER_KEY) or "",
            "export_send": s.get_setting(EXPORT_SEND_KEY) or "",
            "send_choices": [{"value": v, "label": _(label)} for v, label in SEND_CHOICES],
            "archive_folder": s.get_setting(ARCHIVE_FOLDER_KEY) or "",
            # Secrets never leave ccc: only whether they're set.
            "telegram": {"token_set": token_set, "chat": chat, "on": token_set and bool(chat)},
            "discord": {"on": is_discord_webhook(s.get_setting(DISCORD_WEBHOOK_KEY) or "")},
            # What each service is told (#146): the categories once, then on / off per service.
            "notify_categories": [{"key": key, "label": _(label), "hint": _(hint)} for key, label, hint, _on in CATEGORIES],
            "notify_telegram": category_states(s, "telegram"),
            "notify_discord": category_states(s, "discord"),
            "launch_presets": sorted(list_presets(s)),
        }

    def change_settings(self, body: dict) -> dict:
        """Any of the settings() keys (not the choices); secrets: "telegram_token", "discord_webhook"."""
        from ccc.analytics_archive import FOLDER_KEY as ARCHIVE_FOLDER_KEY
        from ccc.export_paths import FOLDER_KEY as EXPORT_FOLDER_KEY, SEND_CHOICES, SEND_KEY as EXPORT_SEND_KEY
        from ccc.notify import (
            CATEGORY_KEYS, DISCORD_WEBHOOK_KEY, RETIRED_CATEGORIES, TELEGRAM_CHAT_KEY, TELEGRAM_TOKEN_KEY, category_states,
            is_discord_webhook, set_category_states,
        )
        from ccc.orchestrator import DEFAULT_CONTINUE_TEXT, RESERVE_KEY
        from ccc.reviews import MODEL_CHOICES, MODEL_KEY

        s = self.storage
        current = self.settings()
        changes: dict[str, str] = {}

        def choice(key: str, allowed, setting: str, to_text=str, off=None):
            if key not in body:
                return
            value = body[key]
            if value not in allowed:
                raise ApiError(HTTPStatus.BAD_REQUEST, _("{name}: not one of the choices.").format(name=key))
            changes[setting] = "" if value == off else to_text(value)

        choice("language", {"auto"} | {code for code, _name in i18n.LANGUAGES}, i18n.LANGUAGE_KEY, off="auto")
        choice("groups", {groups.BY_PROJECT, groups.OWN_ONLY}, groups.MODE_KEY)
        choice("usage_reserve", set(current["reserve_choices"]) | {None}, RESERVE_KEY, off=None)
        choice("review_model", {v for v, _label in MODEL_CHOICES}, MODEL_KEY)
        choice("export_send", {v for v, _label in SEND_CHOICES}, EXPORT_SEND_KEY)
        if "continue_text" in body:
            changes["continue_text"] = text_of(body, "continue_text").strip() or DEFAULT_CONTINUE_TEXT
        if "export_folder" in body:
            changes[EXPORT_FOLDER_KEY] = _folder(text_of(body, "export_folder"))
        if "archive_folder" in body:
            changes[ARCHIVE_FOLDER_KEY] = _folder(text_of(body, "archive_folder"))
        if "telegram_chat" in body:
            changes[TELEGRAM_CHAT_KEY] = text_of(body, "telegram_chat").strip()
        if text_of(body, "telegram_token").strip():
            changes[TELEGRAM_TOKEN_KEY] = text_of(body, "telegram_token").strip()
        webhook = text_of(body, "discord_webhook").strip()
        if webhook:
            if not is_discord_webhook(webhook):
                raise ApiError(HTTPStatus.BAD_REQUEST,
                               _("That isn't a Discord webhook URL (https://discord.com/api/webhooks/…)."))
            changes[DISCORD_WEBHOOK_KEY] = webhook
        shown: dict[str, dict[str, bool]] = {}
        for channel in ("telegram", "discord"):
            field = f"notify_{channel}"
            if field not in body:
                continue
            given = body[field]
            if isinstance(given, dict):  # an older app still sends the retired "context" category
                given = {k: v for k, v in given.items() if k not in RETIRED_CATEGORIES}
            if not isinstance(given, dict) or any(k not in CATEGORY_KEYS or not isinstance(v, bool)
                                                 for k, v in given.items()):
                raise ApiError(HTTPStatus.BAD_REQUEST,
                               _("{name} must map the notification categories to true or false.").format(name=field))
            shown[channel] = {**category_states(s, channel), **given}
        for key, value in changes.items():
            s.set_setting(key, value)
        for channel, states in shown.items():
            set_category_states(s, channel, states)
        i18n.load(s)
        return self.settings()

    def test_telegram(self) -> dict:
        from ccc.notify import TELEGRAM_CHAT_KEY, TELEGRAM_TOKEN_KEY, send_telegram

        token, chat = self.storage.get_setting(TELEGRAM_TOKEN_KEY), self.storage.get_setting(TELEGRAM_CHAT_KEY)
        if not token or not chat:
            raise ApiError(HTTPStatus.CONFLICT, _("Fill in the bot token and chat id first."))
        try:
            send_telegram(token, chat, _(TELEGRAM_TEST))
        except Exception as exc:  # never the exception text: it may contain the token
            raise ApiError(HTTPStatus.BAD_GATEWAY, _("✖ Sending failed ({error}) — check the token and chat id.").format(
                error=type(exc).__name__)) from None
        return {"sent": True}

    def test_discord(self) -> dict:
        from ccc.notify import DISCORD_WEBHOOK_KEY, is_discord_webhook, send_discord

        url = self.storage.get_setting(DISCORD_WEBHOOK_KEY) or ""
        if not is_discord_webhook(url):
            raise ApiError(HTTPStatus.CONFLICT,
                           _("Paste a Discord webhook URL (https://discord.com/api/webhooks/…) first."))
        try:
            send_discord(url, _(DISCORD_TEST))
        except Exception as exc:  # never the exception text: it may contain the webhook
            raise ApiError(HTTPStatus.BAD_GATEWAY, _("✖ Sending failed ({error}) — check the webhook URL.").format(
                error=type(exc).__name__)) from None
        return {"sent": True}

    def notifications_off(self, which: str) -> dict:
        from ccc.notify import DISCORD_WEBHOOK_KEY, TELEGRAM_CHAT_KEY, TELEGRAM_TOKEN_KEY

        keys = (TELEGRAM_TOKEN_KEY, TELEGRAM_CHAT_KEY) if which == "telegram" else (DISCORD_WEBHOOK_KEY,)
        for key in keys:
            self.storage.set_setting(key, "")
        return self.settings()

    def save_prompt_presets(self, body: dict) -> list[dict]:
        """All the preset prompts, in order (at most 9: Alt+1 … Alt+9)."""
        from ccc.prompt_presets import MAX_PRESETS, PromptPreset, save_prompt_presets

        given = body.get("presets")
        if not isinstance(given, list) or len(given) > MAX_PRESETS:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("presets must be a list of at most {n}.").format(n=MAX_PRESETS))
        presets = []
        for item in given:
            name = text_of(item, "name").strip() if isinstance(item, dict) else ""
            text = text_of(item, "text").strip() if isinstance(item, dict) else ""
            if not name or not text:
                raise ApiError(HTTPStatus.BAD_REQUEST, _("Every preset needs a name and a text."))
            presets.append(PromptPreset(name, text, item.get("send_now") is True))
        save_prompt_presets(self.storage, presets)
        return self.prompt_presets()

    def save_launch_preset(self, body: dict) -> dict:
        """{"name", "options", "auto_continue", "remote_control"}: a New conversation preset."""
        from ccc.presets import LaunchChoice, save_preset

        name = text_of(body, "name").strip()
        if not name:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("Give the preset a name."))
        selection = self._selection(body.get("options"))
        save_preset(self.storage, name, LaunchChoice(selection, body.get("auto_continue", True) is True,
                                                     body.get("remote_control", False) is True))
        return self.launch_presets()

    def delete_launch_preset(self, body: dict) -> dict:
        from ccc.presets import delete_preset

        delete_preset(self.storage, text_of(body, "name"))
        return self.launch_presets()

    def backup(self) -> dict:
        from ccc.backup import backup_now, default_backup_dir

        path = backup_now(self.storage.db_path, default_backup_dir(self.storage.db_path))
        return {"path": str(path), "at": iso(datetime.now())}

    # --- Claude Code's terminal (Enter in the list of Conversations) ------------------------

    def _terminal_queue(self, queue_id: int):
        from ccc import managed_sessions

        queue = self._queue(queue_id)
        if not queue.delivery_mode.managed:
            raise ApiError(HTTPStatus.CONFLICT, _("This conversation runs without a Claude Code terminal (headless)."))
        if not managed_sessions.is_running(queue_id):
            raise ApiError(HTTPStatus.CONFLICT, _("Its Claude Code isn't running — the daemon starts it for its next task."))
        return queue

    def terminal(self, queue_id: int) -> dict:
        """The screen of the conversation's Claude Code, with its colours (ANSI) and cursor."""
        from ccc import managed_sessions

        self._terminal_queue(queue_id)
        shown = managed_sessions.screen(queue_id)
        if shown is None:
            raise ApiError(HTTPStatus.CONFLICT, _("Its Claude Code isn't running — the daemon starts it for its next task."))
        return {"screen": shown.text, "width": shown.width, "height": shown.height,
                "cursor_x": shown.cursor_x, "cursor_y": shown.cursor_y}

    def terminal_input(self, queue_id: int, body: dict) -> dict:
        """Into the conversation's Claude Code: {"paste": text, "submit": bool} as one paste (Enter after
        it unless submit is false), {"keys": [...]} tmux keys from managed_sessions.KEYS, {"type": "y"}
        a few characters as typed."""
        from ccc import managed_sessions

        self._terminal_queue(queue_id)
        if "paste" in body:
            text = text_of(body, "paste")
            if not text.strip():
                raise ApiError(HTTPStatus.BAD_REQUEST, _("The message is empty."))
            result = managed_sessions.paste_and_submit(queue_id, text, submit=body.get("submit", True) is not False)
        elif "keys" in body:
            keys = body.get("keys")
            if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                raise ApiError(HTTPStatus.BAD_REQUEST, _("keys must be a list of key names."))
            result = managed_sessions.press(queue_id, keys)
        else:
            result = managed_sessions.type_text(queue_id, text_of(body, "type"))
        if not result.delivered:
            raise ApiError(HTTPStatus.BAD_REQUEST, result.error or _("Not delivered."))
        return self.terminal(queue_id)
