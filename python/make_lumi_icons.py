"""Draws the app icons for Lumi Dungeons and Lumi's Garden into web/static/icons/.

Lumi is one pet in three apps (mnemerynne/games.py LUMI_APPS). Lumi Pet's own icon is hand-drawn (icons/lumi.svg and
the lumi-*.png beside it) and this script never touches it. The other two apps get the same face on their own
colours, with a little badge saying which app it is: a key for the dungeons, a flower for the garden.

For each app it writes <slug>.svg (the drawing, kept beside the PNGs like lumi.svg is) and four PNGs:
<slug>-180.png (iPhone, square), <slug>-192.png and <slug>-512.png (rounded corners on clear), and
<slug>-maskable-512.png (square, the drawing kept inside the middle so a launcher can crop it).

The PNGs are the SVG photographed by headless Chrome, so the script needs Chrome (or Edge) but no Python packages.
Set CHROME_PATH if it isn't in one of the usual places.

Run from anywhere: python python/make_lumi_icons.py
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "mnemerynne" / "web" / "static" / "icons"   # where the app icons live
BROWSERS = (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# Lumi's face, the same shapes as icons/lumi.svg (which come from templates/games/lumi-pet.html).
FACE = """<g transform="translate(-112 -22) scale(2.3)">
    <path d="M92 153 L88 62 Q89 49 100 58 L137 93 Q160 83 183 93 L220 58 Q231 49 232 62 L228 153 Q230 204 160 208 Q90 204 92 153Z" fill="#fce1f2"/>
    <path d="M101 77 L106 118 L126 102Z M219 77 L214 118 L194 102Z" fill="{ear}"/>
    <path d="M160 103 L165 116 L179 120 L165 125 L160 139 L155 125 L141 120 L155 116Z" fill="#ffdc83"/>
    <ellipse cx="129" cy="153" rx="5" ry="9" fill="#3d2450"/><ellipse cx="191" cy="153" rx="5" ry="9" fill="#3d2450"/>
    <ellipse cx="114" cy="169" rx="13" ry="6" fill="{ear}" opacity=".55"/><ellipse cx="206" cy="169" rx="13" ry="6" fill="{ear}" opacity=".55"/>
    <path d="M155 165 L160 169 L165 165 M160 169 Q151 184 145 173 M160 169 Q169 184 175 173" fill="none" stroke="#3d2450" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  </g>"""

# The badge sits on her lower right cheek: a dark disc with a bright rim, and the app's sign inside it.
KEY = """<g transform="translate(398 398)">
    <circle r="66" fill="#0f1426" stroke="#ffdc83" stroke-width="8"/>
    <g transform="rotate(45)" fill="none" stroke="#ffdc83" stroke-width="11" stroke-linecap="round">
      <circle cx="-24" r="15"/><path d="M-9 0 H36 M20 0 V15 M34 0 V12"/>
    </g>
  </g>"""
FLOWER = """<g transform="translate(398 398)">
    <circle r="66" fill="#142218" stroke="#ff8ecb" stroke-width="8"/>
    <g fill="#ff8ecb">{petals}</g>
    <circle r="13" fill="#ffdc83"/>
  </g>""".format(petals="".join(f'<ellipse cy="-25" rx="15" ry="22" transform="rotate({turn})"/>' for turn in range(0, 360, 72)))   # five petals round the middle

APPS = {   # slug: the background's glow (middle, edge), her ears and cheeks, the two little sparkles, and the badge
    "lumi-dungeons": {"inner": "#2b4f7a", "outer": "#0f1426", "ear": "#70ebea", "spark": "#70ebea", "badge": KEY,
                      "what": "Lumi Dungeons' app icon: her face on a deep dungeon blue, with a golden key."},
    "lumi-garden": {"inner": "#3d7a57", "outer": "#12241a", "ear": "#ff8ecb", "spark": "#ffdc83", "badge": FLOWER,
                    "what": "Lumi's Garden's app icon: her face on a leafy green, with a pink flower."},
}


def drawing(app: dict, *, rounded: bool, inset: float = 1.0) -> str:
    """One icon as SVG text. `rounded` gives the square its soft corners; `inset` shrinks the picture towards the
    middle (a maskable icon keeps everything inside the part no launcher crops)."""
    corner = ' rx="112"' if rounded else ""                                   # launchers and iPhones round a square icon themselves
    shrink = f' transform="translate(256 256) scale({inset}) translate(-256 -256)"' if inset != 1.0 else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <!-- {app["what"]} Made by python/make_lumi_icons.py, like the PNGs beside it: change the script, not this file. -->
  <defs>
    <radialGradient id="glow" cx="50%" cy="46%" r="60%"><stop offset="0" stop-color="{app["inner"]}"/><stop offset="1" stop-color="{app["outer"]}"/></radialGradient>
  </defs>
  <rect width="512" height="512"{corner} fill="url(#glow)"/>
  <g{shrink}>
  <path d="M256 60 l9 24 24 9 -24 9 -9 24 -9 -24 -24 -9 24 -9Z" fill="#ffdc83" opacity=".9"/>
  <path d="M92 150 l5 14 14 5 -14 5 -5 14 -5 -14 -14 -5 14 -5Z M420 138 l4 11 11 4 -11 4 -4 11 -4 -11 -11 -4 11 -4Z" fill="{app["spark"]}" opacity=".8"/>
  {FACE.format(ear=app["ear"])}
  {app["badge"]}
  </g>
</svg>
"""


def browser() -> str:
    """The Chrome (or Edge) to photograph with: CHROME_PATH if set, otherwise the first usual place that exists."""
    found = next((path for path in (os.environ.get("CHROME_PATH"), *BROWSERS) if path and Path(path).is_file()), None)
    if found is None:
        raise SystemExit("Chrome wasn't found. Set CHROME_PATH to chrome.exe (or msedge.exe) and run this again.")
    return found


def photograph(chrome: str, svg: str, size: int, target: Path, work: Path) -> None:
    """Render one SVG at size x size pixels into a PNG, on a clear background so rounded corners stay see-through."""
    page = work / "icon.html"                                                 # a bare page holding just the drawing
    sized = svg.replace("<svg ", f'<svg width="{size}" height="{size}" style="display:block" ', 1)   # the drawing at exactly the size wanted
    page.write_text('<!doctype html><html><body style="margin:0;overflow:hidden;background:transparent">' + sized + "</body></html>", encoding="utf-8")
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--user-data-dir={work / 'profile'}",                    # its own throwaway profile: never the person's browser
                    "--default-background-color=00000000", f"--window-size={size},{size}", f"--screenshot={target}", page.as_uri()],
                   check=True, capture_output=True, timeout=120)
    if not target.is_file() or target.stat().st_size < 1000:                  # Chrome says nothing when it fails to write
        raise SystemExit(f"Chrome didn't draw {target.name}.")


def main() -> None:
    """Writes the drawing and the four PNGs each app's manifest and page ask for (web/app.py lumi_manifest, templates/game.html)."""
    chrome = browser()
    with tempfile.TemporaryDirectory() as folder:                             # Chrome's scratch space, removed afterwards
        work = Path(folder)
        for slug, app in APPS.items():
            (OUT / f"{slug}.svg").write_text(drawing(app, rounded=True), encoding="utf-8", newline="\n")   # the drawing itself, for people to look at
            print("wrote", OUT / f"{slug}.svg")
            for name, size, rounded, inset in ((f"{slug}-180.png", 180, False, 1.0), (f"{slug}-192.png", 192, True, 1.0),
                                               (f"{slug}-512.png", 512, True, 1.0), (f"{slug}-maskable-512.png", 512, False, 0.76)):
                photograph(chrome, drawing(app, rounded=rounded, inset=inset), size, OUT / name, work)
                print("wrote", OUT / name)


if __name__ == "__main__":
    main()
