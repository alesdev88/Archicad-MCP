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


# ---------- 0.4.0: criteria query and definition search ----------

@pytest.fixture(scope="module")
def model_type(conn):
    """The most common element type in the test model, so the canary works on
    whatever the small model holds (the reference one has four Objects and no
    walls) without a property read wider than that one type."""
    from collections import Counter
    from archicad_mcp.extract import _fetch_types, get_all_element_ids
    guids = get_all_element_ids(conn)
    if not guids:
        pytest.skip("test model is empty")
    counts = Counter(_fetch_types(conn, guids).values())
    element_type, n = counts.most_common(1)[0]
    if n > 500:
        pytest.skip("dominant type too numerous for a live property read in a canary")
    return element_type


def test_search_definitions_hands_out_addresses_that_resolve(conn):
    """The discovery tool must return addresses the other tools accept. For
    every property match, resolve_property_ids must find it."""
    from archicad_mcp.core.definitions import search_definitions
    from archicad_mcp.extract import resolve_property_ids
    payload = search_definitions(conn, "layer", kind="property", limit=10)
    assert payload["total_matches"] > 0, "no property matches 'layer'"
    addresses = [m["property"] for m in payload["matches"]]
    ids = resolve_property_ids(conn, addresses)
    missing = [a for a in addresses if a not in ids]
    assert not missing, f"addresses that do not resolve: {missing}"
    layer = next((m for m in payload["matches"] if m["property"] == BUILTIN_LAYER), None)
    assert layer is not None, "ModelView_LayerName should surface for 'layer'"


def test_search_definitions_lists_attributes(conn):
    from archicad_mcp.core.definitions import search_definitions
    payload = search_definitions(conn, "a", kind="attribute", limit=200)
    types = {m["attribute_type"] for m in payload["matches"]}
    assert "Layer" in types
    assert not payload.get("notes"), payload.get("notes")


def test_find_elements_by_type_and_layer_agrees_with_a_direct_read(conn, model_type):
    """Cross-check the criteria path against a direct property read over one
    element type: the count on the most common layer must agree, an AND of a
    value with its own negation must be empty, and the OR must be everything."""
    from collections import Counter
    from archicad_mcp.core.query import find_elements
    from archicad_mcp.extract import fetch_property_values
    everything = find_elements(conn, [{"element_types": [model_type]}])
    layers = fetch_property_values(conn, everything["guids"], [BUILTIN_LAYER])
    top_layer, expected = Counter(v[BUILTIN_LAYER] for v in layers.values()).most_common(1)[0]
    on_top = [{"property": BUILTIN_LAYER, "operator": "equal", "value": top_layer}]
    not_on_top = [{"property": BUILTIN_LAYER, "operator": "not_equal", "value": top_layer}]
    payload = find_elements(conn, [{"element_types": [model_type], "comparisons": on_top}])
    assert payload["count"] == expected
    assert payload["property_reads"] == everything["count"]
    none = find_elements(conn, [{"element_types": [model_type], "comparisons": on_top + not_on_top}])
    assert none["count"] == 0
    either = find_elements(conn, [{"element_types": [model_type], "logical_operator": "or",
                                   "comparisons": on_top + not_on_top}])
    assert either["count"] == everything["count"]


def test_find_elements_story_and_classification_need_no_property_read(conn, model_type):
    from archicad_mcp.core.query import find_elements
    payload = find_elements(conn, [{"element_types": [model_type], "comparisons": [
        {"property": "story", "operator": "greater_or_equal", "value": -100}]}])
    assert payload["property_reads"] == 0
    systems = conn.official("API.GetAllClassificationSystems").get("classificationSystems", [])
    if not systems:
        pytest.skip("test model has no classification system")
    everything = find_elements(conn, [{"element_types": [model_type]}])
    for system in systems:
        address = f"classification:{system['name']}"
        classified = find_elements(conn, [{"element_types": [model_type], "comparisons": [
            {"property": address, "operator": "has_value"}]}])
        unclassified = find_elements(conn, [{"element_types": [model_type], "comparisons": [
            {"property": address, "operator": "has_no_value"}]}])
        assert classified["count"] + unclassified["count"] == everything["count"]
        assert classified["property_reads"] == 0


def test_find_elements_custom_property_availability_precheck(conn, model_type):
    """Needs a custom property scoped by classification in the test model
    (the reference model has 'MCP Test/Fire Rating' on its Object item). An
    element the definition does not cover must be answered notAvailable
    without being read; the covered ones are read and one of them has a value."""
    from archicad_mcp.core.definitions import search_definitions
    from archicad_mcp.core.query import find_elements
    found = search_definitions(conn, "fire rating", kind="property", editable_only=True)
    custom = [m for m in found["matches"] if not m["builtin"]]
    if not custom:
        pytest.skip("test model has no custom 'fire rating' property")
    address = custom[0]["property"]
    assert "/" in address, "a custom property is addressed Group/Name"
    everything = find_elements(conn, [{"element_types": [model_type]}])
    unavailable = find_elements(conn, [{"element_types": [model_type], "comparisons": [
        {"property": address, "operator": "not_available"}]}])
    available = find_elements(conn, [{"element_types": [model_type], "comparisons": [
        {"property": address, "operator": "available"}]}])
    assert unavailable["count"] + available["count"] == everything["count"]
    # the pre-check, not a read, answered the uncovered elements
    assert unavailable.get("skipped_not_available", 0) == unavailable["count"]
    assert available["property_reads"] == available["count"]
    with_value = find_elements(conn, [{"element_types": [model_type], "comparisons": [
        {"property": address, "operator": "has_value"}]}])
    print(f"{address}: {with_value['count']} of {available['count']} covered elements have a value")
    assert with_value["count"] <= available["count"]
