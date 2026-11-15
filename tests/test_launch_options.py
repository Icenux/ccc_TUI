from ccc.launch_options import (
    DEFAULT_SELECTION, FORK, SESSION_CONTINUE, SESSION_NEW, describe_flags,
    persistent_flags, session_mode, toggle,
)


def test_default_is_new_session_with_skip_permissions():
    assert session_mode(DEFAULT_SELECTION) == SESSION_NEW
    assert persistent_flags(DEFAULT_SELECTION) == ["--dangerously-skip-permissions"]


def test_exclusive_group_replaces_previous_choice():
    sel = toggle(DEFAULT_SELECTION, "model_opus", True)
    sel = toggle(sel, "model_sonnet", True)
    assert "model_opus" not in sel and "model_sonnet" in sel


def test_optional_exclusive_group_can_be_cleared():
    sel = toggle(DEFAULT_SELECTION, "effort_max", True)
    sel = toggle(sel, "effort_max", False)
    assert not any(k.startswith("effort_") for k in sel)


def test_required_group_cannot_be_emptied():
    sel = toggle(DEFAULT_SELECTION, "perm_skip", False)
    assert "perm_skip" in sel


def test_fork_only_with_a_resuming_session_mode():
    assert FORK not in toggle(DEFAULT_SELECTION, FORK, True)
    sel = toggle(toggle(DEFAULT_SELECTION, SESSION_CONTINUE, True), FORK, True)
    assert FORK in sel
    assert FORK not in toggle(sel, SESSION_NEW, True)


def test_persistent_flags_in_group_order_without_session_flags():
    sel = DEFAULT_SELECTION
    for key in ("model_opus", "effort_max", "other_chrome", SESSION_CONTINUE, FORK):
        sel = toggle(sel, key, True)
    assert persistent_flags(sel) == [
        "--dangerously-skip-permissions", "--model", "opus", "--effort", "max", "--chrome",
    ]


def test_describe_flags():
    assert describe_flags(None) == "legacy (bypass permissions)"
    assert describe_flags(["--permission-mode", "plan", "--model", "haiku"]) == "plan mode · Haiku"
    assert describe_flags(["--weird"]) == "--weird"


def test_effort_levels_match_installed_claude():
    """The effort choices must be what `claude --effort` accepts (issue #99)."""
    import re
    import shutil
    import subprocess

    import pytest

    from ccc.launch_options import GROUPS

    if not shutil.which("claude"):
        pytest.skip("claude CLI not installed")
    out = subprocess.run(["claude", "--help"], capture_output=True, text=True, timeout=60).stdout
    m = re.search(r"--effort <level>.*?\(([a-z, ]+)\)", out, re.S)
    if not m:
        pytest.skip("claude --help does not list effort levels")
    cli = [x.strip() for x in m.group(1).split(",")]
    ours = [o.flags[1] for g in GROUPS if g.key == "effort" for o in g.options]
    assert sorted(ours) == sorted(cli)


def test_permission_modes_match_installed_claude():
    """Every `--permission-mode` claude accepts is offered (issue #99);
    bypassPermissions is the "Bypass permissions" flag."""
    import re
    import shutil
    import subprocess

    import pytest

    from ccc.launch_options import GROUPS

    if not shutil.which("claude"):
        pytest.skip("claude CLI not installed")
    out = subprocess.run(["claude", "--help"], capture_output=True, text=True, timeout=60).stdout
    m = re.search(r"--permission-mode <mode>.*?\(choices: ([^)]*)\)", out, re.S)
    if not m:
        pytest.skip("claude --help does not list permission modes")
    cli = set(re.findall(r'"(\w+)"', m.group(1))) - {"bypassPermissions"}
    ours = {o.flags[1] for g in GROUPS if g.key == "permissions" for o in g.options if o.flags[0] == "--permission-mode"}
    assert ours == cli
