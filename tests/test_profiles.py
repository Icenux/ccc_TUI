"""Claude accounts a conversation can run under (ccc.profiles, #239)."""
import json

import pytest

from ccc import profiles, runner
from ccc.launch_options import DEFAULT_SELECTION
from ccc.managed_sessions import build_interactive_command, start
from ccc.models import RunStatus
from ccc.orchestrator import start_conversation
from ccc.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "test.db")


@pytest.fixture
def work(tmp_path, monkeypatch):
    """A profile 'work' whose folder is inside tmp_path (never the real home)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return profiles.add("work", str(tmp_path / "claude-work"))


def test_default_profile_changes_nothing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert profiles.env_for("") is None
    assert profiles.env_for(None) is None
    assert profiles.env_assignments("") == []


def test_unknown_name_runs_as_default(work):
    assert profiles.get("gone") is None
    assert profiles.env_for("gone") is None


def test_named_profile_env_sets_dir_and_drops_credentials(work, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    env = profiles.env_for("work")
    assert env["CLAUDE_CONFIG_DIR"] == str(work.config_dir)
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_env_assignments_for_tmux(work):
    args = profiles.env_assignments("work")
    assert args[0] == "env"
    assert f"CLAUDE_CONFIG_DIR={work.config_dir}" in args
    assert args.count("-u") == len(profiles.OVERRIDING_CREDENTIALS)


def test_add_validates(work, tmp_path):
    with pytest.raises(profiles.ProfileError):
        profiles.add("Bad Name")
    with pytest.raises(profiles.ProfileError):
        profiles.add("work")  # exists
    with pytest.raises(profiles.ProfileError):
        profiles.add("other", "relative/dir")
    with pytest.raises(profiles.ProfileError):
        profiles.add("other", str(work.config_dir))  # folder already taken
    with pytest.raises(profiles.ProfileError):
        profiles.add("other", str(profiles.default_config_dir()))


def test_add_creates_private_folder_and_remove_keeps_it(work):
    assert work.config_dir.is_dir()
    assert (work.config_dir.stat().st_mode & 0o777) == 0o700
    assert profiles.remove("work") is True
    assert profiles.remove("work") is False
    assert work.config_dir.is_dir()  # login and history stay


def test_malformed_registry_is_empty(tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    monkeypatch.setenv("CCC_PROFILES_FILE", str(path))
    assert profiles.list_profiles() == []
    path.write_text(json.dumps({"profiles": {"../x": "/a", "ok": "/b", "n": 5}}))
    assert [p.name for p in profiles.list_profiles()] == ["ok"]


def test_all_config_dirs_default_first(work):
    dirs = profiles.all_config_dirs()
    assert dirs[0] == profiles.default_config_dir()
    assert work.config_dir in dirs


def test_start_conversation_stores_profile(storage, work, tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    queue = start_conversation(storage, str(project), DEFAULT_SELECTION, interactive=False, profile="work")
    assert queue.profile == "work"
    assert storage.get_queue(queue.id).profile == "work"


def test_start_conversation_default_has_no_profile(storage, tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    queue = start_conversation(storage, str(project), DEFAULT_SELECTION, interactive=False)
    assert queue.profile == ""


def test_start_conversation_unknown_profile_is_refused(storage, tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    with pytest.raises(ValueError):
        start_conversation(storage, str(project), DEFAULT_SELECTION, interactive=False, profile="nope")


def test_login_expired_is_recognised_and_names_the_way_back():
    out = json.dumps({"is_error": True, "result": "Login expired · Please run /login", "session_id": "s"})
    result = runner.classify_output(out, "", 1)
    assert result.status == RunStatus.ERROR and result.login_expired
    assert "/login" in result.error_text


def test_login_expired_with_status_401():
    out = json.dumps({"is_error": True, "result": "x", "api_error_status": 401})
    assert runner.classify_output(out, "", 1).login_expired


def test_login_message_for_a_profile_points_at_ccc_profile_login():
    assert "ccc profile login work" in runner.login_expired_message("work")


def test_ordinary_error_is_not_login_expired():
    out = json.dumps({"is_error": True, "result": "boom"})
    assert not runner.classify_output(out, "", 1).login_expired


def test_interactive_session_starts_in_the_profile_environment(storage, work, tmp_path, monkeypatch):
    project = tmp_path / "p"
    project.mkdir()
    queue = start_conversation(storage, str(project), DEFAULT_SELECTION, profile="work")
    seen = []
    monkeypatch.setattr("ccc.managed_sessions._tmux", lambda *a, **k: seen.append(a))
    monkeypatch.setattr("ccc.managed_sessions.make_transparent", lambda: None)
    start(queue, build_interactive_command(queue, False))
    shell_command = seen[0][-1]
    assert f"CLAUDE_CONFIG_DIR={work.config_dir}" in shell_command
