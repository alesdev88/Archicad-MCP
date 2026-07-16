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
