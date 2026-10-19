"""The Tapir release this server installs, and how its files are fetched and checked.

The pinned release lives in tapir_release.json (written by
scripts/pin_tapir_release.py), with the size and sha256 GitHub publishes for
every add-on asset. A download is untrusted data until it matches both.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

RELEASES_URL = "https://github.com/ENZYME-APD/tapir-archicad-automation/releases"
LATEST_API_URL = ("https://api.github.com/repos/ENZYME-APD/"
                  "tapir-archicad-automation/releases/latest")
_PINNED_FILE = Path(__file__).with_name("tapir_release.json")


class InstallError(Exception):
    """str(exc) is a user-facing, actionable message."""


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    assets: dict[str, Asset]
    pinned: bool


def load_pinned() -> Release:
    data = json.loads(_PINNED_FILE.read_text())
    tag = data["tag"]
    assets = {name: Asset(name, a["size"], a["sha256"],
                          f"{RELEASES_URL}/download/{tag}/{name}")
              for name, a in data["assets"].items()}
    return Release(data["version"], tag, assets, pinned=True)


def platform_key(platform: str | None = None) -> str:
    platform = platform or sys.platform
    if platform == "darwin":
        return "Mac"
    if platform == "win32":
        return "Win"
    raise ValueError(f"Tapir has no build for {platform}; only macOS and Windows")


def asset_name(archicad_version: int, platform: str | None = None) -> str:
    key = platform_key(platform)
    extension = ".zip" if key == "Mac" else ".apx"
    return f"TapirAddOn_AC{archicad_version}_{key}{extension}"


def release_page_url(release: Release) -> str:
    return f"{RELEASES_URL}/tag/{release.tag}"


def fetch_latest(client: httpx.Client) -> Release:
    """The newest release, checked against the digests GitHub publishes.

    Without a digest there is nothing to check a download against, and an
    unchecked file is exactly what the pin exists to avoid, so refuse.
    """
    try:
        response = client.get(LATEST_API_URL,
                              headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise InstallError(
            f"Could not read the latest Tapir release from GitHub ({exc}). "
            "--latest needs the GitHub API; the pinned version still works "
            "without --latest.") from exc
    assets = {}
    for raw in data.get("assets", []):
        name = raw.get("name", "")
        if not name.startswith("TapirAddOn_"):
            continue
        digest = raw.get("digest") or ""
        if not digest.startswith("sha256:"):
            raise InstallError(
                f"GitHub gives no sha256 digest for {name} in release "
                f"{data.get('tag_name', '?')}, so a download could not be checked. "
                "--latest needs GitHub's sha256 digest; the pinned version still "
                "works without --latest.")
        assets[name] = Asset(name, int(raw["size"]), digest.removeprefix("sha256:"),
                             raw["browser_download_url"])
    tag = data.get("tag_name", "?")
    return Release(tag, tag, assets, pinned=False)
