"""capture_window: what the current Archicad window shows, as an image the model sees."""
import io
import json
from pathlib import Path

from fastmcp import Client
from PIL import Image

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.window_image import capture_window
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


class Window:
    """Answers SaveCurrentWindowAsImage the way Tapir does: writes the file it is given."""

    def __init__(self, size=(800, 600), write=True, window="FloorPlan", missing=()):
        self.size, self.write, self.window = size, write, window
        self.missing = set(missing)
        self.sent: list[dict] = []

    def save(self, params):
        self.sent.append(params)
        if self.write:
            Image.new("RGB", self.size, "white").save(params["imageFilePath"], "PNG")
        return {"imageFilePath": params["imageFilePath"]}

    def core(self) -> FakeCore:
        official = dict(api_replays.OFFICIAL)
        official["API.IsAddOnCommandAvailable"] = lambda p: {
            "available": p["addOnCommandId"]["commandName"] not in self.missing}
        tapir = dict(api_replays.TAPIR)
        tapir["SaveCurrentWindowAsImage"] = self.save
        tapir["GetCurrentWindowType"] = lambda p: {"currentWindowType": self.window}
        return FakeCore(official=official, tapir=tapir)

    def connection(self) -> ArchicadConnection:
        return ArchicadConnection(19723, core=self.core())


def _size(png: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png)).size


def test_the_window_comes_back_as_a_png_of_its_size():
    window = Window(size=(800, 600))
    result = capture_window(window.connection())
    assert _size(result["png"]) == (800, 600)
    assert result["window"] == "FloorPlan"
    assert result["width"] == 800 and result["height"] == 600
    [sent] = window.sent
    assert sent["format"] == "png" and sent["entireDrawing"] is False
    assert Path(sent["imageFilePath"]).is_absolute()


def test_the_image_file_does_not_outlive_the_call():
    window = Window()
    capture_window(window.connection())
    assert not Path(window.sent[0]["imageFilePath"]).exists()


def test_a_large_window_is_scaled_to_the_longest_side_limit():
    window = Window(size=(4000, 2000))
    result = capture_window(window.connection())
    assert _size(result["png"]) == (1600, 800)
    assert result["scaled_from"] == [4000, 2000]


def test_the_whole_drawing_can_be_asked_for():
    window = Window()
    capture_window(window.connection(), entire_drawing=True)
    assert window.sent[0]["entireDrawing"] is True


def test_no_file_written_is_reported_as_an_error():
    result = capture_window(Window(write=False).connection())
    assert "png" not in result
    assert "no image" in result["error"]


async def _call(monkeypatch, window, args=None):
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=window.core()))
    async with Client(build_server(mode="full")) as client:
        return await client.call_tool("capture_window", args or {})


async def test_the_tool_hands_the_model_an_image_and_what_it_shows(monkeypatch):
    result = await _call(monkeypatch, Window(window="3DModel"))
    kinds = [block.type for block in result.content]
    assert kinds == ["image", "text"]
    assert result.content[0].mimeType == "image/png"
    assert json.loads(result.content[1].text)["window"] == "3DModel"


async def test_an_older_tapir_is_refused_before_anything_is_sent(monkeypatch):
    window = Window(missing={"SaveCurrentWindowAsImage"})
    result = await _call(monkeypatch, window)
    assert "1.7.2" in json.loads(result.content[0].text)["error"]
    assert window.sent == []
