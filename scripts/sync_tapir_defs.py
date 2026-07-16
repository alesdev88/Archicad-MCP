"""Refresh bundled Tapir command definitions from the Tapir repository.

Run whenever the Tapir add-on is updated:  uv run python scripts/sync_tapir_defs.py
"""
from pathlib import Path

import httpx

BASE = ("https://raw.githubusercontent.com/ENZYME-APD/"
        "tapir-archicad-automation/main/docs/archicad-addon")
FILES = ["command_definitions.js", "common_schema_definitions.js"]
TARGET = Path(__file__).resolve().parent.parent / "src/archicad_mcp/gateway/definitions"


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        response = httpx.get(f"{BASE}/{name}", follow_redirects=True, timeout=30)
        response.raise_for_status()
        (TARGET / name).write_text(response.text, encoding="utf-8")
        print(f"synced {name} ({len(response.text)} bytes)")


if __name__ == "__main__":
    main()
