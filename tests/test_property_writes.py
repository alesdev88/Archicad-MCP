"""Planning and sending property writes, shared by set_element_data and scripts."""
import pytest

from multiconn_archicad.errors import StandardAPIError

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.element_data import (
    cap_list,
    plan_property_writes,
    send_property_writes,
    set_element_data,
    value_fit,
)
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def _conn_with_cells(cells: dict, set_results=None):
    """A connection whose property cells are exactly `cells`.

    cells maps (guid, "Group/Name") to a propertyValue dict, or to None for a
    property that is not available on that element.
    """
    official = dict(api_replays.OFFICIAL)

    def values(params):
        rows = []
        for el in params["elements"]:
            row = []
            for p in params["properties"]:
                name = p["propertyId"]["guid"].removeprefix("pid-")
                cell = cells.get((el["elementId"]["guid"], name))
                row.append({"error": {"code": 1, "message": "n/a"}} if cell is None
                           else {"propertyValue": cell})
            rows.append({"propertyValues": row})
        return {"propertyValuesForElements": rows}

    official["API.GetPropertyValuesOfElements"] = values
    official["API.SetPropertyValuesOfElements"] = set_results or (
        lambda p: {"executionResults": [{"success": True}
                                        for _ in p["elementPropertyValues"]]})
    core = FakeCore(official=official, tapir=dict(api_replays.TAPIR))
    return ArchicadConnection(19723, core=core), core


@pytest.mark.parametrize("value_type,value,sent", [
    ("string", "001", "001"),
    ("integer", 12, 12),
    ("integer", "12", 12),
    ("integer", "-5", -5),
    ("number", 2, 2.0),
    ("length", 0.9, 0.9),
    ("boolean", True, True),
    ("area", 3.5, 3.5),
])
def test_values_that_fit_are_sent_in_the_property_type(value_type, value, sent):
    out, reason = value_fit(value_type, value)
    assert reason is None
    assert out == sent and type(out) is type(sent)


@pytest.mark.parametrize("value_type,value", [
    ("integer", "001"),   # the CVP case: a leading zero cannot survive an Integer
    ("integer", "12a"),
    ("integer", True),
    ("string", 12),
    ("number", "2.5"),
    ("boolean", "yes"),
])
def test_values_that_do_not_fit_are_refused_with_a_reason(value_type, value):
    _, reason = value_fit(value_type, value)
    assert reason is not None
    assert value_type in reason and repr(value) in reason


def test_unknown_types_pass_through_unchecked():
    assert value_fit("someFutureType", object) == (object, None)


def test_plan_builds_typed_payloads_and_keeps_the_current_value():
    conn, core = _conn_with_cells({
        ("d-1", "D/Pozicija"): {"type": "integer", "status": "normal", "value": 0}})
    planned, skipped = plan_property_writes(conn, [
        {"guid": "d-1", "property": "D/Pozicija", "value": "12"}])
    assert skipped == []
    assert planned == [{
        "guid": "d-1", "property": "D/Pozicija", "current": 0, "new": 12,
        "payload": {"elementId": {"guid": "d-1"},
                    "propertyId": {"guid": "pid-D/Pozicija"},
                    "propertyValue": {"type": "integer", "status": "normal",
                                      "value": 12}}}]
    assert not any(c == "API.SetPropertyValuesOfElements" for c, _ in core.calls)


def test_plan_skips_each_unwritable_change_with_its_reason():
    conn, _ = _conn_with_cells({
        ("d-1", "D/Pozicija"): {"type": "integer", "status": "normal", "value": 0},
        ("d-1", "D/Finish"): {"type": "singleEnum", "status": "normal"},
        ("d-2", "D/Pozicija"): None})
    planned, skipped = plan_property_writes(conn, [
        {"guid": "d-1", "property": "D/Pozicija", "value": "001"},
        {"guid": "d-1", "property": "D/Finish", "value": "Oak"},
        {"guid": "d-2", "property": "D/Pozicija", "value": 3}])
    assert planned == []
    reasons = {(s["guid"], s["property"]): s["reason"] for s in skipped}
    assert "'001'" in reasons[("d-1", "D/Pozicija")]
    assert "enum" in reasons[("d-1", "D/Finish")]
    assert "available" in reasons[("d-2", "D/Pozicija")]


def test_plan_skips_a_property_name_that_does_not_resolve():
    conn, core = _conn_with_cells({})
    core.official_responses["API.GetPropertyIds"] = {
        "properties": [{"error": {"code": 1, "message": "not found"}}]}
    planned, skipped = plan_property_writes(conn, [
        {"guid": "d-1", "property": "No/Such", "value": "x"}])
    assert planned == []
    assert "resolve" in skipped[0]["reason"]


def _planned(n):
    return [{"guid": f"g-{i}", "property": "D/P", "current": None, "new": "x",
             "payload": {"elementId": {"guid": f"g-{i}"}}} for i in range(n)]


def test_send_batches_and_reports_each_refused_element():
    def set_results(params):
        items = params["elementPropertyValues"]
        return {"executionResults": [
            {"success": False, "error": {"code": 6001,
                                         "message": "TeamWork permission denied"}}
            if item["elementId"]["guid"] == "g-500" else {"success": True}
            for item in items]}

    conn, core = _conn_with_cells({}, set_results=set_results)
    applied, failed, error = send_property_writes(conn, _planned(501))
    assert error is None
    assert applied == 500
    assert failed == [{"guid": "g-500", "property": "D/P", "code": 6001,
                       "message": "TeamWork permission denied"}]
    sends = [c for c, _ in core.calls if c == "API.SetPropertyValuesOfElements"]
    assert len(sends) == 2  # 500 per request, the same chunk as property reads


def test_send_nothing_sends_nothing():
    conn, core = _conn_with_cells({})
    assert send_property_writes(conn, []) == (0, [], None)
    assert core.calls == []


def _fail_on_batch(n):
    """SetPropertyValuesOfElements that succeeds except for batch number `n`."""
    batches = []

    def set_results(params):
        batches.append(len(params["elementPropertyValues"]))
        if len(batches) == n:
            raise StandardAPIError(message="Invalid program status", code=4001)
        return {"executionResults": [{"success": True}
                                     for _ in params["elementPropertyValues"]]}
    return set_results, batches


def test_a_failed_batch_stops_sending_and_keeps_the_progress():
    set_results, batches = _fail_on_batch(2)
    conn, _ = _conn_with_cells({}, set_results=set_results)
    applied, failed, error = send_property_writes(conn, _planned(1500))
    # The first 500 landed before the second batch was refused; the third
    # batch is never sent.
    assert applied == 500 and failed == []
    assert isinstance(error, StandardAPIError) and error.code == 4001
    assert batches == [500, 500]


def _string_cells(guids):
    return {(g, "D/P"): {"type": "string", "status": "normal", "value": "old"}
            for g in guids}


def test_set_element_data_keeps_the_count_when_a_batch_fails():
    guids = [f"g-{i}" for i in range(1000)]
    set_results, _ = _fail_on_batch(2)
    conn, _ = _conn_with_cells(_string_cells(guids), set_results=set_results)
    out = set_element_data(conn, [{"guid": g, "property": "D/P", "value": "new"}
                                  for g in guids], dry_run=False)
    assert out == {"dry_run": False, "applied": 500,
                   "stopped": {"code": 4001, "message": "Invalid program status"}}


def test_set_element_data_caps_the_failed_list():
    guids = [f"g-{i}" for i in range(60)]

    def refuse_all(params):
        return {"executionResults": [
            {"success": False, "error": {"code": 6001, "message": "denied"}}
            for _ in params["elementPropertyValues"]]}

    conn, _ = _conn_with_cells(_string_cells(guids), set_results=refuse_all)
    out = set_element_data(conn, [{"guid": g, "property": "D/P", "value": "new"}
                                  for g in guids], dry_run=False)
    assert out["applied"] == 0
    assert len(out["failed"]) == 50 and out["failed_not_shown"] == 10
    assert out["failed"][0]["guid"] == "g-0"


def test_cap_list():
    assert cap_list(list(range(3)), 5) == ([0, 1, 2], 0)
    assert cap_list(list(range(8)), 5) == ([0, 1, 2, 3, 4], 3)
