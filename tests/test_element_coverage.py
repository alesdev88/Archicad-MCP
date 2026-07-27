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

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays

# The fixture plan: three model elements plus one 2D marker that only Tapir sees.
TYPES = {"w-1": "Wall", "w-2": "Wall", "z-1": "Zone", "ie-1": "InteriorElevation"}
MODEL_ONLY = ["w-1", "w-2", "z-1"]
WHOLE_PLAN = MODEL_ONLY + ["ie-1"]


def _elements(guids):
    return {"elements": [{"elementId": {"guid": g}} for g in guids]}


def make_core(tapir_on=True, selected=()):
    official = dict(api_replays.OFFICIAL)
    official["API.GetAllElements"] = _elements(MODEL_ONLY)
    official["API.GetSelectedElements"] = _elements(
        [g for g in selected if g in MODEL_ONLY])
    official["API.GetTypesOfElements"] = lambda p: {"typesOfElements": [
        {"typeOfElement": {"elementId": el["elementId"],
                           "elementType": TYPES[el["elementId"]["guid"]]}}
        for el in p["elements"]]}
    if not tapir_on:
        official["API.IsAddOnCommandAvailable"] = {"available": False}
    tapir = dict(api_replays.TAPIR)
    tapir["GetAllElements"] = _elements(WHOLE_PLAN)
    tapir["GetSelectedElements"] = _elements(selected)
    tapir["GetElementsByType"] = lambda p: _elements(
        [g for g, t in TYPES.items() if t == p["elementType"]])
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


# ---------- query_elements ----------

async def test_query_by_type_finds_a_2d_marker(core):
    """The regression: this used to return a silent count of 0."""
    payload = await call("query_elements", {"element_type": "InteriorElevation"})
    assert payload["count"] == 1
    assert payload["guids"] == ["ie-1"]


async def test_query_by_type_does_not_sweep_types_of_the_whole_plan(core):
    """Asking Tapir for one type replaces 'fetch everything, filter here'."""
    await call("query_elements", {"element_type": "Wall"})
    assert not any(c == "API.GetTypesOfElements" for c, _ in core.calls)
    assert any(c == "GetElementsByType" for c, _ in core.calls)


async def test_query_unfiltered_covers_the_whole_plan(core):
    payload = await call("query_elements")
    assert payload["count"] == 4
    assert payload["by_type"]["InteriorElevation"] == 1
    assert payload["coverage"] == "whole-plan"


async def test_query_selection_sees_a_selected_marker(monkeypatch):
    _install(monkeypatch, make_core(selected=("ie-1",)))
    payload = await call("query_elements", {"selection_only": True})
    assert payload["guids"] == ["ie-1"]


async def test_query_without_tapir_says_coverage_is_partial(monkeypatch):
    _install(monkeypatch, make_core(tapir_on=False))
    payload = await call("query_elements")
    assert payload["count"] == 3
    assert payload["coverage"] == "model-elements-only"
    assert "Tapir" in payload["coverage_note"]


async def test_query_by_type_without_tapir_still_filters(monkeypatch):
    _install(monkeypatch, make_core(tapir_on=False))
    payload = await call("query_elements", {"element_type": "Wall"})
    assert set(payload["guids"]) == {"w-1", "w-2"}
    assert payload["coverage"] == "model-elements-only"


# ---------- get_model_summary ----------

async def test_model_summary_counts_the_whole_plan(core):
    payload = await call("get_model_summary")
    assert payload["element_count"] == 4
    assert payload["by_type"]["InteriorElevation"] == 1
    assert payload["coverage"] == "whole-plan"


async def test_model_summary_flags_partial_coverage_without_tapir(monkeypatch):
    """element_count must not read as a project total when it isn't one."""
    _install(monkeypatch, make_core(tapir_on=False))
    payload = await call("get_model_summary")
    assert payload["element_count"] == 3
    assert payload["coverage"] == "model-elements-only"
    assert "Tapir" in payload["coverage_note"]
