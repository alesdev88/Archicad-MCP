"""Shared by the tools that place a library part along a path: create_swept_beam and
create_road_marking.

A source curve (a Morph line, Polyline, Line or Arc) or 3D points become nodes and plan
arcs (swept_path); the part is placed at a point and its node arrays are written in the
part's own coordinates. Tapir reports z as an absolute elevation for morphs, polylines,
lines, arcs and placed objects, and CreateObjects derives the story from an absolute z,
so everything here works in absolute z.
"""

from __future__ import annotations

import json
import math
import re

from multiconn_archicad.errors import ArchicadAPIError

from archicad_mcp.connection import ArchicadUnavailableError
from archicad_mcp.core import swept_path as sp

# From Tapir 1.7.2 (#558) SetGDLParametersOfElements writes an array item by item through
# ACAPI_LibraryPart_ChangeAParameter: it refuses a list of another size, runs the part's
# Parameter Script after every item (so the parts' node merge reacts to each one) and takes
# angle items in degrees. Up to 1.7.1 a list replaced the array as sent: any size, no
# script, angles in radians (docs/superpowers/specs/2026-10-19-tapir-1.7.2-array-writes.md).
ITEM_WRITES = (1, 7, 2)
MERGE_GAP = 0.01  # both parts merge consecutive nodes closer than this in plan
PARK_CLEARANCE = 10.0  # metres between a parked node and any real one across the path
GROW_STEP = 1.0  # each add-node write slides the end grip this far past its marker


class PlacedPathError(ValueError):
    """The request cannot become a part placed along a path."""


def from_json_text(value, name: str, kind: type):
    """Parse a list or object that arrived as JSON text.

    Claude Code collapses a nullable list or object field to an untyped schema
    and sends the value as text; refusing it would make the tool unusable there.
    """
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise PlacedPathError(f"{name} arrived as text that is not valid JSON ({exc.msg}). "
                              f"Pass a {kind.__name__}, or JSON text of one.") from exc
    if not isinstance(parsed, kind):
        raise PlacedPathError(f"{name} must be a JSON {kind.__name__}, "
                              f"got {type(parsed).__name__}.")
    return parsed


def details(conn, guid: str) -> dict:
    result = conn.tapir("GetDetailsOfElements", {"elements": [{"elementId": {"guid": guid}}]})
    entry = (result.get("detailsOfElements") or [{}])[0]
    if "error" in entry or "type" not in entry:
        raise PlacedPathError(f"Could not read element {guid}: {entry.get('error', entry)}")
    return entry


def path_from_element(conn, guid: str, tol: float, part: str):
    """The source's fitted path, its story, and whether it is flat (a Polyline, Line or Arc)."""
    entry = details(conn, guid)
    kind, d, floor = entry["type"], entry.get("details", {}), entry.get("floorIndex")
    if kind == "Morph":
        return _morph_path(d, tol), floor, False
    if kind == "PolyLine":
        return _polyline_path(d, part), floor, True
    if kind == "Line":
        z = float(d.get("zCoordinate", 0.0))
        b, e = d["begCoordinate"], d["endCoordinate"]
        return _distinct_path([(b["x"], b["y"], z), (e["x"], e["y"], z)], [0.0, 0.0]), floor, True
    if kind == "Arc":
        return _arc_path(d), floor, True
    raise PlacedPathError(
        f"Element {guid} is a {kind}. The source must be a Morph line, Polyline, Line "
        "or Arc (Splines are not supported: their curve shape is not readable).")


def _morph_path(d: dict, tol: float) -> sp.SweptPath:
    body = d.get("body") or {}
    if body.get("polygons"):
        raise PlacedPathError("This morph has faces. Draw the guide as a morph line (edges only).")
    order = sp.chain_wire_edges([tuple(e["vertexIds"]) for e in body.get("wireEdges", [])])
    o = d["origin"]
    ax = d.get("xAxis") or {"x": 1, "y": 0, "z": 0}
    ay = d.get("yAxis") or {"x": 0, "y": 1, "z": 0}
    az = d.get("zAxis") or {"x": 0, "y": 0, "z": 1}
    verts = body["vertices"]
    pts = []
    for i in order:
        v = verts[i]
        pts.append(tuple(o[k] + v["x"] * ax[k] + v["y"] * ay[k] + v["z"] * az[k]
                         for k in ("x", "y", "z")))
    return sp.fit_points(pts, tol)


def _polyline_path(d: dict, part: str) -> sp.SweptPath:
    coords = d.get("coordinates") or []
    if len(coords) < 2:
        raise PlacedPathError("The polyline has fewer than two points.")
    z = float(d.get("zCoordinate", 0.0))
    pts = [(float(c["x"]), float(c["y"]), z) for c in coords]
    if len(pts) > 2 and math.dist(pts[0][:2], pts[-1][:2]) < sp.DUPLICATE_EPS:
        raise PlacedPathError(f"The polyline is closed. A {part} needs a start and an end.")
    arcs = [0.0] * len(pts)
    for a in d.get("arcs") or []:
        if a["endIndex"] == a["begIndex"] + 1:
            arcs[a["begIndex"]] = math.degrees(a["arcAngle"])
    return _distinct_path(pts, arcs)


def _distinct_path(pts: list[sp.Point], arcs: list[float]) -> sp.SweptPath:
    """Drop repeated vertices, as the fit does for points (DWG polylines often
    repeat one). A zero-length piece has no direction and would make a false
    kink. The piece after a dropped vertex keeps its arc."""
    nodes, out_arcs, pending = [pts[0]], [], arcs[0]
    for p, arc in zip(pts[1:], arcs[1:]):
        if math.dist(p, nodes[-1]) >= sp.DUPLICATE_EPS:
            out_arcs.append(pending)
            nodes.append(p)
        pending = arc
    if len(nodes) < 2:
        raise sp.PathError("The source has fewer than two distinct points.")
    return sp.SweptPath(nodes, out_arcs + [0.0])


def _arc_path(d: dict) -> sp.SweptPath:
    if abs(float(d.get("ratio", 1.0)) - 1.0) > 1e-6:
        raise PlacedPathError("Elliptical arcs are not supported.")
    z = float(d.get("zCoordinate", 0.0))
    ox, oy, r = d["origin"]["x"], d["origin"]["y"], float(d["radius"])
    beg, end = float(d["begAngle"]), float(d["endAngle"])
    sweep = (end - beg) % (2 * math.pi)
    if sweep < 1e-9:
        raise PlacedPathError("The arc has no length.")
    p1 = (ox + r * math.cos(beg), oy + r * math.sin(beg), z)
    p2 = (ox + r * math.cos(end), oy + r * math.sin(end), z)
    return sp.SweptPath([p1, p2], [math.degrees(sweep), 0.0])


def path_from_points(points, tol: float) -> sp.SweptPath:
    try:
        pts = [(float(p["x"]), float(p["y"]), float(p["z"])) for p in points]
    except (KeyError, TypeError, ValueError) as exc:
        raise PlacedPathError("points must be a list of {x, y, z} numbers in metres.") from exc
    return sp.fit_points(pts, tol)


def attribute_index(conn, kind: str, name: str) -> int:
    attrs = conn.tapir("GetAttributesByType", {"attributeType": kind}).get("attributes", [])
    for a in attrs:
        if a.get("name") == name:
            return int(a["index"])
    for a in attrs:
        if str(a.get("name", "")).casefold() == name.casefold():
            return int(a["index"])
    close = sorted(str(a.get("name")) for a in attrs
                   if name.casefold() in str(a.get("name", "")).casefold()
                   or str(a.get("name", "")).casefold() in name.casefold())[:10]
    hint = f" Similar names: {', '.join(close)}." if close else ""
    raise PlacedPathError(f"No {kind} attribute named '{name}'.{hint}")


def library_part_loaded(conn, part: str) -> bool:
    parts = conn.tapir("GetAvailableLibraryParts",
                       {"filterByTypeId": "Object"}).get("libraryParts", [])
    want = part.casefold()
    return any(str(p.get("documentName", "")).casefold() == want
               or str(p.get("fileName", "")).casefold() == want + ".gsm" for p in parts)


def existing_frame(conn, guid: str, part: str):
    """A placed part's origin (absolute) and its angle in radians."""
    entry = details(conn, guid)
    d = entry.get("details", {})
    name = str((d.get("libPart") or {}).get("name", ""))
    if entry["type"] != "Object" or name.casefold() != part.casefold():
        raise PlacedPathError(f"Element {guid} is not a {part}. update_guid must "
                              f"point at a placed {part}.")
    o = d["origin"]
    return (float(o["x"]), float(o["y"]), float(o["z"])), float(d.get("angle", 0.0))


def to_local(nodes, origin, angle: float) -> list[sp.Point]:
    c, s = math.cos(-angle), math.sin(-angle)
    out = []
    for x, y, z in nodes:
        dx, dy = x - origin[0], y - origin[1]
        out.append((dx * c - dy * s, dx * s + dy * c, z - origin[2]))
    return out


def place(conn, part: str, origin, floor_index) -> str:
    item = {"libraryPartName": part,
            "coordinates": {"x": origin[0], "y": origin[1], "z": origin[2]}}
    if floor_index is not None:
        item["floorIndex"] = floor_index
    result = conn.tapir("CreateObjects", {"objectsData": [item]})
    elements = result.get("elements") or []
    if not elements or "elementId" not in elements[0]:
        raise PlacedPathError(f"CreateObjects did not place '{part}': {result}")
    return elements[0]["elementId"]["guid"]


def tapir_version(conn) -> tuple[int, int, int] | None:
    """The Tapir add-on's version, or None when it does not say."""
    try:
        raw = str(conn.tapir("GetAddOnVersion").get("version", ""))
    except (ArchicadAPIError, ArchicadUnavailableError):
        return None
    numbers = [int(n) for n in re.findall(r"\d+", raw)[:3]]
    return tuple((numbers + [0, 0, 0])[:3]) if numbers else None


def writes_items(conn) -> bool:
    """Whether this Tapir writes arrays item by item (1.7.2 and newer)."""
    version = tapir_version(conn)
    return version is not None and version >= ITEM_WRITES


def gdl_parameters(conn, guid: str) -> dict:
    """A placed part's GDL parameters by name, as the API reads them (angles in radians)."""
    res = conn.tapir("GetGDLParametersOfElements", {"elements": [{"elementId": {"guid": guid}}]})
    entry = (res.get("gdlParametersOfElements") or [{}])[0]
    params = entry.get("parameters")
    if not params:
        raise PlacedPathError(f"Could not read the parameters of element {guid}: {entry.get('error', entry)}")
    return {p["name"]: p["value"] for p in params}


def check_plan_gaps(local, part: str) -> None:
    """With item writes the part's merge runs on every node written, so two consecutive
    nodes closer than 1 cm in plan would lose one mid-write and fail it."""
    for i in range(len(local) - 1):
        gap = math.dist(local[i][:2], local[i + 1][:2])
        if gap < MERGE_GAP:
            raise PlacedPathError(
                f"Nodes {i + 1} and {i + 2} are {gap * 1000:.1f} mm apart in plan. The {part} merges "
                "nodes closer than 10 mm, and with Tapir 1.7.2 or newer it does so while they are "
                "written, which fails the write. Remove the vertical piece or thin out the points.")


def resize_writes(current: dict, count: int) -> list[dict]:
    """Single value writes that turn a placed part's path into count nodes on a straight
    line along x, 1 m and more apart, for a Tapir that cannot resize an array.

    A = 1 then aDone = 2 is the parts' own fresh path (a changed A that was not typed): two
    nodes 1 m apart, every helper array zeroed, the add-node grips balanced. The Swept Beam
    also squares its end cuts there. Each extEnd write after it is an add-node grip drag
    that appends one node 1.25 m on (EXT_PARK 0.25 plus the step)."""
    start = float(current.get("extEnd", 0.0))
    return ([{"name": "A", "value": 1.0}, {"name": "aDone", "value": 2.0}]
            + [{"name": "extEnd", "value": start + k * GROW_STEP} for k in range(1, count - 1)])


def parked_xy(xs: list[float], ys: list[float]) -> list[dict]:
    """nodeY, nodeX and nodeY again, in an order the part's node merge never fires on.

    Every item write runs the merge, which drops a node within 1 cm of its neighbour in
    plan. The y values are parked first, 1 m apart and 10 m clear of every real y, then the
    x values go in, then the real y values: at every step consecutive nodes stay at least
    1 m apart, or both are real nodes (check_plan_gaps)."""
    park = max(abs(y) for y in ys) + PARK_CLEARANCE
    return [{"name": "nodeY", "value": [park + i for i in range(len(ys))]},
            {"name": "nodeX", "value": xs}, {"name": "nodeY", "value": ys}]


def write_parameters(conn, guid: str, params: list[dict], part: str) -> None:
    result = conn.tapir("SetGDLParametersOfElements", {"elementsWithGDLParameters": [
        {"elementId": {"guid": guid}, "gdlParameters": params}]})
    first = (result.get("executionResults") or [{}])[0]
    if not first.get("success", False):
        raise PlacedPathError(f"Writing the {part} parameters of element {guid} failed "
                              f"(the element exists): {first.get('error', first)}")
