"""create_road_arrow: place the Road Arrow library part on a lane axis
(docs/superpowers/specs/2026-11-05-road-arrow-design.md in the Archicad GDL repo).

road_arrow_table.json is exported from the part's model by road_arrow/export_table.py in the GDL
repo, whose tests check that the two agree. This module works out an arrow's size (the call's, the
speed's, or the part's own Size fix), its length, width and reach, and where an arrow of a given
length sits on a lane axis.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from pathlib import Path
from typing import NamedTuple

from archicad_mcp.core import drape as dr
from archicad_mcp.core import placed_path as pp

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
    if isinstance(value, bool) or not isinstance(value, (int, float)):
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
        keep = current if current in sizes else next(iter(sizes))
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
    d = ((x1 - x0) / norm, (y1 - y0) / norm)
    ax, ay = axis_point(path, station, offset)
    tail = (ax - a * length * d[0], ay - a * length * d[1])
    tip = (tail[0] + length * d[0], tail[1] + length * d[1])
    past = s_tail < -STATION_TOL or s_tip > path.length + STATION_TOL
    return Placement(tail, tip, math.atan2(d[1], d[0]), past)
