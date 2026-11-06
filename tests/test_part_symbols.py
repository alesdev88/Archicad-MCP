"""A library part's 2D symbol: its extents, its drawing, and the reads behind them.

Values are worked by hand. Shapes as Tapir's main after 1.8.0 answers them, read from its
source: GetLibraryPart2DPrimitives gives lines, arcs and polygons in the part's coordinates.
"""
from __future__ import annotations

import io
import math

import pytest
from multiconn_archicad.errors import TapirCommandError
from PIL import Image

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.part_symbols import (
    CELL,
    CROSS,
    FILL,
    LINE,
    NUMBER,
    default_parameters,
    draw_symbols,
    own_size_of,
    symbol_extents,
    symbol_of,
)
from tests.conftest import FakeCore
from tests.fixtures import api_replays

R = math.sqrt(0.5)
PI = math.pi


def line(x0, y0, x1, y1):
    return {"type": "Line", "begCoordinate": {"x": x0, "y": y0},
            "endCoordinate": {"x": x1, "y": y1}}


def arc(beg, end, origin=(0.0, 0.0), radius=1.0, whole=False):
    primitive = {"type": "Arc", "origin": {"x": origin[0], "y": origin[1]}, "radius": radius,
                 "begAngle": beg, "endAngle": end}
    if whole:
        primitive["isWhole"] = True
    return primitive


def polygon(points, holes=(), fill=False):
    primitive = {"type": "Polygon", "coordinates": [{"x": x, "y": y} for x, y in points],
                 "holes": [{"coordinates": [{"x": x, "y": y} for x, y in hole]}
                           for hole in holes]}
    if fill:
        primitive["isFill"] = True
    return primitive


@pytest.mark.parametrize("primitives, expected", [
    ([line(0, 1, 2, -1)], (0, -1, 2, 1)),
    ([polygon([(0, 0), (2, 0), (2, 1)], holes=[[(0.5, 0.1), (1, 0.1), (1, 0.2)]])], (0, 0, 2, 1)),
    ([arc(0, 0, origin=(1, 1), radius=0.5, whole=True)], (0.5, 0.5, 1.5, 1.5)),
    ([arc(0, PI / 2)], (0, 0, 1, 1)),
    ([arc(PI / 4, 3 * PI / 4)], (-R, R, R, 1)),
    ([arc(3 * PI / 4, 5 * PI / 4)], (-1, -R, -R, R)),
    ([arc(5 * PI / 4, 7 * PI / 4)], (-R, -1, R, -R)),
    ([arc(7 * PI / 4, PI / 4)], (R, -R, 1, R)),
    ([arc(-PI / 4, PI / 4)], (R, -R, 1, R)),
    ([arc(3 * PI / 2, 5 * PI / 2)], (0, -1, 1, 1)),
    ([arc(0.1, 0.2)], (math.cos(0.2), math.sin(0.1), math.cos(0.1), math.sin(0.2))),
    ([line(0, 1, 2, -1), arc(0, 0, origin=(5, 0), radius=1, whole=True)], (0, -1, 6, 1)),
])
def test_symbol_extents(primitives, expected):
    assert symbol_extents(primitives) == pytest.approx(expected)


def test_nothing_has_no_extents():
    assert symbol_extents([]) is None


@pytest.mark.parametrize("primitive", [
    {"type": "Line", "begCoordinate": {"x": 0, "y": 0}},
    {"type": "Polygon"},
    {"type": "Polygon", "coordinates": []},
    {"type": "Arc", "radius": 1.0, "begAngle": 0.0, "endAngle": 1.0},
    {"type": "Arc", "origin": {"x": 0, "y": 0}, "radius": 1.0},
    {"type": "Text"},
])
def test_a_primitive_missing_its_fields_is_skipped(primitive):
    assert symbol_extents([primitive]) is None
    assert symbol_extents([primitive, line(0, 0, 1, 1)]) == pytest.approx((0, 0, 1, 1))


def _image(symbols) -> Image.Image:
    data = draw_symbols(symbols)
    assert data.startswith(b"\x89PNG")
    return Image.open(io.BytesIO(data)).convert("RGB")


def _rows_of(image: Image.Image, colour, box=None) -> list[int]:
    region = image.crop(box) if box else image
    width, height = region.size
    pixels = region.load()
    return [y for y in range(height) for x in range(width) if pixels[x, y] == colour]


@pytest.mark.parametrize("count, size", [(1, (CELL, CELL)), (4, (4 * CELL, CELL)),
                                         (5, (4 * CELL, 2 * CELL))])
def test_the_sheet_has_one_cell_per_symbol_in_rows_of_four(count, size):
    assert _image([[line(0, 0, 1, 1)]] * count).size == size


def test_a_symbol_is_drawn_black_with_the_cross_red_and_y_up():
    image = _image([[line(0, 1, 1, 1)]])
    black, red = _rows_of(image, LINE), _rows_of(image, CROSS)
    assert black and red
    # Image rows grow downward, so a line above the insertion point has the smaller rows.
    assert sum(black) / len(black) < sum(red) / len(red)


def test_the_cross_shows_when_the_insertion_point_is_outside_the_symbol():
    assert _rows_of(_image([[line(5, 5, 6, 6)]]), CROSS)


@pytest.mark.parametrize("primitives", [[line(0, 0, 0, 2)], [line(1, 1, 1, 1)], []])
def test_a_symbol_with_no_width_or_height_is_drawn(primitives):
    assert _image([primitives]).size == (CELL, CELL)


def test_each_cell_shows_its_number():
    image = _image([[line(0, 0, 1, 1)], [line(0, 0, 1, 1)]])
    assert _rows_of(image, NUMBER, box=(0, 0, CELL // 4, CELL // 4))
    assert _rows_of(image, NUMBER, box=(CELL, 0, CELL + CELL // 4, CELL // 4))


def test_a_fill_polygon_is_filled_light_and_outlined():
    image = _image([[polygon([(0, 0), (1, 0), (1, 1), (0, 1)], fill=True)]])
    assert _rows_of(image, FILL) and _rows_of(image, LINE)


def test_an_arc_is_drawn():
    assert _rows_of(_image([[arc(0, PI / 2)]]), LINE)


# ---------- the reads ----------

def _connection(tapir, missing=()):
    official = dict(api_replays.OFFICIAL)
    official["API.IsAddOnCommandAvailable"] = lambda p: {
        "available": p["addOnCommandId"]["commandName"] not in missing}
    core = FakeCore(official=official, tapir=tapir)
    return ArchicadConnection(19723, core=core), core


def _sent(core, command):
    return [p for c, p in core.calls if c == command]


def _refuses(p):
    raise TapirCommandError(message="refused", code=1)


def _named(**values):
    return [{"name": k, "type": "Length", "value": v} for k, v in values.items()]


def test_own_size_comes_from_a_b_and_zzyzx():
    assert own_size_of(_named(A=0.8, B=2.0, ZZYZX=0.5)) == {"a": 0.8, "b": 2.0, "height": 0.5}


def test_a_part_without_zzyzx_has_no_height():
    assert own_size_of(_named(A=0.9, B=2.1)) == {"a": 0.9, "b": 2.1, "height": None}


@pytest.mark.parametrize("zzyzx", [0, -1, "tall"])
def test_a_height_not_above_zero_is_no_height(zzyzx):
    assert own_size_of(_named(A=0.9, B=2.1, ZZYZX=zzyzx)) == {"a": 0.9, "b": 2.1, "height": None}


@pytest.mark.parametrize("a", [0, -0.5, None, "wide", True, [1.0]])
def test_an_a_that_is_no_positive_number_gives_no_size(a):
    assert own_size_of(_named(A=a, B=2.0)) is None


def test_a_b_that_is_no_positive_number_gives_no_size():
    assert own_size_of(_named(A=1.0, B="deep")) is None


@pytest.mark.parametrize("parameters", [None, []])
def test_no_parameters_give_no_size(parameters):
    assert own_size_of(parameters) is None


def test_default_parameters_ask_by_name():
    answer = {"gdlParametersOfLibraryParts": [{"parameters": _named(A=1.0)}]}
    conn, core = _connection({"GetGDLParametersOfLibraryParts": answer})
    assert default_parameters(conn, "Bed Double 01") == _named(A=1.0)
    assert _sent(core, "GetGDLParametersOfLibraryParts") == [
        {"libraryParts": [{"libraryPartName": "Bed Double 01"}]}]


@pytest.mark.parametrize("tapir, missing", [
    ({}, ["GetGDLParametersOfLibraryParts"]),
    ({"GetGDLParametersOfLibraryParts": _refuses}, ()),
    ({"GetGDLParametersOfLibraryParts": {"gdlParametersOfLibraryParts": [
        {"error": {"code": 1, "message": "Not found"}}]}}, ()),
    ({"GetGDLParametersOfLibraryParts": {"gdlParametersOfLibraryParts": [
        {"parameters": []}]}}, ()),
    ({"GetGDLParametersOfLibraryParts": {"gdlParametersOfLibraryParts": []}}, ()),
])
def test_default_parameters_are_none_when_the_read_fails(tapir, missing):
    conn, _ = _connection(tapir, missing)
    assert default_parameters(conn, "Door") is None


def test_symbol_of_asks_for_the_defaults_or_an_instance():
    answer = {"primitives": [line(0, 0, 1, 1)]}
    conn, core = _connection({"GetLibraryPart2DPrimitives": answer})
    assert symbol_of(conn, "Door") == [line(0, 0, 1, 1)]
    symbol_of(conn, "Door", "G")
    assert _sent(core, "GetLibraryPart2DPrimitives") == [
        {"name": "Door"}, {"name": "Door", "elementId": {"guid": "G"}}]


@pytest.mark.parametrize("tapir, missing", [
    ({}, ["GetLibraryPart2DPrimitives"]),
    ({"GetLibraryPart2DPrimitives": _refuses}, ()),
])
def test_symbol_of_is_none_when_the_read_fails(tapir, missing):
    conn, _ = _connection(tapir, missing)
    assert symbol_of(conn, "Door") is None


def test_a_symbol_with_nothing_in_it_is_an_empty_list():
    conn, _ = _connection({"GetLibraryPart2DPrimitives": {"primitives": []}})
    assert symbol_of(conn, "Door") == []
