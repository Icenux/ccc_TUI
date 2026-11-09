import subprocess

import pytest

from ccc import gitops


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "a.txt").write_text("one\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


def test_not_a_repo(tmp_path):
    assert gitops.snapshot(str(tmp_path)) is None


def test_clean_snapshot_then_commit(repo):
    before = gitops.snapshot(str(repo))
    assert before.clean and before.head
    (repo / "a.txt").write_text("two\n")
    (repo / "b.txt").write_text("new\n")
    outcome = gitops.commit_task(str(repo), before, "Fix things\nmore detail", "my conv")
    assert outcome.commit and outcome.commit != before.head
    log = _git(repo, "log", "-1", "--format=%s%n%b")
    assert log.startswith("ccc: Fix things") and "ccc-conversation: my conv" in log
    assert _git(repo, "status", "--porcelain") == ""
    shown = _git(repo, "show", outcome.commit)
    assert "b.txt" in shown and "+two" in shown


def test_no_changes_no_commit(repo):
    before = gitops.snapshot(str(repo))
    outcome = gitops.commit_task(str(repo), before, "noop", "c")
    assert outcome.commit is None and "no changes" in outcome.note


def test_dirty_before_task_is_not_committed(repo):
    (repo / "a.txt").write_text("my own wip\n")
    before = gitops.snapshot(str(repo))
    assert before.clean is False
    (repo / "c.txt").write_text("claude\n")
    outcome = gitops.commit_task(str(repo), before, "task", "c")
    assert outcome.commit is None and "uncommitted changes before" in outcome.note
    assert "a.txt" in _git(repo, "status", "--porcelain")  # untouched


def test_detached_head_is_not_committed(repo):
    head = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", head)
    before = gitops.snapshot(str(repo))
    (repo / "d.txt").write_text("x\n")
    outcome = gitops.commit_task(str(repo), before, "task", "c")
    assert outcome.commit is None and "detached" in outcome.note


def test_repo_hooks_and_fsmonitor_never_run(repo, tmp_path):
    marker = tmp_path / "pwned"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    fsmon = tmp_path / "fsmon.sh"
    fsmon.write_text(f"#!/bin/sh\ntouch {marker}\n")
    fsmon.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(fsmon))
    before = gitops.snapshot(str(repo))
    (repo / "a.txt").write_text("changed\n")
    outcome = gitops.commit_task(str(repo), before, "task", "c")
    # core.fsmonitor isn't on the allowlist: blocked outright, nothing runs.
    assert outcome.commit is None and before.blocked
    assert not marker.exists()


def test_external_diff_driver_is_not_used_by_review_diff(repo, tmp_path):
    marker = tmp_path / "pwned"
    tool = tmp_path / "extdiff.sh"
    tool.write_text(f"#!/bin/sh\ntouch {marker}\n")
    tool.chmod(0o755)
    _git(repo, "config", "diff.external", str(tool))
    before = gitops.snapshot(str(repo))
    (repo / "a.txt").write_text("changed\n")
    outcome = gitops.commit_task(str(repo), before, "task", "c")
    assert outcome.commit is None and before.blocked
    assert gitops.review_diff(str(repo), []) is None
    assert not marker.exists()


def test_claude_can_run_commands():
    from ccc.models import Queue

    def q(flags):
        return Queue(id=1, name="n", project_path="/p", session_id="s", launch_flags=flags)

    assert gitops.claude_can_run_commands(q(None))  # legacy queues ran with skip-permissions
    assert gitops.claude_can_run_commands(q(["--dangerously-skip-permissions", "--model", "opus"]))
    assert gitops.claude_can_run_commands(q(["--permission-mode", "bypassPermissions"]))
    assert not gitops.claude_can_run_commands(q(["--permission-mode", "acceptEdits"]))
    assert not gitops.claude_can_run_commands(q(["--permission-mode", "plan"]))
    assert not gitops.claude_can_run_commands(q([]))


def test_signing_program_never_runs(repo, tmp_path):
    marker = tmp_path / "pwned"
    prog = tmp_path / "gpg.sh"
    prog.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    prog.chmod(0o755)
    _git(repo, "config", "commit.gpgSign", "true")
    _git(repo, "config", "gpg.program", str(prog))
    _git(repo, "config", "log.showSignature", "true")
    before = gitops.snapshot(str(repo))
    (repo / "a.txt").write_text("changed\n")
    outcome = gitops.commit_task(str(repo), before, "task", "c")
    # Signing config present -> not auto-committed, and nothing executed.
    assert outcome.commit is None and "config" in outcome.note
    assert not marker.exists()


def test_filter_or_include_in_repo_config_skips_commit(repo, tmp_path):
    marker = tmp_path / "pwned"
    _git(repo, "config", "filter.evil.clean", f"sh -c 'touch {marker}; cat'")
    (repo / ".gitattributes").write_text("*.txt filter=evil\n")
    before = gitops.snapshot(str(repo))
    (repo / "a.txt").write_text("changed\n")
    outcome = gitops.commit_task(str(repo), before, "task", "c")
    assert outcome.commit is None and "config" in outcome.note
    assert not marker.exists()


def test_config_changed_during_task_skips_commit(repo):
    before = gitops.snapshot(str(repo))
    _git(repo, "config", "user.name", "Someone Else")
    (repo / "a.txt").write_text("changed\n")
    outcome = gitops.commit_task(str(repo), before, "task", "c")
    assert outcome.commit is None and "changed during the task" in outcome.note


def test_show_refuses_risky_repo(repo):
    head = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "config", "include.path", "/tmp/elsewhere")
    assert gitops.review_diff(str(repo), [head]) is None


def test_unknown_config_key_blocks_fail_closed(repo):
    # Not on the allowlist (could be any of git's many program hooks).
    _git(repo, "config", "core.editor", "/tmp/evil-editor")
    before = gitops.snapshot(str(repo))
    assert before.blocked
    (repo / "a.txt").write_text("changed\n")
    assert gitops.commit_task(str(repo), before, "t", "c").commit is None


def test_unreadable_config_blocks(repo, monkeypatch):
    real = gitops._git

    def failing(cwd, *args):
        if args[:1] == ("config",):
            import subprocess as sp
            return sp.CompletedProcess(args, 1, "", "boom")
        return real(cwd, *args)

    monkeypatch.setattr(gitops, "_git", failing)
    snap = gitops.snapshot(str(repo))
    assert snap.blocked and "read" in snap.blocked


def test_newline_in_value_cannot_smuggle_a_key(repo):
    # Without --null this value would print as a separate filter.* line.
    _git(repo, "config", "user.name", "Name\nfilter.evil.clean=touch /tmp/x")
    before = gitops.snapshot(str(repo))
    assert before.blocked is None  # user.name is allowed; the value is just text
    assert gitops._config_entries("user.name\nName\nfilter.evil.clean=x\x00") == [("user.name", "Name\nfilter.evil.clean=x")]


def test_typical_clone_config_is_allowed(repo):
    _git(repo, "remote", "add", "origin", "https://example.com/r.git")
    _git(repo, "config", "branch.main.remote", "origin")
    _git(repo, "config", "branch.main.merge", "refs/heads/main")
    _git(repo, "config", "pull.rebase", "false")
    assert gitops.snapshot(str(repo)).blocked is None



def test_hook_without_config_never_runs(repo, tmp_path):
    marker = tmp_path / "pwned"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    before = gitops.snapshot(str(repo))
    (repo / "a.txt").write_text("changed\n")
    assert gitops.commit_task(str(repo), before, "task", "c").commit
    assert not marker.exists()
