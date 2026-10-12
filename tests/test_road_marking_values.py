"""create_road_marking's settings: presets, the call's changes, validation, the reach and the warnings."""
import re

import pytest

from archicad_mcp.core import road_marking as rm

DEFAULTS = rm.TABLE["defaults"]


def test_preset_names_match_without_regard_to_case():
    assert rm.preset_name("5352-1 disabled BAYS, shared zone") == "5352-1 Disabled bays, shared zone"
    assert rm.preset_name("custom") == "Custom"
    with pytest.raises(rm.RoadMarkingError, match=re.escape("Unknown marking '5999'. Valid: 5111 Solid divider")):
        rm.preset_name("5999")


def test_a_new_preset_applies_and_the_calls_values_go_on_top():
    want, state = rm.resolve(DEFAULTS, marking="5333-2 Bus stop on the carriageway", lettering={"offset": 1.75})
    assert state["letterOffset"] == 1.5 and want["letterOffset"] == 1.75
    assert want["bayLayout"] == "Stop end bars" and want["markingType"] == "5333-2 Bus stop on the carriageway"


def test_the_markings_own_preset_is_not_applied_again():
    current = dict(DEFAULTS, s1Width=0.12)
    want, _ = rm.resolve(current, marking="5121 broken divider, in settlements")
    assert want["s1Width"] == 0.12


def test_flip_shift_and_thickness_survive_a_new_preset():
    current = dict(DEFAULTS, flipSides=True, patternShift=1.0, thickness=0.005)
    want, _ = rm.resolve(current, marking="Parking 90 degrees")
    assert (want["flipSides"], want["patternShift"], want["thickness"]) == (True, 1.0, 0.005)
    assert want["s1Offset"] == 2.5


def test_stripes_bays_and_lettering_map_to_the_parts_parameters():
    want, _ = rm.resolve(DEFAULTS, stripes={"2": {"on": True, "offset": 0.3, "shape": "Triangle, point left"}},
                         bays={"layout": "Taxi bay", "depth": 4.5, "symbol": False},
                         lettering={"text": "BUS", "align": "Centre", "repeat": 20})
    assert (want["s2On"], want["s2Offset"], want["s2Shape"]) == (True, 0.3, "Triangle, point left")
    assert (want["bayLayout"], want["bayDepth"], want["showSymbol"]) == ("Taxi bay", 4.5, False)
    assert (want["letterText"], want["letterAlign"], want["letterRepeat"]) == ("BUS", "Centre", 20.0)


@pytest.mark.parametrize("kwargs,message", [
    ({"stripes": {"4": {"width": 0.1}}}, "stripes keys are '1', '2' and '3'"),
    ({"stripes": {"1": "wide"}}, "stripes['1'] must be an object"),
    ({"stripes": {"1": {"colour": "red"}}}, "Unknown stripe key 'colour'. Valid: on, offset"),
    ({"stripes": {"1": {"shape": "Circle"}}}, "s1Shape must be one of: Bar, Triangle, point left"),
    ({"stripes": {"1": {"angle": 85}}}, "s1Angle must be -80 to 80, got 85"),
    ({"stripes": {"1": {"width": "wide"}}}, "s1Width must be a number"),
    ({"stripes": {"1": {"on": 1}}}, "s1On must be true or false"),
    ({"bays": {"depth": 0.2}}, "bayDepth must be at least 0.3, got 0.2"),
    ({"bays": {"colour": "blue"}}, "Unknown bays key 'colour'. Valid: layout, width"),
    ({"bays": {"layout": "Garage"}}, "bayLayout must be one of: None, Disabled bay"),
    ({"lettering": {"text": "STOP"}}, "letterText must be one of: None, BUS, TAXI"),
    ({"lettering": {"align": "Both ends"}}, "letterAlign must be one of: Start, Centre, End"),
    ({"lettering": {"font": "Arial"}}, "Unknown lettering key 'font'"),
    ({"flat": {"thickness": 0.1}}, "thickness must be 0.0005 to 0.05, got 0.1"),
    ({"flat": {"patternAlign": "Middle"}}, "patternAlign must be one of: Start, Centre, End, Both ends"),
])
def test_bad_values_are_refused_with_the_valid_ones(kwargs, message):
    with pytest.raises(rm.RoadMarkingError, match=re.escape(message)):
        rm.resolve(DEFAULTS, **kwargs)


@pytest.mark.parametrize("marking,flip,reach", [
    ("5121 Broken divider, in settlements", False, (-0.075, 0.075)),
    ("5131 Double solid", False, (-0.225, 0.225)),
    ("5231 Pedestrian crossing", False, (-2.0, 2.0)),
    ("Parking 60 degrees", False, (0.0, 5.8)),
    ("Parking 60 degrees", True, (-5.8, 0.0)),
    ("5352-1 Disabled bays, shared zone", False, (0.0, 5.45)),
    ("5352-2 Disabled bay, parallel", True, (-2.45, 0.0)),
    ("5333-2 Bus stop on the carriageway", False, (-0.15, 3.05)),
    ("5111-1 Bus lane line", False, (-0.075, 2.8)),
    ("5111-1 Bus lane line", True, (-2.8, 0.075)),
    ("5334-1 Taxi stop on the carriageway", False, (-0.075, 2.55)),
])
def test_the_reach_covers_stripes_bays_and_the_word(marking, flip, reach):
    want, _ = rm.resolve(DEFAULTS, marking=marking, flat={"flipSides": flip})
    assert rm.reach(want) == pytest.approx(reach)


def test_a_bay_row_or_word_past_the_paths_ends_is_reported():
    want, _ = rm.resolve(DEFAULTS, marking="5352-1 Disabled bays, shared zone")
    assert rm.overhang(want, 10.0) == []
    notes = rm.overhang(want, 5.0)
    assert len(notes) == 1 and "bay row is 6.30 m long" in notes[0] and "1.30 m past the end" in notes[0]
    want, _ = rm.resolve(DEFAULTS, marking="5333 Bus stop in a lay-by")
    assert "The word BUS is 4.00 m long and reaches 0.50 m before the start" in rm.overhang(want, 3.0)[0]
    want, _ = rm.resolve(DEFAULTS, marking="5333-2 Bus stop on the carriageway")
    notes = rm.overhang(want, 1.0)
    assert len(notes) == 1 and "word" in notes[0]          # the stop's end bars stay inside the path


def test_bay_and_lettering_values_with_nothing_to_act_on_are_reported():
    want, _ = rm.resolve(DEFAULTS, bays={"depth": 4.0}, lettering={"offset": 2.0})
    notes = rm.no_effect(want, {"depth": 4.0}, {"offset": 2.0})
    assert len(notes) == 2 and "bay layout is None" in notes[0] and "word is None" in notes[1]
    want, _ = rm.resolve(DEFAULTS, bays={"layout": "Taxi bay", "depth": 4.0})
    assert rm.no_effect(want, {"layout": "Taxi bay", "depth": 4.0}, None) == []
