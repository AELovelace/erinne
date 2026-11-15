"""Write Lumi Launch's angle table into both rules files (static/games/lumi-launch/rules.js and
mnemerynne/lumilaunch_rules.py): [cos, sin] for every whole degree from 15° to 75°, as literal numbers rounded to
six places.

Why a table: the server replays every launch with the same arithmetic as the page, and Math.cos in a browser isn't
promised to round exactly like Python's math.cos. The same decimal literal is the same number in both languages, so
both sides fly Lumi along exactly the same path.

Run from the repo root:  python python/make_lumi_launch_tables.py   (it only rewrites the ANGLES block)."""

from __future__ import annotations

import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOW, HIGH = 15, 75                                       # rules C.AIM_LOW .. C.AIM_LOW + C.AIM_STEPS


def pairs() -> list[tuple[str, str]]:
    """Each degree's cos and sin as six-place decimal text (the exact literals both files hold)."""
    out = []
    for degree in range(LOW, HIGH + 1):                  # one row a degree, 61 rows
        rad = math.radians(degree)
        out.append((f"{math.cos(rad):.6f}", f"{math.sin(rad):.6f}"))
    return out


def block(lang: str) -> str:
    """The table as code: a JS const or a Python list, six pairs a line."""
    rows = [f"[{c}, {s}]" if lang == "js" else f"({c}, {s})" for c, s in pairs()]
    lines = [", ".join(rows[i:i + 6]) for i in range(0, len(rows), 6)]
    if lang == "js":
        body = ",\n    ".join(lines)
        return f"  const ANGLES = [\n    {body},\n  ];\n"
    body = ",\n    ".join(lines)
    return f"ANGLES = (\n    {body},\n)\n"


def rewrite(path: Path, lang: str) -> None:
    """Swap the text between the ANGLES BEGIN and ANGLES END marker lines for a fresh table."""
    text = path.read_text(encoding="utf-8")
    marker = r"(ANGLES BEGIN[^\n]*\n)(.*?)(\s*(?://|#) ANGLES END)"
    new, count = re.subn(marker, lambda m: m.group(1) + block(lang) + m.group(3).lstrip("\n"),
                         text, flags=re.S)
    if count != 1:
        raise SystemExit(f"{path}: expected one ANGLES BEGIN/END block, found {count}")
    path.write_text(new, encoding="utf-8")
    print(f"wrote {HIGH - LOW + 1} angles into {path.relative_to(ROOT)}")


if __name__ == "__main__":
    rewrite(ROOT / "mnemerynne" / "web" / "static" / "games" / "lumi-launch" / "rules.js", "js")   # the page's copy
    rewrite(ROOT / "mnemerynne" / "lumilaunch_rules.py", "py")                                       # the server's copy
