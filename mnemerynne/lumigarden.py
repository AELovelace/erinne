"""Lumi Pet's Stardust Garden: a little flower farm that feeds a powder press.

Plots hold flowers. Each kind takes its own time to grow and gives its own amount of starpowder when it is trimmed:
cut flowers (the early kinds) are harvested once and replanted, perennials (the last kinds) grow back by themselves.
A flower left too long is overgrown and gives half; nothing is ever lost. The powder goes to the press, and pressing
(by hand, or by the helpers on their own) squeezes it into real coins. Each further hundred coins in a day costs
twice the powder of the hundred before, so the press slows down but never stops.

Lumi's own build runs the garden: six helpers, one per class tree, each made stronger by the skills she has really
learned in that tree and by her fitted mods, and the moves she has equipped are abilities to fire by hand. Two
minigames move every growing flower forward: watering (repeat a growing sequence) and fertilizing (press fast).

Every garden climbs the flower ladder by its own harvests: kinds are unlocked per garden, in order. Moonrise
replants a finished garden as a starter garden in exchange for a permanent upgrade or one more garden.

Growing is only timestamps: a flower stores the moment it is ready, and boosts move that moment earlier. The page
sends choices and a count of presses; every amount is worked out here. Like lumibattle, this module never loads or
saves a pet: lumipet.py hands it the pet and saves it afterwards. `rng` is anything with random() and randrange(n).
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from math import floor
from pathlib import Path

from mnemerynne import coins, lumibattle

GAME = "lumi-pet"
MIN = 60_000
TREES = ("fighter", "thief", "monk", "mage", "cleric", "paladin")
SKILL_MULT = 1.25                       # each skill she has learned in a tree multiplies that tree's helper by this
LEVEL_BONUS = 0.02                      # and each of her levels after the first adds this much to everything
PRESS_WINDOW = 10_000                   # presses are counted over at most this long, so idling never banks a burst
PRESS_SPARE = 3                         # a few presses of slack, for a batch that arrives a moment early
LUCKY_CAP, GIFT_CAP = 0.5, 0.25         # the Thief can only make so many presses lucky, and gifts so common
MAX_GARDENS = 99
_ID = re.compile(r"^[a-z0-9-]{1,40}$")
_COLOUR = re.compile(r"^#[0-9a-f]{6}$")


class GardenError(ValueError):
    """Refused; the message is safe to show."""


def _whole(value: object, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def load_catalog(path: Path | None = None) -> dict:
    """The garden's numbers and its flowers, helpers, cosmetics and upgrades. A bad entry stops the server at start,
    like a bad price would."""
    path = path or Path(__file__).parent / "lumipet_data" / "garden.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    def bad(what: str) -> ValueError:
        return ValueError(f"Lumi's garden: {what}")

    plots, press = data.get("plots"), data.get("press")
    if not isinstance(plots, dict) or not _whole(plots.get("start"), 1, 16) or not _whole(plots.get("max"), plots["start"], 16) \
            or not _whole(plots.get("price"), 1, 100_000) or not 1 <= plots.get("growth", 0) <= 3:
        raise bad("plots need start, max (16 at most), price and growth.")
    if not isinstance(press, dict) or any(not _whole(press.get(key), 1, 100_000) for key in
                                          ("powder_per_coin", "band", "tap", "max_rate", "lucky", "lucky_chance", "away_hours", "away_kept", "present_seconds")):
        raise bad("the press needs whole numbers for every setting.")
    if not 1 <= data.get("fresh", 0) <= 10 or not _whole(data.get("reminder_minutes"), 1, 1440):
        raise bad("fresh and reminder_minutes are missing or out of range.")
    seen: set[str] = set()

    def named(thing: object, what: str) -> None:
        if not isinstance(thing, dict) or not _ID.match(str(thing.get("id", ""))) or thing["id"] in seen or not thing.get("name"):
            raise bad(f"a {what} has a bad or repeated id, or no name.")
        seen.add(thing["id"])

    flowers = data.get("flowers")
    if not isinstance(flowers, list) or not flowers:
        raise bad("there are no flowers.")
    for flower in flowers:
        named(flower, "flower")
        if flower.get("sort") not in ("cut", "perennial") or not _whole(flower.get("minutes"), 1, 2880) or not _whole(flower.get("powder"), 1, 10_000_000) \
                or not _whole(flower.get("coins"), 0, 100_000) or not _whole(flower.get("stardust"), 0, 100_000_000) or (flower["coins"] and flower["stardust"]) \
                or not _whole(flower.get("level"), 1, lumibattle.MAX_LEVEL) or not _whole(flower.get("needs"), 0, 10 ** 12) \
                or not _COLOUR.match(str(flower.get("colour"))) or not _COLOUR.match(str(flower.get("heart"))) or not _whole(flower.get("petals"), 3, 12):
            raise bad(f"{flower['id']} is missing something, or has both a coin and a stardust price.")
    if flowers[0]["coins"] or flowers[0]["stardust"] or flowers[0]["needs"] or flowers[0]["level"] != 1:
        raise bad("the first flower must be free, so a new garden always has something to plant.")
    if [flower["needs"] for flower in flowers] != sorted(flower["needs"] for flower in flowers):
        raise bad("flowers must be listed in the order they unlock.")
    helpers = data.get("helpers")
    if not isinstance(helpers, list) or tuple(h.get("id") for h in helpers if isinstance(h, dict)) != TREES:
        raise bad("there must be one helper per class tree, in order.")
    for helper in helpers:
        named(helper, "helper")
        if not helper.get("about") or not _whole(helper.get("cost"), 1, 10 ** 9) or not 1.05 <= helper.get("growth", 0) <= 3 \
                or not isinstance(helper.get("mods"), list) or any(key not in lumibattle.BONUS_KEYS for key in helper["mods"]):
            raise bad(f"helper {helper['id']} is missing something.")
    abilities = data.get("abilities")
    if not isinstance(abilities, dict) or tuple(abilities) != TREES or any(
            not isinstance(a, dict) or not a.get("name") or not a.get("about") or not _whole(a.get("seconds"), 0, 3600) or not _whole(a.get("cooldown"), 10, 86_400)
            for a in abilities.values()):
        raise bad("there must be one ability per class tree, with name, about, seconds and cooldown.")
    for kind, price in (("cosmetics", "stardust"), ("upgrades", "max")):
        if not isinstance(data.get(kind), list) or not data[kind]:
            raise bad(f"there are no {kind}.")
        for thing in data[kind]:
            named(thing, kind[:-1])
            if not thing.get("about") or not _whole(thing.get(price), 1, 10 ** 12):
                raise bad(f"{thing['id']} needs words and a {price}.")
    water, feed, moon, gifts = data.get("water"), data.get("feed"), data.get("moonrise"), data.get("gifts")
    if not isinstance(water, dict) or not _whole(water.get("rounds"), 1, 12) or not _whole(water.get("share"), 1, 20) or not _whole(water.get("cooldown"), 10, 86_400) \
            or water["rounds"] * water["share"] > 90:
        raise bad("watering needs rounds, share and cooldown, and can't skip a whole flower.")
    if not isinstance(feed, dict) or any(not _whole(feed.get(key), 1, 86_400) for key in ("seconds", "per", "minutes", "max", "cooldown")):
        raise bad("fertilizing needs seconds, per, minutes, max and cooldown.")
    if not isinstance(moon, dict) or not _whole(moon.get("needs"), 1, 10 ** 15) or not 1 <= moon.get("growth", 0) <= 10:
        raise bad("moonrise needs how much powder it asks for, and its growth.")
    if not isinstance(gifts, dict) or any(not _whole(gifts.get(key), 0, 100_000) for key in ("chance", "burst", "snack", "potion", "token", "token_days", "token_coins")) \
            or gifts["burst"] < 1:
        raise bad("gifts need whole numbers, and a burst of powder must be possible.")
    return data


CATALOG = load_catalog()
_FLOWERS = {flower["id"]: {**flower, "rung": rung} for rung, flower in enumerate(CATALOG["flowers"])}
_HELPERS = {helper["id"]: helper for helper in CATALOG["helpers"]}
_COSMETICS = {thing["id"]: thing for thing in CATALOG["cosmetics"]}
_UPGRADES = {thing["id"]: thing for thing in CATALOG["upgrades"]}


# --------------------------------------------------------------------------- the saved garden
def _empty() -> dict:
    return {"flower": None, "ready_at": 0, "grow": 0, "last": None}


def _starter() -> dict:
    """A brand-new garden: a few empty plots, and only the first flower to plant."""
    return {"plots": [_empty() for _ in range(CATALOG["plots"]["start"])], "unlocked": [CATALOG["flowers"][0]["id"]], "given": 0.0}


def fresh(now: int) -> dict:
    return {"gardens": [_starter()], "at": 0, "powder": 0.0, "squeezed": 0.0, "day": "", "coins_today": 0, "paid": 0, "earned": 0.0,
            "helpers": dict.fromkeys(TREES, 0), "cosmetics": [], "upgrades": {}, "moonrises": 0, "cooldowns": {}, "effects": {},
            "water": None, "feed": None, "tick": now, "press_at": now, "seen": now, "token_at": 0, "away": None}


def current(conn: sqlite3.Connection, user_id: int, now: int) -> dict:
    """This person's garden as saved (a new one if they have never opened it). Not caught up: see advance."""
    row = conn.execute("SELECT state FROM lumi_gardens WHERE user_id = ?", (user_id,)).fetchone()
    return {**fresh(now), **json.loads(row["state"])} if row else fresh(now)


def save(conn: sqlite3.Connection, user_id: int, state: dict) -> None:
    conn.execute("INSERT INTO lumi_gardens(user_id, state) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, "
                 "updated_at = datetime('now')", (user_id, json.dumps(state, separators=(",", ":"))))


# --------------------------------------------------------------------------- every number, in one place
def _build(pet: dict, helper: dict) -> float:
    """How much Lumi's own build lifts a helper: the skills she has learned in its tree, and the percentages her
    passives and fitted mods give to the stats that helper cares about."""
    learned = sum(1 for skill in pet["skills"] if lumibattle._NODES.get(skill, {}).get("tree") == helper["id"])
    extra = lumibattle.bonuses(pet)
    return SKILL_MULT ** learned * (1 + sum(extra[key] for key in helper["mods"]) / 100)


def rates(state: dict, pet: dict, now: int) -> dict:
    """The garden's numbers right now: what a press squeezes, how often one is lucky, what the press does by itself,
    how fast flowers grow, how long they stay fresh, and what is kept while away."""
    press, level, ups, effects = CATALOG["press"], state["helpers"], state["upgrades"], state["effects"]
    lift = {tree: _build(pet, _HELPERS[tree]) for tree in TREES}
    double = 2 if effects.get("double", 0) > now else 1
    everything = (1 + 0.05 * level["paladin"] * lift["paladin"]) * (1 + LEVEL_BONUS * (pet["level"] - 1))
    strong = 1 + 0.25 * ups.get("strong-press", 0)
    frenzy = effects.get("frenzy_by", 1) if effects.get("frenzy", 0) > now else 1
    return {
        "tap": press["tap"] * (1 + 0.5 * level["fighter"]) * lift["fighter"] * everything * strong * frenzy * double,
        "lucky": 1.0 if effects.get("lucky", 0) > now else min(LUCKY_CAP, (press["lucky_chance"] + level["thief"] * lift["thief"]) / 100),
        "lucky_by": press["lucky"],
        "idle": 0.2 * level["monk"] * lift["monk"] * everything * strong,       # powder a second, before any doubling
        "double": double,
        "growth": max(0.4, 1 / (1 + 0.03 * level["mage"] * lift["mage"])) * 0.95 ** ups.get("rich-soil", 0),     # a share of each flower's time
        "fresh": CATALOG["fresh"] * (1 + 0.1 * level["cleric"] * lift["cleric"]) * (1 + 0.25 * ups.get("long-bloom", 0)),
        "kept": min(1.0, press["away_kept"] / 100 + 0.03 * level["cleric"] * lift["cleric"]),
        "away_ms": (press["away_hours"] + ups.get("night-shift", 0)) * 60 * MIN,
        "yield": everything * double,
        "cool": 0.9 ** ups.get("quick-paws", 0),
        "gift": min(GIFT_CAP, (CATALOG["gifts"]["chance"] + 0.3 * level["thief"] * lift["thief"] + ups.get("lucky-clover", 0)) / 100),
    }


def price(state: dict) -> float:
    """Powder for the next coin: each further band of coins in a day costs twice the band before."""
    press = CATALOG["press"]
    return press["powder_per_coin"] * 2.0 ** (state["coins_today"] // press["band"])


def _squeeze(conn: sqlite3.Connection, user_id: int, state: dict, powder: float, by: float = 1.0) -> tuple[float, int]:
    """Press up to this much powder from the hopper (a lucky press counts `by` times over) and pay any whole coins it
    makes, once, by the count of coins ever paid. Returns (powder used, coins paid)."""
    today = coins.today(conn)
    if state["day"] != today:                             # a new day: the press is quick again
        state["day"], state["coins_today"] = today, 0
    used = max(0.0, min(state["powder"], powder))
    state["powder"] -= used
    state["squeezed"] += used * by
    made = 0
    while state["squeezed"] >= price(state) and made < 100_000:
        state["squeezed"] -= price(state)
        state["coins_today"] += 1
        made += 1
    if made:
        state["paid"] += made
        coins.prize(conn, user_id, made, GAME, f"lumi-garden:{user_id}:{state['paid']}")
    return used, made


def advance(conn: sqlite3.Connection, user_id: int, state: dict, pet: dict, now: int) -> None:
    """Catch the press up to now. With the page open (looks a few seconds apart) the helpers squeeze at their full
    rate; a longer gap is time away, paid at the kept share and only up to the away limit. Flowers need nothing here:
    they are ready when the clock says so."""
    start, now = state["tick"], max(now, state["tick"])
    gap = now - start
    if gap:
        numbers, present = rates(state, pet, now), CATALOG["press"]["present_seconds"] * 1000
        counted = gap if gap <= present else present + min(gap - present, numbers["away_ms"]) * numbers["kept"]
        counted += max(0, min(now, state["effects"].get("double", 0)) - start)          # the doubled stretch counts twice
        used, made = _squeeze(conn, user_id, state, numbers["idle"] * counted / 1000)
        if gap > present:
            state["away"] = {"ms": gap, "powder": used, "coins": made}                  # for the page to say once
    state["tick"] = now
    if state["feed"] and now > state["feed"]["until"] + MIN:                           # a fertilizer round nobody finished
        state["feed"] = None


# --------------------------------------------------------------------------- flowers
def _stage(plot: dict, numbers: dict, now: int) -> str:
    if not plot["flower"]:
        return "empty"
    if now < plot["ready_at"]:
        return "growing"
    return "overgrown" if now > plot["ready_at"] + numbers["fresh"] * plot["grow"] else "ready"


def _growing(state: dict, now: int):
    """Every flower still on its way, in every garden."""
    return (plot for garden in state["gardens"] for plot in garden["plots"] if plot["flower"] and plot["ready_at"] > now)


def _plant(plot: dict, flower: dict, numbers: dict, now: int) -> None:
    grow = max(1000, int(flower["minutes"] * MIN * numbers["growth"]))
    plot.update(flower=flower["id"], grow=grow, ready_at=now + grow, last=flower["id"])


def _trim(state: dict, garden: dict, plot: dict, numbers: dict, now: int) -> float:
    """Harvest one ready flower: its powder goes to the hopper and counts towards this garden's ladder. A cut flower
    leaves the plot empty; a perennial starts growing again."""
    flower = _FLOWERS[plot["flower"]]
    got = flower["powder"] * numbers["yield"] * (0.5 if _stage(plot, numbers, now) == "overgrown" else 1)
    state["powder"] += got
    state["earned"] += got
    garden["given"] += got
    if flower["sort"] == "cut":
        plot.update(flower=None, ready_at=0, grow=0)
    else:
        _plant(plot, flower, numbers, now)
    return got


def _gift(conn: sqlite3.Connection, user_id: int, state: dict, pet: dict, got: float, now: int, rng, pantry: list[str], held: int) -> dict | None:
    """Something extra turned up in a harvest: mostly a burst of powder, sometimes a snack or a potion, and rarely a
    Star Capsule's worth of coins (at most one in so many days). Whatever can't be given becomes a burst."""
    odds = CATALOG["gifts"]
    roll = rng.randrange(odds["burst"] + odds["snack"] + odds["potion"] + odds["token"])
    if roll < odds["token"] and now - state["token_at"] >= odds["token_days"] * 24 * 60 * MIN:
        state["token_at"] = now
        paid = coins.prize(conn, user_id, odds["token_coins"], GAME, f"lumi-garden-token:{user_id}:{now}")
        return {"kind": "token", "coins": paid}
    roll -= odds["token"]
    if 0 <= roll < odds["potion"] and pet["potions"] < lumibattle.POTION_CAP:
        pet["potions"] += 1
        return {"kind": "potion"}
    roll -= odds["potion"]
    room = [snack for snack in pantry if pet["inventory"].get(snack, 0) < held]
    if 0 <= roll < odds["snack"] and room:
        snack = room[rng.randrange(len(room))]
        pet["inventory"][snack] = pet["inventory"].get(snack, 0) + 1
        return {"kind": "snack", "item": snack}
    burst = got * 5
    state["powder"] += burst
    state["earned"] += burst
    return {"kind": "burst", "powder": burst}


def _skip(state: dict, now: int, by) -> int:
    """Move every growing flower forward by by(plot) milliseconds. Returns how many were moved."""
    moved = 0
    for plot in _growing(state, now):
        plot["ready_at"] = max(now, plot["ready_at"] - int(by(plot)))
        moved += 1
    return moved


def moonrise_needs(state: dict) -> float:
    return CATALOG["moonrise"]["needs"] * CATALOG["moonrise"]["growth"] ** state["moonrises"]


def _cool(state: dict, key: str, seconds: float, numbers: dict, now: int) -> None:
    """Refuse something still cooling down, then start its cooldown."""
    left = state["cooldowns"].get(key, 0) - now
    if left > 0:
        raise GardenError(f"Not yet: ready again in {max(1, round(left / 1000))} seconds.")
    state["cooldowns"][key] = now + int(seconds * 1000 * numbers["cool"])


def _ability(pet: dict, move: str) -> tuple[str, float]:
    """Which tree's ability an equipped move fires, and how strongly: deeper in the tree is stronger, and her two
    first moves are small versions."""
    node = lumibattle._NODES.get(move)
    if node:
        return node["tree"], 1 + 0.25 * node["index"]
    return ("mage" if lumibattle._BASICS[move]["magic"] else "fighter"), 0.5


# --------------------------------------------------------------------------- one choice
def act(conn: sqlite3.Connection, user_id: int, pet: dict, state: dict, action: str, now: int, rng=None, *, item: str | None = None,
        plot: int | None = None, count: int = 0, picks: list | None = None, pantry: list[str] | None = None, held: int = 99) -> dict | None:
    """One thing the person chose in the garden. Changes the garden and perhaps the pet (the caller saves both) and
    returns a note of what happened, for the page to say. Call inside a transaction, after advance."""
    rng = rng or random.SystemRandom()
    numbers, picks = rates(state, pet, now), picks or []
    garden = state["gardens"][state["at"]]
    state["seen"], state["away"] = now, None              # she has looked: nothing ready is news any more

    def bed() -> dict:
        if plot is None or not 0 <= plot < len(garden["plots"]):
            raise GardenError("Choose one of the plots.")
        return garden["plots"][plot]

    def harvest(beds: list[dict], replant: bool) -> dict:
        got, gift = 0.0, None
        for one in beds:
            kind = one["flower"]
            got += (gave := _trim(state, garden, one, numbers, now))
            if replant and not one["flower"]:              # a cut flower: put the same kind straight back in
                _plant(one, _FLOWERS[kind], numbers, now)
            if gift is None and rng.random() < numbers["gift"]:
                gift = _gift(conn, user_id, state, pet, gave, now, rng, pantry or [], held)
        return {"trimmed": len(beds), "powder": got, "gift": gift}

    if action == "look":
        return None
    if action == "press":
        allowed = int(min(now - state["press_at"], PRESS_WINDOW) / 1000 * CATALOG["press"]["max_rate"]) + PRESS_SPARE
        pressed = max(0, min(count, allowed))
        state["press_at"] = now
        lucky = sum(1 for _ in range(pressed) if rng.random() < numbers["lucky"])
        plain = _squeeze(conn, user_id, state, numbers["tap"] * (pressed - lucky))
        bonus = _squeeze(conn, user_id, state, numbers["tap"] * lucky, numbers["lucky_by"])
        return {"pressed": pressed, "lucky": lucky, "powder": plain[0] + bonus[0], "coins": plain[1] + bonus[1]}
    if action == "plot":
        rules = CATALOG["plots"]
        if len(garden["plots"]) >= rules["max"]:
            raise GardenError("This garden has every plot it can hold.")
        cost = plot_price(garden)
        if not coins.spend(conn, user_id, cost, GAME, "plot"):
            raise GardenError("Not enough coins for another plot.")
        garden["plots"].append(_empty())
        return {"bought": "plot", "paid": cost}
    if action == "plant":
        flower = _FLOWERS.get(item or "")
        if flower is None or item not in garden["unlocked"]:
            raise GardenError("This garden hasn't unlocked that flower yet.")
        if bed()["flower"]:
            raise GardenError("Something is already growing there.")
        _plant(bed(), flower, numbers, now)
        return {"planted": item}
    if action == "trim":
        if _stage(bed(), numbers, now) not in ("ready", "overgrown"):
            raise GardenError("Nothing there is ready to trim yet.")
        return harvest([bed()], False)
    if action == "trim_all":
        ready = [one for one in garden["plots"] if _stage(one, numbers, now) in ("ready", "overgrown")]
        if not ready:
            raise GardenError("Nothing is ready to trim yet.")
        return harvest(ready, True)
    if action == "unlock":
        flower = _FLOWERS.get(item or "")
        if flower is None or item in garden["unlocked"]:
            raise GardenError("That isn't a flower this garden can unlock.")
        if flower["rung"] != len(garden["unlocked"]):
            raise GardenError("Unlock the flowers before it first.")
        if garden["given"] < flower["needs"]:
            raise GardenError(f"This garden needs to have given {flower['needs']:,} starpowder first.")
        if pet["level"] < flower["level"]:
            raise GardenError(f"{flower['name']} needs Lumi to be level {flower['level']}.")
        if flower["stardust"]:                             # a boutique flower: starpowder only
            if state["powder"] < flower["stardust"]:
                raise GardenError("Not enough starpowder for that.")
            state["powder"] -= flower["stardust"]
        elif not coins.spend(conn, user_id, flower["coins"], GAME, "seed"):
            raise GardenError("Not enough coins for that.")
        garden["unlocked"].append(item)
        return {"unlocked": item}
    if action == "cosmetic":
        thing = _COSMETICS.get(item or "")
        if thing is None or item in state["cosmetics"]:
            raise GardenError("That isn't something new for the garden.")
        if state["powder"] < thing["stardust"]:
            raise GardenError("Not enough starpowder for that.")
        state["powder"] -= thing["stardust"]
        state["cosmetics"].append(item)
        return {"bought": item}
    if action == "helper":
        helper = _HELPERS.get(item or "")
        if helper is None:
            raise GardenError("That isn't one of Lumi's helpers.")
        cost = helper_cost(state, item)
        if state["powder"] < cost:
            raise GardenError("Not enough starpowder for that.")
        state["powder"] -= cost
        state["helpers"][item] += 1
        return {"helper": item, "level": state["helpers"][item]}
    if action == "ability":
        if item not in pet["moves"] or (item not in lumibattle._NODES and item not in lumibattle._BASICS):
            raise GardenError("Lumi doesn't have that move ready. Her moves are chosen in Battle.")
        tree, strength = _ability(pet, item)
        about, effects = CATALOG["abilities"][tree], state["effects"]
        if tree == "cleric" and not any(_stage(one, numbers, now) == "overgrown" for g in state["gardens"] for one in g["plots"]):
            raise GardenError("Nothing is overgrown just now.")
        if tree == "mage" and not any(True for _ in _growing(state, now)):
            raise GardenError("Nothing is growing just now.")
        _cool(state, "move:" + item, about["cooldown"], numbers, now)
        if tree == "fighter":
            effects["frenzy"], effects["frenzy_by"] = now + about["seconds"] * 1000, 1 + 2 * strength
        elif tree == "thief":
            effects["lucky"] = now + int(about["seconds"] * strength * 1000)
        elif tree == "monk":
            used, made = _squeeze(conn, user_id, state, (numbers["idle"] * about["seconds"] + numbers["tap"] * 20) * strength)
            return {"ability": tree, "powder": used, "coins": made}
        elif tree == "mage":
            return {"ability": tree, "moved": _skip(state, now, lambda one: 0.04 * strength * one["grow"])}
        elif tree == "cleric":
            fresh_again = [one for g in state["gardens"] for one in g["plots"] if _stage(one, numbers, now) == "overgrown"]
            for one in fresh_again:
                one["ready_at"] = now
            return {"ability": tree, "moved": len(fresh_again)}
        else:
            effects["double"] = now + int(about["seconds"] * strength * 1000)
        return {"ability": tree}
    if action == "water_start":
        rules = CATALOG["water"]
        _cool(state, "water", rules["cooldown"], numbers, now)
        state["water"] = {"seq": [rng.randrange(4) for _ in range(rules["rounds"])], "round": 1}
        return {"water": "start"}
    if action == "water_answer":
        game, rules = state["water"], CATALOG["water"]
        if not game:
            raise GardenError("The watering cans are put away. Start a round first.")
        right = picks == game["seq"][:game["round"]]
        if right and game["round"] < rules["rounds"]:
            game["round"] += 1
            return {"water": "more", "round": game["round"]}
        done = game["round"] if right else game["round"] - 1
        state["water"] = None
        moved = _skip(state, now, lambda one: rules["share"] / 100 * done * one["grow"])
        return {"water": "done", "rounds": done, "moved": moved, "right": right}
    if action == "feed_start":
        rules = CATALOG["feed"]
        _cool(state, "feed", rules["cooldown"], numbers, now)
        state["feed"] = {"until": now + rules["seconds"] * 1000}
        return {"feed": "start"}
    if action == "feed_end":
        game, rules = state["feed"], CATALOG["feed"]
        if not game:
            raise GardenError("The fertilizer is put away. Start a round first.")
        state["feed"] = None
        counted = max(0, min(count, rules["seconds"] * CATALOG["press"]["max_rate"] + PRESS_SPARE)) if now <= game["until"] + 5000 else 0
        minutes = min(rules["max"], counted // rules["per"] * rules["minutes"])
        return {"feed": "done", "presses": counted, "minutes": minutes, "moved": _skip(state, now, lambda one: minutes * MIN)}
    if action == "moonrise":
        if len(garden["plots"]) < CATALOG["plots"]["max"] or garden["given"] < moonrise_needs(state):
            raise GardenError("This garden isn't ready for a moonrise yet: it needs every plot, and more starpowder given.")
        if item == "garden":
            if len(state["gardens"]) >= MAX_GARDENS:
                raise GardenError("Lumi has all the gardens she can look after.")
        elif item not in _UPGRADES or state["upgrades"].get(item, 0) >= _UPGRADES[item]["max"]:
            raise GardenError("Choose a permanent upgrade, or another garden.")
        state["gardens"][state["at"]] = _starter()         # this garden starts over: plots, flowers and its ladder
        state["moonrises"] += 1
        if item == "garden":
            state["gardens"].append(_starter())
        else:
            state["upgrades"][item] = state["upgrades"].get(item, 0) + 1
        return {"moonrise": item}
    if action == "garden":
        if not 0 <= count < len(state["gardens"]):
            raise GardenError("Lumi doesn't have that garden.")
        state["at"] = count
        return None
    raise GardenError("Lumi doesn't know that one.")


def plot_price(garden: dict) -> int:
    rules = CATALOG["plots"]
    return round(rules["price"] * rules["growth"] ** (len(garden["plots"]) - rules["start"]))


def helper_cost(state: dict, helper: str) -> int:
    return floor(_HELPERS[helper]["cost"] * _HELPERS[helper]["growth"] ** state["helpers"][helper])


# --------------------------------------------------------------------------- reminders
def ready_since(state: dict, now: int) -> str | None:
    """Whether slow flowers have become ready that the person hasn't seen. Returns the moment that names this batch
    (so one batch is one reminder), or None. Quick flowers never count, or a phone would buzz all day: they are for
    someone sitting with the garden open."""
    moments = []
    for garden in state["gardens"]:
        moments += [plot["ready_at"] for plot in garden["plots"] if plot["flower"] and state["seen"] < plot["ready_at"] <= now
                    and _FLOWERS[plot["flower"]]["minutes"] >= CATALOG["reminder_minutes"]]
    return str(min(moments)) if moments else None


# --------------------------------------------------------------------------- what the page is told
def view(state: dict, pet: dict, now: int) -> dict:
    """The garden as the page draws it, with every price and time worked out here so the page never does the sums.
    Times are milliseconds from `now` (zero or less: already there). A watering round shows only the part of its
    sequence that is to be repeated this round."""
    numbers, garden = rates(state, pet, now), state["gardens"][state["at"]]
    plots = [{"flower": plot["flower"], "last": plot["last"], "stage": _stage(plot, numbers, now), "ready_in": plot["ready_at"] - now if plot["flower"] else 0,
              "grow": plot["grow"]} for plot in garden["plots"]]
    flowers = []
    for flower in CATALOG["flowers"]:
        unlocked = flower["id"] in garden["unlocked"]
        flowers.append({**flower, "unlocked": unlocked, "next": not unlocked and _FLOWERS[flower["id"]]["rung"] == len(garden["unlocked"]),
                        "powder_now": flower["powder"] * numbers["yield"], "minutes_now": flower["minutes"] * numbers["growth"]})
    moves = []
    for move in pet["moves"]:
        if move in lumibattle._NODES or move in lumibattle._BASICS:
            tree, strength = _ability(pet, move)
            entry = lumibattle._NODES.get(move) or lumibattle._BASICS[move]
            moves.append({"id": move, "move": entry["name"], "tree": tree, "name": CATALOG["abilities"][tree]["name"], "about": CATALOG["abilities"][tree]["about"],
                          "strength": strength, "ready_in": state["cooldowns"].get("move:" + move, 0) - now})
    water, feed, full = state["water"], state["feed"], len(garden["plots"]) >= CATALOG["plots"]["max"]
    return {
        "at": state["at"], "gardens": len(state["gardens"]), "plots": plots, "plot_price": None if full else plot_price(garden),
        "given": garden["given"], "flowers": flowers,
        "powder": state["powder"], "squeezed": state["squeezed"], "price": price(state), "coins_today": state["coins_today"], "band": CATALOG["press"]["band"],
        "rates": {key: numbers[key] for key in ("tap", "lucky", "lucky_by", "idle", "double", "growth", "fresh", "kept", "yield", "gift")} | {"away_hours": numbers["away_ms"] / 60 / MIN},
        "helpers": [{"id": helper["id"], "name": helper["name"], "about": helper["about"], "level": state["helpers"][helper["id"]], "cost": helper_cost(state, helper["id"])}
                    for helper in CATALOG["helpers"]],
        "moves": moves,
        "effects": {key: state["effects"][key] - now for key in ("frenzy", "lucky", "double") if state["effects"].get(key, 0) > now},
        "water": {"ready_in": state["cooldowns"].get("water", 0) - now, "rounds": CATALOG["water"]["rounds"], "share": CATALOG["water"]["share"],
                  "show": water["seq"][:water["round"]] if water else None},
        "feed": {"ready_in": state["cooldowns"].get("feed", 0) - now, "seconds": CATALOG["feed"]["seconds"], "per": CATALOG["feed"]["per"], "max": CATALOG["feed"]["max"],
                 "until_in": feed["until"] - now if feed else None},
        "cosmetics": [{**thing, "owned": thing["id"] in state["cosmetics"]} for thing in CATALOG["cosmetics"]],
        "upgrades": [{**thing, "count": state["upgrades"].get(thing["id"], 0)} for thing in CATALOG["upgrades"]],
        "moonrise": {"needs": moonrise_needs(state), "ready": full and garden["given"] >= moonrise_needs(state), "count": state["moonrises"]},
        "away": state["away"], "max_rate": CATALOG["press"]["max_rate"],
    }
