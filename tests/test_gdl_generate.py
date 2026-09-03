"""HSF generation: edge statuses, manifold splitting, scripts, manifests."""

import xml.etree.ElementTree as ET

import pytest

from archicad_mcp.gdl.config import GroupSpec, ObjectConfig, find_object, load_config
from archicad_mcp.gdl.generate import build_group_gdl, build_hsf
from archicad_mcp.gdl.mesh import Mesh


def _cube_mesh() -> Mesh:
    v = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
         (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    faces = [[(i, None) for i in q] for q in quads]
    return Mesh(verts=v, uvs=[], groups={"steel": faces})


def test_cube_edges_are_sharp_and_visible():
    mesh = _cube_mesh()
    tris = []
    from archicad_mcp.gdl.mesh import triangulate
    for f in mesh.groups["steel"]:
        tris += triangulate(f)
    lines = build_group_gdl(mesh.verts, [], tris, textured=False)
    edge_lines = [l for l in lines if l.startswith("EDGE")]
    # cube corner edges (90 degrees) stay visible+sharp (status 0);
    # the coplanar quad diagonals become invisible+smooth (status 3)
    statuses = {l.rsplit(",", 1)[1].strip() for l in edge_lines}
    assert statuses == {"0", "3"}
    assert sum(1 for l in edge_lines if l.endswith(" 0")) == 12
    assert lines[-1] == "BODY -1"


def test_non_manifold_edge_is_split_into_instances():
    # three triangles sharing the SAME edge (0-1): a GDL edge takes at most
    # two polygons, so a third face must get its own edge instance
    v = [(0, 0, 0), (1, 0, 0), (0.5, 1, 0), (0.5, -1, 0), (0.5, 0, 1)]
    tris = [[(0, None), (1, None), (2, None)],
            [(1, None), (0, None), (3, None)],
            [(0, None), (1, None), (4, None)]]
    lines = build_group_gdl(v, [], tris, textured=False)
    edge_lines = [l for l in lines if l.startswith("EDGE")]
    # 3 faces x 3 edges = 9 edge slots; edge 0-1 needs 2 instances -> 8 total
    assert len(edge_lines) == 8


def test_teve_used_only_when_textured():
    v = [(0, 0, 0), (1, 0, 0), (1, 1, 0)]
    uvs = [(0, 0), (1, 0), (1, 1)]
    tris = [[(0, 0), (1, 1), (2, 2)]]
    plain = build_group_gdl(v, uvs, tris, textured=False)
    tex = build_group_gdl(v, uvs, tris, textured=True)
    assert any(l.startswith("VERT") for l in plain)
    assert not any(l.startswith("TEVE") for l in plain)
    assert any(l.startswith("TEVE") for l in tex)
    assert not any(l.startswith("VERT ") for l in tex)


@pytest.fixture()
def built(tmp_path):
    tex = tmp_path / "oak.jpg"
    tex.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    cfg = ObjectConfig(
        name="Test Object",
        variants=[("Oak", {"top": tex}), ("Black", {"top": (0.1, 0.1, 0.1)})],
        frame_variants=[("Black steel", (0.1, 0.1, 0.1)),
                        ("Grey steel", (0.7, 0.7, 0.7))],
        groups={"steel": GroupSpec(label="Frame", texture="@frame"),
                "wood": GroupSpec(label="Top", texture="top")},
    )
    mesh = _cube_mesh()
    mesh.groups["wood"] = [[(0, None), (1, None), (2, None)]]
    result = build_hsf(mesh, cfg, "Test Object", tmp_path / "hsf",
                       textures_dir=tmp_path / "tex")
    return tmp_path / "hsf", result


def test_hsf_structure_and_manifests(built):
    hsf, result = built
    for f in ["libpartdata.xml", "ancestry.xml", "paramlist.xml",
              "libpartdocs.xml", "scripts/2d.gdl", "scripts/3d.gdl",
              "scripts/vl.gdl"]:
        assert (hsf / f).exists(), f
    for xml in ["libpartdata.xml", "ancestry.xml", "paramlist.xml"]:
        ET.parse(hsf / xml)  # well-formed
    data = (hsf / "libpartdata.xml").read_text()
    assert result.guid in data
    # textures ship as library files next to the .gsm, never inside it
    assert "GDLPict" not in data
    assert not (hsf / "images").exists()
    assert len(result.textures) == 1
    assert result.textures[0].exists()
    assert result.textures[0].name.startswith("oak_")
    assert "<Script_VL" in data


def test_finish_dropdowns_and_materials(built):
    hsf, result = built
    gdl = (hsf / "scripts/3d.gdl").read_text()
    # textures referenced by library file name (external render engines need
    # real image files; gsm-embedded pictures render flat in Enscape)
    assert f'DEFINE TEXTURE "top_v1" "{result.textures[0].name}",' in gdl
    # variant selection chains for both dropdowns
    assert 'if finish = 2 then material "Test Object_m2_v2"' in gdl
    assert 'if frame_finish = 2 then material "Test Object_m1_f2"' in gdl
    vl = (hsf / "scripts/vl.gdl").read_text()
    assert 'values{2} "finish"' in vl and "`Oak`" in vl
    assert 'values{2} "frame_finish"' in vl and "`Grey steel`" in vl
    params = (hsf / "paramlist.xml").read_text()
    assert '<Integer Name="finish">' in params
    assert '<Integer Name="frame_finish">' in params
    assert '"Override Surface: Top"' in params


def test_stable_guid_via_config():
    cfg = ObjectConfig(name="X", guid="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
    assert cfg.resolve_guid("anything") == "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    cfg2 = ObjectConfig(name="X")
    assert cfg2.resolve_guid("My Chair") == cfg2.resolve_guid("My Chair")
    assert cfg2.resolve_guid("My Chair") != cfg2.resolve_guid("My Chair v2")


def test_config_load_and_prefix_match(tmp_path):
    (tmp_path / "oak.jpg").write_bytes(b"x")
    (tmp_path / "assets.json").write_text("""{
      "objects": {
        "My Chair": {
          "guid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
          "variants": [{"label": "Oak", "roles": {"face": "oak.jpg"}},
                       {"label": "Black", "roles": {"face": [0.1, 0.1, 0.1]}}],
          "groups": {"mtl_1": {"label": "Seat", "texture": "face",
                               "rgb": [0.7, 0.6, 0.4]}},
          "decimate": {"Frame": 6000}
        }
      }
    }""")
    objects = load_config(tmp_path / "assets.json")
    cfg = find_object(objects, "My Chair v4")
    assert cfg.guid == "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    assert cfg.variants[0][1]["face"] == tmp_path / "oak.jpg"
    assert cfg.variants[1][1]["face"] == (0.1, 0.1, 0.1)
    assert cfg.groups["mtl_1"].label == "Seat"
    assert cfg.decimate == {"Frame": 6000}
    fallback = find_object(objects, "Unknown Thing")
    assert fallback.groups == {} and fallback.guid is None


def test_downscale_caps_the_long_edge(tmp_path):
    from PIL import Image as PILImage

    from archicad_mcp.gdl.generate import MAX_TEXTURE_PX, _downscale

    src = tmp_path / "big.jpg"
    PILImage.new("RGB", (3000, 1500), (120, 90, 60)).save(src)
    _downscale(src)
    with PILImage.open(src) as img:
        assert max(img.size) == MAX_TEXTURE_PX
        assert img.size == (MAX_TEXTURE_PX, MAX_TEXTURE_PX // 2)


def test_downscale_leaves_small_images_alone(tmp_path):
    from PIL import Image as PILImage

    from archicad_mcp.gdl.generate import _downscale

    src = tmp_path / "small.png"
    PILImage.new("RGB", (256, 256), (10, 20, 30)).save(src)
    before = src.read_bytes()
    _downscale(src)
    assert src.read_bytes() == before


def test_downscale_ignores_a_file_it_cannot_read(tmp_path):
    from archicad_mcp.gdl.generate import _downscale

    junk = tmp_path / "notreally.jpg"
    junk.write_text("this is not an image")
    _downscale(junk)  # must not raise: a bad texture is not a build failure
    assert junk.read_text() == "this is not an image"
