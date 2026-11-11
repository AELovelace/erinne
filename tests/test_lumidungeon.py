"""Lumi Pet's dungeon (mnemerynne/lumidungeon.py): floors, movement, the satchel and its stakes, locks, games and
the page never being told what it hasn't seen."""

import json
import random
import sys
from pathlib import Path

import pytest

from mnemerynne import coins, lumibattle, lumidungeon as D, lumipet, users
from mnemerynne.db import transaction
from test_media import (_login, _no_network, _pair, _screens_for, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import make_lumi_sokoban  # noqa: E402

T0 = 1_800_000_000_000
ENVS = [env["id"] for env in D.CATALOG["environments"]]


class Dice:
    """A scripted dice: random() gives `chance`; randrange(n) gives the listed numbers in turn, then `pick`."""

    def __init__(self, *rolls, pick=0, chance=0.5):
        self.rolls, self.pick, self.chance = list(rolls), pick, chance

    def random(self):
        return self.chance

    def randrange(self, n):
        return min(self.rolls.pop(0) if self.rolls else self.pick, n - 1)


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


def _pet(**fields):
    even = {"stat_points": {key: fields["level"] - 1 for key in lumibattle.STAT_KEYS}} if "level" in fields else {}   # spread evenly
    return {"glow": "rose", "boost": None, "decor": [], **lumibattle.fresh_fields(), **even, **fields}


def _start(conn, uid, env="moonlit-shrine", seed=1):
    with transaction(conn):
        return lumipet.dungeon_start(conn, uid, env, T0, random.Random(seed))["dungeon"]["run"]


def _do(conn, uid, action, rng=None, **kwargs):
    with transaction(conn):
        return lumipet.dungeon_act(conn, uid, action, now=T0, rng=rng or random.Random(5), **kwargs)


def _run(conn, uid):
    return D.current(conn, uid)


def _put(conn, uid, run):
    D._save(conn, uid, run)


def _set_pet(conn, uid, **fields):
    pet = json.loads(conn.execute("SELECT data FROM lumi_pets WHERE user_id = ?", (uid,)).fetchone()[0])
    if "level" in fields and "stat_points" not in fields:            # a level set by hand comes with its points spread evenly
        fields = {**fields, "stat_points": {key: fields["level"] - 1 for key in lumibattle.STAT_KEYS}}
    pet.update(fields)
    conn.execute("UPDATE lumi_pets SET data = ? WHERE user_id = ?", (json.dumps(pet), uid))


def _path(run, goal_kind):
    """The ways to walk from where Lumi is to the nearest room of a kind, avoiding enemies and the vault."""
    seen, queue = {run["at"]: []}, [run["at"]]
    for at in queue:
        if run["rooms"][at]["kind"] == goal_kind and at != run["at"]:
            return seen[at]
        for way in run["rooms"][at]["exits"]:
            to = D._step(at, way)
            if to not in seen and (run["rooms"][to]["kind"] not in ("enemy", "vault") or run["rooms"][to]["kind"] == goal_kind):
                seen[to] = seen[at] + [way]
                queue.append(to)
    return None


def _clear(run, keep=()):
    """Turn every enemy, treasure and key room into an empty one (except kinds to `keep`), for tests that are about
    something else and shouldn't meet a fight or pick up coins on the way."""
    for room in run["rooms"].values():
        if room["kind"] in ("enemy", "treasure", "key") and room["kind"] not in keep:
            room.update(kind="empty", done=True)
    return run


# --------------------------------------------------------------------------- catalogs
def test_twelve_environments_from_mystical_to_cyberpunk_each_with_trophies():
    families = [env["family"] for env in D.CATALOG["environments"]]
    assert len(ENVS) == 12 and [families.count(f) for f in ("mystical", "fantasy", "cyberpunk")] == [4, 4, 4]
    assert [env["opens"] for env in D.CATALOG["environments"]] == sorted(env["opens"] for env in D.CATALOG["environments"])
    assert set(D.TROPHIES) == set(ENVS) and all(len(found) == 2 for found in D.TROPHIES.values())
    assert len(D._FOUND_MODS) == 4 and {mod["tek"] for mod in D._FOUND_MODS} == {"magitek", "cybertek"}


def test_bad_dungeon_catalogs_are_refused(tmp_path):
    def broken(change):
        data = json.loads(json.dumps(D.CATALOG))
        change(data)
        path = tmp_path / "dungeon.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            D.load_catalog(path)

    broken(lambda d: d["environments"][0].update(family="steampunk"))
    broken(lambda d: d["environments"][0].update(elements=["plasma"]))
    broken(lambda d: d["environments"][0]["palette"].update(wall="purple"))
    broken(lambda d: d["environments"].append(dict(d["environments"][0])))
    broken(lambda d: [env.update(opens=5) for env in d["environments"]])        # nowhere to go at level 1
    broken(lambda d: d.update(environments=[]))


def test_every_shipped_sokoban_level_can_be_solved_and_they_get_harder():
    best = []
    for level in D.SOKOBAN:
        answer = make_lumi_sokoban.solve(level["rows"])
        assert answer is not None, level["id"]
        assert D.sokoban_solved(level["rows"], answer) and len(answer) == level["best"]
        best.append(len(answer))
    assert len(D.SOKOBAN) == 16 and best == sorted(best) and best[0] >= 8


def test_sokoban_answers_are_replayed_strictly():
    rows = ["#####", "#@$.#", "#####"]
    assert D.sokoban_solved(rows, "r")
    for wrong in ("", "l", "rr", "u", "rx", "r" * 3):
        assert not D.sokoban_solved(rows, wrong), wrong                         # unfinished, into a wall, a box into a wall, junk
    two = ["#######", "#@$$..#", "#######"]
    assert not D.sokoban_solved(two, "r")                                       # a box can't push another box
    for bad in (["#@#"], ["#@@$.#"], ["#@$#"], ["#@$.x#"]):
        with pytest.raises(ValueError):
            D.sokoban_parse(bad)


def test_lights_out_presses_flip_neighbours_and_only_all_dark_opens():
    board = [False] * 9
    D.lights_press(board, 3, 4)
    assert board == [False, True, False, True, True, True, False, True, False]
    assert D.lights_solved(board, 3, [4]) and not D.lights_solved(board, 3, [0]) and not D.lights_solved(board, 3, [9])


# --------------------------------------------------------------------------- floors
def test_every_floor_is_whole_has_what_it_promises_and_never_locks_anything_but_the_vault():
    for seed in range(400):
        rng = random.Random(seed)
        floor = D.generate(ENVS[seed % 12], 1 + seed % 6, rng, _pet())
        rooms, start = floor["rooms"], floor["start"]
        kinds = [room["kind"] for room in rooms.values()]
        assert D.ROOMS[0] <= len(rooms) <= D.ROOMS[1]
        assert [kinds.count(k) for k in ("start", "stairs", "vault", "game")] == [1, 1, 1, 1], seed
        assert 3 <= kinds.count("enemy") <= 4 and kinds.count("treasure") == 2
        vault = next(key for key, room in rooms.items() if room["kind"] == "vault")
        assert len(rooms[vault]["exits"]) == 1, "the vault is a dead end"
        assert kinds.count("key") == (1 if rooms[vault]["lock"]["kind"] == "key" else 0)
        for key, room in rooms.items():                                         # every doorway has a room behind it, and leads back
            for way in room["exits"]:
                assert D.BACK[way] in rooms[D._step(key, way)]["exits"], seed
        reached, queue = {start}, [start]                                       # walk the floor without ever entering the vault
        for at in queue:
            for way in rooms[at]["exits"]:
                to = D._step(at, way)
                if to not in reached and to != vault:
                    reached.add(to)
                    queue.append(to)
        assert reached == set(rooms) - {vault}, "the key, the stairs and the way home never need the vault"
        assert rooms[start]["seen"] and sum(room["seen"] for room in rooms.values()) == 1


def test_lights_locks_are_always_solvable_and_rune_codes_use_the_runes():
    seen = set()
    for seed in range(300):
        lock = D._lock(1 + seed % 5, random.Random(seed))
        seen.add(lock["kind"])
        if lock["kind"] == "lights":
            size = lock["size"]
            assert any(lock["board"]) and len(lock["board"]) == size * size
            assert any(D.lights_solved(lock["board"], size, [c for c in range(size * size) if mask >> c & 1]) for mask in range(1 << size * size))
        elif lock["kind"] == "runes":
            assert len(lock["code"]) == D.RUNE_SLOTS and set(lock["code"]) <= set(D.RUNES)
        elif lock["kind"] == "sokoban":
            assert any(level["id"] == lock["level"] for level in D.SOKOBAN)
    assert seen == set(D.LOCKS)


def test_deeper_floors_bring_tougher_enemies_and_more_coins():
    def tiers(depth):
        found = [lumibattle._ENEMIES[D._enemy(D._ENVS["crystal-cavern"], depth, random.Random(seed))["id"]]["tier"] for seed in range(300)]
        return {tier: found.count(tier) for tier in lumibattle.TIERS}

    shallow, deep = tiers(1), tiers(5)
    assert shallow["epic"] == shallow["legendary"] == 0 and shallow["common"] > shallow["uncommon"] > 0
    assert deep["common"] == deep["uncommon"] == 0 and deep["legendary"] > 0
    assert sum(D._coins(5, random.Random(s)) for s in range(50)) > 2.5 * sum(D._coins(1, random.Random(s)) for s in range(50))
    local = [lumibattle._ENEMIES[D._enemy(D._ENVS["crystal-cavern"], 1, random.Random(seed))["id"]]["element"] for seed in range(300)]
    assert sum(element in ("ice", "none") for element in local) > 200          # mostly this place's own creatures


def test_a_vault_offers_a_trophy_she_doesnt_have_yet(conn):
    both = [t["id"] for t in D.TROPHIES["toy-attic"]]
    loot = D._vault_loot(D._ENVS["toy-attic"], 1, random.Random(1), _pet(decor=[both[0]]), {"trophies": [], "mods": []})
    assert loot["trophy"] == both[1] and loot["coins"] > 0
    none = D._vault_loot(D._ENVS["toy-attic"], 1, random.Random(1), _pet(decor=both), {"trophies": [], "mods": []})
    held = D._vault_loot(D._ENVS["toy-attic"], 1, random.Random(1), _pet(decor=[both[0]]), {"trophies": [both[1]], "mods": []})
    assert "trophy" not in none and "trophy" not in held


def test_every_place_has_a_decor_set_with_a_piece_for_every_spot():
    spots = sorted(spot["id"] for spot in lumipet.HOUSE["spots"] if spot["id"] != "trophy")
    assert set(D.SETS) == set(ENVS)
    for env, pieces in D.SETS.items():
        assert sorted(piece["spot"] for piece in pieces) == spots, env
        assert all(piece["price"] == 0 and piece["found"] == env and "rarity" not in piece for piece in pieces)
    assert len(D.FOUND_NAMES) == 12 * (len(spots) + 2)                          # every set piece and trophy has a name to tell


def test_set_pieces_are_rare_likelier_deeper_and_never_found_twice():
    env = D._ENVS["moonlit-shrine"]

    def rate(where, depth):                                                     # how often a chest or a creature holds one
        return sum(D._set_piece(env, where, depth, random.Random(seed), set()) is not None for seed in range(3000)) / 3000
    assert 0.05 < rate("treasure", 1) < 0.11 and 0.02 < rate("enemy", 1) < 0.06
    assert rate("treasure", 4) > rate("treasure", 1) + 0.05 and rate("enemy", 4) > rate("enemy", 1) + 0.03
    ids, owned = {piece["id"] for piece in D.SETS["moonlit-shrine"]}, set()
    found = [D._set_piece(env, "treasure", 1, Dice(), owned) for _ in range(len(ids) + 1)]   # a dice that always hits
    assert set(found[:-1]) == ids == owned and found[-1] is None                # each piece once, then none left


def test_a_floor_hides_set_pieces_in_chests_and_creatures_but_never_one_she_has():
    pieces = [piece["id"] for piece in D.SETS["candy-catacombs"]]
    kinds = set()
    for seed in range(300):
        floor = D.generate("candy-catacombs", 3, random.Random(seed), _pet(decor=pieces[:10]), {"trophies": pieces[10:12], "mods": []})
        hidden = [(room["kind"], room["loot"]["trophy"]) for room in floor["rooms"].values() if room["kind"] != "vault" and room.get("loot", {}).get("trophy")]
        assert all(kind in ("treasure", "enemy") and piece in pieces[12:] for kind, piece in hidden)
        assert len({piece for _, piece in hidden}) == len(hidden)                # never the same piece twice on a floor
        kinds |= {kind for kind, _ in hidden}
    assert kinds == {"treasure", "enemy"}


# --------------------------------------------------------------------------- a trip
def test_setting_out_needs_the_level_and_one_trip_at_a_time(conn, alice):
    with transaction(conn), pytest.raises(lumipet.PetError, match="opens at level 35"):
        lumipet.dungeon_start(conn, alice, "rooftop-garden", T0)
    with transaction(conn), pytest.raises(lumipet.PetError, match="isn't somewhere"):
        lumipet.dungeon_start(conn, alice, "the-moon", T0)
    run = _start(conn, alice)
    assert run["depth"] == 1 and run["room"]["kind"] == "start" and run["hp"] == run["hpMax"] and run["say"]
    assert run["satchel"] == {"coins": 0, "potions": 0, "keys": 0, "trophies": [], "mods": []}
    with transaction(conn), pytest.raises(lumipet.PetError, match="already out exploring"):
        lumipet.dungeon_start(conn, alice, "crystal-cavern", T0)
    with transaction(conn), pytest.raises(lumipet.PetError, match="out exploring"):
        lumipet.fight_start(conn, alice, "common", T0)                          # no side fights while she's away
    with transaction(conn):
        assert lumipet.state(conn, alice, T0 + 40 * 24 * lumipet.HOUR)["dungeon"]["run"]["id"] == run["id"]     # weeks later, still waiting


def test_the_page_only_ever_learns_rooms_she_has_stood_in(conn, alice):
    shown = _start(conn, alice)
    full = _run(conn, alice)
    assert len(full["rooms"]) >= 11
    known = {key: room for key, room in shown["rooms"].items() if room["kind"] != "unknown"}
    assert list(known) == [full["at"]]
    assert {key for key, room in shown["rooms"].items() if room["kind"] == "unknown"} == {D._step(full["at"], way) for way in full["rooms"][full["at"]]["exits"]}
    text = json.dumps(shown)
    for secret in ("enemy", "loot", "code", "prize", "deck", "level"):
        assert f'"{secret}"' not in text.replace('"scenario": "enemy"', ""), secret
    _put(conn, alice, _clear(full))
    way = full["rooms"][full["at"]]["exits"][0]
    after = _do(conn, alice, "move", way=way)["dungeon"]["run"]
    assert sum(room["kind"] != "unknown" for room in after["rooms"].values()) == 2 and after["at"] == D._step(full["at"], way)


def test_walls_are_solid_and_treasure_goes_in_the_satchel_once(conn, alice):
    _start(conn, alice, seed=3)
    run = _clear(_run(conn, alice), keep=("treasure",))
    for key, room in run["rooms"].items():                                      # keep one treasure room, so the sums are simple
        if room["kind"] == "treasure" and key != next(k for k, r in run["rooms"].items() if r["kind"] == "treasure"):
            room.update(kind="empty", done=True)
    _put(conn, alice, run)
    closed = next((way for way in D.DIRS if way not in run["rooms"][run["at"]]["exits"]), None)
    if closed:
        with pytest.raises(lumipet.PetError, match="no way through"):
            _do(conn, alice, "move", way=closed)
    path = _path(run, "treasure")
    for way in path:
        seen = _do(conn, alice, "move", way=way)["dungeon"]["run"]
    loot = run["rooms"][seen["at"]]["loot"]
    assert seen["satchel"]["coins"] == 0 and seen["event"]["scenario"] == "see_treasure" and not seen["room"]["done"]     # it waits to be opened
    assert seen["say"] == "There is a chest here, shut tight and waiting." and "loot" not in json.dumps(seen)
    out = _do(conn, alice, "take")["dungeon"]["run"]
    assert out["satchel"]["coins"] == loot["coins"] > 0 and out["event"]["scenario"] == "treasure" and str(loot["coins"]) in out["say"] and out["room"]["done"]
    with pytest.raises(lumipet.PetError, match="nothing here to pick up"):
        _do(conn, alice, "take")
    back = _do(conn, alice, "move", way=D.BACK[path[-1]])["dungeon"]["run"]
    again = _do(conn, alice, "move", way=path[-1])["dungeon"]["run"]
    assert again["satchel"]["coins"] == back["satchel"]["coins"] == loot["coins"] and again["event"]["scenario"] == "again"
    assert coins.peek(conn, alice) == 1000                                      # nothing is hers until she's home


def test_walking_home_banks_the_satchel_once(conn, alice):
    _start(conn, alice)
    run = _clear(_run(conn, alice))
    trophy, mod = D.TROPHIES[run["env"]][0]["id"], D._FOUND_MODS[0]["id"]
    run["satchel"] = {"coins": 120, "potions": 3, "keys": 1, "trophies": [trophy], "mods": [mod]}
    _put(conn, alice, run)
    _set_pet(conn, alice, potions=9)
    way = run["rooms"][run["at"]]["exits"][0]
    _do(conn, alice, "move", way=way)
    with pytest.raises(lumipet.PetError, match="back where Lumi came in"):
        _do(conn, alice, "leave")
    _do(conn, alice, "move", way=D.BACK[way])
    out = _do(conn, alice, "leave")
    ended = out["dungeon"]["run"]
    assert ended["over"] == "home" and ended["ended"] == {"coins": 120, "potions": 1, "trophies": [trophy], "mods": [mod], "depth": 1, "at": T0}   # dated, for Lumi Pet's welcome home
    assert out["coins"] == 1120 and out["pet"]["potions"] == 10 and trophy in out["pet"]["decor"] and mod in out["pet"]["mods_owned"]
    assert conn.execute("SELECT ref FROM coin_transactions WHERE amount = 120").fetchone()[0] == f"lumi-run:{alice}:{ended['id']}"
    with pytest.raises(lumipet.PetError, match="isn't out exploring"):
        _do(conn, alice, "leave")
    placed = None
    with transaction(conn):
        placed = lumipet.act(conn, alice, "decorate", item=trophy, now=T0)      # a found trophy goes on the bracket for nothing
    assert placed["pet"]["house"]["trophy"] == trophy and placed["coins"] == 1120
    assert _start(conn, alice, "crystal-cavern")["depth"] == 1                  # a finished trip makes way for the next


def test_a_key_lies_where_it_is_until_she_picks_it_up_and_can_be_left_for_later(conn, alice):
    _start(conn, alice)
    run = _clear(_run(conn, alice))
    spot = next(key for key, room in run["rooms"].items() if room["kind"] == "empty" and key != run["at"])
    run["rooms"][spot].update(kind="key", done=False, loot={"keys": 1})
    _put(conn, alice, run)
    path = _path(run, "key")
    for way in path:
        seen = _do(conn, alice, "move", way=way)["dungeon"]["run"]
    assert seen["event"]["scenario"] == "see_key" and seen["satchel"]["keys"] == 0
    away = _do(conn, alice, "move", way=D.BACK[path[-1]])["dungeon"]["run"]     # walk off without it
    assert away["satchel"]["keys"] == 0
    back = _do(conn, alice, "move", way=path[-1])["dungeon"]["run"]
    assert back["event"]["scenario"] == "see_key", "still lying there"
    taken = _do(conn, alice, "take")["dungeon"]["run"]
    assert taken["satchel"]["keys"] == 1 and taken["event"]["scenario"] == "key" and taken["room"]["done"]
    with pytest.raises(lumipet.PetError, match="nothing here to pick up"):
        _do(conn, alice, "take")


def test_nothing_can_be_taken_from_a_room_with_nothing_in_it(conn, alice):
    _start(conn, alice)
    with pytest.raises(lumipet.PetError, match="nothing here to pick up"):
        _do(conn, alice, "take")


def test_trophies_and_dungeon_mods_cant_be_bought(conn, alice):
    with transaction(conn), pytest.raises(lumipet.PetError, match="waiting somewhere in the dungeon"):
        lumipet.act(conn, alice, "decorate", item="trophy-moon-bell", now=T0)
    _set_pet(conn, alice, level=20)
    with transaction(conn), pytest.raises(lumipet.PetError, match="dungeon vaults"):
        lumipet.act(conn, alice, "mod", item="glitch-fang", slot=0, now=T0)
    assert coins.peek(conn, alice) == 1000


def test_giving_up_leaves_everything_behind(conn, alice):
    _start(conn, alice)
    run = _run(conn, alice)
    run["satchel"]["coins"] = 80
    _put(conn, alice, run)
    ended = _do(conn, alice, "abandon")["dungeon"]["run"]
    assert ended["over"] == "abandoned" and ended["ended"]["lost"]["coins"] == 80 and ended["satchel"]["coins"] == 0
    assert ended["ended"]["at"] == T0                                           # dated, for Lumi Pet's welcome home
    assert coins.peek(conn, alice) == 1000


def test_stairs_go_down_keep_the_satchel_and_drop_the_keys(conn, alice):
    _start(conn, alice)
    run = _clear(_run(conn, alice))
    run["satchel"].update(coins=55, keys=1)
    _put(conn, alice, run)
    with pytest.raises(lumipet.PetError, match="no stairs here"):
        _do(conn, alice, "descend")
    for way in _path(run, "stairs"):
        out = _do(conn, alice, "move", way=way)["dungeon"]["run"]
    assert out["room"]["kind"] == "stairs" and out["event"]["scenario"] == "stairs"
    down = _do(conn, alice, "descend")["dungeon"]["run"]
    assert down["depth"] == 2 and down["room"]["kind"] == "start" and down["satchel"]["coins"] == 55 and down["satchel"]["keys"] == 0
    assert sum(room["kind"] != "unknown" for room in down["rooms"].values()) == 1


# --------------------------------------------------------------------------- fights in the dungeon
def _walk_into_enemy(conn, uid):
    run = _run(conn, uid)
    seen, queue = {run["at"]: []}, [run["at"]]
    for at in queue:
        if run["rooms"][at]["kind"] == "enemy":
            for way in seen[at]:
                out = _do(conn, uid, "move", way=way, rng=Dice(pick=1))
            return out
        for way in run["rooms"][at]["exits"]:
            to = D._step(at, way)
            if to not in seen and run["rooms"][to]["kind"] != "vault" and (run["rooms"][at]["kind"] != "enemy"):
                seen[to] = seen[at] + [way]
                queue.append(to)
    raise AssertionError("no enemy to walk into")


def _only_one_enemy(conn, uid, hp=None):
    """Keep the nearest enemy and empty the others, so a walk meets exactly one."""
    run = _run(conn, uid)
    first = True
    seen, queue = {run["at"]}, [run["at"]]
    for at in queue:
        room = run["rooms"][at]
        if room["kind"] == "enemy":
            if not first:
                room.update(kind="empty", done=True)
            first = False
        elif room["kind"] in ("treasure", "key"):
            room.update(kind="empty", done=True)               # nothing to pick up on the way
        for way in room["exits"]:
            to = D._step(at, way)
            if to not in seen and run["rooms"][to]["kind"] != "vault":
                seen.add(to)
                queue.append(to)
    if hp is not None:
        run["hp"] = hp
    _put(conn, uid, run)


def test_a_dungeon_fight_carries_her_health_in_and_out_and_fills_the_satchel(conn, alice):
    _set = lambda **f: _set_pet(conn, alice, **f)                               # noqa: E731
    _start(conn, alice)
    fighter = [node["id"] for node in lumibattle.CATALOG["trees"][0]["nodes"]]
    _set(level=40, skills=fighter, moves=["comet-cleave"])                      # a trained fighter: this one she wins
    _only_one_enemy(conn, alice, hp=300)
    out = _walk_into_enemy(conn, alice)
    fight, run = out["fight"], out["dungeon"]["run"]
    assert fight["status"] == "active" and fight["player"]["hp"] == 300 < fight["player"]["hpMax"] and fight["dungeon"] is True and run["fighting"]
    assert run["event"]["scenario"] == "enemy" and fight["enemy"]["name"] in run["say"]
    with pytest.raises(lumipet.PetError, match="middle of a fight"):
        _do(conn, alice, "abandon")
    while fight["status"] == "active":
        with transaction(conn):
            fight = lumipet.fight_act(conn, alice, fight["id"], fight["turn"], "move0", T0, Dice(chance=0.01))["fight"]
    assert fight["status"] == "victory"
    with transaction(conn):
        state = lumipet.state(conn, alice, T0)
    run = state["dungeon"]["run"]
    assert run["satchel"]["coins"] == fight["reward"]["coins"] > 0 and state["coins"] == 1000          # in the satchel, not the purse
    assert run["hp"] == fight["player"]["hp"] <= 300 and not run["fighting"] and run["room"]["done"] and run["event"]["scenario"] == "victory"
    assert state["pet"]["exp"] > 0 or state["pet"]["level"] > 40                # EXP is hers at once


def test_a_beaten_creature_can_drop_a_piece_of_the_places_set(conn, alice):
    _start(conn, alice)
    _set_pet(conn, alice, level=40, skills=[node["id"] for node in lumibattle.CATALOG["trees"][0]["nodes"]], moves=["comet-cleave"])
    _only_one_enemy(conn, alice, hp=300)
    piece = D.SETS["moonlit-shrine"][0]
    run = _run(conn, alice)
    for room in run["rooms"].values():
        if room["kind"] == "enemy":
            room["loot"] = {"trophy": piece["id"]}                              # this creature is carrying one
    _put(conn, alice, run)
    fight = _walk_into_enemy(conn, alice)["fight"]
    while fight["status"] == "active":
        with transaction(conn):
            fight = lumipet.fight_act(conn, alice, fight["id"], fight["turn"], "move0", T0, Dice(chance=0.01))["fight"]
    with transaction(conn):
        run = lumipet.state(conn, alice, T0)["dungeon"]["run"]
    assert fight["status"] == "victory" and run["satchel"]["trophies"] == [piece["id"]] and run["event"]["dropped"] == piece["id"]
    assert piece["name"] in run["say"] and "loot" not in _run(conn, alice)["rooms"][run["at"]]
    with transaction(conn), pytest.raises(lumipet.PetError, match="waiting somewhere in the dungeon"):
        lumipet.act(conn, alice, "decorate", item=piece["id"], now=T0)         # not hers until she walks out with it


def test_being_beaten_ends_the_trip_and_empties_the_satchel_but_keeps_her_exp(conn, alice):
    _start(conn, alice)
    _only_one_enemy(conn, alice, hp=1)
    run = _run(conn, alice)
    run["satchel"].update(coins=200, trophies=["trophy-moon-bell"])
    _put(conn, alice, run)
    _set_pet(conn, alice, exp=7)
    fight = _walk_into_enemy(conn, alice)["fight"]
    while fight["status"] == "active":
        with transaction(conn):
            fight = lumipet.fight_act(conn, alice, fight["id"], fight["turn"], "defend", T0, Dice(chance=0.01))["fight"]
    with transaction(conn):
        state = lumipet.state(conn, alice, T0)
    run = state["dungeon"]["run"]
    assert fight["status"] == "defeat" and run["over"] == "defeat" and run["ended"]["lost"]["coins"] == 200
    assert run["ended"]["at"] == T0                                             # dated, for Lumi Pet's welcome home
    assert run["satchel"] == {"coins": 0, "potions": 0, "keys": 0, "trophies": [], "mods": []}
    assert state["coins"] == 1000 and "trophy-moon-bell" not in state["pet"]["decor"] and state["pet"]["exp"] == 7
    assert state["pet"]["faint_until"] == T0 + lumibattle.FAINT_MS
    with transaction(conn), pytest.raises(lumipet.PetError, match="still napping"):
        lumipet.dungeon_start(conn, alice, "moonlit-shrine", T0 + 1000)


def test_running_from_a_fight_steps_back_and_the_enemy_stays(conn, alice):
    _start(conn, alice)
    _only_one_enemy(conn, alice)
    out = _walk_into_enemy(conn, alice)
    lair = out["dungeon"]["run"]["at"]
    with transaction(conn):
        fight = lumipet.fight_act(conn, alice, out["fight"]["id"], 0, "run", T0, Dice(chance=0.1))["fight"]
    run = _run(conn, alice)
    assert fight["status"] == "ran" and run["at"] != lair and run["prev"] == lair and not run["rooms"][lair]["done"]
    assert run["event"]["scenario"] == "fled" and run["over"] is None


def test_a_potion_between_fights_uses_her_own_and_only_when_it_helps(conn, alice):
    _start(conn, alice)
    with pytest.raises(lumipet.PetError, match="full health"):
        _do(conn, alice, "potion")
    run = _run(conn, alice)
    run["hp"] = 5
    _put(conn, alice, run)
    with pytest.raises(lumipet.PetError, match="no potions"):
        _do(conn, alice, "potion")
    _set_pet(conn, alice, potions=2)
    out = _do(conn, alice, "potion")
    assert out["dungeon"]["run"]["hp"] == 5 + out["dungeon"]["run"]["hpMax"] // 2 and out["pet"]["potions"] == 1


def test_a_potion_found_on_the_trip_counts_at_once_and_is_drunk_before_her_own(conn, alice):
    _start(conn, alice)
    _set_pet(conn, alice, potions=1)
    run = _run(conn, alice)
    run["hp"] = 5
    run["satchel"]["potions"] = 1
    _put(conn, alice, run)
    with transaction(conn):
        assert lumipet.state(conn, alice, T0)["potions_ready"] == 2            # one of hers, one found
    first = _do(conn, alice, "potion")
    assert first["dungeon"]["run"]["satchel"]["potions"] == 0 and first["pet"]["potions"] == 1 and first["potions_ready"] == 1
    run = _run(conn, alice)
    run["hp"] = 5
    _put(conn, alice, run)
    second = _do(conn, alice, "potion")
    assert second["pet"]["potions"] == 0 and second["potions_ready"] == 0
    conn.execute("DELETE FROM lumi_runs")
    with transaction(conn):
        assert lumipet.state(conn, alice, T0)["potions_ready"] == 0


def test_a_found_potion_can_be_drunk_in_a_dungeon_fight(conn, alice):
    _start(conn, alice)
    _only_one_enemy(conn, alice, hp=20)
    run = _run(conn, alice)
    run["satchel"]["potions"] = 1
    _put(conn, alice, run)
    fight = _walk_into_enemy(conn, alice)["fight"]
    with transaction(conn):
        out = lumipet.fight_act(conn, alice, fight["id"], fight["turn"], "potion", T0, Dice(chance=0.999))     # the enemy's reply misses
    assert out["fight"]["player"]["hp"] == 20 + out["fight"]["player"]["hpMax"] // 2
    assert out["pet"]["potions"] == 0 and out["dungeon"]["run"]["satchel"]["potions"] == 0 and out["potions_ready"] == 0
    with transaction(conn), pytest.raises(lumipet.PetError, match="No potions left"):
        lumipet.fight_act(conn, alice, fight["id"], out["fight"]["turn"], "potion", T0, Dice(chance=0.999))
    assert _run(conn, alice)["satchel"]["potions"] == 0 and _run(conn, alice)["fight"] == fight["id"]          # a refusal changed nothing


# --------------------------------------------------------------------------- locks
def _face_vault(conn, uid, lock):
    """Clear the floor, fit the vault with a given lock, and walk Lumi up to its door."""
    run = _clear(_run(conn, uid))
    vault = next(key for key, room in run["rooms"].items() if room["kind"] == "vault")
    run["rooms"][vault]["lock"] = lock
    run["rooms"][vault]["loot"] = {"coins": 90, "potions": 1, "trophy": D.TROPHIES[run["env"]][0]["id"]}
    _put(conn, uid, run)
    out = None
    for way in _path(run, "vault"):
        out = _do(conn, uid, "move", way=way)["dungeon"]["run"]
    assert out["at"] != vault and out["facing"]["at"] == vault, "she stops at the door"
    return out, vault


def test_a_key_door_wants_a_key_from_this_floor(conn, alice):
    _start(conn, alice)
    shown, vault = _face_vault(conn, alice, {"kind": "key"})
    assert shown["facing"]["lock"] == {"kind": "key"} and shown["event"]["scenario"] == "locked_key"
    with pytest.raises(lumipet.PetError, match="needs a key"):
        _do(conn, alice, "unlock")
    assert _do(conn, alice, "back")["dungeon"]["run"]["facing"] is None
    run = _run(conn, alice)
    run["satchel"]["keys"] = 1
    run["facing"] = vault
    _put(conn, alice, run)
    inside = _do(conn, alice, "unlock")["dungeon"]["run"]
    assert inside["at"] == vault and inside["satchel"]["keys"] == 0 and inside["satchel"]["coins"] == 0 and inside["event"]["scenario"] == "see_vault"
    opened = _do(conn, alice, "take")["dungeon"]["run"]
    assert opened["satchel"]["coins"] == 90
    assert opened["satchel"]["trophies"] == [D.TROPHIES[opened["env"]["id"]][0]["id"]] and opened["event"]["scenario"] == "vault"
    assert "Moon bell" in opened["say"] or "Lantern charm" in opened["say"]
    with pytest.raises(lumipet.PetError, match="no locked door"):
        _do(conn, alice, "unlock")


def test_a_sokoban_door_shows_its_level_and_opens_only_to_a_real_answer(conn, alice):
    _start(conn, alice)
    level = D.SOKOBAN[3]
    shown, vault = _face_vault(conn, alice, {"kind": "sokoban", "level": level["id"]})
    assert shown["facing"]["lock"] == {"kind": "sokoban", "rows": level["rows"]}
    answer = make_lumi_sokoban.solve(level["rows"])
    for wrong in ("", answer[:-1], answer + "u" * 700):
        with pytest.raises(lumipet.PetError, match="crates aren't all in place"):
            _do(conn, alice, "sokoban", moves=wrong)
    with pytest.raises(lumipet.PetError, match="isn't how this door opens"):
        _do(conn, alice, "lights", picks=[0])
    assert _do(conn, alice, "sokoban", moves=answer)["dungeon"]["run"]["at"] == vault


def test_a_lights_door_opens_when_every_lamp_is_out(conn, alice):
    _start(conn, alice)
    board = [False] * 9
    for cell in (0, 4):
        D.lights_press(board, 3, cell)
    shown, vault = _face_vault(conn, alice, {"kind": "lights", "size": 3, "board": board})
    assert shown["facing"]["lock"] == {"kind": "lights", "size": 3, "board": board}
    with pytest.raises(lumipet.PetError, match="still lit"):
        _do(conn, alice, "lights", picks=[0])
    assert _do(conn, alice, "lights", picks=[4, 0])["dungeon"]["run"]["at"] == vault


def test_a_rune_door_answers_each_guess_never_shows_its_code_and_resets_when_tries_run_out(conn, alice):
    _start(conn, alice)
    code = ["sun", "moon", "star", "sun"]
    shown, vault = _face_vault(conn, alice, {"kind": "runes", "code": code, "tries": []})
    assert shown["facing"]["lock"] == {"kind": "runes", "runes": list(D.RUNES), "slots": 4, "tries": [], "left": 8} and "code" not in json.dumps(shown)
    first = _do(conn, alice, "rune", picks=["sun", "sun", "moon", "leaf"])["dungeon"]["run"]
    assert first["facing"]["lock"]["tries"] == [{"guess": ["sun", "sun", "moon", "leaf"], "exact": 1, "near": 2,
                                               "marks": ["right", "near", "near", "miss"]}] and first["facing"]["lock"]["left"] == 7
    assert first["say"] == "1 in the right place (lit green), 2 right but in the wrong place (ringed gold)."
    assert D.rune_marks(["sun", "sun", "sun", "moon"], ["moon", "sun", "leaf", "leaf"]) == ["miss", "right", "miss", "near"]   # one sun in the code: one light
    for bad in (["sun"], ["sun", "moon", "star", "gear"], [0, 1, 2, 3]):
        with pytest.raises(lumipet.PetError, match="Choose 4 runes"):
            _do(conn, alice, "rune", picks=bad)
    for _ in range(7):
        last = _do(conn, alice, "rune", picks=["leaf"] * 4, rng=Dice(pick=4))["dungeon"]["run"]
    assert last["facing"]["lock"]["tries"] == [] and last["event"]["scenario"] == "runes_reset"       # a new code: all "wave"
    assert _do(conn, alice, "rune", picks=["wave"] * 4)["dungeon"]["run"]["at"] == vault


# --------------------------------------------------------------------------- games
def _into_game(conn, uid, game):
    run = _clear(_run(conn, uid))
    room = next(room for room in run["rooms"].values() if room["kind"] == "game")
    room["game"] = game
    _put(conn, uid, run)
    for way in _path(run, "game"):
        out = _do(conn, uid, "move", way=way)["dungeon"]["run"]
    return out


def test_the_cup_game_is_luck_the_server_already_decided(conn, alice):
    _start(conn, alice)
    shown = _into_game(conn, alice, {"kind": "cups", "prize": 2})
    assert shown["game"] == {"kind": "cups", "cups": 3} and shown["event"]["scenario"] == "game_cups"
    with pytest.raises(lumipet.PetError, match="Choose a cup"):
        _do(conn, alice, "cup", picks=[3])
    won = _do(conn, alice, "cup", picks=[2])["dungeon"]["run"]
    assert won["event"]["scenario"] == "game_win" and won["satchel"]["coins"] == won["event"]["coins"] > 0 and won["room"]["done"]
    with pytest.raises(lumipet.PetError, match="no game like that"):
        _do(conn, alice, "cup", picks=[2])
    conn.execute("DELETE FROM lumi_runs")
    _start(conn, alice)
    _into_game(conn, alice, {"kind": "cups", "prize": 2})
    lost = _do(conn, alice, "cup", picks=[0])["dungeon"]["run"]
    assert lost["event"]["scenario"] == "game_lose" and 0 < lost["satchel"]["coins"] < won["satchel"]["coins"] and lost["event"]["prize"] == 2


def test_the_matching_game_shows_a_card_only_once_it_is_turned(conn, alice):
    _start(conn, alice)
    deck = ["sun", "moon", "sun", "star", "moon", "leaf", "star", "leaf"]
    shown = _into_game(conn, alice, {"kind": "match", "deck": deck, "found": [], "open": None, "flips": 0, "last": None})
    assert shown["game"]["cards"] == [None] * 8 and shown["game"]["left"] == D.FLIPS
    one = _do(conn, alice, "flip", picks=[0])["dungeon"]["run"]["game"]
    assert one["cards"] == ["sun"] + [None] * 7 and one["open"] == 0
    with pytest.raises(lumipet.PetError, match="face down"):
        _do(conn, alice, "flip", picks=[0])
    miss = _do(conn, alice, "flip", picks=[1])["dungeon"]["run"]
    assert miss["game"]["cards"][:2] == ["sun", "moon"] and miss["game"]["found"] == [] and miss["event"]["scenario"] == "match_miss"
    again = _do(conn, alice, "flip", picks=[0])["dungeon"]["run"]["game"]
    assert again["cards"][1] is None, "the missed card has turned back"
    pair = _do(conn, alice, "flip", picks=[2])["dungeon"]["run"]
    assert pair["game"]["found"] == [0, 2] and pair["event"]["scenario"] == "match_pair"
    for a, b in ((1, 4), (3, 6), (5, 7)):
        _do(conn, alice, "flip", picks=[a])
        done = _do(conn, alice, "flip", picks=[b])["dungeon"]["run"]
    assert done["event"] == {"scenario": "game_win", "coins": done["satchel"]["coins"], "pairs": 4} and done["room"]["done"]


def test_the_matching_game_ends_when_the_turns_run_out(conn, alice):
    _start(conn, alice)
    deck = ["sun", "moon", "sun", "star", "moon", "leaf", "star", "leaf"]
    _into_game(conn, alice, {"kind": "match", "deck": deck, "found": [], "open": None, "flips": 0, "last": None})
    for _ in range(D.FLIPS // 2):
        _do(conn, alice, "flip", picks=[0])
        out = _do(conn, alice, "flip", picks=[1])["dungeon"]["run"]
    assert out["event"]["scenario"] == "game_lose" and out["event"]["pairs"] == 0 and out["room"]["done"] and out["satchel"]["coins"] > 0


# --------------------------------------------------------------------------- API
@pytest.fixture
def on(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    return client


def test_a_trip_through_the_page(on, conn):
    state = on.get("/api/media/games/lumi-pet/state").json()
    assert state["dungeon"]["run"] is None and [env["open"] for env in state["dungeon"]["environments"]][:3] == [True, True, False]
    assert on.post("/api/media/games/lumi-pet/dungeon", json={"env": "rooftop-garden"}).status_code == 400
    run = on.post("/api/media/games/lumi-pet/dungeon", json={"env": "crystal-cavern"}).json()["dungeon"]["run"]
    assert run["env"]["name"] == "Crystal Cavern" and run["room"]["kind"] == "start"
    closed = next((way for way in "nesw" if way not in run["room"]["exits"]), None)
    if closed:
        assert on.post("/api/media/games/lumi-pet/dungeon/act", json={"action": "move", "way": closed}).status_code == 400
    for body in ({"action": "move", "way": "up"}, {"action": "teleport"}, {"action": "sokoban", "moves": "rrx"}, {"action": "leave", "coins": 9999},
                 {"action": "move", "way": "n", "to": "3,3"}, {"action": "cup", "picks": list(range(300))}):
        assert on.post("/api/media/games/lumi-pet/dungeon/act", json=body).status_code == 422       # only choices, never rooms or results
    home = on.post("/api/media/games/lumi-pet/dungeon/act", json={"action": "leave"}).json()
    assert home["dungeon"]["run"]["over"] == "home" and home["coins"] == 1000
    on.cookies.clear()
    assert on.post("/api/media/games/lumi-pet/dungeon", json={"env": "crystal-cavern"}).status_code == 401


# --------------------------------------------------------------------------- her health between trips
MIN = 60_000


def _care(conn, uid, action, now=T0, **kwargs):
    with transaction(conn):
        return lumipet.act(conn, uid, action, now=now, **kwargs)


def _home_hurt(conn, uid, hp):
    """A trip that ends at once, with this much health left."""
    _start(conn, uid)
    run = _run(conn, uid)
    run["hp"] = hp
    _put(conn, uid, run)
    return _do(conn, uid, "leave")


def test_bruises_come_home_and_mend_in_their_share_of_ten_minutes(conn, alice):
    full = lumibattle.stats(_pet())["hp"]
    out = _home_hurt(conn, alice, full // 2)
    assert out["health"] == {"hp": full // 2, "max": full, "mends_in": 5 * MIN, "out": False}       # half gone: half of ten minutes
    with transaction(conn):
        later = lumipet.state(conn, alice, T0 + 2 * MIN)
    assert later["health"]["hp"] == int(full * 0.7) and later["health"]["mends_in"] == 3 * MIN
    with transaction(conn):
        run = lumipet.dungeon_start(conn, alice, "moonlit-shrine", T0 + 2 * MIN, random.Random(1))["dungeon"]["run"]
    assert run["hp"] == int(full * 0.7) and run["hpMax"] == full                                    # she sets out as hurt as she is
    with transaction(conn):
        lumipet.dungeon_act(conn, alice, "abandon", now=T0 + 2 * MIN)
        whole = lumipet.state(conn, alice, T0 + 6 * MIN)       # a whole number of HP rounds the wound up a little
    assert whole["health"]["hp"] == full and whole["health"]["mends_in"] == 0 and whole["pet"]["wound"] == 0


def test_giving_up_and_being_beaten_leave_their_mark_and_a_paid_wake_heals_it_all(conn, alice):
    full = lumibattle.stats(_pet())["hp"]
    _start(conn, alice)
    run = _run(conn, alice)
    run["hp"] = 10
    _put(conn, alice, run)
    assert _do(conn, alice, "abandon")["health"]["hp"] == 10
    _set_pet(conn, alice, wound=0.0)
    _start(conn, alice)
    _only_one_enemy(conn, alice, hp=1)
    fight = _walk_into_enemy(conn, alice)["fight"]
    while fight["status"] == "active":
        with transaction(conn):
            out = lumipet.fight_act(conn, alice, fight["id"], fight["turn"], "defend", T0, Dice(chance=0.01))
            fight = out["fight"]
    assert out["pet"]["wound"] == 1 and out["health"] == {"hp": 1, "max": full, "mends_in": lumibattle.FAINT_MS, "out": False}
    assert _care(conn, alice, "heal")["health"]["hp"] == full                    # 50 coins: awake and whole


def test_a_nap_mends_twice_as_fast_as_waiting(conn, alice):
    full = lumibattle.stats(_pet())["hp"]
    _home_hurt(conn, alice, full // 2)
    _care(conn, alice, "nap")
    with transaction(conn):
        napping = lumipet.state(conn, alice, T0 + MIN)
    assert napping["pet"]["wound"] == pytest.approx(0.3)                         # a minute of nap: two minutes' mending
    with transaction(conn):
        assert lumipet.state(conn, alice, T0 + 5 * MIN)["health"]["hp"] == full  # the whole nap covers a whole bar


def test_bought_food_mends_her_at_home_and_on_a_trip_but_the_free_bowl_does_not(conn, alice):
    full = lumibattle.stats(_pet())["hp"]
    _home_hurt(conn, alice, 1)
    _set_pet(conn, alice, wound=0.9)
    _care(conn, alice, "buy", item="lunch", count=2)
    assert _care(conn, alice, "feed", item="apple")["pet"]["wound"] == 0.9       # free, so no mending
    _set_pet(conn, alice, cooldowns={})
    fed = _care(conn, alice, "feed", item="lunch", now=T0 + 1)
    assert fed["pet"]["wound"] == pytest.approx(0.45, abs=1e-4)                  # lunch fills 45, and mends 45%
    _set_pet(conn, alice, cooldowns={})
    with transaction(conn):
        lumipet.dungeon_start(conn, alice, "moonlit-shrine", T0 + 1, random.Random(1))
    run = _run(conn, alice)
    run["hp"] = 5
    _put(conn, alice, run)
    assert _care(conn, alice, "feed", item="lunch", now=T0 + 1)["health"] == {"hp": 5 + int(full * 0.45), "max": full, "mends_in": 0, "out": True}


def test_a_potion_at_home_gives_back_half_and_only_when_it_helps(conn, alice):
    full = lumibattle.stats(_pet())["hp"]
    with pytest.raises(lumipet.PetError, match="already at full health"):
        _care(conn, alice, "drink")
    _home_hurt(conn, alice, 1)
    with pytest.raises(lumipet.PetError, match="No potions left"):
        _care(conn, alice, "drink")
    _set_pet(conn, alice, potions=2, wound=0.8)
    out = _care(conn, alice, "drink")
    assert out["pet"]["potions"] == 1 and out["pet"]["wound"] == pytest.approx(0.3) and out["health"]["hp"] == int(full * 0.7)
    _start(conn, alice)
    with pytest.raises(lumipet.PetError, match="out exploring"):
        _care(conn, alice, "drink")


def test_looking_around_tells_more_of_the_room_and_the_story_is_kept(conn, alice):
    from mnemerynne import lumitales

    shown = _start(conn, alice)
    looks = lumitales.LIBRARY["moonlit-shrine"]["look"]
    assert shown["scene"] in looks and shown["story"] == [shown["say"]]         # what the room is like, and the tale so far
    first = _do(conn, alice, "look")["dungeon"]["run"]
    second = _do(conn, alice, "look")["dungeon"]["run"]
    assert first["event"] == {"scenario": "look", "n": 1} and first["scene"] == shown["scene"]     # the room is still itself
    assert len({shown["scene"], first["say"], second["say"]}) == 3 and {first["say"], second["say"]} <= set(looks)
    assert second["story"] == [shown["say"], first["say"], second["say"]] and second["at"] == shown["at"]
    for _ in range(20):
        last = _do(conn, alice, "look")["dungeon"]["run"]
    assert len(last["story"]) == D.LOG and last["story"][-1] == last["say"]     # only the last few lines are kept
