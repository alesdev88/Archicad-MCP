"""A library part's 2D symbol: its extents, its drawing, and the reads behind them.

GetLibraryPart2DPrimitives (Tapir's main after 1.8.0, read from its source and not tried
live yet) gives the lines, arcs and polygons of a part's symbol in the part's own
coordinates, at its default parameters or those of a placed instance. It says which way
neither an arc runs nor whether `reflected` applies, so an arc is taken as running
counter-clockwise from begAngle to endAngle, and a mirrored sample is not compared.
"""
from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw, ImageFont

CELL = 400
COLUMNS = 4
LINE = (0, 0, 0)
FILL = (215, 215, 215)
CROSS = (200, 0, 0)
NUMBER = (0, 0, 160)

_BACKGROUND = (255, 255, 255)
_MARGIN = 30                 # pixels between a cell's edge and what it draws
_CROSS_ARM = 8               # pixels from the cross's centre to the end of an arm
_NUMBER_AT = (6, 6)
_NUMBER_SIZE = 22
_ARC_STEP = math.radians(5)  # the most an arc turns between two points drawn

Point = tuple[float, float]


def _point(value: object) -> Point | None:
    if not isinstance(value, dict):
        return None
    x, y = value.get("x"), value.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return float(x), float(y)
    return None


def _points(values: object) -> list[Point]:
    """The points of an outline; none when any of them is not a point."""
    if not isinstance(values, list):
        return []
    points = [_point(v) for v in values]
    return [] if None in points else points


def _arc(primitive: dict) -> tuple[Point, float, float, float] | None:
    """(origin, radius, begin, sweep); the sweep is counter-clockwise, 2 pi for a whole arc."""
    origin = _point(primitive.get("origin"))
    radius = primitive.get("radius")
    if origin is None or not isinstance(radius, (int, float)):
        return None
    if primitive.get("isWhole"):
        return origin, float(radius), 0.0, math.tau
    begin, end = primitive.get("begAngle"), primitive.get("endAngle")
    if not isinstance(begin, (int, float)) or not isinstance(end, (int, float)):
        return None
    return origin, float(radius), float(begin), (end - begin) % math.tau


def _at(origin: Point, radius: float, angle: float) -> Point:
    return origin[0] + radius * math.cos(angle), origin[1] + radius * math.sin(angle)


def _arc_points(origin: Point, radius: float, begin: float, sweep: float) -> list[Point]:
    """The ends of an arc and each axis extreme it passes."""
    points = [_at(origin, radius, begin), _at(origin, radius, begin + sweep)]
    for quarter in range(4):
        angle = quarter * math.pi / 2
        if (angle - begin) % math.tau <= sweep:
            points.append(_at(origin, radius, angle))
    return points


def symbol_extents(primitives: list[dict]) -> tuple[float, float, float, float] | None:
    """(xMin, yMin, xMax, yMax) of the lines, arcs and polygons; None when there are none.
    A primitive missing what it needs is skipped. A polygon's holes lie inside its outline."""
    points: list[Point] = []
    for primitive in primitives:
        kind = primitive.get("type") if isinstance(primitive, dict) else None
        if kind == "Line":
            ends = [_point(primitive.get("begCoordinate")), _point(primitive.get("endCoordinate"))]
            if None not in ends:
                points += ends
        elif kind == "Polygon":
            points += _points(primitive.get("coordinates"))
        elif kind == "Arc":
            arc = _arc(primitive)
            if arc is not None:
                points += _arc_points(*arc)
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _projection(extents: tuple | None, left: int, top: int):
    """Plan coordinates to pixels of the cell at (left, top), y up, the origin always in view."""
    x_min, y_min, x_max, y_max = extents or (0.0, 0.0, 0.0, 0.0)
    x_min, x_max, y_min, y_max = min(x_min, 0.0), max(x_max, 0.0), min(y_min, 0.0), max(y_max, 0.0)
    room = CELL - 2 * _MARGIN
    span = max(x_max - x_min, y_max - y_min)
    scale = room / span if span > 0 else 1.0
    centre_x, centre_y = (x_min + x_max) / 2, (y_min + y_max) / 2

    def project(point: Point) -> tuple[float, float]:
        return (left + CELL / 2 + (point[0] - centre_x) * scale,
                top + CELL / 2 - (point[1] - centre_y) * scale)
    return project


def _draw_primitive(draw: ImageDraw.ImageDraw, primitive: dict, project) -> None:
    kind = primitive.get("type") if isinstance(primitive, dict) else None
    if kind == "Line":
        ends = [_point(primitive.get("begCoordinate")), _point(primitive.get("endCoordinate"))]
        if None not in ends:
            draw.line([project(p) for p in ends], fill=LINE)
    elif kind == "Polygon":
        outline = [project(p) for p in _points(primitive.get("coordinates"))]
        if len(outline) >= 3 and primitive.get("isFill"):
            draw.polygon(outline, fill=FILL)
        if len(outline) >= 2:
            draw.line([*outline, outline[0]], fill=LINE)
    elif kind == "Arc":
        arc = _arc(primitive)
        if arc is not None:
            origin, radius, begin, sweep = arc
            steps = max(1, math.ceil(sweep / _ARC_STEP))
            points = [_at(origin, radius, begin + sweep * i / steps) for i in range(steps + 1)]
            draw.line([project(p) for p in points], fill=LINE)


def _number_font():
    try:
        return ImageFont.load_default(size=_NUMBER_SIZE)
    except TypeError:        # Pillow before 10.1 has one size of default font
        return ImageFont.load_default()


def draw_symbols(symbols: list[list[dict]]) -> bytes:
    """A PNG with one cell per symbol, in rows of COLUMNS, each at its own scale, numbered
    from 1, with a cross at the insertion point. Names stay out of it, so no font has to
    carry them."""
    rows = math.ceil(len(symbols) / COLUMNS)
    sheet = Image.new("RGB", (min(len(symbols), COLUMNS) * CELL, rows * CELL), _BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    draw.fontmode = "1"      # no smoothing, so the number is one colour
    font = _number_font()
    for index, primitives in enumerate(symbols):
        left, top = (index % COLUMNS) * CELL, (index // COLUMNS) * CELL
        project = _projection(symbol_extents(primitives), left, top)
        # Fills first, so a later line is not painted over.
        for primitive in sorted(primitives, key=lambda p: not (isinstance(p, dict)
                                                               and p.get("isFill"))):
            _draw_primitive(draw, primitive, project)
        x, y = project((0.0, 0.0))
        draw.line([(x - _CROSS_ARM, y), (x + _CROSS_ARM, y)], fill=CROSS)
        draw.line([(x, y - _CROSS_ARM), (x, y + _CROSS_ARM)], fill=CROSS)
        draw.text((left + _NUMBER_AT[0], top + _NUMBER_AT[1]), str(index + 1), fill=NUMBER,
                  font=font)
    out = io.BytesIO()
    sheet.save(out, format="PNG")
    return out.getvalue()
