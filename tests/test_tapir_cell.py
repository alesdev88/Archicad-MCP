import math

import pytest

from archicad_mcp.extract import TAPIR_LIST_SEPARATOR, TAPIR_UNDEFINED_TEXT, tapir_cell


def d(value_type, collection="Single", measure="Default"):
    return {"propertyValueType": value_type, "propertyCollectionType": collection,
            "propertyMeasureType": measure}


def v(text):
    return {"propertyValue": {"value": text}, "isAvailable": True}


def test_constants():
    assert TAPIR_UNDEFINED_TEXT == "<Undefined>"
    assert TAPIR_LIST_SEPARATOR == "; "


@pytest.mark.parametrize("raw, definition, cell_type, value", [
    (v("0.29999999999999999"), d("Real", measure="Length"), "length", pytest.approx(0.3)),
    (v("6.3000000000000007"), d("Real", measure="Area"), "area", pytest.approx(6.3)),
    (v("11.890408816319191"), d("Real", measure="Volume"), "volume", pytest.approx(11.890408816)),
    (v("1.5"), d("Real"), "number", 1.5),
    (v("90"), d("Real", measure="Angle"), "angle", pytest.approx(math.pi / 2)),
    (v("23"), d("Integer"), "integer", 23),
    (v("True"), d("Boolean"), "boolean", True),
    (v("False"), d("Boolean"), "boolean", False),
    (v("SW - 032"), d("String"), "string", "SW - 032"),
    (v("8AAE8204-4D9F-44A7-A108-9F888AD60ED2"), d("Guid"), "string",
     "8AAE8204-4D9F-44A7-A108-9F888AD60ED2"),
    (v("Main Model"), d("String", "SingleChoiceEnumeration"), "string", "Main Model"),
    (v("51; 59"), d("Integer", "List"), "integerList", [51, 59]),
    (v(""), d("String", "List"), "stringList", []),
    (v("1.5; 2"), d("Real", "List", "Length"), "lengthList", [1.5, 2.0]),
    (v("A; B"), d("String", "MultipleChoiceEnumeration"), "multiEnum", ["A", "B"]),
    (v("True; False"), d("Boolean", "List"), "booleanList", [True, False]),
    (v("90; 180"), d("Real", "List", "Angle"), "angleList",
     [pytest.approx(math.pi / 2), pytest.approx(math.pi)]),
])
def test_a_value_becomes_a_typed_cell(raw, definition, cell_type, value):
    assert tapir_cell(raw, definition) == {
        "status": "normal", "type": cell_type, "value": value, "via": "tapir"}


@pytest.mark.parametrize("raw, status", [
    ({"error": {"code": -2130312908, "message": "m"}, "isAvailable": False}, "notAvailable"),
    ({"error": {"code": -2130312908, "message": "m"}, "isAvailable": True}, "notEvaluated"),
    ({"error": {"code": -2130312908, "message": "m"}}, "notAvailable"),
    (v("<Undefined>"), "userUndefined"),
])
def test_statuses(raw, status):
    for definition in (d("String"), d("Real", measure="Length"), d("Integer", "List")):
        assert tapir_cell(raw, definition) == {"status": status, "via": "tapir"}


@pytest.mark.parametrize("text, definition", [
    ("3,00", d("Real", measure="Length")), ("90°", d("Real", measure="Angle")),
    ("Da", d("Boolean")), ("", d("Integer")), ("1; x", d("Integer", "List")),
    ("nan", d("Real")), ("1_0", d("Integer")), ("Yes; True", d("Boolean", "List")),
])
def test_text_that_does_not_parse_is_kept_as_text(text, definition):
    assert tapir_cell(v(text), definition) == {
        "status": "normal", "type": "string", "value": text, "unparsed": True, "via": "tapir"}


def test_an_unknown_declared_type_is_text():
    assert tapir_cell(v("x"), {})["type"] == "string"
