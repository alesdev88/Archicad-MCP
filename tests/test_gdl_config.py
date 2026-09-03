"""Config parsing shared by the file loader and the inline tool argument."""

import json
from pathlib import Path

from archicad_mcp.gdl.config import (
    load_config,
    parse_objects,
    save_object_config,
)

SPEC = {
    "guid": "4E501AE2-172D-4F03-B248-C9C2DE3E641E",
    "source": "chair.obj",
    "textures": {"logo": "maps/logo.jpg"},
    "variants": [{"label": "Natural oak", "roles": {"face": "maps/oak.jpg"}},
                 {"label": "Black", "roles": {"face": [0.09, 0.09, 0.10]}}],
    "frame_variants": [{"label": "Black steel", "rgb": [0.10, 0.10, 0.11]}],
    "groups": {"m1_1": {"label": "Seat", "texture": "face",
                        "rgb": [0.72, 0.58, 0.40], "uv_rotate": 90}},
    "decimate": {"Powder Co": 6000},
}


def test_file_and_inline_parse_identically(tmp_path):
    path = tmp_path / "assets.json"
    path.write_text(json.dumps({"objects": {"Chair": SPEC}}))
    from_file = load_config(path)
    inline = parse_objects({"objects": {"Chair": SPEC}}, tmp_path)
    assert from_file == inline


def test_parse_resolves_paths_against_the_base(tmp_path):
    objects = parse_objects({"objects": {"Chair": SPEC}}, tmp_path)
    cfg = objects["Chair"]
    assert cfg.source == tmp_path / "chair.obj"
    assert cfg.textures["logo"] == tmp_path / "maps" / "logo.jpg"
    assert cfg.variants[0][1]["face"] == tmp_path / "maps" / "oak.jpg"
    assert cfg.variants[1][1]["face"] == (0.09, 0.09, 0.10)
    assert cfg.groups["m1_1"].uv_rotate == 90


def test_save_merges_without_dropping_other_objects(tmp_path):
    path = tmp_path / "assets.json"
    path.write_text(json.dumps({"objects": {"Stool": SPEC}}))
    save_object_config(path, "Chair", SPEC)
    raw = json.loads(path.read_text())
    assert set(raw["objects"]) == {"Stool", "Chair"}


def test_save_creates_the_file_when_absent(tmp_path):
    path = tmp_path / "assets.json"
    save_object_config(path, "Chair", SPEC)
    assert json.loads(path.read_text())["objects"]["Chair"] == SPEC


def test_save_replaces_an_existing_entry(tmp_path):
    path = tmp_path / "assets.json"
    save_object_config(path, "Chair", SPEC)
    save_object_config(path, "Chair", {**SPEC, "decimate": {}})
    assert json.loads(path.read_text())["objects"]["Chair"]["decimate"] == {}
