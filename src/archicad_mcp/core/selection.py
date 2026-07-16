from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import element_payload


def manage_selection(conn: ArchicadConnection, action: str,
                     guids: list[str] | None = None) -> dict:
    if action == "get":
        response = conn.official("API.GetSelectedElements")
        return {"guids": [e["elementId"]["guid"] for e in response.get("elements", [])]}
    if action == "set":
        guids = guids or []
        conn.tapir("ChangeSelectionOfElements",
                   {"addElementsToSelection": element_payload(guids)})
        return {"selected": len(guids)}
    if action == "clear":
        current = conn.official("API.GetSelectedElements").get("elements", [])
        conn.tapir("ChangeSelectionOfElements",
                   {"removeElementsFromSelection": current})
        return {"cleared": len(current)}
    return {"error": f"Unknown action '{action}'. Use 'get', 'set', or 'clear'."}
