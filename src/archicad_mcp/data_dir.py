"""Where this MCP keeps what it must remember between sessions."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_root(platform: str | None = None) -> Path:
    """The per-user folder for the MCP's own files (Tapir backups, learned parts)."""
    platform = platform or sys.platform
    if platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path.home() / ".local" / "share"
    return base / "archicad-mcp"
