import pytest

from ccc import api, api_audit, api_scope, pairing

from test_api import _call, server, storage  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _fresh_alarms():
    api_audit._last_alarm.clear()
    api_audit._seen_folders.clear()


@pytest.fixture
def sent(monkeypatch):
    """The texts the alarms send (the channels are faked, the thread is not used)."""
    texts = []
    monkeypatch.setattr("ccc.notify.Notifier.configured", lambda self, category=None: True)
    monkeypatch.setattr("ccc.notify.Notifier.send_now", lambda self, text, category=None: texts.append(text))
    monkeypatch.setattr("ccc.api_audit._spawn", lambda target, *args: target(*args))
    return texts


def test_changes_and_refusals_are_logged_reads_are_not(server, storage):  # noqa: F811
    base, token = server
    _call(f"{base}/api/v1/status", token)
    _call(f"{base}/api/v1/conversations/999/messages", token, "POST", {"text": "secret text"})
    _call(f"{base}/api/v1/status", "wrong")
    entries = storage.list_audit()
    assert [(e["method"], e["status"]) for e in entries] == [("GET", 401), ("POST", 404)]  # newest first
    assert all("secret text" not in str(e) for e in entries)
    assert not any(e["method"] == "GET" and e["status"] == 200 for e in entries)


def test_a_new_conversation_keeps_its_folder_and_never_the_body(server, storage, tmp_path):  # noqa: F811
    base, token = server
    folder = tmp_path / "p"
    folder.mkdir()
    _call(f"{base}/api/v1/conversations", token, "POST", {"path": str(folder), "prompt": "do the secret thing", "interactive": False})
    entry = storage.list_audit(1)[0]
    assert entry["detail"] == str(folder) and "secret" not in str(entry)


def test_a_refusal_is_logged_with_the_device(server, storage):  # noqa: F811
    base, _ = server
    token = api.add_device(storage, "Reader")[1]
    reader = storage.list_devices()[-1]
    api_scope.remove_root(storage, "/")  # the fixture opens everything
    assert _call(f"{base}/api/v1/conversations", token, "POST", {"path": "/nowhere/z"})[0] == 403
    entry = storage.list_audit(1)[0]
    assert (entry["device_id"], entry["device_name"], entry["status"]) == (reader.id, "Reader", 403)


def test_pairing_is_logged_and_announced(server, storage, sent):  # noqa: F811
    base, _ = server
    code, _expires = pairing.new_code(storage)
    assert _call(f"{base}/api/v1/pair", None, "POST", {"code": code, "name": "Pixel"})[0] == 201
    assert _call(f"{base}/api/v1/pair", None, "POST", {"code": "wrong", "name": "Evil"})[0] == 401
    statuses = [(e["path"], e["status"]) for e in storage.list_audit()]
    assert ("/api/v1/pair", 201) in statuses and ("/api/v1/pair", 401) in statuses
    assert any("Pixel" in t and "paired" in t for t in sent)
    assert any("wrong pairing code" in t for t in sent)


def test_an_alarm_of_a_kind_is_sent_once_in_a_while(storage, sent):  # noqa: F811
    assert api_audit.alarm(storage, "bad-token", "one", now=1000.0)
    assert not api_audit.alarm(storage, "bad-token", "two", now=1100.0)
    assert api_audit.alarm(storage, "other", "three", now=1100.0)
    assert api_audit.alarm(storage, "bad-token", "four", now=1000.0 + api_audit.ALARM_GAP_SECONDS + 1)
    assert sent == ["one", "three", "four"]


def test_the_log_keeps_only_the_newest(storage, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(storage), "AUDIT_KEEP", 5)
    for n in range(9):
        storage.add_audit(1, "P", "1.1.1.1", "POST", f"/x{n}", 200)
    assert [e["path"] for e in storage.list_audit(100)] == [f"/x{n}" for n in range(8, 3, -1)]


def test_audit_endpoint_lists_the_entries(server, storage):  # noqa: F811
    base, token = server
    _call(f"{base}/api/v1/conversations/999/messages", token, "POST", {"text": "x"})
    status, result = _call(f"{base}/api/v1/audit", token)
    assert status == 200 and result["entries"][0]["status"] == 404


def test_cli_log(storage, capsys):  # noqa: F811
    from argparse import Namespace
    from ccc import api_cli

    api_cli.run(storage, "", Namespace(api_command="log", n=10))
    assert "Nothing logged" in capsys.readouterr().out
    storage.add_audit(3, "Pixel", "100.1.1.1", "POST", "/api/v1/update", 403)
    api_cli.run(storage, "", Namespace(api_command="log", n=10))
    out = capsys.readouterr().out
    assert "403" in out and "Pixel" in out and "100.1.1.1" in out


def test_what_a_request_carried_is_made_harmless_and_short(storage, capsys):  # noqa: F811
    from argparse import Namespace
    from ccc import api_cli

    api_audit.record(storage, None, "1.2.3.4", "POST", "/api/v1/x\x1b[31m[red]" + "a" * 500, 401)
    entry = storage.list_audit(1)[0]
    assert "\x1b" not in entry["path"] and len(entry["path"]) <= 300
    storage.add_audit(None, "", "1.1.1.1", "POST", "/old\x1b[2J", 401)  # written before the cleaning
    api_cli.run(storage, "", Namespace(api_command="log", n=5))
    assert "\x1b" not in capsys.readouterr().out


def test_strangers_cannot_push_the_known_devices_entries_out(storage, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(storage), "AUDIT_KEEP_STRANGERS", 3)
    storage.add_audit(7, "Phone", "1.1.1.1", "POST", "/mine", 200)
    for n in range(10):
        storage.add_audit(None, "", "6.6.6.6", "GET", f"/scan{n}", 401)
    paths = [e["path"] for e in storage.list_audit(100)]
    assert "/mine" in paths and len([p for p in paths if p.startswith("/scan")]) == 3


def test_a_wrong_pairing_code_is_logged_once(server, storage):  # noqa: F811
    base, _ = server
    _call(f"{base}/api/v1/pair", None, "POST", {"code": "wrong", "name": "x"})
    assert len(storage.list_audit()) == 1


def test_a_refused_folder_is_reported_once_as_a_folder_problem(server, storage, sent):  # noqa: F811
    base, token = server
    api_scope.remove_root(storage, "/")  # the fixture opens everything
    for typed in ("/nowhere/a", "/nowhere/ab", "/nowhere/abc"):
        status, result = _call(f"{base}/api/v1/mcp-servers?path={typed}", token)
        assert status == 403 and "ccc api roots --add" in result["error"]  # the app shows this
    entries = storage.list_audit()
    assert len(entries) == 1 and entries[0]["detail"] == "folder not allowed: /nowhere/a"
    assert _call(f"{base}/api/v1/conversations", token, "POST", {"path": "/nowhere/z"})[0] == 403
    assert len(storage.list_audit()) == 2  # a refused change is never collapsed
    assert len(sent) == 1 and "folder /nowhere/a" in sent[0] and "tried" not in sent[0]


def test_another_refusal_is_reported_as_a_forbidden_request(server, storage, sent):  # noqa: F811
    base, token = server
    api_scope.set_skip_allowed(storage, False)
    status, _result = _call(f"{base}/api/v1/conversations", token, "POST", {"path": "/tmp", "options": ["perm_skip"]})
    assert status == 403 and any("which is not allowed" in t and "tried" in t for t in sent)
