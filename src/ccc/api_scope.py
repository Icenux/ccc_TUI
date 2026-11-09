"""Where, and how freely, the API may start Claude (#160). Whoever holds a
device's token can start a conversation, and a conversation is Claude Code
running as the user in a folder — so which folders, and whether it may skip
every permission check, are decided on the computer, not by the phone:

* folders: only inside the allowed roots (`ccc api roots`). Not set yet = only
  the folders ccc already knows from its conversations (so the phone can start
  more in the projects it has, but not walk to ~ or /);
* "Skip all permission checks": refused unless `ccc api skip-permissions on`;
  until then the phone's default is "Ask for everything" (approve in the app).

It reaches every endpoint that takes a folder: starting a conversation,
the sessions of a folder, the MCP servers of a folder."""
from __future__ import annotations

import os
import shlex
from http import HTTPStatus
from pathlib import Path

from ccc.api_common import ApiError
from ccc.i18n import _

ROOTS_KEY = "api_allowed_roots"  # one folder per line
SKIP_KEY = "api_allow_skip_permissions"  # "1" = the phone may start conversations that skip permission checks
SKIP_OPTION = "perm_skip"
SAFE_PERMISSION = "perm_manual"  # what replaces it in the phone's defaults


def _real(path: str | Path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(path))))


def roots(storage) -> list[Path]:
    """The allowed roots as set; empty = not set."""
    return [_real(line) for line in (storage.get_setting(ROOTS_KEY) or "").splitlines() if line.strip()]


def add_root(storage, path: str) -> Path:
    real = _real(path)
    if not real.is_dir():
        raise ValueError(_("'{path}' is not an existing directory").format(path=path))
    current = [str(r) for r in roots(storage)]
    if str(real) not in current:
        storage.set_setting(ROOTS_KEY, "\n".join(current + [str(real)]))
    return real


def remove_root(storage, path: str) -> bool:
    real = str(_real(path))
    current = [str(r) for r in roots(storage)]
    if real not in current:
        return False
    storage.set_setting(ROOTS_KEY, "\n".join(r for r in current if r != real))
    return True


def _too_wide(folder: Path) -> bool:
    """/ and the home folder (and anything above it): allowing those allows the whole account."""
    home = _real(Path.home())
    return folder == home or folder in home.parents


def allowed_folders(storage) -> list[Path]:
    """Where a conversation may be started: the roots; when none is set, the folders of the conversations ccc
    has — except / and ~ (a conversation once started there must not open everything beneath)."""
    explicit = roots(storage)
    return explicit or sorted({f for f in (_real(q.project_path) for q in storage.list_queues()) if not _too_wide(f)})


class FolderNotAllowed(ApiError):
    """403 because the folder is outside the allowed roots."""

    def __init__(self, folder: str):
        super().__init__(HTTPStatus.FORBIDDEN, _("The API may not use this folder. Allow it on the computer: ccc api roots --add {path}").format(path=shlex.quote(folder)))
        self.folder = folder


def check_folder(storage, path: str) -> str:
    """The folder (resolved: ~ expanded, symlinks followed) when it is inside an allowed root, else 403."""
    real = _real(path)
    if not any(real == base or base in real.parents for base in allowed_folders(storage)):
        raise FolderNotAllowed(str(real))
    return str(real)


def skip_allowed(storage) -> bool:
    return (storage.get_setting(SKIP_KEY) or "") == "1"


def set_skip_allowed(storage, allowed: bool) -> None:
    storage.set_setting(SKIP_KEY, "1" if allowed else "0")


def default_selection(storage, default: frozenset[str]) -> frozenset[str]:
    """The phone's default choice: without "Skip all permission checks" unless that is allowed."""
    if skip_allowed(storage) or SKIP_OPTION not in default:
        return default
    return (default - {SKIP_OPTION}) | {SAFE_PERMISSION}


def check_selection(storage, selection: frozenset[str]) -> None:
    if SKIP_OPTION in selection and not skip_allowed(storage):
        raise ApiError(HTTPStatus.FORBIDDEN, _("Starting a conversation that skips every permission check is not allowed from the app. "
                                               "Allow it on the computer: ccc api skip-permissions on"))
