import json
from pathlib import Path

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp import tapir_requirements as req
from archicad_mcp.connection import ArchicadConnection, probe_port
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.server import build_server
from archicad_mcp.tapir_install.release import load_pinned
from tests.conftest import FakeCore

pytestmark = pytest.mark.usefixtures("mac_host")

DEFS = Path(req.__file__).parent / "gateway" / "definitions"


def _v(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split("."))


def fake_conn(missing: set[str] = frozenset(), *, tapir: bool = True, version: str = "1.5.9"):
    def available(params):
        name = params["addOnCommandId"]["commandName"]
        return {"available": tapir and name not in missing}
    core = FakeCore(official={"API.GetProductInfo": {"version": 29, "buildNumber": 5101},
                              "API.IsAddOnCommandAvailable": available},
                    tapir={"GetAddOnVersion": {"version": version},
                           "GetProjectInfo": {"projectName": "P"}})
    return ArchicadConnection(19723, core=core), core


async def test_every_gated_tool_is_registered(tmp_path):
    server = build_server(mode="full", gdl_workspace=tmp_path, enable_scripts=True)
    names = {t.name for t in await server.list_tools()}
    assert set(req.TOOL_COMMANDS) <= names
    assert "create_elements" not in req.TOOL_COMMANDS


def test_every_command_has_a_version_or_is_fork_only():
    for tool, commands in req.TOOL_COMMANDS.items():
        for c in commands:
            assert c in req.FORK_ONLY or req.command_version(c), f"{tool}: {c}"


def test_explicit_versions_only_cover_commands_the_definitions_lack():
    registry = build_registry()
    for name, (version, reason) in req.EXPLICIT_VERSIONS.items():
        assert reason
        assert not (name in registry and registry[name].version), (
            f"{name}: the synced definitions now carry it (since "
            f"{registry[name].version}); drop the explicit entry")


def test_fork_only_comes_from_the_local_overlay():
    assert {"CreateRailings", "GetStairBoundaries"} <= req.FORK_ONLY
    assert req.command_version("CreateRailings") is None


def test_pinned_release_meets_every_release_backed_minimum():
    pinned = _v(load_pinned().version)
    for tool in req.TOOL_COMMANDS:
        minimum, _ = req.tool_minimum(tool)
        if minimum is not None:
            assert pinned >= _v(minimum), tool
    synced = json.loads((DEFS / "tapir_version.json").read_text())["version"]
    assert pinned >= _v(synced)


def test_tool_minimum_picks_the_newest_command():
    assert req.tool_minimum("edit_classifications") == ("1.6.0", "UpdateClassificationItems")
    assert req.tool_minimum("create_swept_beam")[0] == "1.5.7"


def test_refusal_names_both_versions():
    conn, _ = fake_conn({"UpdateClassificationItems"})
    error = req.tapir_refusal(conn, "edit_classifications")["error"]
    for part in ("1.6.0", "1.5.9", "UpdateClassificationItems", "install-tapir"):
        assert part in error


def test_no_refusal_when_the_command_is_there():
    conn, _ = fake_conn()
    assert req.tapir_refusal(conn, "edit_classifications") is None


def test_refusal_when_tapir_is_missing():
    conn, _ = fake_conn(tapir=False)
    error = req.tapir_refusal(conn, "move_elements")["error"]
    assert "requires the Tapir add-on" in error and "install-tapir --archicad 29" in error


def test_fork_only_refusal_says_no_release():
    conn, _ = fake_conn({"CreateRailings"})
    error = req.require_tapir_command(conn, "CreateRailings", "create_elements")["error"]
    assert "no Tapir release has yet" in error and "install-tapir" not in error


def test_unavailable_tools_lists_editing_tools_on_old_tapir():
    conn, _ = fake_conn({"UpdateClassificationItems"})
    assert req.unavailable_tools(conn) == [
        "edit_classifications", "edit_property_definitions", "import_definitions"]


def test_unavailable_tools_probes_only_what_the_version_cannot_promise():
    conn, core = fake_conn(version=load_pinned().version)
    assert req.unavailable_tools(conn) == []
    assert [c for c, _ in core.calls].count("API.IsAddOnCommandAvailable") == 1  # the Tapir probe


def test_a_fork_with_the_commands_is_not_called_outdated():
    # Reports 1.5.10, has the 1.6.0 editing commands (the Archicad 29 fork).
    conn, _ = fake_conn(version="1.5.10")
    assert req.unavailable_tools(conn) == []


def test_probe_port_reports_tools_needing_newer_tapir():
    _, core = fake_conn({"UpdateClassificationItems"}, version="1.5.9")
    info = probe_port(19723, core=core)
    assert info.tools_needing_newer_tapir == [
        "edit_classifications", "edit_property_definitions", "import_definitions"]
    assert "custom Tapir build" in info.tapir_hint


async def test_gated_tool_sends_nothing_after_refusal(monkeypatch, tmp_path):
    conn, core = fake_conn(tapir=False)
    monkeypatch.setattr(server_mod, "get_connection", lambda port: conn)
    async with Client(build_server(mode="full")) as client:
        result = await client.call_tool("move_elements", {
            "guids": ["a"], "vector": {"x": 1, "y": 0, "z": 0}, "confirm": True})
    payload = json.loads(result.content[0].text)
    assert "requires the Tapir add-on" in payload["error"]
    assert {c for c, _ in core.calls} <= {"API.IsAddOnCommandAvailable", "API.GetProductInfo"}


@pytest.mark.parametrize("tool", ["edit_property_definitions", "edit_classifications"])
def test_editing_gate_uses_the_shared_refusal(tool):
    from archicad_mcp.core.definition_edit import editing_unavailable
    conn, _ = fake_conn({"UpdateClassificationItems"})
    error = editing_unavailable(conn, tool)["error"]
    assert f"'{tool}' needs Tapir 1.6.0" in error


def test_a_command_first_released_in_1_7_2_is_outdated_on_1_7_1():
    # The 1.7.2 definitions label SaveCurrentWindowAsImage 1.7.1, but the 1.7.1
    # release has no such command (live 21.10.2026, MCP-Test on Tapir 1.7.1).
    conn, _ = fake_conn({"SaveCurrentWindowAsImage"}, version="1.7.1")
    error = req.require_tapir_command(conn, "SaveCurrentWindowAsImage", "x")["error"]
    assert "1.7.2 or newer" in error and "install-tapir" in error


def test_version_corrections_still_correct_the_definitions():
    from archicad_mcp.gateway.registry import CORRECTED_VERSIONS, DEFINITIONS_DIR, _load_js_json
    groups = _load_js_json(DEFINITIONS_DIR / "command_definitions.js", "gCommands")
    labels = {c["name"]: c.get("version") for g in groups for c in g["commands"]}
    for name, (version, reason) in CORRECTED_VERSIONS.items():
        assert reason
        assert _v(labels[name]) < _v(version), (
            f"{name}: the definitions now say {labels[name]}; drop the correction")


def test_every_reader_of_the_registry_sees_the_corrected_version():
    # describe_api_command and the dashboard read the registry, as the gates do.
    assert build_registry()["SaveCurrentWindowAsImage"].version == "1.7.2"


def test_outdated_refusal_and_instance_hint_say_replace():
    conn, core = fake_conn({"UpdateClassificationItems"})
    assert "--replace" in req.tapir_refusal(conn, "edit_classifications")["error"]
    assert "--replace" in probe_port(19723, core=core).tapir_hint
