from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection

ATTRIBUTE_DETAIL_COMMANDS = {
    "Layer": "API.GetLayerAttributes",
    "BuildingMaterial": "API.GetBuildingMaterialAttributes",
    "Composite": "API.GetCompositeAttributes",
    "Surface": "API.GetSurfaceAttributes",
    "Profile": "API.GetProfileAttributes",
    "ZoneCategory": "API.GetZoneCategoryAttributes",
}


def list_attributes(conn: ArchicadConnection, attribute_type: str) -> dict:
    command = ATTRIBUTE_DETAIL_COMMANDS.get(attribute_type)
    if command is None:
        return {"error": f"Unknown attribute_type '{attribute_type}'. "
                         f"Valid: {sorted(ATTRIBUTE_DETAIL_COMMANDS)}."}
    ids = conn.official("API.GetAttributesByType", {"attributeType": attribute_type})
    attribute_ids = ids.get("attributeIds", [])
    if not attribute_ids:
        return {"attribute_type": attribute_type, "names": []}
    response = conn.official(command, {"attributeIds": attribute_ids})
    names = []
    for item in response.get("attributes", []):
        # each item is {"<type>Attribute": {..., "name": ...}}
        inner = next(iter(item.values()), {})
        if isinstance(inner, dict) and "name" in inner:
            names.append(inner["name"])
    return {"attribute_type": attribute_type, "names": names}


def tapir_attributes(conn: ArchicadConnection, attribute_type: str) -> list[dict]:
    """[{index, name, guid}] of every attribute of the type, from Tapir.

    The index is what element details hold (GetDetailsOfElements layerIndex,
    a mesh's lineTypeIndex) and what SetDetailsOfElements takes.
    """
    response = conn.tapir("GetAttributesByType", {"attributeType": attribute_type})
    return [{"index": a["index"], "name": a["name"], "guid": a["attributeId"]["guid"]}
            for a in response.get("attributes", [])]


def layer_table(conn: ArchicadConnection) -> dict[int, dict]:
    """Layer index -> {name, guid, isHidden, isLocked}."""
    layers = tapir_attributes(conn, "Layer")
    if not layers:
        return {}
    response = conn.tapir("GetLayers", {
        "attributeIds": [{"attributeId": {"guid": a["guid"]}} for a in layers],
        "fields": ["isHidden", "isLocked"]})
    flags = {item.get("index"): item for item in response.get("layers", [])
             if isinstance(item, dict) and "index" in item}
    return {a["index"]: {"name": a["name"], "guid": a["guid"],
                         "isHidden": bool(flags.get(a["index"], {}).get("isHidden")),
                         "isLocked": bool(flags.get(a["index"], {}).get("isLocked"))}
            for a in layers}
