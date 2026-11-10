"""Make the Stardust Garden's flowers and decorations (mnemerynne/lumipet_data/garden.json).

The garden has 42 flowers on one ladder and 15 decoration slots of 15 things each. Typing 267 entries by hand would
hide the pattern, so this script writes them from the short tables below and leaves every other setting in
garden.json (the press, plots, helpers, minigames, gifts) exactly as it is. Run it from the repo root after changing
a table:

    .venv/Scripts/python.exe python/make_lumi_garden.py

Then run tests/test_lumigarden.py and tests/js/lumi_garden.test.mjs: they check the ladder's order, that every
flower looks different, and that garden-art.js can draw every shape and decoration named here.
"""

from __future__ import annotations

import colorsys
import json
import math
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "mnemerynne" / "lumipet_data" / "garden.json"

# (id, name, minutes, shape). Cut flowers first, quickest first: harvested once and planted again.
CUT = [
    ("moondaisy", "Moondaisy", 2, "daisy"), ("glowbell", "Glowbell", 5, "bell"), ("pixie-puff", "Pixie Puff", 8, "puff"),
    ("dewdrop-star", "Dewdrop Star", 12, "star"), ("starpoppy", "Starpoppy", 15, "poppy"), ("candy-spike", "Candy Spike", 20, "spike"),
    ("comet-clover", "Comet Clover", 30, "clover"), ("sugar-trumpet", "Sugar Trumpet", 40, "trumpet"), ("moon-mushroom", "Moon Mushroom", 50, "mushroom"),
    ("dusk-tulip", "Dusk Tulip", 60, "tulip"), ("little-sun", "Little Sun", 75, "sun"), ("heartstring", "Heartstring", 90, "hearts"),
    ("pebble-cactus", "Pebble Cactus", 105, "cactus"), ("nebula-rose", "Nebula Rose", 120, "rose"), ("mist-lotus", "Mist Lotus", 150, "lotus"),
    ("ribbon-orchid", "Ribbon Orchid", 180, "orchid"), ("snowball-bloom", "Snowball Bloom", 210, "cluster"), ("lantern-lily", "Lantern Lily", 240, "lily"),
    ("twilight-bell", "Twilight Bell", 300, "bell"), ("aurora-iris", "Aurora Iris", 360, "iris"), ("ember-poppy", "Ember Poppy", 420, "poppy"),
    ("frost-star", "Frost Star", 480, "star"), ("velvet-tulip", "Velvet Tulip", 540, "tulip"), ("honey-trumpet", "Honey Trumpet", 600, "trumpet"),
    ("dream-puff", "Dream Puff", 720, "puff"), ("royal-spike", "Royal Spike", 840, "spike"), ("fortune-clover", "Fortune Clover", 960, "clover"),
    ("glass-lily", "Glass Lily", 1080, "lily"), ("phoenix-daisy", "Phoenix Daisy", 1260, "daisy"), ("midnight-rose", "Midnight Rose", 1440, "rose"),
]
# Perennials come last on the ladder: trimmed, they grow back by themselves, for less powder a minute.
PERENNIAL = [
    ("moon-fern", "Moon Fern", 60, "fern"), ("fairy-mushroom", "Fairy Mushroom", 120, "mushroom"), ("prism-hydrangea", "Prism Hydrangea", 180, "cluster"),
    ("cuddle-cactus", "Cuddle Cactus", 240, "cactus"), ("star-wisteria", "Star Wisteria", 360, "wisteria"), ("great-sunflower", "Great Sunflower", 480, "sun"),
    ("lullaby-hearts", "Lullaby Hearts", 600, "hearts"), ("comet-orchid", "Comet Orchid", 720, "orchid"), ("silver-fern", "Silver Fern", 900, "fern"),
    ("dawn-iris", "Dawn Iris", 1080, "iris"), ("galaxy-wisteria", "Galaxy Wisteria", 1260, "wisteria"), ("eternity-lotus", "Eternity Lotus", 1440, "lotus"),
]
BOUTIQUE = {"nebula-rose", "mist-lotus", "aurora-iris", "dream-puff", "glass-lily", "midnight-rose",      # bought with starpowder, never coins
            "prism-hydrangea", "comet-orchid", "galaxy-wisteria", "eternity-lotus"}
PETALS = {"daisy": 10, "bell": 3, "puff": 12, "star": 5, "poppy": 4, "spike": 6, "clover": 3, "trumpet": 5, "mushroom": 3, "tulip": 3, "sun": 12,
          "hearts": 4, "cactus": 5, "rose": 7, "lotus": 8, "orchid": 5, "cluster": 9, "lily": 6, "iris": 6, "fern": 5, "wisteria": 7}
LAST_NEEDS = 90_000                     # powder a garden must have given before its last flower opens
PERENNIAL_SHARE = 0.55                  # a perennial's powder a minute, beside a cut flower of the same time

# Decorations: (slot id, slot name, first price in starpowder, three things it can be). The first is sold in five
# colours; the second and third come only from the Seedling Capsule, in the colours below.
SLOTS = [
    ("fence", "Fence", 300, ("picket fence", "bamboo fence", "crystal fence")),
    ("path", "Path", 400, ("stepping stones", "brick path", "mossy path")),
    ("edging", "Plot edging", 600, ("glowing border", "shell border", "ribbon border")),
    ("statue", "Statue", 800, ("garden gnome", "bunny statue", "fairy statue")),
    ("scarecrow", "Scarecrow", 1000, ("scarecrow Lumi", "pumpkin scarecrow", "robot scarecrow")),
    ("press", "Press", 1500, ("painted press", "ribboned press", "crystal press")),
    ("lamps", "Lamps", 1200, ("firefly lamps", "paper lanterns", "mushroom lamps")),
    ("arch", "Arch", 2000, ("rose arch", "moon gate", "ribbon arch")),
    ("fountain", "Water", 2500, ("bird bath", "little fountain", "wishing well")),
    ("bench", "Seat", 900, ("garden bench", "swing seat", "picnic blanket")),
    ("tree", "Tree", 3000, ("blossom tree", "willow", "pine")),
    ("bush", "Bush", 700, ("round topiary", "star topiary", "berry bush")),
    ("banner", "Banner", 500, ("bunting", "windchime", "pinwheels")),
    ("critters", "Little visitors", 1500, ("butterflies", "fireflies", "ladybirds")),
    ("sky", "In the sky", 4000, ("crescent moon", "hot-air balloon", "kite")),
]
SOLD = [("Snowy", "#f6f1ff", "#b9a8d6", 1.0), ("Rosy", "#f9a8c9", "#ad3c79", 1.2), ("Minty", "#9fe0c4", "#3f8f77", 1.4),
        ("Lilac", "#c9a8ff", "#8060af", 1.6), ("Golden", "#ffdc83", "#c9922e", 2.0)]
CAPSULE = [  # (which of the three things, colour name, a, b, rarity)
    (1, "Moonlit", "#9fd8ff", "#4a6fb0", "common"), (1, "Sunset", "#ffb38a", "#d9603f", "common"), (1, "Berry", "#e58ad0", "#8a2f7a", "rare"),
    (1, "Ocean", "#70ebea", "#2a8f9f", "rare"), (1, "Starry", "#b9a8ff", "#3d2f7a", "epic"),
    (2, "Frosted", "#e6f6ff", "#9fc6e8", "common"), (2, "Candy", "#ffc2e2", "#ff6b8b", "common"), (2, "Emerald", "#8be9a8", "#2f7a4f", "rare"),
    (2, "Aurora", "#7fe0c8", "#b48cff", "epic"), (2, "Royal", "#ffe9a8", "#8a5a00", "legendary"),
]
CAPSULE_RULES = {"name": "Seedling Capsule", "price": 600, "odds": {"common": 60, "rare": 27, "epic": 10, "legendary": 3},
                 "sell": {"common": 150, "rare": 400, "epic": 1200, "legendary": 4000}}


def nice(value: float) -> int:
    """A round-looking number: two significant figures."""
    if value < 100:
        return int(round(value / 5) * 5)
    scale = 10 ** (len(str(int(value))) - 2)
    return int(round(value / scale) * scale)


def colour(turn: float, light: float, rich: float) -> str:
    red, green, blue = colorsys.hls_to_rgb(turn % 1, light, rich)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def flowers() -> list[dict]:
    ladder = [(*entry, "cut") for entry in CUT] + [(*entry, "perennial") for entry in PERENNIAL]
    grow = (LAST_NEEDS / 40) ** (1 / (len(ladder) - 2))            # needs climb evenly from 40 to LAST_NEEDS
    out, used = [], {}
    for rung, (ident, name, minutes, shape, sort) in enumerate(ladder):
        rate = 3 + 3 * min(1.0, math.log(minutes / 2) / math.log(720))    # 3 powder a minute for a daisy, 6 for a day-long bloom
        powder = round(minutes * rate * (PERENNIAL_SHARE if sort == "perennial" else 1))
        needs = 0 if rung == 0 else nice(40 * grow ** (rung - 1))
        boutique = ident in BOUTIQUE
        second = used.get(shape, 0)                                 # the second flower of a shape gets more petals and stands taller
        used[shape] = second + 1
        turn = rung * 0.381966                                      # the golden angle: neighbours on the ladder never share a colour
        out.append({"id": ident, "name": name, "sort": sort, "minutes": minutes, "powder": powder,
                    "coins": 0 if boutique or rung == 0 else 20 + 5 * rung, "stardust": nice(needs * 0.3) if boutique else 0,
                    "level": 1, "needs": needs, "shape": shape, "petals": PETALS[shape] + 2 * second, "size": round(0.9 + 0.3 * second + 0.004 * rung, 2),
                    "colour": "#f6f1ff" if rung == 0 else colour(turn, 0.74, 0.82), "heart": "#ffdc83" if rung == 0 else colour(turn + 0.42, 0.62, 0.78)})
    return out


def decor() -> dict:
    slots = []
    for slot, slot_name, price, things in SLOTS:
        items = [{"id": f"{slot}-0-{name.lower()}", "name": f"{name} {things[0]}", "thing": 0, "a": a, "b": b, "stardust": nice(price * times), "rarity": "shop"}
                 for name, a, b, times in SOLD]
        items += [{"id": f"{slot}-{thing}-{name.lower()}", "name": f"{name} {things[thing]}", "thing": thing, "a": a, "b": b, "stardust": 0, "rarity": rarity}
                  for thing, name, a, b, rarity in CAPSULE]
        slots.append({"id": slot, "name": slot_name, "things": list(things), "items": items})
    return {"capsule": CAPSULE_RULES, "slots": slots}


def main() -> None:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    data["flowers"], data["decor"] = flowers(), decor()
    data.pop("cosmetics", None)                           # the seven old cosmetics are now decorations (lumigarden.LEGACY_DECOR)
    lines = ["{"]                                         # one entry a line, so a diff shows exactly which flower changed
    for at, (key, value) in enumerate(data.items()):
        comma = "," if at < len(data) - 1 else ""
        if key in ("flowers", "helpers", "upgrades"):
            rows = ",\n".join("  " + json.dumps(row, ensure_ascii=False) for row in value)
            lines.append(f' "{key}": [\n{rows}\n ]{comma}')
        elif key == "decor":
            slots = ",\n".join('   {"id": %s, "name": %s, "things": %s, "items": [\n%s\n   ]}' % (
                json.dumps(slot["id"]), json.dumps(slot["name"]), json.dumps(slot["things"]),
                ",\n".join("    " + json.dumps(item, ensure_ascii=False) for item in slot["items"])) for slot in value["slots"])
            lines.append(f' "decor": {{\n  "capsule": {json.dumps(value["capsule"])},\n  "slots": [\n{slots}\n  ]\n }}{comma}')
        else:
            lines.append(f' "{key}": {json.dumps(value, ensure_ascii=False)}{comma}')
    lines.append("}")
    PATH.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(data['flowers'])} flowers, {sum(len(slot['items']) for slot in data['decor']['slots'])} decorations")


if __name__ == "__main__":
    main()
