"""Draw icon.png, the Claude Desktop extension icon.

Not part of the package or the test suite. Pillow is a build-time-only need, so
it is pulled in ephemerally rather than added to the project's dependencies:

    uv run --with pillow python scripts/make_icon.py

The motif is the plan-view door swing: a wall with an opening, the leaf standing
open, and its quarter arc. It is the most universally legible symbol in
architectural drawing, it is nobody's trademark, and it survives being shrunk to
a list tile because it is only three bold shapes.

Two things drive the geometry. The walls are inset from every edge, so the mark
reads as composed rather than as a crop that ran off the tile. And the arc is
struck at a radius equal to the opening, which is what makes it read as a door
rather than as a curve that happens to be nearby.

Drawn at 4x and downsampled, because Pillow's draw primitives are not
antialiased.
"""
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512          # design canvas, and the size shipped
SUPERSAMPLE = 4
N = SIZE * SUPERSAMPLE

BG = (21, 35, 58)          # deep navy, a drawing ground
WALL = (245, 247, 250)     # near white poche
SWING = (242, 169, 59)     # amber, so the door reads apart from the wall

CORNER_RADIUS = 112
WALL_T = 66                # wall thickness
WALL_Y = 277               # top face of the wall
X0, X1 = 56, 456           # outer ends of the two wall segments
JAMB_A, JAMB_B = 186, 326  # the opening
ARC_W, LEAF_W = 20, 30


def u(v: float) -> float:
    """Design units to supersampled pixels."""
    return v * SUPERSAMPLE


def render() -> Image.Image:
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, N - 1, N - 1], radius=u(CORNER_RADIUS), fill=BG)

    d.rectangle([u(X0), u(WALL_Y), u(JAMB_A), u(WALL_Y + WALL_T)], fill=WALL)
    d.rectangle([u(JAMB_B), u(WALL_Y), u(X1), u(WALL_Y + WALL_T)], fill=WALL)

    hinge = (JAMB_A, WALL_Y + WALL_T / 2)
    radius = JAMB_B - JAMB_A
    d.arc([u(hinge[0] - radius), u(hinge[1] - radius),
           u(hinge[0] + radius), u(hinge[1] + radius)],
          start=270, end=360, fill=SWING, width=u(ARC_W))

    tip = (hinge[0], hinge[1] - radius)
    d.line([u(hinge[0]), u(hinge[1]), u(tip[0]), u(tip[1])],
           fill=SWING, width=u(LEAF_W))
    # Pillow's thick lines have square ends; round both so the leaf does not
    # read as a snapped-off bar.
    for point in (tip, hinge):
        r = u(LEAF_W / 2)
        d.ellipse([u(point[0]) - r, u(point[1]) - r,
                   u(point[0]) + r, u(point[1]) + r], fill=SWING)

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "icon.png"
    render().save(out, optimize=True)
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
