"""Lumi Pet's battles: turn-based fights against the creatures of lumipet_data/battle.json, with levels, six skill
trees and magitek/cybertek mods.

The core is ported from MommyBot's Touhou Trader (src/touhou/battleRules.js and battles.js): its damage formula,
accuracy, speed order, Defend, Potion and Run, its EXP curve and level cap, one fight at a time, and the turn number
that refuses a stale button. None of its characters, spell names or art come across: every enemy and move is ours.

Added on top: physical moves use attack against defense while spells use magic against ward and cost MP; every enemy
has its own elemental weaknesses and resistances; and enemies scale to Lumi (her level, give or take two).

Stat points (Doll's design, 2026-11-11, Pokémon style): each level raises every stat by half of what it used to by
itself, and gives STAT_KEYS-many points to place by hand (one per stat if spread evenly), each worth the other half of
that stat's growth. So a Lumi with her points spread evenly has exactly the numbers she had before points existed.
Auto places them from her skill trees and the moves she uses most (auto_weights). Fight items (KIT) are bought between
fights: a mana potion, and X-Attack, X-Defend and X-Speed, which raise a stat by one stage for the rest of the fight.

Two ways to fight: a solo fight (resolve: Lumi and the creature, the faster first), and a party's co-op fight
(team_start, team_act: up to three Lumis against one to three creatures, in rounds: every Lumi in seat order, then
every creature). lumiparty.py keeps a co-op fight in its party; this module only plays it.

The server rolls everything. The page only says which button was pressed. `rng` is anything with random() and
randrange(n): a secure one in play, a seeded one in tests.

This module never loads or saves a pet: callers (lumipet.py, web/games.py) hand it the pet they loaded and save it.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from math import ceil, floor
from pathlib import Path

from mnemerynne import coins

GAME = "lumi-pet"
MAX_LEVEL = 50
PACE = 1.7                              # all damage is scaled by this, so a fight is about 5 to 10 turns
NODE_COSTS = (1, 1, 2, 2, 3, 3, 5, 5)   # skill points for a tree's eight nodes, in order: 22 for a whole tree
RESPEC_PRICE = 100                      # coins to hand every skill point back
POTION_PRICE, POTION_CAP = 20, 10
HEAL_PRICE = 50                         # coins to wake her at once after a defeat
FAINT_MS = 10 * 60_000                  # otherwise she naps this long, for free
MEND_MS = FAINT_MS                      # waiting mends a whole health bar in this long, so half a bar takes half of it
NAP_MEND = 2                            # a nap mends this many times faster than waiting
POTION_MEND = 0.5                       # a potion gives back this share of her health
MOVE_SLOTS = 4
DEFEND_MP = 6                           # MP that Defend gives back
STATUS_TURNS, STATUS_HURT = 3, 6        # poison and burn: this many turns, this percent of full health each
STUN_CHANCE = 30
ELEMENTS = ("fire", "ice", "wind", "spirit", "holy", "dark")
# Touhou Trader's wheel, read as attacker -> defender: each element is strong against one and weak against one.
WHEEL = {"fire": {"ice": 1.5, "dark": 0.75}, "ice": {"wind": 1.5, "fire": 0.75}, "wind": {"spirit": 1.5, "ice": 0.75},
         "spirit": {"holy": 1.5, "wind": 0.75}, "holy": {"dark": 1.5, "spirit": 0.75}, "dark": {"fire": 1.5, "holy": 0.75}}
GLOW_ELEMENT = {"rose": "fire", "lagoon": "ice", "starlight": "holy", "zephyr": "wind", "wisp": "spirit", "umbra": "dark"}
GLOW_MATCH = 1.1                        # a spell of Lumi's own glow element
# tier: (health, attack, defense multipliers, bonus EXP). Tuned by simulation so commons are a warm-up and a
# legendary needs the right element (see the plan's balance table).
TIERS = {"common": (0.60, 0.80, 0.85, 0), "uncommon": (0.75, 0.90, 0.92, 5), "rare": (0.90, 0.98, 1.00, 12),
         "epic": (1.10, 1.06, 1.08, 25), "legendary": (1.40, 1.15, 1.15, 50)}
ENEMY_HP_GROWTH = 1.0                   # enemy health grows by up to this much more by level 50, to keep pace with her skills
# The five mod slots, in the order they open: (kind, the level that opens it). Three cybertek, two magitek.
MOD_SLOTS = (("cybertek", 10), ("magitek", 15), ("cybertek", 25), ("magitek", 35), ("cybertek", 45))
BONUS_KEYS = ("attack", "defense", "magic", "ward", "speed", "hp", "mp", "crit", "dodge", "coins")
EFFECTS = (None, "pierce", "crit", "first", "poison", "burn", "stun", "ward", "drain")
BODIES = ("blob", "spark", "bug", "wing", "sprout", "imp", "crab", "cat", "golem", "ghost", "bird", "hound", "jelly", "crystal", "drake")
_ID = re.compile(r"^[a-z0-9-]{1,40}$")

# Stat points. GROWTH is what each stat used to gain per level by itself (base_stats); now half of it is fixed and the
# other half comes from points. A point in a stat is worth GROWTH / 2 of it.
STAT_KEYS = ("hp", "attack", "defense", "magic", "ward", "speed", "mp")
STAT_NAMES = {"hp": "Health", "attack": "Attack", "defense": "Defense", "magic": "Magic", "ward": "Ward", "speed": "Speed", "mp": "MP"}
GROWTH = {"hp": 8, "attack": 2, "defense": 1.5, "magic": 2, "ward": 1.5, "speed": 1, "mp": 2}
POINTS_PER_LEVEL = len(STAT_KEYS)       # one per stat, if spread evenly
POINT_CAP = 3                           # at most this many points a level in any one stat (three times an even share)
STAT_RESET_PRICE = 100                  # coins to take every placed point back (the first time after points arrived is free)
# What each skill tree leans on, for Auto. Every stat also starts with a weight of 1, so none is ever left at nothing.
TREE_STATS = {"fighter": {"attack": 3, "hp": 2, "defense": 1}, "thief": {"speed": 3, "attack": 2},
              "monk": {"hp": 2, "defense": 2, "speed": 1, "attack": 1}, "mage": {"magic": 3, "mp": 2},
              "cleric": {"ward": 2, "mp": 2, "hp": 1, "magic": 1}, "paladin": {"defense": 2, "ward": 2, "hp": 2}}
# What using each kind of action leans on, for Auto: a punch, a spell, bracing, or a fight item.
USE_STATS = {"physical": {"attack": 2, "speed": 1}, "magic": {"magic": 2, "mp": 1}, "defend": {"defense": 1.5, "ward": 1.5}, "item": {"hp": 1}}

# Fight items, bought between fights (Lumi Dungeons → Battle) and used in a fight, on Lumi or, in a party, on a friend.
# An X item raises its stats by one stage (STAGE each, up to MAX_STAGE stages) until the fight ends.
KIT = {"ether": {"name": "Mana potion", "icon": "💧", "price": 25, "about": "Gives back half her MP"},
       "x-attack": {"name": "X-Attack", "icon": "⚔", "price": 30, "about": "Attack and magic up a stage for the rest of the fight", "stage": "attack"},
       "x-defend": {"name": "X-Defend", "icon": "⛨", "price": 30, "about": "Defense and ward up a stage for the rest of the fight", "stage": "defend"},
       "x-speed": {"name": "X-Speed", "icon": "➶", "price": 30, "about": "Speed up a stage: she goes first more, and dodges more", "stage": "speed"}}
KIT_CAP = 9                             # of each fight item she carries
STAGE, MAX_STAGE = 0.5, 2               # each stage adds half again; two stages at most (double)
STAGED = {"attack": "attack", "magic": "attack", "defense": "defend", "ward": "defend", "speed": "speed"}   # which stage lifts each stat
SPEED_DODGE = 8                         # extra percent to dodge for each stage of speed
# Co-op fights (lumiparty.py): up to three Lumis against one to three creatures.
TEAM_FOES = 3


class BattleError(ValueError):
    """Refused; the message is safe to show."""


# --------------------------------------------------------------------------- the catalog
def _check_move(move: dict, where: str, enemy: bool = False) -> None:
    if not move.get("name") or not isinstance(move.get("power"), int) or not 0 <= move["power"] <= 200 \
            or not isinstance(move.get("accuracy"), int) or not 30 <= move["accuracy"] <= 100 \
            or move.get("effect") not in EFFECTS or not 1 <= move.get("hits", 1) <= 5:
        raise ValueError(f"Lumi Pet's battles: {where} has a bad move.")
    if not enemy and (move.get("element") not in ELEMENTS + ("none", "glow") or not isinstance(move.get("magic"), bool)
                      or not 0 <= move.get("mp", 0) <= 50 or not 0 <= move.get("heal", 0) <= 100
                      or (move["power"] == 0 and not move.get("heal") and move.get("effect") != "ward")):
        raise ValueError(f"Lumi Pet's battles: {where} has a bad move.")


def _check_bonus(bonus: object, where: str) -> None:
    if not isinstance(bonus, dict) or not bonus or any(k not in BONUS_KEYS or not isinstance(v, int) or not 1 <= v <= 50 for k, v in bonus.items()):
        raise ValueError(f"Lumi Pet's battles: {where} has a bad bonus.")


def load_catalog(path: Path | None = None) -> dict:
    """Trees, mods and enemies, checked at start so a bad edit never surfaces in the middle of a fight."""
    data = json.loads((path or Path(__file__).parent / "lumipet_data" / "battle.json").read_text(encoding="utf-8"))
    seen: set[str] = set()

    def fresh(thing: dict, where: str) -> None:
        if not isinstance(thing, dict) or not _ID.match(str(thing.get("id", ""))) or thing["id"] in seen or not thing.get("name"):
            raise ValueError(f"Lumi Pet's battles: a bad or repeated id among the {where}.")
        seen.add(thing["id"])

    for move in data.get("basics") or [None]:
        fresh(move, "basic moves")
        _check_move(move, move["id"])
    for tree in data.get("trees") or [None]:
        fresh(tree, "trees")
        if len(tree.get("nodes", [])) != len(NODE_COSTS):
            raise ValueError(f"Lumi Pet's battles: the {tree['id']} tree needs {len(NODE_COSTS)} skills.")
        places, earlier = set(), {}                       # places taken, and each earlier skill's row
        for node in tree["nodes"]:
            fresh(node, f"{tree['id']} skills")
            # Where it sits in its tree, and which skills lead to it. A skill may only need ones listed before it, so
            # a tree can never loop, and anything learned under an older, simpler tree is still a valid build.
            needs, at = node.get("needs"), node.get("at")
            if not isinstance(needs, list) or any(need not in earlier for need in needs) or (not needs and earlier):
                raise ValueError(f"Lumi Pet's battles: {node['id']} must need skills listed before it in its tree (only the first needs none).")
            if not isinstance(at, list) or len(at) != 2 or not all(isinstance(n, int) for n in at) or not (0 <= at[0] <= 2 and 0 <= at[1] <= 5) or tuple(at) in places:
                raise ValueError(f"Lumi Pet's battles: {node['id']} needs its own place in the tree: [column 0-2, row 0-5].")
            if any(earlier[need] >= at[1] for need in needs):
                raise ValueError(f"Lumi Pet's battles: {node['id']} must sit on a lower row than the skills it needs, so the tree reads downwards.")
            places.add(tuple(at))
            earlier[node["id"]] = at[1]
            if node.get("kind") == "move":
                _check_move(node, node["id"])
            elif node.get("kind") == "passive":
                _check_bonus(node.get("bonus"), node["id"])
            else:
                raise ValueError(f"Lumi Pet's battles: {node['id']} is neither a move nor a passive.")
    for mod in data.get("mods") or [None]:
        fresh(mod, "mods")
        _check_bonus(mod.get("bonus"), mod["id"])
        if mod.get("tek") not in ("magitek", "cybertek") or not isinstance(mod.get("price"), int) or not 0 <= mod["price"] <= 100_000:   # 0: only found in the dungeon
            raise ValueError(f"Lumi Pet's battles: {mod['id']} needs a kind and a price.")
    for enemy in data.get("enemies") or [None]:
        fresh(enemy, "enemies")
        lists = [enemy.get(key) for key in ("weak", "resist", "immune")]
        if enemy.get("tier") not in TIERS or enemy.get("body") not in BODIES or enemy.get("element") not in ELEMENTS + ("none", "mirror") \
                or any(not isinstance(found, list) or any(e not in ELEMENTS for e in found) for found in lists) \
                or len(set(sum(lists, []))) != len(sum(lists, [])) or not re.match(r"^#[0-9a-f]{6}$", str(enemy.get("colour"))):
            raise ValueError(f"Lumi Pet's battles: {enemy['id']} has a bad tier, body, colour or element.")
        shape = enemy.get("shape")
        if not isinstance(shape, dict) or set(shape) != {"hp", "atk", "def", "res", "spd"} or any(not 0.3 <= v <= 2.5 for v in shape.values()):
            raise ValueError(f"Lumi Pet's battles: {enemy['id']} has a bad shape.")
        moves = enemy.get("moves") or []
        for move in moves:
            _check_move(move, enemy["id"], enemy=True)
        if not any(move.get("level") == 1 for move in moves) or any(not 1 <= move.get("level", 0) <= MAX_LEVEL for move in moves):
            raise ValueError(f"Lumi Pet's battles: {enemy['id']} needs a move it knows at level 1.")
    for tier in TIERS:                                    # every tier can always be fought
        if not any(enemy["tier"] == tier for enemy in data["enemies"]):
            raise ValueError(f"Lumi Pet's battles: no {tier} enemy.")
    return data


CATALOG = load_catalog()
_NODES = {node["id"]: {**node, "tree": tree["id"], "index": i} for tree in CATALOG["trees"] for i, node in enumerate(tree["nodes"])}
_BASICS = {move["id"]: move for move in CATALOG["basics"]}
_MODS = {mod["id"]: mod for mod in CATALOG["mods"]}
_ENEMIES = {enemy["id"]: enemy for enemy in CATALOG["enemies"]}


def fresh_fields() -> dict:
    """What a pet needs for battles; lumipet merges these under any save, so older pets gain them."""
    return {"level": 1, "exp": 0, "skills": [], "moves": list(_BASICS), "mods": [None] * len(MOD_SLOTS), "mods_owned": [],
            "potions": 0, "faint_until": 0, "wins": 0, "losses": 0, "wound": 0.0,
            "stat_points": dict.fromkeys(STAT_KEYS, 0), "auto_stats": False, "stat_reset_free": 0,   # placed points; Auto on level-up; a free reset waiting
            "used": {}, "kit": {}}                        # how often each move (and defend, items) was used, for Auto; fight items held


# --------------------------------------------------------------------------- her health between dungeon trips
def health(pet: dict) -> int:
    """Her health right now, outside the dungeon. `wound` is the share of it still missing (0: none, 1: all of it);
    lumipet.advance mends it as time passes. Never below 1, and never shown as full while any is missing."""
    full = stats(pet)["hp"]
    return full if pet["wound"] <= 0 else max(1, min(full - 1, floor(full * (1 - pet["wound"]))))


def hurt(pet: dict, hp: int) -> None:
    """Remember the health she came home with, as the share of it that is missing."""
    pet["wound"] = min(1.0, max(0.0, 1 - hp / stats(pet)["hp"]))


def mend(pet: dict, share: float) -> None:
    """Give back this share of her whole health (food, a potion, time passing)."""
    pet["wound"] = max(0.0, pet["wound"] - share)


def mends_in(pet: dict) -> int:
    """Milliseconds of plain waiting until she is whole again."""
    return ceil(pet["wound"] * MEND_MS)


# --------------------------------------------------------------------------- Lumi's numbers
def level_threshold(level: int) -> int:
    """EXP needed to leave this level (Touhou Trader's curve)."""
    return 20 + level * 15


def spent(pet: dict) -> int:
    return sum(NODE_COSTS[_NODES[skill]["index"]] for skill in pet["skills"] if skill in _NODES)


def points(pet: dict) -> int:
    """Skill points waiting to be spent: one per level after the first."""
    return pet["level"] - 1 - spent(pet)


def bonuses(pet: dict) -> dict[str, int]:
    """Every percentage her passives and fitted mods add up to."""
    total = dict.fromkeys(BONUS_KEYS, 0)
    sources = [_NODES[s].get("bonus") for s in pet["skills"] if s in _NODES] + \
              [_MODS[m]["bonus"] for i, m in enumerate(pet["mods"]) if m in _MODS and pet["level"] >= MOD_SLOTS[i][1]]
    for bonus in filter(None, sources):
        for key, amount in bonus.items():
            total[key] += amount
    return total


def base_stats(level: int) -> dict[str, float]:
    """Touhou Trader's stat lines, with a magic pair beside the physical one."""
    return {"hp": 50 + 8 * level, "attack": 10 + 2 * level, "defense": 8 + 1.5 * level, "magic": 10 + 2 * level,
            "ward": 8 + 1.5 * level, "speed": 10 + level, "mp": 20 + 2 * level}


def lumi_base(pet: dict) -> dict[str, float]:
    """Her numbers before passives and mods: level 1's, half of each level's growth since by itself, and the
    points she has placed (each worth the other half). Spread evenly this is exactly base_stats(level)."""
    one, gained, placed = base_stats(1), pet["level"] - 1, pet.get("stat_points") or {}
    return {key: one[key] + GROWTH[key] * gained / 2 + GROWTH[key] / 2 * min(int(placed.get(key, 0)), POINT_CAP * gained) for key in STAT_KEYS}


def stats(pet: dict) -> dict:
    """Lumi's fighting numbers right now: her level and placed points, lifted by passives and mods."""
    extra, base = bonuses(pet), lumi_base(pet)
    out = {key: floor(value * (1 + extra[key] / 100)) for key, value in base.items()}
    return {**out, "crit": extra["crit"], "dodge": extra["dodge"], "coins": extra["coins"]}


# --------------------------------------------------------------------------- stat points
def stat_points_earned(pet: dict) -> int:
    return POINTS_PER_LEVEL * (pet["level"] - 1)


def stat_points_left(pet: dict) -> int:
    """Points waiting to be placed."""
    return stat_points_earned(pet) - sum(pet["stat_points"].get(key, 0) for key in STAT_KEYS)


def stat_cap(pet: dict) -> int:
    """The most points any one stat may hold at her level."""
    return POINT_CAP * (pet["level"] - 1)


def _use_kind(action: str) -> str | None:
    """What a remembered action leans on: "physical", "magic", "defend" or "item" (None: running doesn't count)."""
    if action == "defend":
        return "defend"
    if action in KIT or action == "potion":
        return "item"
    move = _BASICS.get(action) or _NODES.get(action)
    return None if move is None else "magic" if move.get("magic") else "physical"


def remember(pet: dict, action: str) -> None:
    """Count one use of a move (by its id), "defend" or an item, for Auto."""
    if _use_kind(action):
        pet["used"][action] = pet["used"].get(action, 0) + 1


def auto_weights(pet: dict) -> dict[str, float]:
    """How much Auto favours each stat: 1 each, plus her skill trees (each learned skill counts) and the actions she
    uses most (their share of all she has used, so a long history and a short one weigh the same)."""
    weight = dict.fromkeys(STAT_KEYS, 1.0)
    for skill in pet["skills"]:
        node = _NODES.get(skill)
        for key, amount in (TREE_STATS.get(node["tree"], {}) if node else {}).items():
            weight[key] += amount / 2
    counted = {action: n for action, n in pet["used"].items() if _use_kind(action) and n > 0}
    total = sum(counted.values())
    for action, n in counted.items():
        for key, amount in USE_STATS[_use_kind(action)].items():
            weight[key] += amount * 3 * n / total
    return weight


def place_auto(pet: dict, count: int | None = None) -> dict[str, int]:
    """Place `count` points (all that are waiting, by default) the way auto_weights leans, one at a time to whichever
    stat is furthest below its fair share (the D'Hondt rule), never past the cap. Returns what was placed where."""
    count = stat_points_left(pet) if count is None else min(count, stat_points_left(pet))
    weight, cap, placed = auto_weights(pet), stat_cap(pet), dict.fromkeys(STAT_KEYS, 0)
    for _ in range(max(0, count)):
        room = [key for key in STAT_KEYS if pet["stat_points"].get(key, 0) < cap]
        if not room:
            break
        best = max(room, key=lambda key: (weight[key] / (1 + pet["stat_points"].get(key, 0)), -STAT_KEYS.index(key)))
        pet["stat_points"][best] = pet["stat_points"].get(best, 0) + 1
        placed[best] += 1
    return {key: n for key, n in placed.items() if n}


def level_up(pet: dict, gained: int) -> int:
    """Add EXP; levels follow (Touhou Trader's curve), and with Auto on their stat points place themselves.
    Returns how many levels she went up."""
    before = pet["level"]
    pet["exp"] += gained
    while pet["level"] < MAX_LEVEL and pet["exp"] >= level_threshold(pet["level"]):
        pet["exp"] -= level_threshold(pet["level"])
        pet["level"] += 1
    if pet["level"] == MAX_LEVEL:
        pet["exp"] = 0
    if pet["level"] > before and pet.get("auto_stats"):
        place_auto(pet)
    return pet["level"] - before


def settle_points(pet: dict) -> None:
    """A save from before stat points: give her every point her level has earned, placed by Auto so she is as strong
    as she was, and one free reset to place them herself. lumipet.load calls this once."""
    pet["stat_points"] = dict.fromkeys(STAT_KEYS, 0)
    place_auto(pet)
    pet["stat_reset_free"] = 1


def known_moves(pet: dict) -> list[dict]:
    """Every move she could equip: the two she was born with, and each move skill she has learned."""
    return list(_BASICS.values()) + [_NODES[s] for s in pet["skills"] if s in _NODES and _NODES[s]["kind"] == "move"]


def _defences(element: str) -> tuple[list[str], list[str]]:
    """What a creature of this element is weak to and resists, from the wheel."""
    return ([a for a in ELEMENTS if WHEEL[a].get(element) == 1.5], [a for a in ELEMENTS if WHEEL[a].get(element) == 0.75])


def _lumi(pet: dict, carry: dict | None = None) -> dict:
    """Lumi as she walks into a fight: her equipped moves, her glow as her element, and full health and MP unless
    `carry` says what she has left (in the dungeon, one fight's bruises go into the next)."""
    numbers, element = stats(pet), GLOW_ELEMENT.get(pet["glow"], "fire")
    known = {move["id"]: move for move in known_moves(pet)}
    moves = [{k: v for k, v in known[m].items() if k not in ("tree", "index", "kind", "needs", "at")} for m in pet["moves"] if m in known] or [dict(_BASICS["pounce"])]
    for move in moves:
        if move["element"] == "glow":                     # Glow Spark and Starfall take whatever colour she is
            move["element"] = element
    weak, resist = _defences(element)
    hp = max(1, min(numbers["hp"], carry["hp"])) if carry else numbers["hp"]
    mp = max(0, min(numbers["mp"], carry["mp"])) if carry else numbers["mp"]
    return {"name": pet.get("name") or "Lumi", "side": "lumi", "level": pet["level"], "element": element, "hp": hp, "hpMax": numbers["hp"],
            "mp": mp, "mpMax": numbers["mp"], "stats": numbers, "crit": numbers["crit"], "dodge": numbers["dodge"],
            "moves": moves, "weak": weak, "resist": resist, "immune": [], "status": {}, "stages": {}}


def _enemy(entry: dict, level: int, lumi_level: int, lumi_element: str) -> dict:
    """An enemy scaled to this fight. Its level follows Lumi's; its health also grows with the skill points she has
    had time to earn, so a level-50 fight is not shorter than a level-5 one."""
    health, attack, defense, _ = TIERS[entry["tier"]]
    base, shape = base_stats(level), entry["shape"]
    growth = 1 + ENEMY_HP_GROWTH * (lumi_level - 1) / (MAX_LEVEL - 1)
    hp = max(1, floor(base["hp"] * health * shape["hp"] * growth))
    element = lumi_element if entry["element"] == "mirror" else entry["element"]
    weak, resist = (list(entry["weak"]), list(entry["resist"]))
    if entry["element"] == "mirror":                      # the Prism Shard copies her glow, and breaks to whatever beats it
        weak, resist = _defences(lumi_element)[0], [lumi_element]
    moves = [{"name": m["name"], "power": m["power"], "accuracy": m["accuracy"], "magic": bool(entry.get("magic")), "element": element,
              "effect": m.get("effect"), "hits": m.get("hits", 1), "mp": 0} for m in entry["moves"] if m["level"] <= level]
    numbers = {"attack": floor(base["attack"] * attack * shape["atk"]), "magic": floor(base["attack"] * attack * shape["atk"]),
               "defense": floor(base["defense"] * defense * shape["def"]), "ward": floor(base["defense"] * defense * shape["res"]),
               "speed": floor(base["speed"] * shape["spd"])}
    return {"id": entry["id"], "name": entry["name"], "side": "enemy", "tier": entry["tier"], "body": entry["body"], "colour": entry["colour"],
            "level": level, "element": element, "hp": hp, "hpMax": hp, "stats": numbers, "crit": 0, "dodge": entry.get("dodge", 0),
            "thorns": entry.get("thorns", 0), "moves": moves, "weak": weak, "resist": resist, "immune": list(entry["immune"]), "status": {}, "stages": {}}


# --------------------------------------------------------------------------- one turn
def stat_of(fighter: dict, key: str) -> float:
    """A fighter's stat with its X-item stages: each stage adds STAGE of it."""
    return fighter["stats"][key] * (1 + STAGE * fighter.get("stages", {}).get(STAGED[key], 0))


def _multiplier(attacker: dict, defender: dict, move: dict) -> float:
    element = move["element"]
    if element == "none":
        return 1.0
    if element in defender["immune"]:
        return 0.0
    mult = 1.5 if element in defender["weak"] else 0.75 if element in defender["resist"] else 1.0
    return mult * (GLOW_MATCH if attacker["side"] == "lumi" and move["magic"] and element == attacker["element"] else 1.0)


def _strike(state: dict, attacker: dict, defender: dict, move: dict, rng, guarding: bool = False) -> None:
    """One use of a move: accuracy, element, attack against defense (or magic against ward), then its effect.
    The formula is Touhou Trader's, times PACE."""
    log = state["log"]
    if move.get("heal") or (move["power"] == 0 and move.get("effect") == "ward"):       # a move that helps its user
        if move.get("heal"):
            before = attacker["hp"]
            attacker["hp"] = min(attacker["hpMax"], attacker["hp"] + max(1, attacker["hpMax"] * move["heal"] // 100))
            log.append(f"{attacker['name']} used {move['name']}: +{attacker['hp'] - before} HP.")
        if move.get("effect") == "ward":
            attacker["status"]["ward"] = move.get("turns", 2) + 1                      # +1: this turn's end takes one off
            log.append(f"{attacker['name']} used {move['name']}: damage is halved for {move.get('turns', 2)} turns." if not move.get("heal")
                       else f"A ward shimmers around {attacker['name']}.")
        return
    dealt, mult = 0, _multiplier(attacker, defender, move)
    if mult == 0:
        log.append(f"{attacker['name']} used {move['name']}. It has no effect on {defender['name']}!")
        return
    # A treat's boost: the solo fight keeps one for Lumi in the state; in a co-op fight each Lumi carries her own.
    power_up = attacker["side"] == "lumi" and attacker.get("boost", state.get("boost")) == "power"
    guard_up = defender["side"] == "lumi" and defender.get("boost", state.get("boost")) == "guard"
    guarding = guarding or bool(defender.get("guarding"))  # a co-op Lumi who chose Defend this round
    dodge = defender["dodge"] + SPEED_DODGE * defender.get("stages", {}).get("speed", 0)
    for _ in range(move.get("hits", 1)):
        if defender["hp"] <= 0:
            break
        if rng.random() * 100 >= move["accuracy"] - (5 if guarding else 0) - dodge:
            log.append(f"{attacker['name']}'s {move['name']} missed!")
            continue
        power = stat_of(attacker, "magic" if move["magic"] else "attack")
        guard = stat_of(defender, "ward" if move["magic"] else "defense") * (0.7 if move.get("effect") == "pierce" else 1)
        damage = floor((((2 * attacker["level"] / 5 + 2) * move["power"] * power / max(1, guard)) / 50 + 2) * PACE)
        damage = floor(damage * (0.85 + rng.random() * 0.15) * mult)
        crit = rng.random() * 100 < attacker["crit"] + (25 if move.get("effect") == "crit" else 0)
        if crit:
            damage = floor(damage * 1.5)
        if power_up:
            damage = floor(damage * 1.1)                  # a treat's boost, eaten before the fight
        if guard_up:
            damage = floor(damage * 0.9)
        if guarding:
            damage = floor(damage * 0.5)
        if defender["status"].get("ward"):
            damage = floor(damage * 0.5)
        damage = max(1, damage)
        defender["hp"] = max(0, defender["hp"] - damage)
        dealt += damage
        log.append(f"{attacker['name']} used {move['name']}: {damage} damage" + (" (guarded)" if guarding else "") + (", a critical hit!" if crit else
                   " — super effective!" if mult > 1.2 else " — resisted." if mult < 1 else "."))
        if defender.get("thorns") and not move["magic"] and attacker["hp"] > 0:
            pricked = max(1, damage * defender["thorns"] // 100)
            attacker["hp"] = max(0, attacker["hp"] - pricked)
            log.append(f"{defender['name']}'s thorns prick {attacker['name']} for {pricked}.")
    if not dealt:
        return
    effect = move.get("effect")
    if effect == "drain":
        attacker["hp"] = min(attacker["hpMax"], attacker["hp"] + dealt // 2)
        log.append(f"{attacker['name']} drains {dealt // 2} HP.")
    elif effect in ("poison", "burn") and defender["hp"] > 0:
        defender["status"][effect] = STATUS_TURNS
        log.append(f"{defender['name']} is {'poisoned' if effect == 'poison' else 'burning'}!")
    elif effect == "stun" and defender["hp"] > 0 and rng.random() * 100 < STUN_CHANCE:
        defender["status"]["stun"] = 1
        log.append(f"{defender['name']} is stunned!")


def _stunned(state: dict, who: dict) -> bool:
    """Whether a stun costs this fighter its action; the stun is used up either way."""
    if not who["status"].pop("stun", 0):
        return False
    state["log"].append(f"{who['name']} is stunned and can't move!")
    return True


def resolve(state: dict, action: str, rng) -> None:
    """Play one turn in place: Lumi's chosen action and the enemy's reply (battleRules.js resolveTurn). Sets
    state["outcome"] to "victory", "defeat" or "ran" when the fight is over. Inventory and rewards are the caller's."""
    lumi, enemy = state["player"], state["enemy"]
    reply = enemy["moves"][rng.randrange(len(enemy["moves"]))]

    def enemy_acts(guarding: bool = False) -> None:
        if enemy["hp"] > 0 and lumi["hp"] > 0 and not _stunned(state, enemy):
            _strike(state, enemy, lumi, reply, rng, guarding)

    if _stunned(state, lumi):
        enemy_acts()
    elif action == "run":
        if rng.random() < 0.75:
            state["outcome"] = "ran"
            state["log"].append(f"{lumi['name']} darts away safely.")
            state["log"] = state["log"][-8:]
            return
        state["log"].append(f"{lumi['name']} couldn't get away!")
        enemy_acts()
    elif action == "potion" or action in KIT:
        use_item(state, lumi, lumi, action)
        enemy_acts()
    elif action == "defend":
        lumi["mp"] = min(lumi["mpMax"], lumi["mp"] + DEFEND_MP)
        state["log"].append(f"{lumi['name']} braces and gathers her glow.")
        enemy_acts(guarding=True)
    else:
        move = lumi["moves"][int(action.removeprefix("move"))]
        lumi["mp"] -= move.get("mp", 0)
        mine, theirs = stat_of(lumi, "speed"), stat_of(enemy, "speed")
        first = move.get("effect") == "first" or mine > theirs or (mine == theirs and rng.random() < 0.5)
        if first:
            _strike(state, lumi, enemy, move, rng)
            enemy_acts()
        else:
            enemy_acts()
            if lumi["hp"] > 0:
                _strike(state, lumi, enemy, move, rng)
    for who in (lumi, enemy):                             # the end of the turn: poison and burn bite, wards fade
        for hurt in ("poison", "burn"):
            if who["status"].get(hurt) and who["hp"] > 0 and lumi["hp"] > 0 and enemy["hp"] > 0:
                loss = max(1, who["hpMax"] * STATUS_HURT // 100)
                who["hp"] = max(0, who["hp"] - loss)
                state["log"].append(f"{who['name']} takes {loss} from the {hurt}.")
                who["status"][hurt] -= 1
        if who["status"].get("ward"):
            who["status"]["ward"] -= 1
        who["status"] = {key: left for key, left in who["status"].items() if left > 0}
    if lumi["hp"] <= 0:
        state["outcome"] = "defeat"
    elif enemy["hp"] <= 0:
        state["outcome"] = "victory"
    state["log"] = state["log"][-8:]


# --------------------------------------------------------------------------- fight items
def item_refusal(target: dict, item: str, giver: str = "") -> str | None:
    """Why this item would do nothing for this fighter (the words to show), or None if it helps."""
    who = target["name"]
    if target["hp"] <= 0:
        return f"{who} has fainted. Only a rest at home helps now."
    if item == "potion" and target["hp"] >= target["hpMax"]:
        return f"{who} is already at full health."
    if item == "ether" and target["mp"] >= target["mpMax"]:
        return f"{who}'s MP is already full."
    stage = KIT.get(item, {}).get("stage")
    if stage and target.get("stages", {}).get(stage, 0) >= MAX_STAGE:
        return f"{who}'s {KIT[item]['name'].removeprefix('X-').lower()} can't go any higher this fight."
    return None


def has_item(pet: dict, item: str) -> bool:
    return pet["potions"] > 0 if item == "potion" else pet["kit"].get(item, 0) > 0


def take_item(pet: dict, item: str) -> None:
    """Use one up from her pocket. Refuses if there isn't one."""
    if not has_item(pet, item):
        raise BattleError("No potions left. Lumi Dungeons' Battle tab sells them between fights." if item == "potion"
                          else f"No {KIT[item]['name']} left. Lumi Dungeons' Battle tab sells them.")
    if item == "potion":
        pet["potions"] -= 1
    else:
        pet["kit"][item] -= 1


def use_item(state: dict, giver: dict, target: dict, item: str) -> None:
    """An item's effect on a fighter (the caller has checked it helps and taken it from a pocket)."""
    log, by = state["log"], "" if giver is target else f"{giver['name']} gives {target['name']} "
    if item == "potion":
        before = target["hp"]
        target["hp"] = min(target["hpMax"], target["hp"] + max(1, target["hpMax"] // 2))
        log.append((by + "a potion: " if by else "The potion restores ") + f"{target['hp'] - before} HP" + (" back." if by else "."))
    elif item == "ether":
        before = target["mp"]
        target["mp"] = min(target["mpMax"], target["mp"] + max(1, target["mpMax"] // 2))
        log.append((by + "a mana potion: " if by else "The mana potion restores ") + f"{target['mp'] - before} MP" + (" back." if by else "."))
    else:
        stage = KIT[item]["stage"]
        target.setdefault("stages", {})[stage] = min(MAX_STAGE, target.get("stages", {}).get(stage, 0) + 1)
        words = {"attack": "attack and magic", "defend": "defense and ward", "speed": "speed"}[stage]
        log.append((by + f"an {KIT[item]['name']}: " if by else f"{KIT[item]['name']}! ") + f"{target['name']}'s {words} go up.")


# --------------------------------------------------------------------------- a co-op fight (a party's: lumiparty.py)
def team_fighter(pet: dict, user_id: int, carry: dict | None) -> dict:
    """One Lumi for a co-op fight: her own numbers, moves, glow and waiting treat boost, and who she belongs to."""
    lumi = _lumi(pet, carry)
    return {**lumi, "user": user_id, "boost": pet.get("boost"), "guarding": False}


def team_foe(entry_id: str, level: int, lumi_level: int, lumi_element: str) -> dict:
    return _enemy(_ENEMIES[entry_id], level, lumi_level, lumi_element)


def team_start(lumis: list[dict], foes: list[dict]) -> dict:
    """A co-op fight: rounds in which every standing Lumi acts in seat order, then every standing creature.
    `turn` counts every action, so a stale button (the page showing an older turn) is refused."""
    names = [f["name"] for f in foes]
    fight = {"lumis": lumis, "foes": foes, "round": 1, "side": "lumis", "acted": [], "at": 0, "turn": 0, "outcome": None,
             "log": [("A wild " + names[0] + " appears!") if len(names) == 1 else "A pack appears: " + ", ".join(names) + "!"],
             "reward": None, "used": {}}
    fight["at"] = _team_next_lumi(fight)
    return fight


def _standing(fighters: list[dict]) -> list[int]:
    return [i for i, f in enumerate(fighters) if f["hp"] > 0]


def _team_next_lumi(fight: dict) -> int | None:
    """The next standing Lumi this round who hasn't acted, in seat order; None once they all have."""
    return next((i for i in _standing(fight["lumis"]) if i not in fight["acted"]), None)


def _team_over(fight: dict) -> bool:
    if not _standing(fight["foes"]):
        fight["outcome"] = "victory"
    elif not _standing(fight["lumis"]):
        fight["outcome"] = "defeat"
    return fight["outcome"] is not None


def team_refusal(fight: dict, seat: int, action: str, *, target: int | None = None) -> str | None:
    """Why this Lumi (by seat) can't do this now, or None. Items are checked by the caller, who knows the pockets."""
    if fight["outcome"]:
        return "That fight is over."
    if fight["at"] != seat:
        who = fight["lumis"][fight["at"]]["name"] if fight["at"] is not None else "the creatures"
        return f"It's {who}'s turn."
    lumi = fight["lumis"][seat]
    if action.startswith("move"):
        index = action.removeprefix("move")
        if not index.isdigit() or int(index) >= len(lumi["moves"]):
            return f"{lumi['name']} doesn't have that move ready."
        if lumi["moves"][int(index)].get("mp", 0) > lumi["mp"]:
            return "Not enough MP for that. Defend to gather some, or a friend can give a mana potion."
        if target is not None and not (0 <= target < len(fight["foes"]) and fight["foes"][target]["hp"] > 0):
            return "That creature is already down. Choose another."
    elif action not in ("defend", "run") and action != "potion" and action not in KIT:
        return f"{lumi['name']} doesn't know that one."
    return None


def team_act(fight: dict, seat: int, action: str, rng, *, target: int | None = None) -> None:
    """One Lumi's action (team_refusal has said yes; an item has been taken from its giver's pocket, and `target` is
    then the seat of the Lumi it is for). Then, if every standing Lumi has acted, the creatures act and a new round
    begins. Sets fight["outcome"] ("victory", "defeat" or "ran") when it is over."""
    lumi, log = fight["lumis"][seat], fight["log"]
    lumi["guarding"] = False                              # a Defend lasts until her next turn
    if action.startswith("move"):
        move = lumi["moves"][int(action.removeprefix("move"))]
        lumi["mp"] -= move.get("mp", 0)
        foes = _standing(fight["foes"])
        aim = target if target in foes else foes[0]
        _strike(fight, lumi, fight["foes"][aim], move, rng)   # (a move that helps its user, _strike gives to her)
        fight["used"].setdefault(str(lumi["user"]), []).append(move.get("id", ""))
    elif action == "defend":
        lumi["mp"] = min(lumi["mpMax"], lumi["mp"] + DEFEND_MP)
        lumi["guarding"] = True
        log.append(f"{lumi['name']} braces and gathers her glow.")
        fight["used"].setdefault(str(lumi["user"]), []).append("defend")
    elif action == "run":
        if rng.random() < 0.75:
            fight["outcome"] = "ran"
            log.append(f"{lumi['name']} calls everyone back, and the party slips away!")
            fight["log"] = log[-12:]
            fight["turn"] += 1
            return
        log.append(f"{lumi['name']} tries to lead everyone away, but the way is blocked!")
    else:                                                 # an item, on herself or a friend
        use_item(fight, lumi, fight["lumis"][seat if target is None else target], action)
        fight["used"].setdefault(str(lumi["user"]), []).append(action)
    fight["acted"].append(seat)
    fight["turn"] += 1
    if not _team_over(fight):
        _team_advance(fight, rng)
    fight["log"] = fight["log"][-12:]


def team_skip(fight: dict, rng, why: str) -> None:
    """The Lumi whose turn it is doesn't act (her player stepped away): she braces, so the round goes on."""
    seat = fight["at"]
    if seat is None or fight["outcome"]:
        return
    lumi = fight["lumis"][seat]
    lumi["guarding"] = True
    fight["log"].append(f"{why} {lumi['name']} braces.")
    fight["acted"].append(seat)
    fight["turn"] += 1
    _team_advance(fight, rng)
    fight["log"] = fight["log"][-12:]


def team_drop(fight: dict, seat: int, rng) -> None:
    """This Lumi's player has gone home mid-fight: she leaves with them. Her turn, if it was hers, passes on."""
    lumi = fight["lumis"][seat]
    lumi.update(gone=True, hp=0)
    fight["log"].append(f"{lumi['name']} heads home.")
    if fight["outcome"] or _team_over(fight):
        return
    if fight["at"] == seat:
        fight["acted"].append(seat)
        fight["turn"] += 1
        _team_advance(fight, rng)


def _team_advance(fight: dict, rng) -> None:
    """Move on to the next Lumi; once every standing Lumi has acted, every standing creature strikes a standing Lumi,
    poison and burn bite, and the next round begins."""
    fight["at"] = _team_next_lumi(fight)
    if fight["at"] is not None:
        return
    for foe in [fight["foes"][i] for i in _standing(fight["foes"])]:
        targets = _standing(fight["lumis"])
        if not targets or _stunned(fight, foe):
            continue
        move = foe["moves"][rng.randrange(len(foe["moves"]))]
        _strike(fight, foe, fight["lumis"][targets[rng.randrange(len(targets))]], move, rng)
    everyone = fight["lumis"] + fight["foes"]
    for who in everyone:                                  # the end of the round: poison and burn bite, wards fade
        for hurt_by in ("poison", "burn"):
            if who["status"].get(hurt_by) and who["hp"] > 0:
                loss = max(1, who["hpMax"] * STATUS_HURT // 100)
                who["hp"] = max(0, who["hp"] - loss)
                fight["log"].append(f"{who['name']} takes {loss} from the {hurt_by}.")
                who["status"][hurt_by] -= 1
        if who["status"].get("ward"):
            who["status"]["ward"] -= 1
        who["status"] = {key: left for key, left in who["status"].items() if left > 0}
    if _team_over(fight):
        return
    fight["round"] += 1
    fight["acted"] = []
    fight["at"] = _team_next_lumi(fight)
    while fight["at"] is not None and _stunned(fight, fight["lumis"][fight["at"]]):
        fight["acted"].append(fight["at"])                # stunned: this round's turn is lost
        fight["at"] = _team_next_lumi(fight)
    if fight["at"] is None:                               # everyone standing was stunned: the creatures go again
        _team_advance(fight, rng)


def team_reward(fight: dict) -> tuple[int, int]:
    """(EXP, coins) each Lumi in a won co-op fight gets: the whole of every creature's worth, for every one of them."""
    gained = sum(15 + foe["level"] * 3 + TIERS[foe["tier"]][3] for foe in fight["foes"])
    return gained, floor(gained * 0.6)


def team_finish(pet: dict, fight: dict, seat: int) -> dict:
    """A won co-op fight, for one member's pet: her own share (the whole of it, as Doll decided), her treat's luck,
    her coin bonus, her levels. Returns what she got. The caller saves the pet."""
    gained, prize = team_reward(fight)
    lumi = fight["lumis"][seat]
    if lumi.get("boost") == "luck":
        gained, prize = floor(gained * 1.25), floor(prize * 1.25)
    prize = floor(prize * (1 + lumi["stats"]["coins"] / 100))
    levels = level_up(pet, gained)
    pet["wins"] += 1
    return {"exp": gained, "coins": prize, "level": pet["level"], "levels": levels}


# --------------------------------------------------------------------------- fights, saved
def _view(row: sqlite3.Row) -> dict:
    return {**json.loads(row["state"]), "id": row["id"], "turn": row["turn"], "status": row["status"]}


def current(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """The fight in progress, so a reload or another device picks it up. It waits as long as it takes: walking
    away from the TV is never a defeat."""
    row = conn.execute("SELECT * FROM lumi_battles WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone()
    return _view(row) if row else None


def start(conn: sqlite3.Connection, user_id: int, pet: dict, tier: str, now: int, rng=None, *, enemy: str | None = None,
          level: int | None = None, carry: dict | None = None, dungeon: bool = False, party: str | None = None) -> dict:
    """Begin a fight against a random enemy of this tier (or any tier: "gamble", for a fifth more reward).
    The dungeon names the enemy and its level itself, carries Lumi's health in, and keeps the coins for her satchel.
    Changes the pet (her waiting treat boost is used); the caller saves it. Call inside a transaction."""
    rng = rng or random.SystemRandom()
    if tier not in TIERS and tier != "gamble":
        raise BattleError("Choose a kind of opponent, or Gamble.")
    if current(conn, user_id):
        raise BattleError("Lumi is already in a fight. Finish it, or run.")
    if pet["faint_until"] > now:
        raise BattleError("Lumi is still napping off her last fight. Let her rest, or wake her for coins.")
    pool = [e for e in CATALOG["enemies"] if tier == "gamble" or e["tier"] == tier]
    entry = _ENEMIES[enemy] if enemy else pool[rng.randrange(len(pool))]
    level = level if level is not None else max(1, min(MAX_LEVEL, pet["level"] + rng.randrange(5) - 2))
    lumi = _lumi(pet, carry)
    state = {"player": lumi, "enemy": _enemy(entry, level, pet["level"], lumi["element"]), "choice": tier, "boost": pet["boost"],
             "dungeon": dungeon, "outcome": None, "reward": None, "log": [f"A wild {entry['name']} appears!"]}
    if party:
        state["party"] = party                            # a party's dungeon fight (lumiparty.py): its end goes back to the party
    pet["boost"] = None
    made = conn.execute("INSERT INTO lumi_battles(user_id, state, created_at) VALUES (?, ?, ?)", (user_id, json.dumps(state, separators=(",", ":")), now))
    return {**state, "id": made.lastrowid, "turn": 0, "status": "active"}


def act(conn: sqlite3.Connection, user_id: int, pet: dict, battle_id: int, turn: int, action: str, now: int, rng=None) -> dict:
    """One button press in a fight. The turn number must be the one the page was shown, so a repeated or stale press
    changes nothing (battles.js act). A win pays once, by the fight's id. Changes the pet; the caller saves it."""
    rng = rng or random.SystemRandom()
    row = conn.execute("SELECT * FROM lumi_battles WHERE id = ? AND user_id = ?", (battle_id, user_id)).fetchone()
    if row is None:
        raise BattleError("That fight isn't Lumi's.")
    if row["status"] != "active":
        raise BattleError("That fight is over.")
    if row["turn"] != turn:
        raise BattleError("That turn already happened.")
    state = json.loads(row["state"])
    lumi = state["player"]
    if action.startswith("move"):
        index = action.removeprefix("move")
        if not index.isdigit() or int(index) >= len(lumi["moves"]):
            raise BattleError("Lumi doesn't have that move ready.")
        if lumi["moves"][int(index)].get("mp", 0) > lumi["mp"]:
            raise BattleError("Not enough MP for that. Defend to gather some.")
    elif action == "potion" or action in KIT:
        why = item_refusal(lumi, action)
        if why:
            raise BattleError(why)
        take_item(pet, action)
    elif action not in ("defend", "run"):
        raise BattleError("Lumi doesn't know that one.")
    remember(pet, lumi["moves"][int(action.removeprefix("move"))].get("id", "") if action.startswith("move") else action)
    resolve(state, action, rng)
    if state["outcome"] == "victory":
        _reward(conn, user_id, pet, state, battle_id)
    elif state["outcome"] == "defeat":
        pet["losses"] += 1
        pet["faint_until"] = now + FAINT_MS
        state["log"].append(f"{lumi['name']} curls up, worn out. She'll nap for ten minutes, or wake now for {HEAL_PRICE} coins.")
    conn.execute("UPDATE lumi_battles SET state = ?, status = ?, turn = turn + 1 WHERE id = ?",
                 (json.dumps(state, separators=(",", ":")), state["outcome"] or "active", battle_id))
    conn.execute("DELETE FROM lumi_battles WHERE user_id = ? AND status != 'active' AND id != ?", (user_id, battle_id))   # keep only the latest finished fight
    return {**state, "id": battle_id, "turn": turn + 1, "status": state["outcome"] or "active"}


def _reward(conn: sqlite3.Connection, user_id: int, pet: dict, state: dict, battle_id: int) -> None:
    """EXP and coins for a win, by the enemy's level and tier (battles.js reward); levels may follow."""
    enemy = state["enemy"]
    gained = 15 + enemy["level"] * 3 + TIERS[enemy["tier"]][3]
    prize = floor(gained * 0.6)
    if state["choice"] == "gamble":
        gained, prize = floor(gained * 1.2), floor(prize * 1.2)
    if state.get("boost") == "luck":
        gained, prize = floor(gained * 1.25), floor(prize * 1.25)
    prize = floor(prize * (1 + state["player"]["stats"]["coins"] / 100))
    before = pet["level"]
    level_up(pet, gained)
    pet["wins"] += 1
    # In the dungeon the coins go into her satchel (lumidungeon.after_fight), hers only if she gets home with it.
    paid = prize if state.get("dungeon") else coins.prize(conn, user_id, prize, GAME, f"lumi-battle:{battle_id}")
    state["reward"] = {"exp": gained, "coins": paid, "level": pet["level"], "levels": pet["level"] - before}
    state["log"].append(f"Victory! +{gained} EXP, +{paid} coins." + (f" {state['player'].get('name', 'Lumi')} is now level {pet['level']}!" if pet["level"] > before else ""))


# --------------------------------------------------------------------------- between fights
def progress(conn: sqlite3.Connection, user_id: int, pet: dict, action: str, item: str | None, slot: int | None, count: int, now: int) -> dict | None:
    """Skills, moves, mods, potions and the wake-up: everything that changes Lumi outside a fight. Returns a note
    about any purchase. Changes the pet; the caller saves it. Call inside a transaction."""
    if action == "learn":
        node = _NODES.get(item)
        if node is None:
            raise BattleError("That isn't a skill.")
        if item in pet["skills"]:
            raise BattleError("Lumi already knows that.")
        missing = [_NODES[need]["name"] for need in node["needs"] if need not in pet["skills"]]      # the skills that lead to it
        if missing:
            raise BattleError(f"{node['name']} needs {' and '.join(missing)} first.")
        if NODE_COSTS[node["index"]] > points(pet):
            raise BattleError("Not enough skill points yet. Each level brings one more.")
        pet["skills"].append(item)
        if node["kind"] == "move" and len(pet["moves"]) < MOVE_SLOTS:
            pet["moves"].append(item)                     # a new move is ready to use if there's room
    elif action == "respec":
        if not pet["skills"]:
            raise BattleError("There are no skill points to hand back.")
        if not coins.spend(conn, user_id, RESPEC_PRICE, GAME, "respec"):
            raise BattleError("Not enough coins for that.")
        pet["skills"], pet["moves"] = [], list(_BASICS)
        return {"bought": "respec", "count": 1, "paid": RESPEC_PRICE}
    elif action == "equip":
        if item not in {move["id"] for move in known_moves(pet)}:
            raise BattleError("Lumi hasn't learned that move.")
        if item in pet["moves"]:
            if len(pet["moves"]) == 1:
                raise BattleError("Lumi needs at least one move.")
            pet["moves"].remove(item)
        elif len(pet["moves"]) >= MOVE_SLOTS:
            raise BattleError(f"Lumi carries {MOVE_SLOTS} moves. Take one off first.")
        else:
            pet["moves"].append(item)
    elif action == "mod":
        if slot is None or not 0 <= slot < len(MOD_SLOTS):
            raise BattleError("Choose a mod slot.")
        tek, opens = MOD_SLOTS[slot]
        if pet["level"] < opens:
            raise BattleError(f"That slot opens at level {opens}.")
        if item is None:
            pet["mods"][slot] = None
            return None
        mod = _MODS.get(item)
        if mod is None or mod["tek"] != tek:
            raise BattleError(f"That slot takes a {tek} mod.")
        note = None
        if item not in pet["mods_owned"]:
            if not mod["price"]:
                raise BattleError("That mod can't be bought. It turns up in dungeon vaults.")
            if not coins.spend(conn, user_id, mod["price"], GAME, "mod"):
                raise BattleError("Not enough coins for that.")
            pet["mods_owned"].append(item)
            note = {"bought": item, "count": 1, "paid": mod["price"]}
        pet["mods"] = [None if fitted == item else fitted for fitted in pet["mods"]]   # one mod, one slot
        pet["mods"][slot] = item
        return note
    elif action == "potions":
        if not 1 <= count <= POTION_CAP or pet["potions"] + count > POTION_CAP:
            raise BattleError(f"Lumi carries up to {POTION_CAP} potions.")
        if not coins.spend(conn, user_id, POTION_PRICE * count, GAME, "potion"):
            raise BattleError("Not enough coins for that.")
        pet["potions"] += count
        return {"bought": "potion", "count": count, "paid": POTION_PRICE * count}
    elif action == "heal":
        if pet["faint_until"] <= now:
            raise BattleError("Lumi is already up and about.")
        if not coins.spend(conn, user_id, HEAL_PRICE, GAME, "heal"):
            raise BattleError("Not enough coins for that. She'll wake by herself soon.")
        pet["faint_until"], pet["wound"] = 0, 0.0         # woken and whole again
        return {"bought": "heal", "count": 1, "paid": HEAL_PRICE}
    elif action == "stat":                                # place points in one stat by hand
        if item not in STAT_KEYS:
            raise BattleError("Choose one of her stats.")
        left, held = stat_points_left(pet), pet["stat_points"].get(item, 0)
        if left < 1:
            raise BattleError("No stat points waiting. Each level brings " + str(POINTS_PER_LEVEL) + " more.")
        if held >= stat_cap(pet):
            raise BattleError(f"{STAT_NAMES[item]} holds as many points as her level allows ({stat_cap(pet)}).")
        pet["stat_points"][item] = held + max(1, min(count, left, stat_cap(pet) - held))
    elif action == "stats_auto":                          # place everything waiting the way Auto would
        if stat_points_left(pet) < 1:
            raise BattleError("No stat points waiting.")
        return {"placed": place_auto(pet)}
    elif action == "auto_stats":                          # Auto on or off for the levels to come
        pet["auto_stats"] = not pet.get("auto_stats")
        if pet["auto_stats"] and stat_points_left(pet) > 0:
            return {"placed": place_auto(pet)}
    elif action == "stats_reset":                         # take every placed point back, to place again
        if not any(pet["stat_points"].values()):
            raise BattleError("No points are placed yet.")
        if pet.get("stat_reset_free"):
            pet["stat_reset_free"] = 0
            pet["stat_points"] = dict.fromkeys(STAT_KEYS, 0)
            return {"bought": "stats_reset", "count": 1, "paid": 0}
        if not coins.spend(conn, user_id, STAT_RESET_PRICE, GAME, "stats_reset"):
            raise BattleError("Not enough coins for that.")
        pet["stat_points"] = dict.fromkeys(STAT_KEYS, 0)
        return {"bought": "stats_reset", "count": 1, "paid": STAT_RESET_PRICE}
    elif action == "kit":                                 # fight items: a mana potion, X-Attack, X-Defend, X-Speed
        if item not in KIT:
            raise BattleError("That isn't a fight item.")
        if not 1 <= count <= KIT_CAP or pet["kit"].get(item, 0) + count > KIT_CAP:
            raise BattleError(f"Lumi carries up to {KIT_CAP} of each.")
        if not coins.spend(conn, user_id, KIT[item]["price"] * count, GAME, item):
            raise BattleError("Not enough coins for that.")
        pet["kit"][item] = pet["kit"].get(item, 0) + count
        return {"bought": item, "count": count, "paid": KIT[item]["price"] * count}
    elif action == "drink":                               # a potion at home, between trips (lumipet.act refuses it on one)
        if pet["wound"] <= 0:
            raise BattleError("Lumi is already at full health.")
        if pet["potions"] < 1:
            raise BattleError("No potions left. Lumi Dungeons' Battle tab sells them.")
        pet["potions"] -= 1
        mend(pet, POTION_MEND)
    else:
        raise BattleError("Lumi doesn't know that one.")
    return None


def summary(pet: dict) -> dict:
    """What the page needs about her progress, worked out here so the page never does the sums."""
    weights = auto_weights(pet)
    return {"points": points(pet), "stats": stats(pet), "next": level_threshold(pet["level"]) if pet["level"] < MAX_LEVEL else 0,
            "stat_points": {"left": stat_points_left(pet), "placed": {key: pet["stat_points"].get(key, 0) for key in STAT_KEYS},
                            "cap": stat_cap(pet), "auto": bool(pet.get("auto_stats")), "free_reset": bool(pet.get("stat_reset_free")),
                            "per_point": {key: GROWTH[key] / 2 for key in STAT_KEYS},
                            "leans": sorted(STAT_KEYS, key=lambda key: -weights[key])[:3]},   # what Auto favours most, to say so
            "kit": {key: pet["kit"].get(key, 0) for key in KIT},
            "element": GLOW_ELEMENT.get(pet["glow"], "fire"),
            "slots": [{"tek": tek, "opens": opens, "open": pet["level"] >= opens, "mod": pet["mods"][i]} for i, (tek, opens) in enumerate(MOD_SLOTS)]}


RULES = {"mend_ms": MEND_MS, "nap_mend": NAP_MEND, "costs": NODE_COSTS, "respec": RESPEC_PRICE, "potion": POTION_PRICE, "potion_cap": POTION_CAP, "heal": HEAL_PRICE,
         "kit": KIT, "kit_cap": KIT_CAP, "stat_keys": STAT_KEYS, "stat_names": STAT_NAMES, "stat_reset": STAT_RESET_PRICE, "points_per_level": POINTS_PER_LEVEL,
         "moves": MOVE_SLOTS, "max_level": MAX_LEVEL, "tiers": list(TIERS), "glows": GLOW_ELEMENT}
