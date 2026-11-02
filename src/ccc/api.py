"""ccc's HTTP API, for the Android app (github.com/Icenux/ccc_android): the conversations, their
messages, sending one, approvals, a new conversation, usage limits, and a
stream of changes. Like the TUI it's just another reader / writer of the
ccc database — the daemon does the work.

It listens on 127.0.0.1 only; `tailscale serve` puts it on the tailnet
with HTTPS (see help/android-api.md). Every request needs the token
(`Authorization: Bearer …`, `ccc api token`), so another program on the
machine or a tailnet device of someone else can't use it. The database
keeps only the token's SHA-256 (it's readable by other users of the
machine), so the token is shown once, when it's made. Every device (a
phone) has its own token; it's checked against the database on every
request, so a device taken off the list is locked out at once — its
open event stream too.

Standard library only (http.server), like the ccc MCP server: no web
framework to install for a handful of JSON endpoints.

GET  /api/v1/status                       daemon, counts, language, the update found, the Android build
GET  /api/v1/conversations[?archived=1]   the list, with groups
GET  /api/v1/conversations/{id}           one, with its queued messages and approvals
GET  /api/v1/conversations/{id}/messages  the transcript (?after=N new ones, ?before=N older, ?limit=)
POST /api/v1/conversations/{id}/messages  {"text"}: queue a message
POST /api/v1/conversations/{id}/retry     retry / resume a blocked or paused one
POST /api/v1/conversations/{id}/seen      mark it read
DELETE /api/v1/conversations/{id}         delete it from ccc (not while it runs)
POST /api/v1/conversations/{id}/archive   {"archived": bool}
POST /api/v1/conversations/{id}/group     {"name": "…" | null}: its own group
GET  /api/v1/conversations/{id}/export    as Markdown, secrets masked
GET  /api/v1/conversations/{id}/files?q=  files of its folder for an @-mention
POST /api/v1/conversations                start one (as the New conversation window)
GET  /api/v1/conversations/{id}/tasks     its task queue; POST adds {"text", "when"}
POST /api/v1/tasks/{id}                   {"text"} / {"when"}; DELETE removes it; POST tasks/{id}/move {"delta"}
POST /api/v1/conversations/{id}/switches  {"paused"} / {"auto_continue"}
GET  /api/v1/conversations/{id}/runs      its runs with their commits
GET  /api/v1/runs/{id}/diff               the run's commit, line by line
POST /api/v1/runs/{id}/comments           {"comments": [{"index", "text"}]} sent to Claude
POST /api/v1/runs/{id}/revert             {"tell": bool}: undo the commit
GET  /api/v1/groups                       grouping mode and own groups; POST groups/rename, groups/delete
GET  /api/v1/search?q=                    search every conversation
POST /api/v1/update/check                 look for a new ccc version now; POST /api/v1/update applies it
POST /api/v1/restart                      restart ccc's services (GET /status says restart_needed after an upgrade)
GET  /api/v1/waiting                      conversations that need you (Alt+N)
GET  /api/v1/prompt-presets               the preset prompts (Alt+1 … 9)
POST /api/v1/pair                         {"code", "name"}: the one request without a token — trades a pairing code (ccc.pairing) for a device and its token
GET  /api/v1/audit                        what the devices did (admin): the newest first, ?limit= (100); changes, refusals and pairings only — see ccc.api_audit
GET  /api/v1/devices                      the devices signed in; "current" marks the caller; ?all=1 adds the signed-out ones (history, with revoked_at / revoked_by)
POST /api/v1/devices/{id}                 {"name"}: rename a device
DELETE /api/v1/devices/{id}               sign a device out (the caller too); the row stays as history
GET  /api/v1/launch-presets               New conversation presets and the last choice
GET  /api/v1/sessions?path=               Claude Code sessions of a folder (to resume one)
GET  /api/v1/mcp-servers?path=            names of the MCP servers configured for a folder (to choose from)
GET  /api/v1/plugins                      installed Claude Code plugins and which ones are on by default
GET  /api/v1/permissions                  every approval waiting
POST /api/v1/permissions/{id}             {"decision": "allow" | "always" | "deny"}
GET  /api/v1/usage                        Claude usage limits
GET  /api/v1/projects                     recently used folders
GET  /api/v1/launch-options               what a new conversation can be started with
GET  /api/v1/events                       Server-Sent Events: "changed" with what changed
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ccc import context_fill, goals, groups, i18n, launch_options, mcp_servers, plugins, updates
from ccc.api_agents import AgentEndpoints
from ccc import api_audit, api_roles, api_scope
from ccc.api_common import ApiError, int_of, iso, route, task_line, text_of
from ccc.api_views import ViewEndpoints
from ccc.heartbeat import daemon_alive
from ccc.i18n import _
from ccc.models import ROLE_ADMIN, REVOKED_BY_APP, REVOKED_BY_COMPUTER, REVOKED_BY_TOKEN, Device, ItemStatus, QueueStatus
from ccc.orchestrator import send_message, start_conversation
from ccc.storage import Storage
from ccc.transcript import TranscriptEvent, find_transcript_path, parse_transcript_events

logger = logging.getLogger(__name__)

PREFIX = "/api/v1"
# A device's "last seen" is written at most this often (every request would write).
SEEN_SECONDS = 30.0
ORIGINAL_NAME = "Token"
CALLER = "_device"  # the device making the request, put in the query by the handler
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8737
MAX_BODY = 1024 * 1024
MESSAGES_LIMIT = 200
EVENTS_POLL_SECONDS = 1.0
EVENTS_KEEPALIVE_SECONDS = 15.0
# A conversation without a transcript yet: look for it again after this.
TRANSCRIPT_LOOKUP_SECONDS = 10.0
DECISIONS = ("allow", "always", "deny")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def add_device(storage: Storage, name: str, role: str = ROLE_ADMIN) -> tuple[Device, str]:
    """A device with a token of its own and a role (models.ROLES). Only the
    token's SHA-256 is kept, so this is the one time it can be shown."""
    value = secrets.token_urlsafe(32)
    return storage.add_device(name.strip() or ORIGINAL_NAME, _digest(value), role), value


def new_token(storage: Storage, name: str = ORIGINAL_NAME) -> str:
    """A single new token: every device is signed out, this is the one left."""
    storage.revoke_all_devices(REVOKED_BY_TOKEN)
    return add_device(storage, name)[1]


def has_token(storage: Storage) -> bool:
    return bool(storage.list_devices())


def revoked_label(device: Device, short: bool = False) -> str:
    """How a signed-out device was signed out, for the TUI (short: a table cell) and `ccc api devices --all`."""
    if short:
        return {REVOKED_BY_APP: _("in the app"), REVOKED_BY_COMPUTER: _("on the computer"),
                REVOKED_BY_TOKEN: _("new token")}.get(device.revoked_by, "")
    return {
        REVOKED_BY_APP: _("signed out in the app"),
        REVOKED_BY_COMPUTER: _("signed out on the computer"),
        REVOKED_BY_TOKEN: _("token replaced (ccc api token --new)"),
    }.get(device.revoked_by, _("signed out"))


LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def client_ip(peer: str, forwarded_for: str) -> str:
    """The phone's address. Behind `tailscale serve` every connection comes
    from the proxy on this computer (127.0.0.1), which names the phone in
    X-Forwarded-For; that header counts only from a loopback peer (anyone else
    could write whatever they like), and its last entry is the proxy's own."""
    if peer in LOOPBACK:
        last = forwarded_for.split(",")[-1].strip()
        if last:
            return last
    return peer


def authenticate(storage: Storage, given: str) -> Device | None:
    return storage.device_by_token(_digest(given)) if given else None


def token_valid(storage: Storage, given: str) -> bool:
    return authenticate(storage, given) is not None


GIT_SECONDS = 15.0
UPDATE_KEY = -1  # the snapshot's entry for "a new ccc version", beside the conversations' ids


@dataclass
class _Transcript:
    path: Path | None = None
    looked_up: float = 0.0
    offset: int = 0
    events: list[TranscriptEvent] = field(default_factory=list)


class Api(AgentEndpoints, ViewEndpoints):
    """The endpoints without HTTP: route() takes a request and returns
    (status, JSON-able body), raising ApiError for an error."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self._lock = threading.Lock()
        self._transcripts: dict[str, _Transcript] = {}
        self._context = context_fill.ContextTracker()
        self._git: dict[str, tuple[float, object]] = {}  # folder -> (when, gitops.WorkStatus | None)
        self._routes = self._route_table()

    # --- routing -------------------------------------------------------------

    def _route_table(self) -> list[tuple[str, re.Pattern, HTTPStatus, object]]:
        """Every route (api_common.route); the agents' and the views' come
        from their own modules."""
        ok = route
        return [
            ok(r"/status", lambda ids, q, b: self.status()),
            ok(r"/update/check", lambda ids, q, b: self.update_check(), "POST"),
            ok(r"/update", lambda ids, q, b: self.update_apply(), "POST"),
            ok(r"/restart", lambda ids, q, b: self.restart(), "POST"),
            ok(r"/conversations", lambda ids, q, b: self.conversations(archived=q.get("archived") in ("1", "true"))),
            ok(r"/conversations", lambda ids, q, b: self.create(b), "POST", HTTPStatus.CREATED),
            ok(r"/conversations/(\d+)", lambda ids, q, b: self.conversation(*ids)),
            ok(r"/conversations/(\d+)", lambda ids, q, b: self.delete(*ids), "DELETE"),
            ok(r"/conversations/(\d+)/messages", lambda ids, q, b: self.messages(
                *ids, after=int_of(q, "after"), before=int_of(q, "before"), limit=int_of(q, "limit") or MESSAGES_LIMIT)),
            ok(r"/conversations/(\d+)/messages", lambda ids, q, b: self.send(*ids, b), "POST", HTTPStatus.CREATED),
            ok(r"/conversations/(\d+)/retry", lambda ids, q, b: self.retry(*ids), "POST"),
            ok(r"/conversations/(\d+)/seen", lambda ids, q, b: self.seen(*ids), "POST"),
            ok(r"/conversations/(\d+)/archive", lambda ids, q, b: self.archive(*ids, b), "POST"),
            ok(r"/conversations/(\d+)/group", lambda ids, q, b: self.set_group(*ids, b), "POST"),
            ok(r"/conversations/(\d+)/export", lambda ids, q, b: self.export(*ids)),
            ok(r"/conversations/(\d+)/files", lambda ids, q, b: self.files(*ids, q.get("q", ""))),
            ok(r"/conversations/(\d+)/tasks", lambda ids, q, b: self.tasks(*ids)),
            ok(r"/conversations/(\d+)/tasks", lambda ids, q, b: self.add_task(*ids, b), "POST", HTTPStatus.CREATED),
            ok(r"/conversations/(\d+)/switches", lambda ids, q, b: self.switches(*ids, b), "POST"),
            ok(r"/conversations/(\d+)/runs", lambda ids, q, b: self.runs(*ids)),
            ok(r"/tasks/(\d+)", lambda ids, q, b: self.edit_task(*ids, b), "POST"),
            ok(r"/tasks/(\d+)", lambda ids, q, b: self.remove_task(*ids), "DELETE"),
            ok(r"/tasks/(\d+)/move", lambda ids, q, b: self.move_task(*ids, b), "POST"),
            ok(r"/runs/(\d+)/diff", lambda ids, q, b: self.diff(*ids)),
            ok(r"/runs/(\d+)/comments", lambda ids, q, b: self.comment(*ids, b), "POST", HTTPStatus.CREATED),
            ok(r"/runs/(\d+)/revert", lambda ids, q, b: self.revert(*ids, b), "POST"),
            ok(r"/groups", lambda ids, q, b: self.groups()),
            ok(r"/groups/rename", lambda ids, q, b: self.rename_group(b), "POST"),
            ok(r"/groups/delete", lambda ids, q, b: self.delete_group(b), "POST"),
            ok(r"/permissions", lambda ids, q, b: self.permissions()),
            ok(r"/permissions/(\d+)", lambda ids, q, b: self.decide(*ids, b), "POST"),
            ok(r"/search", lambda ids, q, b: self.search(q.get("q", ""))),
            ok(r"/waiting", lambda ids, q, b: self.waiting()),
            ok(r"/usage", lambda ids, q, b: self.usage()),
            ok(r"/projects", lambda ids, q, b: self.projects()),
            ok(r"/sessions", lambda ids, q, b: self.sessions(q.get("path", ""))),
            ok(r"/mcp-servers", lambda ids, q, b: self.mcp_servers(q.get("path", ""))),
            ok(r"/plugins", lambda ids, q, b: self.plugins()),
            ok(r"/launch-options", lambda ids, q, b: self.launch_options()),
            ok(r"/launch-presets", lambda ids, q, b: self.launch_presets()),
            ok(r"/prompt-presets", lambda ids, q, b: self.prompt_presets()),
            ok(r"/audit", lambda ids, q, b: self.audit(int_of(q, "limit") or 100)),
            ok(r"/devices", lambda ids, q, b: self.devices(int_of(q, CALLER), q.get("all") in ("1", "true"))),
            ok(r"/devices/(\d+)", lambda ids, q, b: self.rename_device(*ids, b), "POST"),
            ok(r"/devices/(\d+)", lambda ids, q, b: self.remove_device(*ids), "DELETE"),
        ] + self._agent_routes() + self._view_routes()

    def route(self, method: str, path: str, query: dict[str, str], body: dict) -> tuple[HTTPStatus, object]:
        if not path.startswith(PREFIX + "/"):
            raise ApiError(HTTPStatus.NOT_FOUND, _("Not found."))
        rest = path[len(PREFIX):].rstrip("/")
        for route_method, pattern, status, handler in self._routes:
            match = pattern.fullmatch(rest)
            if match and route_method == method:
                return status, handler([int(g) if g.isdigit() else g for g in match.groups()], query, body)
        raise ApiError(HTTPStatus.NOT_FOUND, _("Not found."))

    # --- endpoints ------------------------------------------------------------

    def status(self) -> dict:
        queues = [q for q in self.storage.list_queues() if not q.archived]
        return {
            "version": updates.running_version(),
            "language": i18n.language(),
            "daemon_running": daemon_alive(self.storage),
            "conversations": len(queues),
            "running": sum(q.status == QueueStatus.RUNNING for q in queues),
            "needs_approval": len(self.storage.queues_waiting_for_approval()),
            "update": self._update(),
            "restart_needed": updates.restart_needed(self.storage),
        }

    def devices(self, caller: int | None, history: bool = False) -> dict:
        """Who is signed in: each device's name, when it was made and last
        seen, from which address. With history also the devices signed out
        (read only): "revoked_at" and "revoked_by" ("app", "computer" or
        "token") say when and how; both are null for a device still signed in."""
        return {"devices": [
            {"id": d.id, "name": d.name, "created_at": iso(d.created_at), "last_seen_at": iso(d.last_seen_at),
             "last_ip": d.last_ip, "current": d.id == caller, "role": d.role,
             "revoked_at": iso(d.revoked_at), "revoked_by": d.revoked_by or None}
            for d in self.storage.list_devices(include_revoked=history)]}

    def audit(self, limit: int) -> dict:
        """What the devices did (api_audit), the newest first."""
        return {"entries": [
            {"at": e["at"], "device_id": e["device_id"], "device": e["device_name"], "ip": e["ip"],
             "method": e["method"], "path": e["path"], "status": e["status"], "detail": e["detail"]}
            for e in self.storage.list_audit(max(1, min(limit, 1000)))]}

    def pair(self, body: dict, ip: str) -> dict:
        """A phone that scanned the QR: its code for a device of its own."""
        from ccc import pairing

        name = text_of(body, "name").strip()[:60] or _("Phone")
        paired = pairing.redeem(self.storage, text_of(body, "code").strip(), name)
        if paired is None:
            raise ApiError(HTTPStatus.UNAUTHORIZED, _("The pairing code is wrong, used or has run out."))
        device, token = paired
        self.storage.touch_device(device.id, ip)
        return {"token": token, "device_id": device.id, "name": device.name, "version": updates.running_version()}

    def rename_device(self, device_id: int, body: dict) -> dict:
        name = text_of(body, "name").strip()
        if not name:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("The device needs a name."))
        if not self.storage.rename_device(device_id, name):
            raise ApiError(HTTPStatus.NOT_FOUND, _("No such device."))
        return {"ok": True}

    def remove_device(self, device_id: int) -> dict:
        if not self.storage.revoke_device(device_id, REVOKED_BY_APP):
            raise ApiError(HTTPStatus.NOT_FOUND, _("No such device."))
        return {"ok": True}

    def _update(self) -> dict | None:
        """A new ccc release on GitHub (ccc.updates), as the daemon
        last found it; None when looking is turned off."""
        if not updates.check_enabled(self.storage):
            return None
        state = updates.load(self.storage)
        reason = updates.refusal(self.storage) if state.available else None
        return {"available": state.available, "behind": state.behind, "remote_head": state.remote_head,
                "commits": state.commits, "version": state.version, "installed": state.installed,
                "notes": state.notes, "error": state.error, "checked_at": state.checked_at,
                "can_apply": state.available and reason is None, "reason": reason}

    def update_check(self) -> dict:
        updates.check(self.storage)
        return self._update() or {"available": False}

    def restart(self) -> dict:
        ok, message = updates.restart(self.storage)
        if not ok:
            raise ApiError(HTTPStatus.CONFLICT, message)
        return {"ok": True, "message": message}

    def update_apply(self) -> dict:
        ok, message = updates.apply(self.storage)
        if not ok:
            raise ApiError(HTTPStatus.CONFLICT, message)
        return {"ok": True, "message": message}

    def conversations(self, archived: bool = False) -> list[dict]:
        queues = [q for q in self.storage.list_queues() if q.archived == archived]
        unseen = self.storage.unseen_queue_ids()
        waiting = self.storage.queues_waiting_for_approval()
        # In list order: by group, as in the TUI.
        listed = []
        for group, members in groups.grouped(queues, self.storage.conversation_groups(), groups.mode(self.storage)):
            info = {"key": group.key, "title": group.title} if group is not None else None
            listed += [(q, info) for q in members]
        latest_goals = self.storage.latest_goals()
        return [self._summary(q, q.id in unseen, q.id in waiting, info, latest_goals.get(q.id)) for q, info in listed]

    def _summary(self, q, unseen: bool, needs_approval: bool, group: dict | None, goal=None) -> dict:
        return {
            "id": q.id,
            "name": q.name,
            "path": q.project_path,
            "status": q.status.value,
            "delivery_mode": q.delivery_mode.value,
            "archived": q.archived,
            "paused": q.paused,
            "auto_continue": q.auto_continue,
            "pending": self.storage.count_pending_items(q.id),
            "tokens_today": self.storage.queue_token_total(q.id, day=date.today()),
            "needs_approval": needs_approval,
            "unseen": unseen,
            "last_error": q.last_error,
            "next_retry_at": iso(q.next_retry_at),
            "updated_at": iso(q.updated_at),
            "group": group,
            "context": self._context_of(q),
            "git": self._git_of(q),
            "goal": self._goal_of(goal),
        }

    @staticmethod
    def _goal_of(goal) -> dict | None:
        """Its latest goal loop for the list: while it runs, and for a day after it ended (goals.shown); else null."""
        if not goals.shown(goal):
            return None
        return {"id": goal.id, "status": goal.status.value, "status_label": _(goals.STATUS_LABELS[goal.status]),
                "phase_label": _(goals.PHASE_LABELS[goal.phase]).format(n=goal.round),
                "round": goal.round, "max_rounds": goal.max_rounds, "finished_at": iso(goal.finished_at)}

    def _context_of(self, q) -> dict | None:
        """How full its context window is (context_fill): tokens, percent
        (None: window unknown) and the level the Settings thresholds give
        it — "" / "warn" / "danger"."""
        fill = self._context.fills(self.storage, [q]).get(q.id)
        if fill is None:
            return None
        warn, danger = context_fill.thresholds(self.storage)
        return {"tokens": fill.tokens, "percent": fill.percent, "level": context_fill.level(fill, warn, danger)}

    def _git_of(self, q) -> dict | None:
        """Its folder's branch and what's uncommitted / ahead / behind — only
        where Claude may run commands anyway (see gitops), and looked up at
        most every GIT_SECONDS per folder (a list asks for every conversation)."""
        from ccc import gitops

        if not gitops.claude_can_run_commands(q):
            return None
        now = time.monotonic()
        with self._lock:
            when, status = self._git.get(q.project_path, (0.0, None))
        if now - when >= GIT_SECONDS:
            status = gitops.work_status(q.project_path)
            with self._lock:
                self._git[q.project_path] = (now, status)
        if status is None:
            return None
        return {"branch": status.branch, "added": status.added, "removed": status.removed,
                "ahead": status.ahead, "behind": status.behind}

    def conversation(self, queue_id: int) -> dict:
        q = self._queue(queue_id)
        own = self.storage.conversation_groups().get(q.id)
        summary = self._summary(
            q, q.id in self.storage.unseen_queue_ids(), q.id in self.storage.queues_waiting_for_approval(),
            {"key": f"own:{own}", "title": own} if own else None,  # its own group (the list adds projects)
            self.storage.latest_goals().get(q.id),
        )
        summary.update({
            "session_id": q.session_id,
            "flags": launch_options.describe_flags(q.launch_flags),
            "remote_control_url": q.remote_control_url,
            "queued": [
                {"id": item.id, "text": item.prompt_text, "not_before": iso(item.not_before)}
                for item in self.storage.list_pending_items(q.id)
            ],
            "permissions": [self._permission(r) for r in self.storage.pending_permission_requests(q.id)],
        })
        return summary

    def messages(self, queue_id: int, after: int | None = None, before: int | None = None,
                 limit: int = MESSAGES_LIMIT) -> dict:
        """Transcript events, each with its index: ?after=N the ones after
        N (polling for new ones), ?before=N the ones before (scrolling
        back); at most `limit`, the newest of them."""
        events = self._events(self._queue(queue_id).session_id)
        start = after + 1 if after is not None else 0
        end = before if before is not None else len(events)
        start, end = max(start, 0), min(end, len(events))
        start = max(start, end - max(limit, 1))
        return {
            "total": len(events),
            "messages": [
                {
                    "index": i,
                    "kind": e.kind,
                    "text": i18n.retranslate(e.text) if e.kind == "tool_result" else e.text,
                    "time": iso(e.timestamp),
                    "tool_use_id": e.tool_use_id,
                    "detail": e.detail,
                }
                for i, e in enumerate(events[start:end], start)
            ],
        }

    def send(self, queue_id: int, body: dict) -> dict:
        self._queue(queue_id)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, _("The message is empty."))
        item = send_message(self.storage, queue_id, text)
        return {"id": item.id, "text": item.prompt_text}

    def retry(self, queue_id: int) -> dict:
        self._queue(queue_id)
        return {"retried": self.storage.retry_queue(queue_id)}

    def seen(self, queue_id: int) -> dict:
        self._queue(queue_id)
        self.storage.mark_seen(queue_id)
        return {"ok": True}

    def archive(self, queue_id: int, body: dict) -> dict:
        """{"archived": true} into the Archive, false back (as x in the TUI)."""
        self._queue(queue_id)
        archived = body.get("archived")
        if not isinstance(archived, bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, _("archived must be true or false."))
        self.storage.set_queue_archived(queue_id, archived)
        return self.conversation(queue_id)

    def delete(self, queue_id: int) -> dict:
        """Out of ccc with its tasks and history; Claude Code's transcript stays."""
        queue = self._queue(queue_id)
        if queue.status == QueueStatus.RUNNING:
            raise ApiError(HTTPStatus.CONFLICT,
                           _("Claude is working on '{name}' right now — wait or pause it first.").format(name=queue.name))
        self.storage.delete_queue(queue_id)
        return {"deleted": queue_id}

    # --- the task queue (t in the TUI) -------------------------------------------

    def tasks(self, queue_id: int) -> dict:
        """The tasks waiting in the conversation, in order, and its two switches."""
        queue = self._queue(queue_id)
        return {
            "paused": queue.paused,
            "auto_continue": queue.auto_continue,
            "tasks": [
                {"id": item.id, "text": item.prompt_text, "not_before": iso(item.not_before),
                 "by_routine": item.routine_id is not None}
                for item in self.storage.list_pending_items(queue_id)
            ],
        }

    def add_task(self, queue_id: int, body: dict) -> dict:
        """{"text", "when"}: when is empty (as soon as possible), HH:MM, YYYY-MM-DD HH:MM or "reset"."""
        self._queue(queue_id)
        text = text_of(body, "text").strip()
        if not text:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("The task is empty."))
        item = self.storage.add_queue_item(queue_id, text, not_before=self._when(body))
        return {"id": item.id, "text": item.prompt_text, "not_before": iso(item.not_before)}

    def edit_task(self, item_id: int, body: dict) -> dict:
        """{"text"} and / or {"when"} of a task that hasn't started."""
        item = self._pending_item(item_id)
        if "text" in body:
            text = text_of(body, "text").strip()
            if not text:
                raise ApiError(HTTPStatus.BAD_REQUEST, _("The task is empty."))
            self.storage.update_item_text(item.id, text)
        if "when" in body:
            self.storage.update_item_schedule(item.id, self._when(body))
        return self.tasks(item.queue_id)

    def remove_task(self, item_id: int) -> dict:
        item = self._pending_item(item_id)
        self.storage.skip_item(item.id)
        return self.tasks(item.queue_id)

    def move_task(self, item_id: int, body: dict) -> dict:
        """{"delta": -1} up, 1 down."""
        item = self._pending_item(item_id)
        delta = body.get("delta")
        if delta not in (-1, 1):
            raise ApiError(HTTPStatus.BAD_REQUEST, _("delta must be -1 or 1."))
        self.storage.move_item(item.id, delta)
        return self.tasks(item.queue_id)

    def switches(self, queue_id: int, body: dict) -> dict:
        """{"paused": bool} start no further tasks; {"auto_continue": bool} after a usage limit."""
        self._queue(queue_id)
        for key, setter in (("paused", self.storage.set_queue_paused),
                            ("auto_continue", self.storage.set_queue_auto_continue)):
            if key in body:
                if not isinstance(body[key], bool):
                    raise ApiError(HTTPStatus.BAD_REQUEST, _("{name} must be true or false.").format(name=key))
                setter(queue_id, body[key])
        return self.tasks(queue_id)

    def _pending_item(self, item_id: int):
        item = self.storage.get_item(item_id)
        if item is None:
            raise ApiError(HTTPStatus.NOT_FOUND, _("No such task."))
        if item.status != ItemStatus.PENDING:
            raise ApiError(HTTPStatus.CONFLICT, _("This task has already started."))
        return item

    @staticmethod
    def _when(body: dict) -> datetime | None:
        from ccc.limits import RESET_BUFFER
        from ccc.schedule import parse_when

        text = text_of(body, "when")
        reset_at = None
        if text.strip().lower() == "reset":
            from ccc.usage_api import get_pro_usage

            usage = get_pro_usage()
            if usage is not None and usage.five_hour is not None:
                reset_at = usage.five_hour.resets_at.astimezone().replace(tzinfo=None) + RESET_BUFFER
        try:
            return parse_when(text, datetime.now(), reset_at=reset_at)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    # --- runs, their commits and diffs (the queue detail in the TUI) -----------------

    def runs(self, queue_id: int) -> list[dict]:
        """The conversation's runs, newest first: status, tokens, its commit."""
        self._queue(queue_id)
        return [
            {
                "id": run.id,
                "started_at": iso(run.started_at),
                "finished_at": iso(run.finished_at),
                "status": run.status.value,
                "tokens": run.input_tokens + run.output_tokens + run.cache_read_tokens + run.cache_creation_tokens,
                "cost_usd": run.cost_usd,
                "duration_ms": run.duration_ms,
                "task": run.prompt_text,
                "result": i18n.retranslate(run.result_text),
                "error": i18n.retranslate(run.error_text),
                "commit": run.git_commit,
                "git_note": i18n.retranslate(run.git_note),
                "reverted_by": run.reverted_by,
            }
            for run in self.storage.runs_for_queue(queue_id)
        ]

    def _run(self, run_id: int):
        run = self.storage.find_run(run_id)
        if run is None:
            raise ApiError(HTTPStatus.NOT_FOUND, _("No such run."))
        return run

    def _diff_lines(self, run) -> list:
        from ccc import gitops
        from ccc.diff_comments import parse

        if not run.git_commit:
            raise ApiError(HTTPStatus.CONFLICT, i18n.retranslate(run.git_note) or _("This run made no commit."))
        queue = self._queue(run.queue_id)
        # As everywhere in ccc: git only where Claude may run commands anyway (see gitops).
        if not gitops.claude_can_run_commands(queue) or not gitops._COMMIT.match(run.git_commit):
            raise ApiError(HTTPStatus.CONFLICT,
                           _("ccc runs git only in conversations started with “Skip all permission checks”."))
        return parse(gitops.show(queue.project_path, run.git_commit))

    def diff(self, run_id: int) -> dict:
        """The run's commit (git show), line by line: what each line is, its
        file and numbers; the commentable ones can carry a comment."""
        run = self._run(run_id)
        return {
            "commit": run.git_commit,
            "task": task_line(run.prompt_text),
            "lines": [
                {"index": i, "text": line.text, "kind": line.kind, "path": line.path,
                 "old_line": line.old_line, "new_line": line.new_line, "commentable": line.commentable}
                for i, line in enumerate(self._diff_lines(run))
            ],
        }

    def comment(self, run_id: int, body: dict) -> dict:
        """{"comments": [{"index", "text"}]} on lines of the diff, sent to
        Claude as one task (like inline comments on a pull request)."""
        from ccc.diff_comments import Comment, prompt

        run = self._run(run_id)
        given = body.get("comments")
        if not isinstance(given, list) or not given:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("No comments."))
        lines = self._diff_lines(run)
        comments = []
        for entry in given:
            index = entry.get("index") if isinstance(entry, dict) else None
            text = entry.get("text") if isinstance(entry, dict) else None
            if not isinstance(index, int) or not 0 <= index < len(lines) or not lines[index].commentable \
                    or not isinstance(text, str) or not text.strip():
                raise ApiError(HTTPStatus.BAD_REQUEST, _("Every comment needs a changed line and a text."))
            comments.append((index, Comment(lines[index], text.strip())))
        item = self.storage.add_queue_item(
            run.queue_id, prompt(run.git_commit, task_line(run.prompt_text, 200), [c for _i, c in sorted(comments)]))
        return {"id": item.id, "comments": len(comments)}

    def revert(self, run_id: int, body: dict) -> dict:
        """Undo the run's commit with a new commit; {"tell": true} also queues a note for Claude."""
        from ccc import revert

        run = self._run(run_id)
        try:
            commit = revert.revert(self.storage, run)
        except revert.RevertError as exc:
            raise ApiError(HTTPStatus.CONFLICT, str(exc)) from exc
        if body.get("tell") is True:
            revert.tell_claude(self.storage, run, commit)
        return {"commit": commit}

    # --- groups -----------------------------------------------------------------

    def groups(self) -> dict:
        """The grouping mode and your own groups' names."""
        own = sorted(set(self.storage.conversation_groups().values()), key=str.casefold)
        return {"mode": groups.mode(self.storage), "own": own}

    def set_group(self, queue_id: int, body: dict) -> dict:
        """{"name": "…"} into that own group (made if new); null or "" out of it."""
        self._queue(queue_id)
        name = body.get("name")
        if name is not None and not isinstance(name, str):
            raise ApiError(HTTPStatus.BAD_REQUEST, _("name must be text or null."))
        self.storage.set_conversation_group(queue_id, (name or "").strip() or None)
        return self.conversation(queue_id)

    def rename_group(self, body: dict) -> dict:
        old, new = text_of(body, "old").strip(), text_of(body, "new").strip()
        if not old or not new:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("Give the group's name and its new name."))
        groups.rename(self.storage, old, new)
        return self.groups()

    def delete_group(self, body: dict) -> dict:
        name = text_of(body, "name").strip()
        if not name:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("Give the group's name."))
        groups.delete(self.storage, name)
        return self.groups()

    # --- finding and sharing -------------------------------------------------------

    def search(self, text: str) -> list[dict]:
        """Every conversation's messages (archived too), newest first."""
        from ccc.search import search_conversations

        return [
            {"conversation_id": h.queue_id, "name": h.queue_name, "time": iso(h.timestamp), "kind": h.kind,
             "snippet": h.snippet}
            for h in search_conversations(self.storage, text)
        ]

    def export(self, queue_id: int) -> dict:
        """The conversation as Markdown, secrets masked — to share from the phone."""
        from ccc.search import markdown_export

        self._queue(queue_id)
        filename, text = markdown_export(self.storage, queue_id)
        return {"filename": filename, "markdown": text}

    def files(self, queue_id: int, text: str) -> list[str]:
        """Files of the conversation's folder for an @-mention, best first."""
        from ccc import gitops
        from ccc.file_mentions import INDEX, rank

        queue = self._queue(queue_id)
        return rank(INDEX.files(queue.project_path, gitops.claude_can_run_commands(queue)), text)

    def waiting(self) -> list[dict]:
        """Conversations that need you, most urgent first (Alt+N in the TUI)."""
        from ccc import attention

        return [
            {"conversation_id": w.queue_id, "name": w.name, "kind": w.kind, "label": _(attention.LABELS[w.kind])}
            for w in attention.waiting(self.storage)
        ]

    def prompt_presets(self) -> list[dict]:
        """The preset prompts (Alt+1 … Alt+9): sent at once, or put into the message box."""
        from ccc.prompt_presets import load_prompt_presets

        return [
            {"number": i, "name": p.name, "text": p.text, "send_now": p.send_now}
            for i, p in enumerate(load_prompt_presets(self.storage), 1)
        ]

    def permissions(self) -> list[dict]:
        names = {q.id: q.name for q in self.storage.list_queues()}
        return [
            dict(self._permission(r), conversation=names.get(r.queue_id))
            for queue_id in sorted(self.storage.queues_waiting_for_approval())
            for r in self.storage.pending_permission_requests(queue_id)
        ]

    @staticmethod
    def _permission(request) -> dict:
        return {
            "id": request.id,
            "conversation_id": request.queue_id,
            "tool": request.tool_name,
            "input": request.input,
            "created_at": iso(request.created_at),
        }

    def decide(self, request_id: int, body: dict) -> dict:
        decision = body.get("decision")
        if decision not in DECISIONS:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("decision must be allow, always or deny."))
        request = self.storage.get_permission_request(request_id)
        if request is None:
            raise ApiError(HTTPStatus.NOT_FOUND, _("No such approval."))
        if request.status != "pending":
            raise ApiError(HTTPStatus.CONFLICT, _("This approval has already been decided."))
        if decision == "always":
            self.storage.always_allow_tool(request.queue_id, request.tool_name)
        self.storage.decide_permission_request(request.id, allow=decision != "deny")
        return {"id": request.id, "decision": decision}

    def usage(self) -> dict | None:
        from ccc.usage_api import get_pro_usage

        usage = get_pro_usage()
        if usage is None:
            return None

        def window(w) -> dict | None:
            return {"percent": w.utilization_pct, "resets_at": iso(w.resets_at)} if w else None

        return {
            "five_hour": window(usage.five_hour),
            "seven_day": window(usage.seven_day),
            "per_model": [
                {"model": m.model, "percent": m.utilization_pct, "resets_at": iso(m.resets_at)}
                for m in usage.per_model
            ],
        }

    def projects(self) -> list[str]:
        return self.storage.recent_project_paths()

    def sessions(self, path: str) -> list[dict]:
        """Claude Code sessions recorded in a folder, newest first (to resume one)."""
        from ccc.transcript import list_sessions_for_directory

        if not path.strip():
            return []
        folder = api_scope.check_folder(self.storage, path)
        if not os.path.isdir(folder):
            return []
        return [
            {"session_id": s.session_id, "modified": iso(s.modified), "first_prompt": s.first_prompt,
             "messages": s.message_count}
            for s in list_sessions_for_directory(folder)
        ]

    def mcp_servers(self, path: str) -> list[str]:
        """Names (never the definitions — they can hold tokens) of the MCP
        servers Claude Code would load in a folder."""
        return mcp_servers.names(api_scope.check_folder(self.storage, path)) if path.strip() else []

    def plugins(self) -> dict:
        """Ids of the installed Claude Code plugins and the ones the user has
        switched on (what a conversation inherits unless it picks its own)."""
        return {"installed": plugins.installed(), "enabled": plugins.enabled_by_default()}

    def launch_presets(self) -> dict:
        """The New conversation window's presets and the last choice."""
        from ccc import presets

        def choice(c) -> dict | None:
            if c is None:
                return None
            return {"options": sorted(c.selection), "auto_continue": c.auto_continue,
                    "remote_control": c.remote_control}

        return {
            "last": choice(presets.load_last(self.storage)),
            "presets": [dict(choice(c), name=name) for name, c in presets.list_presets(self.storage).items()],
        }

    def launch_options(self) -> dict:
        return {
            "groups": [
                {
                    "key": group.key,
                    "title": _(group.title) if group.title else "",
                    "exclusive": group.exclusive,
                    "required": group.required,
                    "options": [{"key": o.key, "label": _(o.label)} for o in group.options],
                }
                for group in launch_options.GROUPS
            ],
            "default": sorted(api_scope.default_selection(self.storage, launch_options.DEFAULT_SELECTION)),
        }

    def create(self, body: dict) -> dict:
        """A new conversation, as the New conversation window starts it.
        Options missing from a required group (session, permissions) are
        the defaults."""
        path = body.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, _("Choose a directory."))
        folder = api_scope.check_folder(self.storage, path.strip())
        selection = self._selection(body.get("options"))
        api_scope.check_selection(self.storage, selection)
        try:
            queue = start_conversation(
                self.storage, folder, selection,
                name=text_of(body, "name"), session_id=text_of(body, "session_id") or None, pr=text_of(body, "pr"),
                auto_continue=bool(body.get("auto_continue", True)),
                remote_control=bool(body.get("remote_control", False)),
                interactive=bool(body.get("interactive", True)),
                worktree=bool(body.get("worktree", False)),
            )
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        from ccc.presets import LaunchChoice, save_last

        save_last(self.storage, LaunchChoice(selection, bool(body.get("auto_continue", True)),
                                             bool(body.get("remote_control", False))))
        prompt = body.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            send_message(self.storage, queue.id, prompt)
        return self.conversation(queue.id)

    def _selection(self, options) -> frozenset[str]:
        defaults = api_scope.default_selection(self.storage, launch_options.DEFAULT_SELECTION)
        if options is None:
            return defaults
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            raise ApiError(HTTPStatus.BAD_REQUEST, _("options must be a list of option keys."))
        selection = set(options)
        for group in launch_options.GROUPS:
            chosen = selection & {o.key for o in group.options}
            if group.exclusive and len(chosen) > 1:
                raise ApiError(HTTPStatus.BAD_REQUEST,
                               _("Choose one of: {options}.").format(options=", ".join(sorted(chosen))))
            if group.required and not chosen:
                selection |= {o.key for o in group.options} & defaults
        known = {o.key for g in launch_options.GROUPS for o in g.options}
        unknown = {k for k in selection - known if not (launch_options.is_mcp_server(k) or launch_options.is_plugin(k))}
        if unknown:
            raise ApiError(HTTPStatus.BAD_REQUEST,
                           _("Unknown options: {options}.").format(options=", ".join(sorted(unknown))))
        return frozenset(selection)

    # --- changes (the events stream) -----------------------------------------

    def snapshot(self) -> dict[int, tuple]:
        """What the app shows of each conversation; a difference between
        two snapshots is a change to tell it about."""
        unseen = self.storage.unseen_queue_ids()
        waiting = self.storage.queues_waiting_for_approval()
        own = self.storage.conversation_groups()
        result = {}
        for q in self.storage.list_queues():
            transcript = self._transcript(q.session_id)
            try:
                size = transcript.path.stat().st_size if transcript.path else 0
            except OSError:
                size = 0
            result[q.id] = (
                q.name, q.status.value, q.archived, q.paused, q.last_error, own.get(q.id),
                self.storage.count_pending_items(q.id), q.id in waiting, q.id in unseen, size,
            )
        # Not a conversation: a new ccc version (updates) — told to the app as "update".
        state = updates.load(self.storage)
        result[UPDATE_KEY] = (state.version,) if state.available else ()
        return result

    @staticmethod
    def changes(before: dict[int, tuple], after: dict[int, tuple]) -> dict | None:
        changed = sorted(i for i in after if i != UPDATE_KEY and before.get(i) != after[i])
        removed = sorted(set(before) - set(after) - {UPDATE_KEY})
        update = before.get(UPDATE_KEY, ()) != after.get(UPDATE_KEY, ())
        if not changed and not removed and not update:
            return None
        return {"conversations": changed, "removed": removed, **({"update": True} if update else {})}

    # --- helpers --------------------------------------------------------------

    def _queue(self, queue_id: int):
        queue = self.storage.find_queue(queue_id)
        if queue is None:
            raise ApiError(HTTPStatus.NOT_FOUND, _("No such conversation."))
        return queue

    def _transcript(self, session_id: str) -> _Transcript:
        with self._lock:
            cached = self._transcripts.setdefault(session_id, _Transcript())
            now = time.monotonic()
            if cached.path is None and now - cached.looked_up >= TRANSCRIPT_LOOKUP_SECONDS:
                cached.looked_up = now
                cached.path = find_transcript_path(session_id)
            return cached

    def _events(self, session_id: str) -> list[TranscriptEvent]:
        cached = self._transcript(session_id)
        with self._lock:
            if cached.path is None:
                return []
            try:
                events, cached.offset = parse_transcript_events(cached.path, since_byte_offset=cached.offset)
            except OSError:
                return list(cached.events)
            cached.events.extend(events)
            return list(cached.events)


class _Handler(BaseHTTPRequestHandler):
    api: Api
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        url = urlsplit(self.path)
        query = {k: v[-1] for k, v in parse_qs(url.query).items() if k != CALLER}
        # The phone's language (Accept-Language) for this request only; an app that doesn't say gets the
        # language set in the TUI, as before.
        asked = i18n.from_accept_language(self.headers.get("Accept-Language", ""))
        language_token = i18n.use_for_request(asked)
        body: dict = {}
        denied_folder = ""
        self._device = None
        try:
            i18n.load(self.api.storage)
            if method == "POST" and url.path == PREFIX + "/pair":
                paired = self.api.pair(self._body(), self._client_ip())  # a refusal is logged below, once
                self._audit(method, url.path, HTTPStatus.CREATED, device=self.api.storage.device_by_id(paired["device_id"]))
                self._send(HTTPStatus.CREATED, paired)
                return
            if not self._authorized():
                raise ApiError(HTTPStatus.UNAUTHORIZED, _("Missing or wrong API token."))
            if method == "GET" and url.path == PREFIX + "/events":
                self._events()
                return
            self._check_role(method, url.path)
            query[CALLER] = str(self._device.id)
            body = self._body() if method == "POST" else {}
            status, result = self.api.route(method, url.path, query, body)
            result = api_roles.strip_for(self._device.role, result)
        except api_scope.FolderNotAllowed as exc:
            status, result = exc.status, {"error": exc.message}
            denied_folder = exc.folder
        except ApiError as exc:
            status, result = exc.status, {"error": exc.message}
        except Exception:
            logger.exception("API request %s %s failed", method, url.path)
            status, result = HTTPStatus.INTERNAL_SERVER_ERROR, {"error": _("Something went wrong in ccc.")}
        finally:
            i18n.end_request(language_token)
        self._audit(method, url.path, status, detail=self._detail(method, url.path, body), denied_folder=denied_folder)
        self._send(status, result)

    @staticmethod
    def _detail(method: str, path: str, body: dict) -> str:
        """The one thing worth keeping of a request's body: the folder of a new conversation."""
        folder = body.get("path") if method == "POST" and path == PREFIX + "/conversations" else None
        return folder if isinstance(folder, str) else ""

    def _audit(self, method: str, path: str, status: int, detail: str = "", device=None, denied_folder: str = "") -> None:
        """Log the request (api_audit) and raise the alarm for what is out of the ordinary."""
        device = device or self._device
        ip = self._client_ip()
        if denied_folder:  # the role was fine, the folder is not allowed: say so, and once per folder
            api_audit.record_folder_denied(self.api.storage, device, ip, method, path, denied_folder)
            api_audit.alarm_folder_denied(self.api.storage, device, denied_folder)
            return
        api_audit.record(self.api.storage, device, ip, method, path, status, detail)
        api_audit.alarm_for(self.api.storage, status, method, path, ip, device)

    def _check_role(self, method: str, path: str) -> None:
        """403 when the device's role is too weak for the request (api_roles)."""
        needed = api_roles.required_role(method, path.removeprefix(PREFIX), self._device.id)
        if not api_roles.allows(self._device.role, needed):
            logger.warning("device %s (%s) refused %s %s: needs %s", self._device.id, self._device.role, method, path, needed)
            raise ApiError(HTTPStatus.FORBIDDEN, _("This device may not do that: it is {role}, this needs {needed}.").format(
                role=self._device.role, needed=needed))

    def _client_ip(self) -> str:
        return client_ip(self.client_address[0], self.headers.get("X-Forwarded-For", ""))

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        self._token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        device = authenticate(self.api.storage, self._token)
        if device is None:
            return False
        self._device = device
        seen = device.last_seen_at
        if seen is None or (datetime.now() - seen).total_seconds() > SEEN_SECONDS:
            self.api.storage.touch_device(device.id, self._client_ip())
        return True

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("Bad request body."))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, _("The body isn't valid JSON.")) from exc
        if not isinstance(body, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, _("The body must be a JSON object."))
        return body

    def _send(self, status: HTTPStatus, result: object) -> None:
        data = json.dumps(result, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _events(self) -> None:
        """Server-Sent Events: "changed" whenever a conversation changes
        (status, queued messages, approvals, a new transcript line);
        the app then fetches what it shows."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.close_connection = True
        before = self.api.snapshot()
        quiet = 0.0
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                time.sleep(EVENTS_POLL_SECONDS)
                if not token_valid(self.api.storage, self._token):
                    return  # the device was signed out
                after = self.api.snapshot()
                change = self.api.changes(before, after)
                before = after
                if change is not None:
                    self.wfile.write(f"event: changed\ndata: {json.dumps(change)}\n\n".encode())
                    quiet = 0.0
                else:
                    quiet += EVENTS_POLL_SECONDS
                    if quiet < EVENTS_KEEPALIVE_SECONDS:
                        continue
                    self.wfile.write(b": keepalive\n\n")
                    quiet = 0.0
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the app went away

    def log_message(self, format: str, *args) -> None:
        logger.debug("%s " + format, self.address_string(), *args)


def make_server(storage: Storage, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"api": Api(storage)})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(db_path: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    storage = Storage(db_path)
    i18n.load(storage)
    server = make_server(storage, host, port)
    if not has_token(storage):
        logger.warning("no API token yet — every request is refused until you make one: ccc api token")
    logger.info("ccc API on http://%s:%s%s", host, port, PREFIX)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
