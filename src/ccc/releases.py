"""GitHub releases as the source of ccc's versions — the same rules as the
Android app's (AppUpdate.kt in ccc_android; both are tested against
tests/fixtures/release-rules.json — the app keeps a copy — so they must not drift apart):

- a new version is the repository's latest release (GitHub's `releases/latest`:
  public, no token is sent) that is not a draft or prerelease, whose tag is a
  version `1.2.3` (2–4 numbers, a leading `v` is fine) and which has the
  artifact attached — `ccc.tar.gz` for ccc, `ccc.apk` for the app (in its own repository, ccc_android);
- versions are compared as numbers; a version that is not a number (a
  checkout or build made by hand, `0.0.0-dev`) never asks for an update."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = "Icenux/ccc_TUI"
API = "https://api.github.com"
ASSET = "ccc.tar.gz"
TIMEOUT = 20


@dataclass(frozen=True)
class Release:
    version: str  # the tag without a `v`
    url: str  # the artifact
    notes: str
    tag: str = ""  # as published


def parse_version(text: str) -> list[int] | None:
    """`1.2.3` / `v1.2` as numbers; None for anything else (`0.0.0-dev`)."""
    parts = text.strip().removeprefix("v").split(".")
    if not 2 <= len(parts) <= 4 or not all(p.isascii() and p.isdigit() for p in parts):
        return None
    return [int(p) for p in parts]


def is_newer(latest: str, installed: str) -> bool:
    """`latest` is newer than `installed`; False when either is not a version."""
    new, old = parse_version(latest), parse_version(installed)
    if new is None or old is None:
        return False
    size = max(len(new), len(old))
    return new + [0] * (size - len(new)) > old + [0] * (size - len(old))


def parse_release(data: object, asset: str = ASSET) -> Release | None:
    """GitHub's release as a Release; None for a draft, a prerelease, a tag
    that is not a version, a release without `asset`, or garbage."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None:
        return None
    for item in data.get("assets") or []:
        if isinstance(item, dict) and item.get("name") == asset and isinstance(item.get("browser_download_url"), str):
            return Release(tag.removeprefix("v"), item["browser_download_url"], (data.get("body") or "").strip(), tag)
    return None


def fetch(repo: str = REPO, base: str = API) -> object:
    """GitHub's `releases/latest` as it came (JSON); None when it isn't JSON.
    No release yet (404) is None; any other failure raises OSError."""
    request = urllib.request.Request(f"{base}/repos/{repo}/releases/latest", headers={
        "Accept": "application/vnd.github+json", "User-Agent": "ccc"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except ValueError:
        return None


def latest(repo: str = REPO, base: str = API, asset: str = ASSET) -> Release | None:
    """The newest release with `asset`; None when there is none."""
    return parse_release(fetch(repo, base), asset)


# Where an artifact may come from: GitHub itself, over https — the first URL and every redirect.
TRUSTED_HOSTS = ("github.com", "githubusercontent.com")


def trusted(url: str) -> bool:
    parts = urllib.parse.urlparse(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in TRUSTED_HOSTS)


class _TrustedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not trusted(newurl):
            raise urllib.error.URLError("redirect to an untrusted address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, target: Path, check=trusted) -> None:
    """Fetch `url` to `target` (written whole or not at all). Only from
    GitHub over https (`check`; tests pass their own server)."""
    if not check(url):
        raise urllib.error.URLError("untrusted download address")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "ccc"})
    opener = urllib.request.build_opener(_TrustedRedirects if check is trusted else urllib.request.HTTPRedirectHandler)
    try:
        with opener.open(request, timeout=TIMEOUT * 3) as response, part.open("wb") as out:
            while chunk := response.read(64 * 1024):
                out.write(chunk)
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
