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
import shlex
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ccc.i18n import N_, _

DEFAULT = ""  # the profile of conversations that name none

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Credentials from the environment outrank a signed-in profile in Claude
# Code's order (an API key, a bearer token, a long-lived OAuth token), so a
# named profile would silently keep using the same account. They are
# removed from the environment of a named profile's processes.
OVERRIDING_CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")


# Shown wherever an agent that is not Claude Code can be added or chosen.
API_LIMITS_NOTICE = N_(
    "Note: for agents other than Claude Code ccc does not know the cost, token use or transcript (shown as zero), and the agent keeps its own history."
)


class ProfileError(ValueError):
    """The user's input is not a usable profile (message is shown as is)."""


KIND_CLAUDE = "claude"    # an account of Claude Code (the default kind)
KIND_COMMAND = "command"  # any other agent started by a command (#226)

# Placeholders of a command profile's templates. The key is deliberately not
# one: an argument list is visible to every process of the machine, so the
# key only ever travels in the environment (see `env_template`).
PLACEHOLDERS = ("{prompt}", "{model}", "{url}", "{session_id}", "{project}")


@dataclass(frozen=True)
class Profile:
    name: str
    config_dir: Path
    kind: str = KIND_CLAUDE
    # The rest is used by KIND_COMMAND only.
    run: str = ""          # command line of one headless run
    resume: str = ""       # ... when it continues a conversation (default: run)
    interactive: str = ""  # command line of the interactive session
    url: str = ""
    model: str = ""
    env: tuple[tuple[str, str], ...] = ()  # (NAME, template with {url} {key} {model})
    key: str = field(default="", repr=False)

    @property
    def is_command(self) -> bool:
        return self.kind == KIND_COMMAND


def _registry_path() -> Path:
    override = os.environ.get("CCC_PROFILES_FILE")
    return Path(override) if override else Path.home() / ".local" / "share" / "ccc" / "profiles.json"


def default_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _read() -> dict[str, dict]:
    """name -> entry. An entry is {"dir": ...} plus, for a command profile,
    its settings; the first versions stored the bare directory string. A
    missing, unreadable or malformed file is an empty list: profiles are
    optional, never a reason for ccc to fail."""
    try:
        data = json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    listed = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(listed, dict):
        return {}
    entries: dict[str, dict] = {}
    for name, entry in listed.items():
        if not (isinstance(name, str) and NAME_RE.match(name)):
            continue
        if isinstance(entry, str):
            entry = {"dir": entry}
        if isinstance(entry, dict) and isinstance(entry.get("dir"), str) and entry["dir"]:
            entries[name] = entry
    return entries


def _profile(name: str, entry: dict) -> Profile:
    def text(field_name: str) -> str:
        value = entry.get(field_name)
        return value if isinstance(value, str) else ""

    env = entry.get("env")
    pairs = tuple((k, v) for k, v in env.items() if isinstance(k, str) and isinstance(v, str)) if isinstance(env, dict) else ()
    kind = KIND_COMMAND if entry.get("kind") == KIND_COMMAND else KIND_CLAUDE
    return Profile(
        name, Path(entry["dir"]), kind, text("run"), text("resume"), text("interactive"),
        text("url"), text("model"), pairs, text("key"),
    )


def _write(profiles: dict[str, dict]) -> None:
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
    return [_profile(name, entry) for name, entry in sorted(_read().items())]


def get(name: str | None) -> Profile | None:
    """The named profile; None for the default profile and for a name that
    is no longer listed (the conversation then runs as the default one
    rather than failing)."""
    if not name:
        return None
    entry = _read().get(name)
    return _profile(name, entry) if entry else None


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


def _env_values(profile: Profile) -> dict[str, str]:
    """What a command profile's `env` templates are filled with."""
    return {"{url}": profile.url, "{key}": profile.key, "{model}": profile.model}


def fill(template: str, values: dict[str, str]) -> str:
    """Replace the known {placeholders} and nothing else, so braces that
    belong to the user's command (JSON, shell) stay as they are."""
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    return template


def command_env(profile: Profile) -> dict[str, str]:
    """The variables a command profile adds to its agent's environment."""
    values = _env_values(profile)
    return {name: fill(template, values) for name, template in profile.env}


def env_for(name: str | None) -> dict[str, str] | None:
    """The environment to start `claude` with; None = inherit ccc's own
    (the default profile, exactly as before profiles existed)."""
    profile = get(name)
    if profile is None:
        return None
    if profile.is_command:
        return {**os.environ, **command_env(profile)}
    env = {k: v for k, v in os.environ.items() if k not in OVERRIDING_CREDENTIALS}
    env["CLAUDE_CONFIG_DIR"] = str(profile.config_dir)
    return env


def _env_file(profile: Profile) -> Path:
    """The variables of a command profile as a shell file (0600) for tmux:
    its command line is visible to every process, so the key must not be in
    it. Rewritten on every start, so it always matches the profile."""
    path = _registry_path().parent / f"env-{profile.name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"export {name}={shlex.quote(value)}\n" for name, value in command_env(profile).items())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o600)
    return path


def env_assignments(name: str | None) -> list[str]:
    """Words put in front of a command so it runs in the environment of the
    profile; empty for the default one. For processes started through tmux,
    which has its own server environment."""
    profile = get(name)
    if profile is None:
        return []
    if profile.is_command:
        return ["sh", "-c", 'set -a; . "$0"; set +a; exec "$@"', str(_env_file(profile))]
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
    taken = {Path(os.path.normpath(e["dir"])) for e in profiles.values()} | {Path(os.path.normpath(default_config_dir()))}
    if path in taken:
        raise ProfileError(_("This folder already belongs to another profile (or is the default one)."))
    if path.exists() and not path.is_dir():
        raise ProfileError(_("This path is a file, not a folder."))
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ProfileError(_("The folder cannot be created: {reason}").format(reason=exc.strerror or exc)) from exc
    profiles[name] = {"dir": str(path)}
    _write(profiles)
    return Profile(name, path)


def add_command(
    name: str, *, run: str, interactive: str = "", resume: str = "", url: str = "", model: str = "",
    key: str = "", env: dict[str, str] | None = None,
) -> Profile:
    """Create a profile that runs any other agent by a command line (#226):
    ccc then needs neither Claude Code nor an Anthropic account. `run` (and
    `interactive`, `resume`) are command lines with the placeholders in
    PLACEHOLDERS; `env` maps variable names to values that may use {url},
    {key} and {model}."""
    name = validate_name(name)
    profiles = _read()
    if name in profiles:
        raise ProfileError(_("A profile with this name already exists."))
    for text in (run, interactive, resume):
        try:
            shlex.split(text)
        except ValueError as exc:
            raise ProfileError(_("The command is not valid: {reason}").format(reason=exc)) from exc
    if not shlex.split(run):
        raise ProfileError(_("The command to run is required."))
    if "{key}" in run + interactive + resume:
        raise ProfileError(_("{key} may only be used in the environment, not in a command line."))
    path = _registry_path().parent / "agents" / name
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ProfileError(_("The folder cannot be created: {reason}").format(reason=exc.strerror or exc)) from exc
    profiles[name] = {
        "dir": str(path), "kind": KIND_COMMAND, "run": run.strip(), "resume": resume.strip(),
        "interactive": interactive.strip(), "url": url.strip(), "model": model.strip(),
        "env": dict(env or {}), "key": key,
    }
    _write(profiles)
    return _profile(name, profiles[name])


def remove(name: str) -> bool:
    """Forget a profile. Its folder (login, history) is left alone; True when
    there was such a profile."""
    profiles = _read()
    if name not in profiles:
        return False
    del profiles[name]
    _write(profiles)
    try:  # holds the key of a command profile
        (_registry_path().parent / f"env-{name}").unlink()
    except OSError:
        pass
    return True


def has_credentials_file(name: str | None) -> bool | None:
    """Is a login saved in the profile's folder? None when it cannot be told
    (macOS keeps it in the Keychain, not in a file)."""
    if sys.platform == "darwin":
        return None
    return (config_dir(name) / ".credentials.json").is_file()
