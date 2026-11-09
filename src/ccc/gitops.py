"""Git around a headless task: note the state before it, commit what
Claude changed after it, show a commit. Conservative on purpose — never
commits work that was already uncommitted before the task (that's the
user's), nor on a detached HEAD.

Security: the daemon runs git outside Claude's permission mode, in a repo
Claude may have just edited. A conversation limited to editing files
could plant .git/hooks/pre-commit or core.fsmonitor and have ccc execute
it. So (1) the orchestrator only uses this module for conversations that
may run commands anyway (claude_can_run_commands), and (2) every git call
here disables hooks, fsmonitor, external diff/textconv and the pager."""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ccc.i18n import _

logger = logging.getLogger(__name__)

SHOW_MAX_CHARS = 200_000


_HARDENING = [
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "core.untrackedCache=false",
    "-c", "diff.external=",
    "-c", "core.pager=cat",
    "-c", "commit.gpgSign=false",
    "-c", "tag.gpgSign=false",
    "-c", "log.showSignature=false",
    "-c", "gpg.program=/bin/false",
    "-c", "gpg.ssh.program=/bin/false",
    "-c", "gpg.x509.program=/bin/false",
]

# Fail-closed allowlist of repo-local config keys that never make git run
# a program. Anything else (git has many ways to run programs from
# config) blocks the daemon from running further git in that repo.
_SAFE_KEY_PATTERNS = [re.compile(p) for p in (
    r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks|autocrlf|eol|safecrlf|quotepath)",
    r"remote\.[^.]+\.(url|fetch|pushurl|tagopt|prune)",
    r"branch\.[^.]+\.(remote|merge|rebase|pushremote)",
    r"user\.(name|email)",
    r"init\.defaultbranch",
    r"pull\.(rebase|ff)",
    r"push\.(default|autosetupremote)",
    r"fetch\.prune",
    r"extensions\.(objectformat|worktreeconfig)",
)]


def _config_entries(raw: str) -> list[tuple[str, str]]:
    """Parse `git config --list --null`: entries end with NUL, key and value
    are split by the first newline — unambiguous even for multi-line values."""
    entries = []
    for entry in raw.split("\x00"):
        if not entry:
            continue
        key, _sep, value = entry.partition("\n")
        entries.append((key.strip().lower(), value))
    return entries


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *_HARDENING, "-C", cwd, *args],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"},
    )


def claude_can_run_commands(queue) -> bool:
    """True when the conversation's own permission mode already lets
    Claude run arbitrary commands, so ccc running git there grants
    nothing new. Legacy queues (no launch flags) ran with
    --dangerously-skip-permissions."""
    flags = queue.launch_flags
    if flags is None or "--dangerously-skip-permissions" in flags:
        return True
    return any(a == "--permission-mode" and b == "bypassPermissions" for a, b in zip(flags, flags[1:]))


@dataclass
class Snapshot:
    head: str
    clean: bool
    on_branch: bool
    config_fingerprint: str = ""
    # Set when the repo's config is unsafe for the daemon to run git in.
    blocked: str | None = None


@dataclass
class CommitOutcome:
    commit: str | None
    note: str


def _local_config(cwd: str) -> str | None:
    """The repo-local config (reading runs nothing; --no-includes so include
    lines are seen, not followed). None when it can't be read."""
    result = _git(cwd, "config", "--local", "--list", "--null", "--no-includes")
    return result.stdout if result.returncode == 0 else None


def _config_problem(config: str | None) -> str | None:
    if config is None:
        return _("couldn't read the repo's git config")
    for key, _value in _config_entries(config):
        if not any(p.fullmatch(key) for p in _SAFE_KEY_PATTERNS):
            return _("repo git config has '{key}', which ccc doesn't run git with").format(key=key)
    return None


def snapshot(cwd: str) -> Snapshot | None:
    """State before a task; None when cwd isn't inside a git work tree."""
    try:
        inside = _git(cwd, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None
    config = _local_config(cwd)
    problem = _config_problem(config)
    if problem:
        # Checked before `git status`, which can already run clean filters.
        return Snapshot("", False, False, config or "", blocked=problem)
    head = _git(cwd, "rev-parse", "HEAD")
    status = _git(cwd, "status", "--porcelain")
    branch = _git(cwd, "symbolic-ref", "-q", "HEAD")
    return Snapshot(
        head=head.stdout.strip() if head.returncode == 0 else "",
        clean=status.returncode == 0 and status.stdout.strip() == "",
        on_branch=branch.returncode == 0,
        config_fingerprint=config,
    )


def commit_task(cwd: str, before: Snapshot, task_text: str, conversation: str) -> CommitOutcome:
    if before.blocked:
        return CommitOutcome(None, _("not committed: {reason}").format(reason=before.blocked))
    config = _local_config(cwd)
    if config is None or config != before.config_fingerprint:
        return CommitOutcome(None, _("not committed: the repo's git config changed during the task"))
    problem = _config_problem(config)
    if problem:
        return CommitOutcome(None, _("not committed: {reason}").format(reason=problem))
    if not before.clean:
        return CommitOutcome(None, _("not committed: there were uncommitted changes before the task"))
    if not before.on_branch:
        return CommitOutcome(None, _("not committed: detached HEAD"))
    status = _git(cwd, "status", "--porcelain")
    if status.returncode != 0:
        return CommitOutcome(None, _("not committed: git status failed"))
    if status.stdout.strip() == "":
        return CommitOutcome(None, _("no changes"))
    first_line = (task_text.strip().splitlines() or ["task"])[0][:72]
    message = f"ccc: {first_line}\n\nccc-conversation: {conversation}\n"
    added = _git(cwd, "add", "-A")
    committed = _git(cwd, "commit", "-q", "--no-verify", "-m", message) if added.returncode == 0 else added
    if committed.returncode != 0:
        logger.warning("auto-commit failed in %s: %s", cwd, committed.stderr.strip()[:300])
        return CommitOutcome(None, _("not committed: git commit failed"))
    head = _git(cwd, "rev-parse", "HEAD").stdout.strip()
    return CommitOutcome(head, _("committed"))


def revert_commit(cwd: str, commit: str, conversation: str) -> CommitOutcome:
    """Undo one of ccc's task commits with a new commit (git revert). Only
    in a clean work tree on a branch, for a commit in its history; a
    revert that conflicts with later changes is abandoned (nothing
    changes). CommitOutcome.commit is the revert commit, or None with why."""
    if not _COMMIT.match(commit or ""):
        return CommitOutcome(None, _("not reverted: that isn't a commit"))
    before = snapshot(cwd)
    if before is None:
        return CommitOutcome(None, _("not reverted: not a git repository"))
    if before.blocked:
        return CommitOutcome(None, _("not reverted: {reason}").format(reason=before.blocked))
    if not before.clean:
        return CommitOutcome(None, _("not reverted: there are uncommitted changes — commit or stash them first"))
    if not before.on_branch:
        return CommitOutcome(None, _("not reverted: detached HEAD"))
    if _git(cwd, "merge-base", "--is-ancestor", commit, "HEAD").returncode != 0:
        return CommitOutcome(None, _("not reverted: the commit isn't in this branch's history"))
    subject = _git(cwd, "log", "-1", "--format=%s", commit).stdout.strip()
    reverted = _git(cwd, "revert", "--no-edit", "--no-commit", commit)
    if reverted.returncode != 0:
        _git(cwd, "revert", "--abort")
        return CommitOutcome(None, _("not reverted: it conflicts with later changes — nothing was changed"))
    if _git(cwd, "status", "--porcelain").stdout.strip() == "":
        return CommitOutcome(None, _("not reverted: its changes are already gone"))
    message = f"ccc: revert {commit[:7]} ({subject[:60]})\n\nccc-conversation: {conversation}\nccc-reverts: {commit}\n"
    committed = _git(cwd, "commit", "-q", "--no-verify", "-m", message)
    if committed.returncode != 0:
        _git(cwd, "revert", "--abort")
        return CommitOutcome(None, _("not reverted: git commit failed"))
    return CommitOutcome(_git(cwd, "rev-parse", "HEAD").stdout.strip(), _("reverted"))


WORKTREES_DIR = Path(".claude") / "worktrees"  # where Claude Code keeps its own (claude --worktree)
_SLUG = re.compile(r"[^a-z0-9._-]+")


def work_tree_root(path: str) -> str | None:
    """The top folder of the git work tree `path` is in (a worktree is its
    own); None outside git."""
    try:
        result = _git(path, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _safe_worktree_dir(top: str) -> Path:
    """<top>/.claude/worktrees, made without following symlinks: a
    symlink planted in the repository must not send the checkout
    elsewhere. ValueError when one is in the way."""
    current = Path(os.path.realpath(top))
    for part in WORKTREES_DIR.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(_("No worktree: {path} is a symbolic link.").format(path=current))
        if not current.exists():
            current.mkdir()
        elif not current.is_dir():
            raise ValueError(_("No worktree: {path} isn't a folder.").format(path=current))
    if not str(current.resolve()).startswith(os.path.realpath(top) + os.sep):
        raise ValueError(_("No worktree: {path} leads outside the repository.").format(path=current))
    return current


def create_worktree(path: str, name: str) -> str:
    """A new git worktree for a conversation: <repo>/.claude/worktrees/<name>
    on a new branch worktree-<name> from HEAD — where `claude --worktree
    <name>` keeps its own, so Claude Code can open it too. The folder is
    left out of the main checkout's `git status` (.git/info/exclude).
    Returns its path; ValueError with a message for the user."""
    top = work_tree_root(path)
    if top is None:
        raise ValueError(_("A worktree needs a git repository — {path} isn't in one.").format(path=path))
    # The checkout must not run anything: besides the repo-local config,
    # the per-worktree one (config.worktree) could set a filter program.
    worktree_config = _git(top, "config", "--worktree", "--list", "--null", "--no-includes")
    problem = _config_problem(_local_config(top)) or (
        _config_problem(worktree_config.stdout) if worktree_config.returncode == 0 else None)
    if problem:
        raise ValueError(_("No worktree: {reason}.").format(reason=problem))
    base = _SLUG.sub("-", name.lower()).strip("-.")[:40] or "conversation"
    slug, n = base, 1
    while (Path(top) / WORKTREES_DIR / slug).exists() or _git(
            top, "rev-parse", "--verify", "--quiet", f"refs/heads/worktree-{slug}").returncode == 0:
        n += 1
        slug = f"{base}-{n}"
    exclude = _git(top, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    if exclude:
        exclude_path = Path(exclude) if Path(exclude).is_absolute() else Path(top) / exclude
        lines = exclude_path.read_text().splitlines() if exclude_path.exists() else []
        if f"/{WORKTREES_DIR.as_posix()}/" not in lines:
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
            with exclude_path.open("a") as handle:
                handle.write(f"/{WORKTREES_DIR.as_posix()}/\n")
    target = _safe_worktree_dir(top) / slug
    added = _git(top, "worktree", "add", "-q", "-b", f"worktree-{slug}", str(target), "HEAD")
    if added.returncode != 0:
        raise ValueError(_("No worktree: {error}").format(error=added.stderr.strip()[:300]))
    try:
        copied = copy_worktree_include(top, str(target))
        if copied:
            logger.info("Copied %d file(s) listed in %s into the new worktree", copied, WORKTREE_INCLUDE)
    except OSError as exc:  # never fails the worktree itself
        logger.warning("Copying %s files failed (%s)", WORKTREE_INCLUDE, type(exc).__name__)
    return str(target)


WORKTREE_INCLUDE = ".worktreeinclude"
INCLUDE_MAX_FILES = 500
INCLUDE_MAX_BYTES = 20 * 1024 * 1024  # per file


def worktree_include_files(top: str) -> list[str]:
    """Files a new worktree should get although git doesn't carry them:
    those matching a pattern in <top>/.worktreeinclude (gitignore syntax —
    the convention Claude Code, Conductor and Codex share) that git
    actually ignores. So tracked files are never copied, nor anything
    under .claude/worktrees (the other worktrees)."""
    include = Path(top) / WORKTREE_INCLUDE
    if include.is_symlink() or not include.is_file():
        return []
    candidates = _git(top, "ls-files", "--others", "--ignored", "-z", f"--exclude-from={include}")
    if candidates.returncode != 0 or not candidates.stdout:
        return []
    paths = [p for p in candidates.stdout.split("\x00")
             if p and not p.startswith(WORKTREES_DIR.as_posix() + "/")]
    if not paths:
        return []
    ignored = subprocess.run(
        ["git", *_HARDENING, "-C", top, "check-ignore", "-z", "--stdin"],
        input="\x00".join(paths) + "\x00", capture_output=True, text=True, timeout=30,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return sorted(p for p in ignored.stdout.split("\x00") if p)[:INCLUDE_MAX_FILES]


def _inside(base: str, rel: str) -> Path | None:
    """base/rel when no component of it is a symlink and it stays in base."""
    parts = Path(rel).parts
    if not parts or Path(rel).is_absolute() or ".." in parts:
        return None
    current = Path(base)
    for part in parts:
        current = current / part
        if current.is_symlink():
            return None
    return current


def copy_worktree_include(top: str, target: str) -> int:
    """Copy worktree_include_files(top) into the worktree at `target`.
    Only regular files, never through a symbolic link on either side (a
    link in the repo must not pull in or overwrite anything outside it),
    never over an existing file. Returns how many were copied."""
    copied = 0
    for rel in worktree_include_files(top):
        source, dest = _inside(top, rel), _inside(target, rel)
        if source is None or dest is None or not source.is_file() or source.stat().st_size > INCLUDE_MAX_BYTES:
            continue
        parent = Path(target)
        for part in Path(rel).parts[:-1]:
            parent = parent / part
            if not parent.exists():
                parent.mkdir()
            if parent.is_symlink() or not parent.is_dir():
                break
        else:
            mode = source.stat().st_mode & 0o777
            try:
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            except FileExistsError:
                continue
            with os.fdopen(fd, "wb") as out, source.open("rb") as src:
                out.write(src.read())
            copied += 1
    return copied


@dataclass(frozen=True)
class WorkStatus:
    """A work tree at a glance: uncommitted lines added / removed, and
    commits ahead of / behind its base branch (None: no base to compare)."""
    added: int
    removed: int
    ahead: int | None = None
    behind: int | None = None
    base: str | None = None
    # The checked-out branch ("HEAD" when detached).
    branch: str | None = None

    def short(self) -> str:
        parts = []
        lines = (f"+{self.added}" if self.added else "") + (f"−{self.removed}" if self.removed else "")
        if lines:
            parts.append(lines)
        if self.ahead:
            parts.append(f"↑{self.ahead}")
        if self.behind:
            parts.append(f"↓{self.behind}")
        return " ".join(parts)


_SHORTSTAT = re.compile(r"(\d+) insertion|(\d+) deletion")


def _base_branch(cwd: str) -> str | None:
    """What to count ahead/behind against: the branch's upstream, or for a
    ccc / Claude Code worktree (branch worktree-<name>) the branch checked
    out in the main work tree it was made from."""
    upstream = _git(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream.returncode == 0 and upstream.stdout.strip():
        return upstream.stdout.strip()
    branch = _git(cwd, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    if not branch.startswith("worktree-"):
        return None
    listing = _git(cwd, "worktree", "list", "--porcelain").stdout
    first = listing.split("\n\n", 1)[0]
    for line in first.splitlines():
        if line.startswith("branch refs/heads/"):
            main = line[len("branch refs/heads/"):]
            return main if main != branch else None
    return None


def work_status(cwd: str) -> WorkStatus | None:
    """None outside git or where the repo's config isn't safe to run git in
    (checked first: `git diff` against the work tree can run filters).
    Only call it for conversations that may run commands (see the module
    docstring)."""
    try:
        if _git(cwd, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            return None
        if _config_problem(_local_config(cwd)):
            return None
        stat = _git(cwd, "diff", "HEAD", "--shortstat", "--no-ext-diff", "--no-textconv")
        added = removed = 0
        for insertions, deletions in _SHORTSTAT.findall(stat.stdout):
            added += int(insertions or 0)
            removed += int(deletions or 0)
        base = _base_branch(cwd)
        ahead = behind = None
        if base:
            counts = _git(cwd, "rev-list", "--left-right", "--count", f"{base}...HEAD").stdout.split()
            if len(counts) == 2:
                behind, ahead = int(counts[0]), int(counts[1])
        head = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        return WorkStatus(added, removed, ahead, behind, base, head or None)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


CHANGED_FILES_MAX = 200


@dataclass(frozen=True)
class FileChange:
    """One changed file of the work tree: uncommitted lines added / removed
    (None for a binary file or a new, untracked one)."""
    path: str
    status: str  # "modified" | "new"
    added: int | None = None
    removed: int | None = None


def changed_files(cwd: str) -> list[FileChange] | None:
    """The files with uncommitted changes (against HEAD, plus new untracked
    ones), at most CHANGED_FILES_MAX. None outside git or where the repo's
    config isn't safe to run git in. Only call it for conversations that may
    run commands (see the module docstring)."""
    try:
        if _git(cwd, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            return None
        if _config_problem(_local_config(cwd)):
            return None
        numstat = _git(cwd, "diff", "HEAD", "--numstat", "-z", "--no-renames", "--no-ext-diff", "--no-textconv")
        new = _git(cwd, "ls-files", "-z", "--others", "--exclude-standard")
    except (OSError, subprocess.TimeoutExpired):
        return None
    files: list[FileChange] = []
    if numstat.returncode == 0:
        for entry in numstat.stdout.split("\x00"):
            parts = entry.split("\t", 2)
            if len(parts) != 3:
                continue
            added, removed, path = parts
            files.append(FileChange(
                path, "modified",
                int(added) if added.isdigit() else None, int(removed) if removed.isdigit() else None))
    if new.returncode == 0:
        files.extend(FileChange(path, "new") for path in new.stdout.split("\x00") if path)
    return sorted(files, key=lambda f: f.path)[:CHANGED_FILES_MAX]


def listed_files(cwd: str) -> list[str] | None:
    """Tracked and untracked, not ignored files under cwd, relative to it
    (the @-mention picker, ccc.file_mentions). None outside git or where
    the repo's config isn't safe to run git in. Only call it for
    conversations that may run commands (see the module docstring)."""
    try:
        if _git(cwd, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            return None
        if _config_problem(_local_config(cwd)):
            return None
        result = _git(cwd, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return list(dict.fromkeys(f for f in result.stdout.split("\x00") if f))


REVIEW_DIFF_MAX_CHARS = 60_000
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def review_diff(cwd: str, commits: list[str]) -> str | None:
    """What a reviewer reads (ccc.reviews): the commits, or with none the
    uncommitted changes and the names of new files. None when cwd isn't a
    git work tree or its config isn't safe to run git with."""
    try:
        inside = _git(cwd, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if inside.returncode != 0 or inside.stdout.strip() != "true" or _config_problem(_local_config(cwd)):
        return None
    commits = [c for c in commits if _COMMIT.match(c)]
    if commits:
        result = _git(cwd, "show", "--stat", "--patch", "--no-color", "--no-ext-diff", "--no-textconv", *commits)
        text = result.stdout if result.returncode == 0 else ""
    else:
        diff = _git(cwd, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "HEAD")
        new = _git(cwd, "ls-files", "--others", "--exclude-standard")
        text = diff.stdout if diff.returncode == 0 else ""
        if new.returncode == 0 and new.stdout.strip():
            text += "\nNew files (read them whole):\n" + new.stdout
    if len(text) > REVIEW_DIFF_MAX_CHARS:
        text = text[:REVIEW_DIFF_MAX_CHARS] + f"\n… (cut at {REVIEW_DIFF_MAX_CHARS} characters; read the files for the rest)"
    return text


def show(cwd: str, commit: str) -> str:
    problem = _config_problem(_local_config(cwd))
    if problem:
        return _("Not shown: {reason}.").format(reason=problem)
    result = _git(cwd, "show", "--stat", "--patch", "--no-color", "--no-ext-diff", "--no-textconv", commit)
    text = result.stdout if result.returncode == 0 else result.stderr
    if len(text) > SHOW_MAX_CHARS:
        text = text[:SHOW_MAX_CHARS] + "\n" + _("… (truncated, {n} chars)").format(n=len(text))
    return text
