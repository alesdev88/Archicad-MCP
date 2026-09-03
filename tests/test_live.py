"""Live tests against a running Archicad 29 with a NON-SENSITIVE test model open.

Run manually:  ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v
Never run against a client project (privacy rule). With several instances
running, the port MUST be given explicitly. Auto-picking the first instance
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
    (Story is NOT a property; it comes from Tapir floorIndex, tested separately.)"""
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
    is refused by design (crash guard), which is a pass for the guard, so skip."""
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


@pytest.fixture(scope="module")
def gdl_workspace():
    """The GDL workspace the design spec's probe needs: a real folder already
    added to Archicad once via File > Libraries and Objects > Library Manager
    (see docs/gdl-pipeline.md). That one-time step has no API, so it cannot be
    set up here; skip when it has not been done on this machine, the same way
    the port fixture above skips when Archicad itself is absent."""
    raw = os.environ.get("ARCHICAD_MCP_GDL_WORKSPACE")
    if not raw:
        pytest.skip("ARCHICAD_MCP_GDL_WORKSPACE not set; needs a folder already "
                    "added to Archicad as a linked library via Library Manager")
    from archicad_mcp.gdl.workspace import Workspace
    return Workspace(raw)


_GDL_PROBE_CUBE_OBJ = """\
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 0 0 1
v 1 0 1
v 1 1 1
v 0 1 1
usemtl steel
f 1 4 3 2
f 5 6 7 8
f 1 2 6 5
f 2 3 7 6
f 3 4 8 7
f 4 1 5 8
"""


def test_gdl_deploy_with_keep_false_leaves_element_count_unchanged(conn, gdl_workspace):
    """The design spec's Testing section requires exactly this: build a known
    small source, deploy with keep=false, and the project's element count must
    come back unchanged. keep=false is supposed to be a net-zero probe, not
    just "delete the element it placed" -- this is what actually proves that."""
    from archicad_mcp.gdl import tools as gdl_tools

    (gdl_workspace.root / "gdl_live_probe_cube.obj").write_text(_GDL_PROBE_CUBE_OBJ)

    build_result = gdl_tools._build_object(
        gdl_workspace, "gdl_live_probe_cube.obj", "GdlLiveProbeCube",
        config={"groups": {}}, decimate=False, validate=True, save_config=False)
    assert "error" not in build_result, build_result

    before = len(conn.official("API.GetAllElements")["elements"])
    payload, _png = gdl_tools._deploy_object(
        gdl_workspace, conn, "GdlLiveProbeCube", place=(0.0, 0.0),
        keep=False, embed=False)
    after = len(conn.official("API.GetAllElements")["elements"])

    assert payload["kept"] is False
    assert after == before, (
        f"deploy with keep=false changed the project element count: "
        f"{before} -> {after}")
