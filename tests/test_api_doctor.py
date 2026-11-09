import json
import os
import stat
from datetime import datetime, timedelta

import pytest

from ccc import api, api_doctor, api_scope
from ccc.api_doctor import FAIL, OK, WARN, listeners, run_checks
from ccc.storage import Storage, make_private

SS_LOOPBACK = "LISTEN 0 5 127.0.0.1:8737 0.0.0.0:*\nLISTEN 0 5 [::1]:8737 [::]:*\nLISTEN 0 5 0.0.0.0:22 0.0.0.0:*\n"
SS_OPEN = "LISTEN 0 5 0.0.0.0:8737 0.0.0.0:*\n"


@pytest.fixture
def storage(tmp_path):
    return Storage(str(tmp_path / "ccc.db"))


def _levels(checks):
    return {c.level for c in checks}


def _texts(checks, level):
    return " | ".join(c.text for c in checks if c.level == level)


def test_listeners_reads_ss_output():
    assert listeners(SS_LOOPBACK) == ["127.0.0.1", "[::1]"]
    assert listeners(SS_OPEN) == ["0.0.0.0"]
    assert listeners("") == []


def test_the_api_open_to_the_network_is_a_failure(storage, tmp_path):
    checks = run_checks(storage, tmp_path / "ccc.db", ss_output=SS_OPEN, tailscale_json="{}")
    assert "0.0.0.0" in _texts(checks, FAIL)
    assert FAIL not in _levels(run_checks(storage, tmp_path / "ccc.db", ss_output=SS_LOOPBACK, tailscale_json="{}"))


def test_a_funnel_is_a_failure(storage, tmp_path):
    funnel = json.dumps({"AllowFunnel": {"xia.ts.net:443": True}})
    checks = run_checks(storage, tmp_path / "ccc.db", ss_output=SS_LOOPBACK, tailscale_json=funnel)
    assert "xia.ts.net:443" in _texts(checks, FAIL)
    quiet = json.dumps({"Web": {}, "AllowFunnel": {"xia.ts.net:443": False}})
    assert FAIL not in _levels(run_checks(storage, tmp_path / "ccc.db", ss_output=SS_LOOPBACK, tailscale_json=quiet))


def test_a_readable_database_is_a_failure_until_made_private(storage, tmp_path):
    path = tmp_path / "ccc.db"
    os.chmod(path, 0o644)
    assert "chmod 600" in _texts(run_checks(storage, path, ss_output=SS_LOOPBACK, tailscale_json="{}"), FAIL)
    make_private(path)
    assert "chmod 600" not in _texts(run_checks(storage, path, ss_output=SS_LOOPBACK, tailscale_json="{}"), FAIL)


def test_a_new_storage_makes_its_file_private(tmp_path):
    path = tmp_path / "old.db"
    path.write_bytes(b"")
    os.chmod(path, 0o666)
    Storage(str(path))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_devices_that_sleep_are_warned_about(storage, tmp_path):
    api.add_device(storage, "Old")
    later = datetime.now() + timedelta(days=api_doctor.STALE_DAYS + 1)
    checks = run_checks(storage, tmp_path / "ccc.db", ss_output=SS_LOOPBACK, tailscale_json="{}", now=later)
    assert "Old" in _texts(checks, WARN) and "ccc api revoke" in _texts(checks, WARN)


def test_limits_are_reported(storage, tmp_path):
    def warned():
        return _texts(run_checks(storage, tmp_path / "ccc.db", ss_output=SS_LOOPBACK, tailscale_json="{}"), WARN)

    assert "No folder is set" in warned()
    api_scope.add_root(storage, "/")
    assert "whole account" in warned()
    api_scope.set_skip_allowed(storage, True)
    assert "skip every permission check" in warned()


def test_cli_doctor_exits_1_on_a_failure(storage, tmp_path, capsys, monkeypatch):
    from argparse import Namespace
    from ccc import api_cli

    monkeypatch.setattr(api_doctor, "_run", lambda command: SS_OPEN if command[0] == "ss" else "{}")
    assert api_cli.run(storage, str(tmp_path / "ccc.db"), Namespace(api_command="doctor")) == 1
    assert "✗" in capsys.readouterr().out
    monkeypatch.setattr(api_doctor, "_run", lambda command: SS_LOOPBACK if command[0] == "ss" else "{}")
    assert api_cli.run(storage, str(tmp_path / "ccc.db"), Namespace(api_command="doctor")) == 0


def test_the_api_unit_is_hardened_and_still_can_do_its_work():
    from pathlib import Path

    unit = (Path(__file__).parent.parent / "deploy" / "systemd" / "ccc-api.service").read_text()
    for setting in ("NoNewPrivileges=yes", "RestrictSUIDSGID=yes", "UMask=0077",
                    "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX"):
        assert setting in unit
    # tmux's socket is in /tmp and Gradle's JVM needs JIT: these would break the API's work.
    for forbidden in ("PrivateTmp=yes", "MemoryDenyWriteExecute=yes", "IPAddressDeny", "ProtectSystem=strict"):
        assert forbidden not in unit
