"""GDL, marker and IFC value rules: what fits, what goes to Tapir, what matches."""
import math

import pytest

from archicad_mcp.core import element_values as ev


def _entry(type_, value=None, **extra):
    return {"name": "p", "type": type_, "value": value, **extra}


def _no_attributes(attribute_type, name):
    raise ValueError(f"no {attribute_type} named '{name}'")


def _fit(entry, value, *, limit=ev.GDL_STRING_LIMIT, arrays=True, resolve=_no_attributes):
    return ev.fit(entry, value, string_limit=limit, arrays=arrays, resolve_attribute=resolve)


@pytest.mark.parametrize("type_,value,sent", [
    ("Length", 0.9, 0.9),
    ("Length", 1, 1.0),
    ("Angle", math.pi / 2, math.pi / 2),
    ("RealNumber", 2.5, 2.5),
    ("Integer", 2, 2),
    ("Integer", 2.0, 2),
    ("PenColor", 5, 5),
    ("Boolean", True, True),
    ("LightSwitch", "Off", "Off"),
    ("LightSwitch", True, "On"),
    ("String", "001", "001"),
    ("Title", "x", "x"),
    ("Material", 12, 12),
])
def test_values_that_fit(type_, value, sent):
    out, reason = _fit(_entry(type_), value)
    assert reason is None
    assert out == sent and type(out) is type(sent)


@pytest.mark.parametrize("type_,value", [
    ("Integer", True),
    ("Integer", 2.5),
    ("Integer", "2"),
    ("Length", "0.9"),
    ("Length", False),
    ("Boolean", "yes"),
    ("Boolean", 1),
    ("LightSwitch", "on"),
    ("String", 12),
    ("Dictionary", {"a": 1}),
    ("UNKNOWN", 1),
])
def test_values_that_do_not_fit_give_a_reason(type_, value):
    _, reason = _fit(_entry(type_), value)
    assert reason is not None


def test_strings_stop_at_the_limit():
    assert _fit(_entry("String"), "x" * 511)[1] is None
    _, reason = _fit(_entry("String"), "x" * 512)
    assert "511" in reason
    _, reason = _fit(_entry("String"), "x" * 256, limit=ev.MARKER_STRING_LIMIT)
    assert "255" in reason


def test_an_attribute_parameter_takes_a_name():
    out, reason = _fit(_entry("Material"), "Paint - White",
                       resolve=lambda kind, name: 12 if kind == "Surface" else 0)
    assert (out, reason) == (12, None)


def test_an_unknown_attribute_name_gives_the_resolver_reason():
    _, reason = _fit(_entry("FillPattern"), "No Such Fill")
    assert reason == "no Fill named 'No Such Fill'"


def test_an_array_takes_the_whole_array_of_its_own_size():
    # Tapir 1.7.2 writes arrays item by item and cannot resize them.
    entry = _entry("Length", [0.1, 0.2, 0.3], dimension1=3, dimension2=1)
    assert _fit(entry, [1, 2, 3]) == ([1.0, 2.0, 3.0], None)
    _, reason = _fit(entry, [1, 2, 3, 4])
    assert "3 items" in reason
    _, reason = _fit(entry, 0.5)
    assert "whole array" in reason


def test_rows_of_one_for_a_one_column_array_are_read_flat():
    entry = _entry("Length", [0.1, 0.2], dimension1=2, dimension2=1)
    assert _fit(entry, [[1], [2]]) == ([1.0, 2.0], None)


def test_a_two_dimensional_array_takes_rows_of_its_own_shape():
    entry = _entry("Integer", [1, 2, 3, 4], dimension1=2, dimension2=2)
    assert _fit(entry, [[1, 2], [3, 4]]) == ([[1, 2], [3, 4]], None)
    for value in ([[1, 2], [3]], [1, 2, 3, 4], [[1, 2, 3], [4, 5, 6]], [[1, 2]]):
        _, reason = _fit(entry, value)
        assert "2 x 2" in reason
    _, reason = _fit(entry, [[1, "x"], [3, 4]])
    assert reason is not None


def test_a_single_value_parameter_refuses_a_list():
    _, reason = _fit(_entry("Length", 0.9), [1, 2])
    assert "not an array" in reason


def test_arrays_can_be_refused_outright():
    entry = _entry("Length", [0.1], dimension1=1, dimension2=1)
    _, reason = _fit(entry, [0.2], arrays=False)
    assert "array" in reason


def test_read_value_reshapes_two_dimensional_arrays():
    assert ev.read_value(_entry("Integer", [1, 2, 3, 4], dimension1=2, dimension2=2)) == \
        [[1, 2], [3, 4]]
    assert ev.read_value(_entry("Integer", [1, 2, 3], dimension1=3, dimension2=1)) == [1, 2, 3]
    assert ev.read_value(_entry("Length", 0.9)) == 0.9


def test_a_single_gdl_angle_goes_to_tapir_in_degrees():
    # ACAPI_LibraryPart_ChangeAParameter takes a single Angle in degrees and
    # the parameter reads back in radians (swept beam findings, 29.09.2026).
    assert ev.to_tapir(_entry("Angle"), math.pi / 2, "gdl") == pytest.approx(90.0)


def test_from_tapir_1_7_3_a_single_gdl_angle_goes_in_radians():
    # Tapir #1405 turns it into degrees for ChangeAParameter itself.
    assert ev.to_tapir(_entry("Angle"), math.pi / 2, "gdl", radians=True) == math.pi / 2


def test_angle_arrays_and_marker_angles_go_in_radians():
    # Whole arrays and marker values are written straight into the parameter
    # list, which holds radians.
    array = _entry("Angle", [0.0], dimension1=1, dimension2=1)
    assert ev.to_tapir(array, [math.pi], "gdl") == [math.pi]
    assert ev.to_tapir(_entry("Angle"), math.pi, "marker") == math.pi
    assert ev.to_tapir(_entry("Length"), 0.9, "gdl") == 0.9


def test_warnings_name_a_locked_parameter_and_an_unlisted_value():
    assert any("locked" in w for w in ev.warnings(_entry("Integer", 1, isLocked=True), 2))
    listed = _entry("Integer", 1, possibleValues=[{"value": 1}, {"value": 2}],
                    canHaveCustomValue=False)
    assert ev.warnings(listed, 2) == []
    [warning] = ev.warnings(listed, 3)
    assert "1, 2" in warning
    custom = _entry("Integer", 1, possibleValues=[{"value": 1}], canHaveCustomValue=True)
    assert ev.warnings(custom, 3) == []


def test_a_text_value_list_comes_as_plain_strings():
    # Tapir reads a String parameter's value list as plain strings.
    listed = _entry("String", "Modern", possibleValues=["Modern", "Classic"],
                    canHaveCustomValue=False)
    assert ev.warnings(listed, "Classic") == []
    [warning] = ev.warnings(listed, "Rustic")
    assert "Modern, Classic" in warning


def test_range_entries_are_not_a_value_list():
    ranged = _entry("Length", 1.0, possibleValues=[{"value": 0.5, "flag": "GreaterThan"}],
                    canHaveCustomValue=False)
    assert ev.warnings(ranged, 0.1) == []


def test_same_compares_numbers_with_a_tolerance():
    assert ev.same(0.9, 0.9000004)
    assert not ev.same(0.9, 0.91)
    assert not ev.same(True, 1)
    assert ev.same([[1, 2], [3, 4]], [[1, 2], [3, 4.0000001]])
    assert not ev.same([1, 2], [1, 2, 3])
    assert ev.same("On", "On") and not ev.same("On", "Off")


@pytest.mark.parametrize("value,ifc_type", [
    ("EI30", "IfcLabel"), (True, "IfcBoolean"), (3, "IfcInteger"), (2.5, "IfcReal"),
])
def test_ifc_type_follows_the_value(value, ifc_type):
    assert ev.ifc_type_for(value) == ifc_type


def test_ifc_type_for_something_else_is_none():
    assert ev.ifc_type_for([1]) is None


@pytest.mark.parametrize("value,ifc_type,ok", [
    (True, "IfcBoolean", True),
    ("Unknown", "IfcLogical", True),
    ("yes", "IfcBoolean", False),
    (3, "IfcCountMeasure", True),
    (2.5, "IfcInteger", False),
    (0.3, "IfcThermalTransmittanceMeasure", True),
    ("x", "IfcDescriptiveMeasure", True),
    ("x", "IfcReal", False),
    (3, "IfcLabel", False),
])
def test_ifc_fit(value, ifc_type, ok):
    assert (ev.ifc_fit(value, ifc_type) is None) is ok


def test_ifc_same_reads_text():
    assert ev.ifc_same(True, "IfcBoolean", "True")
    assert not ev.ifc_same(False, "IfcBoolean", "True")
    assert ev.ifc_same(2.5, "IfcReal", "2.500000")
    assert ev.ifc_same(3, "IfcInteger", "3")
    assert ev.ifc_same("EI30", "IfcLabel", "EI30")
    assert not ev.ifc_same(2.5, "IfcReal", "n/a")
