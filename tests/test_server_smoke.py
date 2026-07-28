from fastmcp import Client

from archicad_mcp.server import build_server


async def test_server_builds_and_lists_tools():
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "list_rules" in names


async def test_full_mode_registers_the_schedule_scheme_tools():
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "read_schedule_scheme" in names


async def test_verdicts_mode_omits_the_schedule_scheme_tools():
    mcp = build_server(mode="verdicts")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "read_schedule_scheme" not in names
