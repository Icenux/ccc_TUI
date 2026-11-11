# Android app & API

ccc has an Android app (its own repository,
[ccc_android](https://github.com/Icenux/ccc_android) — how to install it and
check the download: its README). It talks
to ccc over a small HTTP API: the conversations and their messages,
sending a message, approving tool calls, starting a conversation, usage
limits — and a stream of changes, so the phone sees a new reply or an
approval right away.

Like the TUI, the API only reads and writes ccc's database; the daemon
does the work. It's reachable only from your
[Tailscale](https://tailscale.com) network, and every request needs a
token.

## Turning it on

1. Install its service: `bin/install.sh --api` (with the other options
   you use). The API then runs as `ccc-api.service` and listens on
   `127.0.0.1:8737` — on this computer only.
2. Put it on your tailnet with HTTPS: `tailscale serve --bg 8737`. It's
   then at `https://<computer>.<tailnet>.ts.net/api/v1/` — `tailscale
   serve status` prints the address. The proxy sits on this computer, so
   ccc takes the phone's own address for *Devices* from its
   `X-Forwarded-For` header (and only from a loopback connection).
3. `ccc api token` makes the token the app signs in with and prints it
   — **only this once**: ccc keeps just its fingerprint (SHA-256), so
   nobody can read it from the database. Lost it, or lost the phone?
   `ccc api token --new` makes a new one; every device signed in before
   is signed out at once, open connections too. Each device has a token
   of its own and can be signed out alone: `ccc api devices` lists
   them, `ccc api revoke ID` signs one out. Signing out never deletes the
   row: it stays as read-only history (`ccc api devices --all`) with when
   and how the device was signed out — in the app, on the computer, by a new token,
   or because it is gone: a device not seen for 30 days is taken for uninstalled
   (an uninstall cannot tell ccc) — and its token stops working. A dropped connection alone
   (a sleeping phone, a restart of the API) signs nothing out. When an app that is signed in connects with another token
   (a new QR code, or a typed one), it signs its old device out first, so
   the same phone doesn't pile up in the list. The app shows the same
   history under *Connection → Devices → History*. An app whose token was
   revoked notices on its next request (the open events stream ends too):
   it forgets the token, stops asking and shows the sign-in screen.
4. In the app enter the address (just `<computer>.<tailnet>.ts.net`
   will do) and the token — or skip steps 3 and 4 and **pair by QR code**:

   *Settings → Connection and updates → Pair a new device* (or `ccc api pair` in a
   shell) shows a QR code; in the app choose *Connect → Scan QR*. The
   code holds the API's address and a one-time pairing code, good for 5
   minutes and for one phone; the app trades it for a token of its own
   (`POST /pair`, the one request that needs no token), so nothing is
   typed and the token never goes through the picture. The QR has a window of its own; once the
   phone has paired it closes and the Devices list highlights the new
   device. That list (right below, in the same tab) shows every signed-in phone (name, when it paired,
   last seen, address), refreshes by itself, and *Sign out* removes one.
   *History* switches to the devices signed out before (when, and how:
   in the app, on the computer, by a new token) — read only, nothing can
   be deleted from it.
   ccc finds the
   address in `tailscale serve status`; when it can't, give it with
   `ccc api pair --server https://<computer>.<tailnet>.ts.net`.
   *Settings → Connection and updates → Devices* lists the phones signed in
   and signs one out.

Without the service, `ccc api serve` runs it in the terminal
(`--port` for another port).

## In the app

Everything ccc's window does, made for the phone: the conversations,
the task queue, reviews, goals, workflows,
routines, live sessions, analytics, settings — and an interactive
conversation's Claude Code, live, with keys for its menus.

**Notifications** (Connection → *Notify while the app is closed*): the
app keeps its connection to ccc open in the background and tells you
about an approval — answer it right in the notification —, an error, a
usage limit or a finished reply. No push server is involved; on Xiaomi
/ HyperOS allow the app to run in the background, or the system stops
it.

**Updates:** `GET /status` has `update` (the newer GitHub release: `version`, `installed`, `notes`, `can_apply`, why not; `behind`, `remote_head` and `commits` are kept for older apps: 1, the version and the first lines of the notes); `GET /status` also has `restart_needed` (ccc was upgraded under its running processes) and `POST /restart` restarts ccc's services — only while no task runs (409 with the reason); admin role. `POST /update/check` looks now, `POST /update` updates ccc (move to the release, install, restart; 409 with the reason when it can't) — see [Settings](settings.md#updating-ccc). The app shows a banner with an *Update* button; the events stream says `update` when a new version appears.

## Updating the app

The app is updated by version, not through ccc, and from its own repository: a tag `X.Y.Z` (no `v`) in [ccc_android](https://github.com/Icenux/ccc_android) makes GitHub Actions build and test the app; the maintainer then signs it on their own computer and attaches `ccc.apk` to the release (the signing key never reaches GitHub). The app asks GitHub for that repository's newest release (when it comes to the foreground, at most every six hours; no token is sent), and when its version is lower it shows "A new version of the app is available: X" with a *Download and install* button. The download goes to Android's installer, which asks first — and the first time asks to allow installing from the app. A build made by hand has the version `0.0.0-dev` and never asks. The app and ccc are versioned separately; the API stays compatible, so an older app keeps working with a newer ccc.

**What the list shows** (Connection → *Conversation list*): the git state, the queued tasks and the folder, each on or off —
kept on the phone, so a small screen can show less.

## What a device may do

Every paired device may do everything — there are no roles. A stolen phone
or token is therefore a way into the computer: sign the device out at once
(*Settings → Devices → Sign out*, or `ccc api revoke ID`), and see what it did
in `ccc api log`.

## Folders and permission checks

A conversation is Claude Code running as you in a folder, so the computer —
not the phone — decides where and how freely the app may start one:

- **Folders:** `ccc api roots --add ~/DEV` allows a folder and everything
  inside it (`--remove` stops, no option lists). Until you set one, the app
  may use only the folders ccc already has conversations in — it cannot
  walk to `~` or `/` (a conversation once started in `~` opens nothing). The check follows symlinks. It covers starting a
  conversation, prompting a live session, `GET /sessions` and `GET /mcp-servers`; a folder outside
  gets `403`.
- **Skip all permission checks:** refused (`403`) from the app until
  `ccc api skip-permissions on` (`off` takes it back). Meanwhile the app's
  default is *Ask for everything*: you approve in the app.

## Log and alarms

If a token is ever misused you should be able to see it and hear of it.
The API logs everything that changes something, every refusal (`401` /
`403`) and every pairing — never the query, the body or typed keys, only
the path and the folder of a new conversation. Read it with
`ccc api log` (`-n` how many), in *Devices → Activity*, or `GET /audit`. The newest 5000 entries are kept.

Through your Telegram / Discord notifications ([Settings](settings.md))
ccc also tells you at once about what is out of the ordinary: a new
device paired, a wrong pairing code, a request with a wrong token, a
device asking for what is not allowed — each kind at most once in
5 minutes.

## Securing the connection

`ccc api doctor` checks the lot at once and says what to do: the API
listens on this computer only (the tailnet reaches it through
`tailscale serve`), nothing is on the public internet (`tailscale funnel`),
the database is private (mode 600 in a 700 folder — ccc sets it itself), how
many devices may do everything, which have not been seen for 90 days, the
limits and whether alarms can reach you. It exits with 1 when something is
wrong. The `ccc-api` service also runs hardened (no new privileges, only
TCP and unix sockets, files private to you).

**Tailscale ACL** — by default every device on the tailnet reaches every
other. Tag the computer and the phone and let only the phone reach the
API's HTTPS port (Tailscale admin console → Access controls; tag the
devices there or with `tailscale up --advertise-tags=tag:ccc-server`):

```json
{
  "tagOwners": {
    "tag:ccc-server": ["autogroup:admin"],
    "tag:ccc-phone":  ["autogroup:admin"]
  },
  "acls": [
    {"action": "accept", "src": ["tag:ccc-phone"], "dst": ["tag:ccc-server:443"]}
  ]
}
```

Keep your other rules next to it (the example replaces the default
allow-all, so add what else you need). Also worth turning on: device
approval and a short key expiry for the phone, and *Tailnet Lock*.

## Endpoints

Every path starts with `/api/v1`; every request has the header
`Authorization: Bearer <token>`. Answers are JSON; an error is
`{"error": "…"}`. Texts ccc sends (errors, labels, settings choices) come
in the language of the `Accept-Language` header — the app sends the
app's own language (Settings → General, also in Connection; by default the phone's); English or Czech, any other language gets English. Without the
header (an older app) they follow ccc's own language, set in the TUI.
That language and the TUI's look are the computer's own: the app does
not show or change them (it has its own language setting, and its look is fixed); the
settings it shows are the ones that apply to both sides.

| | |
|---|---|
| `GET /status` | the daemon running?, how many conversations, running, waiting for approval |
| `GET /conversations` | the list in the TUI's order, with groups, states (`status` is the queue's; `live_busy` is true while an interactive Claude Code works in its terminal, so the TUI and the app show it as running; `context`: how full its context window is — `percent`, `tokens`, `window` — as Claude Code's status line reports it via [`ccc statusline`](command-line.md#status-line), `null` until it did), queued messages and markers, and its git state (`git`: branch, added, removed, ahead, behind; `null` where Claude may not run commands) and its latest [goal loop](goal.md) (`goal`: id, status, status_label, phase_label, round, max_rounds, finished_at; while it runs and for a day after it ended, else `null`); `?archived=1` the archive |
| `GET /conversations/{id}` | one, with its queued messages (`queued`; `sending` is true for the one ccc has taken and is waiting for Claude Code to be free) and approvals waiting |
| `GET /conversations/{id}/messages` | its transcript, each message with an `index`; `?after=N` only newer ones, `?before=N` older ones, `?limit=` how many (200) |
| `POST /conversations/{id}/messages` | `{"text": "…"}` queues a message, as the message box does |
| `POST /conversations/{id}/retry` | retry / resume a blocked or paused conversation (`r`) |
| `POST /conversations/{id}/seen` | mark it read |
| `POST /conversations/{id}/archive` | `{"archived": true \| false}` — into the [Archive](archive.md) / back |
| `DELETE /conversations/{id}` | delete it from ccc (not while Claude works on it); Claude Code's transcript stays |
| `POST /conversations/{id}/group` | `{"name": "…"}` into an own group (made if new), `null` out of it |
| `GET /conversations/{id}/export` | `{"filename", "markdown"}` — the conversation as Markdown, secrets masked |
| `GET /conversations/{id}/files?q=` | files of its folder for an @-mention, best first |
| `POST /conversations` | start one: `{"path", "name", "options": [keys], "interactive", "remote_control", "auto_continue", "worktree", "session_id", "pr", "prompt"}` — as in [Starting a conversation](new-conversation.md); options left out are the defaults |
| `GET /conversations/{id}/tasks` | its [task queue](tasks.md) and the two switches; `POST` adds `{"text", "when"}` — when is empty (as soon as possible), `HH:MM`, `YYYY-MM-DD HH:MM` or `reset` |
| `POST /tasks/{id}` | `{"text"}` / `{"when"}` of a task that hasn't started; `DELETE` removes it; `POST /tasks/{id}/move` `{"delta": -1 \| 1}` |
| `POST /conversations/{id}/switches` | `{"paused": bool}` start no further tasks, `{"auto_continue": bool}` after a usage limit |
| `GET /conversations/{id}/overview` | the conversation at a glance: `now` (`state`, `state_label`, `pending`, `last_event`, `context`), `changes` (branch, `added`, `removed`, `ahead`, `behind`, `files` with `status` and lines; `null` where ccc may not run git), `usage` (`today`, `total`, 14 `days`, `models`) and `errors` (`current`, `count`, `recent` runs) |
| `GET /conversations/{id}/reviews` | its [reviews](review.md), newest first; `POST` asks for one `{"instructions", "until_pass", "rounds"}`; `POST …/reviews/stop`; `POST /reviews/{id}/apply` sends a FAIL / PARTIAL review's findings back as a task |
| `GET /conversations/{id}/goals` | its [goal loops](goal.md); `POST` starts one `{"goal", "check", "rounds"}`; `POST …/goals/stop` |
| `GET /workflow-templates` | the [workflow](workflows.md) templates with their steps |
| `GET /conversations/{id}/workflows` | its workflow runs, each with its steps and what every agent said; `POST` starts one `{"template", "task", "check", "rounds"}`; `POST …/workflows/answer` `{"approve", "note"}` at a gate; `POST …/workflows/stop` |
| `GET /routines` | every [routine](routines.md) and the schedule kinds; `POST` adds one `{"conversation_id", "name", "prompt", "kind", "value", "fresh"}`; `POST /routines/{id}` changes it, `DELETE` removes it; `POST …/enabled` `{"enabled"}`, `POST …/run` |
| `GET /live-sessions` | [Claude Code sessions](live-sessions.md) running on the computer (`kind`, `status`, `state` as Claude Code names them, plus `kind_label`, `status_label`, `state_label` in the request's language); `POST /live-sessions/{id}/prompt` `{"prompt"}` |
| `GET /analytics?year=&month=` | a month's tokens per day, model and conversation, the months there are ([Analytics](analytics.md)); `POST /analytics/archive` archives finished months now |
| `GET /settings` | ccc's [settings](settings.md) with the choices for each (secrets only as set / not set); `POST` changes some of them; `POST /settings/telegram-test`, `telegram-off`, `discord-test`, `discord-off` |
| `POST /prompt-presets` | `{"presets": [{"name", "text", "send_now"}]}` — all of them, in order; `POST /launch-presets` saves a New conversation preset, `POST /launch-presets/delete` `{"name"}` |
| `GET /conversations/{id}/terminal` | what an interactive conversation's Claude Code shows now — its screen with colours (ANSI) and cursor; `?history=N` adds the N lines that scrolled off above it as `history` (empty in Claude Code's fullscreen mode, which keeps its scrollback itself: use PageUp/PageDown) |
| `POST /conversations/{id}/terminal` | into its Claude Code: `{"paste", "submit"}` text as one paste (Enter after it unless `submit` is false), `{"keys": […]}` keys (Enter, Escape, Tab, BTab, arrows, Home, End, PageUp, PageDown, BSpace, Space, C-c, C-d, C-l, C-o, C-r, C-t, C-u), `{"type": "y"}` a few characters |
| `POST /backup` | back the database up now |
| `GET /launch-options` | the options a conversation can be started with (keys and labels) |
| `GET /projects` | recently used folders |
| `GET /plugins` | `{installed, enabled}`: ids of installed Claude Code plugins and those on by default (tick them as `plugin:<id>` in `options`, together with `plugins_select`) |
| `GET /agents?path=` | the Claude Code [agents](agents.md) of a folder (its project's and the user's) without their prompts: `name`, `description`, `scope` (`project` / `user`), `tools` (`null` = every tool), `model`, `problem` (why Claude Code would skip the file, else empty), `overridden`, `file`. A folder outside the allowed ones gets `403` |
| `GET /agents/{scope}/{name}?path=` | one agent with its `prompt` and a `version`; `POST` on the same path changes it `{"path", "description", "tools", "model", "prompt", "version"}` (`409` when the file changed since that `version`; the name and the file's other fields stay), `DELETE` removes it (the file moves to ccc's own folder). Admin; an agent of a parent folder or behind a link that leads out of the folder is not changed (`403`) |
| `POST /agents` | creates `{"scope", "path", "name", "description", "tools", "model", "prompt"}`; `name` is lowercase letters, digits and hyphens, an existing file is never overwritten (`400`). Answers `201` with the agent, `version` and `new_folder` (Claude Code needs a restart to see the first agent of a new folder) |
| `GET /agent-options` | the templates (key, title, description, tools, model, prompt — in the request's language) and the wizard's choices: `access` (what the agent may do → tools), `effort` (→ model), the allowed `models` |
| `POST /agents/prompt` | `{"description", "access", "claude"}` → `{"prompt", "by"}`: a short outline, or with `"claude": true` instructions written by Claude (one call without tools, one at a time: `429` when another is running, `502` with the reason when Claude could not) |
| `GET /mcp-servers?path=` | names of the MCP servers configured for a folder (tick them as `mcp:<name>` in `options`, together with `mcp_select`) |
| `GET /sessions?path=` | Claude Code sessions recorded in a folder, newest first (to resume one: `session_id` in `POST /conversations` with `session_resume`) |
| `GET /launch-presets` | the New conversation presets and the last choice |
| `GET /prompt-presets` | the preset prompts (Alt+1 … 9 in ccc) |
| `POST /pair` | `{"code", "name"}` — no token needed: trades a pairing code from the QR for a new device; answers `{"token", "device_id", "name", "version"}`, 401 when the code is wrong, used or run out |
| `GET /devices` | the devices signed in: name, made, last seen, address; `current` marks the caller. `POST /devices/{id}` `{"name"}` renames one, `DELETE /devices/{id}` signs it out at once (open connections too; the row stays as history). `GET /devices?all=1` adds the signed-out ones with `revoked_at` and `revoked_by` (`app`, `computer`, `inactive`, `token`) |
| `GET /groups` | the grouping and your own groups; `POST /groups/rename` `{"old", "new"}`, `POST /groups/delete` `{"name"}` |
| `GET /search?q=` | search every conversation (archived too), newest first |
| `GET /waiting` | conversations that need you, most urgent first (Alt+N in ccc) |
| `GET /permissions` | every [approval](approvals.md) waiting |
| `POST /permissions/{id}` | `{"decision": "allow" \| "always" \| "deny"}` |
| `GET /usage` | the [usage limits](limits.md) (null when unknown) |
| `GET /events` | Server-Sent Events: `changed` with `{"conversations": [ids], "removed": [ids]}` whenever one changes |
