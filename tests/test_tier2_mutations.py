import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


@pytest.fixture
def core(monkeypatch):
    selection = {"elements": [{"elementId": {"guid": "w-1"}}]}
    official = dict(api_replays.OFFICIAL)
    official["API.GetSelectedElements"] = selection
    tapir = dict(api_replays.TAPIR)
    tapir["GetSelectedElements"] = selection  # the source once Tapir is present
    tapir["CreateSlabs"] = {"elements": [{"elementId": {"guid": "new-slab-1"}}]}
    tapir["MoveElements"] = lambda p: {"executionResults": [
        {"success": True} for _ in p["elementsWithMoveVectors"]]}
    # delete_elements reads its elements back, so deleting has to stick.
    deleted = set()

    def delete(p):
        deleted.update(e["elementId"]["guid"] for e in p["elements"])
        return {"success": True}

    def details(p):
        return {"detailsOfElements": [
            {"error": {"code": -2130313112, "message": "not found"}}
            if e["elementId"]["guid"] in deleted
            else api_replays.get_details_of_elements({"elements": [e]})
            ["detailsOfElements"][0]
            for e in p["elements"]]}

    tapir["DeleteElements"] = delete
    tapir["GetDetailsOfElements"] = details
    tapir["ChangeSelectionOfElements"] = {}
    core = FakeCore(official=official, tapir=tapir)
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def call(tool, args=None):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


SLAB_ITEM = {"polygonCoordinates": [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 5}],
             "level": 0.0}


async def test_create_elements_dry_run_default(core):
    payload = await call("create_elements", {"element_type": "slab", "items": [SLAB_ITEM]})
    assert payload["dry_run"] is True
    assert payload["command"] == "CreateSlabs"
    assert payload["payload"] == {"slabsData": [SLAB_ITEM]}
    assert not any(c == "CreateSlabs" for c, _ in core.calls)


async def test_create_elements_commit(core):
    payload = await call("create_elements",
                         {"element_type": "slab", "items": [SLAB_ITEM], "dry_run": False})
    assert payload == {"dry_run": False, "created": 1, "elements": ["new-slab-1"]}


async def test_create_elements_unknown_type_points_to_gateway(core):
    payload = await call("create_elements", {"element_type": "door", "items": [{}]})
    assert "execute_write_api_command" in payload["error"]


RAILING_ITEM = {"referenceLinePoints": [{"x": 0, "y": 0, "z": 0},
                                        {"x": 3, "y": 0, "z": 0.9}]}


async def test_create_railing_dry_run(core):
    payload = await call("create_elements",
                         {"element_type": "railing", "items": [RAILING_ITEM]})
    assert payload["dry_run"] is True
    assert payload["command"] == "CreateRailings"
    assert payload["payload"] == {"railingsData": [RAILING_ITEM]}
    assert not any(c == "CreateRailings" for c, _ in core.calls)


async def test_create_railing_commit(core):
    core.tapir_responses["CreateRailings"] = {
        "elements": [{"elementId": {"guid": "new-railing-1"}}]}
    payload = await call("create_elements",
                         {"element_type": "railing", "items": [RAILING_ITEM],
                          "dry_run": False})
    assert payload == {"dry_run": False, "created": 1,
                       "elements": ["new-railing-1"]}


async def test_move_refuses_without_confirm(core):
    payload = await call("move_elements",
                         {"guids": ["w-1"], "vector": {"x": 1.0, "y": 0.0, "z": 0.0}})
    assert "confirm" in payload["error"]
    assert not any(c == "MoveElements" for c, _ in core.calls)


async def test_move_with_confirm(core):
    payload = await call("move_elements",
                         {"guids": ["w-1"], "vector": {"x": 1.0, "y": 0.0, "z": 0.0},
                          "confirm": True})
    assert payload == {"requested": 1, "moved": 1}
    command, params = [c for c in core.calls if c[0] == "MoveElements"][0]
    assert params["elementsWithMoveVectors"][0]["moveVector"] == {"x": 1.0, "y": 0.0, "z": 0.0}


async def test_delete_refuses_without_confirm(core):
    payload = await call("delete_elements", {"guids": ["w-1", "w-2"]})
    assert "2 element(s)" in payload["error"]


async def test_delete_with_confirm(core):
    payload = await call("delete_elements", {"guids": ["w-1"], "confirm": True})
    assert payload == {"requested": 1, "deleted": 1}


async def test_delete_from_a_layout_reports_nothing_deleted(core):
    # End to end through the MCP layer, the live 28.09.2026 case: Archicad
    # answers success, but from a Layout the floor-plan element is not editable.
    core.tapir_responses["FilterElements"] = lambda p: {"elements": []}
    core.tapir_responses["GetCurrentWindowType"] = {"currentWindowType": "Layout"}
    payload = await call("delete_elements", {"guids": ["w-1"], "confirm": True})
    assert payload["deleted"] == 0
    assert payload["active_window"] == "Layout"
    assert payload["not_deleted"][0]["guids"] == ["w-1"]
    assert not any(c == "DeleteElements" for c, _ in core.calls)


async def test_selection_get_reads_through_tapir(core):
    payload = await call("get_selection")
    assert payload == {"guids": ["w-1"], "coverage": "whole-plan"}
    assert not any(c == "API.GetSelectedElements" for c, _ in core.calls)


async def test_selection_set_replaces_current(core):
    # core fixture seeds the current selection as [w-1]
    payload = await call("set_selection", {"guids": ["w-2"]})
    assert payload == {"selected": 1}
    change = [params for cmd, params in core.calls if cmd == "ChangeSelectionOfElements"]
    assert len(change) == 1  # one call does both remove + add
    params = change[0]
    assert params["addElementsToSelection"] == [{"elementId": {"guid": "w-2"}}]
    # the pre-existing selection is removed so "set" replaces rather than appends
    assert params["removeElementsFromSelection"] == [{"elementId": {"guid": "w-1"}}]


async def test_selection_clear_removes_current(core):
    payload = await call("clear_selection")
    assert payload == {"cleared": 1}
    change = [params for cmd, params in core.calls if cmd == "ChangeSelectionOfElements"]
    assert change[0]["removeElementsFromSelection"] == [{"elementId": {"guid": "w-1"}}]
