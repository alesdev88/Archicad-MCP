"""MCP tools over the GDL pipeline.

The pipeline is otherwise a CLI, which makes it unreachable from any client
whose agent has no shell on the machine running Archicad. These tools move
execution into the server process, which already runs there by necessity
because it talks to Archicad over localhost.

The logic lives in module-level functions taking a Workspace, and `register`
only binds them to FastMCP. That keeps the tests free of a server instance,
and keeps this module importable from `server` without a circular import
(hence tool_meta and guarded arriving as arguments).
"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from pathlib import Path

from archicad_mcp.gdl import config as cfg_mod
from archicad_mcp.gdl import mesh as mesh_mod
from archicad_mcp.gdl.toolchain import ToolchainError
from archicad_mcp.gdl.workspace import Workspace, WorkspaceError

SOURCE_SUFFIXES = (".obj", ".3ds")
TEXTURE_SUFFIXES = (".jpg", ".jpeg", ".png")


def _entry(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                            .isoformat(timespec="seconds"),
    }


def _list_sources(ws: Workspace) -> dict:
    root = ws.require_root()
    sources = [_entry(p) for p in sorted(root.iterdir())
               if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES]
    built = [_entry(p) for p in sorted(root.glob("*.gsm"))]
    tex_dir = ws.textures_dir()
    textures = ([_entry(p) for p in sorted(tex_dir.iterdir())
                 if p.is_file() and p.suffix.lower() in TEXTURE_SUFFIXES]
                if tex_dir.is_dir() else [])
    assets = ws.assets_path()
    configured = sorted(cfg_mod.load_config(assets)) if assets.is_file() else []
    return {
        "workspace": str(root),
        "sources": sources,
        "built": built,
        "textures": textures,
        "configured_objects": configured,
    }


def _inspect_source(ws: Workspace, source: str) -> dict:
    path = ws.resolve(source)
    if not path.is_file():
        raise FileNotFoundError(
            f"No such file in the GDL workspace: {source}. Call "
            "list_gdl_sources to see what is there.")
    mesh = mesh_mod.load(path)
    xs = [v[0] for v in mesh.verts]
    ys = [v[1] for v in mesh.verts]
    zs = [v[2] for v in mesh.verts]
    groups = []
    for mat, faces in sorted(mesh.groups.items(), key=lambda kv: -len(kv[1])):
        vs = {vi for f in faces for vi, _ in f}
        groups.append({
            "material": mat,
            "faces": len(faces),
            "x": [round(min(mesh.verts[v][0] for v in vs), 4),
                  round(max(mesh.verts[v][0] for v in vs), 4)],
            "z": [round(min(mesh.verts[v][2] for v in vs), 4),
                  round(max(mesh.verts[v][2] for v in vs), 4)],
        })
    return {
        "source": source,
        "vertex_count": len(mesh.verts),
        "face_count": mesh.face_count,
        "has_uvs": bool(mesh.uvs),
        "bbox": {"x": [round(min(xs), 4), round(max(xs), 4)],
                 "y": [round(min(ys), 4), round(max(ys), 4)],
                 "z": [round(min(zs), 4), round(max(zs), 4)]},
        "groups": groups,
        "notes": list(mesh.notes),
    }


def _gdl_guarded(guarded):
    """Wrap the server's guard so GDL errors also return an error envelope.

    The alternative was widening the server's shared handled-error tuple, which
    would have changed how every existing tool reports a missing file. Keeping
    it here holds the blast radius to this feature.
    """
    def decorate(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except (ToolchainError, WorkspaceError, FileNotFoundError) as exc:
                return {"error": str(exc)}
        return guarded(wrapper)
    return decorate


def register(mcp, default_port, workspace: Workspace, tool_meta, guarded) -> None:
    ws = workspace
    gdl_guarded = _gdl_guarded(guarded)

    @mcp.tool(description="List what is in the GDL workspace folder: source "
                          "meshes (.obj/.3ds), built .gsm library parts, "
                          "textures, and the objects already described in "
                          "assets.json. Call this first.",
              **tool_meta("List GDL workspace", read_only=True, destructive=False))
    @gdl_guarded
    def list_gdl_sources() -> dict:
        return _list_sources(ws)

    @mcp.tool(description="Parse a source mesh in the GDL workspace and return "
                          "its material groups with face counts, bounding box, "
                          "and parser notes. Read this before writing a config: "
                          "the group names are what a config's 'groups' keys "
                          "match against. Does not need Archicad running.",
              **tool_meta("Inspect GDL source mesh", read_only=True, destructive=False))
    @gdl_guarded
    def inspect_gdl_source(source: str) -> dict:
        return _inspect_source(ws, source)
