"""Claude Code subagents over the HTTP API (ccc.api_subagents): reading, creating, changing, removing,
the templates and the wizard's prompt — and what an admin device may and may not reach."""

import pytest

from ccc import agent_wizard, agents, api, api_roles
from ccc.models import ROLE_ADMIN, ROLE_READ
from ccc.storage import Storage

GOOD = "---\nname: reviewer\ndescription: Reviews diffs\ntools: Read, Grep\nmodel: haiku\nmemory: project\n---\n\nYou review.\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    (home / "agents").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setenv("CCC_AGENT_TRASH", str(tmp_path / "trash"))
    storage = Storage(str(tmp_path / "ccc.db"))
    project = tmp_path / "shop"
    (project / ".git").mkdir(parents=True)
    storage.create_queue("shop", str(project), "s1")
    return type("World", (), {"api": api.Api(storage), "project": project, "home": home, "tmp": tmp_path,
                              "storage": storage})


def call(world, method, path, query=None, body=None):
    return world.api.route(method, "/api/v1" + path, query or {}, body or {})


def put(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def error_of(world, *args, **kwargs):
    with pytest.raises(api.ApiError) as raised:
        call(world, *args, **kwargs)
    return raised.value


# --- reading ------------------------------------------------------------------------------


def test_one_agent_comes_with_its_prompt_and_a_version(world):
    put(world.project / ".claude" / "agents", "reviewer.md", GOOD)
    status, listed = call(world, "GET", "/agents", {"path": str(world.project)})
    assert status == 200 and "prompt" not in listed[0]
    status, one = call(world, "GET", "/agents/project/reviewer", {"path": str(world.project)})
    assert one["prompt"].strip() == "You review." and one["tools"] == ["Read", "Grep"] and one["version"]
    assert error_of(world, "GET", "/agents/project/ghost", {"path": str(world.project)}).status == 404
    assert error_of(world, "GET", "/agents/project/reviewer").status == 400  # a project agent needs its folder
    assert error_of(world, "GET", "/agents/project/reviewer", {"path": "/etc"}).status == 403


def test_the_options_hold_templates_and_the_wizard_s_questions(world):
    status, options = call(world, "GET", "/agent-options")
    assert [t["key"] for t in options["templates"]] == ["code-reviewer", "test-runner", "code-explorer", "translator"]
    assert options["templates"][0]["tools"] == ["Read", "Grep", "Glob", "Bash"] and options["templates"][0]["prompt"]
    assert [c["key"] for c in options["access"]] == ["read", "run", "edit"] and options["default_access"] == "read"
    assert {c["model"] for c in options["effort"]} == {"haiku", "sonnet", "opus"}
    assert "sonnet" in options["models"] and "" in options["models"]


def test_the_options_speak_the_requests_language(world):
    from ccc import i18n

    i18n.set_language("cs")
    try:
        _status, options = call(world, "GET", "/agent-options")
    finally:
        i18n.set_language("en")
    assert options["templates"][1]["title"] == "Spouštěč testů" and "Spustí testy" in options["templates"][1]["description"]


# --- creating -----------------------------------------------------------------------------


def test_create_in_the_project_and_for_the_user(world):
    body = {"scope": "project", "path": str(world.project), "name": "checker", "description": "Checks",
            "tools": ["Read", "Grep", "Glob"], "model": "haiku", "prompt": "You check."}
    status, created = call(world, "POST", "/agents", body=body)
    assert status == 201 and created["name"] == "checker" and created["new_folder"] and created["version"]
    assert (world.project / ".claude" / "agents" / "checker.md").is_file()
    status, mine = call(world, "POST", "/agents", body={**body, "scope": "user", "name": "mine", "tools": None})
    assert status == 201 and mine["tools"] is None and (world.home / "agents" / "mine.md").is_file()


@pytest.mark.parametrize("change, status", [
    ({"scope": "nowhere"}, 400), ({"name": "Bad Name"}, 400), ({"name": "../x"}, 400), ({"description": " "}, 400),
    ({"tools": "Read"}, 400), ({"tools": []}, 400), ({"model": "gpt"}, 400), ({"prompt": ""}, 400),
    ({"prompt": "x" * 20001}, 400), ({"path": "/etc"}, 403), ({"path": ""}, 400),
])
def test_create_refuses_bad_requests_and_writes_nothing(world, change, status):
    body = {"scope": "project", "path": str(world.project), "name": "ok", "description": "d", "tools": ["Read"],
            "model": "", "prompt": "p", **change}
    assert error_of(world, "POST", "/agents", body=body).status == status
    assert not (world.project / ".claude").exists() and list((world.home / "agents").iterdir()) == []


def test_create_never_overwrites(world):
    body = {"scope": "user", "name": "a", "description": "d", "tools": None, "prompt": "first"}
    call(world, "POST", "/agents", body=body)
    assert error_of(world, "POST", "/agents", body={**body, "prompt": "second"}).status == 400
    assert "first" in (world.home / "agents" / "a.md").read_text()


def test_an_all_digit_name_works(world):
    call(world, "POST", "/agents", body={"scope": "user", "name": "123", "description": "d", "tools": None, "prompt": "p"})
    assert call(world, "GET", "/agents/user/123")[1]["name"] == "123"
    assert call(world, "DELETE", "/agents/user/123")[1] == {"removed": "123"}


# --- changing and removing ----------------------------------------------------------------


def test_update_keeps_the_rest_of_the_file_and_returns_a_new_version(world):
    put(world.project / ".claude" / "agents", "reviewer.md", GOOD)
    query = {"path": str(world.project)}
    version = call(world, "GET", "/agents/project/reviewer", query)[1]["version"]
    status, changed = call(world, "POST", "/agents/project/reviewer", body={
        "path": str(world.project), "description": "New", "tools": ["Read"], "model": "opus", "prompt": "New prompt",
        "version": version})
    assert status == 200 and changed["description"] == "New" and changed["version"] != version
    text = (world.project / ".claude" / "agents" / "reviewer.md").read_text()
    assert "memory: project" in text and "name: reviewer" in text


def test_update_with_an_old_version_is_a_conflict(world):
    put(world.project / ".claude" / "agents", "reviewer.md", GOOD)
    body = {"path": str(world.project), "description": "d", "tools": ["Read"], "model": "", "prompt": "p"}
    assert error_of(world, "POST", "/agents/project/reviewer", body=body).status == 409  # no version at all
    old = call(world, "GET", "/agents/project/reviewer", {"path": str(world.project)})[1]["version"]
    (world.project / ".claude" / "agents" / "reviewer.md").write_text(GOOD + "\nSomeone else.\n")
    assert error_of(world, "POST", "/agents/project/reviewer", body={**body, "version": old}).status == 409
    assert "Someone else" in (world.project / ".claude" / "agents" / "reviewer.md").read_text()


def test_delete_moves_the_file_and_reports_only_the_name(world):
    put(world.project / ".claude" / "agents", "reviewer.md", GOOD)
    status, result = call(world, "DELETE", "/agents/project/reviewer", {"path": str(world.project)})
    assert (status, result) == (200, {"removed": "reviewer"})
    assert not (world.project / ".claude" / "agents" / "reviewer.md").exists()
    assert len(list((world.tmp / "trash").iterdir())) == 1
    assert error_of(world, "DELETE", "/agents/project/reviewer", {"path": str(world.project)}).status == 404


def test_a_parents_agent_is_shown_but_not_changed_from_a_subfolder(world):
    sub = world.project / "pkg"
    sub.mkdir()
    world.storage.create_queue("pkg", str(sub), "s2")
    put(world.project / ".claude" / "agents", "reviewer.md", GOOD)  # the repository's, above the folder asked for
    assert call(world, "GET", "/agents/project/reviewer", {"path": str(sub)})[1]["name"] == "reviewer"
    body = {"path": str(sub), "description": "d", "tools": ["Read"], "model": "", "prompt": "p",
            "version": call(world, "GET", "/agents/project/reviewer", {"path": str(sub)})[1]["version"]}
    assert error_of(world, "POST", "/agents/project/reviewer", body=body).status == 403
    assert error_of(world, "DELETE", "/agents/project/reviewer", {"path": str(sub)}).status == 403
    assert (world.project / ".claude" / "agents" / "reviewer.md").exists()


def test_an_agents_folder_that_leads_out_of_the_project_is_not_changed(world):
    outside = world.tmp / "elsewhere"
    put(outside, "reviewer.md", GOOD)
    (world.project / ".claude").mkdir()
    (world.project / ".claude" / "agents").symlink_to(outside)
    assert error_of(world, "DELETE", "/agents/project/reviewer", {"path": str(world.project)}).status == 403
    assert (outside / "reviewer.md").exists()


# --- the wizard's prompt ------------------------------------------------------------------


def test_the_prompt_is_an_outline_unless_claude_is_asked(world, monkeypatch):
    async def never(job, tools, run_fn=None):
        raise AssertionError("Claude must not be asked")

    monkeypatch.setattr(agent_wizard, "generate_prompt", never)
    status, result = call(world, "POST", "/agents/prompt", body={"description": "Checks texts", "access": "read"})
    assert status == 200 and result["by"] == "outline" and "Checks texts" in result["prompt"]
    assert error_of(world, "POST", "/agents/prompt", body={"description": "x", "access": "root"}).status == 400


def test_claude_writes_the_prompt_with_the_chosen_access(world, monkeypatch):
    seen = {}

    async def fake(job, tools, run_fn=None):
        seen["args"] = (job, tools)
        return "You check."

    monkeypatch.setattr(agent_wizard, "generate_prompt", fake)
    status, result = call(world, "POST", "/agents/prompt", body={"description": "Checks", "access": "run", "claude": True})
    assert result == {"prompt": "You check.", "by": "claude"} and seen["args"] == ("Checks", ("Read", "Grep", "Glob", "Bash"))
    assert error_of(world, "POST", "/agents/prompt", body={"description": " ", "claude": True}).status == 400


def test_a_failure_of_claude_is_a_bad_gateway_and_frees_the_lock(world, monkeypatch):
    async def fail(job, tools, run_fn=None):
        raise ValueError("usage limit reached")

    monkeypatch.setattr(agent_wizard, "generate_prompt", fail)
    body = {"description": "Checks", "claude": True}
    failed = error_of(world, "POST", "/agents/prompt", body=body)
    assert failed.status == 502 and "usage limit" in failed.message
    assert error_of(world, "POST", "/agents/prompt", body=body).status == 502  # not 429: the lock was released


def test_only_one_prompt_is_written_at_a_time(world, monkeypatch):
    from ccc import api_subagents

    assert api_subagents._generating.acquire(blocking=False)
    try:
        assert error_of(world, "POST", "/agents/prompt", body={"description": "Checks", "claude": True}).status == 429
    finally:
        api_subagents._generating.release()


# --- who may do what ----------------------------------------------------------------------


@pytest.mark.parametrize("method, path, role", [
    ("GET", "/agents", ROLE_READ), ("GET", "/agents/project/x", ROLE_READ), ("GET", "/agent-options", ROLE_READ),
    ("POST", "/agents", ROLE_ADMIN), ("POST", "/agents/project/x", ROLE_ADMIN),
    ("DELETE", "/agents/user/x", ROLE_ADMIN), ("POST", "/agents/prompt", ROLE_ADMIN),
])
def test_reading_is_open_to_every_device_writing_needs_an_admin(method, path, role):
    assert api_roles.required_role(method, path) == role


def test_the_audit_keeps_the_folder_of_a_new_agent_not_its_text():
    from ccc.api import _Handler

    body = {"path": "/home/u/shop", "prompt": "secret instructions"}
    assert _Handler._detail("POST", "/api/v1/agents", body) == "/home/u/shop"
    assert _Handler._detail("POST", "/api/v1/agents/prompt", {"description": "x"}) == ""
    assert _Handler._detail("GET", "/api/v1/agents", body) == ""
