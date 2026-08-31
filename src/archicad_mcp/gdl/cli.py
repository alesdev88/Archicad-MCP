"""Command-line interface for the GDL library-part pipeline.

    archicad-gdl build SOURCE --name "My Chair" [--config assets.json]
                 [--out DIR] [--no-decimate] [--no-validate] [--keep-hsf]
    archicad-gdl deploy GSM [--port N] [--place X Y] [--preview OUT.png]
    archicad-gdl inspect SOURCE

build: mesh -> optional Blender decimation (when the object's config has
"decimate" targets) -> HSF -> LP_XMLConverter compile -> script validation.
deploy: embed into the open project's embedded library, reload libraries,
optionally place an instance and render its preview PNG (the render is the
only automated check that catches silently dropped 3D bodies).
inspect: print a mesh summary (groups, face counts, bounding box).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from archicad_mcp.gdl import config as cfg_mod
from archicad_mcp.gdl import deploy as deploy_mod
from archicad_mcp.gdl import generate, mesh as mesh_mod, toolchain


def _cmd_build(args) -> int:
    objects = cfg_mod.load_config(args.config) if args.config else {}
    cfg = cfg_mod.find_object(objects, args.name)
    mesh = mesh_mod.load(args.source)
    for note in mesh.notes:
        print(f"  ({note})")
    if cfg.decimate and not args.no_decimate:
        known = len(mesh.notes)
        mesh = toolchain.decimate(mesh, cfg.decimate)
        for note in mesh.notes[known:]:
            print(f"  ({note})")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    hsf_dir = out_dir / args.name
    if hsf_dir.exists():
        shutil.rmtree(hsf_dir)
    result = generate.build_hsf(mesh, cfg, args.name, hsf_dir)
    print(f"HSF: {result.hsf_dir}")
    print(f"A={result.a:.4f} B={result.b:.4f} H={result.h:.4f} m  GUID={result.guid}")
    for line in result.groups:
        print(f"  {line}")

    gsm = toolchain.compile_hsf(hsf_dir, out_dir / f"{args.name}.gsm")
    print(f"GSM: {gsm} ({gsm.stat().st_size // 1024} KB)")

    if not args.no_validate:
        issues = toolchain.validate_gsm(gsm)
        if issues:
            print("validation findings:")
            for ln in issues:
                print(f"  {ln.strip()}")
        else:
            print("validation: scripts interpret cleanly "
                  "(render a preview after deploy for the geometry check)")
    if not args.keep_hsf:
        shutil.rmtree(hsf_dir)
    return 0


def _cmd_deploy(args) -> int:
    from archicad_mcp.connection import get_connection
    conn = get_connection(args.port)
    gsm = Path(args.gsm)
    deploy_mod.embed_gsm(conn, gsm)
    deploy_mod.reload_libraries(conn)
    print(f"embedded {gsm.name} and reloaded libraries (port {conn.port})")
    if args.place is not None:
        x, y = args.place
        guid = deploy_mod.place_object(conn, gsm.stem, x=x, y=y)
        print(f"placed '{gsm.stem}' at ({x}, {y}): element {guid}")
        if args.preview:
            path = deploy_mod.preview_png(conn, guid, args.preview)
            print(f"preview: {path} (look at it: this is the geometry check)")
    elif args.preview:
        print("note: --preview needs --place (it renders the placed element)")
    return 0


def _cmd_inspect(args) -> int:
    mesh = mesh_mod.load(args.source)
    for note in mesh.notes:
        print(f"  ({note})")
    xs = [v[0] for v in mesh.verts]
    ys = [v[1] for v in mesh.verts]
    zs = [v[2] for v in mesh.verts]
    print(f"{len(mesh.verts)} verts, {mesh.face_count} faces, "
          f"{len(mesh.groups)} material groups")
    print(f"bbox [m]: x {min(xs):.3f}..{max(xs):.3f}  "
          f"y {min(ys):.3f}..{max(ys):.3f}  z {min(zs):.3f}..{max(zs):.3f}")
    for mat, faces in sorted(mesh.groups.items(), key=lambda kv: -len(kv[1])):
        vs = {vi for f in faces for vi, _ in f}
        gx = [mesh.verts[v][0] for v in vs]
        gz = [mesh.verts[v][2] for v in vs]
        print(f"  {mat}: {len(faces)} faces, x {min(gx):.3f}..{max(gx):.3f}, "
              f"z {min(gz):.3f}..{max(gz):.3f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="archicad-gdl", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="mesh -> HSF -> .gsm")
    p.add_argument("source", help="input .obj or .3ds file")
    p.add_argument("--name", required=True, help="library part name")
    p.add_argument("--config", help="assets JSON (textures, variants, groups)")
    p.add_argument("--out", default="build", help="output directory (default: build)")
    p.add_argument("--no-decimate", action="store_true")
    p.add_argument("--no-validate", action="store_true")
    p.add_argument("--keep-hsf", action="store_true",
                   help="keep the intermediate HSF folder next to the .gsm")
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("deploy", help="embed .gsm into the open project")
    p.add_argument("gsm", help="compiled .gsm file")
    p.add_argument("--port", type=int, default=None,
                   help="Archicad port (default: auto-discover)")
    p.add_argument("--place", nargs=2, type=float, metavar=("X", "Y"),
                   help="also place an instance at model coordinates")
    p.add_argument("--preview", help="render the placed element to this PNG")
    p.set_defaults(func=_cmd_deploy)

    p = sub.add_parser("inspect", help="print a mesh summary")
    p.add_argument("source", help="input .obj or .3ds file")
    p.set_defaults(func=_cmd_inspect)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except toolchain.ToolchainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
