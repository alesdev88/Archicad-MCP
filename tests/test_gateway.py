import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def test_registry_contains_both_kinds():
    registry = build_registry()
    assert "API.GetAllElements" in registry
    assert registry["API.GetAllElements"].kind == "official"
    tapir_names = [c.name for c in registry.values() if c.kind == "tapir"]
    assert "GetProjectInfo" in tapir_names
    assert len(tapir_names) >= 80  # current Tapir ships 100+ commands


def test_tapir_commands_have_resolved_schemas():
    registry = build_registry()
    with_schema = [c for c in registry.values()
                   if c.kind == "tapir" and c.input_schema is not None]
    assert with_schema, "at least some Tapir commands declare input schemas"
    sample = json.dumps([c.input_schema for c in with_schema])
    assert "$ref" not in sample, "all $ref pointers must be resolved"


@pytest.fixture
def core(monkeypatch):
    tapir = dict(api_replays.TAPIR)
    tapir["GetStories"] = {"stories": []}
    core = FakeCore(official=dict(api_replays.OFFICIAL), tapir=tapir)
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def call(tool, args=None):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


async def test_list_api_commands_grouped(core):
    payload = await call("list_api_commands")
    assert "groups" in payload and len(payload["commands"]) > 100


async def test_list_api_commands_filter_by_group(core):
    payload = await call("list_api_commands", {"group": "Official JSON API"})
    assert all(c["group"] == "Official JSON API" for c in payload["commands"])


async def test_describe_known_command(core):
    payload = await call("describe_api_command", {"name": "GetProjectInfo"})
    assert payload["name"] == "GetProjectInfo" and payload["kind"] == "tapir"


async def test_describe_unknown_suggests(core):
    payload = await call("describe_api_command", {"name": "GetProjInfo"})
    assert "GetProjectInfo" in payload["error"]


async def test_execute_routes_official(core):
    payload = await call("execute_api_command", {"name": "API.GetAllElements"})
    assert len(payload["elements"]) == 3


async def test_execute_routes_tapir(core):
    payload = await call("execute_api_command", {"name": "GetStories"})
    assert payload == {"stories": []}


async def test_execute_accepts_params_as_a_json_string(core):
    """Clients that collapse a nullable object field to an untyped schema send
    the value as text. That is their bug, but it makes the tool unusable, so
    the string is parsed rather than rejected."""
    payload = await call("execute_api_command", {
        "name": "API.GetTypesOfElements",
        "params": '{"elements": [{"elementId": {"guid": "w-1"}}]}'})
    assert payload["typesOfElements"][0]["typeOfElement"]["elementType"] == "Wall"
    _, params = [c for c in core.calls if c[0] == "API.GetTypesOfElements"][0]
    assert params == {"elements": [{"elementId": {"guid": "w-1"}}]}


async def test_execute_validates_params_parsed_from_a_string(core):
    """The parsed value still goes through schema validation."""
    registry = build_registry()
    name = next(c.name for c in registry.values()
                if c.kind == "tapir" and c.input_schema
                and c.input_schema.get("required"))
    payload = await call("execute_api_command", {"name": name, "params": "{}"})
    assert "error" in payload and "schema" in payload


async def test_execute_rejects_params_string_that_is_not_json(core):
    payload = await call("execute_api_command", {
        "name": "API.GetTypesOfElements", "params": "elements=w-1"})
    assert "params" in payload["error"] and "JSON" in payload["error"]
    assert not any(c == "API.GetTypesOfElements" for c, _ in core.calls)


async def test_execute_validates_tapir_params(core):
    registry = build_registry()
    # pick a Tapir command with a schema declaring required fields
    candidates = [c for c in registry.values()
                  if c.kind == "tapir" and c.input_schema
                  and c.input_schema.get("required")]
    assert candidates
    name = candidates[0].name
    payload = await call("execute_api_command", {"name": name, "params": {}})
    assert "error" in payload and "schema" in payload
