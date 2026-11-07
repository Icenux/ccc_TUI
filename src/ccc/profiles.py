"""Claude accounts ("profiles") a conversation can run under (#239).

Claude Code keeps one login per configuration directory, and documents
`CLAUDE_CONFIG_DIR` as the way to be signed in to several accounts at once
(code.claude.com/docs/en/authentication, "Log in with multiple accounts").
A profile is therefore a name plus such a directory; a conversation runs
`claude` with that directory in its environment and nothing else changes.

The *default* profile (name "") is exactly what ccc did before profiles
existed: it adds nothing to the environment, so `claude` uses
`CLAUDE_CONFIG_DIR` if ccc itself was started with it, else `~/.claude`.
Nobody who never creates a profile sees any difference.

The profiles are listed in a small JSON file of ccc's data folder. Each
profile directory has its own login, settings, history and MCP servers —
that is Claude Code's behaviour, not something ccc papers over.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ccc.i18n import _

DEFAULT = ""  # the profile of conversations that name none

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Credentials from the environment outrank a signed-in profile in Claude
# Code's order (an API key, a bearer token, a long-lived OAuth token), so a
# named profile would silently keep using the same account. They are
# removed from the environment of a named profile's processes.
OVERRIDING_CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")


class ProfileError(ValueError):
    """The user's input is not a usable profile (message is shown as is)."""


@dataclass(frozen=True)
class Profile:
    name: str
    config_dir: Path


def _registry_path() -> Path:
    override = os.environ.get("CCC_PROFILES_FILE")
    return Path(override) if override else Path.home() / ".local" / "share" / "ccc" / "profiles.json"


def default_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _read() -> dict[str, str]:
    """name -> directory. A missing, unreadable or malformed file is an empty
    list: profiles are optional, never a reason for ccc to fail."""
    try:
        data = json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    listed = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(listed, dict):
        return {}
    return {n: d for n, d in listed.items() if isinstance(n, str) and NAME_RE.match(n) and isinstance(d, str) and d}


def _write(profiles: dict[str, str]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".profiles-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"profiles": profiles}, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)  # atomic: a crash never leaves half a file
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def list_profiles() -> list[Profile]:
    return [Profile(name, Path(directory)) for name, directory in sorted(_read().items())]


def get(name: str | None) -> Profile | None:
    """The named profile; None for the default profile and for a name that
    is no longer listed (the conversation then runs as the default one
    rather than failing)."""
    if not name:
        return None
    directory = _read().get(name)
    return Profile(name, Path(directory)) if directory else None


def config_dir(name: str | None) -> Path:
    profile = get(name)
    return profile.config_dir if profile else default_config_dir()


def all_config_dirs() -> list[Path]:
    """Every directory conversations may live in: the default one first."""
    dirs = [default_config_dir()]
    for profile in list_profiles():
        if profile.config_dir not in dirs:
            dirs.append(profile.config_dir)
    return dirs


def env_for(name: str | None) -> dict[str, str] | None:
    """The environment to start `claude` with; None = inherit ccc's own
    (the default profile, exactly as before profiles existed)."""
    profile = get(name)
    if profile is None:
        return None
    env = {k: v for k, v in os.environ.items() if k not in OVERRIDING_CREDENTIALS}
    env["CLAUDE_CONFIG_DIR"] = str(profile.config_dir)
    return env


def env_assignments(name: str | None) -> list[str]:
    """`env` arguments (`NAME=value`, `-u NAME`) that give a command the
    environment of the profile; empty for the default one. For processes
    started through tmux, which has its own server environment."""
    profile = get(name)
    if profile is None:
        return []
    unset = [arg for var in OVERRIDING_CREDENTIALS for arg in ("-u", var)]
    return ["env", *unset, f"CLAUDE_CONFIG_DIR={profile.config_dir}"]


def validate_name(name: str) -> str:
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise ProfileError(_("The name may only have letters a–z, digits, - and _ (at most 32), starting with a letter or digit."))
    return name


def add(name: str, directory: str | None = None) -> Profile:
    """Create a profile. The directory defaults to ~/.claude-<name> and is
    created (0700) when missing; signing in is done by the user in it."""
    name = validate_name(name)
    profiles = _read()
    if name in profiles:
        raise ProfileError(_("A profile with this name already exists."))
    path = Path(directory.strip()).expanduser() if directory and directory.strip() else Path.home() / f".claude-{name}"
    if not path.is_absolute():
        raise ProfileError(_("The folder must be an absolute path."))
    path = Path(os.path.normpath(path))
    taken = {Path(os.path.normpath(d)) for d in profiles.values()} | {Path(os.path.normpath(default_config_dir()))}
    if path in taken:
        raise ProfileError(_("This folder already belongs to another profile (or is the default one)."))
    if path.exists() and not path.is_dir():
        raise ProfileError(_("This path is a file, not a folder."))
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ProfileError(_("The folder cannot be created: {reason}").format(reason=exc.strerror or exc)) from exc
    profiles[name] = str(path)
    _write(profiles)
    return Profile(name, path)


def remove(name: str) -> bool:
    """Forget a profile. Its folder (login, history) is left alone; True when
    there was such a profile."""
    profiles = _read()
    if name not in profiles:
        return False
    del profiles[name]
    _write(profiles)
    return True


def has_credentials_file(name: str | None) -> bool | None:
    """Is a login saved in the profile's folder? None when it cannot be told
    (macOS keeps it in the Keychain, not in a file)."""
    if sys.platform == "darwin":
        return None
    return (config_dir(name) / ".credentials.json").is_file()
