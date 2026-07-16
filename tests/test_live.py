"""Live tests against a running Archicad 29 with a NON-SENSITIVE test model open.

Run manually:  uv run pytest -m live -v
Never run against a client project (privacy rule).
"""
import pytest

from archicad_mcp.connection import discover_instances, get_connection
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    BUILTIN_STORY,
    BUILTIN_ZONE_NAME,
    BUILTIN_ZONE_NUMBER,
    build_snapshot,
)

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def conn():
    instances = discover_instances()
    if not instances:
        pytest.skip("no running Archicad instance")
    return get_connection(instances[0].port)


def test_product_info_is_archicad_29(conn):
    info = conn.official("API.GetProductInfo")
    assert info["version"] >= 29


def test_builtin_property_names_resolve(conn):
    """THE canary: if these names don't resolve, fix the BUILTIN_* constants in
    extract.py using the dump printed below."""
    from archicad_mcp.extract import resolve_property_ids
    wanted = [BUILTIN_LAYER, BUILTIN_STORY, BUILTIN_ZONE_NUMBER, BUILTIN_ZONE_NAME]
    ids = resolve_property_ids(conn, wanted)
    missing = [n for n in wanted if n not in ids]
    if missing:
        names = conn.official("API.GetAllPropertyNames")
        builtin = sorted(p.get("nonLocalizedName", "") for p in names["properties"]
                         if p.get("type") == "BuiltIn")
        print("\n".join(builtin))
        pytest.fail(f"Built-in names not found: {missing}. "
                    "Pick the right ones from the dump above and update extract.py.")


def test_full_snapshot_builds(conn):
    snap = build_snapshot(
        conn,
        needs=frozenset({"elements", "properties", "classifications", "layers",
                         "zones", "ifc"}))
    assert snap.elements, "test model must contain elements"
    assert snap.layers, "test model must contain layers"
    types = {e.element_type for e in snap.elements}
    print(f"element types found: {sorted(types)}")


def test_tapir_status_reported(conn):
    print(f"tapir available: {conn.tapir_available()}")
