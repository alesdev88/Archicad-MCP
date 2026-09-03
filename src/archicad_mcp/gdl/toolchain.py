"""External tools for the GDL pipeline: LP_XMLConverter and headless Blender.

LP_XMLConverter ships inside every Archicad bundle. Blender is optional and
only needed for mesh decimation; it always runs as a separate background
process, never touching an interactively open Blender session.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from archicad_mcp.gdl.mesh import Mesh, parse_obj


class ToolchainError(RuntimeError):
    pass


# (glob pattern under an install root, path from the match to the binary)
_LP_LAYOUT = {
    "win32": ("Archicad *", "LP_XMLConverter.exe"),
    "darwin": ("Archicad */Archicad *.app",
               "Contents/MacOS/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter"),
}


def _archicad_roots() -> list[Path]:
    """Where Archicad installs live. A seam, so tests can point elsewhere."""
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        return [Path(program_files) / "GRAPHISOFT"]
    return [Path("/Applications/Graphisoft")]


def find_lp_xmlconverter() -> Path:
    """Locate LP_XMLConverter: env override first, then the newest Archicad."""
    env = os.environ.get("LP_XMLCONVERTER")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        raise ToolchainError(f"LP_XMLCONVERTER points to a missing file: {env}")
    pattern, tail = _LP_LAYOUT.get(sys.platform, _LP_LAYOUT["darwin"])
    candidates = []
    for root in _archicad_roots():
        for entry in root.glob(pattern):
            exe = entry / tail
            if exe.is_file():
                m = re.search(r"Archicad (\d+)", entry.name)
                candidates.append((int(m.group(1)) if m else 0, exe))
    if not candidates:
        looked = ", ".join(str(r) for r in _archicad_roots())
        raise ToolchainError(
            f"LP_XMLConverter not found under {looked}. Install Archicad or set "
            "the LP_XMLCONVERTER environment variable to the binary inside the "
            "Archicad installation.")
    return max(candidates)[1]


def compile_hsf(hsf_dir: str | Path, gsm_path: str | Path) -> Path:
    """hsf2libpart: compile an HSF folder into a .gsm library part."""
    lp = find_lp_xmlconverter()
    result = subprocess.run(
        [str(lp), "hsf2libpart", str(hsf_dir), str(gsm_path)],
        capture_output=True, text=True, timeout=300)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or not Path(gsm_path).exists():
        raise ToolchainError(f"hsf2libpart failed:\n{output}")
    return Path(gsm_path)


def validate_gsm(gsm_path: str | Path,
                 extra_libs: list[str | Path] | None = None) -> list[str]:
    """Round-trip the .gsm to XML and interpret its scripts.

    Returns warning/error lines. "Missing ancestor" lines are expected
    outside Archicad (the subtype chain lives in the standard library) and
    are filtered out. NOTE: a clean interpret run does NOT guarantee valid
    3D geometry; Archicad can still drop defective bodies silently. The only
    reliable geometry gate is rendering a preview of the placed element.
    """
    lp = find_lp_xmlconverter()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        (src / Path(gsm_path).name).write_bytes(Path(gsm_path).read_bytes())
        xml = Path(tmp) / "xml"
        out = Path(tmp) / "out"
        r1 = subprocess.run([str(lp), "l2x", str(src), str(xml)],
                            capture_output=True, text=True, timeout=300)
        if r1.returncode != 0:
            raise ToolchainError(f"l2x failed:\n{r1.stdout}{r1.stderr}")
        cmd = [str(lp), "convertlibrary", "-interpret", "-reportlevel", "2",
               str(xml), str(out)]
        cmd += [str(p) for p in (extra_libs or []) if Path(p).exists()]
        r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        lines = (r2.stdout + r2.stderr).splitlines()
        return [ln for ln in lines
                if ("error" in ln.lower() or "warning" in ln.lower())
                and "Missing ancestor" not in ln]


# ------------------------------------------------------------------- Blender

_BLENDER_SCRIPT = """
import bpy, json, sys
argv = sys.argv[sys.argv.index("--") + 1:]
src, dst, targets_json = argv
targets = json.loads(targets_json)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.obj_import(filepath=src)
for obj in list(bpy.data.objects):
    if obj.type != "MESH":
        continue
    faces_before = len(obj.data.polygons)
    target = targets.get(obj.name.split(".")[0])
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new("weld", "WELD")
    mod.merge_threshold = 0.0002
    bpy.ops.object.modifier_apply(modifier=mod.name)
    if target and len(obj.data.polygons) > target:
        mod = obj.modifiers.new("dec", "DECIMATE")
        mod.ratio = target / len(obj.data.polygons)
        bpy.ops.object.modifier_apply(modifier=mod.name)
    print(f"DECIMATED {obj.name}: {faces_before} -> {len(obj.data.polygons)}")
bpy.ops.wm.obj_export(filepath=dst, export_materials=True, export_uv=False,
                      export_normals=False, export_triangulated_mesh=True)
"""


def _blender_roots() -> list[Path]:
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        return [Path(program_files)]
    return [Path("/Applications")]


def find_blender() -> Path | None:
    env = os.environ.get("BLENDER")
    if env and Path(env).is_file():
        return Path(env)
    if sys.platform == "win32":
        pattern, tail = "Blender Foundation/Blender *", "blender.exe"
        candidates = []
        for root in _blender_roots():
            for entry in root.glob(pattern):
                exe = entry / tail
                if exe.is_file():
                    m = re.search(r"Blender (\d+)", entry.name)
                    candidates.append((int(m.group(1)) if m else 0, exe))
        if candidates:
            return max(candidates)[1]
        return None
    else:
        pattern, tail = "Blender.app", "Contents/MacOS/Blender"
        for root in _blender_roots():
            for entry in sorted(root.glob(pattern), reverse=True):
                exe = entry / tail
                if exe.is_file():
                    return exe
        return None


def decimate(mesh: Mesh, targets: dict[str, int]) -> Mesh:
    """Reduce mesh density via a background Blender process.

    `targets` maps material-name substrings to face-count targets; 0 means
    weld only. Do not decimate visible surfaces with gentle curvature (a
    tabletop's wide bevel): collapse produces irregular triangles whose
    smooth shading smears into blotches. Set their target to 0 instead.
    """
    blender = find_blender()
    if blender is None:
        looked = ", ".join(str(r) for r in _blender_roots())
        raise ToolchainError(
            f"Blender not found under {looked}. Install Blender or set the "
            "BLENDER environment variable to override. Decimation needs it.")

    def target_for(mat: str) -> int | None:
        for sub, t in targets.items():
            if sub in mat:
                return t
        return None

    # one OBJ object per material group under a safe alias, mapped back after
    aliases: dict[str, str] = {}
    lines: list[str] = []
    vcount = 0
    for gi, (mat, faces) in enumerate(mesh.groups.items(), 1):
        alias = f"g{gi}"
        aliases[alias] = mat
        lines.append(f"o {alias}")
        lines.append(f"usemtl {alias}")
        used = sorted({vi for f in faces for vi, _ in f})
        local = {vi: i for i, vi in enumerate(used)}
        for vi in used:
            x, y, z = mesh.verts[vi]
            lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
        for f in faces:
            idx = " ".join(str(vcount + local[vi] + 1) for vi, _ in f)
            lines.append(f"f {idx}")
        vcount += len(used)

    by_alias = {alias: target_for(mat) or 0 for alias, mat in aliases.items()}
    with tempfile.TemporaryDirectory() as tmp:
        inter = Path(tmp) / "in.obj"
        outer = Path(tmp) / "out.obj"
        inter.write_text("\n".join(lines) + "\n")
        result = subprocess.run(
            [str(blender), "--background", "--python-expr", _BLENDER_SCRIPT,
             "--", str(inter), str(outer), json.dumps(by_alias)],
            capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not outer.exists():
            raise ToolchainError(
                f"Blender decimation failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
        reduced = parse_obj(outer)
    # restore original material names so group configs keep matching
    reduced.groups = {aliases.get(mat.split(".")[0], mat): faces
                      for mat, faces in reduced.groups.items()}
    stats = [ln for ln in result.stdout.splitlines() if ln.startswith("DECIMATED")]
    reduced.notes = mesh.notes + stats
    return reduced
