"""Rasterise icon.png from icon.svg, the Claude Desktop extension icon.

Not part of the package or the test suite. Pillow is a build-time-only need, so
it is pulled in ephemerally rather than added to the project's dependencies:

    uv run --with pillow python scripts/make_icon.py

icon.svg is the source; edit that, then run this. The motif is a massing model
with a corner cut away: the pale volume is the building, the amber faces are the
cut, where the server reaches in. It survives being shrunk to a list tile
because it is solid shapes rather than strokes, and because the silhouette stays
a plain hexagon at every size.

Every shape in icon.svg is a straight-sided polygon, the squircle tile included,
which is why this renders with Pillow and the twenty-line reader below instead
of a real SVG library. The two candidates, cairosvg and reportlab, would each
add a dependency to install on every machine that builds a bundle, macOS and
Windows alike, to draw a picture that needs none of it. The price is that the
reader understands only moveto, lineto and closepath: give icon.svg a curve and
it raises here rather than quietly rendering the wrong thing.

Drawn at 4x and downsampled, because Pillow's draw primitives are not
antialiased.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

SUPERSAMPLE = 4
SVG = "{http://www.w3.org/2000/svg}"

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "icon.svg"
OUT = REPO / "icon.png"


def polygons(root):
    """Each <path> in document order, as (points, fill colour)."""
    for path in root.iter(f"{SVG}path"):
        fill = path.get("fill")
        d = path.get("d", "")
        if not fill or fill == "none":
            raise ValueError(f"path with nothing to fill: {d[:40]}...")
        tokens = re.findall(r"[A-Za-z]|-?\d+(?:\.\d+)?", d)
        points, i = [], 0
        while i < len(tokens):
            if tokens[i] in ("M", "L"):
                points.append((float(tokens[i + 1]), float(tokens[i + 2])))
                i += 3
            elif tokens[i] == "Z":
                i += 1
            else:
                raise ValueError(
                    f"icon.svg uses the path command {tokens[i]!r}, which this "
                    f"reader does not draw; see the module docstring"
                )
        yield points, fill


def render() -> Image.Image:
    root = ET.parse(SOURCE).getroot()
    _, _, width, height = (float(v) for v in root.get("viewBox").split())
    if width != height:
        raise ValueError(f"icon.svg is {width}x{height}; the icon must be square")

    size = int(width)
    n = size * SUPERSAMPLE
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for points, fill in polygons(root):
        draw.polygon([(x * SUPERSAMPLE, y * SUPERSAMPLE) for x, y in points], fill=fill)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    render().save(OUT, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
