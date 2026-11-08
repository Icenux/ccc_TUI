"""Claude Code's subagents as files (ccc.agents): what is read, what Claude Code
would skip, which of two same-named agents wins — and the API and window on top."""
import pytest

from ccc import agents


@pytest.fixture
def claude_dir(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    (home / "agents").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return home


def write(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


GOOD = "---\nname: reviewer\ndescription: Reviews diffs. Use after changes.\ntools: Read, Grep\nmodel: haiku\n---\n\nYou review.\n"


def test_reads_an_agent(claude_dir):
    write(claude_dir / "agents", "reviewer.md", GOOD)
    [a] = agents.list_agents()
    assert (a.name, a.scope, a.tools, a.model, a.problem) == ("reviewer", agents.SCOPE_USER, ("Read", "Grep"), "haiku", "")
    assert a.description == "Reviews diffs. Use after changes."
    assert a.prompt == "\nYou review." or a.prompt.strip() == "You review."


def test_tools_as_a_yaml_list_and_no_tools(claude_dir):
    write(claude_dir / "agents", "a.md", "---\nname: a\ndescription: d\ntools:\n  - Read\n  - Bash\n---\nx")
    write(claude_dir / "agents", "b.md", "---\nname: b\ndescription: d\n---\nx")
    by_name = {a.name: a for a in agents.list_agents()}
    assert by_name["a"].tools == ("Read", "Bash")
    assert by_name["b"].tools is None  # inherits every tool


def test_block_description_is_joined(claude_dir):
    write(claude_dir / "agents", "a.md", "---\nname: a\ndescription: >\n  First line\n  second line\nmodel: opus\n---\nx")
    [a] = agents.list_agents()
    assert a.description == "First line second line"
    assert a.model == "opus"


def test_quoted_values(claude_dir):
    write(claude_dir / "agents", "a.md", "---\nname: \"a\"\ndescription: 'It: works'\n---\nx")
    [a] = agents.list_agents()
    assert (a.name, a.description) == ("a", "It: works")


def test_documentation_files_are_not_agents(claude_dir):
    folder = claude_dir / "agents"
    write(folder, "README.md", "# Agents\nnothing here")  # no frontmatter
    write(folder, "noname.md", "---\ndescription: d\n---\nx")  # no name
    write(folder, "late.md", "\n---\nname: late\ndescription: d\n---\n")  # --- not on the first line
    write(folder, "notes.txt", GOOD)  # not Markdown
    assert agents.list_agents() == []


@pytest.mark.parametrize("name, problem", [
    ("-x", "the name starts with '-'"),
    ("a:b", "the name contains ':' (reserved for plugins)"),
    ("n" * 257, "the name is longer than 256 characters"),
])
def test_files_claude_code_skips_are_listed_with_the_reason(claude_dir, name, problem):
    write(claude_dir / "agents", "x.md", f"---\nname: {name}\ndescription: d\n---\nx")
    [a] = agents.list_agents()
    assert a.problem == problem


def test_no_description_is_a_problem(claude_dir):
    write(claude_dir / "agents", "x.md", "---\nname: x\n---\nx")
    [a] = agents.list_agents()
    assert a.problem == "no description"
    assert agents.find("x") is None  # Claude Code would not load it


def test_project_agent_wins_over_the_user_one(claude_dir, tmp_path):
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    write(claude_dir / "agents", "r.md", GOOD)
    write(project / ".claude" / "agents", "r.md", GOOD.replace("Reviews diffs", "Project reviewer"))
    found = agents.list_agents(str(project))
    assert [(a.scope, a.overridden) for a in found] == [(agents.SCOPE_PROJECT, False), (agents.SCOPE_USER, True)]
    assert agents.find("reviewer", str(project)).description.startswith("Project reviewer")
    assert agents.find("reviewer").scope == agents.SCOPE_USER  # outside the project


def test_nested_folders_closest_wins_and_search_stops_at_the_repository_root(claude_dir, tmp_path):
    repo = tmp_path / "outer" / "repo"
    sub = repo / "pkg" / "deep"
    sub.mkdir(parents=True)
    (repo / ".git").mkdir()
    write(repo / ".claude" / "agents", "r.md", GOOD.replace("Reviews diffs", "Root"))
    write(repo / "pkg" / ".claude" / "agents", "r.md", GOOD.replace("Reviews diffs", "Package"))
    write(tmp_path / "outer" / ".claude" / "agents", "other.md", GOOD.replace("reviewer", "other"))  # above the repo
    found = agents.list_agents(str(sub))
    assert agents.find("reviewer", str(sub)).description.startswith("Package")
    assert "other" not in {a.name for a in found}


def test_as_dict_has_no_prompt(claude_dir):
    write(claude_dir / "agents", "reviewer.md", GOOD)
    [data] = [agents.as_dict(a) for a in agents.list_agents()]
    assert data["tools"] == ["Read", "Grep"] and "prompt" not in data and data["file"] == "reviewer.md"


def test_a_huge_file_is_not_an_agent(claude_dir):
    write(claude_dir / "agents", "big.md", GOOD + "x" * agents.MAX_FILE_BYTES)
    assert agents.list_agents() == []


# --- API ----------------------------------------------------------------------------


def test_api_lists_the_agents_of_a_folder(claude_dir, tmp_path):
    from ccc import api, api_roles
    from ccc.models import ROLE_READ
    from ccc.storage import Storage

    storage = Storage(str(tmp_path / "ccc.db"))
    folder = tmp_path / "shop"
    (folder / ".git").mkdir(parents=True)
    storage.create_queue("shop", str(folder), "s1")
    write(folder / ".claude" / "agents", "p.md", GOOD.replace("reviewer", "proj"))
    write(claude_dir / "agents", "u.md", GOOD.replace("reviewer", "mine"))
    status, listed = api.Api(storage).route("GET", "/api/v1/agents", {"path": str(folder)}, None)
    assert status == 200
    assert [(a["name"], a["scope"]) for a in listed] == [("proj", "project"), ("mine", "user")]
    assert all("prompt" not in a for a in listed)
    assert api_roles.required_role("GET", "/agents") == ROLE_READ


def test_api_refuses_a_folder_outside_the_allowed_ones(claude_dir, tmp_path):
    from ccc import api
    from ccc.storage import Storage

    storage = Storage(str(tmp_path / "ccc.db"))
    with pytest.raises(api.ApiError) as error:
        api.Api(storage).route("GET", "/api/v1/agents", {"path": "/etc"}, None)
    assert error.value.status == 403


# --- the whole conversation as an agent (launch options, API) --------------------------


def test_agent_launch_option_becomes_the_agent_flag():
    from ccc import launch_options as lo

    sel = lo.toggle(lo.DEFAULT_SELECTION, "agent:reviewer", True)
    assert lo.AGENT_SELECT in sel  # ticking an agent turns the mode on
    assert lo.persistent_flags(sel)[-2:] == ["--agent", "reviewer"]
    assert lo.describe_flags(lo.persistent_flags(sel)).endswith("Agent: reviewer")


def test_only_one_agent_and_untick_clears_it():
    from ccc import launch_options as lo

    sel = lo.toggle(lo.toggle(lo.DEFAULT_SELECTION, "agent:a", True), "agent:b", True)
    assert lo.agent_names(sel) == ["b"]
    assert lo.agent_names(lo.toggle(sel, lo.AGENT_SELECT, False)) == []
    assert "--agent" not in lo.persistent_flags(lo.toggle(sel, lo.AGENT_SELECT, False))


@pytest.mark.parametrize("key", ["agent:", "agent:-x", "agent:a b", "agent:a,b", "agent:" + "n" * 65, "agent:../x"])
def test_unsafe_agent_names_are_no_agent_keys(key):
    from ccc import launch_options as lo

    assert not lo.is_agent(key)


def test_presets_keep_an_agent():
    from ccc.presets import _normalise

    assert "agent:reviewer" in _normalise(["agent:reviewer", "agent_select"])


def test_api_starts_a_conversation_as_an_agent_only_when_it_exists(claude_dir, tmp_path, monkeypatch):
    from ccc import api
    from ccc.orchestrator import start_conversation  # noqa: F401  (patched below)
    from ccc.storage import Storage

    storage = Storage(str(tmp_path / "ccc.db"))
    folder = tmp_path / "shop"
    (folder / ".git").mkdir(parents=True)
    storage.create_queue("shop", str(folder), "s0")
    write(folder / ".claude" / "agents", "reviewer.md", GOOD)
    endpoint = api.Api(storage)
    chosen = {}

    def fake_start(storage_, path, selection, **kwargs):
        chosen["selection"] = selection
        return storage.get_queue_by_path(path)

    monkeypatch.setattr(api, "start_conversation", fake_start)
    body = {"path": str(folder), "options": ["agent_select", "agent:reviewer"]}
    status, _ = endpoint.route("POST", "/api/v1/conversations", {}, body)
    assert status == 201 and "agent:reviewer" in chosen["selection"]
    with pytest.raises(api.ApiError) as missing:
        endpoint.route("POST", "/api/v1/conversations", {}, {**body, "options": ["agent:ghost"]})
    assert missing.value.status == 400
    with pytest.raises(api.ApiError) as two:
        endpoint.route("POST", "/api/v1/conversations", {}, {**body, "options": ["agent:reviewer", "agent:other"]})
    assert two.value.status == 400
