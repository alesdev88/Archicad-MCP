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

from archicad_mcp.core import swept_path as sp


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


def write_parameters(conn, guid: str, params: list[dict], part: str) -> None:
    result = conn.tapir("SetGDLParametersOfElements", {"elementsWithGDLParameters": [
        {"elementId": {"guid": guid}, "gdlParameters": params}]})
    first = (result.get("executionResults") or [{}])[0]
    if not first.get("success", False):
        raise PlacedPathError(f"Writing the {part} parameters of element {guid} failed "
                              f"(the element exists): {first.get('error', first)}")
