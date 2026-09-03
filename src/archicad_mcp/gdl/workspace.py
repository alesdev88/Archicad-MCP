"""Path containment for the MCP-facing GDL tools.

The tools run in the server process, on the machine running Archicad, driven
by an agent that may be sandboxed on another host entirely. So every path a
tool accepts is a name relative to one configured root, and anything that
resolves outside it is refused. Containment lives here rather than in each
tool, because a check repeated per call site is a check that eventually gets
forgotten at one of them.

The root doubles as an Archicad linked library folder: builds write the .gsm
and its textures/ here, and ReloadLibraries picks them up.
"""

from __future__ import annotations

from pathlib import Path


class WorkspaceError(ValueError):
    """A path argument named something outside the workspace, or the root is gone."""


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def resolve(self, name: str) -> Path:
        """Absolute path for a workspace-relative name, or raise.

        `.resolve()` follows symlinks before the containment check, so a link
        inside the workspace pointing out of it is caught rather than followed.
        An absolute `name` lands outside the root and is refused by the same
        check, because `root / "/etc/passwd"` is `/etc/passwd` in pathlib.
        """
        text = str(name).strip()
        if not text:
            raise WorkspaceError("Empty path. Give a name relative to the workspace.")
        resolved = (self.root / text).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise WorkspaceError(
                f"{name!r} resolves outside the GDL workspace ({self.root}). "
                "Tool arguments are names relative to that folder.")
        return resolved

    def require_root(self) -> Path:
        if not self.root.is_dir():
            raise WorkspaceError(
                f"GDL workspace folder does not exist: {self.root}. Set the "
                "GDL workspace folder in the extension settings to a folder "
                "that exists, and add it to Archicad as a linked library.")
        return self.root

    def assets_path(self) -> Path:
        return self.root / "assets.json"

    def textures_dir(self) -> Path:
        return self.root / "textures"
