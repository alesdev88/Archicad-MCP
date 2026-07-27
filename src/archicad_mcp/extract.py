from __future__ import annotations

import os
from typing import Iterable

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, ZoneInfo

# Verified against a live Archicad 29.0 model (2026-07-16):
#   layer name  -> ModelView_LayerName  (NOT General_LayerName, which does not exist)
#   zone number -> Zone_ZoneNumber, zone name -> Zone_ZoneName  (confirmed)
# There is no built-in "home story number" property; an element's story comes
# from Tapir GetDetailsOfElements.floorIndex instead (see _fetch_floor_indices).
BUILTIN_LAYER = "ModelView_LayerName"
BUILTIN_ZONE_NUMBER = "Zone_ZoneNumber"
BUILTIN_ZONE_NAME = "Zone_ZoneName"

# Max element GUIDs per GetPropertyValuesOfElements request. Wide property
# queries against large models have crashed Archicad's API bridge inside
# GetPropertyValuesOfElementsCommand::ComposeResult (observed AC 29.0 build
# 4006); chunking caps the per-request response size to reduce that risk.
PROPERTY_FETCH_CHUNK = 500

# Hard ceiling on how many elements a single property fetch may span. The
# ComposeResult crash above is server-side and unrecoverable mid-session:
# chunking alone did NOT prevent it on a 16k-element model, because a single
# un-composable property on any element in any chunk still aborts Archicad.
# So we refuse, with an actionable error, rather than risk crashing the
# user's Archicad. Scope the query first (query_elements / rule applies_to),
# or raise the ceiling deliberately via ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS.
def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


MAX_PROPERTY_FETCH_ELEMENTS = _int_env("ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS", 5000)

# Max element GUIDs per GetTypesOfElements request. Enumeration now spans the
# whole plan (63122 elements on a live project, vs the 16221 the official
# GetAllElements reported), and a single request that wide is the same shape of
# call that has crashed the API bridge on property reads.
TYPE_FETCH_CHUNK = 2000

# What an enumeration actually covered. Only Tapir sees the whole plan; the
# official API.GetAllElements returns model elements only, so every 2D /
# annotation / viewpoint type is missing from it. Measured live on AC 29.0/4006:
# official 16221 elements vs Tapir 63122 (26%). Tools say which one they got,
# because a bare count that silently omits three quarters of the project reads
# as a verified total.
COVERAGE_FULL = "whole-plan"
COVERAGE_PARTIAL = "model-elements-only"
COVERAGE_PARTIAL_NOTE = (
    "Without the Tapir add-on only model elements are visible: markers, labels, "
    "dimensions, section lines and other 2D elements are NOT counted. Install "
    "Tapir for whole-plan coverage.")


def coverage_of(conn: ArchicadConnection) -> dict:
    """The coverage marker to merge into any whole-model result."""
    if conn.tapir_available():
        return {"coverage": COVERAGE_FULL}
    return {"coverage": COVERAGE_PARTIAL, "coverage_note": COVERAGE_PARTIAL_NOTE}


class PropertyFetchTooWideError(ArchicadUnavailableError):
    """Raised before a property fetch that could crash Archicad's API bridge."""


def _property_name_payload(name: str) -> dict:
    """User-defined properties are addressed 'Group/Name'; everything else BuiltIn."""
    if "/" in name:
        group, prop = name.split("/", 1)
        return {"type": "UserDefined", "localizedName": [group, prop]}
    return {"type": "BuiltIn", "nonLocalizedName": name}


def resolve_property_ids(conn: ArchicadConnection, names: Iterable[str]) -> dict[str, dict]:
    names = list(names)
    if not names:
        return {}
    payload = [_property_name_payload(n) for n in names]
    response = conn.official("API.GetPropertyIds", {"properties": payload})
    out: dict[str, dict] = {}
    for name, item in zip(names, response.get("properties", [])):
        if "propertyId" in item:
            out[name] = item["propertyId"]
    return out


def _guids_of(response: dict) -> list[str]:
    return [e["elementId"]["guid"] for e in response.get("elements", [])]


def get_all_element_ids(conn: ArchicadConnection) -> list[str]:
    """Every element on the plan, 2D and annotation included.

    Tapir's GetAllElements returns the whole plan; the official
    API.GetAllElements returns model elements only (see COVERAGE_PARTIAL_NOTE).
    Falls back to the official command when Tapir is absent -- callers pair the
    result with coverage_of() so a partial enumeration is never reported as a
    project total.
    """
    if conn.tapir_available():
        return _guids_of(conn.tapir("GetAllElements"))
    return _guids_of(conn.official("API.GetAllElements"))


def get_element_ids_of_type(conn: ArchicadConnection, element_type: str) -> list[str]:
    """GUIDs of one element type, without a types sweep over the whole plan.

    Tapir filters server-side, so this costs one request instead of enumerating
    every element and reading its type back (16k+ reads to answer "how many
    walls"). Without Tapir there is no such command, so the client-side filter
    remains -- over model elements only.
    """
    if conn.tapir_available():
        return _guids_of(conn.tapir("GetElementsByType", {"elementType": element_type}))
    guids = _guids_of(conn.official("API.GetAllElements"))
    types = _fetch_types(conn, guids) if guids else {}
    return [g for g in guids if types.get(g) == element_type]


def get_selected_element_ids(conn: ArchicadConnection) -> list[str]:
    """The current selection. Official GetSelectedElements returns [] for a
    selected marker (a CutPlane, say); Tapir's returns it."""
    if conn.tapir_available():
        return _guids_of(conn.tapir("GetSelectedElements"))
    return _guids_of(conn.official("API.GetSelectedElements"))


def element_payload(guids: list[str]) -> list[dict]:
    return [{"elementId": {"guid": g}} for g in guids]


def _fetch_types(conn, guids: list[str]) -> dict[str, str]:
    # Live-verified shape: {"typesOfElements": [{"typeOfElement": {...}}]}
    # Chunked: see TYPE_FETCH_CHUNK.
    out = {}
    for start in range(0, len(guids), TYPE_FETCH_CHUNK):
        chunk = guids[start:start + TYPE_FETCH_CHUNK]
        response = conn.official("API.GetTypesOfElements",
                                 {"elements": element_payload(chunk)})
        for item in response.get("typesOfElements", []):
            t = item.get("typeOfElement", {})
            out[t.get("elementId", {}).get("guid", "")] = t.get("elementType", "")
    return out


def fetch_property_cells(conn, guids: list[str], names: list[str]) -> dict[str, dict[str, dict]]:
    """guid -> {property name -> the raw propertyValue dict (or {})}.

    The raw cell carries `type` ("string", "singleEnum", …) and `status`
    ("normal", "userUndefined", …) alongside `value`. Writers need the type,
    because API.SetPropertyValuesOfElements requires a fully-typed value.

    Requests are chunked into PROPERTY_FETCH_CHUNK-sized element batches to cap
    the per-request response size (a wide query on a large model can crash the
    Archicad API bridge); the per-chunk results are merged. If the element set
    exceeds MAX_PROPERTY_FETCH_ELEMENTS this refuses rather than risk a crash.
    """
    if not guids or not names:
        return {g: {} for g in guids}
    if len(guids) > MAX_PROPERTY_FETCH_ELEMENTS:
        raise PropertyFetchTooWideError(
            f"Refusing to read properties across {len(guids)} elements "
            f"(limit {MAX_PROPERTY_FETCH_ELEMENTS}). Wide property queries have "
            "crashed Archicad's API on large models. Scope the query first "
            "(query_elements by type/layer/story, or a rule's applies_to), or "
            "raise ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS if you accept the risk.")
    ids = resolve_property_ids(conn, names)
    resolved = [n for n in names if n in ids]
    property_payload = [{"propertyId": ids[n]} for n in resolved]
    out: dict[str, dict[str, object]] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        response = conn.official("API.GetPropertyValuesOfElements", {
            "elements": element_payload(chunk),
            "properties": property_payload,
        })
        rows = response.get("propertyValuesForElements", [])
        for guid, row in zip(chunk, rows):
            cells: dict[str, dict] = {}
            for name, cell in zip(resolved, row.get("propertyValues", [])):
                cells[name] = cell.get("propertyValue") or {}
            for name in names:
                cells.setdefault(name, {})
            out[guid] = cells
    return out


def fetch_property_values(conn, guids: list[str], names: list[str]) -> dict[str, dict[str, object]]:
    """guid -> {property name -> value or None}. Thin view over the raw cells."""
    cells = fetch_property_cells(conn, guids, names)
    return {guid: {name: cell.get("value") for name, cell in per_name.items()}
            for guid, per_name in cells.items()}


def _fetch_classifications(conn, guids: list[str]) -> dict[str, dict[str, str | None]]:
    systems = conn.official("API.GetAllClassificationSystems").get("classificationSystems", [])
    system_names = {s["classificationSystemId"]["guid"]: s["name"] for s in systems}
    system_payload = [{"classificationSystemId": {"guid": g}} for g in system_names]
    out: dict[str, dict[str, str | None]] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        response = conn.official("API.GetClassificationsOfElements", {
            "elements": element_payload(chunk),
            "classificationSystemIds": system_payload,
        })
        for guid, row in zip(chunk, response.get("elementClassifications", [])):
            per_system: dict[str, str | None] = {}
            for item in row.get("classificationIds", []):
                cid = item.get("classificationId", {})
                system_guid = cid.get("classificationSystemId", {}).get("guid")
                name = system_names.get(system_guid, system_guid or "?")
                inner = cid.get("classificationId")
                per_system[name] = inner.get("guid") if inner else None
            out[guid] = per_system
    return out


def _fetch_layer_names(conn) -> tuple[str, ...]:
    ids = conn.official("API.GetAttributesByType", {"attributeType": "Layer"})
    attribute_ids = ids.get("attributeIds", [])
    if not attribute_ids:
        return ()
    response = conn.official("API.GetLayerAttributes", {"attributeIds": attribute_ids})
    return tuple(a["layerAttribute"]["name"] for a in response.get("attributes", []))


def _fetch_ifc(conn, guids: list[str]) -> dict[str, dict[str, object]] | None:
    # Tapir may be installed but predate the IFC commands (1.4.0 has no
    # GetIFCPropertiesOfElements), so ask before calling and let IFC rules skip
    # instead of erroring the whole snapshot with a 4010.
    if not conn.tapir_command_available("GetIFCPropertiesOfElements"):
        return None
    out: dict[str, dict[str, object]] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        try:
            response = conn.tapir("GetIFCPropertiesOfElements",
                                  {"elements": element_payload(chunk)})
        except (ArchicadUnavailableError, APIErrorBase):
            return None
        for item in response.get("elements", []):
            guid = item.get("elementId", {}).get("guid", "")
            props = {}
            for p in item.get("properties", []):
                props[f"{p.get('propertySetName')}.{p.get('name')}"] = p.get("value")
            out[guid] = props
    return out


def _fetch_floor_indices(conn, guids: list[str]) -> dict[str, int | None]:
    """guid -> home-story (floor) index, via Tapir GetDetailsOfElements.

    Archicad exposes no built-in story-number property; floorIndex on the
    element detail is the reliable source. Returns {} if Tapir is unavailable.
    """
    if not guids or not conn.tapir_available():
        return {}
    if len(guids) > MAX_PROPERTY_FETCH_ELEMENTS:
        raise PropertyFetchTooWideError(
            f"Refusing to read element details across {len(guids)} elements "
            f"(limit {MAX_PROPERTY_FETCH_ELEMENTS}). Scope the query first, or "
            "raise ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS if you accept the risk.")
    out: dict[str, int | None] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        try:
            response = conn.tapir("GetDetailsOfElements", {"elements": element_payload(chunk)})
        except ArchicadUnavailableError:
            return {}
        for guid, item in zip(chunk, response.get("detailsOfElements", [])):
            out[guid] = item.get("floorIndex") if isinstance(item, dict) else None
    return out


def build_snapshot(conn: ArchicadConnection, needs: frozenset[str],
                   property_names: frozenset[str] = frozenset(),
                   element_types: frozenset[str] | None = None) -> ModelSnapshot:
    """Build a ModelSnapshot fetching only what `needs` demands.

    When `element_types` is given, the per-element data (types, properties,
    classifications, story, ifc) is fetched only for elements of those types.
    The extractor still lists all element ids and their types once (cheap), then
    narrows before the expensive/crash-prone property sweep. `None` means no
    narrowing (fetch every element). Zones and the layer name list are
    independent of this filter.
    """
    elements: tuple[ElementInfo, ...] = ()
    layers: tuple[str, ...] = ()
    zones: tuple[ZoneInfo, ...] = ()
    ifc: dict[str, dict[str, object]] | None = None

    want_elements = bool(needs & {"elements", "properties", "classifications", "ifc", "zones"})
    all_guids = get_all_element_ids(conn) if want_elements else []
    types = _fetch_types(conn, all_guids) if all_guids else {}

    if element_types is None:
        guids = all_guids
    else:
        guids = [g for g in all_guids if types.get(g) in element_types]

    if "elements" in needs and guids:
        prop_names = set(property_names)
        if needs & {"properties", "layers"}:
            prop_names |= {BUILTIN_LAYER}
        values = (fetch_property_values(conn, guids, sorted(prop_names))
                  if "properties" in needs or "layers" in needs else {g: {} for g in guids})
        classif = (_fetch_classifications(conn, guids)
                   if "classifications" in needs else {g: {} for g in guids})
        floors = _fetch_floor_indices(conn, guids) if "story" in needs else {}
        elements = tuple(
            ElementInfo(
                guid=g,
                element_type=types.get(g, ""),
                layer=values.get(g, {}).get(BUILTIN_LAYER),
                story=floors.get(g),
                classifications=classif.get(g, {}),
                properties={k: v for k, v in values.get(g, {}).items()
                            if k != BUILTIN_LAYER},
            )
            for g in guids
        )

    if "layers" in needs:
        layers = _fetch_layer_names(conn)

    if "zones" in needs:
        # Zones are found across ALL elements, independent of element_types
        # (which scopes the element-property sweep, not zone discovery).
        zone_guids = [g for g in all_guids if types.get(g) == "Zone"]
        zone_values = fetch_property_values(
            conn, zone_guids, [BUILTIN_ZONE_NUMBER, BUILTIN_ZONE_NAME])
        zones = tuple(
            ZoneInfo(guid=g,
                     number=zone_values.get(g, {}).get(BUILTIN_ZONE_NUMBER),
                     name=zone_values.get(g, {}).get(BUILTIN_ZONE_NAME))
            for g in zone_guids
        )

    if "ifc" in needs:
        ifc = _fetch_ifc(conn, guids)

    return ModelSnapshot(elements=elements, layers=layers, zones=zones, ifc_properties=ifc)
