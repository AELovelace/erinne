"""Write room mode's decorating spots into Lumi's house catalog (mnemerynne/lumipet_data/house.json).

Doll's design, 2026-11-19: each room of Lumi's flat can have its own wallpaper and floor (the kitchen's and the den's
share the bedroom's pieces, so one bought works in every room, and any of them can be painted a plain colour), and
every new thing in the flat comes in 15 styles: the fridge, her food bowl, the potion shelf, the kitchen counter, the
wardrobe, the terminal, the capsule machine and the paint pots.

The styles are 15 "sets" shared by every thing (a Galaxy fridge, a Galaxy wardrobe...), drawn by
static/games/lumi-pet/room-styles.js from the same set names. Each thing gets them split the way her other furniture
is: the Plain one free with the flat, five sold in the Shop, and nine only from capsules (three common, two uncommon,
two rare, one epic, one legendary).

Run from the project root; it replaces these spots each time (by id) and leaves everything else in the catalog alone:
    .venv/Scripts/python python/make_lumi_room_styles.py
"""

import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "mnemerynne" / "lumipet_data" / "house.json"

# The 15 sets, in the order the Home menu lists them: (id, name, how she gets it). Must match room-styles.js SETS.
SETS = [
    ("plain", "Plain", {"price": 0}),                       # free with the flat
    ("mint", "Mint retro", {"price": 80}),                  # the Shop
    ("strawberry", "Strawberry", {"price": 120}),
    ("wood", "Woodland", {"price": 150}),
    ("stripes", "Candy stripe", {"price": 180}),
    ("chrome", "Chrome", {"price": 240}),
    ("gingham", "Berry gingham", {"price": 0, "rarity": "common"}),   # capsules only
    ("sprout", "Sprout", {"price": 0, "rarity": "common"}),
    ("bubble", "Bubblegum", {"price": 0, "rarity": "common"}),
    ("kitty", "Kitty", {"price": 0, "rarity": "uncommon"}),
    ("frosting", "Frosted", {"price": 0, "rarity": "uncommon"}),
    ("neon", "Neon grid", {"price": 0, "rarity": "rare"}),
    ("holo", "Holographic", {"price": 0, "rarity": "rare"}),
    ("galaxy", "Galaxy", {"price": 0, "rarity": "epic"}),
    ("starlight", "Starlight", {"price": 0, "rarity": "legendary"}),
]

# The things, in the order the Home menu lists them: (spot id, its name in the menu, the noun in each piece's name).
THINGS = [
    ("fridge", "Kitchen: fridge", "fridge"),
    ("bowl", "Kitchen: food bowl", "food bowl"),
    ("counter", "Kitchen: counter", "counter"),
    ("potions", "Kitchen: potion shelf", "potion shelf"),
    ("wardrobe", "Den: wardrobe", "wardrobe"),
    ("terminal", "Den: terminal", "terminal"),
    ("capsules", "Den: capsule machine", "capsule machine"),
    ("paints", "Paint pots (every room)", "paint pots"),
]

# Another room's wall or floor: the bedroom's pieces (same_as), and paintable like the bedroom's own.
SHARED = [
    ("kitchen-wall", "Kitchen: wallpaper", "wallpaper"),
    ("kitchen-floor", "Kitchen: floor", "floor"),
    ("den-wall", "Den: wallpaper", "wallpaper"),
    ("den-floor", "Den: floor", "floor"),
]


def thing_spot(spot_id: str, name: str, noun: str) -> dict:
    """One thing's spot: a piece per set, "<set>-<thing>" ids so room-styles.js can tell which set to draw."""
    items = []
    for set_id, set_name, how in SETS:
        items.append({"id": f"{spot_id}-{set_id}", "name": f"{set_name} {noun}", **how})
    return {"id": spot_id, "name": name, "room": True, "items": items}


def layout(data: dict) -> str:
    """The catalog in its own hand-kept layout: a line per spot, then a line per piece, so a diff shows only what changed."""
    def one(value) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    lines = ["{", '  "spots": [']
    for n, spot in enumerate(data["spots"]):
        head = one({key: value for key, value in spot.items() if key != "items"})[:-1]   # the spot's own fields, open
        end = "," if n < len(data["spots"]) - 1 else ""
        if not spot["items"]:
            lines.append(f'    {head}, "items": []}}{end}')
            continue
        lines.append(f'    {head}, "items": [')
        for m, item in enumerate(spot["items"]):
            lines.append("      " + one(item) + ("," if m < len(spot["items"]) - 1 else "]}" + end))
    lines += ["  ]", "}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    ours = {spot_id for spot_id, _, _ in THINGS} | {spot_id for spot_id, _, _ in SHARED}
    spots = [spot for spot in data["spots"] if spot["id"] not in ours]      # everything else, untouched
    for spot in spots:
        if spot["id"] in ("wallpaper", "floor"):
            spot["paintable"] = True                                         # any room's wall or floor can be painted a colour
    by_id = {spot_id: (name, noun) for spot_id, name, noun in THINGS}
    for spot_id, name, base in SHARED[:2]:                                   # the kitchen: its walls, then its things
        spots.append({"id": spot_id, "name": name, "same_as": base, "items": []})
    spots += [thing_spot(spot_id, *by_id[spot_id]) for spot_id in ("fridge", "bowl", "counter", "potions")]
    for spot_id, name, base in SHARED[2:]:                                   # the den
        spots.append({"id": spot_id, "name": name, "same_as": base, "items": []})
    spots += [thing_spot(spot_id, *by_id[spot_id]) for spot_id in ("wardrobe", "terminal", "capsules", "paints")]
    data["spots"] = spots
    CATALOG.write_text(layout(data), encoding="utf-8")
    print(f"{len(THINGS)} things x {len(SETS)} styles and {len(SHARED)} shared walls/floors written to {CATALOG.name}")


if __name__ == "__main__":
    main()
