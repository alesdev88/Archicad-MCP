"""preview_library_parts: a part's symbol drawn with nothing placed.

Answers as Tapir's main after 1.8.0 gives them, read from its source: a name no loaded part
has raises through the transport, as a top-level error does.
"""
from __future__ import annotations

import base64
import io
import json

import pytest
from fastmcp import Client
from mcp.types import ImageContent, TextContent
from multiconn_archicad.errors import TapirCommandError
from PIL import Image

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.part_preview import MAX_PARTS, preview_library_parts
from archicad_mcp.core.part_symbols import CELL
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def rect(x0, y0, x1, y1):
    return [{"type": "Polygon", "coordinates": [
        {"x": x0, "y": y0}, {"x": x1, "y": y0}, {"x": x1, "y": y1}, {"x": x0, "y": y1}]}]


BED = {"own": {"A": 1.6, "B": 2.0, "ZZYZX": 0.6}, "symbol": rect(0, 0, 1.6, 2.0)}
DOOR = {"own": {"A": 0.9, "B": 2.1}, "symbol": rect(0, 0, 0.9, 0.1)}
READS = {"GetLibraryPart2DPrimitives", "GetGDLParametersOfLibraryParts"}


class Library:
    """Loaded parts by name; a part may have no `own` (its defaults cannot be read)."""

    def __init__(self, parts=None, missing=()):
        self.parts = {"Bed Double 01": BED, "Door": DOOR} if parts is None else parts
        self.missing = set(missing)

    def _symbol(self, p):
        if p["name"] not in self.parts:
            raise TapirCommandError(message=f"No loaded library part with the name '{p['name']}'.",
                                    code=-1)
        return {"primitives": self.parts[p["name"]]["symbol"]}

    def _defaults(self, p):
        out = []
        for entry in p["libraryParts"]:
            own = self.parts.get(entry["libraryPartName"], {}).get("own")
            out.append({"parameters": [{"name": k, "type": "Length", "value": v}
                                       for k, v in own.items()]} if own
                       else {"error": {"code": 1, "message": "Not found library part"}})
        return {"gdlParametersOfLibraryParts": out}

    def connection(self) -> ArchicadConnection:
        official = dict(api_replays.OFFICIAL)
        official["API.IsAddOnCommandAvailable"] = lambda p: {
            "available": p["addOnCommandId"]["commandName"] not in self.missing}
        tapir = dict(api_replays.TAPIR)
        tapir["GetLibraryPart2DPrimitives"] = self._symbol
        tapir["GetGDLParametersOfLibraryParts"] = self._defaults
        tapir["GetAvailableLibraryParts"] = lambda p: {"libraryParts": [
            {"guid": f"lib-{i}", "index": i, "documentName": name, "fileName": f"{name}.gsm",
             "typeId": "Object"} for i, name in enumerate(self.parts, 1)], "skippedCount": 0}
        self.core = FakeCore(official=official, tapir=tapir)
        return ArchicadConnection(19723, core=self.core)

    def commands(self) -> list[str]:
        return [command for command, _ in self.core.calls]


def _size(out) -> tuple[int, int]:
    return Image.open(io.BytesIO(out["png"])).size


def test_parts_are_drawn_numbered_with_their_sizes():
    library = Library()
    out = preview_library_parts(library.connection(), ["Bed Double 01", "Door"])
    assert out["parts"] == [
        {"number": 1, "name": "Bed Double 01", "a": 1.6, "b": 2.0, "height": 0.6,
         "extent": [0.0, 0.0, 1.6, 2.0]},
        {"number": 2, "name": "Door", "a": 0.9, "b": 2.1, "height": None,
         "extent": [0.0, 0.0, 0.9, 0.1]}]
    assert out["not_drawn"] == []
    assert _size(out) == (2 * CELL, CELL)


def test_the_note_says_what_the_picture_leaves_out():
    note = preview_library_parts(Library().connection(), ["Door"])["note"]
    assert "default parameters" in note and "hotspots" in note


@pytest.mark.parametrize("names", [[], [f"P{i}" for i in range(MAX_PARTS + 1)]])
def test_any_other_count_is_an_error_and_reads_nothing(names):
    library = Library()
    out = preview_library_parts(library.connection(), names)
    assert str(MAX_PARTS) in out["error"]
    assert not READS & set(library.commands())


def test_a_name_given_twice_is_drawn_once():
    out = preview_library_parts(Library().connection(), ["Door", "Door"])
    assert [p["name"] for p in out["parts"]] == ["Door"]
    assert _size(out) == (CELL, CELL)


def test_a_name_repeated_counts_once_against_the_limit():
    parts = {f"P{i}": DOOR for i in range(MAX_PARTS)}
    names = [*parts, "P0"]
    out = preview_library_parts(Library(parts).connection(), names)
    assert len(out["parts"]) == MAX_PARTS


def test_a_name_not_found_says_the_closest():
    out = preview_library_parts(Library().connection(), ["Beds Double 01", "Door"])
    assert [p["name"] for p in out["parts"]] == ["Door"]
    [missed] = out["not_drawn"]
    assert missed["name"] == "Beds Double 01" and "Bed Double 01" in missed["reason"]


def test_a_part_not_found_is_numbered_past():
    out = preview_library_parts(Library().connection(), ["Nope", "Door"])
    assert out["parts"][0]["number"] == 1


def test_the_library_is_listed_only_when_a_name_fails():
    library = Library()
    preview_library_parts(library.connection(), ["Door"])
    assert "GetAvailableLibraryParts" not in library.commands()


def test_a_loaded_part_archicad_gives_no_symbol_for_says_so():
    library = Library()
    conn = library.connection()

    def refuses(p):
        raise TapirCommandError(message="Failed to get the 2D primitives", code=-1)

    library.core.tapir_responses["GetLibraryPart2DPrimitives"] = refuses
    [missed] = preview_library_parts(conn, ["Door"])["not_drawn"]
    assert "no 2D symbol" in missed["reason"] and "named" not in missed["reason"]


def test_a_part_with_nothing_to_draw_is_not_drawn():
    library = Library({"Empty": {"own": {"A": 1.0, "B": 1.0}, "symbol": []}})
    out = preview_library_parts(library.connection(), ["Empty"])
    assert out["parts"] == []
    assert "no lines, arcs or polygons" in out["not_drawn"][0]["reason"]


def test_with_nothing_drawn_there_is_no_image():
    out = preview_library_parts(Library().connection(), ["Nope"])
    assert "png" not in out and "note" in out


def test_a_part_whose_defaults_cannot_be_read_is_drawn_without_sizes():
    library = Library({"Door": {"symbol": rect(0, 0, 1, 1)}})
    [part] = preview_library_parts(library.connection(), ["Door"])["parts"]
    assert (part["a"], part["b"], part["height"]) == (None, None, None)
    assert part["extent"] == [0.0, 0.0, 1.0, 1.0]


@pytest.mark.parametrize("command", sorted(READS))
def test_a_tapir_without_either_command_is_refused(command):
    library = Library(missing=[command])
    out = preview_library_parts(library.connection(), ["Door"])
    assert command in out["error"] and "png" not in out


def test_it_sends_no_write():
    library = Library()
    preview_library_parts(library.connection(), ["Door", "Nope"])
    assert set(library.commands()) <= READS | {"GetAvailableLibraryParts", "GetAddOnVersion",
                                               "API.IsAddOnCommandAvailable"}


async def _call(monkeypatch, library, names, mode="full"):
    conn = library.connection()
    monkeypatch.setattr(server_mod, "get_connection", lambda port: conn)
    async with Client(build_server(mode=mode)) as client:
        return await client.call_tool("preview_library_parts", {"names": names})


async def test_the_tool_returns_the_image_then_the_json(monkeypatch):
    result = await _call(monkeypatch, Library(), ["Door"])
    image, text = result.content
    assert isinstance(image, ImageContent) and image.mimeType == "image/png"
    assert base64.b64decode(image.data).startswith(b"\x89PNG")
    assert isinstance(text, TextContent)
    assert json.loads(text.text)["parts"][0]["name"] == "Door"


async def test_the_tool_answers_json_alone_when_nothing_is_drawn(monkeypatch):
    result = await _call(monkeypatch, Library(), ["Nope"])
    [text] = result.content
    assert json.loads(text.text)["parts"] == []


async def test_the_tool_is_full_mode_only():
    async with Client(build_server(mode="verdicts")) as client:
        assert "preview_library_parts" not in {t.name for t in await client.list_tools()}
