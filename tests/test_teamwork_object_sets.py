"""Teamwork lockable object sets: reserve_elements / release_elements with
object_sets, and the reservation check the definition and attribute tools share.

Teamwork is not verified live (the only Teamwork project here is production).
The fake follows Tapir 1.7.1's TeamworkCommands.cpp: GetLockableObjectSetStatus
answers a status and the holders, or an error for a name FindLockableObjectSet
does not know; ReserveLockableObjectSet answers an executionResult with
conflicts; ReleaseLockableObjectSet answers an executionResult.
"""
import json

import pytest
from fastmcp import Client
from multiconn_archicad.errors import TapirCommandError

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.teamwork import (
    canonical_set,
    object_set_states,
    release_elements,
    reserve_elements,
    set_problem,
)
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays

KNOWN = {"LayerSettingsDialog", "Composites", "FillTypes", "BuildingMaterials",
         "ClassificationsAndProperties", "Favorites"}


class Server:
    """Set name -> (status, holders). A reservation turns Free into Editable."""

    def __init__(self, sets=None, offline=()):
        self.sets = {name: ("Free", []) for name in KNOWN}
        self.sets.update(sets or {})
        self.offline = set(offline)

    def _known(self, name):
        if name not in self.sets:
            raise TapirCommandError(
                message=f"No lockable object set named '{name}' was found. Check the "
                        "name and that the project is a Teamwork project.", code=-2130313112)

    def status(self, p):
        name = p["objectSetName"]
        self._known(name)
        if name in self.offline:
            return {"status": "NotAvailable"}
        status, holders = self.sets[name]
        out = {"status": status}
        if holders:
            out["conflicts"] = [{"userId": i, "userName": n} for i, n in holders]
        return out

    def reserve(self, p):
        name = p["objectSetName"]
        if name not in self.sets:
            return {"executionResult": {"success": False, "error": {
                "code": -2130313112, "message": f"No lockable object set named '{name}'"}}}
        status, holders = self.sets[name]
        if status == "Locked":
            return {"executionResult": {"success": False, "error": {
                        "code": -2130312312, "message": "Failed to reserve"}},
                    "conflicts": [{"userId": i, "userName": n} for i, n in holders]}
        self.sets[name] = ("Editable", [])
        return {"executionResult": {"success": True}}

    def release(self, p):
        name = p["objectSetName"]
        if self.sets.get(name, ("", []))[0] == "Editable":
            self.sets[name] = ("Free", [])
        return {"success": True}


def make(server=None, teamwork=True, has_sets=True):
    server = server or Server()
    official = dict(api_replays.OFFICIAL)
    official["API.IsAddOnCommandAvailable"] = lambda p: {"available": has_sets or p[
        "addOnCommandId"]["commandName"] not in {"ReserveLockableObjectSet",
                                                 "ReleaseLockableObjectSet",
                                                 "GetLockableObjectSetStatus"}}
    tapir = dict(api_replays.TAPIR)
    tapir["GetAddOnVersion"] = {"version": "1.7.1" if has_sets else "1.6.0"}
    tapir["GetProjectInfo"] = {**api_replays.TAPIR["GetProjectInfo"], "isTeamwork": teamwork}
    tapir["GetLockableObjectSetStatus"] = server.status
    tapir["ReserveLockableObjectSet"] = server.reserve
    tapir["ReleaseLockableObjectSet"] = server.release
    return ArchicadConnection(19723, core=FakeCore(official=official, tapir=tapir)), server


def sent(conn, name):
    return [p["objectSetName"] for c, p in conn._core.calls if c == name]


# ---------- names ----------

@pytest.mark.parametrize("given,expected", [
    ("Layers", "LayerSettingsDialog"), ("layer combination", "LayerSettingsDialog"),
    ("LayerCombinations", "LayerSettingsDialog"), ("Composite", "Composites"),
    ("fills", "FillTypes"), ("Line", "LineTypes"), ("PenTable", "PenTables"),
    ("Profile", "Profiles"), ("BuildingMaterial", "BuildingMaterials"),
    ("surface", "Surfaces"), ("ZoneCategory", "ZoneCategories"),
    ("Properties", "ClassificationsAndProperties"),
    ("classifications", "ClassificationsAndProperties"),
    ("Favorites", "Favorites"), ("ProjectInfo", "ProjectInfo"),
])
def test_aliases_name_the_archicad_set(given, expected):
    assert canonical_set(given) == expected


def test_an_unknown_name_is_sent_as_given():
    assert canonical_set("MarkupStyles") == "MarkupStyles"


# ---------- reserve ----------

def test_object_sets_refused_outside_teamwork():
    conn, _ = make(teamwork=False)
    out = reserve_elements(conn, None, confirm=True, object_sets=["Layers"])
    assert "not a Teamwork project" in out["error"]
    assert sent(conn, "ReserveLockableObjectSet") == []


def test_neither_guids_nor_sets_is_an_error():
    conn, _ = make()
    assert "guids or object_sets" in reserve_elements(conn, None)["error"]


def test_reserve_dry_run_reads_every_status_and_sends_nothing():
    server = Server({"Composites": ("Editable", []),
                     "FillTypes": ("Locked", [(7, "Someone Else")])},
                    offline={"Favorites"})
    conn, _ = make(server)
    out = reserve_elements(conn, None, object_sets=[
        "Layers", "LayerCombinations", "Composite", "Fills", "Favorites", "Nonsense"])
    assert out["dry_run"] is True
    by_set = {e["set"]: e for e in out["object_sets"]}
    assert list(by_set) == ["LayerSettingsDialog", "Composites", "FillTypes",
                            "Favorites", "Nonsense"]
    assert by_set["LayerSettingsDialog"]["asked"] == ["Layers", "LayerCombinations"]
    assert by_set["LayerSettingsDialog"]["outcome"] == "would_reserve"
    assert by_set["Composites"]["outcome"] == "already_mine"
    assert by_set["FillTypes"]["outcome"] == "held_by_others"
    assert by_set["FillTypes"]["held_by"] == ["Someone Else"]
    assert by_set["Favorites"]["outcome"] == "server_unavailable"
    assert by_set["Nonsense"]["outcome"] == "error"
    assert "No lockable object set named" in by_set["Nonsense"]["error"]
    assert sent(conn, "ReserveLockableObjectSet") == []


def test_reserve_confirm_attempts_only_free_sets_and_reads_back():
    server = Server({"Composites": ("Editable", []),
                     "FillTypes": ("Locked", [(7, "Someone Else")])})
    conn, _ = make(server)
    out = reserve_elements(conn, None, confirm=True,
                           object_sets=["Layers", "Composites", "FillTypes"])
    by_set = {e["set"]: e for e in out["object_sets"]}
    assert by_set["LayerSettingsDialog"]["outcome"] == "reserved"
    assert by_set["LayerSettingsDialog"]["status"] == "Editable"
    assert by_set["Composites"]["outcome"] == "already_mine"
    assert by_set["FillTypes"]["outcome"] == "held_by_others"
    assert sent(conn, "ReserveLockableObjectSet") == ["LayerSettingsDialog"]


def test_a_failed_reservation_reports_the_holders_and_the_message():
    server = Server()
    conn, _ = make(server)
    # Taken by a teammate between the status read and the attempt.
    original = server.status

    def racing(p):
        answer = original(p)
        server.sets["Composites"] = ("Locked", [(9, "Late Comer")])
        return answer
    conn._core.tapir_responses["GetLockableObjectSetStatus"] = racing
    out = reserve_elements(conn, None, confirm=True, object_sets=["Composites"])
    [entry] = out["object_sets"]
    assert entry["outcome"] == "failed"
    assert entry["held_by"] == ["Late Comer"]
    assert "Failed to reserve" in entry["error"]


def test_guids_only_results_keep_their_shape():
    conn, _ = make()
    conn._core.official_responses["API.GetTypesOfElements"] = {"typesOfElements": []}
    out = reserve_elements(conn, ["ghost"])
    assert "object_sets" not in out and out["not_found"] == ["ghost"]
    assert sent(conn, "GetLockableObjectSetStatus") == []


def test_object_sets_need_tapir_1_7_1():
    conn, _ = make(has_sets=False)
    out = reserve_elements(conn, None, object_sets=["Layers"])
    assert "Tapir 1.7.1" in out["error"] and "ReserveLockableObjectSet" in out["error"]


# ---------- release ----------

def test_release_dry_run_says_releasing_sends():
    server = Server({"Composites": ("Editable", [])})
    conn, _ = make(server)
    out = release_elements(conn, None, object_sets=["Composites", "Layers"])
    by_set = {e["set"]: e for e in out["object_sets"]}
    assert by_set["Composites"]["outcome"] == "would_release"
    assert by_set["LayerSettingsDialog"]["outcome"] == "not_mine"
    assert "sends" in out["note"]
    assert sent(conn, "ReleaseLockableObjectSet") == []


def test_release_confirm_releases_mine_only_and_reads_back():
    server = Server({"Composites": ("Editable", [])})
    conn, _ = make(server)
    out = release_elements(conn, None, confirm=True, object_sets=["Composites", "Layers"])
    by_set = {e["set"]: e for e in out["object_sets"]}
    assert by_set["Composites"]["outcome"] == "released"
    assert by_set["Composites"]["status"] == "Free"
    assert by_set["LayerSettingsDialog"]["outcome"] == "not_mine"
    assert sent(conn, "ReleaseLockableObjectSet") == ["Composites"]


# ---------- the shared check ----------

def test_states_are_none_outside_teamwork_after_one_call():
    conn, _ = make(teamwork=False)
    assert object_set_states(conn, ["Composites"]) is None
    assert [c for c, _ in conn._core.calls if c.startswith("Get")] == ["GetProjectInfo"]


def test_problem_texts_name_the_call_or_the_holder():
    server = Server({"Composites": ("Editable", []),
                     "FillTypes": ("Locked", [(7, "Someone Else")])},
                    offline={"Favorites"})
    conn, _ = make(server)
    states = object_set_states(conn, ["Composites", "BuildingMaterials", "FillTypes",
                                      "Favorites", "Nonsense"])
    assert set_problem(states["Composites"]) is None
    free = set_problem(states["BuildingMaterials"])
    assert free.blocking and 'reserve_elements(object_sets=["BuildingMaterials"], ' \
                             'confirm=true)' in free.text
    locked = set_problem(states["FillTypes"])
    assert locked.blocking and "Someone Else" in locked.text
    offline = set_problem(states["Favorites"])
    assert not offline.blocking and "unavailable" in offline.text
    unknown = set_problem(states["Nonsense"])
    assert not unknown.blocking and "could not check" in unknown.text


def test_an_old_tapir_in_teamwork_is_a_warning_not_a_block():
    conn, _ = make(has_sets=False)
    states = object_set_states(conn, ["Composites"])
    problem = set_problem(states["Composites"])
    assert not problem.blocking and "1.7.1" in problem.text


# ---------- tools ----------

async def test_tools_take_object_sets(monkeypatch):
    conn, server = make()
    monkeypatch.setattr(server_mod, "get_connection", lambda port: conn)
    async with Client(build_server(mode="full")) as client:
        result = await client.call_tool("reserve_elements", {"object_sets": ["Layers"]})
        payload = json.loads(result.content[0].text)
        assert payload["object_sets"][0]["outcome"] == "would_reserve"
        result = await client.call_tool("release_elements", {
            "object_sets": ["Layers"], "confirm": True})
        payload = json.loads(result.content[0].text)
        assert payload["object_sets"][0]["outcome"] == "not_mine"
