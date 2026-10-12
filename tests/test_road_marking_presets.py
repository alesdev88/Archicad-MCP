"""road_marking_presets.json: the Road Marking part's preset table, exported from its model
(Archicad GDL repo, road_marking/export_presets.py, which also checks this copy)."""
import json
from pathlib import Path

TABLE = json.loads((Path(__file__).parents[1] / "src" / "archicad_mcp" / "core" / "road_marking_presets.json")
                   .read_text(encoding="utf-8"))


def test_the_table_has_the_parts_38_presets_and_its_values():
    assert len(TABLE["presets"]) == 38
    assert TABLE["defaults"]["markingType"] == "5121 Broken divider, in settlements"
    assert "markingType" not in TABLE["settings"] and "nodeX" not in TABLE["settings"]
    assert set(TABLE["settings"]) | {"markingType"} == set(TABLE["defaults"])
    assert TABLE["letter_widths"] == {"A": 0.6, "B": 0.5, "I": 0.15, "S": 0.5, "T": 0.5, "U": 0.5, "X": 0.6}
    assert TABLE["values"]["layouts"][0] == "None" and "Both ends" in TABLE["values"]["aligns"]
    assert TABLE["values"]["letter_aligns"] == ["Start", "Centre", "End"]


def test_every_preset_sets_what_the_part_sets_within_its_ranges():
    v = TABLE["values"]
    for name, row in TABLE["presets"].items():
        assert set(row) <= set(TABLE["settings"]), name
        assert {"patternAlign", "wholeDashes", "paintSurface", "s1On", "s2On", "s3On",
                "bayLayout", "letterText"} <= set(row), name
        assert row["patternAlign"] in v["aligns"] and row["bayLayout"] in v["layouts"], name
        assert row["letterText"] in v["letter_texts"], name
        for k in (1, 2, 3):
            if f"s{k}Width" in row:
                assert row[f"s{k}Width"] >= 0.01 and row[f"s{k}Dash"] >= 0.01 and row[f"s{k}Gap"] >= 0, name
                assert -80 <= row[f"s{k}Angle"] <= 80 and row[f"s{k}Shape"] in v["shapes"], name
        if row["bayLayout"] != "None":
            assert row["bayDepth"] >= 0.3 and row["bayWidth"] >= 0.3, name
        if row["letterText"] != "None":
            assert row["letterLength"] >= 0.1 and row["letterAlign"] in v["letter_aligns"], name
