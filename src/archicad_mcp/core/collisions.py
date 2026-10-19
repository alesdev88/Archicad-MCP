"""Which elements clash: Tapir GetCollisions as pairs an agent can act on.

GetCollisions (1.2.2) is one ACAPI_Element_GetCollisions call over two
groups of GUIDs. Live on MCP-Test, 19.10.2026 (Archicad 29, Tapir 1.7.1): one
group sent as both sides answered each pair once, with no self pairs; the
first element of a pair comes from group 1; the volume tolerance is the
intersection volume in m3. Crossing walls Archicad joins gave no collision
even at tolerance 0. A Morph made as an open surface collided with nothing
under the volume check and with the wall it crossed once the surface check
was on. Archicad also leaves out building materials set not to take part in
collision detection (its own setting, not tried live). Built against Archicad 30, Tapir spells the clearance key
hasClearanceCollision; against 29 and older, hasClearenceCollision.
The design is docs/superpowers/specs/2026-10-19-collisions-favorites-design.md.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.element_data import cap_list
from archicad_mcp.core.query_schema import element_type_names
from archicad_mcp.extract import PROPERTY_FETCH_CHUNK, element_payload

DEFAULT_VOLUME_TOLERANCE = 0.001
# Tapir's own default; sent whenever the surface check is off, because the
# schema requires all three settings together.
_DEFAULT_SURFACE_TOLERANCE = 0.001
_CLEARANCE_KEYS = ("hasClearanceCollision", "hasClearenceCollision")


@dataclass(frozen=True)
class CollisionQuery:
    """What a no-collisions rule asks: types against types, with tolerances."""
    element_types: tuple[str, ...]
    against_types: tuple[str, ...] | None = None
    volume_tolerance: float = DEFAULT_VOLUME_TOLERANCE
    surface_tolerance: float | None = None


@dataclass(frozen=True)
class Collision:
    element: str
    against: str
    body: bool
    clearance: bool

    @property
    def kind(self) -> str:
        if self.body and self.clearance:
            return "body and clearance"
        return "clearance" if self.clearance else "body"


def resolve_types(names: list[str]) -> tuple[str, ...]:
    """Tapir's ElementType names for `names`, matched without case, with one
    trailing 's' allowed. ValueError lists the valid names for a miss."""
    known = [n for n in element_type_names() if n != "all"]
    by_lower = {n.lower(): n for n in known}
    out: list[str] = []
    for name in names:
        key = str(name).strip().lower()
        match = by_lower.get(key) or (by_lower.get(key[:-1]) if key.endswith("s") else None)
        if match is None:
            raise ValueError(f"Unknown element type '{name}'. Element types: "
                             + ", ".join(known))
        if match not in out:
            out.append(match)
    return tuple(out)


def elements_of_types(conn: ArchicadConnection, types: tuple[str, ...]) -> list[str]:
    """Every element of the types, upper case, in type order, once each."""
    out: dict[str, None] = {}
    for element_type in types:
        response = conn.tapir("GetElementsByType", {"elementType": element_type})
        for e in response.get("elements", []):
            out[e["elementId"]["guid"].upper()] = None
    return list(out)


def _settings(volume_tolerance: float, surface_tolerance: float | None) -> dict:
    return {"volumeTolerance": volume_tolerance,
            "performSurfaceCheck": surface_tolerance is not None,
            "surfaceTolerance": (surface_tolerance if surface_tolerance is not None
                                 else _DEFAULT_SURFACE_TOLERANCE)}


def detect(conn: ArchicadConnection, group: list[str], against: list[str] | None,
           volume_tolerance: float, surface_tolerance: float | None) -> list[Collision]:
    """The collisions between `group` and `against` (`group` itself when None).

    Pairs are kept once as unordered pairs and self pairs dropped, in case
    Archicad ever answers them for groups that overlap.
    """
    other = group if against is None else against
    if not group or not other:
        return []
    response = conn.tapir("GetCollisions", {
        "elementsGroup1": element_payload(group),
        "elementsGroup2": element_payload(other),
        "settings": _settings(volume_tolerance, surface_tolerance)})
    seen: set[frozenset[str]] = set()
    found: list[Collision] = []
    for item in response.get("collisions", []):
        a = item["elementId1"]["guid"].upper()
        b = item["elementId2"]["guid"].upper()
        key = frozenset((a, b))
        if a == b or key in seen:
            continue
        seen.add(key)
        clearance = any(bool(item.get(k)) for k in _CLEARANCE_KEYS)
        found.append(Collision(element=a, against=b,
                               body=bool(item.get("hasBodyCollision")), clearance=clearance))
    return found


def answer(conn: ArchicadConnection, query: CollisionQuery) -> list[Collision]:
    """A rule's query answered over the whole plan."""
    group = elements_of_types(conn, query.element_types)
    against = (None if query.against_types is None
               else elements_of_types(conn, query.against_types))
    return detect(conn, group, against, query.volume_tolerance, query.surface_tolerance)


def _read(conn: ArchicadConnection, guids: list[str]) -> dict[str, dict]:
    """GUID -> {type, id, floorIndex} for the elements the read finds."""
    found: dict[str, dict] = {}
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        items = conn.tapir("GetDetailsOfElements", {
            "elements": element_payload(chunk),
            "fields": ["type", "id", "floorIndex"]}).get("detailsOfElements", [])
        for guid, item in zip(chunk, items):
            if isinstance(item, dict) and "error" not in item:
                found[guid] = item
    return found


def _group(conn: ArchicadConnection, guids: list[str] | None,
           types: tuple[str, ...]) -> list[str]:
    out = dict.fromkeys(g.strip().upper() for g in guids or [] if g and g.strip())
    out.update(dict.fromkeys(elements_of_types(conn, types)))
    return list(out)


def _pair_view(c: Collision, info: dict[str, dict]) -> dict:
    a, b = info.get(c.element, {}), info.get(c.against, {})
    return {"element": c.element, "element_type": a.get("type"),
            "element_id": a.get("id") or None,
            "against": c.against, "against_type": b.get("type"),
            "against_id": b.get("id") or None,
            "story": a.get("floorIndex"), "kind": c.kind}


def find_collisions(conn: ArchicadConnection, guids: list[str] | None,
                    element_types: list[str] | None, against_guids: list[str] | None,
                    against_types: list[str] | None,
                    volume_tolerance: float = DEFAULT_VOLUME_TOLERANCE,
                    surface_tolerance: float | None = None) -> dict:
    if not guids and not element_types:
        return {"error": "Give the first group as guids, element_types or both "
                         "(the second group, against_guids or against_types, is "
                         "optional: without it the first group is checked "
                         "against itself)."}
    try:
        types = resolve_types(element_types or [])
        other_types = resolve_types(against_types or [])
    except ValueError as exc:
        return {"error": str(exc)}
    group = _group(conn, guids, types)
    within = not against_guids and not against_types
    against = None if within else _group(conn, against_guids, other_types)

    explicit = list(dict.fromkeys(g.strip().upper() for g in
                                  [*(guids or []), *(against_guids or [])] if g and g.strip()))
    info = _read(conn, explicit) if explicit else {}
    missing = [g for g in explicit if g not in info]
    group = [g for g in group if g not in missing]
    if against is not None:
        against = [g for g in against if g not in missing]

    found = detect(conn, group, against, volume_tolerance, surface_tolerance)
    involved = list(dict.fromkeys(g for c in found for g in (c.element, c.against)))
    unread = [g for g in involved if g not in info]
    if unread:
        info.update(_read(conn, unread))

    result: dict = {
        "checked": {"elements": len(group),
                    "against": "the same group" if against is None else len(against)},
        "collisions": len(found),
        "tolerances": {"volume_m3": volume_tolerance,
                       "surface_m2": surface_tolerance},
    }
    shown, rest = cap_list(found)
    result["pairs"] = [_pair_view(c, info) for c in shown]
    if rest:
        result["not_shown"] = rest
    by_types = Counter(" / ".join(sorted((str(info.get(c.element, {}).get("type")),
                                          str(info.get(c.against, {}).get("type")))))
                       for c in found)
    result["by_type_pair"] = [{"types": t, "count": n} for t, n in by_types.most_common()]
    elements, elements_rest = cap_list(involved)
    result["elements"] = elements
    if elements_rest:
        result["elements_not_shown"] = elements_rest
    if missing:
        result["not_found"] = missing
    return result
