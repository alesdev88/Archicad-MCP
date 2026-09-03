"""HSF (Hierarchical Symbol Format) generation from a Mesh + ObjectConfig.

The output folder compiles to a .gsm with LP_XMLConverter hsf2libpart (see
toolchain.py). Hard-won rules baked in here:

- TEVE carries explicit UVs; never mix VERT and TEVE in one body (mixing
  silently disables all UVs). Groups without UVs use VERT and get automatic
  texture wrapping.
- Textures ship as SEPARATE image files in the library (not embedded in the
  .gsm as GDLPict sections): DEFINE TEXTURE references them by file name.
  Rationale: Archicad itself can read pictures embedded in the .gsm (by
  numeric GDLPict index), but external render engines (Enscape, Twinmotion)
  receive the model through the add-on API and need each texture as a real
  image file in a loaded library; gsm-embedded pictures render flat there.
  File names carry a content hash so identical textures dedupe across
  objects and an already-embedded file can be skipped safely on redeploy.
- A GDL EDGE belongs to at most 2 polygons. Non-manifold junctions are split
  into paired edge instances or Archicad silently drops the whole body while
  LP_XMLConverter's interpreter still passes it.
- Edge status vocabulary: 0 visible+sharp, 1 invisible+sharp, 3
  invisible+smooth. Status 1 is used between faces of very different sizes so
  a curved rim cannot smear shading gradients across big flat faces.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from archicad_mcp.gdl.config import ObjectConfig
from archicad_mcp.gdl.mesh import Face, Mesh, face_area, face_normal, triangulate

SOFT_EDGE_DEG = 30.0    # dihedral below which an edge is smoothed + hidden
AREA_RATIO_HARD = 12.0  # smooth edges between faces this different in size
                        # are hidden WITHOUT smoothing (status 1)
MAX_TEXTURE_PX = 1024   # textures are downscaled to fit this long edge
COORD_DECIMALS = 4      # 0.1 mm coordinate precision in generated GDL

# Ancestry chain of a plain placeable Object (from the standard library)
ANCESTRY_GUIDS = [
    "F938E33A-329D-4A36-BE3E-85E126820996",
    "103E8D2C-8230-42E1-9597-46F84CCE28C0",
]


def _fmt(v: float) -> str:
    return f"{v:.{COORD_DECIMALS}f}"


@dataclass
class BuildResult:
    hsf_dir: Path
    guid: str
    a: float
    b: float
    h: float
    groups: list[str] = field(default_factory=list)
    textures: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _rotate_uv(u: float, v: float, deg: int) -> tuple[float, float]:
    if deg == 90:
        return v, -u
    if deg == 180:
        return -u, -v
    if deg == 270:
        return -v, u
    return u, v


def build_group_gdl(verts, uvs, tris: list[Face], textured: bool,
                    uv_rotate: int = 0) -> list[str]:
    """Emit BASE/VERT|TEVE/EDGE/PGON/BODY for one material group.

    Vertices are split per (position, uv) pair so TEVE can carry explicit
    UVs. Edge smoothing is decided on the ORIGINAL position topology so UV
    seams do not show up as hard visible edges.
    """
    normals = [face_normal(verts, t) for t in tris]
    areas = [face_area(verts, t) for t in tris]

    orig_edges: dict[tuple[int, int], list[int]] = {}
    for ti, tri in enumerate(tris):
        for k in range(3):
            a, b = tri[k][0], tri[(k + 1) % 3][0]
            orig_edges.setdefault((min(a, b), max(a, b)), []).append(ti)

    cos_soft = math.cos(math.radians(SOFT_EDGE_DEG))
    orig_status: dict[tuple[int, int], int] = {}
    for key, owners in orig_edges.items():
        if len(owners) < 2:
            orig_status[key] = 0
            continue
        # soft when every pair of adjacent faces is (anti)parallel enough;
        # abs() tolerates double-sided geometry with flipped windings
        soft = True
        for oi in range(len(owners)):
            for oj in range(oi + 1, len(owners)):
                n1, n2 = normals[owners[oi]], normals[owners[oj]]
                if abs(n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]) < cos_soft:
                    soft = False
                    break
            if not soft:
                break
        if not soft:
            orig_status[key] = 0
            continue
        amin = min(areas[o] for o in owners)
        amax = max(areas[o] for o in owners)
        orig_status[key] = 1 if (amin > 0 and amax / amin > AREA_RATIO_HARD) else 3

    def corner_key(c):
        return (c[0], c[1] if textured else None)

    local: dict[tuple, int] = {}
    order: list[tuple] = []
    for tri in tris:
        for c in tri:
            k = corner_key(c)
            if k not in local:
                local[k] = len(order) + 1  # GDL 1-based
                order.append(k)

    out = ["BASE"]
    for (vi, vt) in order:
        x, y, z = verts[vi]
        if textured:
            u, v = uvs[vt] if vt is not None else (0.0, 0.0)
            u, v = _rotate_uv(u, v, uv_rotate)
            out.append(f"TEVE {_fmt(x)}, {_fmt(y)}, {_fmt(z)}, {_fmt(u)}, {_fmt(v)}")
        else:
            out.append(f"VERT {_fmt(x)}, {_fmt(y)}, {_fmt(z)}")

    # pair faces onto edge instances two at a time, opposite directions
    instances: dict[tuple[int, int], list[list]] = {}
    edge_defs: list[tuple[int, int, int]] = []
    face_refs: list[tuple[list[str], bool]] = []
    for tri in tris:
        refs: list[str] = []
        smooth_face = False
        for k in range(3):
            ca, cb = tri[k], tri[(k + 1) % 3]
            la, lb = local[corner_key(ca)], local[corner_key(cb)]
            key = (min(la, lb), max(la, lb))
            status = orig_status.get((min(ca[0], cb[0]), max(ca[0], cb[0])), 0)
            slot = None
            for inst in instances.setdefault(key, []):
                if inst[2] == 1 and inst[1] == (lb, la):
                    slot = inst
                    break
            if slot is None:
                edge_defs.append((la, lb, status))
                slot = [len(edge_defs), (la, lb), 0]
                instances[key].append(slot)
            slot[2] += 1
            refs.append(str(slot[0] if slot[1] == (la, lb) else -slot[0]))
            if edge_defs[slot[0] - 1][2] == 3:
                smooth_face = True
        face_refs.append((refs, smooth_face))

    for la, lb, status in edge_defs:
        out.append(f"EDGE {la}, {lb}, -1, -1, {status}")
    for refs, smooth_face in face_refs:
        out.append(f"PGON 3, 0, {2 if smooth_face else 0}, {', '.join(refs)}")
    out.append("BODY -1")
    return out


def _textured_material(name: str, tex_ref: str, rgb) -> list[str]:
    r, g, b = rgb
    return [
        f'DEFINE MATERIAL "{name}" 21,',
        f"\t{_fmt(r)}, {_fmt(g)}, {_fmt(b)},",
        "\t0.35, 0.95, 0.05, 0.0,",
        "\t20.0,",
        "\t0,",
        f'\tind(fill, ""), 1, ind(texture, "{tex_ref}")',
        "",
    ]


def _flat_material(name: str, rgb) -> list[str]:
    r, g, b = rgb
    return [
        f'DEFINE MATERIAL "{name}" 1,',
        f"\t{_fmt(r)}, {_fmt(g)}, {_fmt(b)},",
        "\t1.0, 1.0, 0.0, 0.0,",
        "\t50.0,",
        "\t0",
        "",
    ]


def _downscale(dst: Path) -> None:
    """Cap the long edge at MAX_TEXTURE_PX, in place, keeping the format.

    Pillow rather than a platform image tool: the previous `sips` call worked
    only on macOS and its failure was swallowed, so Windows silently shipped
    source-resolution textures. A texture that cannot be read is not a build
    failure, so it ships as-is.
    """
    try:
        from PIL import Image as PILImage

        with PILImage.open(dst) as img:
            if max(img.size) <= MAX_TEXTURE_PX:
                return
            img.thumbnail((MAX_TEXTURE_PX, MAX_TEXTURE_PX))
            extra = {"quality": 90} if dst.suffix.lower() in (".jpg", ".jpeg") else {}
            img.save(dst, **extra)
    except OSError:
        pass  # unreadable or unwritable image: ship the original


def _texture_filename(src: Path) -> str:
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:6]
    stem = re.sub(r"[^A-Za-z0-9]+", "_", src.stem).strip("_").lower()
    return f"{stem}_{digest}{src.suffix.lower()}"


def build_hsf(mesh: Mesh, cfg: ObjectConfig, name: str, hsf_dir: str | Path,
              textures_dir: str | Path | None = None) -> BuildResult:
    hsf_dir = Path(hsf_dir)
    variants = cfg.variants
    frame_variants = cfg.frame_variants
    variant_roles = set().union(*(v[1].keys() for v in variants)) if variants else set()

    verts = mesh.verts
    minx = min(v[0] for v in verts)
    miny = min(v[1] for v in verts)
    minz = min(v[2] for v in verts)
    verts = [(x - minx, y - miny, z - minz) for x, y, z in verts]
    a0 = max(v[0] for v in verts)
    b0 = max(v[1] for v in verts)
    h0 = max(v[2] for v in verts)

    ordered = sorted(mesh.groups.items(), key=lambda kv: -len(kv[1]))

    used_shared: set[str] = set()
    used_roles: set[str] = set()
    has_frame_groups = False
    infos = []
    for i, (mtl, _faces) in enumerate(ordered, 1):
        spec = cfg.group_for(mtl, i)
        tex = spec.texture
        if tex == "@frame" and frame_variants:
            has_frame_groups = True
        elif tex and tex in variant_roles:
            used_roles.add(tex)
        elif tex and tex in cfg.textures:
            used_shared.add(tex)
        elif tex != "@frame":
            tex = None
        infos.append((spec.label, tex, spec.rgb, spec.uv_rotate))

    # texture registry: (GDL texture name, source file, library file name);
    # library file names carry a content hash so identical textures dedupe
    # across objects and redeploys can skip files that already exist
    picts: list[tuple[str, Path, str]] = []
    for vi, (_vlabel, vmap) in enumerate(variants, 1):
        for role in sorted(used_roles):
            if isinstance(vmap.get(role), Path):
                picts.append((f"{role}_v{vi}", vmap[role], _texture_filename(vmap[role])))
    for key in sorted(used_shared):
        picts.append((key, cfg.textures[key], _texture_filename(cfg.textures[key])))
    pict_file = {ref: fname for ref, _, fname in picts}

    # ---- 3D script ----
    gdl3d = [
        f"! {name} - generated by archicad-gdl",
        f"MUL A / {_fmt(a0)}, B / {_fmt(b0)}, ZZYZX / {_fmt(h0)}",
        "",
    ]
    for ref, _src, _fname in picts:
        gdl3d.append(f'DEFINE TEXTURE "{ref}" "{pict_file[ref]}", 1, 1, 0, 0')
    gdl3d.append("")
    for i, (label, tex, rgb, _uv_rot) in enumerate(infos, 1):
        if tex == "@frame":
            for fi, (_flabel, frgb) in enumerate(frame_variants, 1):
                gdl3d += _flat_material(f"{name}_m{i}_f{fi}", frgb)
        elif tex in variant_roles:
            for vi, (_vlabel, vmap) in enumerate(variants, 1):
                val = vmap.get(tex)
                if isinstance(val, Path):
                    gdl3d += _textured_material(f"{name}_m{i}_v{vi}", f"{tex}_v{vi}", rgb)
                elif val is not None:
                    gdl3d += _flat_material(f"{name}_m{i}_v{vi}", val)
        elif tex:
            gdl3d += _textured_material(f"{name}_m{i}", tex, rgb)
        else:
            gdl3d += _flat_material(f"{name}_m{i}", rgb)

    group_summaries = []
    for i, ((mtl, faces), (label, tex, rgb, uv_rot)) in enumerate(zip(ordered, infos), 1):
        tris = [t for f in faces for t in triangulate(f)]
        gdl3d += [
            f"! ---- group {i}: {label} [{mtl}] ({len(tris)} triangles) ----",
            f"if override_surface_{i} then",
            f"\tmaterial surface_{i}",
            "else",
        ]
        if tex == "@frame":
            gdl3d.append(f'\tmaterial "{name}_m{i}_f1"')
            for fi in range(2, len(frame_variants) + 1):
                gdl3d.append(f'\tif frame_finish = {fi} then material "{name}_m{i}_f{fi}"')
        elif tex in variant_roles:
            gdl3d.append(f'\tmaterial "{name}_m{i}_v1"')
            for vi in range(2, len(variants) + 1):
                gdl3d.append(f'\tif finish = {vi} then material "{name}_m{i}_v{vi}"')
        else:
            gdl3d.append(f'\tmaterial "{name}_m{i}"')
        gdl3d.append("endif")
        has_uvs = any(c[1] is not None for f in faces for c in f)
        textured = has_uvs and tex is not None and tex != "@frame"
        gdl3d += build_group_gdl(verts, mesh.uvs, tris, textured=textured,
                                 uv_rotate=uv_rot)
        gdl3d.append("")
        group_summaries.append(f"{label} [{mtl.strip()}] {len(tris)} tris tex={tex}")

    gdl2d = [
        "project2 3, 270, 2",
        "hotspot2 0, 0",
        "hotspot2 A, 0",
        "hotspot2 A, B",
        "hotspot2 0, B",
        "hotspot2 A/2, B/2",
    ]

    # ---- write HSF ----
    scripts = hsf_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "3d.gdl").write_text("\n".join(gdl3d) + "\n")
    (scripts / "2d.gdl").write_text("\n".join(gdl2d) + "\n")

    vl_lines = []
    if variants:
        pairs = ",\n\t\t\t\t".join(f"{vi}, `{vlabel}`"
                                   for vi, (vlabel, _) in enumerate(variants, 1))
        vl_lines.append(f'values{{2}} "finish"\t{pairs}')
    if has_frame_groups:
        pairs = ",\n\t\t\t\t".join(f"{fi}, `{flabel}`"
                                   for fi, (flabel, _) in enumerate(frame_variants, 1))
        vl_lines.append(f'values{{2}} "frame_finish"\t{pairs}')
    vl_section = ""
    if vl_lines:
        (scripts / "vl.gdl").write_text("\n".join(vl_lines) + "\n")
        vl_section = '\n\t<Script_VL SectVersion="20" SectionFlags="0" SubIdent="0"/>'

    # texture image files ship NEXT TO the .gsm, not inside it (see module
    # docstring: external render engines need real library image files)
    texture_files: list[Path] = []
    if picts:
        tex_dir = Path(textures_dir) if textures_dir else hsf_dir.parent / "textures"
        tex_dir.mkdir(parents=True, exist_ok=True)
        for _ref, src, fname in picts:
            dst = tex_dir / fname
            if dst not in texture_files:
                shutil.copy(src, dst)
                _downscale(dst)
                texture_files.append(dst)

    main_guid = cfg.resolve_guid(name)
    pict_xml = ""
    (hsf_dir / "libpartdata.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<LibpartData Owner="0" Signature="1196644685" Version="46">
\t<Identification>
\t\t<MainGUID>{main_guid}</MainGUID>
\t\t<IsPlaceable>true</IsPlaceable>
\t\t<IsArchivable>false</IsArchivable>
\t\t<MigrationValue>Normal</MigrationValue>
\t\t<IsTemplate>false</IsTemplate>
\t</Identification>
\t<Copyright SectVersion="1" SectionFlags="0" SubIdent="0"/>
\t<Ancestry SectVersion="1" SectionFlags="0" SubIdent="0"/>
\t<ParamSection SectVersion="27" SectionFlags="0" SubIdent="0"/>{vl_section}
\t<Script_2D SectVersion="20" SectionFlags="0" SubIdent="0"/>
\t<Script_3D SectVersion="20" SectionFlags="0" SubIdent="0"/>{pict_xml}
</LibpartData>
""")

    ancestry = "\n".join(f"\t<MainGUID>{g}</MainGUID>" for g in ANCESTRY_GUIDS)
    (hsf_dir / "ancestry.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<Ancestry>
{ancestry}
</Ancestry>
""")

    (hsf_dir / "libpartdocs.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<libpartdocs>
\t<Copyright>
\t\t<Author>archicad-gdl</Author>
\t\t<License>
\t\t\t<Type>CC BY</Type>
\t\t\t<Version>4.0</Version>
\t\t</License>
\t</Copyright>
\t<Keywords SectVersion="1" SectionFlags="0" SubIdent="0">
\t\t<![CDATA[furniture, {name}]]>
\t</Keywords>
\t<CommentSection>
\t\t<![CDATA[Generated by archicad-gdl (archicad_mcp.gdl)]]>
\t</CommentSection>
</libpartdocs>
""")

    extra_params = ""
    if variants:
        extra_params += """\t\t<Integer Name="finish">
\t\t\t<Description><![CDATA["Finish"]]></Description>
\t\t\t<Value>1</Value>
\t\t</Integer>
"""
    if has_frame_groups:
        extra_params += """\t\t<Integer Name="frame_finish">
\t\t\t<Description><![CDATA["Frame finish"]]></Description>
\t\t\t<Value>1</Value>
\t\t</Integer>
"""
    surf_params = []
    for i, (label, _tex, _rgb, _uv_rot) in enumerate(infos, 1):
        surf_params.append(f"""\t\t<Boolean Name="override_surface_{i}">
\t\t\t<Description><![CDATA["Override Surface: {label}"]]></Description>
\t\t\t<Value>0</Value>
\t\t</Boolean>
\t\t<Material Name="surface_{i}">
\t\t\t<Description><![CDATA["{label}"]]></Description>
\t\t\t<Flags>
\t\t\t\t<ParFlg_Child/>
\t\t\t</Flags>
\t\t\t<Value>0</Value>
\t\t</Material>""")
    (hsf_dir / "paramlist.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<ParamSection>
\t<ParamSectHeader>
\t\t<AutoHotspots>false</AutoHotspots>
\t\t<StatBits>
\t\t\t<STBit_UIDefault/>
\t\t</StatBits>
\t\t<WDLeftFrame>0</WDLeftFrame>
\t\t<WDRightFrame>0</WDRightFrame>
\t\t<WDTopFrame>0</WDTopFrame>
\t\t<WDBotFrame>0</WDBotFrame>
\t\t<LayFlags>65535</LayFlags>
\t\t<WDMirrorThickness>0</WDMirrorThickness>
\t\t<WDWallInset>0</WDWallInset>
\t</ParamSectHeader>
\t<Parameters SectVersion="27" SectionFlags="0" SubIdent="0">
\t\t<Length Name="A">
\t\t\t<Description><![CDATA["Width"]]></Description>
\t\t\t<Value>{_fmt(a0)}</Value>
\t\t</Length>
\t\t<Length Name="B">
\t\t\t<Description><![CDATA["Depth"]]></Description>
\t\t\t<Value>{_fmt(b0)}</Value>
\t\t</Length>
\t\t<Length Name="ZZYZX">
\t\t\t<Description><![CDATA["Height"]]></Description>
\t\t\t<Value>{_fmt(h0)}</Value>
\t\t</Length>
{extra_params}{chr(10).join(surf_params)}
\t</Parameters>
</ParamSection>
""")

    return BuildResult(hsf_dir=hsf_dir, guid=main_guid, a=a0, b=b0, h=h0,
                       groups=group_summaries, textures=texture_files,
                       notes=list(mesh.notes))
