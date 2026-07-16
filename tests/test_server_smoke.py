from fastmcp import Client

from archicad_mcp.server import build_server


async def test_server_builds_and_lists_tools():
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "list_rules" in names
