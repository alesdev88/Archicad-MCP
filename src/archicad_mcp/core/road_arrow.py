"""create_road_arrow: place the Road Arrow library part on a lane axis
(docs/superpowers/specs/2026-11-05-road-arrow-design.md in the Archicad GDL repo).

road_arrow_table.json is exported from the part's model by road_arrow/export_table.py in the GDL
repo, whose tests check that the two agree. This module works out an arrow's size (the call's, the
speed's, or the part's own Size fix), its length, width and reach, and where an arrow of a given
length sits on a lane axis. create_road_arrow places the arrows and drapes them.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from pathlib import Path
from typing import NamedTuple

from multiconn_archicad.errors import ArchicadAPIError

from archicad_mcp.connection import ArchicadUnavailableError
from archicad_mcp.core import drape as dr
from archicad_mcp.core import placed_path as pp
from archicad_mcp.core import swept_path as sp

LIBRARY_PART = "Road Arrow"
CUSTOM = "Custom"
MAX_NODES = 40
STATION_TOL = 0.001  # metres a station may lie off the axis and still count as on it
TABLE = json.loads(Path(__file__).with_name("road_arrow_table.json").read_text(encoding="utf-8"))
ANCHORS = {"tail": 0.0, "centre": 0.5, "tip": 1.0}


class RoadArrowError(pp.PlacedPathError):
    """The request cannot become a Road Arrow."""


class Placement(NamedTuple):
    tail: tuple
    tip: tuple
    angle: float  # radians
    past_end: bool


def arrow_name(arrow) -> str:
    """The part's own spelling of an arrow name, matched without regard to case."""
    want = str(arrow).strip().casefold()
    for name in TABLE["arrows"]:
        if name.casefold() == want:
            return name
    raise RoadArrowError(f"Unknown arrow {arrow!r}. Valid: {', '.join(TABLE['arrows'])}.")


def _number(argument: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RoadArrowError(f"{argument} must be a number, got {value!r}.")
    return float(value)


def _check_range(argument: str, value, key: str) -> float:
    value = _number(argument, value)
    lo, hi = TABLE["ranges"][key]
    if not lo <= value <= hi:
        raise RoadArrowError(f"{argument} must be {lo:g} to {hi:g} metres, got {value:g}.")
    return value


def _size_length(size: str) -> float:
    return float(size.split()[0])


def _asked_length(text: str) -> float:
    """The number at the start of a size text, 0 when there is none (as the part reads it)."""
    if not text[:1].isdigit():
        return 0.0
    try:
        return float(text.split()[0])
    except ValueError:
        return 0.0


def _nearest(rows: dict, length: float) -> str:
    """The size nearest in length, the longer on a tie, the wider of two with the same length."""
    return min(rows, key=lambda s: (abs(rows[s]["length"] - length), -rows[s]["length"], -rows[s]["width"]))


def pick_size(name, current, size=None, speed_kmh=None, custom_length=None, custom_width=None):
    """The size name for an arrow and the call's warnings.

    The call's size, else the speed's, else a custom size means Custom, else the current size
    stands when the arrow offers it (or it is Custom), else the part's Size fix."""
    arrow = TABLE["arrows"][name]
    sizes = arrow["sizes"]
    custom = custom_length is not None or custom_width is not None
    if size is not None and speed_kmh is not None:
        raise RoadArrowError("Give size or speed_kmh, not both.")
    if custom and speed_kmh is not None:
        raise RoadArrowError("A custom size (custom_length, custom_width) cannot come with speed_kmh.")
    if custom and size is not None and str(size).strip().casefold() != CUSTOM.casefold():
        raise RoadArrowError(f"custom_length and custom_width mean size 'Custom'; size is {size!r}.")
    if custom_length is not None:
        _check_range("custom_length", custom_length, "customLength")
    if custom_width is not None:
        _check_range("custom_width", custom_width, "customWidth")
    if speed_kmh is not None:
        speed = _number("speed_kmh", speed_kmh)
        if speed <= 0:
            raise RoadArrowError(f"speed_kmh must be a positive number, got {speed_kmh!r}.")
    if size is not None:
        want = str(size).strip().casefold()
        for s in [*sizes, CUSTOM]:
            if s.casefold() == want:
                return s, []
        raise RoadArrowError(f"size {size!r} is not offered by {name}. Sizes: {', '.join([*sizes, CUSTOM])}.")
    if custom:
        return CUSTOM, []
    if speed_kmh is not None:
        if arrow["by_speed"]:
            return next(s for limit, s in TABLE["by_speed"] if limit is None or speed <= limit), []
        keep = current if current in sizes or current == CUSTOM else _nearest(sizes, _asked_length(current))
        return keep, [f"{name} has a fixed length: speed_kmh does not apply, it stays {keep}."]
    if current in sizes or current == CUSTOM:
        return current, []
    return _nearest(sizes, _asked_length(current)), []


def dimensions(name, size, custom_length, custom_width) -> dict:
    """length, width and the reach (u_lo, u_hi, positive to the right) of an arrow's size.

    Custom scales the drawn size nearest in length to the asked length (the part's default custom
    length when none is asked); an unasked length is that size's own."""
    rows = TABLE["arrows"][name]["sizes"]
    if size != CUSTOM:
        row = rows[size]
        return {k: row[k] for k in ("length", "width", "u_lo", "u_hi")}
    drawn = {s: r for s, r in rows.items() if r["drawn"]}
    asked = float(custom_length) if custom_length is not None else TABLE["defaults"]["customLength"]
    base = drawn[_nearest(drawn, asked)]
    width = float(custom_width) if custom_width is not None else base["width"]
    scale = width / base["width"]
    return {"length": float(custom_length) if custom_length is not None else base["length"], "width": width,
            "u_lo": base["u_lo"] * scale, "u_hi": base["u_hi"] * scale}


def axis_point(path: dr.PlanPath, s: float, u: float) -> tuple[float, float]:
    """The plan point at station s and u to the right: as path.xy, and straight on along the end
    piece past either end."""
    end = path.length
    if 0.0 <= s <= end:
        return path.xy(s, u)
    i, j, edge = (0, 1, 0.0) if s < 0 else (len(path.x) - 1, len(path.x) - 2, end)
    x, y = path.xy(edge, u)
    dx, dy = path.x[i] - path.x[j], path.y[i] - path.y[j]
    if s < 0:
        dx, dy = -dx, -dy
    norm = math.hypot(dx, dy)
    past = s - edge
    return x + past * dx / norm, y + past * dy / norm


def axis_height(nodes, node_s, s: float) -> float:
    """The axis height at station s: linear between the nodes, and past an end it runs on with the
    end segment's rise."""
    i = min(max(bisect_right(node_s, s) - 1, 0), len(nodes) - 2)
    run = node_s[i + 1] - node_s[i]
    rise = (nodes[i + 1][2] - nodes[i][2]) / run if run > 0 else 0.0
    return nodes[i][2] + rise * (s - node_s[i])


def place_on_axis(path: dr.PlanPath, station: float, anchor: str, length: float, offset: float) -> Placement:
    """The arrow's tail and tip, with the anchor on the station.

    The points on the axis at the tail's and the tip's stations, each offset to the right, give
    the direction, so both ends lie on the lane on a curve."""
    if anchor not in ANCHORS:
        raise RoadArrowError(f"anchor must be one of: {', '.join(ANCHORS)}; got {anchor!r}.")
    a = ANCHORS[anchor]
    s_tail, s_tip = station - a * length, station + (1 - a) * length
    (x0, y0), (x1, y1) = axis_point(path, s_tail, offset), axis_point(path, s_tip, offset)
    norm = math.hypot(x1 - x0, y1 - y0)
    if norm < STATION_TOL:
        raise RoadArrowError(f"At station {station:g} the axis folds back on itself over the arrow's length "
                             f"({length:g} m): its tail and tip land on the same point, so it has no direction.")
    d = ((x1 - x0) / norm, (y1 - y0) / norm)
    ax, ay = axis_point(path, station, offset)
    tail = (ax - a * length * d[0], ay - a * length * d[1])
    tip = (tail[0] + length * d[0], tail[1] + length * d[1])
    past = s_tail < -STATION_TOL or s_tip > path.length + STATION_TOL
    return Placement(tail, tip, math.atan2(d[1], d[0]), past)


# ---------------------------------------------------------------- the tool

NOT_LOADED = (f"'{LIBRARY_PART}' is not loaded in this project. Load the Road Arrow library part "
              f"(Library/Site/{LIBRARY_PART} in the office GDL library) and reload libraries.")
MOVE = "Move a placed arrow with move_elements, or place a new one."
FAILURES = (pp.PlacedPathError, dr.DrapeError, ArchicadAPIError, ArchicadUnavailableError)


def _arrow_text(exc) -> str:
    """Text from drape.py and the path code, in this part's words."""
    return str(exc).replace("a marking", "an arrow").replace("marking", "arrow").replace("Swept Beam", LIBRARY_PART)


def create_road_arrow(conn, source_guid=None, points=None, stations=None, anchor="tail", offset=0.0, arrow=None,
                      size=None, speed_kmh=None, custom_length=None, custom_width=None, height=None,
                      slope_percent=None, cross_fall=None, drape_on=None, lift=0.0, drape_tolerance=0.005,
                      path_tolerance=0.002, thickness=None, paint_surface=None, update_guid=None,
                      dry_run=True) -> dict:
    """Place Road Arrows at stations along a lane axis (a source element or points), each
    with its anchor on the station and offset metres to the right, or rewrite one placed arrow.
    Values left as None take the part's defaults on a new arrow and keep the arrow's own on
    an update. With drape_on the arrow gets height points that follow the surfaces."""
    try:
        asked = {"source_guid": source_guid, "points": pp.from_json_text(points, "points", list),
                 "stations": stations, "anchor": anchor, "offset": offset, "height": height}
        drape_list = pp.from_json_text(drape_on, "drape_on", list)
        return _create(conn, asked, drape_list, dict(
            arrow=arrow, size=size, speed_kmh=speed_kmh, custom_length=custom_length, custom_width=custom_width,
            slope_percent=slope_percent, cross_fall=cross_fall, lift=lift, drape_tolerance=drape_tolerance,
            path_tolerance=path_tolerance, thickness=thickness, paint_surface=paint_surface,
            update_guid=update_guid, dry_run=dry_run))
    except (sp.PathError, *FAILURES) as exc:
        return {"error": _arrow_text(exc)}


def parse_stations(value) -> list[float]:
    """The stations as numbers: None is the start, a number or a list, or JSON text of either."""
    if value is None:
        return [0.0]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RoadArrowError(f"stations arrived as text that is not valid JSON ({exc.msg}). Pass a number, "
                                 "a list of numbers, or JSON text of one.") from exc
    if not isinstance(value, list):
        value = [value]
    if not value:
        raise RoadArrowError("stations lists no stations. Give one or more distances along the axis in metres.")
    return [_number("station", v) for v in value]


def _same(a, b) -> bool:
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        return (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b)
                and all(_same(x, y) for x, y in zip(a, b)))
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9
    return a == b


def _padded(values) -> list[float]:
    return [round(v, 6) + 0.0 for v in values] + [0.0] * (MAX_NODES - len(values))


def _check_update(asked: dict) -> None:
    for name, default in (("source_guid", None), ("points", None), ("stations", None), ("anchor", "tail"),
                          ("offset", 0.0), ("height", None)):
        if asked[name] != default:
            raise RoadArrowError(f"{name} cannot be given with update_guid: the arrow stays where it stands. {MOVE}")


def _settings(conn, o: dict, current):
    """The arrow's name and size, its dimensions, and the warnings the choice brings."""
    update = current is not None
    held = current if update else TABLE["defaults"]
    name = arrow_name(o["arrow"] if o["arrow"] is not None else held["arrowType"])
    size, warnings = pick_size(name, held["arrowSize"], o["size"], o["speed_kmh"], o["custom_length"],
                               o["custom_width"])
    keep = update and current["arrowSize"] == CUSTOM
    dims = dimensions(name, size, o["custom_length"] if o["custom_length"] is not None else
                      (current["customLength"] if keep else None),
                      o["custom_width"] if o["custom_width"] is not None else
                      (current["customWidth"] if keep else None))
    return name, size, dims, warnings


def _fit_drape(tail, tip, angle, reach, surfaces, o, frame_origin=None):
    """The draped arrow: its origin, the height point arrays in local coordinates, the drape and its warnings."""
    d = dr.drape([tail, tip], [0.0, 0.0], reach, surfaces, tol=o["drape_tolerance"], lift=o["lift"],
                 path_tol=o["path_tolerance"], max_nodes=MAX_NODES)
    origin = frame_origin or d.nodes[0]
    local = pp.to_local(d.nodes, origin, angle)
    return d, origin, local


def _entry(station, tail, angle, d=None, **fields) -> dict:
    out = {"station": station, "tail": {k: round(v, 6) + 0.0 for k, v in zip("xyz", tail)},
           "direction_degrees": round(math.degrees(angle), 6) + 0.0, "nodes": 0, "nodes_added_for_drape": 0,
           "max_along_deviation_mm": 0.0, "max_across_misfit_mm": 0.0, "steepest_cross_fall_percent": 0.0,
           "off_surface": [], "partly_off_surface": [], "warnings": []}
    if d is not None:
        out.update(nodes=len(d.nodes), nodes_added_for_drape=d.added,
                   max_along_deviation_mm=round(d.max_along * 1000, 2),
                   max_across_misfit_mm=round(d.max_across * 1000, 2),
                   steepest_cross_fall_percent=round(max(abs(f) for f in d.falls), 2),
                   off_surface=d.off_surface, partly_off_surface=d.partly_off_surface)
        out["warnings"] = [_arrow_text(w) for w in d.warnings]
        for key, what in (("off_surface", "has no surface under it"), ("partly_off_surface", "is partly off the surface")):
            if getattr(d, key):
                spans = ", ".join(f"{s['from']:g} to {s['to']:g} m" for s in getattr(d, key))
                out["warnings"].append(f"The arrow {what} between stations {spans} along its length: it is placed "
                                       "there at the nearest height the drape found.")
    out.update(fields)
    return out


def _node_params(local) -> list[dict]:
    return [{"name": "nodeCount", "value": len(local)}, {"name": "nodeX", "value": _padded([p[0] for p in local])},
            {"name": "nodeZ", "value": _padded([p[2] for p in local])}]


def _create(conn, asked, drape_on, o) -> dict:
    update_guid, dry_run = o["update_guid"], o["dry_run"]
    if update_guid:
        _check_update(asked)
    elif (asked["source_guid"] is None) == (asked["points"] is None):
        raise RoadArrowError("Give exactly one of source_guid or points (update_guid takes neither).")
    if asked["anchor"] not in ANCHORS:
        raise RoadArrowError(f"anchor must be one of: {', '.join(ANCHORS)}; got {asked['anchor']!r}.")
    offset = _number("offset", asked["offset"])
    o["lift"] = _number("lift", o["lift"])
    if o["path_tolerance"] <= 0 or o["drape_tolerance"] <= 0:
        raise RoadArrowError("path_tolerance and drape_tolerance must be positive lengths in metres.")
    stations = [] if update_guid else parse_stations(asked["stations"])
    if o["thickness"] is not None:
        o["thickness"] = _check_range("thickness", o["thickness"], "thickness")
    extras = {}
    if o["paint_surface"] is not None:
        extras["paintSurface"] = pp.surface_index(conn, o["paint_surface"])
    for name in ("height", "slope_percent", "cross_fall"):
        value = asked["height"] if name == "height" else o[name]
        if value is not None:
            _number(name, value)

    frame = current = None
    if update_guid:
        frame = pp.existing_frame(conn, update_guid, LIBRARY_PART)
        if pp.details(conn, update_guid)["details"].get("reflected"):
            raise RoadArrowError(f"Element {update_guid} is mirrored. Its height points would be written against "
                                 "the wrong side; place the arrow anew instead.")
        current = pp.gdl_parameters(conn, update_guid)
    name, size, dims, warnings = _settings(conn, o, current)
    length = dims["length"]
    reach = (dims["u_lo"], dims["u_hi"])
    surfaces = dr.read_surfaces(conn, drape_on) if drape_on is not None else None
    if surfaces is not None and any(v is not None for v in (asked["height"], o["slope_percent"], o["cross_fall"])):
        warnings.append("height, slope_percent and cross_fall do not apply to a draped arrow.")

    floor = None
    arrows = []     # (origin, angle, entry, parameters)
    if update_guid:
        origin, angle = frame
        tail = origin
        tip = (origin[0] + length * math.cos(angle), origin[1] + length * math.sin(angle), origin[2])
        entry_params = _update_params(name, size, dims, current, o, extras)
        entry = _entry(None, tail, angle)
        if surfaces is not None:
            d, _, local = _fit_drape(tail, tip, angle, reach, surfaces, o, frame_origin=origin)
            entry = _entry(None, d.nodes[0], angle, d)
            entry_params += [p for p in _node_params(local) + [{"name": "nodeFall", "value": _padded(d.falls)},
                                                              {"name": "slopePercent", "value": 0.0},
                                                              {"name": "crossFall", "value": 0.0}]
                             if not _same(p["value"], current[p["name"]])]
        elif current["nodeCount"] > 0 and not _same(length, _held_length(current)):
            warnings.append("The length changed, but the arrow keeps its height points, fitted for the old "
                            "length: give drape_on to fit them again.")
        entry_params = _in_write_order(entry_params)
        arrows.append((origin, angle, entry, entry_params))
    else:
        path, floor, _ = (pp.path_from_element(conn, asked["source_guid"], o["path_tolerance"], LIBRARY_PART)
                          if asked["source_guid"] is not None else (pp.path_from_points(asked["points"], o["path_tolerance"]), None, False))
        plan = dr.PlanPath(path.nodes, path.arcs, o["path_tolerance"])
        a = ANCHORS[asked["anchor"]]
        for station in stations:
            if not -STATION_TOL <= station <= plan.length + STATION_TOL:
                raise RoadArrowError(f"station {station:g} lies off the axis, which runs from 0 to "
                                     f"{plan.length:.3f} m.")
            arrows.append(_fit_new(path, plan, station, a, length, offset, asked, o, reach, surfaces,
                                   name, size, dims, extras))
        ordered = sorted(stations)
        if any(b - a_ < length - 1e-9 for a_, b in zip(ordered, ordered[1:])):
            warnings.append(f"Some arrows overlap: stations closer together than the arrow's length ({length:g} m).")
        for station, (_, _, entry, _) in zip(stations, arrows):
            if entry.pop("past_end"):
                warnings.append(f"At station {station:g} the arrow runs on straight past the axis's end, where a "
                                "drape still fits it but the axis does not guide it.")
    loaded = pp.library_part_loaded(conn, LIBRARY_PART)
    if not loaded:
        warnings.append(NOT_LOADED)
    report = {"dry_run": dry_run, "arrow": name, "size": size, "length": length, "width": dims["width"],
              "story": floor, "warnings": warnings, "arrows": [e for _, _, e, _ in arrows]}
    if dry_run:
        for _, _, entry, params in arrows:
            entry["gdl_parameters"] = params
        return report
    if not loaded:
        raise RoadArrowError(NOT_LOADED)
    placed = []
    try:
        for origin, angle, entry, params in arrows:
            guid = update_guid or pp.place(conn, LIBRARY_PART, origin, floor, angle)
            if not update_guid:
                placed.append(guid)
            entry["element"] = guid
            if params:
                pp.write_parameters(conn, guid, params, LIBRARY_PART)
            entry["updated" if update_guid else "created"] = True
    except FAILURES as exc:
        if not placed:
            raise
        report["error"] = _arrow_text(exc)
        report["elements_placed"] = placed
    return report


def _held_length(current) -> float:
    """The length of the arrow the part holds, or -1 when its settings are not in the table."""
    try:
        custom = current["arrowSize"] == CUSTOM
        return dimensions(arrow_name(current["arrowType"]), current["arrowSize"],
                          current["customLength"] if custom else None,
                          current["customWidth"] if custom else None)["length"]
    except (RoadArrowError, KeyError):
        return -1.0


ORDER = ["arrowType", "arrowSize", "customLength", "customWidth", "nodeCount", "nodeX", "nodeZ", "nodeFall",
         "slopePercent", "crossFall", "thickness", "paintSurface"]


def _in_write_order(params) -> list[dict]:
    return sorted(params, key=lambda p: ORDER.index(p["name"]))


def _head(name, size, dims) -> list[dict]:
    """The settings every write may carry, in write order, without the heights."""
    out = [{"name": "arrowType", "value": name}, {"name": "arrowSize", "value": size}]
    if size == CUSTOM:
        out += [{"name": "customLength", "value": round(dims["length"], 6) + 0.0},
                {"name": "customWidth", "value": round(dims["width"], 6) + 0.0}]
    return out


def _tail_params(o, extras) -> list[dict]:
    out = []
    if o["thickness"] is not None:
        out.append({"name": "thickness", "value": o["thickness"]})
    if "paintSurface" in extras:
        out.append({"name": "paintSurface", "value": extras["paintSurface"]})
    return out


def _update_params(name, size, dims, current, o, extras) -> list[dict]:
    """What an update writes: the settings that differ from the part's, and the values given."""
    params = _head(name, size, dims) + _tail_params(o, extras)
    if o["slope_percent"] is not None:
        params.append({"name": "slopePercent", "value": float(o["slope_percent"])})
    if o["cross_fall"] is not None:
        params.append({"name": "crossFall", "value": float(o["cross_fall"])})
    return [p for p in params if not _same(p["value"], current.get(p["name"]))]


def _fit_new(path, plan, station, a, length, offset, asked, o, reach, surfaces, name, size, dims, extras):
    pl = place_on_axis(plan, station, asked["anchor"], length, offset)
    s_tail = station - a * length
    z_tail = axis_height(path.nodes, plan.node_s, s_tail)
    z_tip = axis_height(path.nodes, plan.node_s, s_tail + length)
    tail, tip = (*pl.tail, z_tail), (*pl.tip, z_tip)
    params = _head(name, size, dims)
    if surfaces is not None:
        d, origin, local = _fit_drape(tail, tip, pl.angle, reach, surfaces, o)
        entry = _entry(station, origin, pl.angle, d, past_end=pl.past_end)
        params += _node_params(local) + [{"name": "nodeFall", "value": _padded(d.falls)},
                                         {"name": "slopePercent", "value": 0.0}, {"name": "crossFall", "value": 0.0}]
    else:
        lift = float(asked["height"] or 0.0)
        origin = (tail[0], tail[1], z_tail + lift)
        slope = (z_tip - z_tail) / length * 100 + float(o["slope_percent"] or 0.0)
        fall = float(o["cross_fall"] or 0.0)
        entry = _entry(station, origin, pl.angle, past_end=pl.past_end, steepest_cross_fall_percent=round(abs(fall), 2))
        params += [{"name": "nodeCount", "value": 0}, {"name": "nodeX", "value": _padded([])},
                   {"name": "nodeZ", "value": _padded([])}, {"name": "nodeFall", "value": _padded([])},
                   {"name": "slopePercent", "value": round(slope, 6) + 0.0}, {"name": "crossFall", "value": fall}]
    return origin, pl.angle, entry, _in_write_order(params + _tail_params(o, extras))
