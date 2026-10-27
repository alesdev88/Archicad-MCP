"""create_road_marking: place a Road Marking along a curve and drape it onto Meshes and
Slabs (docs/superpowers/specs/2026-10-12-road-marking-tool-design.md).

The Road Marking library part (Archicad GDL repo, Library/Site/Road Marking) keeps its
path as node arrays in its own coordinates, like the Swept Beam, with a cross fall in
percent per node. Its presets live in its Parameter Script; road_marking_presets.json
holds the same table, exported from the part's model by road_marking/export_presets.py
in the GDL repo, whose tests check that the two agree. This module works out the
marking's settings (the current or default values, the preset, the call's changes), how
far the marking reaches across its path, and what the part would draw past the path's
ends.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from archicad_mcp.core import drape as dr
from archicad_mcp.core import placed_path as pp
from archicad_mcp.core import swept_path as sp

LIBRARY_PART = "Road Marking"
CUSTOM = "Custom"
TABLE = json.loads(Path(__file__).with_name("road_marking_presets.json").read_text(encoding="utf-8"))
PRESETS: dict[str, dict] = TABLE["presets"]
VALUES = TABLE["values"]
STRIPE_KEYS = {"on": "On", "offset": "Offset", "width": "Width", "dash": "Dash", "gap": "Gap",
               "angle": "Angle", "shape": "Shape"}
BAY_KEYS = {"layout": "bayLayout", "width": "bayWidth", "depth": "bayDepth", "zone_width": "zoneWidth",
            "zone_first": "zoneFirst", "bar_width": "barWidth", "symbol": "showSymbol"}
LETTER_KEYS = {"text": "letterText", "length": "letterLength", "width": "letterWidth", "gap": "letterGap",
               "offset": "letterOffset", "align": "letterAlign", "shift": "letterShift",
               "repeat": "letterRepeat"}
# A setting's kind is its name, or a stripe setting's suffix (s1Width is a Width)
BOOLEANS = {"On", "zoneFirst", "showSymbol", "flipSides", "wholeDashes"}
NUMBERS = {"Offset", "Width", "Dash", "Gap", "Angle", "bayWidth", "bayDepth", "zoneWidth", "barWidth",
           "letterLength", "letterWidth", "letterGap", "letterOffset", "letterShift", "letterRepeat",
           "patternShift", "thickness"}
# (minimum, maximum) as the part's Parameter Script ranges them; the part clamps silently, the tool refuses
RANGES = {"Width": (0.01, None), "Dash": (0.01, None), "Gap": (0.0, None), "Angle": (-80.0, 80.0),
          "bayWidth": (0.3, None), "bayDepth": (0.3, None), "zoneWidth": (0.1, None),
          "barWidth": (0.01, None), "letterLength": (0.1, None), "letterWidth": (0.05, None),
          "letterGap": (0.0, None), "letterRepeat": (0.0, None), "thickness": (0.0005, 0.05)}
# The single Angle parameters; the tool keeps them in degrees, the part reads them in radians
ANGLES = tuple(f"s{k}Angle" for k in (1, 2, 3))
ENUMS = {"Shape": VALUES["shapes"], "patternAlign": VALUES["aligns"], "bayLayout": VALUES["layouts"],
         "letterText": VALUES["letter_texts"], "letterAlign": VALUES["letter_aligns"]}


class RoadMarkingError(pp.PlacedPathError):
    """The request cannot become a Road Marking."""


def _kind(name: str) -> str:
    return name[2:] if len(name) > 2 and name[0] == "s" and name[1] in "123" else name


def preset_name(marking) -> str:
    """The part's own spelling of a preset name, matched without regard to case."""
    want = str(marking).strip().casefold()
    for name in [*PRESETS, CUSTOM]:
        if name.casefold() == want:
            return name
    raise RoadMarkingError(f"Unknown marking {marking!r}. Valid: {', '.join([*PRESETS, CUSTOM])}.")


def check_value(name: str, value):
    """The value the part takes for this setting, or RoadMarkingError."""
    kind = _kind(name)
    if kind in BOOLEANS:
        if not isinstance(value, bool):
            raise RoadMarkingError(f"{name} must be true or false, got {value!r}.")
        return value
    if kind in NUMBERS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RoadMarkingError(f"{name} must be a number, got {value!r}.")
        value = float(value)
        lo, hi = RANGES.get(kind, (None, None))
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            span = f"at least {lo:g}" if hi is None else f"{lo:g} to {hi:g}"
            raise RoadMarkingError(f"{name} must be {span}, got {value:g}.")
        return value
    if kind in ENUMS and value not in ENUMS[kind]:
        raise RoadMarkingError(f"{name} must be one of: {', '.join(ENUMS[kind])}; got {value!r}.")
    return value


def resolve(current: dict, marking=None, stripes=None, bays=None, lettering=None, flat=None):
    """The marking's settings after the call (want), and the part's settings right after
    markingType is written (state): the current values, then the preset when the call picks
    one the marking does not already have (it replaces what it sets, as in the dialog),
    then the call's values."""
    state = dict(current)
    if marking is not None:
        name = preset_name(marking)
        if name != current["markingType"] and name in PRESETS:
            state.update(PRESETS[name])
        state["markingType"] = name
    want = dict(state)
    for key, change in (stripes or {}).items():
        if str(key) not in ("1", "2", "3"):
            raise RoadMarkingError(f"stripes keys are '1', '2' and '3', got {key!r}.")
        if not isinstance(change, dict):
            raise RoadMarkingError(f"stripes['{key}'] must be an object such as {{\"width\": 0.12}}.")
        for k, v in change.items():
            if k not in STRIPE_KEYS:
                raise RoadMarkingError(f"Unknown stripe key {k!r}. Valid: {', '.join(STRIPE_KEYS)}.")
            want[f"s{key}{STRIPE_KEYS[k]}"] = check_value(f"s{key}{STRIPE_KEYS[k]}", v)
    for group, keys, given in (("bays", BAY_KEYS, bays), ("lettering", LETTER_KEYS, lettering)):
        for k, v in (given or {}).items():
            if k not in keys:
                raise RoadMarkingError(f"Unknown {group} key {k!r}. Valid: {', '.join(keys)}.")
            want[keys[k]] = check_value(keys[k], v)
    for name, v in (flat or {}).items():
        if v is not None:
            want[name] = check_value(name, v)
    return want, state


def reach(p: dict) -> tuple[float, float]:
    """How far the marking reaches across its path, (u_lo, u_hi), positive to the right,
    after Flip sides: the switched on stripes, the bay layout and the word, and always 0
    (the part model's layout_reach and lettering_reach)."""
    flip = -1.0 if p["flipSides"] else 1.0
    lo = hi = 0.0
    for k in (1, 2, 3):
        if p[f"s{k}On"]:
            c, w = flip * float(p[f"s{k}Offset"]), float(p[f"s{k}Width"])
            lo, hi = min(lo, c - w / 2), max(hi, c + w / 2)
    if p["bayLayout"] != "None":
        d = flip * (float(p["bayDepth"]) + TABLE["line_width"] / 2)
        lo, hi = min(lo, d), max(hi, d)
    if p["letterText"] != "None":
        scale = float(p["letterWidth"]) / TABLE["letter_base_width"]
        width = (sum(TABLE["letter_widths"][ch] * scale for ch in p["letterText"])
                 + float(p["letterGap"]) * (len(p["letterText"]) - 1))
        centre = flip * float(p["letterOffset"])
        lo, hi = min(lo, centre - width / 2), max(hi, centre + width / 2)
    return lo, hi


def _start(align: str, length: float, size: float, shift: float) -> float:
    if align == "End":
        return length - shift - size
    if align == "Centre":
        return (length - size) / 2 + shift
    return shift


def overhang(p: dict, length: float) -> list[str]:
    """Warnings for a bay row or the placed word reaching past the path's ends, where the
    part draws it straight on but the drape does not reach."""
    out = []

    def note(what, size, start):
        before, after = -start, start + size - length
        if before > 0.001 or after > 0.001:
            out.append(f"{what} is {size:.2f} m long and reaches {max(before, 0.0):.2f} m before the start and "
                       f"{max(after, 0.0):.2f} m past the end of the {length:.2f} m path: the part draws it "
                       "there, but it is not draped.")

    layout = p["bayLayout"]
    if layout not in ("None", "Stop end bars"):
        bay, zone = float(p["bayWidth"]), float(p["zoneWidth"])
        cells = {"Disabled bays, shared zone": [bay, zone, bay], "Taxi bay": [bay],
                 "EV charging bay": [bay]}.get(layout, [zone, bay])
        row = sum(cells)
        note("The bay row", row, _start(p["patternAlign"], length, row, float(p["patternShift"])))
    if p["letterText"] != "None":
        size = float(p["letterLength"])
        note(f"The word {p['letterText']}", size, _start(p["letterAlign"], length, size, float(p["letterShift"])))
    return out


def no_effect(p: dict, bays, lettering) -> list[str]:
    out = []
    if p["bayLayout"] == "None" and any(k != "layout" for k in (bays or {})):
        out.append("The bays values have no effect while the bay layout is None.")
    if p["letterText"] == "None" and any(k != "text" for k in (lettering or {})):
        out.append("The lettering values have no effect while the word is None.")
    return out


# ---------------------------------------------------------------- the tool

NOT_LOADED = (f"'{LIBRARY_PART}' is not loaded in this project. Load the Road Marking library part "
              "(Library/Site/Road Marking in the office GDL library) and reload libraries.")


def create_road_marking(conn, source_guid=None, points=None, start_height=None, slope_percent=None,
                        marking=None, stripes=None, bays=None, lettering=None, flip_sides=None,
                        pattern_align=None, pattern_shift=None, whole_dashes=None, thickness=None,
                        paint_surface=None, drape_on=None, lift=0.0, drape_tolerance=0.005,
                        path_tolerance=0.002, update_guid=None, dry_run=True) -> dict:
    """Values left as None take the part's defaults on a new marking and keep the marking's
    own on an update; a preset replaces what it sets. Offsets, bays and the word lie to the
    right looking from the first point to the last."""
    try:
        given = {name: pp.from_json_text(value, name, kind) for name, value, kind in (
            ("points", points, list), ("stripes", stripes, dict), ("bays", bays, dict),
            ("lettering", lettering, dict), ("drape_on", drape_on, list))}
        flat = {"flipSides": flip_sides, "patternAlign": pattern_align, "patternShift": pattern_shift,
                "wholeDashes": whole_dashes, "thickness": thickness}
        return _create(conn, source_guid, given, flat, start_height, slope_percent, marking, paint_surface,
                       lift, drape_tolerance, path_tolerance, update_guid, dry_run)
    except sp.PathError as exc:
        return {"error": str(exc).replace("Swept Beam", LIBRARY_PART)}
    except (pp.PlacedPathError, dr.DrapeError) as exc:
        return {"error": str(exc)}


def _create(conn, source_guid, given, flat, start_height, slope_percent, marking, paint_surface, lift,
            drape_tolerance, path_tolerance, update_guid, dry_run) -> dict:
    points, drape_on = given["points"], given["drape_on"]
    own_path = bool(update_guid and drape_on and source_guid is None and points is None)
    if not own_path and (source_guid is None) == (points is None):
        raise RoadMarkingError("Give exactly one of source_guid or points (an update with drape_on may give "
                               "neither: it drapes the marking's own path).")
    if path_tolerance <= 0 or drape_tolerance <= 0:
        raise RoadMarkingError("path_tolerance and drape_tolerance must be positive lengths in metres.")
    warnings: list[str] = []
    frame = pp.existing_frame(conn, update_guid, LIBRARY_PART) if update_guid else None
    current = read_parameters(conn, update_guid) if update_guid else dict(TABLE["defaults"])
    if paint_surface is not None:
        flat["paintSurface"] = _surface_index(conn, paint_surface)
    want, state = resolve(current, marking, given["stripes"], given["bays"], given["lettering"], flat)
    warnings += no_effect(want, given["bays"], given["lettering"])
    u_lo, u_hi = reach(want)
    path, floor_index, flat_source = _path(conn, source_guid, points, own_path, current, frame, path_tolerance)
    nodes, arcs, falls = path.nodes, path.arcs, [0.0] * len(path.nodes)
    drape_report = {"nodes_added_for_drape": 0, "max_along_deviation_mm": 0.0, "max_across_misfit_mm": 0.0,
                    "off_surface": [], "partly_off_surface": []}
    if drape_on:
        d = dr.drape(nodes, arcs, (u_lo, u_hi), dr.read_surfaces(conn, drape_on), tol=drape_tolerance,
                     lift=float(lift), path_tol=path_tolerance)
        nodes, arcs, falls = d.nodes, d.arcs, d.falls
        warnings += d.warnings
        if not flat_source and not own_path:
            warnings.append("Draping replaces the source's own heights.")
        if start_height is not None or slope_percent is not None:
            warnings.append("start_height and slope_percent do not apply to a draped marking.")
        drape_report = {"nodes_added_for_drape": d.added, "max_along_deviation_mm": round(d.max_along * 1000, 2),
                        "max_across_misfit_mm": round(d.max_across * 1000, 2), "off_surface": d.off_surface,
                        "partly_off_surface": d.partly_off_surface}
    elif flat_source:
        nodes = sp.apply_slope(nodes, arcs, nodes[0][2] + (start_height or 0.0), slope_percent or 0.0)
    elif start_height is not None or slope_percent is not None:
        warnings.append("start_height and slope_percent apply to flat sources only; this source keeps its own "
                        "heights.")
    warnings += overhang(want, dr.PlanPath(nodes, arcs, path_tolerance).length)
    loaded = pp.library_part_loaded(conn, LIBRARY_PART)
    if not loaded:
        warnings.append(NOT_LOADED)
    origin, angle = frame if frame else (nodes[0], 0.0)
    local = pp.to_local(nodes, origin, angle)
    items = pp.writes_items(conn)
    radians = pp.angles_in_radians(conn)
    if items:
        pp.check_plan_gaps(local, LIBRARY_PART)
    same_count = bool(update_guid) and len(nodes) == len(current["nodeX"]) and not items
    params = node_params(local, arcs, falls, path_tolerance, y_first=same_count, items=items,
                         radians=radians)
    if want["markingType"] != current["markingType"]:
        params.append({"name": "markingType", "value": want["markingType"]})
    params += [{"name": k, "value": _sent(k, want[k], radians)}
               for k in TABLE["settings"] if not _same(want[k], state[k])]
    report = {"dry_run": dry_run, "marking": want["markingType"], "nodes": len(nodes),
              "arcs": sum(1 for a in arcs[:-1] if abs(a) > sp.ARC_EPS_DEG), **drape_report,
              "max_deviation_mm": round(path.max_deviation * 1000, 2),
              "steepest_cross_fall_percent": round(max(abs(f) for f in falls), 2),
              "reach": {"u_lo": round(u_lo, 3), "u_hi": round(u_hi, 3)},
              "origin": {"x": origin[0], "y": origin[1], "z": origin[2]}, "story": floor_index,
              "array_writes": "item by item" if items else "whole arrays", "warnings": warnings}
    if dry_run:
        report["gdl_parameters"] = params
        return report
    if not loaded:
        raise RoadMarkingError(NOT_LOADED)
    guid = update_guid or pp.place(conn, LIBRARY_PART, origin, floor_index)
    if items:
        # no resizing: reset the path, grow it to the node count, then write every item
        placed = current if update_guid else pp.gdl_parameters(conn, guid)
        params = pp.resize_writes(placed, len(nodes)) + params
    if same_count:
        # the same number of nodes: nodeX as 0, 1, 2 first, then nodeY before the real nodeX, so the
        # part's node merge never compares a new x with an old y (parent spec, "Through the API")
        pp.write_parameters(conn, guid, [{"name": "nodeX", "value": [float(i) for i in range(len(nodes))]}],
                            LIBRARY_PART)
    pp.write_parameters(conn, guid, params, LIBRARY_PART)
    report["element"] = guid
    report["updated" if update_guid else "created"] = True
    report["marking_after"] = read_parameters(conn, guid)["markingType"]
    return report


def _path(conn, source_guid, points, own_path, current, frame, tol):
    """The path in absolute coordinates, the source's story, and whether the source is flat."""
    if own_path:
        (ox, oy, oz), angle = frame
        c, s = math.cos(angle), math.sin(angle)
        nodes = [(ox + x * c - y * s, oy + x * s + y * c, oz + z)
                 for x, y, z in zip(current["nodeX"], current["nodeY"], current["nodeZ"])]
        return sp.SweptPath(nodes, [math.degrees(a) for a in current["segArc"]][:len(nodes)]), None, False
    if source_guid is not None:
        return pp.path_from_element(conn, source_guid, tol, LIBRARY_PART)
    return pp.path_from_points(points, tol), None, False


def read_parameters(conn, guid: str) -> dict:
    """A placed marking's parameters by name, single angles in degrees (the API reads them
    back in radians)."""
    values = pp.gdl_parameters(conn, guid)
    for name in ANGLES:
        values[name] = math.degrees(float(values[name]))
    return values


def _sent(name: str, value, radians: bool):
    """A setting as SetGDLParametersOfElements takes it: the stripe angles are kept in
    degrees and go in radians to Tapir 1.7.3 and newer."""
    return math.radians(value) if radians and name in ANGLES else value


def _surface_index(conn, value) -> int:
    if isinstance(value, bool):
        raise RoadMarkingError("paint_surface must be a Surface attribute name or index.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    if isinstance(value, str):
        return pp.attribute_index(conn, "Surface", value)
    raise RoadMarkingError("paint_surface must be a Surface attribute name or index.")


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9
    return a == b


def node_params(local, arcs, falls, tol, y_first=False, items=False, radians=False) -> list[dict]:
    """The node arrays and grip helpers in write order, every grip helper equal to its
    marker so the Parameter Script reads no pending drag.

    Whole arrays (Tapir up to 1.7.1): A and aDone at 1 m first (the part returns A to 1 m),
    then nodeX so it sets the row count (nodeY first after a same count rewrite has written
    nodeX as 0, 1, 2), the arc angles in radians.

    Item writes (Tapir 1.7.2 and newer) follow pp.resize_writes, which sets A and aDone and
    leaves the add-node grips balanced, so neither pair is written here (a lone extEnd write
    would be a drag); x and y go in the order pp.parked_xy gives, the arc angles in degrees
    (in radians from Tapir 1.7.3)."""
    n = len(local)

    def r6(values):
        return [round(v, 6) + 0.0 for v in values]

    xs, ys = r6(p[0] for p in local), r6(p[1] for p in local)
    arc_values = [round(a if items and not radians else math.radians(a), 9) + 0.0 for a in arcs]
    if items:
        head, xy = [], pp.parked_xy(xs, ys)
        ext = []
    else:
        head = [{"name": "A", "value": 1.0}, {"name": "aDone", "value": 1.0}]
        xy = [{"name": "nodeX", "value": xs}, {"name": "nodeY", "value": ys}]
        xy = xy[::-1] if y_first else xy
        ext =[{"name": "extStart", "value": 0.0}, {"name": "extStartDone", "value": 0.0},
               {"name": "extEnd", "value": 0.0}, {"name": "extEndDone", "value": 0.0}]
    return (head + xy
            + [{"name": "nodeZ", "value": r6(p[2] for p in local)},
               {"name": "nodeFall", "value": r6(falls)},
               {"name": "segArc", "value": arc_values},
               {"name": "segSag", "value": [0.0] * n}, {"name": "sagDone", "value": [0.0] * n},
               {"name": "insL", "value": [0.0] * n}, {"name": "insDone", "value": [0.0] * n}]
            + ext + [{"name": "pathTolerance", "value": tol}])
