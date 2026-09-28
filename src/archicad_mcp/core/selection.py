from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import (
    element_payload,
    get_selected_element_ids,
    selection_coverage_of,
)

# One function per operation rather than one dispatching on an action string.
# The directory review rejects a tool that both reads and writes behind a
# parameter, and the split is honest anyway: reading the selection cannot change
# anything, and replacing it discards whatever the user had picked by hand.
#
# All three read the selection through get_selected_element_ids, never the
# official command directly: that one drops markers and native MEP elements, so
# a clear would leave them selected and a "replace" would quietly append.


def get_selection(conn: ArchicadConnection) -> dict:
    return {"guids": get_selected_element_ids(conn), **selection_coverage_of(conn)}


def set_selection(conn: ArchicadConnection, guids: list[str] | None = None) -> dict:
    """Replace the selection: deselect everything, then select `guids`.

    Both halves go in one ChangeSelectionOfElements call so the model is never
    briefly holding a half-applied selection.
    """
    guids = guids or []
    current = element_payload(get_selected_element_ids(conn))
    conn.tapir("ChangeSelectionOfElements", {
        "addElementsToSelection": element_payload(guids),
        "removeElementsFromSelection": current,
    })
    return {"selected": len(guids)}


def clear_selection(conn: ArchicadConnection) -> dict:
    current = element_payload(get_selected_element_ids(conn))
    conn.tapir("ChangeSelectionOfElements",
               {"removeElementsFromSelection": current})
    return {"cleared": len(current)}
