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

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.element_data import error_fields
from archicad_mcp.core.element_kinds import send_items
from archicad_mcp.core.part_records import COMBOS, PartRecord

# Sizes closer than this count as the same size, so a fit does not divide by noise.
_SAME = 1e-6

_HEAD_DIRECTIONS = {"+x": (1.0, 0.0), "-x": (-1.0, 0.0), "+y": (0.0, 1.0), "-y": (0.0, -1.0)}


@dataclass(frozen=True)
class Sample:
    """One placement of a part and the box it was seen to cover."""
    a: float
    b: float
    box: tuple[float, float, float, float]      # xMin, yMin, xMax, yMax from the insertion point


def fit_footprint(samples: list[Sample]) -> dict:
    """A footprint in the record's form: each edge as [k, c], edge = k * size + c.

    The x edges are fitted against a, the y edges against b. When the sizes do not
    differ (a part that clamps a size, or has a fixed one) the edge is fixed: k = 0.
    """
    if not samples:
        raise ValueError("fitting a footprint takes at least one sample")
    return {
        "xMin": _fit([s.a for s in samples], [s.box[0] for s in samples]),
        "xMax": _fit([s.a for s in samples], [s.box[2] for s in samples]),
        "yMin": _fit([s.b for s in samples], [s.box[1] for s in samples]),
        "yMax": _fit([s.b for s in samples], [s.box[3] for s in samples]),
    }


def footprint_at(footprint: dict, a: float, b: float) -> tuple[float, float, float, float]:
    """The box (xMin, yMin, xMax, yMax) the footprint covers at sizes a and b."""
    def edge(name: str, size: float) -> float:
        k, c = footprint[name]
        return k * size + c
    return edge("xMin", a), edge("yMin", b), edge("xMax", a), edge("yMax", b)


def object_fields(record: PartRecord, center: dict, length: float, width: float, height: float,
                  head: dict, mirrored: bool) -> dict:
    """Tapir's fields for an object whose footprint is centred on `center` with its head
    pointing along `head` (a direction vector with x and y)."""
    if record.footprint is None or record.head not in _HEAD_DIRECTIONS:
        raise ValueError(f"'{record.name}' has no footprint and head direction recorded")
    if math.hypot(head["x"], head["y"]) < _SAME:
        raise ValueError("the head direction has no length")

    along_y = record.head in ("+y", "-y")
    a, b = (width, length) if along_y else (length, width)

    x_min, y_min, x_max, y_max = footprint_at(record.footprint, a, b)
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
    """Least squares line edge = k * size + c; a fixed edge when the sizes do not differ."""
    count = len(sizes)
    mean_size, mean_edge = sum(sizes) / count, sum(edges) / count
    if max(sizes) - min(sizes) < _SAME:
        return [0.0, _tidy(mean_edge)]
    k = (sum((s - mean_size) * (e - mean_edge) for s, e in zip(sizes, edges))
         / sum((s - mean_size) ** 2 for s in sizes))
    return [_tidy(k), _tidy(mean_edge - k * mean_size)]


def _tidy(value: float) -> float:
    """Rounded to a micrometre, so a record holds 0.5 and not 0.49999999999999994."""
    return round(value, 6) + 0.0                 # + 0.0 turns -0.0 into 0.0
