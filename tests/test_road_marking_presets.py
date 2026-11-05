"""road_marking_presets.json and road_parking_presets.json: the two parts' preset tables, exported from their
models (Archicad GDL repo, road_marking/export_presets.py and road_parking/export_presets.py, which also check
these copies)."""
import json
from pathlib import Path

CORE = Path(__file__).parents[1] / "src" / "archicad_mcp" / "core"
TABLE = json.loads((CORE / "road_marking_presets.json").read_text(encoding="utf-8"))
PARKING = json.loads((CORE / "road_parking_presets.json").read_text(encoding="utf-8"))


def test_the_table_has_the_parts_28_presets_and_its_values():
    assert len(TABLE["presets"]) == 28
    assert TABLE["defaults"]["markingType"] == "5121 Broken divider, in settlements"
    assert "markingType" not in TABLE["settings"] and "nodeX" not in TABLE["settings"]
    assert set(TABLE["settings"]) | {"markingType"} == set(TABLE["defaults"])
    assert TABLE["letter_widths"] == {"A": 0.6, "B": 0.5, "I": 0.15, "S": 0.5, "T": 0.5, "U": 0.5, "X": 0.6}
    assert TABLE["values"]["layouts"] == ["None", "Stop end bars"] and "Both ends" in TABLE["values"]["aligns"]
    assert TABLE["values"]["letter_aligns"] == ["Start", "Centre", "End"]
    assert not {"bayWidth", "zoneWidth", "zoneFirst", "showSymbol"} & set(TABLE["settings"])


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
            assert row["bayDepth"] >= 0.3 and row["barWidth"] >= 0.01, name
        if row["letterText"] != "None":
            assert row["letterLength"] >= 0.1 and row["letterAlign"] in v["letter_aligns"], name


def test_the_parking_table_has_the_parts_ten_presets_and_its_values():
    assert len(PARKING["presets"]) == 10
    assert PARKING["defaults"]["markingType"] == "Parking 90 degrees"
    assert "markingType" not in PARKING["settings"] and "nodeX" not in PARKING["settings"]
    assert set(PARKING["settings"]) | {"markingType"} == set(PARKING["defaults"])
    assert PARKING["values"]["layouts"] == ["None", "Disabled bay", "Disabled bays, shared zone",
                                            "Disabled bay, parallel", "Taxi bay", "EV charging bay"]
    assert PARKING["values"]["aligns"] == ["Start", "Centre", "End"]
    assert not [k for k in (*PARKING, *PARKING["values"]) if k.startswith("letter")]
    assert not {"letterText", "letterLength", "barWidth"} & set(PARKING["settings"])
    assert {"bayWidth", "zoneWidth", "zoneFirst", "showSymbol"} <= set(PARKING["settings"])
    assert not set(PARKING["presets"]) & set(TABLE["presets"])


def test_every_parking_preset_sets_what_the_part_sets_within_its_ranges():
    v = PARKING["values"]
    for name, row in PARKING["presets"].items():
        assert set(row) <= set(PARKING["settings"]), name
        assert {"patternAlign", "wholeDashes", "paintSurface", "s1On", "s2On", "s3On", "bayLayout"} <= set(row), name
        assert row["patternAlign"] in v["aligns"] and row["bayLayout"] in v["layouts"], name
        for k in (1, 2, 3):
            if f"s{k}Width" in row:
                assert row[f"s{k}Width"] >= 0.01 and row[f"s{k}Dash"] >= 0.01 and row[f"s{k}Gap"] >= 0, name
                assert -80 <= row[f"s{k}Angle"] <= 80 and row[f"s{k}Shape"] in v["shapes"], name
        if row["bayLayout"] != "None":
            assert row["bayDepth"] >= 0.3 and row["bayWidth"] >= 0.3, name
    kerb = [n for n, row in PARKING["presets"].items() if row["bayLayout"] == "None"]
    assert len(kerb) == 5 and all(PARKING["presets"][n]["s3On"] is False and PARKING["presets"][n]["s3Width"] == 0.1
                                  for n in kerb)
