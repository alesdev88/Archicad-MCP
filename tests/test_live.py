"""Live tests against a running Archicad 29 with a NON-SENSITIVE test model open.

Run manually:  ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v
Never run against a client project (privacy rule). With several instances
running, the port MUST be given explicitly — auto-picking the first instance
could hit a live teamwork project.
"""
import os

import pytest

from archicad_mcp.connection import discover_instances, get_connection
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    BUILTIN_ZONE_NAME,
    BUILTIN_ZONE_NUMBER,
    PropertyFetchTooWideError,
    build_snapshot,
)

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def conn():
    port_env = os.environ.get("ARCHICAD_MCP_LIVE_PORT")
    if port_env:
        return get_connection(int(port_env))
    instances = discover_instances()
    if not instances:
        pytest.skip("no running Archicad instance")
    if len(instances) > 1:
        pytest.skip("multiple Archicad instances running; "
                    "set ARCHICAD_MCP_LIVE_PORT to choose the test model")
    if instances[0].project_name and "teamwork" in str(
            get_connection(instances[0].port).tapir("GetProjectInfo")
            .get("projectLocation", "")).lower():
        pytest.skip("refusing to run live tests against a teamwork project")
    return get_connection(instances[0].port)


def test_product_info_is_archicad_29(conn):
    info = conn.official("API.GetProductInfo")
    assert info["version"] >= 29


def test_builtin_property_names_resolve(conn):
    """THE canary: the property-backed built-in names must resolve. If one
    doesn't, fix the BUILTIN_* constant in extract.py from the dump below.
    (Story is NOT a property — it comes from Tapir floorIndex, tested separately.)"""
    from archicad_mcp.extract import resolve_property_ids
    wanted = [BUILTIN_LAYER, BUILTIN_ZONE_NUMBER, BUILTIN_ZONE_NAME]
    ids = resolve_property_ids(conn, wanted)
    missing = [n for n in wanted if n not in ids]
    if missing:
        names = conn.official("API.GetAllPropertyNames")
        builtin = sorted(p.get("nonLocalizedName", "") for p in names["properties"]
                         if p.get("type") == "BuiltIn")
        print("\n".join(builtin))
        pytest.fail(f"Built-in names not found: {missing}. "
                    "Pick the right ones from the dump above and update extract.py.")


def test_story_comes_from_floor_index(conn):
    """floorIndex on the element detail is the story source."""
    from archicad_mcp.extract import _fetch_floor_indices
    guids = [e["elementId"]["guid"]
             for e in conn.official("API.GetAllElements")["elements"][:20]]
    floors = _fetch_floor_indices(conn, guids)
    assert floors, "GetDetailsOfElements returned no floor indices"
    assert all(v is None or isinstance(v, int) for v in floors.values())


def test_full_snapshot_builds(conn):
    """Full snapshot over the whole model. On a large model the property sweep
    is refused by design (crash guard) — that's a pass for the guard, so skip."""
    try:
        snap = build_snapshot(
            conn,
            needs=frozenset({"elements", "properties", "classifications", "layers",
                             "zones", "story", "ifc"}))
    except PropertyFetchTooWideError as exc:
        pytest.skip(f"model too large for a full property sweep (guard worked): {exc}")
    assert snap.elements, "test model must contain elements"
    assert snap.layers, "test model must contain layers"
    types = {e.element_type for e in snap.elements}
    print(f"element types found: {sorted(types)}")


def test_tapir_status_reported(conn):
    print(f"tapir available: {conn.tapir_available()}")
