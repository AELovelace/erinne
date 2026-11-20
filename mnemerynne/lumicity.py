"""Lumi City (Doll's design, 2026-11-19; docs/plans/lumi-city.md): Lumi Town grown into one long city. LefTown on the
left, Downtown in the middle, Right-City on the right, and a gate at each far end. Everyone has a free flat in Lumi
Heights (apartment()), and may buy one plot of land for a house: from the real-estate office, or from another player
who listed theirs. A town that fills up grows a new block at its gate, so nothing already built ever moves.

The map is lumipet_data/city.json, shared with the page's city-map.js: tiles in rows, Downtown's own rows, one block
template every block repeats, and a gate. load_city() checks it when this module loads (CITY), so a bad edit stops the
server at start. The houses and yard pieces are lumipet_data/estate.json (ESTATE, load_estate()): 30 house styles, five
yard slots with 15 pieces each (5 sold, 10 only from the Keepsake Capsule), the capsule, and what a capsule piece is
worth. Those pieces are Lumi City's alone: they never join Lumi Pet's Star or Moon Capsules (lumipet._PRIZES) or her
move-in refund.

Who owns what is the games database (migrations/games/0003_lumi_city.sql): lumi_city_towns (how many blocks each town
has) and lumi_plots (one row per owned plot; a lot with no row is office land). What she owns to put on a plot (house
styles, yard pieces) and her home town are in her pet save (lumipet `estate`, `home_town`). Prices are worked out here
and nowhere else (suggested()): the page only ever shows them.

Every function that changes something runs inside its caller's transaction (db.transaction) and reads inside it, so a
plot, its coins and her save are written together or not at all. Refusals are lumipet.PetError, in Lumi's own voice.
Coins show in the ledger as game "lumi-city": lumi-city_plot (from the office), lumi-city_trade (player to player),
lumi-city_sell (the office buying one back), lumi-city_style, lumi-city_yard, lumi-city_capsule and lumi-city_buyback."""

from __future__ import annotations

import json
import math
import random
import re
import sqlite3
from collections import deque
from pathlib import Path

from mnemerynne import coins, lumipet, users
from mnemerynne.lumipet import PetError

GAME = "lumi-city"                                        # the coin ledger's game name for everything bought or sold here
TOWNS = ("left", "right")                                 # LefTown and Right-City (city.json "towns")
LOT_KEYS = "abcdefgh"                                     # the template's eight lots, as letters in its rows
MAX_BLOCKS = 999                                          # a plot id has at most three digits of block (web/games.py's pattern)
_ID = re.compile(r"^[a-z][a-z0-9-]{1,31}$")               # an estate.json id: a house style or a yard piece
_PLACE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")            # a Downtown place's id, the same as Lumi Town's `at` (TownHere)
_PLOT = re.compile(r"^([lr])-(\d{1,3})-([1-8])$")         # a plot id: "l-3-5" is LefTown, block 3, lot 5
PRICE_KEYS = ("base", "decay", "floor", "avenue", "round", "sell_office", "list_min", "list_max")   # city.json "price"
SLOTS = ("fence", "mailbox", "path", "lamp", "ornament")  # estate.json's yard slots, in this order
STYLES_COUNT, YARD_SHOP, YARD_CAPSULE = 30, 5, 10          # 30 house styles; per slot, 5 pieces sold and 10 from the capsule


# --------------------------------------------------------------------------- the map (city.json)
def _grid(data: dict, section: dict, name: str, known: set[str]) -> list[str]:
    """One section's rows (Downtown, the block template or the gate), checked: `rows` of them, all one width, and only
    symbols the file names. Returns the rows."""
    rows = section.get("rows") if isinstance(section, dict) else None
    if not isinstance(rows, list) or len(rows) != data["rows"] or not all(isinstance(r, str) and r for r in rows):
        raise ValueError(f"Lumi City's {name} needs {data['rows']} rows of tiles.")
    if len({len(r) for r in rows}) != 1:                  # every row the same width, or the map would be ragged
        raise ValueError(f"Lumi City's {name} has rows of different widths.")
    strange = {ch for r in rows for ch in r} - known      # a symbol nobody said what it is
    if strange:
        raise ValueError(f"Lumi City's {name} uses unknown symbols: {''.join(sorted(strange))}.")
    return rows


def _rectangle(rows: list[str], key: str, name: str) -> tuple[int, int, int, int]:
    """Where a letter sits in a section's rows, as (top, left, bottom, right), inclusive. It must be one solid
    rectangle: a lot or a building is a single footprint, never two pieces or a ragged shape."""
    cells = [(y, x) for y, row in enumerate(rows) for x, ch in enumerate(row) if ch == key]   # every tile with this letter
    if not cells:
        raise ValueError(f"Lumi City's {name} has no {key!r} on the map.")
    top, bottom = min(y for y, _ in cells), max(y for y, _ in cells)
    left, right = min(x for _, x in cells), max(x for _, x in cells)
    if len(cells) != (bottom - top + 1) * (right - left + 1):   # anything missing from the box: not one solid rectangle
        raise ValueError(f"Lumi City's {name}: {key!r} must be one solid rectangle.")
    return top, left, bottom, right


def _door(rows: list[str], box: tuple[int, int, int, int], walk: str, name: str, key: str) -> tuple[int, int]:
    """A footprint's front door: the tile just below its bottom row, under its middle (first column + width // 2),
    the same rule city-map.js uses. It must be somewhere a Lumi can stand. Returns (column, row) in the section."""
    top, left, bottom, right = box
    x, y = left + (right - left + 1) // 2, bottom + 1     # under the middle, one row down
    if y >= len(rows) or rows[y][x] not in walk:
        raise ValueError(f"Lumi City's {name}: the door of {key!r} must open onto a street or pavement.")
    return x, y


def _connected(rows: list[str], walk: str) -> bool:
    """Whether every walkable tile can reach every other one (a breadth-first walk from the first)."""
    tiles = {(x, y) for y, row in enumerate(rows) for x, ch in enumerate(row) if ch in walk}   # everywhere a Lumi may stand
    if not tiles:
        return False
    start = next(iter(tiles))
    seen, queue = {start}, deque([start])
    while queue:                                          # flood out one step at a time, up, down, left and right
        x, y = queue.popleft()
        for step in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if step in tiles and step not in seen:
                seen.add(step)
                queue.append(step)
    return len(seen) == len(tiles)                        # nothing left unreached


def load_city(path: Path | None = None) -> dict:
    """The city's layout, validated (it's read here and by the page's city-map.js, so a mistake must stop the server at
    start, not strand a Lumi somewhere she can't walk out of). Raises ValueError naming what's wrong."""
    data = json.loads((path or Path(__file__).parent / "lumipet_data" / "city.json").read_text(encoding="utf-8"))
    if not isinstance(data.get("rows"), int) or not 3 <= data["rows"] <= 200:
        raise ValueError("Lumi City needs a number of rows.")
    tile = data.get("tile")
    if not isinstance(tile, list) or len(tile) != 2 or not all(isinstance(n, int) and n > 0 for n in tile):
        raise ValueError("Lumi City needs a tile size: [width, height].")
    walk, block = data.get("walk"), data.get("block")
    if not isinstance(walk, str) or not walk or not isinstance(block, str) or set(walk) & set(block):
        raise ValueError("Lumi City needs its walkable and blocking symbols, and none can be both.")
    if not isinstance(data.get("symbols"), dict):
        raise ValueError("Lumi City needs its symbols named.")
    known = set(data["symbols"]) | set(walk) | set(block)  # every symbol a section may use
    down, template, gate = data.get("downtown"), data.get("template"), data.get("gate")
    down_rows = _grid(data, down, "Downtown", known)
    block_rows = _grid(data, template, "block template", known)
    gate_rows = _grid(data, gate, "gate", known)
    if not isinstance(down.get("x0"), int) or not down.get("name") or not down.get("street"):
        raise ValueError("Lumi City's Downtown needs its first column (x0), a name and a street.")

    # The avenue runs the whole length of the city: through every block, Downtown and both gates.
    avenue = data.get("avenue")
    if not isinstance(avenue, list) or not avenue or not all(isinstance(y, int) and 0 <= y < data["rows"] for y in avenue):
        raise ValueError("Lumi City needs its avenue's rows.")
    for name, rows in (("block template", block_rows), ("Downtown", down_rows), ("gate", gate_rows)):
        if any(ch not in walk for y in avenue for ch in rows[y]):
            raise ValueError(f"Lumi City's avenue must be walkable all the way across the {name}.")

    # The eight lots of a block: each one solid rectangle with its door on a street.
    lots = template.get("lots")
    if not isinstance(lots, list) or sorted(lot.get("lot") for lot in lots if isinstance(lot, dict)) != list(range(1, 9)):
        raise ValueError("Lumi City's block template needs lots 1 to 8.")
    if sorted(lot.get("key", "") for lot in lots) != list(LOT_KEYS):
        raise ValueError("Lumi City's lots must be the letters a to h, one each.")
    for lot in lots:
        if lot.get("frontage") not in ("avenue", "lane"):
            raise ValueError(f"Lumi City's lot {lot['lot']} faces the avenue or a lane.")
        _door(block_rows, _rectangle(block_rows, lot["key"], "block template"), walk, "block template", lot["key"])
    if any(ch in LOT_KEYS for row in down_rows + gate_rows for ch in row):
        raise ValueError("Lumi City's lots belong in the block template, not Downtown or the gate.")

    # Downtown's places: each a solid building with its door on a street, and an id the town's pages can use.
    places = down.get("places")
    if not isinstance(places, dict) or not places:
        raise ValueError("Lumi City's Downtown needs its places.")
    ids = set()
    for key, place in places.items():
        if not isinstance(place, dict) or not _PLACE.match(str(place.get("id", ""))) or place["id"] in ids:
            raise ValueError(f"Lumi City's Downtown place {key!r} needs its own id.")
        if place.get("town") not in (None, *TOWNS):
            raise ValueError(f"Lumi City's {place['id']} belongs to LefTown (left), Right-City (right) or nobody.")
        ids.add(place["id"])
        _door(down_rows, _rectangle(down_rows, key, "Downtown"), walk, "Downtown", key)

    # The gate's arrival tile, where someone coming into town is put down.
    arrive = gate.get("arrive")
    if not isinstance(arrive, list) or len(arrive) != 2 or not all(isinstance(n, int) for n in arrive) \
            or not 0 <= arrive[1] < data["rows"] or not 0 <= arrive[0] < len(gate_rows[0]) or gate_rows[arrive[1]][arrive[0]] not in walk:
        raise ValueError("Lumi City's gate needs an arrival tile [column, row] on a street.")

    # Everywhere walkable joins up, in the smallest city: a gate, a block, Downtown, a block and a gate.
    city = [g + b + d + b + g for g, b, d in zip(gate_rows, block_rows, down_rows)]
    if not _connected(city, walk):
        raise ValueError("Lumi City has streets that can't be reached from the rest of the city.")
    if not _connected([b + b for b in block_rows], walk):  # two blocks side by side join up too (a town that has grown)
        raise ValueError("Lumi City's blocks don't join up when a town grows.")

    # The two towns' names and street names, the start size, and the price curve.
    towns = data.get("towns")
    for town, district in zip(TOWNS, ("l", "r")):
        about = towns.get(town) if isinstance(towns, dict) else None
        if not isinstance(about, dict) or not about.get("name") or not about.get("avenue") or about.get("district") != district:
            raise ValueError(f"Lumi City's {town} town needs a name, an avenue and district {district!r}.")
        if not isinstance(about.get("lanes"), list) or not about["lanes"] or not all(isinstance(n, str) and n for n in about["lanes"]):
            raise ValueError(f"Lumi City's {about['name']} needs at least one lane name.")
    if not isinstance(data.get("start_blocks"), int) or not 1 <= data["start_blocks"] <= MAX_BLOCKS:
        raise ValueError("Lumi City needs a number of start_blocks (1 or more).")
    price = data.get("price")
    if not isinstance(price, dict) or any(not isinstance(price.get(k), (int, float)) or isinstance(price.get(k), bool) for k in PRICE_KEYS):
        raise ValueError(f"Lumi City's price needs {', '.join(PRICE_KEYS)}.")
    if price["base"] <= 0 or not 0 < price["decay"] <= 1 or price["floor"] < 0 or price["avenue"] <= 0 or price["round"] < 1 \
            or not 0 < price["sell_office"] <= 1 or not 0 < price["list_min"] <= 1 <= price["list_max"]:
        raise ValueError("Lumi City's prices are out of range (base > 0, 0 < decay <= 1, round >= 1, sell_office up to 1, list_min <= 1 <= list_max).")
    return data


# --------------------------------------------------------------------------- the houses and yards (estate.json)
def load_estate(path: Path | None = None) -> dict:
    """The house styles, yard slots and pieces, and the Keepsake Capsule, validated (estate-art.js draws them). Lumi
    City's own: none of these join Lumi Pet's capsules or her move-in refund. Raises ValueError naming what's wrong."""
    data = json.loads((path or Path(__file__).parent / "lumipet_data" / "estate.json").read_text(encoding="utf-8"))
    seen: set[str] = set()                                # every id, across styles and yard pieces

    def piece(item, where: str) -> None:                  # one style or yard piece: an id, a name, an icon, a price, a hint
        if not isinstance(item, dict) or not _ID.match(str(item.get("id", ""))) or item["id"] in seen:
            raise ValueError(f"Lumi City's {where} has a bad or repeated id.")
        seen.add(item["id"])
        if not isinstance(item.get("name"), str) or not item["name"] or not isinstance(item.get("icon"), str) or not item["icon"] \
                or not isinstance(item.get("hint"), str):
            raise ValueError(f"Lumi City's {item['id']} needs a name, an icon and a hint.")
        if not isinstance(item.get("price"), int) or isinstance(item["price"], bool) or not 0 <= item["price"] <= 100_000:
            raise ValueError(f"Lumi City's {item['id']} needs a price from 0 to 100000.")

    styles = data.get("styles")
    if not isinstance(styles, list) or len(styles) != STYLES_COUNT:
        raise ValueError(f"Lumi City needs exactly {STYLES_COUNT} house styles.")
    for style in styles:
        piece(style, "house styles")
    if styles[0]["price"] != 0 or sum(1 for style in styles if style["price"] == 0) != 1:
        raise ValueError("Lumi City's first house style is the free starter, and the only free one.")

    slots = data.get("slots")
    if not isinstance(slots, list) or [s.get("id") if isinstance(s, dict) else None for s in slots] != list(SLOTS) \
            or not all(s.get("name") and s.get("icon") for s in slots):
        raise ValueError(f"Lumi City's yard slots must be {', '.join(SLOTS)}, in that order, each with a name and an icon.")

    yard = data.get("yard")
    if not isinstance(yard, dict) or set(yard) != set(SLOTS):
        raise ValueError("Lumi City's yard needs pieces for every slot.")
    for slot in SLOTS:
        items = yard[slot]
        if not isinstance(items, list) or len(items) != YARD_SHOP + YARD_CAPSULE:
            raise ValueError(f"Lumi City's {slot} needs {YARD_SHOP + YARD_CAPSULE} pieces.")
        for item in items:
            piece(item, f"{slot} pieces")
            if item["price"] and "rarity" in item:        # sold, or from the capsule: never both
                raise ValueError(f"Lumi City's {item['id']} can't be sold and a capsule piece too.")
            if not item["price"] and item.get("rarity") not in lumipet.RARITIES:
                raise ValueError(f"Lumi City's {item['id']} is free, so it must be a capsule piece with a rarity.")
        if sum(1 for item in items if item["price"]) != YARD_SHOP:
            raise ValueError(f"Lumi City's {slot} needs {YARD_SHOP} pieces for sale and {YARD_CAPSULE} from the capsule.")

    capsule = data.get("capsule")
    if not isinstance(capsule, dict) or not capsule.get("name") or not isinstance(capsule.get("price"), int) \
            or not 1 <= capsule["price"] <= 100_000 or not isinstance(capsule.get("odds"), dict) \
            or tuple(capsule["odds"]) != lumipet.RARITIES \
            or any(not isinstance(v, int) or v < 0 for v in capsule["odds"].values()) or sum(capsule["odds"].values()) != 100:
        raise ValueError("The Keepsake Capsule needs a name, a price and odds for every rarity that add up to 100.")
    for rarity, chance in capsule["odds"].items():         # a rarity it can roll must have something to give
        if chance and not any(item.get("rarity") == rarity for slot in SLOTS for item in yard[slot]):
            raise ValueError(f"The Keepsake Capsule can roll {rarity}, but has no {rarity} pieces.")
    values = data.get("values")
    if not isinstance(values, dict) or tuple(values) != lumipet.RARITIES \
            or any(not isinstance(v, int) or not 0 <= v <= 100_000 for v in values.values()):
        raise ValueError("Lumi City needs a value for every rarity, so a repeat can pay back half of it.")
    return data


CITY = load_city()
ESTATE = load_estate()
STARTER = ESTATE["styles"][0]["id"]                       # the free starter house every new plot comes with
_STYLES = {style["id"]: style for style in ESTATE["styles"]}
_YARD = {item["id"]: {**item, "slot": slot} for slot in SLOTS for item in ESTATE["yard"][slot]}   # every yard piece, with its slot
_KEEPSAKES = [item for item in _YARD.values() if "rarity" in item]   # the Keepsake Capsule's pool: capsule-only yard pieces
_LOTS = {lot["lot"]: lot for lot in CITY["template"]["lots"]}       # lot number → its letter and frontage
_TOWN_OF = {"l": "left", "r": "right"}                    # a plot id's letter → its town


# --------------------------------------------------------------------------- numbers: flats, lots and prices
def apartment(user_id: int) -> dict:
    """Her flat in Lumi Heights: 8 doors a floor, in account order, so the numbers never change. Account 1 is 1A,
    account 9 is 2A, account 12 is 2D."""
    floor, door = 1 + (user_id - 1) // 8, (user_id - 1) % 8 + 1   # which floor, and which door on it (1..8)
    return {"floor": floor, "door": door, "label": f"{floor}{'ABCDEFGH'[door - 1]}"}


def towns(conn: sqlite3.Connection) -> dict[str, int]:
    """How many blocks each town has, starting both at city.json's start_blocks the first time. Writes: call inside
    a transaction."""
    for town in TOWNS:                                    # seeded on demand; an existing town keeps its size
        conn.execute("INSERT OR IGNORE INTO lumi_city_towns(town, blocks) VALUES (?, ?)", (town, CITY["start_blocks"]))
    return _blocks(conn)


def _blocks(conn: sqlite3.Connection) -> dict[str, int]:
    """How many blocks each town has, without writing (a town not seeded yet reads as start_blocks)."""
    found = {row["town"]: row["blocks"] for row in conn.execute("SELECT town, blocks FROM lumi_city_towns")}
    return {town: found.get(town, CITY["start_blocks"]) for town in TOWNS}


def plot_id(town: str, block: int, lot: int) -> str:
    """A plot's id: "l-3-5" (LefTown, block 3, lot 5) or "r-1-2"."""
    return f"{town[0]}-{block}-{lot}"


def lots(blocks: dict[str, int]) -> list[str]:
    """Every lot id in the city as it is now: LefTown's, block 1 (by Downtown) outward, lots 1 to 8, then Right-City's."""
    return [plot_id(town, block, lot) for town in TOWNS for block in range(1, blocks[town] + 1) for lot in range(1, 9)]


def parse(plot: str | None) -> tuple[str, int, int]:
    """A plot id as (town, block, lot). Raises PetError when it isn't one."""
    found = _PLOT.match(plot or "")
    if not found or int(found[2]) < 1:
        raise PetError("That isn't a plot in Lumi City.")
    return _TOWN_OF[found[1]], int(found[2]), int(found[3])


def _round_to(step: int, value: float) -> int:
    """`value` to the nearest `step` (halves round up), as a whole number of coins."""
    return int(math.floor(value / step + 0.5)) * step


def suggested(town: str, block: int, lot: int) -> int:
    """The office's price for a lot: city.json's base, smaller by `decay` for each block out from Downtown, more for one
    facing the avenue, rounded, and never below the floor. The same in both towns. The only place a price is worked out."""
    price = CITY["price"]
    frontage = price["avenue"] if _LOTS[lot]["frontage"] == "avenue" else 1   # the avenue side costs more
    return max(int(price["floor"]), _round_to(int(price["round"]), price["base"] * price["decay"] ** (block - 1) * frontage))


def _roman(n: int) -> str:
    """A small number in Roman numerals (II, III, IV...), for street names that come round again."""
    out = ""
    for value, letters in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                           (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:                                 # as many of this numeral as fit
            out += letters
            n -= value
    return out


def street(town: str, block: int) -> str:
    """The cross street of a town's block: its lanes in order, then round again with "II", "III" and so on."""
    lanes = CITY["towns"][town]["lanes"]
    name, lap = lanes[(block - 1) % len(lanes)], (block - 1) // len(lanes) + 1   # which name, and how many times round
    return name if lap == 1 else f"{name} {_roman(lap)}"


# --------------------------------------------------------------------------- what the page sees
def _yard_of(row) -> dict[str, str]:
    """What stands in a plot's yard: {slot: piece id or ""} for every slot, anything retired or misplaced left out."""
    try:
        saved = json.loads(row["yard"]) if row is not None else {}
    except ValueError:
        saved = {}                                        # an unreadable yard is an empty one
    saved = saved if isinstance(saved, dict) else {}
    return {slot: saved[slot] if _YARD.get(saved.get(slot), {}).get("slot") == slot else "" for slot in SLOTS}


def _owner(conn: sqlite3.Connection, user_id: int, names: dict) -> dict:
    """A plot's owner for the page: their id and name. An account that's gone quiet still owns it, as "a neighbour"."""
    if user_id not in names:                              # look each owner up once per view
        person = users.get_user_by_id(conn, user_id)
        names[user_id] = person["username"] if person is not None and person["active"] else "a neighbour"
    return {"id": user_id, "name": names[user_id]}


def view(conn: sqlite3.Connection, user_id: int) -> dict:
    """The whole city for the page: each town's size, every lot (who owns it, what stands there, its price and any
    listing), what this person has (her plot, home town, flat, coins, styles and yard pieces), the catalog and the
    layout. Only reads, so it may run in a read transaction."""
    blocks = _blocks(conn)
    rows = {(row["town"], row["block"], row["lot"]): row for row in conn.execute("SELECT * FROM lumi_plots")}   # every owned plot
    names: dict[int, str] = {}
    out, mine = [], None
    for plot in lots(blocks):
        town, block, lot = parse(plot)
        row = rows.get((town, block, lot))                # None: office land
        if row is not None and row["user_id"] == user_id:
            mine = plot
        out.append({"id": plot, "town": town, "block": block, "lot": lot, "frontage": _LOTS[lot]["frontage"],
                    "suggested": suggested(town, block, lot),
                    "owner": _owner(conn, row["user_id"], names) if row is not None else None,
                    "style": (row["style"] if row["style"] in _STYLES else STARTER) if row is not None else "",
                    "yard": _yard_of(row), "list_price": row["list_price"] if row is not None else None,
                    "version": row["version"] if row is not None else None})
    pet = lumipet._peek(conn, user_id) or {}               # her save as it is (no catching up: this only reads)
    estate = lumipet.estate_of(pet)
    me = {"id": user_id, "plot": mine, "home_town": pet.get("home_town"), "apt": apartment(user_id),
          "coins": coins.peek(conn, user_id), "styles": [s for s in estate["styles"] if s in _STYLES],
          "yard": [y for y in estate["yard"] if y in _YARD]}
    return {"towns": blocks, "lots": out, "me": me, "catalog": ESTATE, "layout": CITY}


# --------------------------------------------------------------------------- what she can do
def _plot_of(conn: sqlite3.Connection, user_id: int):
    """Her plot's row, or None."""
    return conn.execute("SELECT * FROM lumi_plots WHERE user_id = ?", (user_id,)).fetchone()


def _row(conn: sqlite3.Connection, town: str, block: int, lot: int):
    """A lot's row (None: office land)."""
    return conn.execute("SELECT * FROM lumi_plots WHERE town = ? AND block = ? AND lot = ?", (town, block, lot)).fetchone()


def _id_of(row) -> str:
    """A row's plot id."""
    return plot_id(row["town"], row["block"], row["lot"])


def _home_line(row) -> str:
    """Lumi refusing a second plot, naming where her home is."""
    return f"I've already got a home on {street(row['town'], row['block'])}!"


def _own(estate: dict, key: str, item: str) -> None:
    """Add something to what she owns (styles or yard), once."""
    if item not in estate[key]:
        estate[key].append(item)


def act(conn: sqlite3.Connection, user_id: int, action: str, *, plot: str | None = None, price: int | None = None,
        version: int | None = None, item: str | None = None, slot: str | None = None, rng=None) -> dict:
    """One thing she chose to do in Lumi City: pick a home town, buy or sell a plot, list it, buy and place house
    styles and yard pieces, or open the Keepsake Capsule. Call inside a transaction: the plot, any coins and her save
    are written together or not at all, and a refusal (PetError) has written nothing. Returns {"said": {..., "text"},
    ...view()}."""
    now = lumipet.now_ms()
    blocks = towns(conn)                                  # both towns exist from here on
    pet = lumipet.load(conn, user_id, now)                # her save, caught up (a new Lumi if she hasn't one yet)
    estate = lumipet.estate_of(pet)
    have = _plot_of(conn, user_id)                        # her plot, if she owns one
    said: dict = {}

    if action == "home_town":                             # which town's apartment building is hers
        if item not in TOWNS:
            raise PetError("Which town? LefTown or Right-City!")
        if have is not None and have["town"] != item:     # her house decides it while she has one
            raise PetError(f"My house is in {CITY['towns'][have['town']]['name']}, so that's my home town!")
        pet["home_town"] = item
        said = {"home_town": item, "text": f"{CITY['towns'][item]['name']} it is! That's my home town now."}

    elif action == "buy":                                 # office land, at the suggested price
        town, block, lot = parse(plot)
        if block > blocks[town]:
            raise PetError("That plot isn't built yet!")
        taken = _row(conn, town, block, lot)
        if taken is not None:
            raise PetError("That one's already mine!" if taken["user_id"] == user_id else "Someone already lives there!")
        if have is not None:
            raise PetError(_home_line(have))
        cost = suggested(town, block, lot)
        if not coins.spend(conn, user_id, cost, GAME, "plot"):   # checked first, so nothing has changed yet
            raise PetError(f"I can't afford that yet! It's {cost} coins.")
        conn.execute("INSERT INTO lumi_plots(town, block, lot, user_id, style, yard, paid, bought_at) VALUES (?, ?, ?, ?, ?, '{}', ?, ?)",
                     (town, block, lot, user_id, STARTER, cost, now))
        pet["home_town"] = town                           # her house's town is her home town
        _own(estate, "styles", STARTER)                   # the free starter house comes with it
        grew = _grow(conn, town, blocks[town])            # the last office lot in town: a new block at the gate
        said = {"bought": plot, "paid": cost, "expanded": grew,
                "text": f"A home of my own on {street(town, block)}! The starter cottage is ready."}

    elif action == "buy_listing":                         # someone else's plot, at the price they listed
        town, block, lot = parse(plot)
        row = _row(conn, town, block, lot)
        if row is None or row["list_price"] is None or row["list_price"] != price or row["version"] != version:
            raise PetError("Someone changed the price — have another look!")   # (sold, unlisted, repriced or changed since she looked)
        if row["user_id"] == user_id:
            raise PetError("That's my own plot! I can take it off the market instead.")
        if have is not None:
            raise PetError(_home_line(have))
        if coins.peek(conn, user_id) < price:             # said kindly before the ledger is touched
            raise PetError(f"I can't afford that yet! It's {price} coins.")
        if not coins.transfer(conn, user_id, row["user_id"], price, GAME, "trade", ref=f"lumi-plot:{plot}:{row['bought_at']}:{version}"):
            raise PetError("Someone changed the price — have another look!")   # (that very sale has already gone through)
        conn.execute("UPDATE lumi_plots SET user_id = ?, list_price = NULL, listed_at = NULL, version = version + 1, style = ?, "
                     "yard = '{}', paid = ?, bought_at = ? WHERE town = ? AND block = ? AND lot = ?",
                     (user_id, STARTER, price, now, town, block, lot))   # hers now; the seller keeps their own styles and yard pieces
        pet["home_town"] = town
        _own(estate, "styles", STARTER)
        said = {"bought": plot, "paid": price, "text": f"A home of my own on {street(town, block)}! I'll make it cosy."}

    elif action in ("list", "unlist", "sell"):            # her own plot, on the market, off it, or back to the office
        if have is None:
            raise PetError("I haven't got a plot to sell!")
        mine = _id_of(have)
        worth = suggested(have["town"], have["block"], have["lot"])
        where = (have["town"], have["block"], have["lot"])
        if action == "list":
            low, high = math.ceil(worth * CITY["price"]["list_min"]), math.floor(worth * CITY["price"]["list_max"])
            if price is None or not low <= price <= high:
                raise PetError(f"I can list it for {low} to {high} coins.")
            conn.execute("UPDATE lumi_plots SET list_price = ?, listed_at = ?, version = version + 1 WHERE town = ? AND block = ? AND lot = ?",
                         (price, now, *where))
            said = {"listed": mine, "price": price, "text": f"My plot is on the market for {price} coins!"}
        elif action == "unlist":
            if have["list_price"] is None:
                raise PetError("It isn't on the market.")
            conn.execute("UPDATE lumi_plots SET list_price = NULL, listed_at = NULL, version = version + 1 WHERE town = ? AND block = ? AND lot = ?",
                         where)
            said = {"unlisted": mine, "text": "I'm keeping it! It's off the market."}
        else:
            back = int(math.floor(worth * CITY["price"]["sell_office"] + 0.5))   # the office pays half (city.json sell_office)
            if not coins.payout(conn, user_id, back, GAME, "sell", ref=f"lumi-plot-sell:{mine}:{have['bought_at']}:{have['version']}"):
                raise PetError("That sale has already gone through!")
            conn.execute("DELETE FROM lumi_plots WHERE town = ? AND block = ? AND lot = ?", where)   # office land again
            said = {"sold": mine, "paid": back, "text": f"The office gave me {back} coins for it. My house styles and yard pieces are still mine!"}

    elif action == "style_buy":                           # a house style, to put on any plot she ever owns
        style = _STYLES.get(item or "")
        if style is None:
            raise PetError("That isn't a house style.")
        if item in estate["styles"]:
            raise PetError("I've already got that one!")
        if style["price"] and not coins.spend(conn, user_id, style["price"], GAME, "style"):
            raise PetError(f"I can't afford that yet! It's {style['price']} coins.")
        _own(estate, "styles", item)
        said = {"bought": item, "paid": style["price"], "text": f"The {style['name']} is mine!"}

    elif action == "style_set":                           # which house stands on her plot
        style = _STYLES.get(item or "")
        if style is None:
            raise PetError("That isn't a house style.")
        if item != STARTER and item not in estate["styles"]:   # (the starter is everyone's)
            raise PetError("I haven't got that house style yet.")
        if have is None:
            raise PetError("I need a plot first! The office in Downtown sells them.")
        conn.execute("UPDATE lumi_plots SET style = ? WHERE user_id = ?", (item, user_id))
        _own(estate, "styles", item)
        said = {"style": item, "text": f"Now my house is a {style['name']}!"}

    elif action == "yard_buy":                            # a yard piece from the office's shop
        piece = _YARD.get(item or "")
        if piece is None:
            raise PetError("That isn't something for a yard.")
        if not piece["price"]:
            raise PetError(f"That one only comes from the {ESTATE['capsule']['name']}!")
        if item in estate["yard"]:
            raise PetError("I've already got that one!")
        if not coins.spend(conn, user_id, piece["price"], GAME, "yard"):
            raise PetError(f"I can't afford that yet! It's {piece['price']} coins.")
        _own(estate, "yard", item)
        said = {"bought": item, "paid": piece["price"], "text": f"A {piece['name']} for my yard!"}

    elif action == "yard_set":                            # put a piece in a yard slot, or (item "") clear it
        if slot not in SLOTS:
            raise PetError("That isn't a spot in a yard.")
        if have is None:
            raise PetError("I need a plot first! The office in Downtown sells them.")
        piece = _YARD.get(item) if item else None
        if item and (piece is None or piece["slot"] != slot):
            raise PetError("That doesn't go there.")
        if item and item not in estate["yard"]:
            raise PetError("I haven't got that one yet.")
        yard = {key: value for key, value in _yard_of(have).items() if value}   # what's there now, without empty slots
        if item:
            yard[slot] = item
        else:
            yard.pop(slot, None)
        conn.execute("UPDATE lumi_plots SET yard = ? WHERE user_id = ?", (json.dumps(yard, separators=(",", ":")), user_id))
        said = {"yard": slot, "item": item or "", "text": f"There, the {piece['name']} looks lovely!" if piece else "I've cleared that spot."}

    elif action == "capsule":                             # the Keepsake Capsule: a capsule-only yard piece
        capsule = ESTATE["capsule"]
        if all(prize["id"] in estate["yard"] for prize in _KEEPSAKES):   # checked before charging: only repeats are left
            raise PetError(f"I've already got everything the {capsule['name']} holds!")
        if not coins.spend(conn, user_id, capsule["price"], GAME, "capsule"):
            raise PetError(f"I can't afford that yet! It's {capsule['price']} coins.")
        won, rolled = lumipet._roll(_KEEPSAKES, capsule["odds"], rng or random.SystemRandom())   # rolled like Lumi Pet's capsules
        repeat = won["id"] in estate["yard"]
        back = coins.buyback(conn, user_id, ESTATE["values"][rolled] // 2, GAME) if repeat else 0   # a repeat: half its value back
        _own(estate, "yard", won["id"])
        said = {"won": won["id"], "name": won["name"], "slot": won["slot"], "rarity": rolled, "paid": capsule["price"],
                "duplicate": repeat, "buyback": back,
                "text": f"Another {won['name']}! The office gave me {back} coins for it." if repeat else f"A {won['name']}! Ooh, it's {rolled}!"}

    else:
        raise PetError("Lumi doesn't know that one.")

    lumipet._save(conn, user_id, pet)                     # her home town, styles and yard pieces, with the plot
    return {"said": said, **view(conn, user_id)}


def _grow(conn: sqlite3.Connection, town: str, blocks: int) -> bool:
    """A new block at the town's gate once every lot it has is owned. Block numbers count out from Downtown, so the
    new one is the next number and every lot already there keeps its id. True when it grew."""
    owned = conn.execute("SELECT count(*) FROM lumi_plots WHERE town = ? AND block <= ?", (town, blocks)).fetchone()[0]
    if owned < blocks * 8 or blocks >= MAX_BLOCKS:        # office land left (or as big as a plot id allows)
        return False
    conn.execute("UPDATE lumi_city_towns SET blocks = blocks + 1 WHERE town = ?", (town,))
    return True


# --------------------------------------------------------------------------- Lumi Heights (the apartment buildings)
def home_town(conn: sqlite3.Connection, user_id: int) -> str:
    """Which town's Lumi Heights her flat is in: her save's home_town, or LefTown while she hasn't picked. Only reads."""
    picked = (lumipet._peek(conn, user_id) or {}).get("home_town")
    return picked if picked in TOWNS else "left"


def plot_of(conn: sqlite3.Connection, user_id: int) -> str | None:
    """Her plot's id ("l-3-5"), or None if she owns no land."""
    row = _plot_of(conn, user_id)
    return _id_of(row) if row is not None else None


def residents(conn: sqlite3.Connection, town: str) -> list[int]:
    """Everyone whose Lumi lives in this town's Lumi Heights: her save's home_town (not picked yet counts as LefTown)."""
    found = []
    for row in conn.execute("SELECT user_id, CASE WHEN json_valid(data) THEN json_extract(data, '$.home_town') END AS town "
                            "FROM lumi_pets ORDER BY user_id"):
        if (row["town"] or "left") == town:               # None (not picked yet) is LefTown's
            found.append(row["user_id"])
    return found
