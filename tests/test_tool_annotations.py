"""Every tool must declare a title and an accurate safety hint.

This is a submission requirement for the Claude Connectors Directory, but it is
a safety contract first: clients are allowed to run a readOnlyHint tool without
asking the user, and they always prompt for a destructiveHint one. A tool that
writes while claiming readOnlyHint would be granted silent permission to change
someone's model.

The requirement is easy to satisfy once and then lose, because adding a tool is
a one-line decorator and nothing about forgetting the hints fails at runtime.
Hence a test rather than a checklist.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastmcp import Client

from archicad_mcp.server import build_server

# Named rather than derived. Deriving "which tools write" from the code under
# test would make this assert that the code agrees with itself; the whole value
# is in a second, independent statement of the answer.
#
# build_gdl_object and deploy_gdl_object are destructive by this codebase's
# wider-than-MCP-spec definition (writes a file / changes the project), same
# as create_issue. list_gdl_sources and inspect_gdl_source are read-only and
# stay out of this set.
WRITERS = {
    "highlight_failures", "create_issues_from_failures", "set_element_data",
    "create_elements", "move_elements", "delete_elements", "set_selection",
    "clear_selection", "create_issue", "add_issue_comment",
    "attach_elements_to_issue", "export_issues_bcf", "import_issues_bcf",
    "publish", "edit_schedule_scheme", "execute_write_api_command",
    "build_gdl_object", "deploy_gdl_object",
}

# Writes that change only transient application state, never project data or a
# file on disk. Everything else in WRITERS is destructive.
NON_DESTRUCTIVE_WRITERS = {"highlight_failures", "set_selection", "clear_selection"}


async def _tools(mode: str):
    # A workspace is always passed so the GDL tools are covered by every
    # assertion below too. They only actually register in full mode (server.py
    # gates them on mode == "full" regardless of the workspace), so passing one
    # here does not make them appear in verdicts mode; it only stops them from
    # being invisible to this file the way they were before this fix.
    with tempfile.TemporaryDirectory() as tmp:
        async with Client(build_server(mode=mode, gdl_workspace=Path(tmp))) as client:
            return {t.name: t for t in await client.list_tools()}


@pytest.mark.parametrize("mode", ["full", "verdicts"])
async def test_every_tool_declares_a_title_and_a_hint(mode):
    for name, tool in (await _tools(mode)).items():
        ann = tool.annotations
        assert ann is not None, f"{name} has no annotations"
        assert ann.title, f"{name} has no annotations.title"
        assert tool.title, f"{name} has no top-level title"
        assert ann.readOnlyHint is not None, f"{name} declares no readOnlyHint"
        assert ann.destructiveHint is not None, f"{name} declares no destructiveHint"


async def test_hints_match_what_the_tools_actually_do():
    tools = await _tools("full")
    unknown = WRITERS - set(tools)
    assert not unknown, f"WRITERS names tools that do not exist: {sorted(unknown)}"
    for name, tool in tools.items():
        writes = name in WRITERS
        assert tool.annotations.readOnlyHint is not writes, (
            f"{name}: readOnlyHint disagrees with WRITERS")
        expected_destructive = writes and name not in NON_DESTRUCTIVE_WRITERS
        assert tool.annotations.destructiveHint is expected_destructive, (
            f"{name}: destructiveHint should be {expected_destructive}")


async def test_no_tool_both_reads_and_writes_behind_a_parameter():
    """The directory review rejects a tool that dispatches to reads and writes.

    Not something a schema can check, so this pins the specific tools that used
    to have that shape and were split for it. A regression here means someone
    reintroduced manage_selection, manage_issues or the combined gateway.
    """
    names = set(await _tools("full"))
    for gone in ("manage_selection", "manage_issues", "execute_api_command"):
        assert gone not in names, f"{gone} is back, and mixes reads with writes"
    for arrived in ("get_selection", "set_selection", "clear_selection",
                    "list_issues", "create_issue",
                    "execute_read_api_command", "execute_write_api_command"):
        assert arrived in names, f"{arrived} is missing"


async def test_tool_names_fit_the_length_limit():
    """64 characters, per the submission checklist."""
    for name in await _tools("full"):
        assert len(name) <= 64, f"{name} is {len(name)} characters"


async def test_the_dashboard_lists_every_tool():
    """docs/api-dashboard.html is generated from a hand-written tool catalog.

    A second copy of the tool list drifts, and did: the three schedule tools
    shipped and the dashboard never learned about them, so the page understated
    the server for several releases. Nothing about that fails on its own, which
    is what this test is for.
    """
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "build_dashboard.py"
    spec = importlib.util.spec_from_file_location("build_dashboard", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    declared = {t["name"] for t in module.TOOLS}
    live = set(await _tools("full"))
    assert declared == live, (
        f"dashboard is missing {sorted(live - declared)}, "
        f"and lists {sorted(declared - live)} which do not exist")


async def test_the_dashboard_agrees_about_which_tools_write():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "build_dashboard.py"
    spec = importlib.util.spec_from_file_location("build_dashboard", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tools = await _tools("full")
    for entry in module.TOOLS:
        mutates = bool(entry.get("mutates"))
        read_only = tools[entry["name"]].annotations.readOnlyHint
        assert read_only is not mutates, (
            f"{entry['name']}: dashboard says mutates={mutates}, "
            f"annotation says readOnlyHint={read_only}")
