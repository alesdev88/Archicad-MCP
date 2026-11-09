"""What the library-parts-loaded and ifc-spatial-structure rules read from Archicad, from
Tapir's main after 1.8.0 and not tried live yet. Neither reads a property.

Each answers None where this Tapir has no such command, and with the reason as text where
Archicad refuses, so one refusal skips its rule and not the whole audit. Tapir answers a
refusal, such as IFC before Archicad 28, as a top-level error, which the transport raises.
"""
from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.create import LoadedParts
from archicad_mcp.extract import (
    PROPERTY_FETCH_CHUNK,
    _fetch_floor_indices,
    element_payload,
    get_element_ids_of_type,
)
from archicad_mcp.rules.types import IfcStructure, MissingParts

PART_NAMES = "GetMissingLibPartNames"
IFC_STRUCTURE = "GetIFCSpatialStructure"
# The types GetMissingLibPartNames answers for.
PART_TYPES = ("Object", "Lamp", "Window", "Door")
_IFC_OBJECTS = {"project": "IfcProject", "site": "IfcSite", "building": "IfcBuilding"}
_REFUSED = (APIErrorBase, ArchicadUnavailableError)


def _chunks(guids: list[str]) -> list[list[str]]:
    return [guids[i:i + PROPERTY_FETCH_CHUNK] for i in range(0, len(guids), PROPERTY_FETCH_CHUNK)]


def _reason(exc: Exception) -> str:
    return getattr(exc, "message", None) or str(exc)


def read_missing_parts(conn: ArchicadConnection) -> MissingParts | str | None:
    """The elements whose library part no loaded library has. A library listing with
    unread entries among its parts answers as a reason: the part may be one of them."""
    if not conn.tapir_command_available(PART_NAMES):
        return None
    try:
        guids = [g for kind in PART_TYPES for g in get_element_ids_of_type(conn, kind)]
        if not guids:
            return MissingParts(checked=0, guids=())
        names = {}
        for chunk in _chunks(guids):
            answers = conn.tapir(PART_NAMES, {"elements": element_payload(chunk)}).get(
                "libPartNames", [])
            names.update(zip(chunk, answers))
    except _REFUSED as exc:
        return f"Archicad refused the read ({_reason(exc)})"
    library = LoadedParts(conn)
    if library.answer() is None:
        return "the loaded library parts could not be listed"
    if not library.complete():
        return ("some library entries could not be read, so a part not in the listing may "
                "still be loaded")
    loaded = library.spellings()
    missing, unread = [], 0
    for guid in guids:
        name = (names.get(guid) or {}).get("libPartName")
        if name is None:
            unread += 1
        elif name and name.casefold() not in loaded:
            missing.append(guid)
    if unread == len(guids):
        return (f"the part name of all {unread} element(s) could not be read, so what is missing "
                "is not known")
    return MissingParts(checked=len(guids), guids=tuple(missing), unread=unread)


def read_ifc_structure(conn: ArchicadConnection,
                       story_types: frozenset[str]) -> IfcStructure | str | None:
    """What Archicad reports of the IFC model, and the stories of `story_types` elements."""
    if not conn.tapir_command_available(IFC_STRUCTURE):
        return None
    try:
        kinds = {g: kind for kind in sorted(story_types)
                 for g in get_element_ids_of_type(conn, kind)}
        answer = conn.tapir(IFC_STRUCTURE, {})
        ifc_stories = {}
        for chunk in _chunks(list(kinds)):
            rows = conn.tapir(IFC_STRUCTURE, {"elements": element_payload(chunk)}).get(
                "elements", [])
            ifc_stories.update(zip(chunk, rows))
        homes = _fetch_floor_indices(conn, list(kinds)) if kinds else {}
    except _REFUSED as exc:
        return f"Archicad refused the read ({_reason(exc)})"
    reported = frozenset(name for key, name in _IFC_OBJECTS.items() if answer.get(key) is not None)
    stories = {g: (kind, (ifc_stories.get(g) or {}).get("storyIndex"), homes.get(g))
               for g, kind in kinds.items()}
    return IfcStructure(reported=reported, stories=stories)
