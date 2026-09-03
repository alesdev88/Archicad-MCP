"""GDL tools register only in full mode, and only with a workspace configured."""

from pathlib import Path

from archicad_mcp.server import build_server, resolve_gdl_workspace

GDL_TOOLS = {"list_gdl_sources", "inspect_gdl_source", "build_gdl_object",
             "deploy_gdl_object"}


async def _tool_names(server):
    """Get the set of tool names registered on the server."""
    tools = await server.list_tools()
    return {tool.name for tool in tools}


def test_resolve_treats_blank_as_unset():
    assert resolve_gdl_workspace("") is None
    assert resolve_gdl_workspace("   ") is None
    assert resolve_gdl_workspace(None) is None


def test_resolve_returns_a_path():
    assert resolve_gdl_workspace("/tmp/gdl") == Path("/tmp/gdl")


async def test_no_workspace_means_no_gdl_tools():
    server = build_server(mode="full", gdl_workspace=None)
    assert not (GDL_TOOLS & await _tool_names(server))


async def test_full_mode_with_workspace_registers_them(tmp_path):
    server = build_server(mode="full", gdl_workspace=tmp_path)
    assert GDL_TOOLS <= await _tool_names(server)


async def test_verdicts_mode_never_registers_them(tmp_path):
    server = build_server(mode="verdicts", gdl_workspace=tmp_path)
    assert not (GDL_TOOLS & await _tool_names(server))
