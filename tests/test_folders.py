import asyncio
import subprocess

import pytest

from ccc import folders, gitops
from ccc.launch_options import DEFAULT_SELECTION
from ccc.models import QueueStatus, RunStatus
from ccc.orchestrator import Orchestrator, QueueRuntime, start_conversation
from ccc.runner import RunResult
from ccc.storage import Storage


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def _fresh_cache():
    folders._roots.clear()


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "h.db")


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t")):
        _git(path, *args)
    (path / "a.txt").write_text("one\n")
    (path / "sub").mkdir()
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")
    return path


def test_create_worktree_branches_off_head_and_hides_from_the_main_status(repo):
    path = gitops.create_worktree(str(repo), "Fix Login!")
    assert path == str(repo / ".claude" / "worktrees" / "fix-login")
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD") == "worktree-fix-login"
    assert (repo / ".claude/worktrees/fix-login/a.txt").read_text() == "one\n"
    assert _git(repo, "status", "--porcelain") == ""  # left out of the main checkout
    again = gitops.create_worktree(str(repo), "fix login")
    assert again.endswith("fix-login-2")
    assert gitops.work_tree_root(path) == path != gitops.work_tree_root(str(repo / "sub"))


def test_create_worktree_refuses_outside_git_and_unsafe_config(tmp_path, repo):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ValueError, match="needs a git repository"):
        gitops.create_worktree(str(plain), "x")
    _git(repo, "config", "core.pager", "touch /tmp/pwned")
    with pytest.raises(ValueError, match="No worktree"):
        gitops.create_worktree(str(repo), "x")


def test_start_conversation_in_its_own_worktree(storage, repo):
    queue = start_conversation(storage, str(repo), DEFAULT_SELECTION, name="login", worktree=True,
                               interactive=False)
    assert queue.project_path == str(repo / ".claude" / "worktrees" / "login")


def test_neighbours_are_conversations_in_the_same_work_tree(storage, repo):
    a = storage.create_queue("a", str(repo), "s1")
    b = storage.create_queue("b", str(repo / "sub"), "s2")  # same repo, a subfolder
    c = storage.create_queue("c", gitops.create_worktree(str(repo), "c"), "s3")  # own worktree
    archived = storage.create_queue("old", str(repo), "s4")
    storage.set_queue_archived(archived.id, True)
    assert [q.name for q in folders.neighbours(storage, a)] == ["b"]
    assert folders.neighbours(storage, c) == []
    storage.update_queue_status(b.id, QueueStatus.RUNNING)
    assert folders.running_neighbour(storage, a).name == "b"


def test_no_git_for_conversations_that_may_not_run_commands(storage, repo, monkeypatch):
    calls = []
    monkeypatch.setattr(gitops, "work_tree_root", lambda path: calls.append(path))
    restricted = storage.create_queue("ro", str(repo), "s1", launch_flags=["--permission-mode", "acceptEdits"])
    folders.neighbours(storage, restricted)
    assert calls == []


async def _until(cond, timeout=3.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_headless_tasks_in_one_folder_take_turns_and_dont_commit_each_others_work(storage, repo, monkeypatch):
    monkeypatch.setattr("ccc.orchestrator.POLL_INTERVAL_SECONDS", 0.02)
    a = storage.create_queue("a", str(repo), "s1")
    b = storage.create_queue("b", str(repo), "s2")
    release = asyncio.Event()
    running = []

    async def run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        running.append(prompt)
        assert len(running) - len(done) == 1, "two tasks at once in one folder"
        (repo / f"{prompt}.txt").write_text(prompt)
        if prompt == "first":
            await release.wait()
        done.append(prompt)
        return RunResult(status=RunStatus.SUCCESS)

    done = []
    storage.add_queue_item(a.id, "first")
    orch = Orchestrator(storage, run_fn=run)
    await orch.start_all()
    assert await _until(lambda: running == ["first"])
    storage.add_queue_item(b.id, "second")
    await asyncio.sleep(0.2)
    assert running == ["first"]  # b waits for a
    release.set()
    assert await _until(lambda: done == ["first", "second"])
    assert await _until(lambda: len(storage.runs_for_queue(b.id)) == 1)
    await orch.stop_all()
    # Each task committed only its own file.
    first = storage.runs_for_queue(a.id)[0]
    second = storage.runs_for_queue(b.id)[0]
    assert _git(repo, "show", "--name-only", "--format=", first.git_commit) == "first.txt"
    assert _git(repo, "show", "--name-only", "--format=", second.git_commit) == "second.txt"


@pytest.mark.asyncio
async def test_no_commit_while_an_interactive_neighbour_works(storage, repo, monkeypatch):
    from ccc.models import DeliveryMode

    monkeypatch.setattr("ccc.orchestrator.POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr("ccc.reviews.managed_idle", lambda queue: False)  # Claude busy with something typed
    storage.create_queue("live", str(repo), "s9", delivery_mode=DeliveryMode.INTERACTIVE)
    a = storage.create_queue("a", str(repo), "s1")

    async def run(project_path, session_id, prompt, resume, launch_flags=None, first_run_flags=None):
        (repo / "x.txt").write_text("x")
        return RunResult(status=RunStatus.SUCCESS)

    storage.add_queue_item(a.id, "task")
    # Only a's runtime: the interactive neighbour is faked as busy.
    runtime = QueueRuntime(storage, storage.get_queue(a.id), run, None)
    runtime.start()
    assert await _until(lambda: storage.runs_for_queue(a.id))
    await runtime.stop()
    run_row = storage.runs_for_queue(a.id)[0]
    assert run_row.git_commit is None and "live" in run_row.git_note


def test_a_symlinked_worktrees_folder_is_refused(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".claude" / "worktrees").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic link"):
        gitops.create_worktree(str(repo), "x")
    assert list(outside.iterdir()) == []


def test_a_filter_in_the_worktree_config_is_refused(repo):
    _git(repo, "config", "extensions.worktreeConfig", "true")
    _git(repo, "config", "--worktree", "filter.evil.smudge", "touch /tmp/pwned")
    with pytest.raises(ValueError, match="No worktree"):
        gitops.create_worktree(str(repo), "x")


def test_an_own_worktree_needs_skip_permissions(storage, repo):
    from ccc.launch_options import toggle

    restricted = toggle(DEFAULT_SELECTION, "perm_accept_edits", True)
    with pytest.raises(ValueError, match="Bypass permissions"):
        start_conversation(storage, str(repo), restricted, name="x", worktree=True, interactive=False)
    assert not (repo / ".claude" / "worktrees").exists()


def test_new_worktree_gets_the_gitignored_files_from_worktreeinclude(tmp_path, repo):
    (repo / ".gitignore").write_text(".env\nconfig/local.json\nbuild/\n")
    (repo / ".worktreeinclude").write_text(".env\nconfig/\na.txt\nbuild/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "ignore")
    (repo / ".env").write_text("KEY=1\n")
    (repo / "config").mkdir()
    (repo / "config/local.json").write_text("{}")
    (repo / "config/notes.txt").write_text("untracked, not ignored")  # matches, but git doesn't ignore it
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (repo / "build").mkdir()
    (repo / "build/link").symlink_to(outside)  # ignored, but a symlink
    gitops.create_worktree(str(repo), "w")  # existing worktree: its .env must not leak into the next
    (repo / ".claude/worktrees/w/.env").write_text("OTHER=1\n")
    path = gitops.create_worktree(str(repo), "x")
    dest = repo / ".claude/worktrees/x"
    assert path == str(dest)
    assert (dest / ".env").read_text() == "KEY=1\n"
    assert (dest / "config/local.json").read_text() == "{}"
    assert not (dest / "config/notes.txt").exists()
    assert (dest / "a.txt").read_text() == "one\n"  # tracked: from the checkout, not copied
    assert not (dest / "build/link").exists()
    assert not (dest / ".claude").exists()


def test_worktreeinclude_never_follows_symlinks(tmp_path, repo):
    (repo / ".gitignore").write_text(".env\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "ignore")
    (repo / ".env").write_text("KEY=1\n")
    real = tmp_path / "include"
    real.write_text(".env\n")
    (repo / ".worktreeinclude").symlink_to(real)  # the list itself is a symlink: ignored
    path = gitops.create_worktree(str(repo), "x")
    assert not (repo / ".claude/worktrees/x/.env").exists()
    # A tracked symlink in the checkout must not redirect a copy outside.
    (repo / ".worktreeinclude").unlink()
    (repo / ".worktreeinclude").write_text("conf/\n")
    (repo / "conf").mkdir()
    (repo / "conf/s.txt").write_text("s")
    (repo / ".gitignore").write_text(".env\nconf/\n")
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "evil").mkdir()
    (target / "conf").symlink_to(tmp_path / "evil")
    assert gitops.copy_worktree_include(str(repo), str(target)) == 0
    assert not (tmp_path / "evil/s.txt").exists()
    assert path


def test_work_status_counts_uncommitted_lines_and_commits_against_the_base(repo):
    path = gitops.create_worktree(str(repo), "feature")
    assert gitops.work_status(path) == gitops.WorkStatus(0, 0, 0, 0, _git(repo, "branch", "--show-current"), "worktree-feature")
    (repo / ".claude/worktrees/feature/a.txt").write_text("one\ntwo\nthree\n")
    _git(path, "commit", "-qam", "more")
    (repo / ".claude/worktrees/feature/a.txt").write_text("ONE\ntwo\nthree\n")
    (repo / "b.txt").write_text("main moved on\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "main")
    status = gitops.work_status(path)
    assert (status.added, status.removed, status.ahead, status.behind) == (1, 1, 1, 1)
    assert status.short() == "+1−1 ↑1 ↓1"
    # A plain checkout without an upstream: only the uncommitted lines.
    main = gitops.work_status(str(repo))
    assert (main.ahead, main.base, main.short()) == (None, None, "")


def test_work_status_refuses_outside_git_and_with_unsafe_config(tmp_path, repo):
    assert gitops.work_status(str(tmp_path)) is None
    _git(repo, "config", "filter.x.clean", "touch /tmp/pwned")
    assert gitops.work_status(str(repo)) is None


def test_work_status_short_shows_only_what_is_there():
    assert gitops.WorkStatus(4, 0).short() == "+4"
    assert gitops.WorkStatus(0, 2, 0, 3).short() == "−2 ↓3"
    assert gitops.WorkStatus(0, 0, 0, 0).short() == ""
