"""find_collisions: Tapir GetCollisions as pairs an agent can act on.

The canned answers follow what MCP-Test answered on 19.10.2026 (Archicad 29,
Tapir 1.7.1): one group sent as both sides gives each pair once with the
first element from group 1. Built against Archicad 30, Tapir spells the
clearance key correctly (hasClearanceCollision), so both spellings appear.
"""
from __future__ import annotations

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import collisions
from archicad_mcp.core.collisions import Collision
from tests.conftest import FakeCore
from tests.fixtures import api_replays

WALL = "2E618D06-949F-4B46-A01F-CA890F434A32"
BOX = "7C7A9EEE-03D3-E44A-9961-4BA5D7582C8E"
SMALL = "E7D7F085-7131-DC42-B7FF-BD0262E436F8"
MISSING = "00000000-0000-0000-0000-000000000001"

DETAILS = {
    WALL: {"type": "Wall", "id": "SW - 011", "floorIndex": 0},
    BOX: {"type": "Morph", "id": "MORPH-010", "floorIndex": 0},
    SMALL: {"type": "Morph", "id": "MORPH-011", "floorIndex": 0},
}


def _pair(a, b, body=True, clearance=False, key="hasClearenceCollision"):
    return {"elementId1": {"guid": a}, "elementId2": {"guid": b},
            "hasBodyCollision": body, key: clearance}


def _details(params):
    out = []
    for e in params["elements"]:
        guid = e["elementId"]["guid"].upper()
        if guid in DETAILS:
            out.append({k: v for k, v in DETAILS[guid].items()
                        if k in params.get("fields", DETAILS[guid])})
        else:
            out.append({"error": {"code": -2130313112, "message": "Element not found"}})
    return {"detailsOfElements": out}


def conn_with(pairs, by_type=None):
    core = FakeCore(official=dict(api_replays.OFFICIAL), tapir={
        "GetCollisions": lambda p: {"collisions": pairs},
        "GetDetailsOfElements": _details,
        "GetElementsByType": lambda p: {"elements": [
            {"elementId": {"guid": g}} for g in (by_type or {}).get(p["elementType"], [])]},
    })
    return ArchicadConnection(19723, core=core), core


def _sent(core, command):
    return [params for name, params in core.calls if name == command]


def test_settings_always_sent():
    conn, core = conn_with([])
    collisions.detect(conn, [WALL], [BOX], 0.001, None)
    collisions.detect(conn, [WALL], [BOX], 0.001, 0.02)
    first, second = _sent(core, "GetCollisions")
    assert first["settings"] == {"volumeTolerance": 0.001, "performSurfaceCheck": False,
                                 "surfaceTolerance": 0.001}
    assert second["settings"] == {"volumeTolerance": 0.001, "performSurfaceCheck": True,
                                  "surfaceTolerance": 0.02}


def test_same_group_sent_twice():
    conn, core = conn_with([])
    collisions.detect(conn, [WALL, BOX], None, 0.001, None)
    sent = _sent(core, "GetCollisions")[0]
    assert sent["elementsGroup1"] == sent["elementsGroup2"]
    assert [e["elementId"]["guid"] for e in sent["elementsGroup2"]] == [WALL, BOX]


def test_clearance_spelt_both_ways():
    conn, _ = conn_with([_pair(WALL, BOX, body=False, clearance=True),
                         _pair(WALL, SMALL, body=False, clearance=True,
                               key="hasClearanceCollision")])
    found = collisions.detect(conn, [WALL], [BOX, SMALL], 0.001, None)
    assert [c.clearance for c in found] == [True, True]
    assert [c.kind for c in found] == ["clearance", "clearance"]


def test_self_and_mirrored_pairs_dropped():
    conn, _ = conn_with([_pair(WALL, BOX), _pair(BOX, WALL), _pair(WALL, WALL)])
    found = collisions.detect(conn, [WALL, BOX], None, 0.001, None)
    assert found == [Collision(element=WALL, against=BOX, body=True, clearance=False)]


def test_kind_names_both():
    assert Collision(WALL, BOX, body=True, clearance=True).kind == "body and clearance"
    assert Collision(WALL, BOX, body=True, clearance=False).kind == "body"


def test_resolve_types():
    assert collisions.resolve_types(["walls", "BEAM"]) == ("Wall", "Beam")
    with pytest.raises(ValueError, match="Wall"):
        collisions.resolve_types(["Wal"])


def test_find_collisions_report():
    conn, core = conn_with([_pair(WALL, BOX), _pair(WALL, SMALL)])
    result = collisions.find_collisions(conn, [WALL.lower()], None,
                                        [BOX.lower(), SMALL.lower()], None)
    assert result["collisions"] == 2
    assert result["checked"] == {"elements": 1, "against": 2}
    assert result["pairs"][0] == {
        "element": WALL, "element_type": "Wall", "element_id": "SW - 011",
        "against": BOX, "against_type": "Morph", "against_id": "MORPH-010",
        "story": 0, "kind": "body"}
    assert result["by_type_pair"] == [{"types": "Morph / Wall", "count": 2}]
    assert sorted(result["elements"]) == sorted([WALL, BOX, SMALL])
    sent = _sent(core, "GetCollisions")[0]
    assert [e["elementId"]["guid"] for e in sent["elementsGroup1"]] == [WALL]


def test_find_collisions_by_type_within_group():
    conn, core = conn_with([_pair(BOX, SMALL)], by_type={"Morph": [BOX, SMALL]})
    result = collisions.find_collisions(conn, None, ["morphs"], None, None)
    assert result["checked"] == {"elements": 2, "against": "the same group"}
    assert result["collisions"] == 1
    assert _sent(core, "GetElementsByType") == [{"elementType": "Morph"}]


def test_unknown_guid_listed():
    conn, core = conn_with([])
    result = collisions.find_collisions(conn, [WALL, MISSING], None, [BOX], None)
    assert result["not_found"] == [MISSING]
    sent = _sent(core, "GetCollisions")[0]
    assert [e["elementId"]["guid"] for e in sent["elementsGroup1"]] == [WALL]


def test_unknown_type_is_error():
    conn, core = conn_with([])
    result = collisions.find_collisions(conn, None, ["Wal"], None, None)
    assert "Wall" in result["error"]
    assert not _sent(core, "GetCollisions")


def test_no_groups_is_error():
    conn, core = conn_with([])
    result = collisions.find_collisions(conn, None, None, None, None)
    assert "guids" in result["error"] and "element_types" in result["error"]
    assert not _sent(core, "GetCollisions")


def test_pairs_capped():
    others = [f"00000000-0000-0000-0000-{n:012d}" for n in range(60)]
    for g in others:
        DETAILS[g] = {"type": "Object", "id": "", "floorIndex": 1}
    try:
        conn, _ = conn_with([_pair(WALL, g) for g in others])
        result = collisions.find_collisions(conn, [WALL], None, others, None)
    finally:
        for g in others:
            del DETAILS[g]
    assert result["collisions"] == 60
    assert len(result["pairs"]) == 50
    assert result["not_shown"] == 10


async def _listed(mode):
    from fastmcp import Client

    from archicad_mcp.server import build_server
    async with Client(build_server(mode=mode)) as client:
        return {t.name: t for t in await client.list_tools()}


async def test_tool_registered_full_mode_only():
    full = await _listed("full")
    assert full["find_collisions"].annotations.readOnlyHint is True
    assert "find_collisions" not in await _listed("verdicts")


def test_tool_needs_getcollisions():
    from archicad_mcp.tapir_requirements import TOOL_COMMANDS
    assert TOOL_COMMANDS["find_collisions"] == ("GetCollisions", "GetElementsByType")
