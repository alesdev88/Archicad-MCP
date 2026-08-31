"""Mesh loading for the GDL pipeline: Wavefront OBJ and Autodesk 3DS.

Both parsers return a Mesh whose vertices are in meters. Faces are stored per
material group as corner lists of (vertex index, uv index or None).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

Corner = tuple[int, int | None]
Face = list[Corner]


@dataclass
class Mesh:
    verts: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    groups: dict[str, list[Face]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def face_count(self) -> int:
        return sum(len(f) for f in self.groups.values())


def _autoscale(verts: list[tuple[float, float, float]]) -> tuple[float, str]:
    """Guess source units from the model extent: furniture-scale heuristics."""
    span = max(max(abs(v[i]) for v in verts) for i in range(3))
    if span > 100:
        return 1 / 1000, "mm"
    if span > 5:
        return 1 / 100, "cm"
    return 1.0, "m"


def load(path: str | Path) -> Mesh:
    path = Path(path)
    if path.suffix.lower() == ".3ds":
        return parse_3ds(path)
    return parse_obj(path)


# ------------------------------------------------------------------------ OBJ

def parse_obj(path: str | Path) -> Mesh:
    verts: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    groups: dict[str, list[Face]] = {}
    current = "default"
    for raw in Path(path).read_text(errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("v "):
            _, x, y, z = line.split()[:4]
            verts.append((float(x), float(y), float(z)))
        elif line.startswith("vt "):
            parts = line.split()
            uvs.append((float(parts[1]), float(parts[2])))
        elif line.startswith("usemtl "):
            current = line.split(None, 1)[1].strip()
        elif line.startswith("f "):
            corners: Face = []
            for tok in line.split()[1:]:
                fields = tok.split("/")
                vi = int(fields[0])
                vi = vi - 1 if vi > 0 else len(verts) + vi
                vt = None
                if len(fields) > 1 and fields[1]:
                    ti = int(fields[1])
                    vt = ti - 1 if ti > 0 else len(uvs) + ti
                corners.append((vi, vt))
            groups.setdefault(current, []).append(corners)
    scale, unit = _autoscale(verts)
    verts = [(x * scale, y * scale, z * scale) for x, y, z in verts]
    return Mesh(verts=verts, uvs=uvs, groups=groups,
                notes=[f"obj units detected: {unit}"])


# ------------------------------------------------------------------------ 3DS

def parse_3ds(path: str | Path) -> Mesh:
    """Binary 3DS reader.

    Applies keyframer pivot correction (world = v - pivot + node position;
    exporters park sub-meshes at the origin and place them via the keyframer),
    drops exact duplicate meshes, welds coincident vertices (3DS meshes ship
    as unstitched patches whose seams would otherwise render as visible
    boundary edges across smooth surfaces), and autodetects units.
    """
    data = Path(path).read_bytes()

    def read_cstr(pos: int) -> tuple[str, int]:
        end = data.index(b"\x00", pos)
        return data[pos:end].decode("latin-1"), end + 1

    def chunks(start: int, end: int):
        pos = start
        while pos + 6 <= end:
            cid, clen = struct.unpack_from("<HI", data, pos)
            if clen < 6 or pos + clen > end:
                break
            yield cid, pos + 6, pos + clen
            pos += clen

    meshes: list[dict] = []
    nodes: dict[str, tuple[tuple, tuple]] = {}

    def parse_trimesh(objname: str, start: int, end: int) -> None:
        mesh = {"name": objname, "verts": [], "uvs": [], "faces": [], "matgroups": {}}
        for cid, s, e in chunks(start, end):
            if cid == 0x4110:
                (n,) = struct.unpack_from("<H", data, s)
                mesh["verts"] = [v for v in struct.iter_unpack("<3f", data[s + 2:s + 2 + 12 * n])]
            elif cid == 0x4140:
                (n,) = struct.unpack_from("<H", data, s)
                mesh["uvs"] = [uv for uv in struct.iter_unpack("<2f", data[s + 2:s + 2 + 8 * n])]
            elif cid == 0x4120:
                (n,) = struct.unpack_from("<H", data, s)
                fpos = s + 2
                for _ in range(n):
                    a, b, c, _flags = struct.unpack_from("<4H", data, fpos)
                    mesh["faces"].append((a, b, c))
                    fpos += 8
                for c2, s2, _e2 in chunks(fpos, e):
                    if c2 == 0x4130:
                        mname, p = read_cstr(s2)
                        (fn,) = struct.unpack_from("<H", data, p)
                        idxs = struct.unpack_from(f"<{fn}H", data, p + 2)
                        mesh["matgroups"].setdefault(mname, []).extend(idxs)
        meshes.append(mesh)

    def parse_node(start: int, end: int) -> None:
        name, pivot, post = None, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        for cid, s, _e in chunks(start, end):
            if cid == 0xB010:
                name, _ = read_cstr(s)
            elif cid == 0xB013:
                pivot = struct.unpack_from("<3f", data, s)
            elif cid == 0xB020:
                (keys,) = struct.unpack_from("<I", data, s + 10)
                if keys >= 1:
                    post = struct.unpack_from("<3f", data, s + 14 + 6)
        if name:
            nodes[name] = (pivot, post)

    for cid, s, e in chunks(0, len(data)):
        if cid == 0x4D4D:
            for c2, s2, e2 in chunks(s, e):
                if c2 == 0x3D3D:
                    for c3, s3, e3 in chunks(s2, e2):
                        if c3 == 0x4000:
                            name, p = read_cstr(s3)
                            for c4, s4, e4 in chunks(p, e3):
                                if c4 == 0x4100:
                                    parse_trimesh(name, s4, e4)
                elif c2 == 0xB000:
                    for c3, s3, e3 in chunks(s2, e2):
                        if c3 == 0xB002:
                            parse_node(s3, e3)

    # drop exact duplicate meshes (same counts, materials and bbox)
    seen: set[tuple] = set()
    unique: list[dict] = []
    dup_names: list[str] = []
    for mesh in meshes:
        if not mesh["verts"]:
            continue
        xs = [v[0] for v in mesh["verts"]]
        ys = [v[1] for v in mesh["verts"]]
        zs = [v[2] for v in mesh["verts"]]
        key = (len(mesh["verts"]), len(mesh["faces"]),
               tuple(sorted(mesh["matgroups"])),
               round(min(xs), 1), round(max(xs), 1), round(min(ys), 1),
               round(max(ys), 1), round(min(zs), 1), round(max(zs), 1))
        if key in seen:
            dup_names.append(mesh["name"])
            continue
        seen.add(key)
        unique.append(mesh)

    verts: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    groups: dict[str, list[Face]] = {}
    for mesh in unique:
        pivot, post = nodes.get(mesh["name"], ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
        base = len(verts)
        uv_base = len(uvs)
        has_uv = len(mesh["uvs"]) == len(mesh["verts"])
        for (x, y, z) in mesh["verts"]:
            verts.append((x - pivot[0] + post[0],
                          y - pivot[1] + post[1],
                          z - pivot[2] + post[2]))
        if has_uv:
            uvs.extend(mesh["uvs"])
        face_mat: dict[int, str] = {}
        for mname, idxs in mesh["matgroups"].items():
            for fi in idxs:
                face_mat[fi] = mname
        for fi, (a, b, c) in enumerate(mesh["faces"]):
            mat = face_mat.get(fi, "default")
            corners = [(base + vi, uv_base + vi if has_uv else None) for vi in (a, b, c)]
            groups.setdefault(mat, []).append(corners)

    scale, unit = _autoscale(verts)
    verts = [(x * scale, y * scale, z * scale) for x, y, z in verts]

    # weld coincident vertices (0.01 mm tolerance)
    canon: dict[tuple, int] = {}
    remap: dict[int, int] = {}
    welded: list[tuple[float, float, float]] = []
    for vi, (x, y, z) in enumerate(verts):
        key = (round(x, 5), round(y, 5), round(z, 5))
        if key not in canon:
            canon[key] = len(welded)
            welded.append((x, y, z))
        remap[vi] = canon[key]
    dropped = 0
    for mat in groups:
        new_faces: list[Face] = []
        for face in groups[mat]:
            mapped = [(remap[vi], vt) for vi, vt in face]
            if len({vi for vi, _ in mapped}) < 3:
                dropped += 1
                continue
            new_faces.append(mapped)
        groups[mat] = new_faces

    notes = [f"3ds units detected: {unit}",
             f"{len(dup_names)} duplicate meshes dropped"
             + (f" ({', '.join(dup_names)})" if dup_names else ""),
             f"{len(verts) - len(welded)} vertices welded, {dropped} degenerate faces dropped"]
    return Mesh(verts=welded, uvs=uvs, groups=groups, notes=notes)


# ------------------------------------------------------------------ geometry

def triangulate(face: Face) -> list[Face]:
    """Fan-triangulate; keeps winding. Triangles pass through untouched."""
    if len(face) == 3:
        return [face]
    return [[face[0], face[i], face[i + 1]] for i in range(1, len(face) - 1)]


def face_normal(verts, tri: Face) -> tuple[float, float, float]:
    ax, ay, az = verts[tri[0][0]]
    bx, by, bz = verts[tri[1][0]]
    cx, cy, cz = verts[tri[2][0]]
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / ln, ny / ln, nz / ln


def face_area(verts, tri: Face) -> float:
    ax, ay, az = verts[tri[0][0]]
    bx, by, bz = verts[tri[1][0]]
    cx, cy, cz = verts[tri[2][0]]
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return math.sqrt(nx * nx + ny * ny + nz * nz) / 2
