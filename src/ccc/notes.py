"""ccc's own notes (#223): plain Markdown files in a folder of ccc's data directory
(`notes/` next to the database), shared with the Android app through the API (ccc.api_notes).
ccc needs no other application for them; the folder can be opened, backed up or put under git as it is.

A note is addressed by its path inside that folder ("ideas/api.md"): segments of letters, digits, spaces
and `. _ - ( )`, none starting with a dot, ending in `.md`, at most MAX_DEPTH levels. Nothing leads out of
the folder: a link in it is never followed, written or listed. Changing a note needs the `version` it was
read with (size and modification time), so an edit made meanwhile on the other side is refused, not lost.
A removed note moves to `deleted-notes/` next to the folder."""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from ccc.i18n import _

MAX_BYTES = 200_000
MAX_DEPTH = 5
MAX_SEGMENT = 100
MAX_LISTED = 2000

BAD, MISSING, EXISTS, CONFLICT = "bad", "missing", "exists", "conflict"


class NoteError(Exception):
    """`kind` is BAD, MISSING, EXISTS or CONFLICT: what the API turns into 400, 404, 409 and 409."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def notes_dir(db_path: str | Path) -> Path:
    return Path(db_path).parent / "notes"


def trash_dir(root: Path) -> Path:
    return root.parent / "deleted-notes"


def _valid_segment(segment: str) -> bool:
    return (0 < len(segment) <= MAX_SEGMENT and not segment.startswith(".") and segment == segment.strip()
            and all(c.isalnum() or c in " ._-()" for c in segment))


def clean_path(raw: object) -> str:
    """The note's path in its canonical form, or a NoteError."""
    if not isinstance(raw, str) or not raw.strip():
        raise NoteError(BAD, _("Give the note's path."))
    parts = raw.strip().split("/")
    if len(parts) > MAX_DEPTH or not all(_valid_segment(p) for p in parts) or not parts[-1].lower().endswith(".md") \
            or len(parts[-1]) <= 3:
        raise NoteError(BAD, _("A note's path is made of folders and a name ending in .md, e.g. ideas/api.md."))
    return "/".join(parts)


def _locate(root: Path, rel: str) -> Path:
    """The file of a note; refuses a link anywhere on the way (it could lead out of the folder)."""
    current = root
    for part in rel.split("/"):
        current = current / part
        if current.is_symlink():
            raise NoteError(BAD, _("A note may not be a link."))
    return current


def _version(info: os.stat_result) -> str:
    return f"{info.st_size}-{info.st_mtime_ns}"


def _title(text: str, rel: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return rel.rsplit("/", 1)[-1][:-3]


def _entry(rel: str, info: os.stat_result, text: str) -> dict:
    return {
        "path": rel,
        "title": _title(text, rel),
        "size": info.st_size,
        "modified": datetime.fromtimestamp(info.st_mtime).astimezone().isoformat(timespec="seconds"),
        "version": _version(info),
    }


def _read_text(path: Path) -> tuple[os.stat_result, str]:
    try:
        info = path.stat()
        if info.st_size > MAX_BYTES:
            raise NoteError(BAD, _("The note is too long."))
        return info, path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise NoteError(MISSING, _("No such note.")) from None
    except OSError as error:
        raise NoteError(BAD, _("Could not read the note: {error}").format(error=error.strerror or error)) from None


def list_notes(root: Path, query: str = "") -> list[dict]:
    """Every note, the most recently changed first; `query` keeps those with it in the path or the text."""
    found: list[dict] = []
    needle = query.strip().lower()
    if not root.is_dir():
        return found
    for current, folders, files in os.walk(root, followlinks=False):
        folders[:] = [f for f in folders if _valid_segment(f) and not (Path(current) / f).is_symlink()]
        for name in files:
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            try:
                clean_path(rel)
                if path.is_symlink() or not path.is_file():
                    continue
                info, text = _read_text(path)
            except NoteError:
                continue
            if needle and needle not in rel.lower() and needle not in text.lower():
                continue
            found.append(_entry(rel, info, text))
    found.sort(key=lambda e: e["version"].split("-")[1], reverse=True)
    found.sort(key=lambda e: e["modified"], reverse=True)
    return found[:MAX_LISTED]


def read_note(root: Path, raw: object) -> dict:
    rel = clean_path(raw)
    info, text = _read_text(_locate(root, rel))
    return {**_entry(rel, info, text), "content": text}


def _check_content(content: object) -> str:
    if not isinstance(content, str):
        raise NoteError(BAD, _("The note's text is missing."))
    if len(content.encode("utf-8")) > MAX_BYTES:
        raise NoteError(BAD, _("The note is too long."))
    return content


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(scratch, path)
    except OSError as error:
        scratch.unlink(missing_ok=True)
        raise NoteError(BAD, _("Could not write the note: {error}").format(error=error.strerror or error)) from None


def create_note(root: Path, raw: object, content: object) -> dict:
    """A new note; an existing one is never overwritten."""
    rel = clean_path(raw)
    text = _check_content(content)
    path = _locate(root, rel)
    if path.exists():
        raise NoteError(EXISTS, _("A note with this path already exists."))
    _write(path, text)
    return read_note(root, rel)


def update_note(root: Path, raw: object, content: object, version: object) -> dict:
    """Change a note read as `version`; another version means it changed since, and nothing is written."""
    rel = clean_path(raw)
    text = _check_content(content)
    if not isinstance(version, str) or not version:
        raise NoteError(BAD, _("Give the version the note was read with."))
    path = _locate(root, rel)
    try:
        current = _version(path.stat())
    except FileNotFoundError:
        raise NoteError(MISSING, _("No such note.")) from None
    if current != version:
        raise NoteError(CONFLICT, _("The note changed since you opened it."))
    _write(path, text)
    return read_note(root, rel)


def rename_note(root: Path, raw: object, new_raw: object) -> dict:
    rel, new_rel = clean_path(raw), clean_path(new_raw)
    source, target = _locate(root, rel), _locate(root, new_rel)
    if not source.is_file():
        raise NoteError(MISSING, _("No such note."))
    if target.exists():
        raise NoteError(EXISTS, _("A note with this path already exists."))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, target)
    except OSError as error:
        raise NoteError(BAD, _("Could not rename the note: {error}").format(error=error.strerror or error)) from None
    return read_note(root, new_rel)


def delete_note(root: Path, raw: object) -> Path:
    """Take a note away: the file moves to deleted-notes/ (not deleted for good), and that path is returned."""
    rel = clean_path(raw)
    path = _locate(root, rel)
    if not path.is_file():
        raise NoteError(MISSING, _("No such note."))
    target_dir = trash_dir(root)
    target = target_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{rel.replace('/', '__')}"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(target_dir, 0o700)
        shutil.move(os.fspath(path), os.fspath(target))
    except OSError as error:
        raise NoteError(BAD, _("Could not remove the note: {error}").format(error=error.strerror or error)) from None
    return target
