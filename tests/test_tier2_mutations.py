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
    official = dict(api_replays.OFFICIAL)
    official["API.GetSelectedElements"] = {"elements": [{"elementId": {"guid": "w-1"}}]}
    tapir = dict(api_replays.TAPIR)
    tapir["CreateSlabs"] = {"elements": [{"elementId": {"guid": "new-slab-1"}}]}
    tapir["MoveElements"] = {}
    tapir["DeleteElements"] = {}
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


async def test_move_refuses_without_confirm(core):
    payload = await call("move_elements",
                         {"guids": ["w-1"], "vector": {"x": 1.0, "y": 0.0, "z": 0.0}})
    assert "confirm" in payload["error"]
    assert not any(c == "MoveElements" for c, _ in core.calls)


async def test_move_with_confirm(core):
    payload = await call("move_elements",
                         {"guids": ["w-1"], "vector": {"x": 1.0, "y": 0.0, "z": 0.0},
                          "confirm": True})
    assert payload == {"moved": 1}
    command, params = [c for c in core.calls if c[0] == "MoveElements"][0]
    assert params["elementsWithMoveVectors"][0]["moveVector"] == {"x": 1.0, "y": 0.0, "z": 0.0}


async def test_delete_refuses_without_confirm(core):
    payload = await call("delete_elements", {"guids": ["w-1", "w-2"]})
    assert "2 element(s)" in payload["error"]


async def test_delete_with_confirm(core):
    payload = await call("delete_elements", {"guids": ["w-1"], "confirm": True})
    assert payload == {"deleted": 1}


async def test_selection_get_uses_official_api(core):
    payload = await call("get_selection")
    assert payload == {"guids": ["w-1"]}


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
