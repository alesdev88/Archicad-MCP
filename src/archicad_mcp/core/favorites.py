"""Make existing elements match a favourite: Tapir ApplyFavoritesToElements,
dry run first, with a report of what the favourite really changed.

ApplyFavoritesToElements (1.5.4) calls ACAPI_Element_ChangeParameters with a
full mask and the favourite's memo. Its description says it never touches
geometry; live on MCP-Test, 19.10.2026 (Archicad 29, Tapir 1.7.1), it did:
a wall with ID TEST-A, layer "Shell - General", height 2.4 (top unlinked) and
reference line Center came out with the favourite's own ID "SW - 001", its
layer, height 3 linked to the story above and reference line Outside
(Tapir issue #903). Doors and windows took the favourite's library part,
size, sill and ID and stayed in place. So the dry run names what each type
takes over, the commit diffs a details read before and after, and the
Element ID is written back by default: a favourite's ID is a template value,
and forty doors with one ID break the numbering.
The design is docs/superpowers/specs/2026-10-19-collisions-favorites-design.md.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.attributes import layer_table
from archicad_mcp.core.editability import NOT_FOUND, _details, reason_text, refusals
from archicad_mcp.core.element_data import cap_list
from archicad_mcp.core.modify import _grouped_warnings
from archicad_mcp.core.mutate import _group
from archicad_mcp.core.query_schema import element_type_names
from archicad_mcp.extract import PROPERTY_FETCH_CHUNK

KEEPABLE = ("id", "layer")
DEFAULT_KEEP = ("id",)
FLAGS = {"settings": "applySettings", "classifications": "applyClassifications",
         "categories": "applyCategories", "properties": "applyProperties"}

OPENING_TYPES = frozenset({"Door", "Window", "Skylight"})
HIERARCHICAL_TYPES = frozenset({"Stair", "Railing", "CurtainWall"})
_TYPE_WARNINGS = {
    "Wall": ("takes the favourite's height and top link, reference line, structure "
             "and thickness, not only its materials and pens; its plan position stays"),
    "Column": "takes the favourite's height and top link; its plan position stays",
}
# Every type the live check did not cover: the wall shows the favourite can
# carry more than its description says, so the dry run must not imply less.
_UNCHECKED_WARNING = ("may also take the favourite's size, thickness, height or "
                      "elevation (not checked live for this type); the commit reports "
                      "every field it changed")
_OPENING_WARNING = ("takes the favourite's library part, width, height and sill "
                    "height; it stays at its place in the wall")
_HIERARCHICAL_WARNING = ("gets only its own settings: Tapir leaves the settings of its "
                         "parts (treads, posts, panels) alone so it does not move")
_LAYER_WARNING = "takes the favourite's layer"
_ID_WARNING = "takes the favourite's Element ID (keep=[\"id\"] writes its own back)"


def _all_types() -> list[str]:
    return [t for t in element_type_names() if t != "all"]


def _names(conn: ArchicadConnection, element_type: str) -> list[str]:
    try:
        response = conn.tapir("GetFavoritesByType", {"elementType": element_type})
    except APIErrorBase:
        return []
    return list(response.get("favorites") or [])


def favorites_by_type(conn: ArchicadConnection, types: list[str]) -> dict[str, list[str]]:
    """Type -> its favourites' names, for the types that have any."""
    out: dict[str, list[str]] = {}
    for element_type in dict.fromkeys(types):
        names = _names(conn, element_type)
        if names:
            out[element_type] = names
    return out


def _types_holding(index: dict[str, list[str]], favorite: str) -> list[str]:
    return [t for t, names in index.items() if favorite in names]


def _read(conn: ArchicadConnection, guids: list[str], fields: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        # A Tapir before `fields` (1.5.9) refuses it; _details retries without.
        items = _details(conn, chunk, fields).get("detailsOfElements", [])
        for guid, item in zip(chunk, items):
            if isinstance(item, dict) and "error" not in item:
                found[guid] = item
    return found


def _layers(conn: ArchicadConnection) -> dict[int, str]:
    try:
        return {i: v["name"] for i, v in layer_table(conn).items()}
    except APIErrorBase:
        return {}


@dataclass
class FavoritePlan:
    favorite: str
    favorite_types: list[str]
    requested: int
    planned: list[dict] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    flags: dict[str, bool] = field(default_factory=dict)
    keep: tuple[str, ...] = DEFAULT_KEEP
    window: str | None = None
    layers: dict[int, str] = field(default_factory=dict)


def _clean_guids(guids: list[str] | None) -> list[str]:
    return list(dict.fromkeys(g.strip().upper() for g in guids or []
                              if isinstance(g, str) and g.strip()))


def _unknown(favorite: str, index: dict[str, list[str]]) -> dict:
    every = sorted({n for names in index.values() for n in names})
    lowered = {n.lower(): n for n in every}
    close = [lowered[n] for n in difflib.get_close_matches(favorite.lower(), list(lowered),
                                                           n=3, cutoff=0.6)]
    hint = (f" Did you mean: {', '.join(repr(n) for n in close)}?" if close
            else " Call apply_favorites without a favorite to list them.")
    return {"error": f"No favourite is named '{favorite}' (names are matched exactly)."
                     + hint}


def _warnings(element_type: str, keep: tuple[str, ...]) -> list[str]:
    out = []
    if element_type in _TYPE_WARNINGS:
        out.append(_TYPE_WARNINGS[element_type])
    elif element_type in OPENING_TYPES:
        out.append(_OPENING_WARNING)
    elif element_type in HIERARCHICAL_TYPES:
        out.append(_HIERARCHICAL_WARNING)
    else:
        out.append(_UNCHECKED_WARNING)
    if "layer" not in keep:
        out.append(_LAYER_WARNING)
    if "id" not in keep:
        out.append(_ID_WARNING)
    return out


def plan(conn: ArchicadConnection, guids: list[str], favorite: str,
         flags: dict[str, bool], keep: tuple[str, ...]) -> FavoritePlan | dict:
    """Read and check every element; nothing is written."""
    p = FavoritePlan(favorite=favorite, favorite_types=[], requested=len(guids),
                     flags=flags, keep=keep)
    refused, p.window = refusals(conn, guids) if guids else ({}, None)
    p.skipped.update(refused)
    entries = _read(conn, [g for g in guids if g not in refused], ["type", "id", "layerIndex"])
    for g in guids:
        if g not in refused and g not in entries:
            p.skipped[g] = reason_text(NOT_FOUND, p.window)
    types = list(dict.fromkeys(e.get("type", "") for e in entries.values()))
    index = favorites_by_type(conn, types)
    p.favorite_types = _types_holding(index, favorite)
    if not p.favorite_types or set(types) - set(p.favorite_types):
        # Name the type the favourite does belong to, or offer close names.
        full = favorites_by_type(conn, _all_types())
        holders = _types_holding(full, favorite)
        if not holders:
            return _unknown(favorite, full)
    else:
        holders = p.favorite_types
    p.layers = _layers(conn)
    for g in guids:
        entry = entries.get(g)
        if entry is None:
            continue
        element_type = entry.get("type", "")
        if element_type not in p.favorite_types:
            p.skipped[g] = (f"'{favorite}' is not a {element_type} favourite (it is a "
                            f"{' and '.join(holders)} favourite)")
            continue
        p.planned.append({"guid": g, "type": element_type, "id": entry.get("id") or None,
                          "layerIndex": entry.get("layerIndex")})
        p.warnings[g] = _warnings(element_type, keep)
    if not p.favorite_types:
        p.favorite_types = holders
    return p


def _notes(flags: dict[str, bool]) -> list[str]:
    out = []
    if flags.get("classifications"):
        out.append("Classifications are added per system: a system the favourite does "
                   "not classify keeps the element's item.")
    if flags.get("properties"):
        out.append("Property values the favourite stores overwrite the element's.")
    return out


def _planned_view(entry: dict, layers: dict[int, str]) -> dict:
    view = {"guid": entry["guid"], "type": entry["type"], "id": entry["id"]}
    index = entry.get("layerIndex")
    view["layer"] = layers.get(index, index)
    return view


def _favorite_fields(p: FavoritePlan) -> dict:
    if len(p.favorite_types) == 1:
        return {"favorite": p.favorite, "favorite_type": p.favorite_types[0]}
    return {"favorite": p.favorite, "favorite_types": p.favorite_types}


def report_dry_run(p: FavoritePlan) -> dict:
    out: dict = {"dry_run": True, **_favorite_fields(p), "requested": p.requested,
                 "planned_count": len(p.planned)}
    shown, rest = cap_list(p.planned)
    if shown:
        out["planned"] = [_planned_view(e, p.layers) for e in shown]
    if rest:
        out["not_shown"] = rest
    if p.skipped:
        out["skipped"] = _group(p.skipped)
    warnings = {g: w for g, w in p.warnings.items() if w}
    if warnings:
        out["warnings"] = _grouped_warnings(warnings)
    out["applies"] = dict(p.flags)
    out["keep"] = list(p.keep)
    notes = _notes(p.flags)
    if notes:
        out["notes"] = notes
    if p.window is not None:
        out["active_window"] = p.window
    return out


def _to_defaults(conn: ArchicadConnection, favorite: str, dry_run: bool) -> dict:
    full = favorites_by_type(conn, _all_types())
    holders = _types_holding(full, favorite)
    if not holders:
        return _unknown(favorite, full)
    tool = holders[0] if len(holders) == 1 else holders
    if dry_run:
        return {"dry_run": True, "to_defaults": True, "favorite": favorite, "tool": tool,
                "note": "Sets the default settings of that tool, what the next element "
                        "drawn with it gets; no element changes."}
    response = conn.tapir("ApplyFavoritesToElementDefaults", {"favorites": [favorite]})
    results = response.get("executionResults") or [{}]
    outcome = results[0] if isinstance(results[0], dict) else {}
    if outcome.get("success") is False:
        error = outcome.get("error") or {}
        return {"dry_run": False, "to_defaults": True, "favorite": favorite, "tool": tool,
                "applied": False, "error": error.get("message", error)}
    return {"dry_run": False, "to_defaults": True, "favorite": favorite, "tool": tool,
            "applied": True}


def apply_favorites(conn: ArchicadConnection, guids: list[str] | None,
                    favorite: str | None, settings: bool = True,
                    classifications: bool = True, categories: bool = True,
                    properties: bool = True, keep: list[str] | None = None,
                    to_defaults: bool = False, dry_run: bool = True) -> dict:
    keep_set = tuple(DEFAULT_KEEP if keep is None else dict.fromkeys(keep))
    bad = [k for k in keep_set if k not in KEEPABLE]
    if bad:
        return {"error": f"keep takes {', '.join(repr(k) for k in KEEPABLE)}; "
                         f"{', '.join(repr(k) for k in bad)} cannot be kept here. Change "
                         "heights, sizes and other fields back with modify_elements."}
    targets = _clean_guids(guids)
    if to_defaults:
        if targets:
            return {"error": "to_defaults applies the favourite to a tool's default "
                             "settings, not to elements: give no guids with it."}
        if not favorite:
            return {"error": "to_defaults needs a favorite."}
        return _to_defaults(conn, favorite, dry_run)
    if not favorite:
        types = (list(dict.fromkeys(e.get("type", "") for e in
                                    _read(conn, targets, ["type"]).values()))
                 if targets else _all_types())
        return {"favorites": favorites_by_type(conn, types)}
    if not targets:
        return {"error": "Give the elements as guids (or to_defaults=true for the "
                         "tool defaults)."}
    flags = {"settings": settings, "classifications": classifications,
             "categories": categories, "properties": properties}
    p = plan(conn, targets, favorite, flags, keep_set)
    if isinstance(p, dict):
        return p
    if dry_run:
        return report_dry_run(p)
    from archicad_mcp.core.favorites_commit import commit
    return commit(conn, p)
