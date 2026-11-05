"""create_road_marking choosing between a Road Marking and a Parking Place, and placing a Parking Place."""
import json
import math

import pytest

from archicad_mcp.core import road_marking as rm
from tests import test_road_marking_tool as base
from tests.test_road_marking_tool import (LOOK, MESH, NEW, OK, PTS, SLAB, STORIES, _call_tool, _conn, _details,
                                           _marking, _named, _params, _reads, _writes)

PP = rm.PARTS[rm.PARKING_PLACE]
RM = rm.PARTS[rm.ROAD_MARKING]
LIB = {"libraryParts": [{"documentName": "Road Marking", "fileName": "Road Marking.gsm", "typeId": "Object"},
                        {"documentName": "Parking Place", "fileName": "Parking Place.gsm", "typeId": "Object"}]}
ONLY_RM = {"libraryParts": [LIB["libraryParts"][0]]}
ONLY_PP = {"libraryParts": [LIB["libraryParts"][1]]}
CREATED = {"CreateObjects": {"elements": [{"elementId": {"guid": "new"}}]}, "SetGDLParametersOfElements": OK}


def _gdl(**over):
    return base._gdl(PP, **over)


def _plain(**over):
    return base._plain(PP, **over)


def _after(held, writes):
    return base._after(held, writes, PP)


def placed_part(conn) -> str:
    """The library part the first CreateObjects call asked for."""
    placed = [p for c, p in conn._core.calls if c == "CreateObjects"][0]["objectsData"][0]
    return placed["libraryPartName"]


def _new(part=PP):
    return _conn({"GetAvailableLibraryParts": LIB, **CREATED, "GetGDLParametersOfElements": base._gdl(part)})


# ---------------------------------------------------------------- which part

def test_a_parking_name_places_a_parking_place():
    conn = _new(PP)
    r = rm.create_road_marking(conn, points=PTS, marking="Parking 60 degrees", dry_run=False)
    assert r["created"] and placed_part(conn) == "Parking Place"


def test_a_road_marking_name_places_a_road_marking():
    conn = _new(RM)
    r = rm.create_road_marking(conn, points=PTS, marking="5111 Solid divider", dry_run=False)
    assert r["created"] and placed_part(conn) == "Road Marking"


def test_no_name_means_a_road_marking_with_its_defaults():
    r = rm.create_road_marking(_conn({"GetAvailableLibraryParts": LIB}), points=PTS)
    assert r["marking"] == "5121 Broken divider, in settlements"


def test_the_parts_name_is_matched_without_regard_to_case():
    assert rm.part_of_name("parking 60 DEGREES") is PP and rm.part_of_name("5111 solid divider") is RM
    assert rm.part_of_name("Custom") is RM and rm.part_of_name(None) is RM and rm.part_of_name("5999") is RM


def test_a_new_parking_place_takes_its_own_defaults():
    # stale settings are written back to Parking Place's table, not Road Marking's
    stale = dict(showPlanFill=False, paintSurface=78, thickness=0.01, bayWidth=3.0, showSymbol=False)
    conn = _conn({"GetAvailableLibraryParts": LIB, **CREATED, "GetGDLParametersOfElements": _gdl(**stale)})
    rm.create_road_marking(conn, points=PTS, dry_run=False, marking="Parking 90 degrees")
    assert placed_part(conn) == "Parking Place"
    written = dict(_named(_writes(conn)[0]))
    assert written["bayWidth"] == PP.table["defaults"]["bayWidth"] and written["showSymbol"] is True
    assert written["paintSurface"] == PP.table["defaults"]["paintSurface"] and written["thickness"] == 0.003
    assert "letterText" not in written and "barWidth" not in written


def test_the_kerb_line_goes_through_stripes():
    conn = _new(PP)
    r = rm.create_road_marking(conn, points=PTS, marking="Parking 90 degrees", stripes={"3": {"on": True}},
                               dry_run=False)
    assert r["created"] is True
    written = dict(_named(_writes(conn)[0]))
    assert written["s3On"] is True
    assert not {k for k in written if k.startswith("s3")} - {"s3On"}


# ---------------------------------------------------------------- update

def test_update_uses_the_elements_own_part():
    for part, name, other in ((PP, "Parking Place", "Road Marking"), (RM, "Road Marking", "Parking Place")):
        held = _marking(name=name)
        conn = _conn({"GetDetailsOfElements": _details({"m-1": held}), "GetAvailableLibraryParts": LIB,
                      "SetGDLParametersOfElements": OK, "GetGDLParametersOfElements": base._gdl(part)})
        r = rm.create_road_marking(conn, update_guid="m-1", points=PTS, flip_sides=True, dry_run=False)
        assert r["updated"] is True and r["marking"] == part.table["defaults"]["markingType"]
        assert ("flipSides", True) in [n for w in _writes(conn) for n in _named(w)]
        assert not any(p.get("libraryPartName") == other for c, p in conn._core.calls if c == "CreateObjects")


def test_update_refuses_a_name_of_the_other_part():
    for name, other, held, want in (("5352 Disabled bay", "Parking Place", "Road Marking", RM),
                                    ("5111 Solid divider", "Road Marking", "Parking Place", PP)):
        conn = _conn({"GetDetailsOfElements": _details({"m-1": _marking(name=held)}),
                      "GetAvailableLibraryParts": LIB, "SetGDLParametersOfElements": OK,
                      "GetGDLParametersOfElements": base._gdl(want)})
        r = rm.create_road_marking(conn, update_guid="m-1", points=PTS, marking=name, dry_run=False)
        assert f"belongs to {other}" in r["error"] and "place a new one" in r["error"]
        assert held in r["error"] and not _writes(conn)


def test_update_refuses_any_other_library_part():
    beam = _conn({"GetDetailsOfElements": _details({"b-1": _marking(name="Swept Beam")}),
                  "SetGDLParametersOfElements": OK})
    r = rm.create_road_marking(beam, update_guid="b-1", points=PTS, dry_run=False)
    assert "Road Marking" in r["error"] and "Parking Place" in r["error"] and not _writes(beam)


def test_custom_is_valid_on_both_parts():
    for part, name in ((PP, "Parking Place"), (RM, "Road Marking")):
        conn = _conn({"GetDetailsOfElements": _details({"m-1": _marking(name=name)}),
                      "GetAvailableLibraryParts": LIB, "GetGDLParametersOfElements": base._gdl(part)})
        r = rm.create_road_marking(conn, update_guid="m-1", points=PTS, marking="custom")
        assert r["marking"] == "Custom" and "error" not in r


def test_unknown_names_list_both_parts_presets():
    r = rm.create_road_marking(_conn({"GetAvailableLibraryParts": LIB}), points=PTS, marking="5999")
    assert "Unknown marking '5999'" in r["error"]
    assert "5111 Solid divider" in r["error"] and "Parking 90 degrees" in r["error"] and "Custom" in r["error"]


# ---------------------------------------------------------------- refusals

def test_parking_place_refuses_lettering_and_bar_width():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    for kwargs in ({"lettering": {"text": "BUS"}}, {"lettering": {"offset": 1.0}}, {"bays": {"bar_width": 0.2}}):
        r = rm.create_road_marking(conn, points=PTS, marking="Parking 60 degrees", **kwargs)
        assert "Parking Place" in r["error"], kwargs


@pytest.mark.parametrize("stripes", [{"1": {"offset": 1}}, {"2": {"width": 1}}, {"3": {"dash": 1}},
                                     {"1": {"on": True}}, {"1": {"shape": "Bar"}}, {"2": {"angle": 10}}])
def test_parking_place_refuses_stripe_values_the_part_owns(stripes):
    r = rm.create_road_marking(_conn({"GetAvailableLibraryParts": LIB}), points=PTS, marking="Parking 60 degrees",
                               stripes=stripes)
    assert "Parking Place" in r["error"] and "set by the Parking Place part" in r["error"]


def test_parking_place_takes_the_stripe_values_it_leaves_to_the_call():
    r = rm.create_road_marking(_conn({"GetAvailableLibraryParts": LIB}), points=PTS, marking="Parking 60 degrees",
                               stripes={"1": {"width": 6.0, "dash": 0.12, "gap": 2.3, "angle": -20},
                                        "2": {"on": True}, "3": {"on": True}})
    assert "error" not in r
    assert _params(r)["s1Width"] == 6.0 and _params(r)["s2On"] is True and _params(r)["s3On"] is True


@pytest.mark.parametrize("key,value", [("width", 3.0), ("zone_width", 1.0), ("zone_first", False), ("symbol", False)])
def test_road_marking_refuses_the_bay_keys(key, value):
    r = rm.create_road_marking(_conn({"GetAvailableLibraryParts": LIB}), points=PTS,
                               marking="5333-2 Bus stop on the carriageway", bays={key: value})
    assert "Road Marking" in r["error"] and key in r["error"]


def test_both_ends_is_refused_on_parking_place():
    r = rm.create_road_marking(_conn({"GetAvailableLibraryParts": LIB}), points=PTS, marking="Parking 60 degrees",
                               pattern_align="Both ends")
    assert "patternAlign must be one of: Start, Centre, End" in r["error"]
    ok = rm.create_road_marking(_conn({"GetAvailableLibraryParts": LIB}), points=PTS, pattern_align="Both ends")
    assert "error" not in ok


def test_a_stop_still_takes_depth_and_bar_width():
    r = rm.create_road_marking(_conn({"GetAvailableLibraryParts": LIB}), points=PTS,
                               marking="5333-2 Bus stop on the carriageway", bays={"depth": 2.5, "bar_width": 0.2})
    assert "error" not in r and _params(r)["barWidth"] == 0.2


# ---------------------------------------------------------------- warnings and draping

def test_a_missing_part_warns_with_its_own_name():
    for part, loaded, marking in ((PP, ONLY_RM, "Parking 60 degrees"), (RM, ONLY_PP, "5111 Solid divider")):
        conn = _conn({"GetAvailableLibraryParts": loaded})
        r = rm.create_road_marking(conn, points=PTS, marking=marking)
        assert any(f"'{part.name}' is not loaded" in w for w in r["warnings"])
        w = rm.create_road_marking(conn, points=PTS, marking=marking, dry_run=False)
        assert f"'{part.name}' is not loaded" in w["error"] and not any(c == "CreateObjects" for c, _ in conn._core.calls)


def test_a_parking_row_with_bay_values_and_a_short_path_warns_of_the_overhang():
    conn = _conn({"GetDetailsOfElements": _details({"slab": SLAB}), "GetAvailableLibraryParts": LIB})
    r = rm.create_road_marking(conn, points=[{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}],
                               marking="5352-1 Disabled bays, shared zone", bays={"width": 2.4},
                               drape_on=["slab"], start_height=1.0)
    text = " ".join(r["warnings"])
    assert "replaces the source's own heights" in text and "start_height and slope_percent do not apply" in text
    assert "bay row is 6.30 m long" in text


def test_a_parking_row_drapes_on_the_saved_surfaces():
    conn = _conn({"GetDetailsOfElements": _details({"slab": SLAB}), "GetAvailableLibraryParts": LIB,
                  "CreateObjects": {"elements": [{"elementId": {"guid": "new"}}]},
                  "SetGDLParametersOfElements": OK,
                  "GetGDLParametersOfElements": _gdl(markingType="Parking 90 degrees")})
    r = rm.create_road_marking(conn, points=PTS, marking="Parking 90 degrees", stripes={"3": {"on": True}},
                               drape_on=["slab"], lift=0.002, dry_run=False)
    assert r["created"] is True and r["element"] == "new" and r["marking_after"] == "Parking 90 degrees"
    assert placed_part(conn) == "Parking Place"
    # the row reaches 5.0 m to the right of the path; the kerb line adds half its 0.10 m width to the left
    assert r["reach"] == {"u_lo": -0.05, "u_hi": 5.0}
    placed = [p for c, p in conn._core.calls if c == "CreateObjects"][0]["objectsData"][0]
    assert placed["coordinates"]["z"] == pytest.approx(3.252) and "floorIndex" not in placed
    written = {p["name"]: p["value"] for p in _writes(conn)[-1]}
    assert written["nodeZ"] == [0.0, 0.0] and written["s3On"] is True
    assert r["off_surface"] == [] and r["partly_off_surface"] == []


async def test_the_registered_tool_places_a_parking_place_from_json_text(monkeypatch):
    tapir = {"GetDetailsOfElements": _details({"slab": SLAB}), "GetAvailableLibraryParts": LIB}
    payload = await _call_tool(monkeypatch, tapir, {
        "points": json.dumps(PTS), "marking": "5351 Taxi bay", "bays": json.dumps({"depth": 4.5}),
        "lettering": json.dumps({}), "stripes": json.dumps({"1": {"width": 4.5}}),
        "drape_on": json.dumps(["slab"])})
    assert payload["dry_run"] is True and payload["reach"] == {"u_lo": 0.0, "u_hi": 4.55}
    assert _params(payload)["bayDepth"] == 4.5


# ---------------------------------------------------------------- the Object tool's stale settings

EVERYTHING_STALE = {
    **LOOK, "gripNodes": False, "gripCurves": False, "gripAdd": False, "flipSides": True, "patternAlign": "End",
    "patternShift": 1.5, "wholeDashes": False, "s1On": False, "s1Offset": 1.0, "s1Width": 0.5, "s1Dash": 2.0,
    "s1Gap": 3.0, "s1Angle": 20.0, "s1Shape": "Triangle, point left", "s2On": True, "s2Offset": 1.0, "s2Width": 0.5,
    "s2Dash": 2.0, "s2Gap": 3.0, "s2Angle": 20.0, "s2Shape": "Triangle, point left", "s3On": True, "s3Offset": 1.0,
    "s3Width": 0.5, "s3Dash": 2.0, "s3Gap": 3.0, "s3Angle": 20.0, "s3Shape": "Triangle, point left",
    "bayLayout": "Taxi bay", "bayWidth": 3.0, "bayDepth": 6.0, "zoneWidth": 2.0, "zoneFirst": False,
    "showSymbol": False, "showAxis": True, "axisPen": 5, "axisLineType": 2, "showNodeHeights": True, "textPen": 5,
    "textSizeMm": 3.5, "markingType": "Custom"}


def test_the_table_covers_the_look_settings():
    assert set(LOOK) <= set(PP.table["settings"]) and set(LOOK) <= set(PP.table["defaults"])
    assert set(EVERYTHING_STALE) - {"markingType"} == set(PP.table["settings"]) - {"pathTolerance"}
    assert all(EVERYTHING_STALE[k] != PP.table["defaults"][k] for k in EVERYTHING_STALE)


@pytest.mark.parametrize("marking", ["Parking 60 degrees", "Parking 90 degrees", "5354 EV charging bay"])
def test_every_stale_setting_gives_way_to_the_preset_the_call_names(marking):
    held = _plain(**EVERYTHING_STALE)
    conn = _conn({**NEW, "GetAvailableLibraryParts": LIB, "GetGDLParametersOfElements": _gdl(**EVERYTHING_STALE)})
    rm.create_road_marking(conn, points=PTS, marking=marking, dry_run=False)
    state = _after(held, _writes(conn)[0])
    want = {**PP.table["defaults"], **PP.presets[marking], "markingType": marking}
    assert {k: state[k] for k in want if k != "pathTolerance"} == {k: want[k] for k in want if k != "pathTolerance"}


def test_a_stale_preset_equal_to_the_calls_is_not_written_and_its_other_settings_are_corrected():
    conn = _conn({**NEW, "GetAvailableLibraryParts": LIB, "GetGDLParametersOfElements": _gdl(
        markingType="Parking 60 degrees", s1Offset=2.90, s1Width=5.80, s1Dash=0.10, s1Gap=2.40, s1Angle=-30.0,
        wholeDashes=True, s2Offset=5.80, s2Width=0.10, s2Dash=1.0, s2Gap=0.0, s2On=True, showPlanFill=False)})
    r = rm.create_road_marking(conn, points=PTS, marking="Parking 60 degrees", dry_run=False)
    written = dict(_named(_writes(conn)[0]))
    assert "markingType" not in written and "s1Angle" not in written       # s1Angle reads back in radians
    assert written["showPlanFill"] is True and written["s2On"] is False
    assert r["marking"] == "Parking 60 degrees"


def test_a_stale_angle_goes_back_in_the_radians_the_tapir_takes():
    conn = _conn({**NEW, "GetAvailableLibraryParts": LIB, "GetAddOnVersion": {"version": "1.7.3"},
                  "GetGDLParametersOfElements": _gdl(markingType="Parking 60 degrees", s1Angle=20.0)})
    rm.create_road_marking(conn, points=PTS, marking="Parking 60 degrees", dry_run=False)
    assert dict(_named(_writes(conn)[0]))["s1Angle"] == pytest.approx(math.radians(-30))
