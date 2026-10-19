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


def replace_selection(conn: ArchicadConnection, guids: list[str]) -> tuple[int, list[str]]:
    """Select exactly `guids`; (how many Archicad really selected, the rest).

    Tapir 1.7.1 runs every add before every remove (ElementCommands.cpp,
    ChangeSelectionOfElementsCommand::Execute), so an element in both lists
    would end up deselected: only what is not wanted is removed. Archicad
    answers success per element and still leaves some unselected (section
    Objects, railing sub-elements, elevation marker parts), so the selection
    is read back.
    """
    wanted = {g.upper() for g in guids}
    current = get_selected_element_ids(conn)
    conn.tapir("ChangeSelectionOfElements", {
        "addElementsToSelection": element_payload(guids),
        "removeElementsFromSelection": element_payload(
            [g for g in current if g.upper() not in wanted])})
    now = {g.upper() for g in get_selected_element_ids(conn)}
    missed = [g for g in guids if g.upper() not in now]
    return len(guids) - len(missed), missed


def set_selection(conn: ArchicadConnection, guids: list[str] | None = None) -> dict:
    """Replace the selection with `guids`; `selected` is what Archicad really
    selected, read back after the change.

    One ChangeSelectionOfElements call, so the model is never briefly holding
    a half-applied selection.
    """
    guids = guids or []
    selected, missed = replace_selection(conn, guids)
    out: dict = {"requested": len(guids), "selected": selected}
    if missed:
        out["not_selected"] = missed
    return {**out, **selection_coverage_of(conn)}


def clear_selection(conn: ArchicadConnection) -> dict:
    current = element_payload(get_selected_element_ids(conn))
    conn.tapir("ChangeSelectionOfElements",
               {"removeElementsFromSelection": current})
    return {"cleared": len(current)}
