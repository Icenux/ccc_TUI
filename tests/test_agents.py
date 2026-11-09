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
    from ccc import api
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


# --- creating agents (#220) -------------------------------------------------------------


def test_written_file_reads_back_the_same(claude_dir):
    desc = 'Reviews: "bugs" & # things — use after changes'
    path, new_folder = agents.write_agent("my-reviewer", desc, ("Read", "Bash"), "sonnet", "You review.\n\nBe brief.",
                                          agents.SCOPE_USER)
    assert path == claude_dir / "agents" / "my-reviewer.md" and not new_folder  # the fixture made the folder
    [a] = agents.list_agents()
    assert (a.name, a.description, a.tools, a.model, a.problem) == ("my-reviewer", desc, ("Read", "Bash"), "sonnet", "")
    assert a.prompt.strip() == "You review.\n\nBe brief."


def test_no_tools_and_no_model_are_left_out(claude_dir):
    path, _new = agents.write_agent("plain", "d", None, "", "x", agents.SCOPE_USER)
    text = path.read_text()
    assert "tools:" not in text and "model:" not in text
    assert agents.list_agents()[0].tools is None  # inherits every tool


def test_existing_file_is_never_overwritten(claude_dir):
    agents.write_agent("a", "d", None, "", "first", agents.SCOPE_USER)
    with pytest.raises(ValueError, match="already exists"):
        agents.write_agent("a", "d", None, "", "second", agents.SCOPE_USER)
    assert "first" in (claude_dir / "agents" / "a.md").read_text()


def test_symlink_is_not_followed(claude_dir, tmp_path):
    target = tmp_path / "victim.txt"
    target.write_text("keep")
    (claude_dir / "agents" / "a.md").symlink_to(target)
    with pytest.raises(ValueError):
        agents.write_agent("a", "d", None, "", "x", agents.SCOPE_USER)
    assert target.read_text() == "keep"


@pytest.mark.parametrize("name", ["", "Upper", "-x", "a b", "a/b", "../x", "a:b", "n" * 65])
def test_bad_names_are_refused(claude_dir, name):
    with pytest.raises(ValueError):
        agents.write_agent(name, "d", None, "", "x", agents.SCOPE_USER)
    assert list((claude_dir / "agents").iterdir()) == []


@pytest.mark.parametrize("kwargs", [{"description": "  "}, {"model": "gpt"}, {"tools": ()}, {"tools": ("Bash;rm",)}])
def test_bad_fields_are_refused(claude_dir, kwargs):
    fields = {"description": "d", "tools": None, "model": "", **kwargs}
    with pytest.raises(ValueError):
        agents.write_agent("a", fields["description"], fields["tools"], fields["model"], "x", agents.SCOPE_USER)


def test_project_scope_creates_the_folder_and_says_so(claude_dir, tmp_path):
    project = tmp_path / "shop"
    project.mkdir()
    path, new_folder = agents.write_agent("a", "d", None, "", "x", agents.SCOPE_PROJECT, str(project))
    assert path == project / ".claude" / "agents" / "a.md" and new_folder
    with pytest.raises(ValueError):  # no folder to keep it in
        agents.write_agent("b", "d", None, "", "x", agents.SCOPE_PROJECT, "")


def test_every_template_is_a_loadable_agent_with_few_tools(claude_dir):
    from ccc import agent_templates

    for t in agent_templates.TEMPLATES:
        description, tools, model, prompt = t.parts()
        agents.write_agent(t.key, description, tools, model, prompt, agents.SCOPE_USER)
    found = {a.name: a for a in agents.list_agents()}
    assert set(found) == {t.key for t in agent_templates.TEMPLATES}
    assert all(not a.problem and a.tools and "Agent" not in a.tools and a.model for a in found.values())
    for name in ("code-reviewer", "code-explorer"):  # readers only
        assert "Edit" not in found[name].tools and "Write" not in found[name].tools


def test_czech_templates_are_written_in_czech(claude_dir):
    from ccc import agent_templates, i18n

    i18n.set_language("cs")
    try:
        description, _tools, _model, prompt = agent_templates.BY_KEY["test-runner"].parts()
    finally:
        i18n.set_language("en")
    assert "Spustí testy" in description and "Spouštíš testy" in prompt


# --- editing and deleting (#220) ---------------------------------------------------------

RICH = """---
name: rich
description: Old description
tools: Read, Grep
model: haiku
memory: project
hooks:
  PreToolUse:
    - matcher: Bash
      hooks:
        - type: command
          command: ./check.sh
# a comment
---

Old prompt.
"""


def test_edit_changes_only_what_was_asked(claude_dir):
    write(claude_dir / "agents", "rich.md", RICH)
    [a] = agents.list_agents()
    agents.update_agent(a, 'New: "description"', ("Read", "Bash"), "sonnet", "New prompt.\n\nSecond paragraph.")
    text = (claude_dir / "agents" / "rich.md").read_text()
    assert "memory: project" in text and "command: ./check.sh" in text and "# a comment" in text  # untouched
    assert "name: rich" in text and "Old" not in text
    [b] = agents.list_agents()
    assert (b.name, b.description, b.tools, b.model) == ("rich", 'New: "description"', ("Read", "Bash"), "sonnet")
    assert b.prompt.strip() == "New prompt.\n\nSecond paragraph."


def test_edit_can_drop_tools_and_model_and_adds_missing_ones(claude_dir):
    write(claude_dir / "agents", "rich.md", RICH)
    [a] = agents.list_agents()
    agents.update_agent(a, "d", None, "", "p")  # inherit every tool, the session's model
    text = (claude_dir / "agents" / "rich.md").read_text()
    assert "tools:" not in text and "model:" not in text and "memory: project" in text
    write(claude_dir / "agents", "bare.md", "---\nname: bare\ndescription: d\n---\nx")
    bare = next(x for x in agents.list_agents() if x.name == "bare")
    agents.update_agent(bare, "d", ("Read",), "opus", "x")
    assert "tools: Read" in (claude_dir / "agents" / "bare.md").read_text()
    assert next(x for x in agents.list_agents() if x.name == "bare").model == "opus"


def test_edit_replaces_a_list_or_block_field_with_all_its_lines(claude_dir):
    write(claude_dir / "agents", "a.md", "---\nname: a\ndescription: >\n  long\n  text\ntools:\n  - Read\n  - Bash\nmodel: opus\n---\nx")
    [a] = agents.list_agents()
    agents.update_agent(a, "short", ("Grep",), "opus", "x")
    text = (claude_dir / "agents" / "a.md").read_text()
    assert "long" not in text and "- Read" not in text and "- Bash" not in text and "tools: Grep" in text
    assert agents.list_agents()[0].tools == ("Grep",)


def test_edit_keeps_a_model_the_file_already_has_but_refuses_a_new_unknown_one(claude_dir):
    write(claude_dir / "agents", "a.md", "---\nname: a\ndescription: d\nmodel: claude-opus-5-5\n---\nx")
    [a] = agents.list_agents()
    agents.update_agent(a, "d2", None, "claude-opus-5-5", "x")
    with pytest.raises(ValueError, match="Unknown model"):
        agents.update_agent(agents.list_agents()[0], "d3", None, "gpt-5", "x")


@pytest.mark.parametrize("bad", [{"description": "  "}, {"tools": ()}, {"tools": ("a b",)}])
def test_edit_refuses_bad_values_and_leaves_the_file(claude_dir, bad):
    write(claude_dir / "agents", "rich.md", RICH)
    [a] = agents.list_agents()
    fields = {"description": "d", "tools": ("Read",), **bad}
    with pytest.raises(ValueError):
        agents.update_agent(a, fields["description"], fields["tools"], "haiku", "p")
    assert (claude_dir / "agents" / "rich.md").read_text() == RICH


def test_edit_refuses_a_file_changed_since_it_was_read(claude_dir):
    write(claude_dir / "agents", "rich.md", RICH)
    [a] = agents.list_agents()
    (claude_dir / "agents" / "rich.md").write_text(RICH + "\nSomeone else wrote this.\n")
    with pytest.raises(ValueError, match="changed on disk"):
        agents.update_agent(a, "d", ("Read",), "haiku", "p")
    assert "Someone else" in (claude_dir / "agents" / "rich.md").read_text()


def test_edit_refuses_a_symbolic_link_and_keeps_the_mode(claude_dir, tmp_path):
    real = tmp_path / "shared.md"
    real.write_text(RICH)
    (claude_dir / "agents" / "rich.md").symlink_to(real)
    [a] = agents.list_agents()
    with pytest.raises(ValueError, match="symbolic link"):
        agents.update_agent(a, "d", ("Read",), "haiku", "p")
    assert real.read_text() == RICH
    (claude_dir / "agents" / "rich.md").unlink()
    write(claude_dir / "agents", "mode.md", RICH.replace("name: rich", "name: mode"))
    (claude_dir / "agents" / "mode.md").chmod(0o600)
    agents.update_agent(agents.list_agents()[0], "d", ("Read",), "haiku", "p")
    assert (claude_dir / "agents" / "mode.md").stat().st_mode & 0o777 == 0o600
    assert not list((claude_dir / "agents").glob(".*ccc-tmp"))  # no scratch file left


def test_delete_moves_the_file_to_ccc_s_folder(claude_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_AGENT_TRASH", str(tmp_path / "trash"))
    write(claude_dir / "agents", "gone.md", GOOD.replace("reviewer", "gone"))
    [a] = agents.list_agents()
    kept = agents.delete_agent(a)
    assert not (claude_dir / "agents" / "gone.md").exists() and agents.list_agents() == []
    assert kept.parent == tmp_path / "trash" and kept.name.endswith("-user-gone.md")
    assert "You review." in kept.read_text()  # recoverable
    assert kept.parent.stat().st_mode & 0o777 == 0o700


def test_delete_touches_only_agent_files(claude_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_AGENT_TRASH", str(tmp_path / "trash"))
    stray = tmp_path / "elsewhere" / "notes.md"
    stray.parent.mkdir()
    stray.write_text("keep")
    fake = agents.Agent(name="x", description="d", scope="user", path=stray)
    with pytest.raises(ValueError, match="not an agent file"):
        agents.delete_agent(fake)
    assert stray.read_text() == "keep"


def test_edit_does_not_follow_a_symlink_planted_at_a_guessable_scratch_name(claude_dir, tmp_path):
    """The agents folder can come from a cloned repository: a link at `.<file>.ccc-tmp` must not make ccc
    overwrite what it points to."""
    write(claude_dir / "agents", "rich.md", RICH)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    for guess in (".rich.md.ccc-tmp", "rich.md.ccc-tmp", ".rich.md.tmp"):
        (claude_dir / "agents" / guess).symlink_to(victim)
    [a] = agents.list_agents()
    agents.update_agent(a, "d", ("Read",), "haiku", "p")
    assert victim.read_text() == "keep me"
    assert agents.list_agents()[0].description == "d"
    left = [p.name for p in (claude_dir / "agents").iterdir() if p.is_symlink()]
    assert sorted(left) == sorted([".rich.md.ccc-tmp", "rich.md.ccc-tmp", ".rich.md.tmp"])  # theirs, untouched
