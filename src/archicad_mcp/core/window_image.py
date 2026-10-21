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

# The longest side, in pixels, of the image the model gets: Claude's own
# native long edge, so it is not scaled twice. Larger windows are scaled down.
MAX_SIDE = 1568

# Windows past this many pixels are refused before they are decoded: an entire
# drawing at a close zoom can run to hundreds of megabytes once decoded.
MAX_PIXELS = 40_000_000


def capture_window(conn: ArchicadConnection, entire_drawing: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="archicad-mcp-") as folder:
        path = Path(folder) / "window.png"
        conn.tapir("SaveCurrentWindowAsImage", {"imageFilePath": str(path), "format": "png",
                                                "entireDrawing": entire_drawing})
        # Read inside the folder's lifetime and close at once, so nothing holds
        # the file when the folder goes (Windows refuses to delete an open file).
        data = path.read_bytes() if path.is_file() else None
    if not data:
        return {"error": "Archicad answered but wrote no image file, so there is no "
                         "image of the window."}
    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size  # from the header, before anything is decoded
        if width * height > MAX_PIXELS:
            return {"error": f"The window image is {width} x {height} pixels, too large to "
                             "send. Zoom in on what to check, or leave entire_drawing off."}
        image.load()
    except (OSError, Image.DecompressionBombError) as exc:
        return {"error": f"Archicad wrote an image file that could not be read ({exc})."}
    result = {"window": active_window(conn), "width": width, "height": height}
    if max(width, height) > MAX_SIDE:
        result["scaled_from"] = [width, height]
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        result["width"], result["height"] = image.size
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    result["png"] = buffer.getvalue()
    return result
