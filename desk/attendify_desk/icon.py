"""Tray icon generated in code (no image assets to ship)."""

from __future__ import annotations

from PIL import Image, ImageDraw

COLOURS = {
    "green": (34, 170, 90),
    "amber": (240, 170, 20),
    "grey": (140, 140, 140),
}


def make_icon(colour: str = "grey", size: int = 64) -> Image.Image:
    """A coloured disc with a white dot and two broadcast arcs."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = size / 64
    d.ellipse((2 * u, 2 * u, 62 * u, 62 * u), fill=COLOURS.get(colour, COLOURS["grey"]))
    d.ellipse((26 * u, 26 * u, 38 * u, 38 * u), fill="white")
    w = max(2, int(4 * u))
    d.arc((16 * u, 16 * u, 48 * u, 48 * u), -50, 50, fill="white", width=w)
    d.arc((16 * u, 16 * u, 48 * u, 48 * u), 130, 230, fill="white", width=w)
    d.arc((8 * u, 8 * u, 56 * u, 56 * u), -50, 50, fill="white", width=w)
    d.arc((8 * u, 8 * u, 56 * u, 56 * u), 130, 230, fill="white", width=w)
    return img
