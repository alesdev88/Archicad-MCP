from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import element_payload

# One function per operation rather than one dispatching on an action string.
# The directory review rejects a tool that both reads and writes behind a
# parameter, and the split is honest anyway: reading the selection cannot change
# anything, and replacing it discards whatever the user had picked by hand.


def get_selection(conn: ArchicadConnection) -> dict:
    response = conn.official("API.GetSelectedElements")
    return {"guids": [e["elementId"]["guid"] for e in response.get("elements", [])]}


def set_selection(conn: ArchicadConnection, guids: list[str] | None = None) -> dict:
    """Replace the selection: deselect everything, then select `guids`.

    Both halves go in one ChangeSelectionOfElements call so the model is never
    briefly holding a half-applied selection.
    """
    guids = guids or []
    current = conn.official("API.GetSelectedElements").get("elements", [])
    conn.tapir("ChangeSelectionOfElements", {
        "addElementsToSelection": element_payload(guids),
        "removeElementsFromSelection": current,
    })
    return {"selected": len(guids)}


def clear_selection(conn: ArchicadConnection) -> dict:
    current = conn.official("API.GetSelectedElements").get("elements", [])
    conn.tapir("ChangeSelectionOfElements",
               {"removeElementsFromSelection": current})
    return {"cleared": len(current)}
