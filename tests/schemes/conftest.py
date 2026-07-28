"""Shared helpers for tests/schemes/*.py.

tests/conftest.py (the parent directory) is the single source of the
FakeCore stub itself; this module is the single source of everything built
on top of it for schedule-scheme tests specifically: the fixture path, a
scheme loader, two XML-fragment builders for hand-built schemes, a stubbed
ArchicadConnection carrying property definitions, and a fastmcp tool-call
helper. These are plain functions and constants, not pytest fixtures, so
each test module imports the ones it needs directly (`from
tests.schemes.conftest import FIXTURE, load, ...`) rather than requesting
them by name as fixture arguments.

Before this module existed, _item, _scheme_xml, load, FIXTURE, and the
FakeCore-backed connection builder were each copy-pasted across several of
these files, and the two connection builders (test_core_edit.py's
conn_with_properties, test_validate.py's conn_with) had already drifted
apart: different names, and different default property lists. This is the
one copy of each.
"""
import json
from pathlib import Path

from fastmcp import Client

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.schemes.model import parse_scheme
from archicad_mcp.schemes.xml_io import load_scheme_tree
from tests.conftest import FakeCore

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def load():
    """Parse the sample fixture into a fresh Scheme."""
    return parse_scheme(load_scheme_tree(FIXTURE))


def _item(item_id, parent, caption, *, first_child="0", previous="0", next_="0", index="0"):
    """A minimal Header_Item fragment carrying only the fields parse_scheme
    and _next_item_id read: ID_of_Item/Parent/firstChild/previous/next for
    tree structure, Index_of_Columns, and a Caption to tell items apart.
    Binding fields are left out on purpose: field_value/_int_field default
    them to '' / 0, and no test using this helper asserts on binding."""
    return (
        "<Header_Item>"
        f'<Index_of_Columns value="{index}"/>'
        f'<ID_of_Item value="{item_id}"/>'
        f'<ID_of_Parent value="{parent}"/>'
        f'<ID_of_firstChild value="{first_child}"/>'
        f'<ID_of_previous value="{previous}"/>'
        f'<ID_of_next value="{next_}"/>'
        f"<Caption>{caption}</Caption>"
        "</Header_Item>"
    )


def _scheme_xml(*items):
    """Wrap Header_Item fragments in a minimal Scheme_Settings/Header_Items
    document, standing in for a full Archicad export."""
    return (
        '<Scheme_Settings ID="1" Name="s" Scheme_Type="Element_List" Version="29.0.0">'
        "<Header_Items>" + "".join(items) + "</Header_Items>"
        "</Scheme_Settings>"
    )


# The single property this office's sample project is stubbed as having.
# Deliberately narrow: test_core_edit.py's safety-conscious tests rely on
# names outside this list (e.g. "OFFICE/Fire Rating") staying unresolvable,
# so a caller that needs more properties resolvable passes its own
# `properties` argument rather than this default growing to cover it.
DEFAULT_PROPERTIES = {
    "properties": [
        {"propertyId": {"guid": "69A58F6F-1111-4000-8000-000000000001"},
         "propertyGroupName": "OFFICE", "propertyName": "Door ID"},
    ]
}


def conn_with_properties(properties=DEFAULT_PROPERTIES):
    # conn.tapir() gates on tapir_available(), which probes via the OFFICIAL
    # table, so the fake has to answer that too or every Tapir call raises.
    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}},
                    tapir={"GetAllProperties": properties})
    return ArchicadConnection(19723, core=core)


def conn_without_tapir():
    # The probe itself answers False: no Tapir table needed, since
    # conn.tapir() raises before ever consulting it.
    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": False}})
    return ArchicadConnection(19723, core=core)


async def call(mcp, tool, args=None):
    """Call a registered tool through a real fastmcp Client and decode its
    JSON payload. Going through the Client, rather than calling the
    underlying Python function directly, is what actually exercises the
    tool's registered signature and @_guarded wrapping."""
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)
