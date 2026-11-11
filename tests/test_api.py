import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from ccc import api, api_scope
from ccc.models import QueueStatus
from ccc.storage import Storage
from ccc.usage_api import ProUsage, WindowUsage


@pytest.fixture
def storage(tmp_path):
    # These tests are about the endpoints, so the scope (ccc.api_scope) is wide open; tests/test_api_scope.py closes it.
    opened = Storage(str(tmp_path / "ccc.db"))
    api_scope.add_root(opened, "/")
    api_scope.set_skip_allowed(opened, True)
    return opened


@pytest.fixture
def projects(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    (root / "-p").mkdir(parents=True)
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", root)
    return root / "-p"


def _queue(storage, tmp_path, name="shop", session="s1"):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return storage.create_queue(name, str(folder), session)


def _transcript_line(role, text, minute=0):
    return json.dumps({
        "type": role,
        "message": {"role": role, "content": text if role == "user" else [{"type": "text", "text": text}]},
        "timestamp": f"2026-10-21T10:{minute:02d}:00.000Z",
    }) + "\n"


def test_only_the_tokens_fingerprint_is_kept_and_a_new_one_signs_the_rest_out(storage):
    assert not api.has_token(storage)
    first = api.new_token(storage)
    assert len(first) > 30
    assert first not in [d.token_sha256 for d in storage.list_devices()]
    assert api.token_valid(storage, first)
    second = api.new_token(storage)
    assert api.token_valid(storage, second)
    assert not api.token_valid(storage, first)
    assert not api.token_valid(storage, "")


def test_every_device_has_its_own_token_and_is_signed_out_alone(storage):
    phone, phone_token = api.add_device(storage, "Phone")
    _tablet, tablet_token = api.add_device(storage, "Tablet")
    assert api.authenticate(storage, phone_token).id == phone.id
    storage.revoke_device(phone.id, 'computer')
    assert not api.token_valid(storage, phone_token)
    assert api.token_valid(storage, tablet_token)


def test_the_token_from_before_devices_becomes_a_device(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    Storage(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('api_token_sha256', ?)", (api._digest("old-token"),))
    storage = Storage(path)
    assert api.token_valid(storage, "old-token")
    assert storage.get_setting("api_token_sha256") is None
    assert [d.name for d in storage.list_devices()] == ["Original token"]


def test_conversations_list_with_state(storage, tmp_path):
    q = _queue(storage, tmp_path)
    storage.add_queue_item(q.id, "go on")
    storage.create_permission_request(q.id, "Bash", {"command": "ls"})
    archived = _queue(storage, tmp_path, "old", "s2")
    storage.set_queue_archived(archived.id, True)

    listed = api.Api(storage).conversations()

    assert [c["name"] for c in listed] == ["shop"]
    assert listed[0]["pending"] == 1
    assert listed[0]["tokens_today"] == 0
    assert listed[0]["needs_approval"] is True
    assert listed[0]["status"] == "idle"
    assert listed[0]["live_busy"] is False
    assert [c["name"] for c in api.Api(storage).conversations(archived=True)] == ["old"]


def test_conversations_carry_their_own_group(storage, tmp_path):
    q = _queue(storage, tmp_path)
    storage.set_conversation_group(q.id, "Release")

    listed = api.Api(storage).conversations()

    assert listed[0]["group"] == {"key": "own:Release", "title": "Release"}


def test_conversation_detail_has_queued_messages_and_approvals(storage, tmp_path):
    q = _queue(storage, tmp_path)
    storage.add_queue_item(q.id, "first")
    request = storage.create_permission_request(q.id, "Edit", {"file_path": "a.py"})

    detail = api.Api(storage).conversation(q.id)

    assert [m["text"] for m in detail["queued"]] == ["first"]
    assert detail["permissions"][0]["id"] == request.id
    assert detail["permissions"][0]["input"] == {"file_path": "a.py"}


def test_unknown_conversation_is_404(storage):
    with pytest.raises(api.ApiError) as error:
        api.Api(storage).route("GET", "/api/v1/conversations/99", {}, {})
    assert error.value.status == 404


def test_messages_new_ones_after_an_index_and_older_before(storage, tmp_path, projects):
    q = _queue(storage, tmp_path)
    path = projects / "s1.jsonl"
    path.write_text("".join(_transcript_line("user" if i % 2 == 0 else "assistant", f"m{i}", i) for i in range(6)))
    endpoint = api.Api(storage)

    everything = endpoint.messages(q.id)
    assert everything["total"] == 6
    assert [m["text"] for m in everything["messages"]] == [f"m{i}" for i in range(6)]
    assert everything["messages"][0]["kind"] == "human"
    assert everything["messages"][1]["kind"] == "assistant_text"

    assert [m["index"] for m in endpoint.messages(q.id, limit=2)["messages"]] == [4, 5]
    assert [m["index"] for m in endpoint.messages(q.id, before=4, limit=2)["messages"]] == [2, 3]

    with path.open("a") as f:
        f.write(_transcript_line("assistant", "m6", 6))
    assert [m["text"] for m in endpoint.messages(q.id, after=5)["messages"]] == ["m6"]


def test_messages_of_a_conversation_without_a_transcript(storage, tmp_path, projects):
    q = _queue(storage, tmp_path)
    assert api.Api(storage).messages(q.id) == {"total": 0, "messages": []}


def test_send_queues_the_message_and_resumes_a_paused_conversation(storage, tmp_path):
    q = _queue(storage, tmp_path)
    storage.update_queue_status(q.id, QueueStatus.PAUSED_LIMIT)

    status, result = api.Api(storage).route("POST", f"/api/v1/conversations/{q.id}/messages", {},
                                            {"text": "  go on  "})

    assert status == 201
    assert result["text"] == "go on"
    assert [i.prompt_text for i in storage.list_pending_items(q.id)] == ["go on"]
    assert storage.get_queue(q.id).status == QueueStatus.IDLE


def test_send_refuses_an_empty_message(storage, tmp_path):
    q = _queue(storage, tmp_path)
    with pytest.raises(api.ApiError) as error:
        api.Api(storage).send(q.id, {"text": "   "})
    assert error.value.status == 400
    assert storage.list_pending_items(q.id) == []


@pytest.mark.parametrize("decision, status, always", [
    ("allow", "allowed", False), ("always", "allowed", True), ("deny", "denied", False),
])
def test_decide_an_approval(storage, tmp_path, decision, status, always):
    q = _queue(storage, tmp_path)
    request = storage.create_permission_request(q.id, "Bash", {"command": "ls"})
    endpoint = api.Api(storage)
    assert [p["conversation"] for p in endpoint.permissions()] == ["shop"]

    endpoint.decide(request.id, {"decision": decision})

    assert storage.get_permission_request(request.id).status == status
    assert storage.is_tool_always_allowed(q.id, "Bash") is always
    assert endpoint.permissions() == []
    with pytest.raises(api.ApiError) as error:
        endpoint.decide(request.id, {"decision": "allow"})
    assert error.value.status == 409


def test_decide_needs_a_known_decision(storage, tmp_path):
    q = _queue(storage, tmp_path)
    request = storage.create_permission_request(q.id, "Bash", {})
    with pytest.raises(api.ApiError) as error:
        api.Api(storage).decide(request.id, {"decision": "maybe"})
    assert error.value.status == 400
    assert storage.get_permission_request(request.id).status == "pending"


def test_create_a_conversation_with_defaults_and_a_first_message(storage, tmp_path):
    folder = tmp_path / "new"
    folder.mkdir()

    status, result = api.Api(storage).route("POST", "/api/v1/conversations", {}, {
        "path": str(folder), "options": ["model_sonnet"], "interactive": False, "prompt": "hello",
    })

    assert status == 201
    queue = storage.get_queue(result["id"])
    assert queue.name == "new"
    assert queue.delivery_mode.value == "headless"
    # The session and permission choices were missing: the defaults.
    assert queue.launch_flags == ["--dangerously-skip-permissions", "--model", "sonnet"]
    assert result["queued"][0]["text"] == "hello"


@pytest.mark.parametrize("body", [
    {"path": ""},
    {"path": "/no/such/folder"},
    {"path": "/tmp", "options": ["model_opus", "model_haiku"]},
    {"path": "/tmp", "options": ["nonsense"]},
    {"path": "/tmp", "options": "model_opus"},
])
def test_create_refuses_a_bad_request(storage, body):
    with pytest.raises(api.ApiError) as error:
        api.Api(storage).create(body)
    assert error.value.status == 400
    assert storage.list_queues() == []


def test_launch_options_and_projects(storage, tmp_path):
    _queue(storage, tmp_path)
    endpoint = api.Api(storage)

    options = endpoint.launch_options()

    assert options["groups"][0]["key"] == "session"
    assert {"key": "model_opus", "label": "Opus"} in options["groups"][3]["options"]
    assert "perm_skip" in options["default"]
    assert endpoint.projects() == [str(tmp_path / "shop")]


def test_usage(storage, monkeypatch):
    resets = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("ccc.usage_api.get_pro_usage",
                        lambda force_refresh=False: ProUsage(WindowUsage(64.0, resets), None, []))

    usage = api.Api(storage).usage()

    assert usage == {"five_hour": {"percent": 64.0, "resets_at": resets.isoformat()},
                     "seven_day": None, "per_model": []}


def test_changes_name_the_conversations_that_changed(storage, tmp_path, projects):
    q = _queue(storage, tmp_path)
    other = _queue(storage, tmp_path, "docs", "s2")
    endpoint = api.Api(storage)
    before = endpoint.snapshot()
    assert endpoint.changes(before, endpoint.snapshot()) is None

    storage.add_queue_item(q.id, "next")
    (projects / "s2.jsonl").write_text(_transcript_line("user", "hi"))
    endpoint._transcripts.clear()  # look the new transcript up now
    storage.delete_queue(other.id)

    assert endpoint.changes(before, endpoint.snapshot()) == {"conversations": [q.id], "removed": [other.id]}


def test_live_busy_is_in_the_list_and_detail_and_is_a_change(storage, tmp_path):
    q = _queue(storage, tmp_path)
    endpoint = api.Api(storage)
    before = endpoint.snapshot()

    storage.set_live_busy(q.id, True)

    assert endpoint.conversations()[0]["live_busy"] is True
    assert endpoint.conversation(q.id)["live_busy"] is True
    assert endpoint.conversation(q.id)["status"] == "idle"  # the queue's own state is not touched
    assert endpoint.changes(before, endpoint.snapshot()) == {"conversations": [q.id], "removed": []}
    storage.set_live_busy(q.id, False)
    assert endpoint.conversations()[0]["live_busy"] is False


def test_context_is_in_the_list_and_detail_and_is_a_change(storage, tmp_path):
    q = _queue(storage, tmp_path)
    endpoint = api.Api(storage)
    assert endpoint.conversations()[0]["context"] is None  # older status line / not reported
    before = endpoint.snapshot()

    storage.set_context(q.session_id, 42.5, 85000, 200000)

    expected = {"percent": 42.5, "tokens": 85000, "window": 200000}
    assert endpoint.conversations()[0]["context"] == expected
    assert endpoint.conversation(q.id)["context"] == expected
    assert endpoint.changes(before, endpoint.snapshot()) == {"conversations": [q.id], "removed": []}


# --- over HTTP ------------------------------------------------------------------


@pytest.fixture
def server(storage):
    httpd = api.make_server(storage, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", api.new_token(storage)
    httpd.shutdown()
    httpd.server_close()


def _call(url, token=None, method="GET", body=None):
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_http_needs_the_token(server):
    base, token = server
    assert _call(f"{base}/api/v1/status")[0] == 401
    assert _call(f"{base}/api/v1/status", "wrong")[0] == 401
    status, result = _call(f"{base}/api/v1/status", token)
    assert status == 200
    assert result["daemon_running"] is False


def test_http_a_new_token_locks_the_old_one_out(server, storage):
    base, token = server
    newer = api.new_token(storage)
    assert _call(f"{base}/api/v1/status", token)[0] == 401
    assert _call(f"{base}/api/v1/status", newer)[0] == 200


def test_http_send_and_read_back(server, storage, tmp_path):
    base, token = server
    q = _queue(storage, tmp_path)

    status, _ = _call(f"{base}/api/v1/conversations/{q.id}/messages", token, "POST", {"text": "hello"})
    assert status == 201
    status, result = _call(f"{base}/api/v1/conversations/{q.id}", token)
    assert status == 200
    assert [m["text"] for m in result["queued"]] == ["hello"]


def test_http_errors_are_json(server):
    base, token = server
    assert _call(f"{base}/api/v1/nothing", token) == (404, {"error": "Not found."})
    assert _call(f"{base}/api/v1/conversations/x/messages", token)[0] == 404
    assert _call(f"{base}/api/v1/conversations/1/messages?after=x", token)[0] == 400


def test_http_events_stream_tells_about_a_change(server, storage, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EVENTS_POLL_SECONDS", 0.05)
    base, token = server
    q = _queue(storage, tmp_path)
    request = urllib.request.Request(f"{base}/api/v1/events", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=5) as stream:
        assert stream.headers["Content-Type"] == "text/event-stream"
        assert stream.readline() == b": connected\n"
        stream.readline()
        storage.add_queue_item(q.id, "next")
        assert stream.readline() == b"event: changed\n"
        assert json.loads(stream.readline().removeprefix(b"data: ")) == {"conversations": [q.id], "removed": []}


def test_retry_and_seen(storage, tmp_path):
    q = _queue(storage, tmp_path)
    storage.update_queue_status(q.id, QueueStatus.BLOCKED_ERROR, last_error="boom")
    endpoint = api.Api(storage)

    assert endpoint.route("POST", f"/api/v1/conversations/{q.id}/retry", {}, {}) == (200, {"retried": True})
    assert storage.get_queue(q.id).status == QueueStatus.IDLE
    assert endpoint.route("POST", f"/api/v1/conversations/{q.id}/seen", {}, {})[0] == 200
    assert storage.get_queue(q.id).last_seen_at > datetime.now() - timedelta(minutes=1)


def test_http_events_stream_ends_when_a_new_token_is_made(server, storage, monkeypatch):
    monkeypatch.setattr(api, "EVENTS_POLL_SECONDS", 0.05)
    base, token = server
    request = urllib.request.Request(f"{base}/api/v1/events", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=5) as stream:
        assert stream.readline() == b": connected\n"
        api.new_token(storage)
        assert stream.read() == b"\n"  # the blank line after "connected", then closed


def test_cli_shows_a_token_only_when_it_makes_one(storage, capsys):
    from argparse import Namespace

    from ccc import api_cli

    api_cli.run(storage, "", Namespace(api_command="token", new=False))
    made = capsys.readouterr().out.splitlines()[0]
    assert api.token_valid(storage, made)
    api_cli.run(storage, "", Namespace(api_command="token", new=False))
    assert made not in capsys.readouterr().out
    api_cli.run(storage, "", Namespace(api_command="token", new=True))
    assert not api.token_valid(storage, made)


# --- round 1: managing conversations ---------------------------------------------


def test_archive_and_restore(storage, tmp_path):
    q = _queue(storage, tmp_path)
    endpoint = api.Api(storage)

    assert endpoint.route("POST", f"/api/v1/conversations/{q.id}/archive", {}, {"archived": True})[1]["archived"]
    assert endpoint.conversations() == []
    endpoint.archive(q.id, {"archived": False})
    assert [c["id"] for c in endpoint.conversations()] == [q.id]
    with pytest.raises(api.ApiError) as error:
        endpoint.archive(q.id, {"archived": "yes"})
    assert error.value.status == 400


def test_archived_conversation_takes_no_task_or_message(storage, tmp_path):
    q = _queue(storage, tmp_path)
    endpoint = api.Api(storage)
    endpoint.archive(q.id, {"archived": True})
    for call in (lambda: endpoint.add_task(q.id, {"text": "x"}), lambda: endpoint.send(q.id, {"text": "x"})):
        with pytest.raises(api.ApiError) as error:
            call()
        assert error.value.status == 409
    assert storage.list_pending_items(q.id) == []
    endpoint.archive(q.id, {"archived": False})
    endpoint.add_task(q.id, {"text": "x"})


def test_delete_but_not_while_it_runs(storage, tmp_path):
    q = _queue(storage, tmp_path)
    storage.update_queue_status(q.id, QueueStatus.RUNNING)
    endpoint = api.Api(storage)
    with pytest.raises(api.ApiError) as error:
        endpoint.route("DELETE", f"/api/v1/conversations/{q.id}", {}, {})
    assert error.value.status == 409

    storage.update_queue_status(q.id, QueueStatus.IDLE)
    assert endpoint.route("DELETE", f"/api/v1/conversations/{q.id}", {}, {}) == (200, {"deleted": q.id})
    assert storage.find_queue(q.id) is None


def test_own_groups_put_rename_delete(storage, tmp_path):
    q = _queue(storage, tmp_path)
    other = _queue(storage, tmp_path, "docs", "s2")
    endpoint = api.Api(storage)

    endpoint.set_group(q.id, {"name": " Release "})
    endpoint.set_group(other.id, {"name": "Release"})
    assert endpoint.groups() == {"mode": "own", "own": ["Release"]}
    assert {c["group"]["title"] for c in endpoint.conversations()} == {"Release"}
    assert endpoint.conversation(q.id)["group"] == {"key": "own:Release", "title": "Release"}

    endpoint.rename_group({"old": "Release", "new": "v2"})
    assert endpoint.groups()["own"] == ["v2"]
    endpoint.set_group(other.id, {"name": None})
    assert storage.conversation_groups() == {q.id: "v2"}
    endpoint.delete_group({"name": "v2"})
    assert endpoint.groups()["own"] == []
    with pytest.raises(api.ApiError):
        endpoint.rename_group({"old": "", "new": "x"})


def test_search_export_and_files(storage, tmp_path, projects):
    q = _queue(storage, tmp_path)
    (projects / "s1.jsonl").write_text(_transcript_line("user", "Add rate limiting") +
                                       _transcript_line("assistant", "Rate limiting added", 1))
    (tmp_path / "shop" / "src").mkdir()
    (tmp_path / "shop" / "src" / "limits.py").write_text("")
    endpoint = api.Api(storage)

    hits = endpoint.route("GET", "/api/v1/search", {"q": "rate LIMITING"}, {})[1]
    assert [h["kind"] for h in hits] == ["assistant_text", "human"]
    assert hits[0]["conversation_id"] == q.id

    exported = endpoint.export(q.id)
    assert exported["filename"].startswith("shop-") and exported["filename"].endswith(".md")
    assert "Rate limiting added" in exported["markdown"]

    assert endpoint.files(q.id, "limit") == ["src/limits.py"]


def test_waiting_prompt_presets_and_sessions(storage, tmp_path, projects, monkeypatch):
    from ccc.prompt_presets import PromptPreset, save_prompt_presets

    q = _queue(storage, tmp_path)
    storage.create_permission_request(q.id, "Bash", {})
    save_prompt_presets(storage, [PromptPreset("Tests", "Run the tests", send_now=True)])
    endpoint = api.Api(storage)

    assert endpoint.waiting() == [{"conversation_id": q.id, "name": "shop", "kind": "approval",
                                   "label": "waits for approval"}]
    assert endpoint.prompt_presets() == [{"number": 1, "name": "Tests", "text": "Run the tests", "send_now": True}]

    folder = tmp_path / "shop"
    session_dir = projects.parent / api_encode(str(folder))
    session_dir.mkdir()
    (session_dir / "abc.jsonl").write_text(_transcript_line("user", "first prompt"))
    sessions = endpoint.sessions(str(folder))
    assert [(s["session_id"], s["first_prompt"]) for s in sessions] == [("abc", "first prompt")]
    assert endpoint.sessions("/no/such") == []


def api_encode(path):
    from ccc.transcript import encode_project_dir

    return encode_project_dir(path)


def test_creating_remembers_the_choice_like_the_tui(storage, tmp_path):
    folder = tmp_path / "new"
    folder.mkdir()
    endpoint = api.Api(storage)
    assert endpoint.launch_presets() == {"last": None, "presets": []}

    endpoint.create({"path": str(folder), "options": ["model_opus"], "interactive": False, "auto_continue": False})

    last = endpoint.launch_presets()["last"]
    assert "model_opus" in last["options"] and last["auto_continue"] is False


# --- round 2: the task queue, runs, diffs ------------------------------------------


def test_tasks_add_edit_move_remove_and_switches(storage, tmp_path):
    q = _queue(storage, tmp_path)
    endpoint = api.Api(storage)

    first = endpoint.route("POST", f"/api/v1/conversations/{q.id}/tasks", {}, {"text": " one ", "when": ""})[1]
    second = endpoint.add_task(q.id, {"text": "two", "when": "2030-01-02 03:04"})
    assert second["not_before"] == "2030-01-02T03:04:00"
    assert [t["text"] for t in endpoint.tasks(q.id)["tasks"]] == ["one", "two"]

    endpoint.move_task(second["id"], {"delta": -1})
    endpoint.edit_task(first["id"], {"text": "one, better", "when": "2030-05-06 07:08"})
    listed = endpoint.tasks(q.id)["tasks"]
    assert [(t["text"], t["not_before"]) for t in listed] == [("two", "2030-01-02T03:04:00"),
                                                              ("one, better", "2030-05-06T07:08:00")]
    endpoint.route("DELETE", f"/api/v1/tasks/{second['id']}", {}, {})
    assert [t["text"] for t in endpoint.tasks(q.id)["tasks"]] == ["one, better"]

    switches = endpoint.switches(q.id, {"paused": True, "auto_continue": False})
    assert (switches["paused"], switches["auto_continue"]) == (True, False)
    assert storage.get_queue(q.id).paused


@pytest.mark.parametrize("call", [
    lambda e, q, i: e.add_task(q, {"text": "  "}),
    lambda e, q, i: e.add_task(q, {"text": "x", "when": "tomorrow-ish"}),
    lambda e, q, i: e.move_task(i, {"delta": 3}),
    lambda e, q, i: e.switches(q, {"paused": "yes"}),
    lambda e, q, i: e.edit_task(i, {"text": ""}),
])
def test_tasks_refuse_bad_input(storage, tmp_path, call):
    q = _queue(storage, tmp_path)
    item = storage.add_queue_item(q.id, "keep")
    with pytest.raises(api.ApiError) as error:
        call(api.Api(storage), q.id, item.id)
    assert error.value.status == 400
    assert [i.prompt_text for i in storage.list_pending_items(q.id)] == ["keep"]


def test_a_started_task_cant_be_changed(storage, tmp_path):
    from ccc.models import ItemStatus

    q = _queue(storage, tmp_path)
    item = storage.add_queue_item(q.id, "running")
    storage.set_item_status(item.id, ItemStatus.RUNNING)
    with pytest.raises(api.ApiError) as error:
        api.Api(storage).remove_task(item.id)
    assert error.value.status == 409


def _git(cwd, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo_run(storage, tmp_path):
    from ccc.models import PromptRun, RunStatus

    repo = tmp_path / "calc"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    _git(repo, "commit", "-qam", "ccc: subtract")
    queue = storage.create_queue("calc", str(repo), "s-calc")
    run_id = storage.insert_prompt_run(PromptRun(
        id=0, queue_id=queue.id, queue_item_id=None, prompt_text="Make add subtract\nplease", started_at=datetime.now(),
        finished_at=datetime.now(), status=RunStatus.SUCCESS, input_tokens=10, output_tokens=5,
        git_commit=_git(repo, "rev-parse", "HEAD"),
    ))
    return queue, run_id, repo


def test_mcp_servers_lists_names_only(storage, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"db": {"command": "x", "env": {"TOKEN": "secret"}}}}')
    endpoint = api.Api(storage)

    assert endpoint.mcp_servers(str(tmp_path)) == ["db"]
    assert endpoint.mcp_servers("") == []


def test_create_stores_the_chosen_mcp_servers_as_a_marker(storage, tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()

    result = api.Api(storage).create({
        "path": str(folder), "options": ["mcp_select", "mcp:db"], "interactive": False, "auto_continue": False,
    })

    assert storage.get_queue(result["id"]).launch_flags == ["--dangerously-skip-permissions", "--ccc-mcp=db"]


def test_conversations_carry_git(storage, tmp_path, monkeypatch):
    from ccc import gitops

    q = _queue(storage, tmp_path)  # a legacy queue: Claude may run commands here
    calls = []
    monkeypatch.setattr(gitops, "work_status", lambda cwd: calls.append(cwd) or gitops.WorkStatus(12, 3, 2, 0, "origin/main", "feature-x"))
    server = api.Api(storage)

    listed = server.conversations()[0]
    assert listed["context"] is None  # the status line has not reported yet
    assert listed["git"] == {"branch": "feature-x", "added": 12, "removed": 3, "ahead": 2, "behind": 0}
    server.conversations()
    server.conversation(q.id)
    assert len(calls) == 1  # looked up once per folder in GIT_SECONDS
    monkeypatch.setattr(api, "GIT_SECONDS", 0.0)
    server.conversations()
    assert len(calls) == 2


def test_conversations_carry_the_latest_goal_while_it_runs_and_a_day_after(storage, tmp_path):
    from datetime import datetime, timedelta

    from ccc import goals
    from ccc.models import GoalStatus

    q = _queue(storage, tmp_path)
    server = api.Api(storage)
    assert server.conversations()[0]["goal"] is None
    goal = goals.start(storage, q.id, "tokens expire", max_rounds=5)
    storage.update_goal(goal.id, round=2)
    listed = server.conversations()[0]["goal"]
    assert listed["status"] == "running" and (listed["round"], listed["max_rounds"]) == (2, 5)
    assert listed["status_label"] == "running" and listed["phase_label"] and listed["finished_at"] is None
    assert server.conversation(q.id)["goal"]["id"] == goal.id
    storage.update_goal(goal.id, status=GoalStatus.STUCK, finished_at=datetime.now())
    assert server.conversations()[0]["goal"]["status"] == "stuck"
    storage.update_goal(goal.id, finished_at=datetime.now() - timedelta(days=2))
    assert server.conversations()[0]["goal"] is None


def test_no_git_where_claude_may_not_run_commands(storage, tmp_path, monkeypatch):
    from ccc import gitops

    q = _queue(storage, tmp_path)
    monkeypatch.setattr(gitops, "claude_can_run_commands", lambda queue: False)
    monkeypatch.setattr(gitops, "work_status", lambda cwd: pytest.fail("git must not run here"))
    listed = api.Api(storage).conversations()[0]
    assert listed["git"] is None


def test_status_carries_the_update(storage, monkeypatch):
    from ccc import updates

    status = api.Api(storage).status()
    assert "android_build" not in status
    assert status["update"]["available"] is False
    updates.save(storage, updates.UpdateState(version="0.2.0", installed="0.1.0", notes="one\ntwo"))
    monkeypatch.setattr(updates, "refusal", lambda st, *a: "A task is running (shop)")
    update = api.Api(storage).status()["update"]
    assert update["version"] == "0.2.0" and update["commits"] == ["one", "two"] and update["notes"] == "one\ntwo" and "android" not in update and not update["can_apply"] and "shop" in update["reason"]
    storage.set_setting(updates.CHECK_KEY, "off")
    assert api.Api(storage).status()["update"] is None


def test_update_endpoints_check_and_apply(storage, monkeypatch):
    from ccc import updates

    server = api.Api(storage)
    monkeypatch.setattr(updates, "check", lambda st: updates.save(st, updates.UpdateState(version="0.2.0")) or updates.load(st))
    monkeypatch.setattr(updates, "refusal", lambda st, *a: None)
    assert server.route("POST", "/api/v1/update/check", {}, {})[1]["available"] is True
    monkeypatch.setattr(updates, "apply", lambda st: (True, "Updated to abc — ccc is restarting."))
    assert server.route("POST", "/api/v1/update", {}, {})[1] == {"ok": True, "message": "Updated to abc — ccc is restarting."}
    monkeypatch.setattr(updates, "apply", lambda st: (False, "A task is running (shop) — the restart would stop it."))
    with pytest.raises(api.ApiError) as error:
        server.route("POST", "/api/v1/update", {}, {})
    assert error.value.status == 409


def test_a_new_ccc_version_is_a_change_of_its_own(storage):
    from ccc import updates

    server = api.Api(storage)
    before = server.snapshot()
    assert server.changes(before, server.snapshot()) is None
    updates.save(storage, updates.UpdateState(version="0.2.0"))
    change = server.changes(before, server.snapshot())
    assert change == {"conversations": [], "removed": [], "update": True}
    again = server.snapshot()
    assert server.changes(again, server.snapshot()) is None


def test_http_devices_are_listed_renamed_and_signed_out(server, storage):
    base, token = server
    other, other_token = api.add_device(storage, "Tablet")
    status, result = _call(f"{base}/api/v1/devices?_device={other.id}", token)
    assert status == 200
    by_name = {d["name"]: d for d in result["devices"]}
    assert by_name["Tablet"]["current"] is False  # the caller can't claim to be another device
    assert [d["name"] for d in result["devices"] if d["current"]] == ["Token"]
    assert by_name["Token"]["last_seen_at"] is not None
    assert _call(f"{base}/api/v1/devices/{other.id}", token, "POST", {"name": "iPad"})[0] == 200
    assert storage.list_devices()[1].name == "iPad"
    assert _call(f"{base}/api/v1/devices/{other.id}", token, "POST", {"name": " "})[0] == 400
    assert _call(f"{base}/api/v1/devices/{other.id}", token, "DELETE")[0] == 200
    assert _call(f"{base}/api/v1/status", other_token)[0] == 401
    assert _call(f"{base}/api/v1/devices/{other.id}", token, "DELETE")[0] == 404


def test_http_events_stream_ends_when_its_device_is_signed_out(server, storage, monkeypatch):
    monkeypatch.setattr(api, "EVENTS_POLL_SECONDS", 0.05)
    base, token = server
    device, device_token = api.add_device(storage, "Phone")
    request = urllib.request.Request(f"{base}/api/v1/events", headers={"Authorization": f"Bearer {device_token}"})
    with urllib.request.urlopen(request, timeout=5) as stream:
        assert stream.readline() == b": connected\n"
        storage.revoke_device(device.id, 'computer')
        assert stream.read() == b"\n"
    assert _call(f"{base}/api/v1/status", token)[0] == 200


def test_cli_lists_and_signs_out_devices(storage, capsys):
    from argparse import Namespace

    from ccc import api_cli

    device, _token = api.add_device(storage, "Phone")
    api_cli.run(storage, "", Namespace(api_command="devices", all=False))
    assert "Phone" in capsys.readouterr().out
    assert api_cli.run(storage, "", Namespace(api_command="revoke", id=device.id)) == 0
    assert api_cli.run(storage, "", Namespace(api_command="revoke", id=device.id)) == 1
    api_cli.run(storage, "", Namespace(api_command="devices", all=False))
    assert "Phone" not in capsys.readouterr().out
    api_cli.run(storage, "", Namespace(api_command="devices", all=True))
    history = capsys.readouterr().out
    assert "Phone" in history and "signed out on the computer" in history


def test_pairing_code_is_good_once_and_only_until_it_runs_out(storage):
    from ccc import pairing

    code, expires = pairing.new_code(storage)
    assert code not in storage.get_setting(pairing.CODE_KEY)
    assert pairing.redeem(storage, "wrong", "Phone") is None
    device, token = pairing.redeem(storage, code, "Phone")
    assert api.authenticate(storage, token).id == device.id
    assert pairing.redeem(storage, code, "Phone") is None
    late, _ = pairing.new_code(storage)
    assert pairing.redeem(storage, late, "Phone", now=expires + timedelta(hours=1)) is None
    newer, _ = pairing.new_code(storage)
    other, _ = pairing.new_code(storage)
    assert pairing.redeem(storage, newer, "Phone") is None  # only the last code counts


def test_http_pair_needs_no_token_but_a_good_code(server, storage):
    from ccc import pairing

    base, _token = server
    code, _ = pairing.new_code(storage)
    assert _call(f"{base}/api/v1/pair", None, "POST", {"code": "nope", "name": "Phone"})[0] == 401
    status, result = _call(f"{base}/api/v1/pair", None, "POST", {"code": code, "name": "Pixel"})
    assert status == 201
    assert result["name"] == "Pixel"
    assert _call(f"{base}/api/v1/status", result["token"])[0] == 200
    assert _call(f"{base}/api/v1/pair", None, "POST", {"code": code, "name": "Pixel"})[0] == 401
    assert _call(f"{base}/api/v1/status")[0] == 401  # everything else still wants a token


def test_pairing_payload_and_qr(storage):
    from urllib.parse import parse_qs, urlsplit

    from ccc import pairing

    payload = pairing.payload("https://xia.tail0ffcfb.ts.net:8737", "abc_-123")
    query = parse_qs(urlsplit(payload).query)
    assert payload.startswith("ccc://pair?")
    assert query == {"server": ["https://xia.tail0ffcfb.ts.net:8737"], "code": ["abc_-123"]}
    lines = pairing.qr_lines(payload)
    assert len({len(line) for line in lines}) == 1  # a rectangle
    assert set("".join(lines)) <= set(" ▀▄█")
    assert len(lines[0]) > 20
    assert not lines[0].strip() and not lines[-1].strip()  # the quiet zone is empty


def test_server_address_is_the_one_set_else_tailscales(storage, monkeypatch):
    from ccc import pairing

    monkeypatch.setattr(pairing, "tailscale_url", lambda: "https://found.ts.net:8737")
    assert pairing.server_url(storage) == "https://found.ts.net:8737"
    storage.set_setting(pairing.SERVER_KEY, "https://mine.example/")
    assert pairing.server_url(storage) == "https://mine.example"


def test_cli_pair_prints_a_qr_for_the_address(storage, capsys, monkeypatch):
    from argparse import Namespace

    from ccc import api_cli, pairing

    monkeypatch.setattr(pairing, "api_reachable", lambda server: True)
    assert api_cli.run(storage, "", Namespace(api_command="pair", server="https://x.ts.net:8737")) == 0
    out = capsys.readouterr().out
    assert "█" in out or "▀" in out
    assert "https://x.ts.net:8737" in out
    assert storage.get_setting("api_pair_code")


def test_client_ip_comes_from_the_proxy_only_when_the_proxy_is_on_this_computer():
    assert api.client_ip("127.0.0.1", "100.64.0.7") == "100.64.0.7"
    assert api.client_ip("::1", "10.0.0.1, 100.64.0.7") == "100.64.0.7"  # the proxy's own entry is the last
    assert api.client_ip("127.0.0.1", "") == "127.0.0.1"
    # Anyone else could write the header themselves.
    assert api.client_ip("100.64.0.9", "1.2.3.4") == "100.64.0.9"


def test_http_the_device_keeps_the_address_the_proxy_names(server, storage):
    base, token = server
    request = urllib.request.Request(f"{base}/api/v1/status")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-Forwarded-For", "100.64.0.7")
    urllib.request.urlopen(request, timeout=5).read()
    assert storage.list_devices()[0].last_ip == "100.64.0.7"


def test_signing_out_keeps_the_device_as_read_only_history(storage):
    phone, phone_token = api.add_device(storage, "Phone")
    tablet, _tablet_token = api.add_device(storage, "Tablet")
    assert storage.revoke_device(phone.id, "app")
    assert not storage.revoke_device(phone.id, "computer")  # once: the first way stays
    assert not api.token_valid(storage, phone_token)
    assert not storage.rename_device(phone.id, "Renamed")
    assert [d.name for d in storage.list_devices()] == ["Tablet"]
    history = {d.name: d for d in storage.list_devices(include_revoked=True)}
    assert history["Phone"].revoked_by == "app" and history["Phone"].revoked_at is not None
    assert history["Tablet"].revoked_at is None
    api.new_token(storage)
    assert storage.revoke_device(tablet.id, "computer") is False  # already signed out by the new token
    by = {d.name: d.revoked_by for d in storage.list_devices(include_revoked=True)}
    assert by["Tablet"] == "token" and by["Phone"] == "app"


def test_devices_added_columns_are_migrated_into_an_old_database(tmp_path):
    import sqlite3

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                 "token_sha256 TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, last_seen_at TEXT, "
                 "last_ip TEXT NOT NULL DEFAULT '')")
    conn.execute("INSERT INTO devices (name, token_sha256, created_at) VALUES ('Old', 'x', '2026-10-01T10:00:00')")
    conn.commit()
    conn.close()
    storage = Storage(path)
    Storage(path)  # a second start changes nothing
    [old] = storage.list_devices()
    assert old.name == "Old" and old.revoked_at is None
    assert storage.revoke_device(old.id, "app")
    assert storage.list_devices() == []


def test_http_devices_history_is_listed_with_all_and_cannot_be_deleted_again(server, storage):
    base, token = server
    other, _other_token = api.add_device(storage, "Tablet")
    assert _call(f"{base}/api/v1/devices/{other.id}", token, "DELETE")[0] == 200
    assert [d["name"] for d in _call(f"{base}/api/v1/devices", token)[1]["devices"]] == ["Token"]
    everyone = {d["name"]: d for d in _call(f"{base}/api/v1/devices?all=1", token)[1]["devices"]}
    assert everyone["Tablet"]["revoked_by"] == "app" and everyone["Tablet"]["revoked_at"]
    assert everyone["Token"]["revoked_at"] is None and everyone["Token"]["revoked_by"] is None
    assert _call(f"{base}/api/v1/devices/{other.id}", token, "DELETE")[0] == 404
    assert _call(f"{base}/api/v1/devices/{other.id}", token, "POST", {"name": "x"})[0] == 404


def test_cli_pair_warns_when_the_api_does_not_answer(storage, capsys, monkeypatch):
    from argparse import Namespace

    from ccc import api_cli, pairing

    monkeypatch.setattr(pairing, "api_reachable", lambda server: False)
    assert api_cli.run(storage, "", Namespace(api_command="pair", server="https://x.ts.net:8737")) == 0
    assert "doesn't answer at https://x.ts.net:8737" in capsys.readouterr().err
    assert storage.get_setting("api_pair_code")  # the code is made anyway: the API may be about to start


def test_health_answers_without_a_token_and_api_reachable_uses_it(server):
    from ccc import pairing

    base, _token = server
    assert _call(f"{base}/api/v1/health") == (200, {"ok": True})
    assert pairing.api_reachable(base) is True
    assert pairing.api_reachable("http://127.0.0.1:9") is False  # nothing listens there (the discard port)


def test_cli_pair_can_save_the_qr_as_a_picture(storage, monkeypatch, tmp_path, capsys):
    from argparse import Namespace

    from ccc import api_cli, pairing

    monkeypatch.setattr(pairing, "tailscale_url", lambda: "https://xia.ts.net:8737")
    monkeypatch.setattr(pairing, "api_reachable", lambda server: True)
    target = tmp_path / "out" / "qr.png"
    assert api_cli.run(storage, "", Namespace(api_command="pair", server=None, image=str(target))) == 0
    assert target.read_bytes().startswith(b"\x89PNG")
    assert target.stat().st_mode & 0o777 == 0o600  # it holds a one-time code
    assert str(target) in capsys.readouterr().out
    assert storage.get_setting(pairing.CODE_KEY)


def test_accept_language_picks_the_language_ccc_has():
    from ccc import i18n

    assert i18n.from_accept_language("cs-CZ,cs;q=0.9,en;q=0.8") == "cs"
    assert i18n.from_accept_language("de,en;q=0.5") == "en"
    assert i18n.from_accept_language("en;q=0.2, cs;q=0.9") == "cs"
    assert i18n.from_accept_language("de-DE, fr;q=0.8") == "en"  # none of ccc's: the source language
    assert i18n.from_accept_language("*") is None
    assert i18n.from_accept_language("") is None


def test_http_texts_follow_the_phone_language_not_the_tui_one(server, storage, monkeypatch):
    from ccc import i18n

    monkeypatch.delenv("CCC_LANG", raising=False)  # tests force English through it
    monkeypatch.setattr(i18n, "_current", "en")  # the server loads the setting into it: put it back afterwards
    base, token = server
    storage.set_setting("language", "en")

    def error_for(language):
        request = urllib.request.Request(f"{base}/api/v1/devices/999", method="DELETE")
        request.add_header("Authorization", f"Bearer {token}")
        if language:
            request.add_header("Accept-Language", language)
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read())["error"]

    assert error_for("cs-CZ") == "Takové zařízení neexistuje."
    assert error_for("en-US") == "No such device."
    assert error_for(None) == "No such device."  # no header: the TUI's language (English here)
    storage.set_setting("language", "cs")
    assert error_for("en-US") == "No such device."  # the phone wins
    assert error_for(None) == "Takové zařízení neexistuje."


def test_status_says_when_a_restart_is_needed_and_restart_does_it(storage, monkeypatch):
    from ccc import updates

    monkeypatch.setattr(updates, "restart_needed", lambda st: True)
    assert api.Api(storage).status()["restart_needed"] is True
    monkeypatch.setattr(updates, "restart", lambda st: (True, "ccc is restarting."))
    assert api.Api(storage).restart() == {"ok": True, "message": "ccc is restarting."}
    monkeypatch.setattr(updates, "restart", lambda st: (False, "A task is running (shop)"))
    with pytest.raises(api.ApiError) as error:
        api.Api(storage).restart()
    assert error.value.status == 409


def test_overview_endpoint_has_the_four_parts(storage, repo_run):
    queue, _run_id, repo = repo_run
    (repo / "calc.py").write_text("changed\n")
    data = api.Api(storage).overview(queue.id)
    assert set(data) == {"now", "changes", "usage", "errors"}
    assert data["now"]["state"] == "idle"
    assert [f["path"] for f in data["changes"]["files"]] == ["calc.py"]
    assert data["usage"]["total"] == 15
    status, body = api.Api(storage).route("GET", f"/api/v1/conversations/{queue.id}/overview", {}, None)[:2]
    assert status == 200 and body["errors"]["recent"] == []
