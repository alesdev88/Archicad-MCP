"""Refresh bundled Tapir command definitions from the Tapir repository.

Run whenever a new Tapir add-on is released:

    uv run python scripts/sync_tapir_defs.py

Pulls the definitions from the latest published *release tag* (not the moving
`main` branch) so the bundled catalog corresponds to a real add-on version, and
records that version in `tapir_version.json` next to the definitions. The
dashboard reads that file to report the bundled version and to compare against
GitHub's latest release. If the release lookup fails (offline / rate-limited),
it falls back to `main`.

Tapir regenerates the published definitions only at a release, so `main` lags
the commands merged since. To work against those before they are released, sync
from a local checkout instead; its own generator rebuilds the definitions from
the C++ sources:

    uv run python scripts/sync_tapir_defs.py --from-checkout ~/Developer/tapir-main

That records source "main-branch" with the checkout's version and commit, and
check_release_version.py holds any release until the definitions come from the
pinned Tapir release again.
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

import httpx

REPO = "ENZYME-APD/tapir-archicad-automation"
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/docs/archicad-addon/{name}"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
FILES = ["command_definitions.js", "common_schema_definitions.js"]
TARGET = Path(__file__).resolve().parent.parent / "src/archicad_mcp/gateway/definitions"


def _latest_release_tag() -> str | None:
    """The tag_name of the latest Tapir release, or None if it can't be reached."""
    try:
        r = httpx.get(LATEST, headers={"Accept": "application/vnd.github+json"},
                      follow_redirects=True, timeout=30)
        r.raise_for_status()
        tag = str(r.json().get("tag_name", "")).strip()
        return tag or None
    except Exception as exc:
        print(f"  could not read latest release ({exc}); falling back to main")
        return None


def checkout_version(checkout: Path) -> str:
    """ADDON_VERSION of a Tapir checkout, as its AddOnVersion.hpp states it."""
    text = (checkout / "archicad-addon" / "Sources" / "AddOnVersion.hpp").read_text(encoding="utf-8")
    match = re.search(r'#define\s+ADDON_VERSION\s+"([^"]+)"', text)
    if match is None:
        raise SystemExit(f"no ADDON_VERSION in {checkout}")
    return match.group(1)


def sync_from_checkout(checkout: Path) -> None:
    """Generate the definitions in a Tapir checkout and copy them in."""
    subprocess.run([sys.executable, "-I", str(checkout / "tools" / "generate_addon_docs.py")],
                   cwd=checkout, check=True)
    commit = subprocess.run(["git", "-C", str(checkout), "rev-parse", "--short=8", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        text = (checkout / "docs" / "archicad-addon" / name).read_text(encoding="utf-8")
        (TARGET / name).write_text(text, encoding="utf-8")
        print(f"synced {name} ({len(text)} bytes) from {checkout} at {commit}")
    version = checkout_version(checkout)
    (TARGET / "tapir_version.json").write_text(
        json.dumps({"version": version, "synced": datetime.date.today().isoformat(),
                    "source": "main-branch", "commit": commit}, indent=2) + "\n",
        encoding="utf-8")
    print(f"recorded tapir_version.json: version={version} (unreleased, main-branch {commit})")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--from-checkout":
        sync_from_checkout(Path(sys.argv[2]).expanduser().resolve())
        return
    TARGET.mkdir(parents=True, exist_ok=True)
    tag = _latest_release_tag()
    ref = tag or "main"
    for name in FILES:
        response = httpx.get(RAW.format(repo=REPO, ref=ref, name=name),
                             follow_redirects=True, timeout=30)
        response.raise_for_status()
        (TARGET / name).write_text(response.text, encoding="utf-8")
        print(f"synced {name} ({len(response.text)} bytes) from {ref}")

    version = (tag or "").lstrip("vV") or None
    synced = datetime.date.today().isoformat()
    (TARGET / "tapir_version.json").write_text(
        json.dumps({"version": version, "synced": synced,
                    "source": "release" if tag else "main-branch"}, indent=2) + "\n",
        encoding="utf-8")
    print(f"recorded tapir_version.json: version={version or 'unknown'} synced={synced}")


if __name__ == "__main__":
    main()
