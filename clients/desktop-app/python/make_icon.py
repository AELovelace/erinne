r"""Makes the desktop app's icon (src/Mnemerynne.Desktop/Assets/app.ico) from the site's own app icon.

The site's PWA icon (mnemerynne/web/static/icons/icon-512.png) is the 🌸 people already know from the browser, so
the window, the taskbar, the tray, the installer and the Explorer drive all wear the same one. Run it again only if
that icon changes:
    .venv\Scripts\python.exe clients\desktop-app\python\make_icon.py      (from the repo root; needs Pillow)
"""

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent                     # clients/desktop-app/python
REPO = HERE.parents[2]                                      # the repo root
SOURCE = REPO / "mnemerynne" / "web" / "static" / "icons" / "icon-512.png"
OUT = HERE.parent / "src" / "Mnemerynne.Desktop" / "Assets" / "app.ico"
SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]   # Windows' sizes

img = Image.open(SOURCE).convert("RGBA")                    # keeps the transparent corners
OUT.parent.mkdir(parents=True, exist_ok=True)
img.save(OUT, sizes=SIZES)                                  # one .ico with every size Windows asks for
print(f"wrote {OUT}")
