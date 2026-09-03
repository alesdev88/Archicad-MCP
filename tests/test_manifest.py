"""The manifest's advertised tool list must match what the server registers."""

import json
from pathlib import Path

from archicad_mcp.server import build_server

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"


async def test_manifest_lists_every_registered_tool(tmp_path):
    raw = json.loads(MANIFEST.read_text())
    advertised = {t["name"] for t in raw["tools"]}
    server = build_server(mode="full", gdl_workspace=tmp_path)
    tools = await server.list_tools()
    registered = {tool.name for tool in tools}
    assert registered == advertised


def test_manifest_declares_the_gdl_workspace_field():
    raw = json.loads(MANIFEST.read_text())
    field = raw["user_config"]["gdl_workspace"]
    assert field["type"] == "directory"
    assert raw["server"]["mcp_config"]["env"]["ARCHICAD_MCP_GDL_WORKSPACE"] == \
        "${user_config.gdl_workspace}"
