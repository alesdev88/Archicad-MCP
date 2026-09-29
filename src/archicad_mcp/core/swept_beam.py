"""create_swept_beam: place a Swept Beam along a curve (spec Part 3).

The Swept Beam library part (gdl-src/swept-beam) stores its path as node
arrays in its own coordinates. This module reads a source curve from the
model or takes 3D points, fits it into nodes with swept_path, and writes the
nodes into a new or an existing Swept Beam.

Tapir reports z as an absolute elevation (story elevation plus offset) for
morphs, polylines, lines, arcs and placed objects, and CreateObjects derives
the story from an absolute z, so this module works in absolute z throughout.
"""

from __future__ import annotations

import json
import math

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import swept_path as sp

LIBRARY_PART = "Swept Beam"
NEAR_VERTICAL_DEG = 85.0
NOT_LOADED = (f"'{LIBRARY_PART}' is not loaded in this project. Build it with "
              "'archicad-gdl build-source gdl-src/swept-beam --out <GDL workspace>' "
              "into the linked GDL workspace folder, then reload libraries.")


class SweptBeamError(ValueError):
    """The request cannot become a Swept Beam."""


def create_swept_beam(conn: ArchicadConnection, source_guid: str | None = None,
                      points: list[dict] | str | None = None,
                      start_height: float | None = None,
                      slope_percent: float | None = None, profile: dict | str | None = None,
                      offset_u: float | None = None, offset_w: float | None = None,
                      flip: bool | None = None, path_tolerance: float = 0.002,
                      update_guid: str | None = None, dry_run: bool = True) -> dict:
    """Section values left as None take the defaults on a new beam (a 0.2 x 0.2
    rectangle, no offsets, no flip) and keep the beam's own values on an update."""
    try:
        points = _from_json_text(points, "points", list)
        profile = _from_json_text(profile, "profile", dict)
        return _create(conn, source_guid, points, start_height, slope_percent, profile,
                       offset_u, offset_w, flip, path_tolerance, update_guid, dry_run)
    except (sp.PathError, SweptBeamError) as exc:
        return {"error": str(exc)}


def _from_json_text(value, name: str, kind: type):
    """Parse a list or object that arrived as JSON text.

    Claude Code collapses a nullable list or object field to an untyped schema
    and sends the value as text; refusing it would make the tool unusable there.
    """
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SweptBeamError(f"{name} arrived as text that is not valid JSON ({exc.msg}). "
                             f"Pass a {kind.__name__}, or JSON text of one.") from exc
    if not isinstance(parsed, kind):
        raise SweptBeamError(f"{name} must be a JSON {kind.__name__}, "
                             f"got {type(parsed).__name__}.")
    return parsed


def _create(conn, source_guid, points, start_height, slope_percent, profile,
            offset_u, offset_w, flip, path_tolerance, update_guid, dry_run) -> dict:
    if (source_guid is None) == (points is None):
        raise SweptBeamError("Give exactly one of source_guid or points.")
    if path_tolerance <= 0:
        raise SweptBeamError("path_tolerance must be a positive length in metres.")
    warnings: list[str] = []
    if source_guid is not None:
        path, floor_index, flat = _path_from_element(conn, source_guid, path_tolerance)
    else:
        path, floor_index, flat = _path_from_points(points, path_tolerance), None, False
    if flat:
        base = path.nodes[0][2] + (start_height or 0.0)
        path.nodes = sp.apply_slope(path.nodes, path.arcs, base, slope_percent or 0.0)
    elif start_height is not None or slope_percent is not None:
        warnings.append("start_height and slope_percent apply to flat sources only; "
                        "this source keeps its own heights.")
    steepest = path.steepest_slope_deg()
    if steepest > NEAR_VERTICAL_DEG:
        warnings.append(f"A piece rises at {steepest:.1f} degrees. Near vertical, the "
                        "plumb section orientation is undefined.")
    profile_params = _profile_params(conn, profile, offset_u, offset_w, flip,
                                     keep_unset=update_guid is not None)
    loaded = _library_part_loaded(conn)
    if not loaded:
        warnings.append(NOT_LOADED)
    if update_guid is not None:
        origin, angle = _existing_frame(conn, update_guid)
    else:
        origin, angle = path.nodes[0], 0.0
    local = _to_local(path.nodes, origin, angle)
    params = _node_params(local, path.arcs, path_tolerance) + profile_params
    report = {
        "dry_run": dry_run,
        "nodes": len(path.nodes),
        "arcs": path.arc_count,
        "straight": path.straight_count,
        "max_deviation_mm": round(path.max_deviation * 1000, 2),
        "steepest_slope_deg": round(steepest, 1),
        "origin": {"x": origin[0], "y": origin[1], "z": origin[2]},
        "story": floor_index,
        "warnings": warnings,
    }
    if dry_run:
        report["gdl_parameters"] = params
        return report
    if not loaded:
        raise SweptBeamError(NOT_LOADED)
    guid = update_guid or _place(conn, origin, floor_index)
    _write_parameters(conn, guid, params)
    report["element"] = guid
    report["updated" if update_guid else "created"] = True
    return report


def _details(conn, guid: str) -> dict:
    result = conn.tapir("GetDetailsOfElements", {"elements": [{"elementId": {"guid": guid}}]})
    entry = (result.get("detailsOfElements") or [{}])[0]
    if "error" in entry or "type" not in entry:
        raise SweptBeamError(f"Could not read element {guid}: {entry.get('error', entry)}")
    return entry


def _path_from_element(conn, guid: str, tol: float):
    entry = _details(conn, guid)
    kind, d, floor = entry["type"], entry.get("details", {}), entry.get("floorIndex")
    if kind == "Morph":
        return _morph_path(d, tol), floor, False
    if kind == "PolyLine":
        return _polyline_path(d), floor, True
    if kind == "Line":
        z = float(d.get("zCoordinate", 0.0))
        b, e = d["begCoordinate"], d["endCoordinate"]
        return _distinct_path([(b["x"], b["y"], z), (e["x"], e["y"], z)], [0.0, 0.0]), floor, True
    if kind == "Arc":
        return _arc_path(d), floor, True
    raise SweptBeamError(
        f"Element {guid} is a {kind}. The source must be a Morph line, Polyline, Line "
        "or Arc (Splines are not supported: their curve shape is not readable).")


def _morph_path(d: dict, tol: float) -> sp.SweptPath:
    body = d.get("body") or {}
    if body.get("polygons"):
        raise SweptBeamError("This morph has faces. Draw the guide as a morph line (edges only).")
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


def _polyline_path(d: dict) -> sp.SweptPath:
    coords = d.get("coordinates") or []
    if len(coords) < 2:
        raise SweptBeamError("The polyline has fewer than two points.")
    z = float(d.get("zCoordinate", 0.0))
    pts = [(float(c["x"]), float(c["y"]), z) for c in coords]
    if len(pts) > 2 and math.dist(pts[0][:2], pts[-1][:2]) < sp.DUPLICATE_EPS:
        raise SweptBeamError("The polyline is closed. A Swept Beam needs a start and an end.")
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
        raise SweptBeamError("Elliptical arcs are not supported.")
    z = float(d.get("zCoordinate", 0.0))
    ox, oy, r = d["origin"]["x"], d["origin"]["y"], float(d["radius"])
    beg, end = float(d["begAngle"]), float(d["endAngle"])
    sweep = (end - beg) % (2 * math.pi)
    if sweep < 1e-9:
        raise SweptBeamError("The arc has no length.")
    p1 = (ox + r * math.cos(beg), oy + r * math.sin(beg), z)
    p2 = (ox + r * math.cos(end), oy + r * math.sin(end), z)
    return sp.SweptPath([p1, p2], [math.degrees(sweep), 0.0])


def _path_from_points(points, tol: float) -> sp.SweptPath:
    try:
        pts = [(float(p["x"]), float(p["y"]), float(p["z"])) for p in points]
    except (KeyError, TypeError, ValueError) as exc:
        raise SweptBeamError("points must be a list of {x, y, z} numbers in metres.") from exc
    return sp.fit_points(pts, tol)


def _attribute_index(conn, kind: str, name: str) -> int:
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
    raise SweptBeamError(f"No {kind} attribute named '{name}'.{hint}")


def _profile_params(conn, profile, offset_u, offset_w, flip, keep_unset=False) -> list[dict]:
    """Section parameters. With keep_unset (rewriting a placed beam), a value the
    call leaves as None is not written, so the beam keeps its own."""
    common = []
    for name, value, default, conv in (("profileOffsetU", offset_u, 0.0, float),
                                       ("profileOffsetW", offset_w, 0.0, float),
                                       ("flipProfile", flip, False, bool)):
        if value is None:
            if keep_unset:
                continue
            value = default
        common.append({"name": name, "value": conv(value)})
    if not profile:
        if keep_unset:
            return common
        profile = {"rectangle": {"width": 0.2, "height": 0.2}}
    if "attribute" in profile:
        index = _attribute_index(conn, "Profile", str(profile["attribute"]))
        return [{"name": "profileMode", "value": "Profile attribute"},
                {"name": "beamProfile", "value": index}] + common
    if "rectangle" in profile:
        rect = profile["rectangle"] or {}
        w, h = float(rect.get("width", 0.2)), float(rect.get("height", 0.2))
        if w <= 0 or h <= 0:
            raise SweptBeamError("Rectangle width and height must be positive.")
        out = [{"name": "profileMode", "value": "Rectangle"},
               {"name": "rectW", "value": w}, {"name": "rectH", "value": h}]
        if rect.get("building_material"):
            out.append({"name": "rectBMat", "value": _attribute_index(
                conn, "BuildingMaterial", str(rect["building_material"]))})
        return out + common
    raise SweptBeamError('profile must be {"attribute": "<name>"} or '
                         '{"rectangle": {"width": w, "height": h, "building_material": "<name>"}}.')


def _library_part_loaded(conn) -> bool:
    parts = conn.tapir("GetAvailableLibraryParts",
                       {"filterByTypeId": "Object"}).get("libraryParts", [])
    want = LIBRARY_PART.casefold()
    return any(str(p.get("documentName", "")).casefold() == want
               or str(p.get("fileName", "")).casefold() == want + ".gsm" for p in parts)


def _existing_frame(conn, guid: str):
    entry = _details(conn, guid)
    d = entry.get("details", {})
    name = str((d.get("libPart") or {}).get("name", ""))
    if entry["type"] != "Object" or name.casefold() != LIBRARY_PART.casefold():
        raise SweptBeamError(f"Element {guid} is not a Swept Beam. update_guid must "
                             "point at a placed Swept Beam.")
    o = d["origin"]
    return (float(o["x"]), float(o["y"]), float(o["z"])), float(d.get("angle", 0.0))


def _to_local(nodes, origin, angle: float) -> list[sp.Point]:
    c, s = math.cos(-angle), math.sin(-angle)
    out = []
    for x, y, z in nodes:
        dx, dy = x - origin[0], y - origin[1]
        out.append((dx * c - dy * s, dx * s + dy * c, z - origin[2]))
    return out


def _node_params(local, arcs, tol: float) -> list[dict]:
    """Node arrays and grip helpers for SetGDLParametersOfElements.

    Angle arrays go in radians (single angles would go in degrees; gate A).
    Every grip helper and its marker are written equal, and A equal to aDone,
    so the Parameter Script reads no pending grip drag or length edit (gate C).
    """
    n = len(local)

    def r6(values):
        return [round(v, 6) + 0.0 for v in values]

    length = round(sum(sp.segment_plan_length(local[i], local[i + 1], arcs[i])
                       for i in range(n - 1)), 6)
    return [
        {"name": "nodeX", "value": r6(p[0] for p in local)},
        {"name": "nodeY", "value": r6(p[1] for p in local)},
        {"name": "nodeZ", "value": r6(p[2] for p in local)},
        {"name": "nodeRoll", "value": [0.0] * n},
        {"name": "segArc", "value": [round(math.radians(a), 9) + 0.0 for a in arcs]},
        {"name": "segSag", "value": [0.0] * n},
        {"name": "sagDone", "value": [0.0] * n},
        {"name": "insL", "value": [0.0] * n},
        {"name": "insDone", "value": [0.0] * n},
        {"name": "extStart", "value": 0.0},
        {"name": "extStartDone", "value": 0.0},
        {"name": "extEnd", "value": 0.0},
        {"name": "extEndDone", "value": 0.0},
        {"name": "pathTolerance", "value": tol},
        {"name": "A", "value": length},
        {"name": "aDone", "value": length},
    ]


def _place(conn, origin, floor_index) -> str:
    item = {"libraryPartName": LIBRARY_PART,
            "coordinates": {"x": origin[0], "y": origin[1], "z": origin[2]}}
    if floor_index is not None:
        item["floorIndex"] = floor_index
    result = conn.tapir("CreateObjects", {"objectsData": [item]})
    elements = result.get("elements") or []
    if not elements or "elementId" not in elements[0]:
        raise SweptBeamError(f"CreateObjects did not place '{LIBRARY_PART}': {result}")
    return elements[0]["elementId"]["guid"]


def _write_parameters(conn, guid: str, params: list[dict]) -> None:
    result = conn.tapir("SetGDLParametersOfElements", {"elementsWithGDLParameters": [
        {"elementId": {"guid": guid}, "gdlParameters": params}]})
    first = (result.get("executionResults") or [{}])[0]
    if not first.get("success", False):
        raise SweptBeamError(f"Writing the Swept Beam parameters of element {guid} failed "
                             f"(the element exists): {first.get('error', first)}")
