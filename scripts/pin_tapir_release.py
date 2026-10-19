"""Pin a Tapir release: write its add-on assets' sizes and sha256 hashes.

    uv run python scripts/pin_tapir_release.py 1.7.1

Writes src/archicad_mcp/tapir_install/tapir_release.json from the GitHub
release API, which publishes a sha256 digest for every asset. Only the
TapirAddOn_* assets are kept: the installer executables are not ours to run.
Review the diff before committing; a bump is a deliberate, tested change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

REPO = "ENZYME-APD/tapir-archicad-automation"
OUT = (Path(__file__).resolve().parents[1]
       / "src" / "archicad_mcp" / "tapir_install" / "tapir_release.json")


def pinned_data(release: dict) -> dict:
    assets = {}
    for asset in release.get("assets", []):
        name = asset["name"]
        if not name.startswith("TapirAddOn_"):
            continue
        digest = asset.get("digest") or ""
        if not digest.startswith("sha256:"):
            raise SystemExit(f"{name} has no sha256 digest in the release API")
        assets[name] = {"size": asset["size"], "sha256": digest.removeprefix("sha256:")}
    if not assets:
        raise SystemExit("the release has no TapirAddOn_* assets")
    return {
        "version": release["tag_name"],
        "tag": release["tag_name"],
        "published": release["published_at"][:10],
        "assets": dict(sorted(assets.items())),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    url = f"https://api.github.com/repos/{REPO}/releases/tags/{argv[0]}"
    response = httpx.get(url, headers={"Accept": "application/vnd.github+json"},
                         timeout=30, follow_redirects=True)
    response.raise_for_status()
    data = pinned_data(response.json())
    OUT.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {OUT} ({data['version']}, {len(data['assets'])} assets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
