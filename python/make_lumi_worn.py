"""Adds Lumi's worn starter furniture to her house catalog (mnemerynne/lumipet_data/house.json), and prices the old free
starter pieces as tier 1.

Doll's design (2026-11-11): Lumi has just moved to a new town, so her room starts bare. Her own old things come out
of moving boxes, one box a day (lumipet.py unpack): a worn, well-loved set that is not the shop's set. The pieces a
new house used to start with (Dusk plum wallpaper, the cushion bed and so on) are now tier 1, bought in the Home tab.

Each worn piece is put first in its spot, priced 0, marked "worn" and numbered "box" (the order the boxes are
unpacked: the bed first, so she has somewhere to sleep). Spots whose first object is "nothing" or the outside
(curtains, the window view, the trophy bracket) keep it first and free; curtains get a worn piece after it, and the
view and the trophy get none. The art for every worn piece is in static/games/lumi-pet/house.js under the same ids.

Safe to run again: it changes nothing once the worn pieces are there. Run from anywhere:
    python python/make_lumi_worn.py
"""

from __future__ import annotations

import json
from math import ceil
from pathlib import Path

HOUSE = Path(__file__).resolve().parents[1] / "mnemerynne" / "lumipet_data" / "house.json"

# spot: (id, name), in the order the boxes come out of the van. The bed first: everything else can wait a night.
WORN = {
    "bed": ("bed-worn", "Mattress on the floor"),
    "lamp": ("lamp-worn", "Bare bulb on a stand"),
    "rug": ("rug-worn", "Thrift-shop rag rug"),
    "curtains": ("curtain-worn", "Pinned-up bedsheet"),
    "sidetable": ("table-worn", "Moving-box table"),
    "toychest": ("chest-worn", "Laundry basket of toys"),
    "plant": ("plant-worn", "Avocado pit in a jar"),
    "shelf": ("shelf-worn", "Plank and brick shelf"),
    "wallart": ("art-worn", "Taped-up postcard"),
    "ceiling": ("hang-worn", "Paper lantern, a bit torn"),
    "wallpaper": ("wall-worn", "Peeling old paper"),
    "floor": ("floor-worn", "Scuffed old boards"),
    "frame": ("frame-worn", "Chipped wooden frame"),
    "door": ("door-worn", "Creaky old door"),
}
KEEP_FREE = ("curtains", "view", "trophy")   # their first object is "nothing" or the outside: it stays first and free


def tier_one_price(spot: dict) -> int:
    """What an old starter piece costs now: half the spot's cheapest bought piece, rounded up to ten (at least 40)."""
    sold = [item["price"] for item in spot["items"] if item["price"] and not item.get("worn")]
    return max(40, ceil(min(sold) / 2 / 10) * 10)


def main() -> None:
    """Writes the worn pieces and the tier-1 prices into house.json, keeping its one-object-per-line layout."""
    data = json.loads(HOUSE.read_text(encoding="utf-8"))
    spots = {spot["id"]: spot for spot in data["spots"]}
    if any(item.get("worn") for spot in data["spots"] for item in spot["items"]):
        print("The worn set is already in", HOUSE)
        return
    for box, (spot_id, (item_id, name)) in enumerate(WORN.items(), start=1):
        spot = spots[spot_id]
        worn = {"id": item_id, "name": name, "price": 0, "worn": True, "box": box}   # free, but only from its moving box
        if spot_id in KEEP_FREE:
            spot["items"].insert(1, worn)                                           # after the free "nothing"
        else:
            first = spot["items"][0]
            first["price"] = tier_one_price(spot)                                   # the old starter is tier 1 now
            spot["items"].insert(0, worn)
        print(f"box {box:>2}: {spot_id:<10} {name}" + ("" if spot_id in KEEP_FREE else f"  ({spot['items'][1]['name']} now {spot['items'][1]['price']} coins)"))
    lines = ['{', '  "spots": [']
    for i, spot in enumerate(data["spots"]):                                        # one object per line, like the file always was
        head = {k: v for k, v in spot.items() if k != "items"}
        lines.append("    " + json.dumps(head, ensure_ascii=False)[:-1] + ', "items": [')
        lines += ["      " + json.dumps(item, ensure_ascii=False) + ("," if j < len(spot["items"]) - 1 else "]}" + ("," if i < len(data["spots"]) - 1 else ""))
                  for j, item in enumerate(spot["items"])]                         # the last one closes its spot, as before
    lines += ["  ]", "}", ""]
    HOUSE.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print("wrote", HOUSE)


if __name__ == "__main__":
    main()
