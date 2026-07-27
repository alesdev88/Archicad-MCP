from __future__ import annotations

from collections import Counter

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    _fetch_floor_indices,
    coverage_of,
    fetch_property_values,
    get_all_element_ids,
    get_element_ids_of_type,
    get_selected_element_ids,
)


def _types_for(conn: ArchicadConnection, guids: list[str]) -> dict[str, str]:
    from archicad_mcp.extract import _fetch_types
    return _fetch_types(conn, guids)


def _starting_set(conn: ArchicadConnection, element_type: str | None,
                  selection_only: bool) -> tuple[list[str], dict[str, str]]:
    """(guids, known types). Asking for one type is answered by Tapir directly,
    which skips reading the type of every element on the plan."""
    if selection_only:
        guids = get_selected_element_ids(conn)
        types = _types_for(conn, guids) if guids else {}
        if element_type is not None:
            guids = [g for g in guids if types.get(g) == element_type]
        return guids, types
    if element_type is not None:
        guids = get_element_ids_of_type(conn, element_type)
        return guids, {g: element_type for g in guids}
    guids = get_all_element_ids(conn)
    return guids, (_types_for(conn, guids) if guids else {})


def query_elements(conn: ArchicadConnection, element_type: str | None = None,
                   layer: str | None = None, story: int | None = None,
                   classification_system: str | None = None,
                   selection_only: bool = False) -> dict:
    guids, types = _starting_set(conn, element_type, selection_only)

    if layer is not None:
        values = fetch_property_values(conn, guids, [BUILTIN_LAYER])
        guids = [g for g in guids if values.get(g, {}).get(BUILTIN_LAYER) == layer]

    if story is not None:
        floors = _fetch_floor_indices(conn, guids)
        guids = [g for g in guids if floors.get(g) == story]

    if classification_system is not None:
        from archicad_mcp.extract import _fetch_classifications
        classif = _fetch_classifications(conn, guids) if guids else {}
        guids = [g for g in guids if classif.get(g, {}).get(classification_system)]

    by_type = Counter(types.get(g, "") for g in guids)
    return {"count": len(guids), "guids": guids, "by_type": dict(by_type),
            **coverage_of(conn)}
