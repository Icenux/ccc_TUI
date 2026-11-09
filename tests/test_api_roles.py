import pytest

from ccc import api, api_roles, pairing
from ccc.api_roles import allows, required_role
from ccc.models import ROLE_ADMIN, ROLE_OPERATE, ROLE_READ, ROLES
from ccc.storage import Storage

from test_api import _call, server, storage  # noqa: F401  (fixtures)


@pytest.mark.parametrize("method, path, needed", [
    ("GET", "/conversations", ROLE_READ),
    ("GET", "/conversations/3/messages", ROLE_READ),
    ("GET", "/devices", ROLE_READ),
    ("POST", "/conversations/3/messages", ROLE_OPERATE),
    ("POST", "/permissions/7", ROLE_OPERATE),
    ("POST", "/tasks/4/move", ROLE_OPERATE),
    ("DELETE", "/tasks/4", ROLE_OPERATE),
    ("POST", "/conversations", ROLE_ADMIN),
    ("GET", "/conversations/3/terminal", ROLE_ADMIN),
    ("POST", "/conversations/3/terminal", ROLE_ADMIN),
    ("POST", "/live-sessions/abc-1/prompt", ROLE_ADMIN),
    ("GET", "/routines", ROLE_ADMIN),
    ("POST", "/routines/2/run", ROLE_ADMIN),
    ("GET", "/settings", ROLE_ADMIN),
    ("POST", "/settings", ROLE_ADMIN),
    ("POST", "/update", ROLE_ADMIN),
    ("POST", "/restart", ROLE_ADMIN),
    ("POST", "/prompt-presets", ROLE_ADMIN),
    ("POST", "/launch-presets", ROLE_ADMIN),
    ("POST", "/launch-presets/delete", ROLE_ADMIN),
    ("GET", "/launch-presets", ROLE_READ),
    ("POST", "/app/build", ROLE_ADMIN),
    ("POST", "/backup", ROLE_ADMIN),
    ("DELETE", "/conversations/3", ROLE_ADMIN),
    ("POST", "/devices/9", ROLE_ADMIN),
    ("POST", "/something/new", ROLE_ADMIN),  # unknown: closed by default
])
def test_what_a_request_needs(method, path, needed):
    assert required_role(method, path) == needed


def test_a_device_may_rename_and_sign_out_itself_only():
    assert required_role("DELETE", "/devices/9", device_id=9) == ROLE_READ
    assert required_role("POST", "/devices/9", device_id=9) == ROLE_READ
    assert required_role("DELETE", "/devices/9", device_id=4) == ROLE_ADMIN


def test_roles_are_ordered_and_an_unknown_one_is_the_weakest():
    assert [allows(ROLE_OPERATE, r) for r in ROLES] == [True, True, False]
    assert allows("nonsense", ROLE_READ) and not allows("nonsense", ROLE_OPERATE)


def test_http_refuses_what_the_role_does_not_allow(server, storage):  # noqa: F811
    base, _admin_token = server
    _reader, read_token = api.add_device(storage, "Reader", ROLE_READ)
    _operator, operate_token = api.add_device(storage, "Operator", ROLE_OPERATE)
    assert _call(f"{base}/api/v1/status", read_token)[0] == 200
    status, result = _call(f"{base}/api/v1/conversations/1/messages", read_token, "POST", {"text": "hi"})
    assert status == 403 and "read" in result["error"]
    assert _call(f"{base}/api/v1/conversations", operate_token, "POST", {"path": "/"})[0] == 403
    assert _call(f"{base}/api/v1/settings", operate_token)[0] == 403
    assert _call(f"{base}/api/v1/update", operate_token, "POST")[0] == 403
    assert _call(f"{base}/api/v1/conversations/1/terminal", operate_token, "POST", {"keys": ["Enter"]})[0] == 403


def test_http_a_weak_device_can_still_sign_itself_out_but_not_another(server, storage):  # noqa: F811
    base, admin_token = server
    reader, read_token = api.add_device(storage, "Reader", ROLE_READ)
    other = storage.list_devices()[0]
    assert _call(f"{base}/api/v1/devices/{other.id}", read_token, "DELETE")[0] == 403
    assert _call(f"{base}/api/v1/devices/{reader.id}", read_token, "DELETE")[0] == 200
    assert _call(f"{base}/api/v1/status", read_token)[0] == 401


def test_devices_from_before_roles_stay_admin(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                     "token_sha256 TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, last_seen_at TEXT, "
                     "last_ip TEXT NOT NULL DEFAULT '')")
        conn.execute("INSERT INTO devices (name, token_sha256, created_at) VALUES ('Phone', 'x', '2026-01-01T00:00:00')")
    assert [d.role for d in Storage(str(path)).list_devices()] == [ROLE_ADMIN]


def test_a_pairing_code_carries_the_role_and_defaults_to_operate(storage):  # noqa: F811
    code, _ = pairing.new_code(storage)
    assert pairing.redeem(storage, code, "Phone")[0].role == ROLE_OPERATE
    code, _ = pairing.new_code(storage, role=ROLE_READ)
    assert pairing.redeem(storage, code, "Tablet")[0].role == ROLE_READ
    with pytest.raises(ValueError):
        pairing.new_code(storage, role="root")


def test_cli_changes_a_role(storage, capsys):  # noqa: F811
    from argparse import Namespace
    from ccc import api_cli

    device, _ = api.add_device(storage, "Phone")
    assert api_cli.run(storage, "", Namespace(api_command="role", id=device.id, role=ROLE_READ)) == 0
    assert storage.list_devices()[0].role == ROLE_READ
    assert api_cli.run(storage, "", Namespace(api_command="role", id=99, role=ROLE_READ)) == 1


def test_a_device_that_only_looks_does_not_get_the_links_that_control_a_conversation():
    answer = {"id": 1, "remote_control_url": "https://x", "list": [{"remote_control_url": "https://y", "name": "a"}]}
    assert api_roles.strip_for(ROLE_READ, answer) == {"id": 1, "list": [{"name": "a"}]}
    assert api_roles.strip_for(ROLE_OPERATE, answer) == answer
