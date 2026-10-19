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


# A Mac bundle unpacks to a few MB; anything near this is not Tapir.
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
_CHUNK = 64 * 1024


def _user_agent() -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return f"archicad-mcp/{version('archicad-mcp-server')}"
    except PackageNotFoundError:
        return "archicad-mcp"


def make_client() -> httpx.Client:
    return httpx.Client(timeout=60.0, follow_redirects=True,
                        headers={"User-Agent": _user_agent()})


def download(asset: Asset, dest_dir: Path, client: httpx.Client) -> Path:
    """Stream the asset into dest_dir; refuse more bytes than it should have.

    The size and hash are checked by verify(), not here, so that a dry run
    can report a mismatch with both hashes instead of a bare failure.
    """
    target = dest_dir / asset.name
    received = 0
    try:
        with client.stream("GET", asset.url, follow_redirects=True) as response:
            if response.status_code != 200:
                raise InstallError(
                    f"Downloading {asset.name} failed: GitHub answered "
                    f"{response.status_code} for {asset.url}.")
            with target.open("wb") as out:
                for chunk in response.iter_bytes(_CHUNK):
                    received += len(chunk)
                    if received > asset.size:
                        raise InstallError(
                            f"Downloading {asset.name} failed: more than the "
                            f"expected {asset.size} bytes arrived, so it is not "
                            "the release file (a proxy page, or a changed release).")
                    out.write(chunk)
    except httpx.HTTPError as exc:
        raise InstallError(f"Downloading {asset.name} failed: {exc}") from exc
    return target


def sha256_of(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, asset: Asset) -> None:
    size = path.stat().st_size
    actual = sha256_of(path)
    if size != asset.size or actual != asset.sha256:
        raise InstallError(
            f"{asset.name} does not match the release: expected {asset.size} bytes "
            f"with sha256 {asset.sha256}, got {size} bytes with sha256 {actual}. "
            "A proxy may have changed the download, or the release file changed. "
            "Nothing was installed.")


def check_mac_zip(path: Path, archicad_version: int) -> str:
    """The bundle folder name, after checking every member stays inside it."""
    import zipfile
    from pathlib import PurePosixPath

    expected = f"TapirAddOn_AC{archicad_version}_Mac.bundle"
    try:
        with zipfile.ZipFile(path) as zf:
            members = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise InstallError(f"{path.name} is not a zip file.") from exc
    total = 0
    for info in members:
        name = info.filename
        parts = PurePosixPath(name).parts
        if name.startswith(("/", "\\")) or ".." in parts or not parts \
                or parts[0] != expected or "\\" in name:
            raise InstallError(
                f"{path.name} holds '{name}', which is not inside {expected}/. "
                "Refusing to unpack it.")
        total += info.file_size
    if not members:
        raise InstallError(f"{path.name} is empty.")
    if total > MAX_UNPACKED_BYTES:
        raise InstallError(f"{path.name} unpacks to {total} bytes, far more than Tapir.")
    return expected
