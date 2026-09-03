"""find_elements must be schema-typed: element types and operators as enums,
so a client validates before sending and a typo never reaches Archicad."""
import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.query_schema import (
    ComparisonSpec,
    GroupSpec,
    element_type_names,
    groups_to_dicts,
)
from archicad_mcp.criteria import OPERATORS
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def test_element_type_names_come_from_the_bundled_schema():
    names = element_type_names()
    assert names[0] == "all"
    assert {"Wall", "Zone", "CutPlane", "InteriorElevation", "Railing"} <= set(names)


def test_groups_to_dicts_drops_the_absent_value():
    g = GroupSpec(element_types=["Wall"], comparisons=[
        ComparisonSpec(property="OFFICE/Fire Rating", operator="has_value"),
        ComparisonSpec(property="story", operator="equal", value=-1)])
    d = groups_to_dicts([g])
    assert d == [{"element_types": ["Wall"], "element_types_operator": "is",
                  "logical_operator": "and",
                  "comparisons": [{"property": "OFFICE/Fire Rating", "operator": "has_value"},
                                  {"property": "story", "operator": "equal", "value": -1}]}]


async def _schema():
    async with Client(build_server(mode="full")) as client:
        tools = {t.name: t for t in await client.list_tools()}
        return tools["find_elements"].inputSchema


def _resolve(schema, node):
    while "$ref" in node:
        node = schema["$defs"][node["$ref"].split("/")[-1]]
    return node


async def test_the_schema_enumerates_operators_and_element_types():
    schema = await _schema()
    group = _resolve(schema, schema["properties"]["groups"]["items"])
    assert group.get("additionalProperties") is False
    types = _resolve(schema, group["properties"]["element_types"])
    enum_holder = next(v for v in types.get("anyOf", [types]) if v.get("type") == "array")
    assert set(_resolve(schema, enum_holder["items"])["enum"]) == set(element_type_names())
    comparison = _resolve(schema, group["properties"]["comparisons"]["items"])
    assert set(_resolve(schema, comparison["properties"]["operator"])["enum"]) == OPERATORS
    assert group["properties"]["element_types_operator"]["enum"] == ["is", "is_not"]
    assert group["properties"]["logical_operator"]["enum"] == ["and", "or"]


async def test_a_bad_operator_is_rejected_before_any_archicad_call(monkeypatch):
    core = FakeCore(official=dict(api_replays.OFFICIAL), tapir=dict(api_replays.TAPIR))
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    async with Client(build_server(mode="full")) as client:
        result = await client.call_tool("find_elements", {"groups": [{
            "element_types": ["Wall"],
            "comparisons": [{"property": "x", "operator": "like", "value": "y"}]}]},
            raise_on_error=False)
        assert result.is_error
        result = await client.call_tool("find_elements", {"groups": [{
            "element_types": ["Walls"]}]}, raise_on_error=False)
        assert result.is_error
    assert core.calls == []


async def test_a_typed_query_still_runs(monkeypatch):
    core = FakeCore(official=dict(api_replays.OFFICIAL), tapir=dict(api_replays.TAPIR))
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    async with Client(build_server(mode="full")) as client:
        result = await client.call_tool("find_elements", {"groups": [{
            "element_types": ["Wall"],
            "comparisons": [{"property": "ModelView_LayerName", "operator": "equal",
                             "value": "Sketch"}]}]})
        assert json.loads(result.content[0].text)["guids"] == ["w-2"]
