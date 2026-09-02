import json

import jsonschema
import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
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


def test_local_overlay_commands_are_registered():
    registry = build_registry()
    assert "CreateRailings" in registry
    assert registry["CreateRailings"].access == "write"
    assert registry["GetStairBoundaries"].access == "read"


def test_local_overlay_commands_carry_a_schema():
    registry = build_registry()
    schema = registry["CreateRailings"].input_schema
    assert schema is not None
    assert "railingsData" in schema["properties"]


def test_get_stair_boundaries_schema_accepts_the_addons_payload_shape():
    """GetStairBoundaries lives only in the local overlay, and the overlay once
    described "stairs" as a bare {"guid": ...} list (the #/ElementId shape)
    while the add-on's Execute reads each item's "elementId" field (the
    #/ElementIdArrayItem shape, {"elementId": {"guid": ...}}). Both halves of
    that mismatch were only caught by actually validating a realistic payload
    with jsonschema.validate, the same call execute.py makes: checking that the
    schema merely resolves its $ref would have passed either way.
    """
    registry = build_registry()
    schema = registry["GetStairBoundaries"].input_schema
    payload = {"stairs": [{"elementId": {"guid": "00000000-0000-0000-0000-000000000001"}}]}
    jsonschema.validate(payload, schema)  # must not raise

    # And the shape the add-on actually rejects (a bare guid, no "elementId"
    # wrapper) must fail validation, so the schema is not just permissive.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"stairs": [{"guid": "00000000-0000-0000-0000-000000000001"}]}, schema)


def _read_command_with_required_params() -> str:
    """A read command whose schema declares required fields.

    Picked from the registry rather than hardcoded, so the test keeps testing
    validation after a Tapir definitions refresh reshuffles the catalog. It has
    to be a read command specifically: the write tool would refuse it for want
    of confirm=true and never reach the validation this exercises.
    """
    registry = build_registry()
    return next(c.name for c in registry.values()
                if c.kind == "tapir" and c.access == "read" and c.input_schema
                and c.input_schema.get("required"))


@pytest.fixture
def core(monkeypatch):
    tapir = dict(api_replays.TAPIR)
    tapir["GetStories"] = {"stories": []}
    tapir["HighlightElements"] = {}
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
    payload = await call("execute_read_api_command", {"name": "API.GetAllElements"})
    assert len(payload["elements"]) == 3


async def test_execute_routes_tapir(core):
    payload = await call("execute_read_api_command", {"name": "GetStories"})
    assert payload == {"stories": []}


async def test_execute_accepts_params_as_a_json_string(core):
    """Clients that collapse a nullable object field to an untyped schema send
    the value as text. That is their bug, but it makes the tool unusable, so
    the string is parsed rather than rejected."""
    payload = await call("execute_read_api_command", {
        "name": "API.GetTypesOfElements",
        "params": '{"elements": [{"elementId": {"guid": "w-1"}}]}'})
    assert payload["typesOfElements"][0]["typeOfElement"]["elementType"] == "Wall"
    _, params = [c for c in core.calls if c[0] == "API.GetTypesOfElements"][0]
    assert params == {"elements": [{"elementId": {"guid": "w-1"}}]}


async def test_execute_validates_params_parsed_from_a_string(core):
    """The parsed value still goes through schema validation."""
    payload = await call("execute_read_api_command",
                         {"name": _read_command_with_required_params(), "params": "{}"})
    assert "error" in payload and "schema" in payload


async def test_execute_rejects_params_string_that_is_not_json(core):
    payload = await call("execute_read_api_command", {
        "name": "API.GetTypesOfElements", "params": "elements=w-1"})
    assert "params" in payload["error"] and "JSON" in payload["error"]
    assert not any(c == "API.GetTypesOfElements" for c, _ in core.calls)


async def test_execute_validates_tapir_params(core):
    payload = await call("execute_read_api_command",
                         {"name": _read_command_with_required_params(), "params": {}})
    assert "error" in payload and "schema" in payload


# ---- the read/write split ----------------------------------------------------
# The two gateway tools carry different annotations: the read one is
# readOnlyHint, which is what lets a client run it without asking the user
# first. These four tests are what stops that annotation from becoming a lie.


async def test_read_tool_refuses_a_write_command(core):
    payload = await call("execute_read_api_command", {"name": "DeleteElements"})
    assert "execute_write_api_command" in payload["error"]
    assert payload["access"] == "write"
    assert not core.calls


async def test_write_tool_refuses_a_read_command(core):
    payload = await call("execute_write_api_command",
                         {"name": "API.GetAllElements", "confirm": True})
    assert "execute_read_api_command" in payload["error"]
    assert not core.calls


async def test_write_tool_refuses_without_confirm(core):
    payload = await call("execute_write_api_command", {"name": "QuitArchicad"})
    assert "confirm=true" in payload["error"]
    # The refusal echoes what it would have run, so the caller can confirm the
    # exact call rather than retyping it from memory.
    assert payload["command"] == "QuitArchicad"
    assert not core.calls


async def test_write_tool_runs_when_confirmed(core):
    # A real GUID, not the fixture's "w-1" shorthand: this command's bundled
    # schema constrains the format, and the point here is that a confirmed call
    # reaches Archicad, not that validation can be tripped.
    guid = "00000000-0000-0000-0000-000000000001"
    payload = await call("execute_write_api_command", {
        "name": "HighlightElements",
        "params": {"elements": [{"elementId": {"guid": guid}}],
                   "highlightedColors": [[255, 0, 0, 255]]},
        "confirm": True})
    assert "error" not in payload
    assert any(cmd == "HighlightElements" for cmd, _ in core.calls)


async def test_gates_answer_without_a_connection(monkeypatch):
    """The refusals must not depend on Archicad being reachable.

    They did once. The connection was built as an argument to the gateway call,
    so it was opened before any gate ran, and on a machine with three copies of
    Archicad open every refusal came back as "several instances running, pass a
    port" instead of what was actually wrong. The offline suite missed it
    because its fixture always connects cleanly, which is exactly the condition
    under which the bug is invisible.
    """
    def refuse(port):
        raise ArchicadUnavailableError("no Archicad running")
    monkeypatch.setattr(server_mod, "get_connection", refuse)

    read_given_write = await call("execute_read_api_command", {"name": "DeleteElements"})
    assert "execute_write_api_command" in read_given_write["error"]

    write_unconfirmed = await call("execute_write_api_command", {"name": "QuitArchicad"})
    assert "confirm=true" in write_unconfirmed["error"]

    write_given_read = await call("execute_write_api_command",
                                  {"name": "API.GetAllElements", "confirm": True})
    assert "execute_read_api_command" in write_given_read["error"]

    # And the connection is still genuinely needed once a command clears them.
    allowed = await call("execute_read_api_command", {"name": "API.GetAllElements"})
    assert "no Archicad running" in allowed["error"]
