r"""Makes the desktop app's icon (src/Mnemerynne.Desktop/Assets/app.ico) from the site's own app icon, without its background.

The site's PWA icon (mnemerynne/web/static/icons/icon-512.png) is the 🌸 people already know from the browser, so
the window, the taskbar, the tray, the installer and the Explorer drive all wear the same flower. That PNG is a full
square tile (a dark gradient with a pink glow at the bottom), which installed web apps need; the desktop app shows
only the flower, so this script keys the dark tile out, crops to the flower and writes:
    Assets/app.ico      every size Windows asks for, with transparency
    Assets/app-512.png  the same flower as a transparent PNG (to look at, or for anything that wants a PNG)
Run it again only if the site's icon changes:
    .venv\Scripts\python.exe clients\desktop-app\python\make_icon.py      (from the repo root; needs Pillow)
"""

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent                     # clients/desktop-app/python
REPO = HERE.parents[2]                                      # the repo root
SOURCE = REPO / "mnemerynne" / "web" / "static" / "icons" / "icon-512.png"
ASSETS = HERE.parent / "src" / "Mnemerynne.Desktop" / "Assets"
OUT = ASSETS / "app.ico"
OUT_PNG = ASSETS / "app-512.png"
SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]   # Windows' sizes

LO, HI = 125, 175            # brightness (the brightest channel) where a pixel goes from background to flower:
                             # the bottom glow peaks at 114, the flower's darkest edge (cyan) is ~180
BG = (12, 4, 18)             # the tile's dark colour, taken back out of the soft edge pixels
MARGIN = 0.04                # empty space around the flower, as a share of the icon's side


def smoothstep(x: float) -> float:
    """0 below LO, 1 above HI, a smooth S-curve between: soft, anti-aliased edges instead of a jagged cut."""
    t = min(1.0, max(0.0, (x - LO) / (HI - LO)))
    return t * t * (3 - 2 * t)


def key_out(img: Image.Image) -> Image.Image:
    """The flower alone: dark tile pixels become transparent, edge pixels lose the tile's colour (no dark fringe)."""
    out = Image.new("RGBA", img.size)
    px, dst = img.load(), out.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a0 = px[x, y]
            a = smoothstep(max(r, g, b)) * (a0 / 255)          # how much of this pixel is flower
            if a <= 0:
                dst[x, y] = (0, 0, 0, 0)                        # pure background: fully see-through
                continue
            # Un-premultiply: what's left after taking the tile's share (1 - a) of BG out of the colour.
            rgb = tuple(int(min(255, max(0, (c - k * (1 - a)) / a))) for c, k in zip((r, g, b), BG))
            dst[x, y] = (*rgb, round(a * 255))
    return out


def fit_square(img: Image.Image, side: int = 512) -> Image.Image:
    """Crop to the flower and centre it on a transparent square, so it fills the icon even at 16 px."""
    flower = img.crop(img.getchannel("A").getbbox())          # tight box around everything not see-through
    inner = round(side * (1 - 2 * MARGIN))
    scale = inner / max(flower.size)                           # longest side fills the space inside the margin
    flower = flower.resize((round(flower.width * scale), round(flower.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(flower, ((side - flower.width) // 2, (side - flower.height) // 2), flower)
    return canvas


icon = fit_square(key_out(Image.open(SOURCE).convert("RGBA")))   # the site's tile -> the flower alone
ASSETS.mkdir(parents=True, exist_ok=True)
icon.save(OUT_PNG)                                          # the transparent PNG
icon.save(OUT, sizes=SIZES)                                 # one .ico with every size Windows asks for
print(f"wrote {OUT} and {OUT_PNG}")
