"""Lumi Pet on the server: one companion per account, with needs that run down while you're away.

Ported from MommyBot's Littlepottchi (src/dressup/store.js and care.js, before it was retired): a pet is one JSON
document, every read catches it up from the time it was last saved, and every action is validated and saved in one
transaction. The page only says what the person chose (which snack, which toy); the server works out what happens.

Needs (hunger, energy, joy) fall along a logarithmic curve: quickly while she's full, slowly near empty, so the last
quarter of any meter takes exactly one day. Nothing is ever lost at zero: she is sad and sleepy, and a snack fixes it.

The item catalog is lumipet_data/items.json, checked when this module loads so a bad edit stops the server at start
rather than in the middle of someone's purchase. Times are whole milliseconds, like the page's Date.now().
"""

from __future__ import annotations

import json
from math import floor
import math
import random
import re
import sqlite3
import time
from pathlib import Path

from mnemerynne import coins, lumibattle, lumidungeon, lumigarden, lumiparty, push
from mnemerynne.db import transaction

GAME = "lumi-pet"
HOUR = 3_600_000
LAST_QUARTER_HOURS = 24                 # how long the last 25% of any meter takes
DECAY = {"hunger": 30.0, "energy": 40.0, "joy": 40.0}    # B: a smaller number empties faster at the top of the meter
START = 85.0                            # where a new pet's meters begin
COOLDOWN = 30_000                       # between two of the same quick care (cuddles, snacks)
NAP_MINUTES = 5                         # a nap is a timed rest; its energy arrives when it finishes
NAP_ENERGY = 35
AWAKE_MS = 15 * 60_000                  # how long a gentle wake keeps her up at night (rules.js AWAKE_MS)
CUDDLE_JOY = 5
MAX_MOMENTS = 999_999
MAX_HELD = 99                           # of any one snack or treat
GLOWS = tuple(lumibattle.GLOW_ELEMENT)      # rose, lagoon, starlight, zephyr, wisp, umbra: each is also her element in a fight
KINDS = ("snacks", "treats", "toys")
BOOSTS = ("power", "guard", "luck")     # what a treat lends the next fight (battles come later)
LOW = 25.0                              # under this a need is worth a reminder (rules.js LOW)
REMIND_EVERY = 3 * HOUR                 # at most one reminder per person in this long
QUIET = (22, 7)                         # no reminders from 22:00 to 07:00 on the phone's clock
REMIND_ORDER = ("hunger", "energy", "joy", "nap", "garden")   # when several are waiting, the first of these is the one sent
_ID = re.compile(r"^[a-z0-9-]{1,40}$")


class PetError(ValueError):
    """Refused; the message is safe to show."""


def _tau(b: float) -> float:
    """The curve's time constant in hours, chosen so 25% down to 0% takes LAST_QUARTER_HOURS."""
    return LAST_QUARTER_HOURS / (math.exp(100 / b) - math.exp(75 / b))


def decayed(value: float, hours: float, b: float) -> float:
    """A meter `hours` later. value = 100 - b·ln(1 + t/tau): find where `value` sits on the curve, then move along it.
    Working from the value means a snack needs no saved start time: a fuller meter is simply earlier on the curve."""
    if hours <= 0:
        return value
    tau = _tau(b)
    elapsed = tau * (math.exp((100 - value) / b) - 1)     # hours since this meter was full
    return max(0.0, 100 - b * math.log(1 + (elapsed + hours) / tau))


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def load_catalog(path: Path | None = None) -> dict:
    """The items Lumi can own, validated the way Littlepottchi's catalog was (src/dressup/catalog.js)."""
    data = json.loads((path or Path(__file__).parent / "lumipet_data" / "items.json").read_text(encoding="utf-8"))
    seen: set[str] = set()
    for kind in KINDS:
        if not isinstance(data.get(kind), list) or not data[kind]:
            raise ValueError(f"Lumi Pet's catalog needs some {kind}.")
        for item in data[kind]:
            if not isinstance(item, dict) or not _ID.match(str(item.get("id", ""))) or item["id"] in seen:
                raise ValueError(f"Lumi Pet's catalog has a bad or repeated id in {kind}.")
            seen.add(item["id"])
            if not item.get("name") or not item.get("icon") or not isinstance(item.get("price"), int) or not 0 <= item["price"] <= 100_000:
                raise ValueError(f"Lumi Pet's {item['id']} needs a name, an icon and a price.")
            numbers = ("minutes", "joy", "energy", "hunger") if kind == "toys" else ("fullness", "joy")
            if any(not isinstance(item.get(n), int) or not -100 <= item[n] <= 100 for n in numbers):
                raise ValueError(f"Lumi Pet's {item['id']} has a number out of range.")
            if kind == "toys" and not 1 <= item["minutes"] <= 30:
                raise ValueError(f"Lumi Pet's {item['id']} needs a play time of 1 to 30 minutes.")
            if kind == "treats" and item.get("boost") not in BOOSTS:
                raise ValueError(f"Lumi Pet's {item['id']} needs a known boost.")
            # A rarity marks a toy that only comes out of a capsule, so it can't also be sold or be free for everyone.
            if "rarity" in item and (kind != "toys" or item["rarity"] not in RARITIES or item["price"]):
                raise ValueError(f"Lumi Pet's {item['id']} can only be a capsule prize if it's an unsold toy.")
    for kind in ("snacks", "toys"):                       # nobody can be left with nothing to feed or play with
        if not any(item["price"] == 0 and "rarity" not in item for item in data[kind]):
            raise ValueError(f"Lumi Pet's catalog needs a free item among its {kind}.")
    return data


def load_house(path: Path | None = None) -> dict:
    """The house: fixed spots, each with a handful of objects to swap in, paperdoll style (Littlepottchi's outfit
    slots, pointed at furniture). The order of spots here is the order the page lists them; the page's own art file
    (static/games/lumi-pet/house.js) decides how they layer. Each spot's first object is free: it is what a new
    house starts with."""
    data = json.loads((path or Path(__file__).parent / "lumipet_data" / "house.json").read_text(encoding="utf-8"))
    seen: set[str] = set()
    if not isinstance(data.get("spots"), list) or not data["spots"]:
        raise ValueError("Lumi's house needs some spots.")
    for spot in data["spots"]:
        if not isinstance(spot, dict) or not _ID.match(str(spot.get("id", ""))) or spot["id"] in seen or not spot.get("name"):
            raise ValueError("Lumi's house has a bad or repeated spot.")
        seen.add(spot["id"])
        if not isinstance(spot.get("items"), list) or not spot["items"]:
            raise ValueError(f"The {spot['id']} spot needs something to put in it.")
        for item in spot["items"]:
            if not isinstance(item, dict) or not _ID.match(str(item.get("id", ""))) or item["id"] in seen or not item.get("name"):
                raise ValueError(f"The {spot['id']} spot has a bad or repeated object.")
            seen.add(item["id"])
            if not isinstance(item.get("price"), int) or not 0 <= item["price"] <= 100_000:
                raise ValueError(f"The house's {item['id']} needs a price.")
            # A rarity marks a Star Capsule prize: it can only be won, so it can't also be sold or found in the dungeon.
            if "rarity" in item and (item["rarity"] not in RARITIES or item["price"] or item.get("found")):
                raise ValueError(f"The house's {item['id']} can't be a Star Capsule prize as well as sold or found.")
        if spot["items"][0]["price"] != 0 or "rarity" in spot["items"][0]:
            raise ValueError(f"The {spot['id']} spot's first object must be free.")
    return data


RARITIES = ("common", "uncommon", "rare", "epic", "legendary")


def load_closet(path: Path | None = None) -> dict:
    """What Lumi can wear: slots (one accessory each, like Littlepottchi's outfit slots), the two capsules' prices and
    odds, and `values`, what a prize of each rarity is worth when it has no shop price (a repeat pays back half).
    An accessory priced 0 isn't sold: it only comes out of a capsule. Accessories are for looks, never for fights."""
    data = json.loads((path or Path(__file__).parent / "lumipet_data" / "closet.json").read_text(encoding="utf-8"))
    seen: set[str] = set()
    capsule = data.get("capsule")
    if not isinstance(capsule, dict) or not capsule.get("name") or not isinstance(capsule.get("price"), int) or not 1 <= capsule["price"] <= 100_000 \
            or not isinstance(capsule.get("odds"), dict) or tuple(capsule["odds"]) != RARITIES \
            or any(not isinstance(v, int) or v < 1 for v in capsule["odds"].values()) or sum(capsule["odds"].values()) != 100:
        raise ValueError("The Star Capsule needs a price and odds for every rarity that add up to 100.")
    moon = data.get("moon")                               # the dearer capsule: its odds name only the rarities it can give
    if not isinstance(moon, dict) or not moon.get("name") or not isinstance(moon.get("price"), int) or not 1 <= moon["price"] <= 100_000 \
            or not isinstance(moon.get("odds"), dict) or not moon["odds"] or any(rarity not in RARITIES for rarity in moon["odds"]) \
            or any(not isinstance(v, int) or v < 1 for v in moon["odds"].values()) or sum(moon["odds"].values()) != 100:
        raise ValueError("The Moon Capsule needs a price and odds that add up to 100.")
    values = data.get("values")
    if not isinstance(values, dict) or tuple(values) != RARITIES or any(not isinstance(v, int) or not 2 <= v <= 100_000 for v in values.values()):
        raise ValueError("The closet needs a value for every rarity, so a repeat can pay back half of it.")
    for slot in data.get("slots") or [None]:
        if not isinstance(slot, dict) or not _ID.match(str(slot.get("id", ""))) or slot["id"] in seen or not slot.get("name") or not slot.get("items"):
            raise ValueError("Lumi's closet has a bad or repeated slot.")
        seen.add(slot["id"])
        for item in slot["items"]:
            if not isinstance(item, dict) or not _ID.match(str(item.get("id", ""))) or item["id"] in seen or not item.get("name") \
                    or item.get("rarity") not in RARITIES or not isinstance(item.get("price"), int) or not 0 <= item["price"] <= 100_000:
                raise ValueError(f"Lumi's closet has a bad or repeated accessory in {slot['id']}.")
            seen.add(item["id"])
    return data


CATALOG = load_catalog()
HOUSE = load_house()
CLOSET = load_closet()
_WEAR = {item["id"]: {**item, "slot": slot["id"]} for slot in CLOSET["slots"] for item in slot["items"]}
_DECOR = {item["id"]: {**item, "spot": spot["id"]} for spot in HOUSE["spots"] for item in spot["items"]}
_ITEMS = {item["id"]: {**item, "kind": kind} for kind in KINDS for item in CATALOG[kind]}
# All the capsules can give: clothes, then toys, then decor. A clothing prize has a "slot", a toy's kind is "toys" and
# a house object has a "spot"; _shelf says which of her lists keeps each.
_PRIZES = [*_WEAR.values(), *(toy for toy in _ITEMS.values() if "rarity" in toy), *(piece for piece in _DECOR.values() if "rarity" in piece)]


def _shelf(prize: dict) -> str:
    return "decor" if "spot" in prize else "toys" if prize.get("kind") == "toys" else "closet"


def now_ms() -> int:
    return int(time.time() * 1000)


def _fresh(now: int) -> dict:
    """A brand-new companion."""
    return {"version": 2, "hunger": START, "energy": START, "joy": START, "updated": now, "moments": 0, "memory": None,
            "glow": "rose", "rest": "auto", "restUntil": 0, "task": None, "done": None, "cooldowns": {},
            "inventory": {}, "toys": [], "boost": None, "imported": False, "low": {}, "seen": now, "house": {}, "decor": [],
            "outfit": {}, "closet": [], "storyteller": "library", "garden_reminders": True,
            **lumibattle.fresh_fields()}


def advance(pet: dict, now: int) -> None:
    """Catch a pet up to `now`. A finished nap or play pays its reward at the moment it finished, and the rest of the
    absence decays normally from there (care.js advance): being away for a week never banks a week-old reward."""
    now = max(now, pet["updated"])
    start = pet["updated"]

    def decay_until(moment: int) -> None:
        nonlocal start
        hours = max(0, moment - start) / HOUR
        resting = lumibattle.NAP_MEND if pet["task"] and pet["task"]["kind"] == "nap" else 1    # a nap mends faster
        lumibattle.mend(pet, resting * max(0, moment - start) / lumibattle.MEND_MS)      # her health comes back with time
        for meter, b in DECAY.items():
            pet[meter] = decayed(pet[meter], hours, b)
        start = max(start, moment)

    task = pet["task"]
    if task and now >= task["finishesAt"]:
        decay_until(task["finishesAt"])
        for meter, amount in task["rewards"].items():
            pet[meter] = _clamp(pet[meter] + amount)
        pet["moments"] = min(MAX_MOMENTS, pet["moments"] + 1)
        pet["done"] = {"kind": task["kind"], "item": task.get("item"), "at": task["finishesAt"]}   # for the page to announce once
        pet["task"] = None
    decay_until(now)
    pet["cooldowns"] = {key: until for key, until in pet["cooldowns"].items() if until > now}
    pet["updated"] = now
    _mark_low(pet, now)


def _mark_low(pet: dict, now: int) -> None:
    """Note when each need first dropped under LOW, and forget it once it's back up. That moment names the episode,
    so one stretch of hunger is one reminder however often the worker looks (care.js episodes)."""
    for meter in DECAY:
        if pet[meter] < LOW:
            pet["low"].setdefault(meter, now)
        else:
            pet["low"].pop(meter, None)


def load(conn: sqlite3.Connection, user_id: int, now: int | None = None, *, seen: bool = True) -> dict:
    """This person's pet, caught up to now and saved. Call inside a transaction. seen=False: the worker looking,
    not the person, so it doesn't count as a visit."""
    now = now_ms() if now is None else now
    row = conn.execute("SELECT data FROM lumi_pets WHERE user_id = ?", (user_id,)).fetchone()
    pet = {**_fresh(now), **json.loads(row["data"])} if row else _fresh(now)   # older saves gain any newer fields
    advance(pet, now)
    if seen:
        pet["seen"] = now
    _save(conn, user_id, pet)
    return pet


def _save(conn: sqlite3.Connection, user_id: int, pet: dict) -> None:
    conn.execute("INSERT INTO lumi_pets(user_id, data) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, "
                 "updated_at = datetime('now')", (user_id, json.dumps(pet, separators=(",", ":"))))


def snapshot(conn: sqlite3.Connection, user_id: int, pet: dict, now: int) -> dict:
    """What the page draws. `now` is the server's clock, so the page can count timers down without trusting its own."""
    worn = {slot: item for slot, item in pet["outfit"].items() if _WEAR.get(item, {}).get("slot") == slot}   # drop anything since retired
    run, party = lumidungeon.current(conn, user_id), lumiparty.mine(conn, user_id)
    party_info = {"code": party["code"], "phase": party["phase"], "mode": party["mode"]} if party else None
    if party and party["phase"] == "trip":               # out with friends: the party's trip is her trip (lumiparty.py)
        run_view = lumiparty.view(conn, party, user_id, now=now)["run"]
        run = {"over": None, "hp": run_view["hp"], "satchel": {"potions": run_view["satchel"]["potions"]}, "fight": None}
    else:
        run_view = lumidungeon.view(run, pet)
    return {"pet": {**pet, **{meter: round(pet[meter], 1) for meter in DECAY}, "house": placed(pet), "outfit": worn}, "now": now,
            "coins": coins.peek(conn, user_id), "catalog": CATALOG, "house": HOUSE, "closet": CLOSET,
            "battle": {key: lumibattle.CATALOG[key] for key in ("basics", "trees", "mods")} | {"rules": lumibattle.RULES},
            "progress": lumibattle.summary(pet), "fight": lumibattle.current(conn, user_id),
            "potions_ready": lumidungeon.potions_ready(run, pet),
            "health": _health(run, pet), "party": party_info,
            "garden": lumigarden.view(_garden(conn, user_id, pet, now), pet, now),
            "garden_decor": lumigarden.CATALOG["decor"],        # the decorations and the capsule: fixed, so not part of the view
            "dungeon": {"environments": lumidungeon.open_environments(pet), "run": run_view}}


def _garden(conn: sqlite3.Connection, user_id: int, pet: dict, now: int) -> dict:
    """Her Stardust Garden (lumigarden.py), with the press caught up to now and saved: looking is what makes the
    helpers' squeezing count, the way looking catches her needs up."""
    garden = lumigarden.current(conn, user_id, now)
    lumigarden.advance(conn, user_id, garden, pet, now)
    lumigarden.save(conn, user_id, garden)
    return garden


def _health(run: dict | None, pet: dict) -> dict:
    """Her health for the page: the trip's own count while she is out (nothing mends down there but potions and
    food), else what she has at home and how long plain waiting takes to fill it (a nap takes half as long). A party
    trip's view (lumiparty) has the same hp."""
    full = lumibattle.stats(pet)["hp"]
    if run and not run["over"]:
        return {"hp": min(full, run["hp"]), "max": full, "mends_in": 0, "out": True}
    return {"hp": lumibattle.health(pet), "max": full, "mends_in": lumibattle.mends_in(pet), "out": False}


def _eat(conn: sqlite3.Connection, user_id: int, pet: dict, share: float) -> None:
    """Bought food gives back a share of her health: at home, or to the trip's count if she is out (not mid-fight)."""
    if lumiparty.heal(conn, user_id, share):              # out with a party: their trip's count
        return
    run = lumidungeon.current(conn, user_id)
    if run and not run["over"]:
        if not run["fight"]:
            full = lumibattle.stats(pet)["hp"]
            run["hp"] = min(full, run["hp"] + floor(full * share))
            lumidungeon._save(conn, user_id, run)
    else:
        lumibattle.mend(pet, share)


def placed(pet: dict) -> dict[str, str]:
    """What stands in every spot: the person's choice where it is still valid, else the spot's free first object.
    Worked out on reading, so a spot added to the catalog later, or an object removed from it, never breaks a save."""
    return {spot["id"]: pet["house"][spot["id"]] if _DECOR.get(pet["house"].get(spot["id"]), {}).get("spot") == spot["id"]
            else spot["items"][0]["id"] for spot in HOUSE["spots"]}


def state(conn: sqlite3.Connection, user_id: int, now: int | None = None) -> dict:
    """Read the pet (catching it up). Call inside a transaction."""
    now = now_ms() if now is None else now
    return snapshot(conn, user_id, load(conn, user_id, now), now)


def _cool(pet: dict, key: str, now: int) -> None:
    """Refuse a quick care repeated too soon, then start its cooldown."""
    if pet["cooldowns"].get(key, 0) > now:
        raise PetError("Lumi is still enjoying that. Try again in a moment.")
    pet["cooldowns"][key] = now + COOLDOWN


def _moment(pet: dict, memory: str) -> None:
    pet["moments"] = min(MAX_MOMENTS, pet["moments"] + 1)
    pet["memory"] = memory


def act(conn: sqlite3.Connection, user_id: int, action: str, *, item: str | None = None, count: int = 1,
        moments: int = 0, glow: str | None = None, slot: int | None = None, now: int | None = None, rng=None) -> dict:
    """One thing the person chose to do. Call inside a transaction: the pet, and any coins, save together or not at all.
    The pet is caught up first, so a refused action never changes what time already did (store.js act)."""
    now = now_ms() if now is None else now
    pet = load(conn, user_id, now)
    thing = _ITEMS.get(item) if item is not None else None
    said = None                                           # what just happened, for the page to say

    if action == "cuddle":
        _cool(pet, "cuddle", now)
        pet["joy"] = _clamp(pet["joy"] + CUDDLE_JOY)
        _moment(pet, "cuddle")
    elif action == "feed":
        if thing is None or thing["kind"] not in ("snacks", "treats"):
            raise PetError("Choose something from the pantry.")
        if thing["price"] and pet["inventory"].get(item, 0) < 1:
            raise PetError(f"There's no {thing['name'].lower()} left. The shop has more.")
        _cool(pet, "feed", now)
        if thing["price"]:                                # free food never runs out, and only bought food mends her
            _eat(conn, user_id, pet, max(0, thing["fullness"]) / 100)
            pet["inventory"][item] -= 1
            if pet["inventory"][item] <= 0:
                del pet["inventory"][item]
        pet["hunger"] = _clamp(pet["hunger"] + thing["fullness"])
        pet["joy"] = _clamp(pet["joy"] + thing["joy"])
        if thing["kind"] == "treats":
            pet["boost"] = thing["boost"]                 # one boost waits at a time; a newer treat replaces it
        _moment(pet, "snack")
    elif action in ("play", "nap"):
        if pet["task"]:
            raise PetError("Lumi is busy napping." if pet["task"]["kind"] == "nap" else "Lumi is busy playing.")
        if action == "play":
            if thing is None or thing["kind"] != "toys" or ((thing["price"] or "rarity" in thing) and item not in pet["toys"]):   # bought or won toys only once hers
                raise PetError("Choose a toy from Lumi's toy box.")
            rewards = {"joy": thing["joy"], "energy": thing["energy"], "hunger": thing["hunger"]}
            pet["task"] = {"kind": "play", "item": item, "started": now, "finishesAt": now + thing["minutes"] * 60_000, "rewards": rewards}
            pet["rest"], pet["restUntil"] = "awake", now + AWAKE_MS      # playing wakes her, even at night
            pet["memory"] = "play"
        else:
            pet["task"] = {"kind": "nap", "item": None, "started": now, "finishesAt": now + NAP_MINUTES * 60_000, "rewards": {"energy": NAP_ENERGY}}
            pet["rest"], pet["restUntil"] = "nap", now + NAP_MINUTES * 60_000
            pet["memory"] = "rest"
        pet["done"] = None
    elif action == "wake":
        if pet["task"] and pet["task"]["kind"] == "nap":
            pet["task"] = None                            # a nap cut short earns nothing
        pet["rest"], pet["restUntil"] = "awake", now + AWAKE_MS
        pet["memory"] = "rest"
    elif action == "buy":
        if thing is None or not thing["price"]:
            raise PetError("The shop doesn't sell that.")
        if thing["kind"] == "toys":
            if item in pet["toys"]:
                raise PetError("Lumi already has that toy.")
            count = 1
        elif not 1 <= count <= MAX_HELD or pet["inventory"].get(item, 0) + count > MAX_HELD:
            raise PetError(f"Lumi's pantry holds up to {MAX_HELD} of each.")
        if not coins.spend(conn, user_id, thing["price"] * count, GAME):
            raise PetError("Not enough coins for that.")
        if thing["kind"] == "toys":
            pet["toys"].append(item)
        else:
            pet["inventory"][item] = pet["inventory"].get(item, 0) + count
        said = {"bought": item, "count": count, "paid": thing["price"] * count}
    elif action == "decorate":
        # Put an object in its spot. One she doesn't own yet is bought first, in this same transaction; owned ones
        # (and each spot's free first object) swap back and forth for nothing, like changing an outfit.
        piece = _DECOR.get(item) if item is not None else None
        if piece is None:
            raise PetError("That isn't something for Lumi's house.")
        if piece.get("found") and item not in pet["decor"]:
            raise PetError("That one can't be bought. It's waiting somewhere in the dungeon.")
        if "rarity" in piece and item not in pet["decor"]:   # priced 0 like the free objects, but it has to be won
            raise PetError("That one only comes from a capsule.")
        if piece["price"] and item not in pet["decor"]:
            if not coins.spend(conn, user_id, piece["price"], GAME, "decor"):
                raise PetError("Not enough coins for that.")
            pet["decor"].append(item)
            said = {"bought": item, "count": 1, "paid": piece["price"]}
        pet["house"][piece["spot"]] = item
    elif action == "wear":
        # Put an accessory on, or take it off if she's already wearing it. One that's sold and not yet hers is bought
        # first; one that only comes from the Star Capsule can't be bought at all.
        piece = _WEAR.get(item) if item is not None else None
        if piece is None:
            raise PetError("That isn't something Lumi can wear.")
        if item not in pet["closet"]:
            if not piece["price"]:
                raise PetError("That one only comes from a capsule.")
            if not coins.spend(conn, user_id, piece["price"], GAME, "wear"):
                raise PetError("Not enough coins for that.")
            pet["closet"].append(item)
            said = {"bought": item, "count": 1, "paid": piece["price"]}
        if pet["outfit"].get(piece["slot"]) == item and said is None:
            del pet["outfit"][piece["slot"]]
        else:
            pet["outfit"][piece["slot"]] = item
    elif action == "capsule":
        # Open a capsule: item None is the Star Capsule, "moon" the Moon Capsule, which only holds its own rarities. Roll
        # a rarity first, then pick equally among every prize of it, so adding more commons to the catalog never changes
        # the odds of a legendary (MommyBot's gacha rolled the same way). A repeat is bought back for half its value.
        # Prizes are clothes, toys and house objects together (_PRIZES).
        if item not in (None, "moon"):
            raise PetError("Lumi doesn't know that capsule.")
        kind = CLOSET["moon" if item else "capsule"]
        rng = rng or random.SystemRandom()
        def owned(prize: dict) -> bool:                   # already in her closet, toy box or house
            return prize["id"] in pet[_shelf(prize)]
        pool = [prize for prize in _PRIZES if prize["rarity"] in kind["odds"]]
        if all(owned(prize) for prize in pool):           # checked before charging: all that's left would be repeats
            raise PetError(f"Lumi already has everything the {kind['name']} holds!")
        if not coins.spend(conn, user_id, kind["price"], GAME, "capsule"):
            raise PetError("Not enough coins for that.")
        roll, rolled = rng.randrange(100), RARITIES[-1]
        for rarity, chance in kind["odds"].items():
            if roll < chance:
                rolled = rarity
                break
            roll -= chance
        choices = [prize for prize in pool if prize["rarity"] == rolled]
        won = choices[rng.randrange(len(choices))]
        said = {"capsule": won["id"], "name": won["name"], "rarity": rolled, "paid": kind["price"]}
        if owned(won):                                    # what she has stays as it is; only the coins change
            said |= {"duplicate": True, "buyback": coins.buyback(conn, user_id, (won["price"] or CLOSET["values"][rolled]) // 2, GAME)}
        elif "spot" in won:
            pet["decor"].append(won["id"])
            pet["house"][won["spot"]] = won["id"]         # it goes straight into the room
        elif _shelf(won) == "toys":
            pet["toys"].append(won["id"])                 # straight into her toy box, ready to play with
        else:
            pet["closet"].append(won["id"])
            pet["outfit"][won["slot"]] = won["id"]        # she wears it straight out of the capsule
    elif action == "garden_reminders":                    # whether "your flowers are ready" may be sent to her phone
        pet["garden_reminders"] = not pet.get("garden_reminders", True)
    elif action == "storyteller":
        # Whose words the dungeon uses: the library that ships with the game, or (as well) a line written fresh for
        # each moment by the person's own model. Only a preference; whether a model is there is checked when asked.
        pet["storyteller"] = "library" if pet.get("storyteller") == "live" else "live"
    elif action == "glow":
        if glow not in GLOWS:
            raise PetError("Choose one of Lumi's glow colours.")
        pet["glow"] = glow
    elif action in ("learn", "respec", "equip", "mod", "potions", "heal", "drink"):
        run = lumidungeon.current(conn, user_id) if action == "drink" else None
        if (run and not run["over"]) or (action == "drink" and lumiparty.on_trip(conn, user_id)):
            raise PetError("Lumi is out exploring. She can drink it there, in Lumi Dungeons.")
        try:                                              # her skills, moves, mods and potions (lumibattle.py)
            said = lumibattle.progress(conn, user_id, pet, action, item, slot, count, now)
        except lumibattle.BattleError as refused:
            raise PetError(str(refused)) from None
    elif action == "import":
        # The happy moments counted in this browser before pets lived on the server. A keepsake number only: no coin
        # depends on it, so there is nothing to gain by inventing one. Taken once per account.
        if not pet["imported"]:
            pet["imported"] = True
            pet["moments"] = min(MAX_MOMENTS, pet["moments"] + max(0, moments))
            if glow in GLOWS:
                pet["glow"] = glow
    else:
        raise PetError("Lumi doesn't know that one.")

    _mark_low(pet, now)                                   # a snack that lifts her over the line ends that episode
    _save(conn, user_id, pet)
    return {**snapshot(conn, user_id, pet, now), "said": said}


# --------------------------------------------------------------------------- reminders (push)
def episodes(pet: dict, now: int) -> dict[str, str]:
    """What Lumi would mention right now: {kind: episode}. Only news the person hasn't seen: a need that began since
    their last visit, or a nap that finished in the last hour while they were away."""
    found = {meter: str(since) for meter, since in pet["low"].items() if since > pet["seen"]}
    done = pet["done"]
    if done and done["kind"] == "nap" and done["at"] > pet["seen"] and now - done["at"] < HOUR:
        found["nap"] = str(done["at"])
    return found


def _quiet(utc_offset: int, now: int) -> bool:
    """Whether it's night on the phone, by the clock offset it gave when it subscribed."""
    hour = ((now // 1000 + utc_offset * 60) // 3600) % 24
    return hour >= QUIET[0] or hour < QUIET[1]


def sweep(conn: sqlite3.Connection, data_dir: Path, contact: str, after: int = 0, limit: int = 100, now: int | None = None) -> int:
    """The worker's look at a batch of pets whose people asked for reminders (store.js tick). Catches each up, queues
    anything new, and sends at most one notification per person: none at night, none within REMIND_EVERY of the last.
    Returns where the next batch starts (0: back to the beginning). Each need is sent once, even across restarts."""
    now = now_ms() if now is None else now
    users = [row[0] for row in conn.execute("SELECT DISTINCT user_id FROM push_subscriptions WHERE user_id > ? ORDER BY user_id LIMIT ?", (after, limit))]
    for user_id in users:
        with transaction(conn):                           # decide inside a transaction; talk to the network outside it
            pet = load(conn, user_id, now, seen=False)
            current = episodes(pet, now)
            flowers = lumigarden.ready_since(lumigarden.current(conn, user_id, now), now) if pet.get("garden_reminders", True) else None
            if flowers:                                   # flowers ready that she hasn't looked at (never advances the garden)
                current["garden"] = flowers
            for kind, episode in current.items():
                conn.execute("INSERT OR IGNORE INTO lumi_push_events(user_id, kind, episode, created_at) VALUES (?, ?, ?, ?)", (user_id, kind, episode, now))
            waiting = []
            for row in conn.execute("SELECT kind, episode FROM lumi_push_events WHERE user_id = ? AND sent_at IS NULL", (user_id,)).fetchall():
                if current.get(row["kind"]) == row["episode"]:
                    waiting.append(row["kind"])
                else:                                     # fed, or seen, before it was ever sent: nothing to say
                    conn.execute("DELETE FROM lumi_push_events WHERE user_id = ? AND kind = ? AND episode = ?", (user_id, row["kind"], row["episode"]))
            conn.execute("DELETE FROM lumi_push_events WHERE user_id = ? AND sent_at < ?", (user_id, now - 7 * 24 * HOUR))
            recent = conn.execute("SELECT 1 FROM lumi_push_events WHERE user_id = ? AND sent_at > ?", (user_id, now - REMIND_EVERY)).fetchone()
            offset = conn.execute("SELECT utc_offset FROM push_subscriptions WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchone()[0]
        if not waiting or recent or _quiet(offset, now):
            continue
        need = next(kind for kind in REMIND_ORDER if kind in waiting)
        if push.send(conn, data_dir, contact, user_id, {"kind": GAME, "need": need}):
            with transaction(conn):                       # everything waiting is covered by this one notification
                conn.execute("UPDATE lumi_push_events SET sent_at = ? WHERE user_id = ? AND sent_at IS NULL", (now, user_id))
    return users[-1] if len(users) == limit else 0


# --------------------------------------------------------------------------- fights (lumibattle.py does the fighting)
def fight_start(conn: sqlite3.Connection, user_id: int, tier: str, now: int | None = None, rng=None) -> dict:
    """Begin a fight. Call inside a transaction. A fight wakes her, and uses up a waiting treat boost."""
    now = now_ms() if now is None else now
    pet = load(conn, user_id, now)
    if pet["task"]:
        raise PetError("Lumi is busy napping." if pet["task"]["kind"] == "nap" else "Lumi is busy playing.")
    run = lumidungeon.current(conn, user_id)
    if (run and not run["over"]) or lumiparty.on_trip(conn, user_id):
        raise PetError("Lumi is out exploring. Her fights are in the dungeon until she comes home.")
    try:
        fight = lumibattle.start(conn, user_id, pet, tier, now, rng)
    except lumibattle.BattleError as refused:
        raise PetError(str(refused)) from None
    pet["rest"], pet["restUntil"] = "awake", now + AWAKE_MS
    _save(conn, user_id, pet)
    return {**snapshot(conn, user_id, pet, now), "fight": fight}


def fight_act(conn: sqlite3.Connection, user_id: int, battle_id: int, turn: int, action: str, now: int | None = None, rng=None) -> dict:
    """One button press in a fight. Call inside a transaction: the turn, a potion used, EXP and coins save together.
    The answer carries the fight as it now stands, including one that just ended."""
    now = now_ms() if now is None else now
    pet = load(conn, user_id, now)
    run = lumidungeon.current(conn, user_id)
    found = action == "potion" and run is not None and run["fight"] == battle_id and run["satchel"]["potions"] > 0
    lent = action == "potion" and not found and lumiparty.satchel_potion(conn, user_id, battle_id, take=False)
    if lent:
        pet["potions"] += 1                               # a party trip: lend her one found in her own satchel
    if found:
        pet["potions"] += 1                               # lend her one from the satchel for this turn; a refusal rolls it all back
    try:
        fight = lumibattle.act(conn, user_id, pet, battle_id, turn, action, now, rng)
    except lumibattle.BattleError as refused:
        raise PetError(str(refused)) from None
    if found:
        run["satchel"]["potions"] -= 1                    # the potion she drank was the found one
        lumidungeon._save(conn, user_id, run)
    if lent:
        lumiparty.satchel_potion(conn, user_id, battle_id, take=True)
    if fight.get("party") and fight["status"] != "active":
        lumiparty.after_fight(conn, user_id, pet, fight, now, rng)    # a party's fight: the party settles the room
    elif run and run["fight"] == battle_id and fight["status"] != "active":
        lumidungeon.after_fight(conn, user_id, pet, run, fight)       # a dungeon fight: clear the room, step back, or end the trip
        _narrate(conn, user_id, rng)
    _save(conn, user_id, pet)
    return {**snapshot(conn, user_id, pet, now), "fight": fight}


# --------------------------------------------------------------------------- the dungeon (lumidungeon.py does the exploring)
def _narrate(conn: sqlite3.Connection, user_id: int, rng=None) -> None:
    """Give the run's latest moment its line from the library (lumitales.py) and keep it with the run, so a reload
    or another device reads the same words."""
    from mnemerynne import lumitales                      # imported late: it reads the dungeon's catalogs

    run = lumidungeon.current(conn, user_id)
    run["say"] = lumitales.line(run, rng)
    run["scene"] = lumitales.scene(run)                   # what the room she is standing in is like
    run["log"].append(run["say"])                         # the story so far (lumidungeon keeps the last few)
    run["said_for"] = None                                # no live line has been written for this moment yet
    lumidungeon._save(conn, user_id, run)


def dungeon_start(conn: sqlite3.Connection, user_id: int, env: str, now: int | None = None, rng=None) -> dict:
    """Set out for an environment. Call inside a transaction."""
    now = now_ms() if now is None else now
    pet = load(conn, user_id, now)
    if pet["task"]:
        raise PetError("Lumi is busy napping." if pet["task"]["kind"] == "nap" else "Lumi is busy playing.")
    if lumiparty.code_of(conn, user_id):
        raise PetError("Lumi is in a party. Leave it to explore on her own.")
    try:
        lumidungeon.start(conn, user_id, pet, env, now, rng)
    except lumidungeon.DungeonError as refused:
        raise PetError(str(refused)) from None
    _narrate(conn, user_id, rng)
    pet["rest"], pet["restUntil"] = "awake", now + AWAKE_MS
    _save(conn, user_id, pet)
    return snapshot(conn, user_id, pet, now)


def dungeon_act(conn: sqlite3.Connection, user_id: int, action: str, *, way: str | None = None, moves: str = "",
                picks: list | None = None, now: int | None = None, rng=None) -> dict:
    """One choice in the dungeon: a step, a door, a game, the way home. Call inside a transaction: the step, anything
    picked up, and coins banked at the exit save together. The answer carries a fight if she walked into one."""
    now = now_ms() if now is None else now
    pet = load(conn, user_id, now)
    try:
        fight = lumidungeon.act(conn, user_id, pet, action, now, rng, way=way, moves=moves, picks=picks)
    except (lumidungeon.DungeonError, lumibattle.BattleError) as refused:
        raise PetError(str(refused)) from None
    _narrate(conn, user_id, rng)
    _save(conn, user_id, pet)
    out = snapshot(conn, user_id, pet, now)
    return {**out, "fight": fight or out["fight"]}


# --------------------------------------------------------------------------- the Stardust Garden (lumigarden.py does the gardening)
def garden_act(conn: sqlite3.Connection, user_id: int, action: str, *, item: str | None = None, plot: int | None = None, count: int = 0,
               picks: list | None = None, now: int | None = None, rng=None) -> dict:
    """One choice in the garden: a press, a plant, a trim, a purchase, a minigame answer. Call inside a transaction:
    the garden, any coins and anything a gift adds to the pet save together or not at all."""
    now = now_ms() if now is None else now
    pet = load(conn, user_id, now)
    run = lumidungeon.current(conn, user_id)
    if (run and not run["over"]) or lumiparty.on_trip(conn, user_id):
        raise PetError("Lumi is out exploring. The garden grows without her until she is home.")
    garden = _garden(conn, user_id, pet, now)
    pantry = [snack["id"] for snack in CATALOG["snacks"] if snack["price"]]      # what a gift of a snack may be
    try:
        said = lumigarden.act(conn, user_id, pet, garden, action, now, rng, item=item, plot=plot, count=count, picks=picks, pantry=pantry, held=MAX_HELD)
    except lumigarden.GardenError as refused:
        raise PetError(str(refused)) from None
    lumigarden.save(conn, user_id, garden)
    _save(conn, user_id, pet)
    return {**snapshot(conn, user_id, pet, now), "said": said}
