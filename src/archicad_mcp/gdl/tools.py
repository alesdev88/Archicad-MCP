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
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from archicad_mcp.core import mutate as _mutate
from archicad_mcp.gdl import config as cfg_mod
from archicad_mcp.gdl import deploy as deploy_mod
from archicad_mcp.gdl import generate
from archicad_mcp.gdl import mesh as mesh_mod
from archicad_mcp.gdl import toolchain
from archicad_mcp.gdl.toolchain import ToolchainError
from archicad_mcp.gdl.workspace import Workspace, WorkspaceError
from archicad_mcp.connection import get_connection

SOURCE_SUFFIXES = (".obj", ".3ds")
TEXTURE_SUFFIXES = (".jpg", ".jpeg", ".png")


class MeshParseError(ValueError):
    """Raised when a source mesh file cannot be parsed."""
    pass


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


def _config_for(ws: Workspace, name: str, config: dict | None):
    """The ObjectConfig to build with, from the argument or from assets.json.

    All paths (inline or saved) are validated through ws.resolve() to enforce
    workspace containment. Absolute paths and ".." traversals are rejected.
    """
    if config is not None:
        objects = cfg_mod.parse_objects({"objects": {name: config}}, ws.root,
                                        resolve=ws.resolve)
        return objects[name], config
    assets = ws.assets_path()
    if assets.is_file():
        raw = json.loads(assets.read_text())
        objects = cfg_mod.parse_objects(raw, assets.parent, resolve=ws.resolve)
    else:
        objects = {}
    return cfg_mod.find_object(objects, name), None


def _build_object(ws: Workspace, source: str, name: str,
                  config: dict | None = None, decimate: bool = True,
                  validate: bool = True, save_config: bool = True) -> dict:
    ws.require_root()
    src = ws.resolve(source)
    if not src.is_file():
        raise FileNotFoundError(
            f"No such file in the GDL workspace: {source}. Call "
            "list_gdl_sources to see what is there.")
    gsm_path = ws.resolve(f"{name}.gsm")
    hsf_dir = ws.resolve(name)

    cfg, raw_spec = _config_for(ws, name, config)
    try:
        mesh = mesh_mod.load(src)
    except ValueError as exc:
        raise MeshParseError(
            f"Failed to parse source mesh {source}: {exc}") from exc
    if cfg.decimate and decimate:
        mesh = toolchain.decimate(mesh, cfg.decimate)

    if hsf_dir.exists():
        shutil.rmtree(hsf_dir)
    result = generate.build_hsf(mesh, cfg, name, hsf_dir,
                                textures_dir=ws.textures_dir())
    try:
        gsm = toolchain.compile_hsf(hsf_dir, gsm_path)
    finally:
        shutil.rmtree(hsf_dir, ignore_errors=True)

    findings = (toolchain.validate_gsm(gsm, extra_libs=[ws.textures_dir()])
                if validate else [])
    if save_config and raw_spec is not None:
        try:
            cfg_mod.save_object_config(ws.assets_path(), name, raw_spec)
        except (json.JSONDecodeError, OSError) as exc:
            return {
                "gsm": gsm.name,
                "bytes": gsm.stat().st_size,
                "guid": result.guid,
                "size_m": {"a": round(result.a, 4), "b": round(result.b, 4),
                           "h": round(result.h, 4)},
                "groups": list(result.groups),
                "textures": [p.name for p in result.textures],
                "notes": list(result.notes),
                "validation": [ln.strip() for ln in findings],
                "config_saved": False,
                "config_save_error": f"Could not save config to assets.json: {exc}",
            }

    return {
        "gsm": gsm.name,
        "bytes": gsm.stat().st_size,
        "guid": result.guid,
        "size_m": {"a": round(result.a, 4), "b": round(result.b, 4),
                   "h": round(result.h, 4)},
        "groups": list(result.groups),
        "textures": [p.name for p in result.textures],
        "notes": list(result.notes),
        "validation": [ln.strip() for ln in findings],
        "config_saved": bool(save_config and raw_spec is not None),
    }


def _deploy_object(ws: Workspace, conn, name: str, place: tuple[float, float],
                   keep: bool, embed: bool) -> tuple[dict, bytes]:
    """Reload, place, render, and unless kept, delete again.

    The render is the only automated check that catches a body Archicad
    silently dropped, so it is not optional. But an iteration loop that leaves
    every attempt standing at the origin is unusable, so the default is a
    transient probe: the element created here is deleted here, leaving the
    project net-zero.
    """
    ws.require_root()
    gsm = ws.resolve(f"{name}.gsm")
    if not gsm.is_file():
        raise FileNotFoundError(
            f"No such library part in the GDL workspace: {name}.gsm. Build it "
            "first with build_gdl_object.")

    embedded = None
    if embed:
        tex_dir = ws.textures_dir()
        textures = ([p for p in sorted(tex_dir.iterdir())
                     if p.suffix.lower() in TEXTURE_SUFFIXES]
                    if tex_dir.is_dir() else [])
        gsm_result = deploy_mod.embed_gsm(conn, gsm)
        gsm_results = gsm_result.get("executionResults") or []
        # Unlike a texture (whose filename carries a content hash, so a
        # failure there means the identical file is already in place and
        # skipping is correct), <name>.gsm carries no hash. Tapir cannot
        # overwrite an existing embedded file, so a failed embed here means
        # the name is already taken and the render below would silently show
        # the PREVIOUS build under a success payload. Fail loudly instead of
        # reloading, placing and rendering a stale object.
        if not gsm_results or not gsm_results[0].get("success"):
            raise ToolchainError(
                f"Could not embed {gsm.name} into the project's embedded "
                "library: the name is likely already there, and Tapir cannot "
                "overwrite an existing embedded file. Deploy under a fresh "
                "name, or use the linked library folder instead of embed=true.")
        added, skipped = deploy_mod.embed_textures(conn, textures)
        embedded = {"textures_added": added, "textures_skipped": skipped}

    deploy_mod.reload_libraries(conn)
    x, y = place
    guid = deploy_mod.place_object(conn, name, x=x, y=y)
    try:
        png = deploy_mod.preview_image_bytes(conn, guid)
    except BaseException:
        # The render failed after the element was placed. Clean up if we were
        # going to, then let the original error through with its type intact:
        # an APIErrorBase carries the server's modal-dialog hint, a RuntimeError
        # would not.
        if not keep:
            try:
                _mutate.delete_elements(conn, [guid], confirm=True)
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"The preview render failed, and deleting the element it "
                    f"placed also failed. Element {guid} is still in the project "
                    f"and has to be removed by hand."
                ) from cleanup_error
        raise
    if not keep:
        # confirm=True is safe here because this deletes only the element
        # placed a few lines above, never user data. The tool would be
        # unusable if every probe needed a confirmation round trip.
        _mutate.delete_elements(conn, [guid], confirm=True)

    payload = {
        "library_part": name,
        "element_guid": guid,
        "placed_at": {"x": x, "y": y},
        "kept": keep,
        "port": conn.port,
        "note": ("Look at the render. A body Archicad dropped shows up here and "
                 "nowhere else: the offline validator passes it."),
    }
    if embedded is not None:
        payload["embedded"] = embedded
    return payload, png


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
            # WorkspaceError, MeshParseError and json.JSONDecodeError are all
            # ValueError subclasses already, so ValueError alone would cover
            # them; they stay named for documentation value. The ValueError
            # widening itself is what catches a malformed mesh or a malformed
            # assets.json falling straight out of mesh.load()/json.loads()
            # from tools that never wrapped those calls (inspect_gdl_source,
            # list_gdl_sources): before this, those escaped as raw exceptions
            # instead of the {"error": ...} envelope every other tool returns.
            except (ToolchainError, WorkspaceError, FileNotFoundError, MeshParseError,
                    ValueError, json.JSONDecodeError) as exc:
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

    @mcp.tool(description="Build a .gsm library part from a source mesh in the "
                          "GDL workspace. Pass 'config' to describe the object "
                          "(groups, textures, variants, decimate targets); it is "
                          "saved into assets.json on success, so a later rebuild "
                          "only needs the name. Writes <name>.gsm and textures/ "
                          "into the workspace, which is the linked library "
                          "folder. Does not need Archicad running. A clean "
                          "validation does NOT prove the geometry survived: "
                          "deploy and look at the render.",
              **tool_meta("Build GDL library part", read_only=False, destructive=True))
    @gdl_guarded
    def build_gdl_object(source: str, name: str, config: dict | None = None,
                         decimate: bool = True, validate: bool = True,
                         save_config: bool = True) -> dict:
        return _build_object(ws, source, name, config, decimate, validate,
                             save_config)

    @mcp.tool(description="Reload libraries, place the built library part, "
                          "render it, and return the image. The render is the "
                          "only check that catches a 3D body Archicad silently "
                          "dropped, so look at it. By default the placed "
                          "instance is deleted again after the render, leaving "
                          "the project unchanged; pass keep=true to actually "
                          "place it. Requires Archicad running with a project "
                          "open, and the workspace folder added once as a "
                          "linked library via Library Manager. Pass embed=true "
                          "to also push the .gsm and its textures into the "
                          "project's embedded library, for when the object must "
                          "travel inside the .pln itself; this is the fallback "
                          "if the linked-library folder does not work in your "
                          "setup. Tapir cannot overwrite an existing embedded "
                          "file, so each embed=true deploy of the same object "
                          "needs a fresh name; a name already embedded fails "
                          "with a clear error rather than silently rendering "
                          "the previous build.",
              **tool_meta("Deploy GDL library part", read_only=False, destructive=True))
    @gdl_guarded
    def deploy_gdl_object(name: str, x: float = 0.0, y: float = 0.0,
                          keep: bool = False, embed: bool = False,
                          port: int | None = None):
        # No return type annotation: Image cannot be serialized into an MCP
        # outputSchema, so omitting the annotation allows FastMCP to skip
        # schema generation and return raw content blocks instead.
        from fastmcp.utilities.types import Image

        conn = get_connection(port if port is not None else default_port)
        payload, png = _deploy_object(ws, conn, name, (x, y), keep, embed)
        return [payload, Image(data=png, format="png")]
