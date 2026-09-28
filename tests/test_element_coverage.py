"""Element enumeration must cover the whole plan, not just model elements.

Live measurement on a real project (AC 29.0/4006, Tapir 1.5.3):

    official  API.GetAllElements  ->  16221
    tapir     GetAllElements      ->  63122

Everything 2D (markers, labels, dimensions, section lines) is invisible to the
official command, and the tools reported that absence as a bare ``count: 0`` --
which reads as "verified absent" and gets acted on.
"""

import json

import pytest
from fastmcp import Client
from multiconn_archicad.errors import TapirCommandError

import archicad_mcp.server as server_mod
from archicad_mcp import extract
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays

# The fixture plan: three model elements, one 2D marker that only Tapir sees,
# and a native MEP duct route.
TYPES = {"w-1": "Wall", "w-2": "Wall", "z-1": "Zone", "ie-1": "InteriorElevation"}
MODEL_ONLY = ["w-1", "w-2", "z-1"]

# A native MEP duct route. Live on AC 29/5101 with Tapir 1.5.10 (2026-09-28):
# official API.GetSelectedElements left it out and API.GetTypesOfElements
# answered 7203 "Element not supported"; Tapir GetSelectedElements returned it,
# and Tapir GetDetailsOfElements typed it "Unknown".
MEP_ROUTE = "route-1"
WHOLE_PLAN = MODEL_ONLY + ["ie-1", MEP_ROUTE]


def _elements(guids):
    return {"elements": [{"elementId": {"guid": g}} for g in guids]}


def official_types(p):
    """Live shape (AC 29/5101, 2026-09-28): one item per requested element, in
    request order. Only model elements are typed; everything else on the plan
    answers 7203 (35902 of 63127 elements on a live project, 2D and MEP alike),
    and a GUID that is not on the plan answers 7204."""
    items = []
    for el in p["elements"]:
        g = el["elementId"]["guid"]
        if g in MODEL_ONLY:
            items.append({"typeOfElement": {"elementId": el["elementId"],
                                            "elementType": TYPES[g]}})
        elif g in WHOLE_PLAN:
            items.append({"error": {"code": 7203, "message": "Element not supported"}})
        else:
            items.append({"error": {"code": 7204, "message": "Element not found"}})
    return {"typesOfElements": items}


def tapir_details(p):
    """Live shape of GetDetailsOfElements with fields=["type"]: Tapir types the
    2D elements, calls a native MEP element "Unknown", and answers a per-item
    error for a GUID that is not on the plan."""
    assert p.get("fields") == ["type"]
    items = []
    for el in p["elements"]:
        g = el["elementId"]["guid"]
        if g in WHOLE_PLAN:
            items.append({"type": TYPES.get(g, "Unknown")})
        else:
            items.append({"error": {"code": -2130313115,
                                    "message": "Failed to get the details of element"}})
    return {"detailsOfElements": items}


def elements_by_type(p):
    if p["elementType"] == "Unknown":  # live: Tapir refuses to filter by it
        raise TapirCommandError(message="Invalid elementType 'Unknown'.", code=-2130313112)
    return _elements([g for g, t in TYPES.items() if t == p["elementType"]])


def make_core(tapir_on=True, selected=()):
    official = dict(api_replays.OFFICIAL)
    official["API.GetAllElements"] = _elements(MODEL_ONLY)
    official["API.GetSelectedElements"] = _elements(
        [g for g in selected if g in MODEL_ONLY])
    official["API.GetTypesOfElements"] = official_types
    if not tapir_on:
        official["API.IsAddOnCommandAvailable"] = {"available": False}
    tapir = dict(api_replays.TAPIR)
    tapir["GetAllElements"] = _elements(WHOLE_PLAN)
    tapir["GetSelectedElements"] = _elements(selected)
    tapir["GetElementsByType"] = elements_by_type
    tapir["GetDetailsOfElements"] = tapir_details
    return FakeCore(official=official, tapir=tapir if tapir_on else {})


@pytest.fixture
def core(monkeypatch):
    return _install(monkeypatch, make_core())


def _install(monkeypatch, core):
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def call(tool, args=None):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


# ---------- find_elements ----------

async def test_query_by_type_finds_a_2d_marker(core):
    """The regression: this used to return a silent count of 0."""
    payload = await call("find_elements", {"groups": [{"element_types": ["InteriorElevation"]}]})
    assert payload["count"] == 1
    assert payload["guids"] == ["ie-1"]


async def test_query_by_type_does_not_sweep_types_of_the_whole_plan(core):
    """Asking Tapir for one type replaces 'fetch everything, filter here'."""
    await call("find_elements", {"groups": [{"element_types": ["Wall"]}]})
    assert not any(c == "API.GetTypesOfElements" for c, _ in core.calls)
    assert any(c == "GetElementsByType" for c, _ in core.calls)


async def test_query_unfiltered_covers_the_whole_plan(core):
    payload = await call("find_elements", {"groups": [{"element_types": ["Zone"],
                                                        "element_types_operator": "is_not"}]})
    assert payload["count"] == 4
    payload = await call("find_elements", {"groups": [{"element_types": ["all"]}]})
    assert payload["count"] == 5
    assert payload["coverage"] == "whole-plan"


async def test_query_types_what_the_official_api_cannot(core):
    """Live, the official type read rejects the 2D marker and the MEP route
    (7203); without Tapir's answer both landed in a by_type bucket keyed ''."""
    payload = await call("find_elements", {"groups": [{"element_types": ["all"]}]})
    assert payload["by_type"] == {"Wall": 2, "Zone": 1, "InteriorElevation": 1,
                                  "Unknown": 1}


async def test_query_selection_types_a_selected_mep_route(monkeypatch):
    _install(monkeypatch, make_core(selected=(MEP_ROUTE,)))
    payload = await call("find_elements", {"groups": [{"element_types": ["all"]}],
                                           "selection_only": True})
    assert payload["guids"] == [MEP_ROUTE]
    assert payload["by_type"] == {"Unknown": 1}


async def test_query_by_unknown_type_filters_in_the_server(core):
    """'Unknown' is in the schema's type list, but Tapir GetElementsByType
    refuses it, so it is matched here against the types read back."""
    payload = await call("find_elements", {"groups": [{"element_types": ["Unknown"]}]})
    assert payload["guids"] == [MEP_ROUTE]


async def test_query_selection_sees_a_selected_marker(monkeypatch):
    _install(monkeypatch, make_core(selected=("ie-1",)))
    payload = await call("find_elements", {"groups": [{"element_types": ["all"]}],
                                           "selection_only": True})
    assert payload["guids"] == ["ie-1"]


async def test_query_selection_coverage_follows_the_selection_source(monkeypatch):
    """Tapir present but too old for GetSelectedElements: the selection came
    from the official command, so 'whole-plan' would overstate it."""
    core = make_core(selected=("w-1", MEP_ROUTE))
    core.official_responses["API.IsAddOnCommandAvailable"] = lambda p: {
        "available": p["addOnCommandId"]["commandName"] != "GetSelectedElements"}
    _install(monkeypatch, core)
    payload = await call("find_elements", {"groups": [{"element_types": ["all"]}],
                                           "selection_only": True})
    assert payload["guids"] == ["w-1"]
    assert payload["coverage"] == "model-elements-only"


async def test_query_without_tapir_says_coverage_is_partial(monkeypatch):
    _install(monkeypatch, make_core(tapir_on=False))
    payload = await call("find_elements", {"groups": [{"element_types": ["all"]}]})
    assert payload["count"] == 3
    assert payload["coverage"] == "model-elements-only"
    assert "Tapir" in payload["coverage_note"]


async def test_query_by_type_without_tapir_still_filters(monkeypatch):
    _install(monkeypatch, make_core(tapir_on=False))
    payload = await call("find_elements", {"groups": [{"element_types": ["Wall"]}]})
    assert set(payload["guids"]) == {"w-1", "w-2"}
    assert payload["coverage"] == "model-elements-only"


# ---------- get_selection / set_selection / clear_selection ----------

async def test_get_selection_sees_a_selected_mep_route(monkeypatch):
    """The regression: a selected duct route read back as {"guids": []}."""
    core = _install(monkeypatch, make_core(selected=(MEP_ROUTE,)))
    payload = await call("get_selection")
    assert payload["guids"] == [MEP_ROUTE]
    assert payload["coverage"] == "whole-plan"
    assert not any(c == "API.GetSelectedElements" for c, _ in core.calls)


async def test_get_selection_without_tapir_says_coverage_is_partial(monkeypatch):
    """An empty or short selection must not read as the whole selection."""
    _install(monkeypatch, make_core(tapir_on=False, selected=("w-1", MEP_ROUTE)))
    payload = await call("get_selection")
    assert payload["guids"] == ["w-1"]
    assert payload["coverage"] == "model-elements-only"
    assert "MEP" in payload["coverage_note"]
    assert "Tapir" in payload["coverage_note"]


async def test_get_selection_falls_back_when_tapir_lacks_the_command(monkeypatch):
    core = make_core(selected=("w-1", MEP_ROUTE))
    core.official_responses["API.IsAddOnCommandAvailable"] = lambda p: {
        "available": p["addOnCommandId"]["commandName"] != "GetSelectedElements"}
    _install(monkeypatch, core)
    payload = await call("get_selection")
    assert payload["guids"] == ["w-1"]
    assert payload["coverage"] == "model-elements-only"
    assert not any(c == "GetSelectedElements" for c, _ in core.calls)


def _selection_change(core):
    changes = [p for c, p in core.calls if c == "ChangeSelectionOfElements"]
    assert len(changes) == 1
    return changes[0]


async def test_clear_selection_deselects_a_selected_mep_route(monkeypatch):
    core = make_core(selected=("w-1", MEP_ROUTE))
    core.tapir_responses["ChangeSelectionOfElements"] = {}
    _install(monkeypatch, core)
    payload = await call("clear_selection")
    assert payload["cleared"] == 2
    removed = _selection_change(core)["removeElementsFromSelection"]
    assert {"elementId": {"guid": MEP_ROUTE}} in removed


async def test_set_selection_replaces_a_selected_mep_route(monkeypatch):
    """'Replace' must not quietly become 'append' for an unsupported type."""
    core = make_core(selected=(MEP_ROUTE,))
    core.tapir_responses["ChangeSelectionOfElements"] = {}
    _install(monkeypatch, core)
    await call("set_selection", {"guids": ["w-2"]})
    change = _selection_change(core)
    assert change["removeElementsFromSelection"] == [{"elementId": {"guid": MEP_ROUTE}}]
    assert change["addElementsToSelection"] == [{"elementId": {"guid": "w-2"}}]


# ---------- get_model_summary ----------

async def test_model_summary_counts_the_whole_plan(core):
    payload = await call("get_model_summary")
    assert payload["element_count"] == 5
    assert payload["by_type"] == {"Wall": 2, "Zone": 1, "InteriorElevation": 1,
                                  "Unknown": 1}
    assert payload["coverage"] == "whole-plan"


async def test_model_summary_flags_partial_coverage_without_tapir(monkeypatch):
    """element_count must not read as a project total when it isn't one."""
    _install(monkeypatch, make_core(tapir_on=False))
    payload = await call("get_model_summary")
    assert payload["element_count"] == 3
    assert payload["coverage"] == "model-elements-only"
    assert "Tapir" in payload["coverage_note"]


# ---------- element types: what the official API cannot name ----------

def _conn(core):
    return ArchicadConnection(19723, core=core)


def test_types_come_from_tapir_where_the_official_api_refuses():
    core = make_core()
    types = extract._fetch_types(_conn(core), ["w-1", "ie-1", MEP_ROUTE, "gone-1"])
    assert types == {"w-1": "Wall", "ie-1": "InteriorElevation", MEP_ROUTE: "Unknown"}
    asked = [el["elementId"]["guid"] for c, p in core.calls
             if c == "GetDetailsOfElements" for el in p["elements"]]
    assert asked == ["ie-1", MEP_ROUTE]  # only what the official read refused


def test_types_without_tapir_still_keep_elements_that_exist():
    """7203 means 'exists, no official type'; only 7204 means 'not found'."""
    types = extract._fetch_types(_conn(make_core(tapir_on=False)),
                                 ["w-1", "ie-1", MEP_ROUTE, "gone-1"])
    assert types == {"w-1": "Wall", "ie-1": "Unknown", MEP_ROUTE: "Unknown"}


def test_types_when_tapir_cannot_answer_the_type_read():
    """If Tapir refuses the type read (a Tapir older than 1.5.7 has no
    `fields`), the elements still exist, so they stay in, labelled Unknown."""
    core = make_core()
    def refuse(p):
        raise TapirCommandError(message="Invalid parameters", code=-2130313112)
    core.tapir_responses["GetDetailsOfElements"] = refuse
    types = extract._fetch_types(_conn(core), ["w-1", "ie-1", MEP_ROUTE])
    assert types == {"w-1": "Wall", "ie-1": "Unknown", MEP_ROUTE: "Unknown"}


def test_reserve_does_not_call_an_mep_route_not_found():
    """The write-path consequence: reserve_elements derived not_found from the
    official type read, so a route (or any 2D element) was never attempted."""
    from archicad_mcp.core.teamwork import reserve_elements
    core = make_core()
    core.tapir_responses["GetProjectInfo"] = {
        **api_replays.TAPIR["GetProjectInfo"], "isTeamwork": True}
    core.tapir_responses["FilterElements"] = {"elements": []}  # nothing reserved yet
    result = reserve_elements(_conn(core), ["w-1", "ie-1", MEP_ROUTE, "gone-1"])
    assert result["not_found"] == ["gone-1"]
    assert result["would_attempt"] == ["w-1", "ie-1", MEP_ROUTE]
