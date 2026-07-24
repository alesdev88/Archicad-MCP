"""Refresh bundled Tapir command definitions from the Tapir repository.

Run whenever a new Tapir add-on is released:

    uv run python scripts/sync_tapir_defs.py

Pulls the definitions from the latest published *release tag* (not the moving
`main` branch) so the bundled catalog corresponds to a real add-on version, and
records that version in `tapir_version.json` next to the definitions. The
dashboard reads that file to report the bundled version and to compare against
GitHub's latest release. If the release lookup fails (offline / rate-limited),
it falls back to `main`.
"""
from __future__ import annotations

import datetime
import json
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


def main() -> None:
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
