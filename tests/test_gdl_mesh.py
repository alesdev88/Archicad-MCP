"""Mesh parsing for the GDL pipeline: OBJ, synthetic 3DS, welding, units."""

import struct

from archicad_mcp.gdl.mesh import parse_3ds, parse_obj, triangulate


def _chunk(cid: int, payload: bytes) -> bytes:
    return struct.pack("<HI", cid, 6 + len(payload)) + payload


def _synthetic_3ds(pivot=(0.0, 0.0, 0.0)) -> bytes:
    """One quad (two triangles) named 'Plate' at z=0, 100x100 source units,
    with a material 'Steel' assigned to both faces and a keyframer node
    carrying the given pivot."""
    verts = [(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)]
    vert_payload = struct.pack("<H", len(verts))
    for v in verts:
        vert_payload += struct.pack("<3f", *v)
    faces = [(0, 1, 2), (0, 2, 3)]
    face_payload = struct.pack("<H", len(faces))
    for f in faces:
        face_payload += struct.pack("<4H", *f, 0)
    matgroup = b"Steel\x00" + struct.pack("<H", 2) + struct.pack("<2H", 0, 1)
    face_chunk_payload = face_payload + _chunk(0x4130, matgroup)
    trimesh = _chunk(0x4110, vert_payload) + _chunk(0x4120, face_chunk_payload)
    obj = b"Plate\x00" + _chunk(0x4100, trimesh)
    editor = _chunk(0x4000, obj)

    node = _chunk(0xB010, b"Plate\x00" + struct.pack("<3H", 0, 0, 0xFFFF))
    node += _chunk(0xB013, struct.pack("<3f", *pivot))
    track = struct.pack("<H", 0) + b"\x00" * 8 + struct.pack("<I", 1)
    track += struct.pack("<IH", 0, 0) + struct.pack("<3f", 0, 0, 0)
    node += _chunk(0xB020, track)
    keyframer = _chunk(0xB002, node)

    body = _chunk(0x3D3D, editor) + _chunk(0xB000, keyframer)
    return _chunk(0x4D4D, body)


def test_parse_3ds_units_and_groups(tmp_path):
    f = tmp_path / "plate.3ds"
    f.write_bytes(_synthetic_3ds())
    mesh = parse_3ds(f)
    # span 100 -> cm is wrong (span > 5 and <= 100 is cm); 100 > 100 is false,
    # so units resolve to cm: 100 cm -> 1.0 m
    assert max(v[0] for v in mesh.verts) == 1.0
    assert list(mesh.groups) == ["Steel"]
    assert len(mesh.groups["Steel"]) == 2


def test_parse_3ds_applies_keyframer_pivot(tmp_path):
    f = tmp_path / "plate.3ds"
    f.write_bytes(_synthetic_3ds(pivot=(0.0, 0.0, -50.0)))
    mesh = parse_3ds(f)
    # world = v - pivot: the plate lifts by +50 source units = 0.5 m
    zs = {round(v[2], 6) for v in mesh.verts}
    assert zs == {0.5}


def test_parse_3ds_welds_duplicate_vertices(tmp_path):
    # two triangles sharing an edge, but with duplicated seam vertices
    verts = [(0, 0, 0), (10, 0, 0), (10, 10, 0),
             (0, 0, 0), (10, 10, 0), (0, 10, 0)]
    vert_payload = struct.pack("<H", len(verts))
    for v in verts:
        vert_payload += struct.pack("<3f", *v)
    faces = [(0, 1, 2), (3, 4, 5)]
    face_payload = struct.pack("<H", len(faces))
    for fc in faces:
        face_payload += struct.pack("<4H", *fc, 0)
    trimesh = _chunk(0x4110, vert_payload) + _chunk(0x4120, face_payload)
    obj = b"M\x00" + _chunk(0x4100, trimesh)
    data = _chunk(0x4D4D, _chunk(0x3D3D, _chunk(0x4000, obj)))
    f = tmp_path / "seam.3ds"
    f.write_bytes(data)
    mesh = parse_3ds(f)
    assert len(mesh.verts) == 4  # 6 stored, 2 welded away


def test_parse_obj_autodetects_units(tmp_path):
    f = tmp_path / "box.obj"
    f.write_text("v 0 0 0\nv 80 0 0\nv 80 40 0\nf 1 2 3\n")
    mesh = parse_obj(f)  # span 80 -> cm
    assert abs(max(v[0] for v in mesh.verts) - 0.8) < 1e-9
    f2 = tmp_path / "box_m.obj"
    f2.write_text("v 0 0 0\nv 1.6 0 0\nv 1.6 0.8 0\nf 1 2 3\n")
    mesh2 = parse_obj(f2)  # span 1.6 -> already meters
    assert abs(max(v[0] for v in mesh2.verts) - 1.6) < 1e-9


def test_parse_obj_uv_corners(tmp_path):
    f = tmp_path / "quad.obj"
    f.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
                 "vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\n"
                 "usemtl wood\nf 1/1 2/2 3/3 4/4\n")
    mesh = parse_obj(f)
    face = mesh.groups["wood"][0]
    assert [c[1] for c in face] == [0, 1, 2, 3]
    assert len(triangulate(face)) == 2
