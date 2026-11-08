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


# --- Command profiles: other agents, no Claude Code (#226) ---

@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return profiles.add_command(
        "nim", run="myagent --model {model} --api {url} -p {prompt}", interactive="myagent --model {model}",
        url="https://api.example/v1", model="big-1", key="sekret",
        env={"OPENAI_API_KEY": "{key}", "OPENAI_BASE_URL": "{url}"},
    )


def test_command_profile_is_stored_and_the_old_format_still_reads(agent, tmp_path, monkeypatch):
    got = profiles.get("nim")
    assert got.is_command and got.model == "big-1" and got.key == "sekret"
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"profiles": {"acc": "/some/dir"}}))
    monkeypatch.setenv("CCC_PROFILES_FILE", str(path))
    assert not profiles.get("acc").is_command


def test_registry_with_a_key_is_private(agent):
    assert (profiles._registry_path().stat().st_mode & 0o777) == 0o600


def test_command_is_split_before_filling_and_has_no_key(agent):
    from ccc import backends
    argv = backends.run_command(agent, "sid", 'say "hi there"', False, "/p")
    assert argv == ["myagent", "--model", "big-1", "--api", "https://api.example/v1", "-p", 'say "hi there"']
    assert "sekret" not in " ".join(argv)


def test_key_in_a_command_line_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(profiles.ProfileError):
        profiles.add_command("bad", run="x --key {key}")
    with pytest.raises(profiles.ProfileError):
        profiles.add_command("bad", run="")


def test_command_env_gets_key_and_keeps_the_rest(agent, monkeypatch):
    monkeypatch.setenv("KEEP", "1")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    env = profiles.env_for("nim")
    assert env["OPENAI_API_KEY"] == "sekret" and env["OPENAI_BASE_URL"] == "https://api.example/v1"
    assert env["KEEP"] == "1" and "CLAUDE_CONFIG_DIR" not in env


def test_tmux_never_sees_the_key_in_the_command_line(storage, agent, tmp_path, monkeypatch):
    project = tmp_path / "p"
    project.mkdir()
    queue = start_conversation(storage, str(project), DEFAULT_SELECTION, profile="nim")
    seen = []
    monkeypatch.setattr("ccc.managed_sessions._tmux", lambda *a, **k: seen.append(a))
    monkeypatch.setattr("ccc.managed_sessions.make_transparent", lambda: None)
    start(queue, build_interactive_command(queue, False))
    shell_command = seen[0][-1]
    assert "myagent --model big-1" in shell_command and "claude" not in shell_command
    assert "sekret" not in shell_command
    env_file = profiles.env_assignments("nim")[-1]
    assert "OPENAI_API_KEY=sekret" in open(env_file).read()
    assert (__import__("os").stat(env_file).st_mode & 0o777) == 0o600


def test_remove_deletes_the_env_file(agent):
    env_file = profiles.env_assignments("nim")[-1]
    profiles.remove("nim")
    assert not __import__("os").path.exists(env_file)


def test_classify_command_output():
    from ccc import backends
    ok = backends.classify("done\n", "", 0, "s")
    assert ok.status == RunStatus.SUCCESS and ok.result_text == "done"
    assert backends.classify("", "429 rate limit exceeded", 1, "s").status == RunStatus.RATE_LIMITED
    assert backends.classify("", "boom", 2, "s").status == RunStatus.ERROR


def test_execute_runs_the_command_profile(agent, tmp_path):
    import asyncio
    profiles.remove("nim")
    profiles.add_command("echo", run="echo got {prompt} {session_id}")
    result = asyncio.run(runner.execute(str(tmp_path), "sid1", "hello", False, profile="echo"))
    assert result.status == RunStatus.SUCCESS and result.result_text == "got hello sid1"
    missing = profiles.add_command("gone", run="no-such-agent-xyz {prompt}")
    result = asyncio.run(runner.execute(str(tmp_path), "s", "x", False, profile="gone"))
    assert result.status == RunStatus.ERROR
