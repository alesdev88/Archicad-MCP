"""reserve_elements / release_elements.

Tapir's ReserveElements reports only reserved-by-other conflicts (read from
TeamworkCommands.cpp); not-found, already-mine and indirectly-reserved are
derived. The fake below keeps a workspace so the derivation can be tested:
reserving w-1 also pulls in d-1 (its door), and w-2 belongs to Someone Else.
"""
import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.teamwork import release_elements, reserve_elements
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays

TYPES = {"w-1": "Wall", "w-2": "Wall", "z-1": "Zone", "d-1": "Door"}
INDIRECT = {"w-1": ["d-1"]}          # reserving w-1 reserves d-1 too
HELD_BY_OTHER = {"w-2": (7, "Someone Else")}


class Workspace:
    def __init__(self, mine=()):
        self.mine = set(mine)

    def filter_elements(self, p):
        assert p["filters"] == ["InMyWorkspace"]
        return {"elements": [e for e in p["elements"] if e["elementId"]["guid"] in self.mine]}

    def reserve(self, p):
        conflicts = []
        for e in p["elements"]:
            g = e["elementId"]["guid"]
            if g in HELD_BY_OTHER:
                uid, name = HELD_BY_OTHER[g]
                conflicts.append({"elementId": {"guid": g}, "user": {"userId": uid, "userName": name}})
                continue
            self.mine.add(g)
            self.mine.update(INDIRECT.get(g, []))
        out = {"executionResult": {"success": True}}
        if conflicts:
            out["conflicts"] = conflicts
        return out

    def release(self, p):
        for e in p["elements"]:
            self.mine.discard(e["elementId"]["guid"])
        return {"executionResult": {"success": True}}


def make(mine=(), teamwork=True):
    ws = Workspace(mine)
    official = dict(api_replays.OFFICIAL)
    official["API.GetTypesOfElements"] = lambda p: {"typesOfElements": [
        {"typeOfElement": {"elementId": e["elementId"], "elementType": TYPES[e["elementId"]["guid"]]}}
        for e in p["elements"] if e["elementId"]["guid"] in TYPES]}
    tapir = dict(api_replays.TAPIR)
    tapir["GetProjectInfo"] = {**api_replays.TAPIR["GetProjectInfo"], "isTeamwork": teamwork}
    tapir["GetAllElements"] = {"elements": [{"elementId": {"guid": g}} for g in TYPES]}
    tapir["FilterElements"] = ws.filter_elements
    tapir["ReserveElements"] = ws.reserve
    tapir["ReleaseElements"] = ws.release
    core = FakeCore(official=official, tapir=tapir)
    return ArchicadConnection(19723, core=core), ws


def calls(conn, name):
    return [c for c, _ in conn._core.calls if c == name]


# ---------- reserve ----------

def test_reserve_refuses_outside_teamwork():
    conn, _ = make(teamwork=False)
    assert "not a Teamwork project" in reserve_elements(conn, ["w-1"], confirm=True)["error"]
    assert calls(conn, "ReserveElements") == []


def test_reserve_dry_run_derives_what_it_can_and_says_what_it_cannot():
    conn, _ = make(mine=["z-1"])
    payload = reserve_elements(conn, ["w-1", "z-1", "ghost", "w-1"])
    assert payload["dry_run"] is True
    assert payload["requested"] == 3
    assert payload["not_found"] == ["ghost"]
    assert payload["already_mine"] == ["z-1"]
    assert payload["would_attempt"] == ["w-1"]
    assert "only learned by attempting" in payload["note"]
    assert calls(conn, "ReserveElements") == []


def test_reserve_commit_separates_the_four_outcomes():
    conn, ws = make(mine=["z-1"])
    payload = reserve_elements(conn, ["w-1", "w-2", "z-1", "ghost"], confirm=True)
    assert payload["dry_run"] is False
    assert payload["reserved"] == ["w-1"]
    assert payload["reserved_by_others"] == [{"guid": "w-2", "user": "Someone Else", "user_id": 7}]
    assert payload["already_mine"] == ["z-1"]
    assert payload["not_found"] == ["ghost"]
    assert payload["indirectly_reserved"] == ["d-1"]
    assert ws.mine == {"z-1", "w-1", "d-1"}
    # already-mine and unknown elements are never sent to Archicad
    sent = [p for c, p in conn._core.calls if c == "ReserveElements"][0]["elements"]
    assert [e["elementId"]["guid"] for e in sent] == ["w-1", "w-2"]


def test_reserve_commit_with_nothing_to_attempt_makes_no_call():
    conn, _ = make(mine=["w-1"])
    payload = reserve_elements(conn, ["w-1"], confirm=True)
    assert payload["reserved"] == [] and payload["already_mine"] == ["w-1"]
    assert calls(conn, "ReserveElements") == []


def test_reserve_reports_an_archicad_refusal():
    conn, _ = make()
    conn._core.tapir_responses["ReserveElements"] = {
        "executionResult": {"success": False, "error": {"code": 1, "message": "offline"}}}
    payload = reserve_elements(conn, ["w-1"], confirm=True)
    assert "offline" in payload["error"]


# ---------- release ----------

def test_release_dry_run_lists_only_what_is_mine():
    conn, _ = make(mine=["w-1", "d-1"])
    payload = release_elements(conn, ["w-1", "w-2", "ghost"])
    assert payload["dry_run"] is True
    assert payload["would_release"] == ["w-1"]
    assert payload["not_mine"] == ["w-2"]
    assert payload["not_found"] == ["ghost"]
    assert calls(conn, "ReleaseElements") == []


def test_release_commit_releases_mine_and_verifies():
    conn, ws = make(mine=["w-1", "d-1"])
    payload = release_elements(conn, ["w-1", "w-2"], confirm=True)
    assert payload["released"] == ["w-1"] and payload["still_mine"] == []
    assert payload["not_mine"] == ["w-2"]
    assert ws.mine == {"d-1"}


def test_release_refuses_outside_teamwork():
    conn, _ = make(teamwork=False)
    assert "error" in release_elements(conn, ["w-1"], confirm=True)


# ---------- tools ----------

async def test_tools_are_confirm_gated_and_full_mode_only(monkeypatch):
    conn, ws = make()
    monkeypatch.setattr(server_mod, "get_connection", lambda port: conn)
    async with Client(build_server(mode="full")) as client:
        names = {t.name for t in await client.list_tools()}
        assert {"reserve_elements", "release_elements"} <= names
        result = await client.call_tool("reserve_elements", {"guids": ["w-1"]})
        payload = json.loads(result.content[0].text)
        assert payload["dry_run"] is True and ws.mine == set()
        result = await client.call_tool("reserve_elements", {"guids": ["w-1"], "confirm": True})
        payload = json.loads(result.content[0].text)
        assert payload["reserved"] == ["w-1"] and "d-1" in ws.mine
    async with Client(build_server(mode="verdicts")) as client:
        names = {t.name for t in await client.list_tools()}
        assert "reserve_elements" not in names
