"""Where a learned library part goes, as the fields Tapir takes.

A sketch says "a bed here, head this way" or "a door in this wall, hinged there,
opening that way". The record says how the part behaves: its footprint and head
direction in its own axes, or which hinge and swing each flag combination gives.
This module joins the two: the insertion point, angle, mirroring and size of an
object, the `reflected` and `oSide` flags of a door or window.

Pure arithmetic, apart from write_leaf_state, the one Archicad call: the leaves
of a door or window are set after it is placed. Plan coordinates are metres,
angles radians counter-clockwise about the insertion point.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import NamedTuple

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.element_data import error_fields
from archicad_mcp.core.element_kinds import send_items
from archicad_mcp.core.part_records import COMBOS, PartRecord

# Sizes closer than this count as the same size, so a fit does not divide by noise.
_SAME = 1e-6

# How far an axis's extent slope (how fast the plan box grows with the size) may stray
# from 1 for a fit to be taken: a part whose box grows at another rate does not draw the
# size it is given on that axis.
SLOPE_TOLERANCE = 0.05

# Metres the footprint at the size asked may differ from it before placement warns.
SIZE_TOLERANCE = 0.03

# Hinge points closer than this to the opening's centre along the wall, and swing points
# closer than this to the wall's line, name neither jamb or side.
AMBIGUOUS = 0.05

_HEAD_DIRECTIONS = {"+x": (1.0, 0.0), "-x": (-1.0, 0.0), "+y": (0.0, 1.0), "-y": (0.0, -1.0)}

# Per axis: its edges, their places in a box, and the size (A or B) they follow.
_AXES = {"x": (("xMin", 0), ("xMax", 2), 0), "y": (("yMin", 1), ("yMax", 3), 1)}


@dataclass(frozen=True)
class Sample:
    """One placement of a part and the box it was seen to cover."""
    a: float                                    # the A and B it took (its GDL read back)
    b: float
    box: tuple[float, float, float, float]      # xMin, yMin, xMax, yMax from the insertion point
    sent: tuple[float, float] | None = None     # the A and B sent, when not a and b

    def size(self, axis: str, against: str) -> float:
        """The A (axis "x") or B (axis "y") taken, or sent when `against` is "sent"."""
        sizes = self.sent if against == "sent" and self.sent is not None else (self.a, self.b)
        return sizes[_AXES[axis][2]]


class AxisFit(NamedTuple):
    """One axis of a footprint, and what its edges were fitted against: "fixed" (the
    box does not change), "taken" (the size read back), "sent", or None for no fit."""
    edges: dict | None
    against: str | None


def fit_axis(samples: list[Sample], axis: str) -> AxisFit:
    """The two edges of one axis ("x" by A, "y" by B), each as [k, c], edge = k * size + c.

    The edges are fitted against the size the part took and against the size sent,
    wherever that size varies across the samples, and the first fit whose box grows as
    fast as the size (extent slope within SLOPE_TOLERANCE of 1) is taken, the size taken
    first: Bed Double 01 clamps B, so its box follows the B read back; Chair 01's GDL
    forces A = B, so its box width follows the A sent. A box that does not change keeps
    fixed edges (k = 0). When no fit grows at that rate, the part's plan size does not
    follow its size on this axis, and there is no fit.
    """
    if not samples:
        raise ValueError("fitting a footprint takes at least one sample")
    (low, low_at), (high, high_at), _ = _AXES[axis]
    lows, highs = [s.box[low_at] for s in samples], [s.box[high_at] for s in samples]
    if _spread(lows) < _SAME and _spread(highs) < _SAME:
        return AxisFit({low: [0.0, _tidy(_mean(lows))], high: [0.0, _tidy(_mean(highs))]},
                       "fixed")
    for against in ("taken", "sent"):
        sizes = [s.size(axis, against) for s in samples]
        if _spread(sizes) < _SAME:
            continue
        low_fit, high_fit = _fit(sizes, lows), _fit(sizes, highs)
        if abs(high_fit[0] - low_fit[0] - 1) <= SLOPE_TOLERANCE:
            return AxisFit({low: low_fit, high: high_fit}, against)
    return AxisFit(None, None)


def fit_footprint(samples: list[Sample]) -> dict | None:
    """A footprint in the record's form (fit_axis for each axis), or None when the
    plan box on an axis follows neither size."""
    x, y = fit_axis(samples, "x"), fit_axis(samples, "y")
    if x.edges is None or y.edges is None:
        return None
    return {**x.edges, **y.edges}


def footprint_at(footprint: dict, a: float, b: float) -> tuple[float, float, float, float]:
    """The box (xMin, yMin, xMax, yMax) the footprint covers at sizes a and b."""
    def edge(name: str, size: float) -> float:
        k, c = footprint[name]
        return k * size + c
    return edge("xMin", a), edge("yMin", b), edge("xMax", a), edge("yMax", b)


def object_fields(record: PartRecord, center: dict, length: float, width: float, height: float,
                  head: dict, mirrored: bool, warnings: list[str] | None = None) -> dict:
    """Tapir's fields for an object whose footprint is centred on `center` with its head
    pointing along `head` (a direction vector with x and y).

    A footprint that covers nothing at this size is refused (ValueError). One that covers
    more than SIZE_TOLERANCE off the length or width asked adds a warning to `warnings`.
    """
    if record.footprint is None or record.head not in _HEAD_DIRECTIONS:
        raise ValueError(f"'{record.name}' has no footprint and head direction recorded")
    if math.hypot(head["x"], head["y"]) < _SAME:
        raise ValueError("the head direction has no length")

    along_y = record.head in ("+y", "-y")
    a, b = (width, length) if along_y else (length, width)

    x_min, y_min, x_max, y_max = footprint_at(record.footprint, a, b)
    across_x, across_y = x_max - x_min, y_max - y_min
    if across_x <= 0 or across_y <= 0:
        raise ValueError(f"the footprint recorded for '{record.name}' covers "
                         f"{max(across_x, 0.0):.2f} x {max(across_y, 0.0):.2f} m at A {a:g}, "
                         f"B {b:g}, so it cannot be placed by placement at this size: place "
                         "it by coordinates, angle, reflected and dimensions, or learn it again")
    drawn_length, drawn_width = (across_y, across_x) if along_y else (across_x, across_y)
    if warnings is not None and (abs(drawn_length - length) > SIZE_TOLERANCE
                                 or abs(drawn_width - width) > SIZE_TOLERANCE):
        warnings.append(f"covers {drawn_length:.2f} m along its head and {drawn_width:.2f} m "
                        f"across by the footprint recorded for '{record.name}', not the "
                        f"{length:g} x {width:g} m asked: the part does not draw the size it "
                        "is sent at, so look with capture_window after placing")
    head_x, head_y = _HEAD_DIRECTIONS[record.head]
    if mirrored:                                 # reflected: local x becomes -x
        x_min, x_max = -x_max, -x_min
        head_x = -head_x

    angle = (math.atan2(head["y"], head["x"]) - math.atan2(head_y, head_x)) % math.tau
    if math.tau - angle < 1e-12:
        angle = 0.0
    cos, sin = math.cos(angle), math.sin(angle)

    local_x, local_y = (x_min + x_max) / 2, (y_min + y_max) / 2
    return {
        "coordinates": {"x": center["x"] - (local_x * cos - local_y * sin),
                        "y": center["y"] - (local_x * sin + local_y * cos),
                        "z": center.get("z", 0.0)},
        "angle": angle,
        "reflected": mirrored,
        "dimensions": {"x": a, "y": b, "z": height},
    }


def opening_fields(record: PartRecord, wall_beg: dict, wall_end: dict, center_offset: float,
                   hinge_near: dict, opens_toward: dict) -> dict:
    """The `reflected` and `oSide` flags that give a door or window the hinge and swing seen.

    Hinge and swing are read along the wall from its begin point to its end: the hinge
    is "begin" when it lies before the opening's centre (`center_offset` along the
    wall), else "end"; the swing is "left" when `opens_toward` lies to the left of the
    wall, else "right".
    """
    if not record.combos:
        raise ValueError(f"'{record.name}' has no hinge and swing combinations recorded")

    dx, dy = wall_end["x"] - wall_beg["x"], wall_end["y"] - wall_beg["y"]
    length = math.hypot(dx, dy)
    if length < _SAME:
        raise ValueError("the wall has no length")
    dx, dy = dx / length, dy / length

    along = (hinge_near["x"] - wall_beg["x"]) * dx + (hinge_near["y"] - wall_beg["y"]) * dy
    hinge = "begin" if along < center_offset else "end"
    side = dx * (opens_toward["y"] - wall_beg["y"]) - dy * (opens_toward["x"] - wall_beg["x"])
    swing = "left" if side > 0 else "right"

    for combo in COMBOS:
        if record.combos.get(combo) == [hinge, swing]:
            return {"reflected": combo[0] == "t", "oSide": combo[1] == "t"}
    raise ValueError(f"'{record.name}' has no recorded combination with the hinge at the "
                     f"{hinge} of the wall and the swing to the {swing}")


def write_leaf_state(conn: ArchicadConnection, guid: str, parameters: dict) -> str | None:
    """Set which leaves of a placed door or window open: one SetGDLParametersOfElements
    call with a leaf state's values. Archicad's message when it refuses them or the call
    fails, else None.

    CreateDoors and CreateWindows take no GDL parameters on Tapir 1.7.2, so this runs
    after the create, and a refusal leaves the element as placed.
    """
    entry = {"elementId": {"guid": guid},
             "gdlParameters": [{"name": name, "value": value}
                               for name, value in parameters.items()]}
    sent = send_items(conn.tapir, "SetGDLParametersOfElements", "elementsWithGDLParameters",
                      [entry], 1)
    if sent.stopped is not None:
        return error_fields(sent.stopped)["message"]
    outcome = sent.outcomes[0]
    if isinstance(outcome, dict):
        return outcome.get("message") or "Archicad refused the values without a reason"
    return None


def _fit(sizes: list[float], edges: list[float]) -> list[float]:
    """Least squares line edge = k * size + c, over sizes that differ."""
    mean_size, mean_edge = _mean(sizes), _mean(edges)
    k = (sum((s - mean_size) * (e - mean_edge) for s, e in zip(sizes, edges))
         / sum((s - mean_size) ** 2 for s in sizes))
    return [_tidy(k), _tidy(mean_edge - k * mean_size)]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _spread(values: list[float]) -> float:
    return max(values) - min(values)


def _tidy(value: float) -> float:
    """Rounded to a micrometre, so a record holds 0.5 and not 0.49999999999999994."""
    return round(value, 6) + 0.0                 # + 0.0 turns -0.0 into 0.0
