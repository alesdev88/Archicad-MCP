"""Update the Claude Desktop bundle: find the newest release, check it, hand it over.

Desktop updates only the extensions from its own directory, so a bundle installed
from a file stays on its version until someone installs a newer file. This does
the fetching and leaves the installing to Desktop: the checked file is opened as
if double-clicked, and Desktop shows its usual dialog. It never writes into the
extension's own folder, which Desktop manages and which holds the interpreter
this code is running on.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from archicad_mcp.tapir_install.release import (
    Asset,
    InstallError,
    download,
    make_client,
    verify,
)

RELEASES_URL = "https://github.com/alesdev88/Archicad-MCP/releases"
LATEST_API_URL = "https://api.github.com/repos/alesdev88/Archicad-MCP/releases/latest"
# The names scripts/build_bundle.py gives its targets. An Intel Mac has no bundle
# to be running from, so the machine type does not need asking.
_TARGETS = {"darwin": "darwin-arm64", "win32": "win32"}


@dataclass(frozen=True)
class _Latest:
    version: str
    page: str
    asset: Asset


def bundle_version(prefix: Path | None = None) -> str | None:
    """The installed bundle's version, or None when not running from a bundle.

    A bundle runs on its own interpreter at <bundle>/runtime, beside the manifest
    Desktop installed. The folder name matters: a dev checkout also has a
    manifest.json next to its .venv, and that one says nothing about what is
    installed.
    """
    prefix = Path(prefix or sys.prefix)
    manifest = prefix.parent / "manifest.json"
    if prefix.name != "runtime" or not manifest.is_file():
        return None
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


def _version_key(version: str) -> tuple[int, ...]:
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise InstallError(f"Cannot compare version '{version}' with another: it is not "
                           f"x.y.z. Releases are listed at {RELEASES_URL}.")
    return tuple(int(part) for part in version.split("."))


def _fetch_latest(client: httpx.Client, platform: str) -> _Latest:
    try:
        response = client.get(LATEST_API_URL,
                              headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise InstallError(f"Could not read the latest release from GitHub ({exc}). "
                           f"Releases are listed at {RELEASES_URL}.") from exc
    version = data["tag_name"].removeprefix("v")
    _version_key(version)
    page = data["html_url"]
    name = f"archicad-mcp-{version}-{_TARGETS[platform]}.mcpb"
    raw = next((a for a in data["assets"] if a["name"] == name), None)
    if raw is None:
        raise InstallError(f"Release {version} has no {name} yet. "
                           f"Check again later, or see {page}.")
    digest = raw.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise InstallError(f"GitHub gives no sha256 digest for {name}, so a download could "
                           f"not be checked. Install it by hand from {page}.")
    return _Latest(version, page, Asset(name, raw["size"], digest.removeprefix("sha256:"),
                                        raw["browser_download_url"]))


def _clear(folder: Path) -> None:
    for old in folder.glob("*.mcpb"):
        old.unlink(missing_ok=True)


def _fetch_bundle(asset: Asset, folder: Path, client: httpx.Client) -> Path:
    """The checked file in folder, which then holds nothing else of ours."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _clear(folder)
        path = download(asset, folder, client)
        verify(path, asset)
    except InstallError:
        _clear(folder)
        raise
    except OSError as exc:
        raise InstallError(f"Could not save {asset.name} in {folder}: {exc}. "
                           "Nothing was installed.") from exc
    return path


def open_bundle(path: Path, platform: str) -> None:
    """Open the file with its system handler, the same as double-clicking it."""
    if platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.run(["open", str(path)], check=True, capture_output=True)


def run(confirm: bool = False, *, prefix: Path | None = None,
        client: httpx.Client | None = None, platform: str | None = None,
        folder: Path | None = None) -> dict:
    """Report what an update would do; with confirm, fetch it and open Desktop's dialog."""
    installed = bundle_version(prefix)
    if installed is None:
        return {"installed_as_bundle": False,
                "next_step": ("This server was not installed as a Claude Desktop bundle, so "
                              "there is nothing to open. If uv installed it, run: "
                              "uv tool upgrade archicad-mcp-server")}
    platform = platform or sys.platform
    # A fixed folder, so the 60 MB file stays in one place and the next run clears
    # it. It has to outlive this call, because Desktop reads the file after it.
    # The temp folder belongs to the user on both platforms a bundle runs on.
    folder = folder or Path(tempfile.gettempdir()) / "archicad-mcp-update"
    own_client = client is None
    client = client or make_client()
    try:
        latest = _fetch_latest(client, platform)
        result = {"installed_as_bundle": True, "installed_version": installed,
                  "latest_version": latest.version, "release_page": latest.page,
                  "dry_run": not confirm,
                  "update_available": _version_key(latest.version) > _version_key(installed)}
        if not result["update_available"]:
            return {**result, "next_step": "Nothing to update."}
        result |= {"bundle_file": latest.asset.name,
                   "size_mb": round(latest.asset.size / 1_000_000, 1)}
        if not confirm:
            return {**result, "next_step": (
                "Call again with confirm=true to download it, check its sha256 and open "
                "Claude Desktop's install dialog.")}
        path = _fetch_bundle(latest.asset, folder, client)
    finally:
        if own_client:
            client.close()
    result |= {"sha256_checked": True, "downloaded_to": str(path)}
    try:
        open_bundle(path, platform)
    except (OSError, subprocess.SubprocessError) as exc:
        return {**result, "opened": False, "next_step": (
            f"The file is downloaded and checked, but opening it failed ({exc}). Drag {path} "
            "into Claude Desktop's Settings > Extensions to install it.")}
    return {**result, "opened": True, "next_step": (
        "Claude Desktop should now show its install dialog. Nothing is installed until "
        "you accept it there. Afterwards, check that the Office rules folder is still set.")}
