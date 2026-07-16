from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import element_payload


def move_elements(conn: ArchicadConnection, guids: list[str], vector: dict,
                  confirm: bool = False) -> dict:
    if not confirm:
        return {"error": f"Refusing to move {len(guids)} element(s) without "
                         "confirm=true. Review the GUIDs and vector, then retry "
                         "with confirm=true."}
    conn.tapir("MoveElements", {"elementsWithMoveVectors": [
        {"elementId": {"guid": g}, "moveVector": vector} for g in guids]})
    return {"moved": len(guids)}


def delete_elements(conn: ArchicadConnection, guids: list[str],
                    confirm: bool = False) -> dict:
    if not confirm:
        return {"error": f"Refusing to delete {len(guids)} element(s) without "
                         "confirm=true. Deletion is irreversible; retry with "
                         "confirm=true only if certain."}
    conn.tapir("DeleteElements", {"elements": element_payload(guids)})
    return {"deleted": len(guids)}
