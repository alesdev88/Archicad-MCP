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
    official["API.SetPropertyValuesOfElements"] = {"executionResults": [{"success": True}]}
    tapir = dict(api_replays.TAPIR)
    tapir["GetSelectedElements"] = selection  # the source once Tapir is present
    core = FakeCore(official=official, tapir=tapir)
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def call(tool, args=None):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


async def test_full_mode_registers_tier2(core):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert {"find_elements", "search_definitions", "get_element_data",
            "set_element_data"} <= names
    assert "query_elements" not in names  # replaced in 0.4.0, not kept alongside


async def test_verdicts_mode_hides_tier2(core):
    mcp = build_server(mode="verdicts")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "find_elements" not in names


async def test_find_by_type(core):
    payload = await call("find_elements", {"groups": [{"element_types": ["Wall"]}]})
    assert payload["count"] == 2 and set(payload["guids"]) == {"w-1", "w-2"}


async def test_find_by_type_and_layer(core):
    payload = await call("find_elements", {"groups": [{
        "element_types": ["Wall"],
        "comparisons": [{"property": "ModelView_LayerName", "operator": "equal",
                         "value": "Sketch"}]}]})
    assert payload["guids"] == ["w-2"]


async def test_find_selection_only(core):
    payload = await call("find_elements", {"groups": [{"element_types": ["Wall", "Zone"]}],
                                           "selection_only": True})
    assert payload["guids"] == ["w-1"]


async def test_find_rejects_a_malformed_query_without_touching_archicad(core):
    """A query the schema accepts but the language rejects (a unary operator
    given a value) must come back as an error before any Archicad call.
    Unknown operators and type names never get this far: the typed schema
    refuses them (tests/test_query_schema.py)."""
    payload = await call("find_elements", {"groups": [{"comparisons": [
        {"property": "x", "operator": "has_value", "value": 1}]}]})
    assert "takes no 'value'" in payload["error"]
    assert core.calls == []


async def test_get_element_data_returns_values(core):
    payload = await call("get_element_data",
                         {"guids": ["w-1"], "properties": ["OFFICE/Fire Rating"],
                          "include_classifications": True})
    el = payload["elements"][0]
    assert el["guid"] == "w-1" and el["type"] == "Wall"
    assert el["properties"]["OFFICE/Fire Rating"] == "EI60"
    assert el["classifications"] == {"ARCHICAD Classification": "c-wall"}


async def test_set_element_data_dry_run_by_default(core):
    payload = await call("set_element_data", {"changes": [
        {"guid": "w-2", "property": "OFFICE/Fire Rating", "value": "EI30"}]})
    assert payload["dry_run"] is True
    assert payload["planned_changes"] == [
        {"guid": "w-2", "property": "OFFICE/Fire Rating",
         "current": None, "new": "EI30"}]
    assert not any(c == "API.SetPropertyValuesOfElements" for c, _ in core.calls)


async def test_set_element_data_commit(core):
    payload = await call("set_element_data", {"changes": [
        {"guid": "w-2", "property": "OFFICE/Fire Rating", "value": "EI30"}],
        "dry_run": False})
    assert payload == {"dry_run": False, "applied": 1}
    call_names = [c for c, _ in core.calls]
    assert "API.SetPropertyValuesOfElements" in call_names


async def test_set_element_data_commit_reports_partial_failure(monkeypatch):
    official = dict(api_replays.OFFICIAL)
    official["API.SetPropertyValuesOfElements"] = {
        "executionResults": [{"success": True}, {"success": False}]}
    fake_core = FakeCore(official=official, tapir=dict(api_replays.TAPIR))
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=fake_core))
    payload = await call("set_element_data", {"changes": [
        {"guid": "w-1", "property": "OFFICE/Fire Rating", "value": "EI30"},
        {"guid": "w-2", "property": "OFFICE/Status", "value": "Approved"}],
        "dry_run": False})
    assert payload["applied"] == 1
    assert payload["failed"] == 1
    assert "skipped" not in payload


async def test_set_element_data_commit_skips_unresolved_property(monkeypatch):
    official = dict(api_replays.OFFICIAL)
    official["API.GetPropertyIds"] = {
        "properties": [{"error": {"code": 1, "message": "not found"}}]}
    official["API.SetPropertyValuesOfElements"] = {"executionResults": []}
    fake_core = FakeCore(official=official, tapir=dict(api_replays.TAPIR))
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=fake_core))
    payload = await call("set_element_data", {"changes": [
        {"guid": "w-1", "property": "NoSuch/Property", "value": "x"}],
        "dry_run": False})
    assert payload["dry_run"] is False and payload["applied"] == 0
    assert len(payload["skipped"]) == 1
    skipped = payload["skipped"][0]
    assert skipped["guid"] == "w-1" and skipped["property"] == "NoSuch/Property"
    assert "resolve" in skipped["reason"]  # says WHY it was dropped
    call_names = [c for c, _ in fake_core.calls]
    assert "API.SetPropertyValuesOfElements" not in call_names
