"""find_elements: criteria over the open model, evaluated in the server.

Live-probed on AC 29 / Tapir 1.5.9 (2026-09-03): no official or Tapir command
filters elements by property value server-side. Tapir's FilterElements knows
only visibility and editability flags, and the official
GetElementsByClassification matches one exact item. So property criteria are
evaluated here, after reading the values, and every read goes through the
crash-prone GetPropertyValuesOfElements. This module exists to make that read
as small as it can be:

1. Element types narrow server-side (Tapir GetElementsByType, one call per type).
2. Story and classification comparisons are cheap reads that never touch the
   property command, and in an `and` group they narrow the set before any
   property is read.
3. User-defined properties are pre-checked for availability
   (GetPropertyDefinitionAvailability x the element's classification), and an
   (element, property) pair the definition does not cover is answered as
   notAvailable without asking Archicad for it. Verified live: the prediction
   agreed with GetAllPropertyIdsOfElements on 228 of 228 custom definitions.
   The recorded crashes were user-defined property reads, so this is aimed at
   the hypothesised trigger, but it is bounding, not proof of safety.
4. The property fetch ceiling (ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS) still refuses
   an oversized read outright.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.criteria import (
    Cell,
    ClassificationTree,
    Comparison,
    CriteriaError,
    CriteriaGroup,
    group_matches,
    needs_story,
    parse_groups,
    properties_referenced,
    systems_referenced,
)
from archicad_mcp.extract import (
    _fetch_classifications,
    _fetch_floor_indices,
    _fetch_types,
    coverage_of,
    fetch_property_cells,
    get_all_element_ids,
    get_element_ids_of_type,
    get_selected_element_ids,
    resolve_property_ids,
)


def _starting_sets(conn: ArchicadConnection, groups: list[CriteriaGroup],
                   selection_only: bool) -> tuple[list[list[str]], dict[str, str]]:
    """Per-group candidate GUIDs, plus the known type of every candidate."""
    if selection_only:
        guids = get_selected_element_ids(conn)
        types = _fetch_types(conn, guids) if guids else {}
        return [[g for g in guids if grp.type_matches(types.get(g, ""))] for grp in groups], types

    all_guids: list[str] | None = None
    all_types: dict[str, str] = {}
    types: dict[str, str] = {}
    sets: list[list[str]] = []
    for grp in groups:
        if grp.element_types and grp.element_types_operator == "is":
            guids: list[str] = []
            for t in grp.element_types:
                found = get_element_ids_of_type(conn, t)
                guids.extend(found)
                types.update({g: t for g in found})
            sets.append(guids)
            continue
        if all_guids is None:
            all_guids = get_all_element_ids(conn)
            all_types = _fetch_types(conn, all_guids) if all_guids else {}
            types.update(all_types)
        sets.append([g for g in all_guids if grp.type_matches(all_types.get(g, ""))])
    return sets, types


def _split_cheap(group: CriteriaGroup) -> tuple[tuple[Comparison, ...], tuple[Comparison, ...]]:
    cheap = tuple(c for c in group.comparisons if c.kind != "property")
    costly = tuple(c for c in group.comparisons if c.kind == "property")
    return cheap, costly


def find_elements(conn: ArchicadConnection, groups: list[dict],
                  selection_only: bool = False) -> dict:
    try:
        parsed = parse_groups(groups)
    except CriteriaError as exc:
        return {"error": str(exc)}

    sets, types = _starting_sets(conn, parsed, selection_only)
    candidates = list(dict.fromkeys(g for s in sets for g in s))
    notes: list[str] = []

    # ---- cheap data: story and classifications ----
    floors: dict[str, int | None] = {}
    if needs_story(parsed) and candidates:
        floors = _fetch_floor_indices(conn, candidates)
        if not floors:
            notes.append("story comparisons need the Tapir add-on (floorIndex); "
                         "without it no element matches a story comparison")

    systems = systems_referenced(parsed)
    prop_names = properties_referenced(parsed)
    ids = resolve_property_ids(conn, prop_names) if prop_names else {}
    for name in prop_names:
        if name not in ids:
            notes.append(f"property '{name}' did not resolve; its comparisons "
                         "treat every element as notAvailable "
                         "(search_definitions finds the exact address)")

    availability = _custom_availability(conn, ids) if ids else {}
    classif: dict[str, dict[str, str | None]] = {}
    trees: dict[str, ClassificationTree | None] = {}
    if candidates and (systems or availability):
        classif = _fetch_classifications(conn, candidates)
    if systems:
        trees = _load_trees(conn, systems)
        for name in systems:
            if trees.get(name) is None:
                notes.append(f"classification system '{name}' not found in the "
                             "project; its comparisons match nothing")

    def cheap_cell(guid: str, cmp: Comparison) -> Cell:
        if cmp.kind == "story":
            if guid in floors and floors[guid] is not None:
                return Cell("normal", floors[guid])
            return Cell("notAvailable")
        item = classif.get(guid, {}).get(cmp.system)
        if cmp.system not in trees or trees[cmp.system] is None:
            return Cell("notAvailable")
        return Cell("normal", item) if item else Cell("userUndefined")

    # ---- decide which elements need which property reads ----
    # For an `and` group, elements failing a cheap comparison are dropped before
    # the property read; for an `or` group every candidate must be read.
    need_read: dict[str, set[str]] = defaultdict(set)  # guid -> property names
    survivors: list[list[str]] = []
    for grp, guids in zip(parsed, sets):
        cheap, costly = _split_cheap(grp)
        keep = []
        for g in guids:
            if grp.logical_operator == "and" and cheap:
                if not all(_matches(c, cheap_cell(g, c), trees) for c in cheap):
                    continue
            keep.append(g)
            for c in costly:
                need_read[g].add(c.property)
        survivors.append(keep)

    # ---- availability pre-check, then the property reads ----
    cells: dict[str, dict[str, Cell]] = defaultdict(dict)
    skipped = 0
    by_request: dict[frozenset[str], list[str]] = defaultdict(list)
    for guid, names in need_read.items():
        readable = set()
        for name in names:
            if name not in ids:
                cells[guid][name] = Cell("notAvailable")
                continue
            allowed = availability.get(name)
            if allowed is not None:
                items = {v for v in classif.get(guid, {}).values() if v}
                if not (items & allowed):
                    cells[guid][name] = Cell("notAvailable")
                    skipped += 1
                    continue
            readable.add(name)
        if readable:
            by_request[frozenset(readable)].append(guid)

    read_count = 0
    for names, guids in by_request.items():
        raw = fetch_property_cells(conn, guids, sorted(names))
        read_count += len(guids)
        for g in guids:
            for name in names:
                cells[g][name] = Cell.from_api(raw.get(g, {}).get(name))

    # ---- evaluate ----
    matched: dict[str, None] = {}
    for grp, guids in zip(parsed, survivors):
        for g in guids:
            def provider(c: Comparison, g=g) -> Cell:
                if c.kind == "property":
                    return cells.get(g, {}).get(c.property, Cell("notAvailable"))
                return cheap_cell(g, c)
            if group_matches(grp, types.get(g, ""), provider, lambda s: trees.get(s)):
                matched.setdefault(g)

    guids_out = list(matched)
    result = {"count": len(guids_out), "guids": guids_out,
              "by_type": dict(Counter(types.get(g, "") for g in guids_out)),
              "candidates": len(candidates),
              "property_reads": read_count,
              **coverage_of(conn)}
    if skipped:
        result["skipped_not_available"] = skipped
    if notes:
        result["notes"] = notes
    return result


def _matches(cmp: Comparison, cell: Cell, trees: dict) -> bool:
    from archicad_mcp.criteria import comparison_matches
    tree = trees.get(cmp.system) if cmp.kind == "classification" else None
    return comparison_matches(cmp, cell, tree)


def _custom_availability(conn: ArchicadConnection,
                         ids: dict[str, dict]) -> dict[str, frozenset[str]]:
    """name -> classification item GUIDs the definition is available for.

    Only definitions with a non-empty list are returned: built-ins report an
    empty list (their availability is by element type, not classification),
    and for those no cheap pre-check exists.
    """
    names = list(ids)
    response = conn.official("API.GetPropertyDefinitionAvailability",
                             {"propertyIds": [{"propertyId": ids[n]} for n in names]})
    out: dict[str, frozenset[str]] = {}
    for name, item in zip(names, response.get("propertyDefinitionAvailabilityList", [])):
        inner = item.get("propertyDefinitionAvailability", {})
        items = frozenset(x.get("classificationItemId", {}).get("guid", "")
                          for x in inner.get("availableClassifications", []))
        items -= {""}
        if items:
            out[name] = items
    return out


def _load_trees(conn: ArchicadConnection, systems: list[str]) -> dict[str, ClassificationTree | None]:
    known = conn.official("API.GetAllClassificationSystems").get("classificationSystems", [])
    by_name = {s.get("name"): s for s in known}
    out: dict[str, ClassificationTree | None] = {}
    for name in systems:
        system = by_name.get(name)
        if system is None:
            out[name] = None
            continue
        tree = conn.official("API.GetAllClassificationsInSystem",
                             {"classificationSystemId": system["classificationSystemId"]})
        out[name] = ClassificationTree(tree.get("classificationItems", []))
    return out
