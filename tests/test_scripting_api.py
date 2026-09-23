"""The `ac` object scripts see: reads go to Archicad, writes are only recorded."""
import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.scripting.api import ArchicadError, Recorder, ScriptAPI
from tests.conftest import FakeCore
from tests.fixtures import api_replays


@pytest.fixture
def setup():
    official = dict(api_replays.OFFICIAL)
    official["API.SetPropertyValuesOfElements"] = {"executionResults": []}
    core = FakeCore(official=official, tapir=dict(api_replays.TAPIR))
    recorder = Recorder()
    ac = ScriptAPI(ArchicadConnection(19723, core=core), build_registry(), recorder)
    return ac, core, recorder


def _sent(core, name):
    return [p for c, p in core.calls if c == name]


def test_find_returns_the_guids(setup):
    ac, _, _ = setup
    assert sorted(ac.find([{"element_types": ["Wall"]}])) == ["w-1", "w-2"]


def test_find_with_bad_groups_raises_the_criteria_error(setup):
    ac, _, _ = setup
    with pytest.raises(ValueError):
        ac.find([{"logical_operator": "xor"}])


def test_props_reads_values(setup):
    ac, _, _ = setup
    assert ac.props(["w-1"], ["OFFICE/Fire Rating"]) == {
        "w-1": {"OFFICE/Fire Rating": "EI60"}}


def test_details_are_keyed_by_guid(setup):
    ac, _, _ = setup
    details = ac.details(["w-1", "w-2"])
    assert details["w-2"]["floorIndex"] == 1


def test_cmd_runs_a_read(setup):
    ac, _, _ = setup
    assert ac.cmd("GetProjectInfo")["projectName"] == "Test House"


def test_cmd_records_a_write_and_sends_nothing(setup):
    ac, core, recorder = setup
    # The Tapir schema wants real GUID syntax, unlike the fixture's "w-1".
    params = {"elements": [{"elementId": {"guid": "00000000-0000-0000-0000-000000000001"}}],
              "highlightedColors": [[255, 0, 0, 128]]}
    assert ac.cmd("HighlightElements", params) == {"recorded": 1}
    assert _sent(core, "HighlightElements") == []
    assert recorder.operations == [
        {"kind": "command", "name": "HighlightElements", "params": params}]


def test_cmd_validates_a_write_before_recording_it(setup):
    ac, _, recorder = setup
    with pytest.raises(ValueError, match="failed validation"):
        ac.cmd("HighlightElements", {})
    assert recorder.operations == []


def test_cmd_refuses_an_unknown_name_with_suggestions(setup):
    ac, _, _ = setup
    with pytest.raises(ValueError, match="Did you mean"):
        ac.cmd("GetProjectInf")


def test_an_archicad_refusal_raises_archicad_error(setup):
    ac, _, _ = setup
    # Read-classified and absent from the fixtures, so FakeCore refuses it the
    # way Archicad would.
    with pytest.raises(ArchicadError) as info:
        ac.cmd("API.GetActivePenTables")
    assert "no canned response" in info.value.message
    assert ac.ArchicadError is ArchicadError


def test_set_props_plans_into_the_recorder_and_sends_nothing(setup):
    ac, core, recorder = setup
    counts = ac.set_props([("w-2", "OFFICE/Fire Rating", "EI30"),
                           {"guid": "z-1", "property": "OFFICE/Fire Rating",
                            "value": "EI30"}])
    assert counts == {"planned": 1, "skipped": 1}
    assert _sent(core, "API.SetPropertyValuesOfElements") == []
    [op] = recorder.operations
    assert op["kind"] == "props"
    assert [w["guid"] for w in op["writes"]] == ["w-2"]
    assert recorder.skipped[0]["guid"] == "z-1"


def test_consecutive_set_props_calls_share_one_operation(setup):
    ac, _, recorder = setup
    ac.set_props([("w-1", "OFFICE/Fire Rating", "EI30")])
    ac.set_props([("w-2", "OFFICE/Fire Rating", "EI30")])
    assert len(recorder.operations) == 1
    assert len(recorder.operations[0]["writes"]) == 2


def test_port_is_exposed(setup):
    ac, _, _ = setup
    assert ac.port == 19723
