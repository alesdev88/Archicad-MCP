from __future__ import annotations

from typing import Iterable

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, ZoneInfo

BUILTIN_LAYER = "General_LayerName"
BUILTIN_STORY = "General_HomeStoryNumber"
BUILTIN_ZONE_NUMBER = "Zone_ZoneNumber"
BUILTIN_ZONE_NAME = "Zone_ZoneName"


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


def get_all_element_ids(conn: ArchicadConnection) -> list[str]:
    response = conn.official("API.GetAllElements")
    return [e["elementId"]["guid"] for e in response.get("elements", [])]


def _element_payload(guids: list[str]) -> list[dict]:
    return [{"elementId": {"guid": g}} for g in guids]


def _fetch_types(conn, guids: list[str]) -> dict[str, str]:
    response = conn.official("API.GetTypesOfElements", {"elements": _element_payload(guids)})
    out = {}
    for item in response.get("types", []):
        t = item.get("typeOfElement", {})
        out[t.get("elementId", {}).get("guid", "")] = t.get("elementType", "")
    return out


def fetch_property_values(conn, guids: list[str], names: list[str]) -> dict[str, dict[str, object]]:
    """guid -> {property name -> value or None}."""
    if not guids or not names:
        return {g: {} for g in guids}
    ids = resolve_property_ids(conn, names)
    resolved = [n for n in names if n in ids]
    response = conn.official("API.GetPropertyValuesOfElements", {
        "elements": _element_payload(guids),
        "properties": [{"propertyId": ids[n]} for n in resolved],
    })
    out: dict[str, dict[str, object]] = {}
    rows = response.get("propertyValuesForElements", [])
    for guid, row in zip(guids, rows):
        values: dict[str, object] = {}
        for name, cell in zip(resolved, row.get("propertyValues", [])):
            pv = cell.get("propertyValue")
            values[name] = pv.get("value") if pv else None
        for name in names:
            values.setdefault(name, None)
        out[guid] = values
    return out


def _fetch_classifications(conn, guids: list[str]) -> dict[str, dict[str, str | None]]:
    systems = conn.official("API.GetAllClassificationSystems").get("classificationSystems", [])
    system_names = {s["classificationSystemId"]["guid"]: s["name"] for s in systems}
    response = conn.official("API.GetClassificationsOfElements", {
        "elements": _element_payload(guids),
        "classificationSystemIds": [{"classificationSystemId": {"guid": g}}
                                    for g in system_names],
    })
    out: dict[str, dict[str, str | None]] = {}
    for guid, row in zip(guids, response.get("elementClassifications", [])):
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
    if not conn.tapir_available():
        return None
    try:
        response = conn.tapir("GetIFCPropertiesOfElements",
                              {"elements": _element_payload(guids)})
    except ArchicadUnavailableError:
        return None
    out: dict[str, dict[str, object]] = {}
    for item in response.get("elements", []):
        guid = item.get("elementId", {}).get("guid", "")
        props = {}
        for p in item.get("properties", []):
            props[f"{p.get('propertySetName')}.{p.get('name')}"] = p.get("value")
        out[guid] = props
    return out


def build_snapshot(conn: ArchicadConnection, needs: frozenset[str],
                   property_names: frozenset[str] = frozenset()) -> ModelSnapshot:
    elements: tuple[ElementInfo, ...] = ()
    layers: tuple[str, ...] = ()
    zones: tuple[ZoneInfo, ...] = ()
    ifc: dict[str, dict[str, object]] | None = None

    want_elements = bool(needs & {"elements", "properties", "classifications", "ifc", "zones"})
    guids = get_all_element_ids(conn) if want_elements else []
    types = _fetch_types(conn, guids) if guids else {}

    if "elements" in needs and guids:
        prop_names = set(property_names)
        if needs & {"properties", "layers"}:
            prop_names |= {BUILTIN_LAYER, BUILTIN_STORY}
        values = (fetch_property_values(conn, guids, sorted(prop_names))
                  if "properties" in needs or "layers" in needs else {g: {} for g in guids})
        classif = (_fetch_classifications(conn, guids)
                   if "classifications" in needs else {g: {} for g in guids})
        elements = tuple(
            ElementInfo(
                guid=g,
                element_type=types.get(g, ""),
                layer=values.get(g, {}).get(BUILTIN_LAYER),
                story=values.get(g, {}).get(BUILTIN_STORY),
                classifications=classif.get(g, {}),
                properties={k: v for k, v in values.get(g, {}).items()
                            if k not in (BUILTIN_LAYER, BUILTIN_STORY)},
            )
            for g in guids
        )

    if "layers" in needs:
        layers = _fetch_layer_names(conn)

    if "zones" in needs:
        zone_guids = [g for g in guids if types.get(g) == "Zone"]
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
