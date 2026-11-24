"""Folders on disk for the New conversation dialog (#337): the listing the
tree and the tab completion use, and the project search.

Pure functions, no widgets. Everything reads one level at a time (never a
recursive walk of a whole disk, except the project search, which has a
depth and a time limit and is only run when the user asks for it):

- hidden folders (a name starting with a dot) are left out, unless the user
  types the dot himself;
- pseudo file systems (/proc, /sys, /dev, /run …), folders nobody may read
  and files are skipped;
- a slow or unplugged disk can't freeze the dialog: callers run these in a
  worker, and the search stops at its deadline."""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

# Never listed under "/" and never searched: pseudo file systems and the
# system's own folders, where nobody keeps a project.
SKIPPED_ROOT_FOLDERS = frozenset({
    "proc", "sys", "dev", "run", "tmp", "lost+found", "boot", "bin", "sbin", "lib", "lib32", "lib64", "libx32",
    "usr", "etc", "var", "snap", "srv", "opt",
})
# Never searched into: dependency and build folders hold thousands of
# folders with a .git of somebody else's project.
SKIPPED_SEARCH_FOLDERS = frozenset({
    "node_modules", "__pycache__", "venv", "site-packages", "target", "build", "dist", "vendor", "Library",
})


def is_hidden(name: str) -> bool:
    return name.startswith(".")


def short(path: str | Path, home: str | None = None) -> str:
    """`path` with the home folder as ~ (how the design shows folders)."""
    text = str(path)
    home = home or str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home.rstrip("/") + "/"):
        return "~" + text[len(home):]
    return text


def expand(text: str) -> str:
    return os.path.expanduser(text)


def visible_folders(path: str | Path, show_hidden: bool = False) -> list[str]:
    """The names of the folders directly in `path`, sorted without regard to
    case; [] when it can't be read."""
    path = str(path)
    names = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                name = entry.name
                if is_hidden(name) and not show_hidden:
                    continue
                if path == "/" and name in SKIPPED_ROOT_FOLDERS:
                    continue
                try:
                    if entry.is_dir():
                        names.append(name)
                except OSError:
                    continue
    except OSError:
        return []
    return sorted(names, key=str.casefold)


def completions(typed: str, limit: int = 12) -> list[str]:
    """The folders the typed text can be completed to, as paths in the form
    the user types them (~ stays ~). "~/DEV/cc" → "~/DEV/ccc_TUI/" …; a
    trailing slash lists the folder's own folders. Hidden ones only when the
    last part starts with a dot."""
    if not typed:
        return []
    parent_text, _slash, prefix = typed.rpartition("/")
    parent_text = parent_text + "/" if _slash else ""
    parent = expand(parent_text) if parent_text else ""
    if not parent or not os.path.isdir(parent):
        return []
    show_hidden = prefix.startswith(".")
    matches = [n for n in visible_folders(parent, show_hidden) if n.casefold().startswith(prefix.casefold())]
    return [parent_text + name + "/" for name in matches[:limit]]


def common_prefix(options: list[str]) -> str:
    return os.path.commonprefix(options) if options else ""


def complete(typed: str) -> tuple[str, list[str]]:
    """What Tab does: (the text to put in the field, the choices left). One
    choice completes it fully; several complete to what they share."""
    options = completions(typed, limit=200)
    if not options:
        return typed, []
    if len(options) == 1:
        return options[0], options
    shared = common_prefix(options)
    return (shared if len(shared) > len(typed) else typed), options[:12]


def nearest_existing(text: str, default: str) -> str:
    """The deepest existing folder of the typed path (the tree opens there)."""
    path = Path(expand(text)) if text.strip() else Path(default)
    while True:
        if path.is_dir():
            return str(path)
        if path.parent == path:
            return default
        path = path.parent


def breadcrumb(path: str | Path) -> str:
    """"~ / DEV /" — where the tree is, shortened with the home folder."""
    text = short(path)
    if text == "/":
        return "/"
    head = "" if text.startswith("~") else "/ "
    return head + " / ".join(p for p in text.split("/") if p) + " /"


def mount_points(proc_mounts: str = "/proc/mounts", user: str | None = None) -> list[str]:
    """Where other disks are mounted (under /mnt, /media and /run/media/<user>) —
    next to the home folder these are where projects are searched."""
    wanted = ("/mnt/", "/media/", f"/run/media/{user or os.environ.get('USER', '')}/")
    found = []
    try:
        with open(proc_mounts, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) >= 2:
                    mount = fields[1].replace("\\040", " ")
                    if any(mount.startswith(prefix) for prefix in wanted) and mount not in found:
                        found.append(mount)
    except OSError:
        pass
    return found


def search_roots(extra: list[str] | None = None) -> list[str]:
    roots = [str(Path.home()), *mount_points(), *(extra or [])]
    unique: list[str] = []
    for root in roots:
        if os.path.isdir(root) and not any(root == r or root.startswith(r.rstrip("/") + "/") for r in unique):
            unique.append(root)
    return unique


def find_projects(
    query: str,
    roots: list[str],
    max_depth: int = 6,
    seconds: float = 20.0,
    stop: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[str]:
    """Folders with a .git whose name contains `query` (case-insensitive),
    found breadth first so the shallow ones come first. Stops at the deadline
    or when `stop()` says so; never goes into hidden folders, dependency
    folders or the system's folders, and a project's own sub-folders aren't
    searched (a project inside a project is rare, and its tree is big)."""
    needle = query.strip().casefold()
    deadline = clock() + seconds
    queue = [(root, 0) for root in roots]
    seen: set[str] = set()
    while queue:
        if clock() > deadline or (stop is not None and stop()):
            return
        path, depth = queue.pop(0)
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        name = os.path.basename(path.rstrip("/")) or path
        if os.path.isdir(os.path.join(path, ".git")):
            if not needle or needle in name.casefold():
                yield path
            continue  # a project: not searched further
        if depth >= max_depth:
            continue
        for child in visible_folders(path):
            if (path == "/" and child in SKIPPED_ROOT_FOLDERS) or child in SKIPPED_SEARCH_FOLDERS:
                continue
            queue.append((os.path.join(path, child), depth + 1))
