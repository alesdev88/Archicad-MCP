from __future__ import annotations

from collections import Counter

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    BUILTIN_STORY,
    fetch_property_values,
)


def _selected_guids(conn: ArchicadConnection) -> list[str]:
    response = conn.official("API.GetSelectedElements")
    return [e["elementId"]["guid"] for e in response.get("elements", [])]


def _all_guids(conn: ArchicadConnection) -> list[str]:
    response = conn.official("API.GetAllElements")
    return [e["elementId"]["guid"] for e in response.get("elements", [])]


def _types_for(conn: ArchicadConnection, guids: list[str]) -> dict[str, str]:
    from archicad_mcp.extract import element_payload
    response = conn.official("API.GetTypesOfElements", {"elements": element_payload(guids)})
    return {t["typeOfElement"]["elementId"]["guid"]: t["typeOfElement"]["elementType"]
            for t in response.get("types", [])}


def query_elements(conn: ArchicadConnection, element_type: str | None = None,
                   layer: str | None = None, story: int | None = None,
                   classification_system: str | None = None,
                   selection_only: bool = False) -> dict:
    guids = _selected_guids(conn) if selection_only else _all_guids(conn)
    types = _types_for(conn, guids) if guids else {}

    if element_type is not None:
        guids = [g for g in guids if types.get(g) == element_type]

    if layer is not None or story is not None:
        values = fetch_property_values(conn, guids, [BUILTIN_LAYER, BUILTIN_STORY])
        if layer is not None:
            guids = [g for g in guids if values.get(g, {}).get(BUILTIN_LAYER) == layer]
        if story is not None:
            guids = [g for g in guids if values.get(g, {}).get(BUILTIN_STORY) == story]

    if classification_system is not None:
        from archicad_mcp.extract import _fetch_classifications
        classif = _fetch_classifications(conn, guids) if guids else {}
        guids = [g for g in guids if classif.get(g, {}).get(classification_system)]

    by_type = Counter(types.get(g, "") for g in guids)
    return {"count": len(guids), "guids": guids, "by_type": dict(by_type)}
