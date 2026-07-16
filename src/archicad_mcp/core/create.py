from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection

CREATE_COMMANDS: dict[str, tuple[str, str]] = {
    "column": ("CreateColumns", "columnsData"),
    "slab": ("CreateSlabs", "slabsData"),
    "zone": ("CreateZones", "zonesData"),
    "polyline": ("CreatePolylines", "polylinesData"),
    "object": ("CreateObjects", "objectsData"),
    "mesh": ("CreateMeshes", "meshesData"),
}


def create_elements(conn: ArchicadConnection, element_type: str,
                    items: list[dict], dry_run: bool = True) -> dict:
    entry = CREATE_COMMANDS.get(element_type.lower())
    if entry is None:
        return {"error": f"Unknown element_type '{element_type}'. Valid types: "
                         f"{sorted(CREATE_COMMANDS)}. For door/window/stair and other "
                         "Tapir creation commands use execute_api_command "
                         "(describe_api_command shows the schema)."}
    command, payload_key = entry
    payload = {payload_key: items}
    if dry_run:
        return {"dry_run": True, "command": command, "payload": payload}
    response = conn.tapir(command, payload)
    created = [e["elementId"]["guid"] for e in response.get("elements", [])]
    return {"dry_run": False, "created": len(created), "elements": created}
