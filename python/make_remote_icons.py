"""Draws the Remote app's icons (a round D-pad in the remote panel's colours) into web/static/icons/.

Run from anywhere: python python/make_remote_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "mnemerynne" / "web" / "static" / "icons"   # where the app's icons live
BG, CARD = "#0D0614", "#24102E"     # the panel's night-plum background and glass card
PINK, TEAL = "#FF4FA3", "#22F0FF"   # its neon pink and cyan
OVERSAMPLE = 4                      # drawn this many times too big, then shrunk, for smooth edges


def draw_icon(size: int, fill: float) -> Image.Image:
    """One square icon; `fill` is how much of the width the D-pad takes (less for a maskable icon's safe zone)."""
    big = size * OVERSAMPLE                                  # the working canvas
    image = Image.new("RGB", (big, big), BG)                 # solid: launchers and iPhones round the corners themselves
    pen = ImageDraw.Draw(image)
    middle, radius = big / 2, big * fill / 2                 # the disc's centre and radius
    ring = max(OVERSAMPLE, round(radius * 0.06))             # the neon rim's thickness

    def circle(r: float, **style) -> None:                   # a circle around the middle
        pen.ellipse((middle - r, middle - r, middle + r, middle + r), **style)

    circle(radius, fill=CARD, outline=PINK, width=ring)      # the glass disc and its rim
    near, far, half = radius * 0.46, radius * 0.80, radius * 0.20   # each arrow: base distance, tip distance, half width
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):        # up, down, left and right
        pen.polygon([(middle + dx * far, middle + dy * far),                                # the tip
                     (middle + dx * near - dy * half, middle + dy * near - dx * half),      # one corner of the base
                     (middle + dx * near + dy * half, middle + dy * near + dx * half)],     # the other corner
                    fill=PINK)
    circle(radius * 0.30, fill=PINK, outline=TEAL, width=ring)   # Enter, with its cyan ring
    return image.resize((size, size), Image.LANCZOS)         # shrinks to the real size


def main() -> None:
    """Writes the four files the Remote app's manifest and page ask for."""
    for name, size, fill in (("remote-180.png", 180, 0.80), ("remote-192.png", 192, 0.84), ("remote-512.png", 512, 0.84),
                             ("remote-maskable-512.png", 512, 0.66)):   # maskable: launchers may crop to the middle 80%
        draw_icon(size, fill).save(OUT / name, optimize=True)            # one PNG per use
        print("wrote", OUT / name)


if __name__ == "__main__":
    main()
