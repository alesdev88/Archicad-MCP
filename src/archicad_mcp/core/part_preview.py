"""preview_library_parts: a part's 2D symbol drawn with nothing placed, to choose between
candidates. From Tapir's main after 1.8.0, read from its source and not tried live yet.

Sizes come back as JSON and each cell has its own scale, as a stool and a bed on one scale
would make the stool unreadable.
"""
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.create import LoadedParts, closest_names
from archicad_mcp.core.part_symbols import (
    default_parameters,
    draw_symbols,
    own_size_of,
    symbol_extents,
    symbol_of,
)
from archicad_mcp.tapir_requirements import require_tapir_command

TOOL = "preview_library_parts"
MAX_PARTS = 8
_READS = ("GetLibraryPart2DPrimitives", "GetGDLParametersOfLibraryParts")

NOTE = ("Each cell draws one part's 2D symbol at its default parameters, at its own scale, "
        "numbered as `parts` lists them, with a red cross at the insertion point. It shows only "
        "lines, arcs and polygons, so no texts or hotspots. Use it to choose between candidates: "
        "it does not replace learning the part or the look after placing.")


def _why_not_drawn(library: LoadedParts, name: str, primitives: list | None) -> str:
    if primitives is not None:
        return "its symbol has no lines, arcs or polygons at its default parameters"
    if library.answer() is None:
        return "Archicad gave no 2D symbol for it, and the loaded parts could not be listed"
    names = sorted({str(e.get("documentName")) for e in library.parts()})
    if name in names:
        return "Archicad gave no 2D symbol for it"
    return f"no loaded library part named '{name}'{closest_names(name, names)}"


def preview_library_parts(conn: ArchicadConnection, names: list[str]) -> dict:
    wanted = list(dict.fromkeys(names))
    if not 1 <= len(wanted) <= MAX_PARTS:
        return {"error": f"Give 1 to {MAX_PARTS} part names; {len(wanted)} were given. "
                         "Nothing was read."}
    for command in _READS:
        refusal = require_tapir_command(conn, command, TOOL)
        if refusal is not None:
            return refusal
    library = LoadedParts(conn)     # lists the library only if a name fails
    parts, symbols, not_drawn = [], [], []
    for name in wanted:
        primitives = symbol_of(conn, name)
        extents = symbol_extents(primitives) if primitives else None
        if extents is None:
            not_drawn.append({"name": name, "reason": _why_not_drawn(library, name, primitives)})
            continue
        own = own_size_of(default_parameters(conn, name)) or {}
        symbols.append(primitives)
        parts.append({"number": len(symbols), "name": name, "a": own.get("a"), "b": own.get("b"),
                      "height": own.get("height"),
                      "extent": [round(value, 4) + 0.0 for value in extents]})
    result: dict = {"parts": parts, "not_drawn": not_drawn, "note": NOTE}
    if symbols:
        result["png"] = draw_symbols(symbols)
    return result
