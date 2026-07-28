import json
from pathlib import Path

import pytest
from fastmcp import Client

import archicad_mcp.core.schemes as core_schemes
from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.schemes.model import parse_scheme
from archicad_mcp.schemes.validate import property_index, validate_scheme
from archicad_mcp.schemes.xml_io import load_scheme_tree
from archicad_mcp.server import build_server
from tests.conftest import FakeCore

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"

ALL_PROPERTIES = {
    "properties": [
        {"propertyId": {"guid": "69A58F6F-1111-4000-8000-000000000001"},
         "propertyGroupName": "OFFICE", "propertyName": "Door ID"},
        {"propertyId": {"guid": "432FA53A-B71E-404B-A9D5-F1964237A3EB"},
         "propertyGroupName": "OFFICE", "propertyName": "Fire Rating"},
    ]
}


def conn_with(properties=ALL_PROPERTIES):
    # conn.tapir() gates on tapir_available(), which probes via the OFFICIAL
    # table, so the fake has to answer that too or every call raises.
    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}},
                    tapir={"GetAllProperties": properties})
    return ArchicadConnection(19723, core=core)


def conn_without_tapir():
    # The probe itself answers False: no Tapir table needed, since
    # conn.tapir() raises before ever consulting it.
    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": False}})
    return ArchicadConnection(19723, core=core)


def load():
    return parse_scheme(load_scheme_tree(FIXTURE))


def test_property_index_maps_group_slash_name_to_guid():
    index = property_index(conn_with())
    assert index["OFFICE/Door ID"] == "69A58F6F-1111-4000-8000-000000000001"


def test_resolvable_property_column_produces_no_finding():
    findings = validate_scheme(conn_with(), load())
    assert not [f for f in findings if f["column"] == "Door ID"]


def test_unresolvable_property_guid_is_reported():
    empty = {"properties": []}
    findings = validate_scheme(conn_with(empty), load())
    door = [f for f in findings if f["column"] == "Door ID"]
    assert door and door[0]["severity"] == "error"
    assert "does not exist" in door[0]["message"]


def test_caption_disagreeing_with_binding_is_reported():
    # The fixture's "Fire Resistance" column binds to "Fire Rating Param".
    findings = validate_scheme(conn_with(), load())
    mismatch = [f for f in findings if f["column"] == "Fire Resistance"]
    assert mismatch and mismatch[0]["severity"] == "warning"


def test_builtin_columns_are_not_flagged():
    findings = validate_scheme(conn_with(), load())
    assert not [f for f in findings if f["column"] == "Quantity"]


# --- Tapir is optional in this repo (see connection.py: tapir_available(),
# tapir_command_available()), and most tools degrade rather than erroring
# when it is absent (get_project_info returns a "note" instead). But
# GetAllProperties is the only way to resolve a property binding, so there is
# nothing to degrade to: without it, "does this GUID still exist" is
# unanswerable. The chosen behaviour is to let ArchicadConnection.tapir()'s
# own ArchicadUnavailableError propagate untouched, all the way out of
# validate_scheme/validate_schedule_scheme, exactly like every other
# Tapir-only core function in this codebase (core/project.py's guarded
# fields, the gateway's execute_api_command). That message already names the
# missing command, explains why, and links the add-on to install, so nothing
# here needs to add its own wording. Only the registered tool converts it to
# an {"error": ...} envelope, via @_guarded in server.py, same as every other
# tool that talks to Archicad. ---

def test_property_index_treats_null_properties_as_empty():
    """When Tapir returns null for properties, treat as no properties."""
    null_response = {"properties": None}
    index = property_index(conn_with(null_response))
    assert index == {}


def test_property_index_treats_dict_properties_as_empty():
    """When Tapir incorrectly returns a dict instead of list for properties,
    treat as no properties rather than crashing."""
    dict_response = {"properties": {"some_key": "some_value"}}
    index = property_index(conn_with(dict_response))
    assert index == {}


def test_property_index_raises_a_clear_actionable_error_when_tapir_is_absent():
    with pytest.raises(ArchicadUnavailableError) as exc_info:
        property_index(conn_without_tapir())
    message = str(exc_info.value)
    assert "GetAllProperties" in message
    assert "Tapir add-on" in message
    assert "Install it from" in message


def test_validate_scheme_raises_a_clear_actionable_error_when_tapir_is_absent():
    with pytest.raises(ArchicadUnavailableError, match="GetAllProperties"):
        validate_scheme(conn_without_tapir(), load())


# --- core_schemes.validate_schedule_scheme wiring ---

def test_validate_schedule_scheme_happy_path(monkeypatch):
    monkeypatch.setattr(core_schemes, "get_connection", lambda port: conn_with())
    out = core_schemes.validate_schedule_scheme(str(FIXTURE))
    assert out["name"] == "Sample Door Scheme"
    assert out["column_count"] == 3
    assert out["ok"] is True  # only a warning, no error, with the default properties
    assert len(out["findings"]) == 1
    assert out["findings"][0]["severity"] == "warning"


def test_validate_schedule_scheme_ok_is_false_when_a_binding_is_unresolvable(monkeypatch):
    monkeypatch.setattr(core_schemes, "get_connection",
                        lambda port: conn_with({"properties": []}))
    out = core_schemes.validate_schedule_scheme(str(FIXTURE))
    assert out["ok"] is False
    assert any(f["severity"] == "error" for f in out["findings"])


def test_validate_schedule_scheme_missing_file_short_circuits_before_any_connection(
        monkeypatch):
    """_load() must fail and return before get_connection is ever consulted.
    A poison pill here means the test fails loudly, rather than the real
    get_connection quietly reaching for a live Archicad, if that ordering
    ever regresses."""
    def must_not_be_called(port):
        raise AssertionError("get_connection must not be called: _load() should "
                             "have already rejected this path")

    monkeypatch.setattr(core_schemes, "get_connection", must_not_be_called)
    out = core_schemes.validate_schedule_scheme("/nonexistent/nope.xml")
    assert "error" in out
    assert "not found" in out["error"].lower()


def test_validate_schedule_scheme_core_raises_when_tapir_is_absent(monkeypatch):
    """The core function has no try/except of its own for Tapir errors: only
    the registered tool (decorated with @_guarded in server.py) turns this
    into an error envelope. Unlike read/edit, this function's "always
    returns a dict" contract is completed one layer up, not here."""
    monkeypatch.setattr(core_schemes, "get_connection", lambda port: conn_without_tapir())
    with pytest.raises(ArchicadUnavailableError, match="GetAllProperties"):
        core_schemes.validate_schedule_scheme(str(FIXTURE))


# --- the registered tool: @_guarded must convert the above into a dict ---

async def call(mcp, tool, args=None):
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


async def test_tool_converts_missing_tapir_to_an_error_envelope(monkeypatch):
    monkeypatch.setattr(core_schemes, "get_connection", lambda port: conn_without_tapir())
    payload = await call(build_server(mode="full"), "validate_schedule_scheme",
                         {"path": str(FIXTURE)})
    assert "error" in payload
    assert "GetAllProperties" in payload["error"]
    assert "Tapir add-on" in payload["error"]


async def test_tool_happy_path_through_the_full_server(monkeypatch):
    monkeypatch.setattr(core_schemes, "get_connection", lambda port: conn_with())
    payload = await call(build_server(mode="full"), "validate_schedule_scheme",
                         {"path": str(FIXTURE)})
    assert payload["name"] == "Sample Door Scheme"
    assert payload["ok"] is True


async def test_default_port_is_used_when_the_caller_omits_it(monkeypatch):
    seen = {}

    def fake_get_connection(port):
        seen["port"] = port
        return conn_with()

    monkeypatch.setattr(core_schemes, "get_connection", fake_get_connection)
    mcp = build_server(mode="full", port=19730)
    await call(mcp, "validate_schedule_scheme", {"path": str(FIXTURE)})
    assert seen["port"] == 19730


async def test_explicit_port_overrides_the_default(monkeypatch):
    seen = {}

    def fake_get_connection(port):
        seen["port"] = port
        return conn_with()

    monkeypatch.setattr(core_schemes, "get_connection", fake_get_connection)
    mcp = build_server(mode="full", port=19730)
    await call(mcp, "validate_schedule_scheme", {"path": str(FIXTURE), "port": 19740})
    assert seen["port"] == 19740
