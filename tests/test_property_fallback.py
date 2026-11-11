"""The Tapir fallback of fetch_property_cells: cells the official read refused
are filled from one Tapir GetPropertyValuesOfElements call per chunk."""
import pytest
from multiconn_archicad.errors import TapirCommandError

from archicad_mcp import extract
from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.extract import element_payload, fetch_property_cells, fetch_property_values
from tests.conftest import FakeCore

NAMES = ["General_Locked", "X/Layer"]
ID = {"General_Locked": {"guid": "pid-General_Locked"}, "X/Layer": {"guid": "pid-X/Layer"}}
UNKNOWN = "00000000-1111-2222-3333-444444444444"

_REFUSED_ELEMENT = {"error": {"code": 7203, "message": "Element not supported"}}
_REFUSED_DEFINITION = {"error": {"code": 6702, "message": "Property definition not supported"}}
_ANSWERED = {"propertyValue": {"type": "boolean", "status": "normal", "value": False}}
# label-1 is outside the official read; w-1 has General_Locked answered and X/Layer refused.
_OFFICIAL_ROWS = {"label-1": _REFUSED_ELEMENT,
                  "w-1": {"propertyValues": [_ANSWERED, _REFUSED_DEFINITION]}}
_ANSWERED_ROWS = {"w-1": {"propertyValues": [_ANSWERED, _ANSWERED]}}

# Upper case on purpose: GUIDs compare without case.
_DEFINITIONS = {"properties": [
    {"propertyId": {"guid": "PID-GENERAL_LOCKED"}, "propertyValueType": "Boolean",
     "propertyCollectionType": "Single", "propertyMeasureType": "Default"},
    {"propertyId": {"guid": "PID-X/LAYER"}, "propertyValueType": "String",
     "propertyCollectionType": "Single", "propertyMeasureType": "Default"},
]}
_TAPIR_VALUES = {"pid-General_Locked": "True", "pid-X/Layer": "A-WALL"}
_TAPIR_ELEMENT_ERROR = {"error": {"code": -2130312908,
                                  "message": "Failed to get property values for element"}}


def _tapir_answer(params):
    """Tapir's answer: every value of _TAPIR_VALUES, in the order asked."""
    row = {"propertyValues": [
        {"propertyValue": {"value": _TAPIR_VALUES[p["propertyId"]["guid"]]}, "isAvailable": True}
        for p in params["properties"]]}
    return {"propertyValuesForElements": [row for _ in params["elements"]]}


def _fake(official_rows=None, version="2.0.0", tapir=True, values=_tapir_answer,
          definitions=_DEFINITIONS):
    """A FakeCore whose official read answers `official_rows` (guid -> row),
    with Tapir `version` answering `definitions` and `values`."""
    official_rows = _OFFICIAL_ROWS if official_rows is None else official_rows

    def property_ids(params):
        out = []
        for item in params["properties"]:
            name = item.get("nonLocalizedName") or "/".join(item["localizedName"])
            out.append({"propertyId": {"guid": f"pid-{name}"}})
        return {"properties": out}

    def official_values(params):
        return {"propertyValuesForElements": [
            official_rows[e["elementId"]["guid"]] for e in params["elements"]]}

    tapir_responses = {"GetAddOnVersion": {"version": version},
                       "GetPropertyValuesOfElements": values}
    if definitions is not None:
        tapir_responses["GetAllProperties"] = definitions
    return FakeCore(official={
        "API.GetPropertyIds": property_ids,
        "API.GetPropertyValuesOfElements": official_values,
        "API.IsAddOnCommandAvailable": {"available": tapir},
    }, tapir=tapir_responses)


# What the official read leaves when Tapir may not fill it, and when Tapir was asked and
# did not answer.
_CLOSED_ELEMENT = {"status": "notRead", "reason": extract.NOT_READ_ELEMENT,
                   "cause": extract.NOT_READ_ELEMENT_CAUSE}
_CLOSED_DEFINITION = {"status": "notRead", "reason": extract.NOT_READ_DEFINITION,
                      "cause": extract.NOT_READ_DEFINITION_CAUSE}
_SILENT_ELEMENT = {"status": "notRead",
                   "reason": extract.NOT_READ_ELEMENT_CAUSE + extract.NOT_READ_TAPIR_SILENT,
                   "cause": extract.NOT_READ_ELEMENT_CAUSE}
_SILENT_DEFINITION = {"status": "notRead",
                      "reason": extract.NOT_READ_DEFINITION_CAUSE + extract.NOT_READ_TAPIR_SILENT,
                      "cause": extract.NOT_READ_DEFINITION_CAUSE}


def _conn(core):
    return ArchicadConnection(19723, core=core)


def tapir_calls(core):
    """Parameters of each Tapir value call (the official one is API.-prefixed)."""
    return [p for c, p in core.calls if c == "GetPropertyValuesOfElements"]


def _tapir_commands(core):
    return [c for c, _ in core.calls
            if c in ("GetAddOnVersion", "GetAllProperties", "GetPropertyValuesOfElements")]


def test_the_agreed_constants():
    assert extract.TAPIR_VALUE_FALLBACK_SINCE == (2, 0, 0)
    assert extract.NOT_READ_UNKNOWN_ID == "the property id is not among the project's definitions"
    assert extract.NOT_READ_TAPIR_ELEMENT == "Tapir could not read this element either: "


def test_it_fills_a_refused_element_and_a_refused_definition():
    cells = fetch_property_cells(_conn(_fake()), ["label-1", "w-1"], NAMES, tapir_fallback=True)
    assert cells["label-1"]["General_Locked"] == {
        "status": "normal", "type": "boolean", "value": True, "via": "tapir"}
    assert cells["label-1"]["X/Layer"] == {
        "status": "normal", "type": "string", "value": "A-WALL", "via": "tapir"}
    assert cells["w-1"]["X/Layer"] == {
        "status": "normal", "type": "string", "value": "A-WALL", "via": "tapir"}


def test_an_official_answer_is_never_replaced():
    cells = fetch_property_cells(_conn(_fake()), ["label-1", "w-1"], NAMES, tapir_fallback=True)
    assert cells["w-1"]["General_Locked"] == {"type": "boolean", "status": "normal", "value": False}


def test_the_tapir_call_carries_both_options_and_only_the_gaps():
    core = _fake()
    fetch_property_cells(_conn(core), ["label-1", "w-1"], NAMES, tapir_fallback=True)
    assert len(tapir_calls(core)) == 1
    sent = tapir_calls(core)[0]
    assert sent["includeAvailability"] is True and sent["useSIUnits"] is True
    assert sent["elements"] == element_payload(["label-1", "w-1"])
    assert sent["properties"] == [{"propertyId": ID["General_Locked"]}, {"propertyId": ID["X/Layer"]}]


def test_an_element_without_a_gap_is_not_sent():
    core = _fake(official_rows={**_OFFICIAL_ROWS, **_ANSWERED_ROWS})
    fetch_property_cells(_conn(core), ["label-1", "w-1"], NAMES, tapir_fallback=True)
    assert tapir_calls(core)[0]["elements"] == element_payload(["label-1"])


def test_a_property_without_a_gap_is_not_sent():
    core = _fake(official_rows={"w-1": _OFFICIAL_ROWS["w-1"]})
    fetch_property_cells(_conn(core), ["w-1"], NAMES, tapir_fallback=True)
    assert tapir_calls(core)[0]["properties"] == [{"propertyId": ID["X/Layer"]}]


@pytest.mark.parametrize("setup", ["flag_off", "tapir_1_8_0", "no_tapir", "no_version"])
def test_without_the_gate_tapir_is_not_asked(setup):
    core = {"flag_off": _fake(), "tapir_1_8_0": _fake(version="1.8.0"),
            "no_tapir": _fake(tapir=False), "no_version": _fake(version=None)}[setup]
    cells = fetch_property_cells(_conn(core), ["label-1", "w-1"], NAMES,
                                 tapir_fallback=setup != "flag_off")
    assert tapir_calls(core) == []
    assert "GetAllProperties" not in [c for c, _ in core.calls]
    assert cells["label-1"]["General_Locked"] == _CLOSED_ELEMENT
    assert cells["w-1"]["X/Layer"] == _CLOSED_DEFINITION


def test_no_gap_means_no_tapir_call_at_all():
    core = _fake(official_rows=_ANSWERED_ROWS)
    report = {}
    fetch_property_cells(_conn(core), ["w-1"], NAMES, tapir_fallback=True, report=report)
    assert _tapir_commands(core) == []
    assert report == {"tapir_elements": 0}


def test_the_gate_and_the_definitions_are_asked_once_over_chunks(monkeypatch):
    monkeypatch.setattr(extract, "PROPERTY_FETCH_CHUNK", 1)
    core = _fake()
    report = {}
    fetch_property_cells(_conn(core), ["label-1", "w-1"], NAMES, tapir_fallback=True, report=report)
    commands = [c for c, _ in core.calls]
    assert commands.count("GetAddOnVersion") == 1
    assert commands.count("GetAllProperties") == 1
    assert [p["elements"] for p in tapir_calls(core)] == [
        element_payload(["label-1"]), element_payload(["w-1"])]
    assert report == {"tapir_elements": 2}


def test_a_closed_gate_is_asked_once_over_chunks(monkeypatch):
    monkeypatch.setattr(extract, "PROPERTY_FETCH_CHUNK", 1)
    core = _fake(version="1.8.0")
    fetch_property_cells(_conn(core), ["label-1", "w-1"], NAMES, tapir_fallback=True)
    assert [c for c, _ in core.calls].count("GetAddOnVersion") == 1


def test_an_id_outside_the_definitions_is_not_sent():
    core = _fake()
    cells = fetch_property_cells(_conn(core), ["label-1", "w-1"], NAMES + [UNKNOWN],
                                 tapir_fallback=True)
    sent = tapir_calls(core)[0]["properties"]
    assert {"propertyId": {"guid": UNKNOWN}} not in sent
    assert sent == [{"propertyId": ID["General_Locked"]}, {"propertyId": ID["X/Layer"]}]
    assert cells["label-1"][UNKNOWN] == {"status": "notRead", "reason": extract.NOT_READ_UNKNOWN_ID}
    assert cells["label-1"]["General_Locked"]["status"] == "normal"


def test_only_unknown_ids_send_no_value_call():
    core = _fake()
    cells = fetch_property_cells(_conn(core), ["label-1"], [UNKNOWN], tapir_fallback=True)
    assert tapir_calls(core) == []
    assert cells["label-1"][UNKNOWN] == {"status": "notRead", "reason": extract.NOT_READ_UNKNOWN_ID}


def test_a_tapir_error_for_one_element_names_it():
    def answer(params):
        good = _tapir_answer(params)["propertyValuesForElements"][1]
        return {"propertyValuesForElements": [_TAPIR_ELEMENT_ERROR, good]}
    cells = fetch_property_cells(_conn(_fake(values=answer)), ["label-1", "w-1"], NAMES,
                                 tapir_fallback=True)
    assert cells["label-1"]["General_Locked"] == {
        "status": "notRead",
        "reason": extract.NOT_READ_TAPIR_ELEMENT + "Failed to get property values for element"}
    assert "cause" not in cells["label-1"]["X/Layer"]
    assert cells["w-1"]["X/Layer"]["status"] == "normal"
    assert cells["w-1"]["General_Locked"] == {"type": "boolean", "status": "normal", "value": False}


def test_an_unavailable_value_is_typed_by_tapir_cell():
    def answer(params):
        item = {"error": {"code": -2130312908, "message": "Not available or not evaluated property"},
                "isAvailable": False}
        return {"propertyValuesForElements": [
            {"propertyValues": [item for _ in params["properties"]]} for _ in params["elements"]]}
    cells = fetch_property_cells(_conn(_fake(values=answer)), ["label-1"], NAMES,
                                 tapir_fallback=True)
    assert cells["label-1"]["General_Locked"] == {"status": "notAvailable", "via": "tapir"}


def _raises(params):
    raise TapirCommandError(message="boom", code=1)


@pytest.mark.parametrize("failing", ["values", "definitions", "definitions_missing"])
def test_tapir_failing_keeps_the_official_answers_and_says_tapir_was_silent(failing):
    core = {"values": _fake(values=_raises),
            "definitions": _fake(definitions=_raises),
            "definitions_missing": _fake(definitions=None)}[failing]
    report = {}
    cells = fetch_property_cells(_conn(core), ["label-1", "w-1"], NAMES, tapir_fallback=True,
                                 report=report)
    assert cells["w-1"]["General_Locked"]["value"] is False
    assert cells["label-1"]["General_Locked"] == _SILENT_ELEMENT
    assert cells["w-1"]["X/Layer"] == _SILENT_DEFINITION
    if failing != "values":
        assert tapir_calls(core) == []
        assert report == {"tapir_elements": 0}


def test_archicad_going_away_is_not_swallowed():
    def gone(params):
        raise ArchicadUnavailableError("gone")
    with pytest.raises(ArchicadUnavailableError):
        fetch_property_cells(_conn(_fake(values=gone)), ["label-1"], NAMES, tapir_fallback=True)


def test_a_short_tapir_answer_in_rows_leaves_the_rest_not_read():
    def answer(params):
        return {"propertyValuesForElements": _tapir_answer(params)["propertyValuesForElements"][:1]}
    cells = fetch_property_cells(_conn(_fake(values=answer)), ["label-1", "w-1"], NAMES,
                                 tapir_fallback=True)
    assert cells["label-1"]["X/Layer"]["status"] == "normal"
    assert cells["w-1"]["X/Layer"] == _SILENT_DEFINITION


def test_a_short_tapir_answer_in_values_leaves_the_rest_not_read():
    def answer(params):
        rows = _tapir_answer(params)["propertyValuesForElements"]
        return {"propertyValuesForElements": [{"propertyValues": r["propertyValues"][:1]}
                                              for r in rows]}
    cells = fetch_property_cells(_conn(_fake(values=answer)), ["label-1", "w-1"], NAMES,
                                 tapir_fallback=True)
    assert cells["label-1"]["General_Locked"]["status"] == "normal"
    assert cells["label-1"]["X/Layer"] == _SILENT_ELEMENT
    assert cells["w-1"]["X/Layer"] == _SILENT_DEFINITION


@pytest.mark.parametrize("answer", [
    {},
    {"propertyValuesForElements": None},
    {"propertyValuesForElements": ["oops", {"propertyValues": "oops"}]},
    {"propertyValuesForElements": [{"propertyValues": ["oops", {"propertyValue": "oops"}]},
                                   {"error": "oops"}]},
])
def test_a_malformed_tapir_answer_leaves_the_gaps_not_read(answer):
    cells = fetch_property_cells(_conn(_fake(values=answer)), ["label-1", "w-1"], NAMES,
                                 tapir_fallback=True)
    assert cells["label-1"]["General_Locked"] == _SILENT_ELEMENT
    assert cells["label-1"]["X/Layer"] == _SILENT_ELEMENT
    assert cells["w-1"]["X/Layer"] == _SILENT_DEFINITION


def test_the_report_counts_elements_sent_to_tapir():
    report = {}
    fetch_property_cells(_conn(_fake()), ["label-1", "w-1"], NAMES, tapir_fallback=True,
                         report=report)
    assert report == {"tapir_elements": 2}


def test_fetch_property_values_passes_the_flag():
    conn = _conn(_fake())
    assert fetch_property_values(conn, ["label-1"], ["General_Locked"], tapir_fallback=True) == {
        "label-1": {"General_Locked": True}}


def test_a_gate_closed_reason_names_the_tapir_that_would_read_it():
    cells = fetch_property_cells(_conn(_fake(version="1.8.0")), ["label-1", "w-1"], NAMES,
                                 tapir_fallback=True)
    assert cells["label-1"]["General_Locked"]["reason"].endswith("; Tapir 2.0.0 or newer reads them")
    assert cells["w-1"]["X/Layer"]["reason"].endswith("; Tapir 2.0.0 or newer reads it")


def test_with_the_gate_open_a_filled_cell_carries_no_reason():
    cells = fetch_property_cells(_conn(_fake()), ["label-1", "w-1"], NAMES, tapir_fallback=True)
    assert "reason" not in cells["label-1"]["General_Locked"]
    assert "cause" not in cells["w-1"]["X/Layer"]


def test_a_silent_tapir_does_not_say_tapir_would_read_it():
    cells = fetch_property_cells(_conn(_fake(values=_raises)), ["label-1"], NAMES,
                                 tapir_fallback=True)
    reason = cells["label-1"]["General_Locked"]["reason"]
    assert reason.endswith("; Tapir did not answer it either")
    assert "Tapir 2.0.0" not in reason
