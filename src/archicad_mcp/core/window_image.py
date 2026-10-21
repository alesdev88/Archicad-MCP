"""capture_window: a picture of what the current Archicad window shows.

Tapir 1.7.2's SaveCurrentWindowAsImage writes the window to a file as File >
Save As does with a picture format. The file is read back and handed to the
model, so a placement can be checked by eye (which way a bed faces, which way a
door opens) instead of by the user.
"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path

from PIL import Image

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.editability import active_window

# The longest side, in pixels, of the image the model gets. Larger windows are
# scaled down: enough to read a floor plan at the zoom shown, without sending a
# full-resolution screen.
MAX_SIDE = 1600


def capture_window(conn: ArchicadConnection, entire_drawing: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="archicad-mcp-") as folder:
        path = Path(folder) / "window.png"
        conn.tapir("SaveCurrentWindowAsImage", {"imageFilePath": str(path), "format": "png",
                                                "entireDrawing": entire_drawing})
        if not path.is_file():
            return {"error": "Archicad answered but wrote no image file, so there is no "
                             "image of the window."}
        image = Image.open(path)
        image.load()
    result = {"window": active_window(conn), "width": image.width, "height": image.height}
    if max(image.size) > MAX_SIDE:
        result["scaled_from"] = list(image.size)
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        result["width"], result["height"] = image.size
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    result["png"] = buffer.getvalue()
    return result
