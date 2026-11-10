"""Lumi Pet's battles (mnemerynne/lumibattle.py): the turn rules, scaling, rewards, skills and mods.
Ported from MommyBot's test/touhou-battles.test.js where the rule came from there."""

import json
import random

import pytest

from mnemerynne import coins, lumibattle as B, lumipet, users
from mnemerynne.db import transaction
from test_media import (_login, _no_network, _pair, _screens_for, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings)

T0 = 1_800_000_000_000
TREES = {tree["id"]: [node["id"] for node in tree["nodes"]] for tree in B.CATALOG["trees"]}


class Script:
    """A scripted dice: random() gives the listed numbers in turn (then `rest`); randrange(n) always gives `pick`."""

    def __init__(self, rolls=(), rest=0.5, pick=0):
        self.rolls, self.rest, self.pick = list(rolls), rest, pick

    def random(self):
        return self.rolls.pop(0) if self.rolls else self.rest

    def randrange(self, n):
        return min(self.pick, n - 1)


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


def _pet(level=1, skills=(), mods=(), glow="rose", moves=None):
    pet = {"glow": glow, "boost": None, **B.fresh_fields()}
    pet.update(level=level, skills=list(skills), mods=(list(mods) + [None] * 5)[:5])
    if moves:
        pet["moves"] = moves
    return pet


def _fight(pet, enemy_id, level=None, boost=None):
    lumi = B._lumi(pet)
    return {"player": lumi, "enemy": B._enemy(B._ENEMIES[enemy_id], level or pet["level"], pet["level"], lumi["element"]),
            "choice": "common", "boost": boost, "outcome": None, "reward": None, "log": []}


def _set(conn, uid, **fields):
    with transaction(conn):
        lumipet.state(conn, uid, T0)
    pet = json.loads(conn.execute("SELECT data FROM lumi_pets WHERE user_id = ?", (uid,)).fetchone()[0])
    pet.update(fields)
    conn.execute("UPDATE lumi_pets SET data = ? WHERE user_id = ?", (json.dumps(pet), uid))


def _do(conn, uid, action, **kwargs):
    with transaction(conn):
        return lumipet.act(conn, uid, action, now=T0, **kwargs)


def _start(conn, uid, tier="common", rng=None, now=T0):
    with transaction(conn):
        return lumipet.fight_start(conn, uid, tier, now, rng or Script())["fight"]


def _turn(conn, uid, fight, action, rng=None, now=T0):
    with transaction(conn):
        return lumipet.fight_act(conn, uid, fight["id"], fight["turn"], action, now, rng or Script())["fight"]


# --------------------------------------------------------------------------- the catalog
def test_the_shipped_roster_is_twenty_originals_across_five_tiers_with_six_trees():
    enemies = B.CATALOG["enemies"]
    assert len(enemies) == 20 and [sum(e["tier"] == t for e in enemies) for t in B.TIERS] == [7, 5, 4, 3, 1]
    assert [tree["id"] for tree in B.CATALOG["trees"]] == ["fighter", "thief", "monk", "mage", "cleric", "paladin"]
    assert all(len(nodes) == 8 for nodes in TREES.values()) and sum(B.NODE_COSTS) == 22
    assert [sum(m["tek"] == tek for m in B.CATALOG["mods"]) >= need for tek, need in (("magitek", 2), ("cybertek", 3))] == [True, True]
    assert [tek for tek, _ in B.MOD_SLOTS].count("magitek") == 2 and [tek for tek, _ in B.MOD_SLOTS].count("cybertek") == 3


def test_bad_battle_catalogs_are_refused(tmp_path):
    def broken(change):
        data = json.loads(json.dumps(B.CATALOG))
        change(data)
        path = tmp_path / "battle.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            B.load_catalog(path)

    broken(lambda d: d["trees"][0]["nodes"].pop())
    broken(lambda d: d["trees"][0]["nodes"][0].update(id=d["trees"][1]["nodes"][0]["id"]))
    broken(lambda d: d["trees"][0]["nodes"][0].update(effect="explode"))
    broken(lambda d: d["trees"][0]["nodes"][1].update(bonus={"luck": 5}))
    broken(lambda d: d["enemies"][0].update(weak=["wind"], resist=["wind"]))
    broken(lambda d: d["enemies"][0].update(tier="mythic"))
    broken(lambda d: d["enemies"][0]["moves"][0].update(level=5))
    broken(lambda d: d["enemies"][0]["shape"].update(hp=9))
    broken(lambda d: d.update(enemies=[e for e in d["enemies"] if e["tier"] != "legendary"]))
    broken(lambda d: d["mods"][0].update(tek="steamtek"))


# --------------------------------------------------------------------------- the formula
def test_damage_is_touhou_traders_formula_times_the_pace():
    state = _fight(_pet(10), "dust-bunny")
    lumi, enemy = state["player"], state["enemy"]
    before = enemy["hp"]
    B._strike(state, lumi, enemy, lumi["moves"][0], Script(rest=0.999999))      # hits, top of the damage roll, no crit
    expected = int(int((((2 * 10 / 5 + 2) * 50 * lumi["stats"]["attack"] / enemy["stats"]["defense"]) / 50 + 2) * B.PACE) * (0.85 + 0.999999 * 0.15))
    assert before - enemy["hp"] == expected and "Pounce" in state["log"][-1]


def test_weak_resisted_immune_and_glow_matched_elements(conn):
    spells = TREES["mage"]
    mage = _pet(20, spells, glow="lagoon", moves=["frost-shard", "ember", "shadow-bolt", "glow-spark"])

    def hit(enemy_id, move):
        state = _fight(mage, enemy_id)
        B._strike(state, state["player"], state["enemy"], state["player"]["moves"][move], Script([0.0, 0.999999], rest=0.999999))   # a hit, at the top of the damage roll
        return state["enemy"]["hpMax"] - state["enemy"]["hp"], state["log"][-1]

    weak, words = hit("cinder-mite", 0)                                         # ice into a fire bug
    plain = hit("dust-bunny", 0)[0]
    resisted, said = hit("frost-moth", 0)
    assert "super effective" in words and "resisted" in said and weak > plain > resisted
    assert hit("comet-drake", 1) == (0, "Lumi used Ember. It has no effect on Comet Drake!")
    assert hit("void-jelly", 2)[0] == 0
    state = _fight(mage, "dust-bunny")
    assert B._multiplier(state["player"], state["enemy"], state["player"]["moves"][0]) == B.GLOW_MATCH      # her own glow's element
    assert B._multiplier(state["player"], state["enemy"], state["player"]["moves"][1]) == 1.0


def test_physical_and_magic_use_different_defences():
    brawler = _pet(20, moves=["pounce", "glow-spark"])
    slime, jelly = _fight(brawler, "puddle-slime"), _fight(brawler, "void-jelly")
    assert slime["enemy"]["stats"]["defense"] > 2 * slime["enemy"]["stats"]["ward"]       # soaks claws, hates spells
    assert jelly["enemy"]["stats"]["ward"] > 1.9 * jelly["enemy"]["stats"]["defense"]     # the other way round


def test_her_glow_decides_what_hurts_her_and_the_prism_shard_copies_it():
    for glow, element in B.GLOW_ELEMENT.items():
        state = _fight(_pet(30, glow=glow), "prism-shard")
        lumi, shard = state["player"], state["enemy"]
        assert lumi["element"] == shard["element"] == element and shard["resist"] == [element]
        assert shard["weak"] == lumi["weak"] and len(lumi["weak"]) == 1 and B.WHEEL[lumi["weak"][0]][element] == 1.5


def test_guarding_halves_a_hit_and_gathers_mp():
    state = _fight(_pet(10), "moss-golem")
    state["player"]["mp"] = 0
    B.resolve(state, "defend", Script(rest=0.5))
    guarded = state["player"]["hpMax"] - state["player"]["hp"]
    plain = _fight(_pet(10), "moss-golem")
    B._strike(plain, plain["enemy"], plain["player"], plain["enemy"]["moves"][0], Script(rest=0.5))
    assert guarded == max(1, (plain["player"]["hpMax"] - plain["player"]["hp"]) // 2) and state["player"]["mp"] == B.DEFEND_MP
    assert "(guarded)" in state["log"][-1]


def test_speed_decides_who_goes_first_and_a_first_move_jumps_the_queue():
    slow = _fight(_pet(10, TREES["thief"][:1], moves=["pounce", "quick-swipe"]), "clockwork-owl")
    B.resolve(slow, "move0", Script())
    assert slow["log"][0].startswith("Clockwork Owl used")                      # the owl is far faster
    B.resolve(slow, "move1", Script())
    assert slow["log"][2].startswith("Lumi used Quick Swipe")


def test_poison_bites_at_the_end_of_each_turn_then_wears_off_and_a_ward_fades():
    pet = _pet(20, TREES["thief"][:3] + TREES["fighter"][:5], moves=["poison-thorn", "parry"])
    state = _fight(pet, "moss-golem")
    B.resolve(state, "move0", Script())
    assert state["enemy"]["status"] == {"poison": B.STATUS_TURNS - 1} and any("from the poison" in line for line in state["log"])
    B.resolve(state, "move1", Script())
    assert state["player"]["status"] == {"ward": 2}
    hurt = state["player"]["hp"]
    B.resolve(state, "defend", Script())
    B.resolve(state, "defend", Script())
    assert "poison" not in state["enemy"]["status"] and state["player"]["hp"] < hurt
    B.resolve(state, "defend", Script())
    assert state["player"]["status"] == {}


def test_a_stun_costs_one_action_and_thorns_prick_claws_but_not_spells():
    state = _fight(_pet(10), "bell-phantom")
    state["enemy"]["status"]["stun"] = 1
    hp = state["player"]["hp"]
    B.resolve(state, "move0", Script())
    assert state["player"]["hp"] == hp and "stunned and can't move" in " ".join(state["log"]) and "stun" not in state["enemy"]["status"]
    thorny = _fight(_pet(10), "thornling")
    B._strike(thorny, thorny["player"], thorny["enemy"], thorny["player"]["moves"][0], Script())
    assert thorny["player"]["hp"] < thorny["player"]["hpMax"] and "thorns prick" in thorny["log"][-1]
    full = thorny["player"]["hp"]
    B._strike(thorny, thorny["player"], thorny["enemy"], thorny["player"]["moves"][1], Script())
    assert thorny["player"]["hp"] == full


def test_heals_never_overfill_and_multi_hit_moves_hit_each_time():
    monk = _pet(30, TREES["monk"], moves=["meditate", "hundred-paws"])
    state = _fight(monk, "moss-golem")
    state["player"]["hp"] = state["player"]["hpMax"] - 3
    B._strike(state, state["player"], state["player"], state["player"]["moves"][0], Script())
    assert state["player"]["hp"] == state["player"]["hpMax"]
    B._strike(state, state["player"], state["enemy"], state["player"]["moves"][1], Script())
    assert sum("Hundred Paws" in line for line in state["log"]) == 4


def test_running_works_three_times_in_four_and_a_failed_run_costs_a_hit():
    away = _fight(_pet(5), "dust-bunny")
    B.resolve(away, "run", Script([0.74]))
    assert away["outcome"] == "ran"
    stuck = _fight(_pet(5), "dust-bunny")
    B.resolve(stuck, "run", Script([0.75]))
    assert stuck["outcome"] is None and stuck["player"]["hp"] < stuck["player"]["hpMax"]


# --------------------------------------------------------------------------- scaling
def test_enemies_scale_with_lumi_by_tier_and_grow_tougher_as_she_learns():
    low, high = B._enemy(B._ENEMIES["dust-bunny"], 5, 5, "fire"), B._enemy(B._ENEMIES["dust-bunny"], 45, 45, "fire")
    assert high["hp"] > 5 * low["hp"] and high["stats"]["attack"] > 3 * low["stats"]["attack"]
    same_level_early, same_level_late = B._enemy(B._ENEMIES["dust-bunny"], 30, 1, "fire"), B._enemy(B._ENEMIES["dust-bunny"], 30, 50, "fire")
    assert same_level_late["hp"] == pytest.approx(2 * same_level_early["hp"], rel=0.02)   # health keeps pace with her skill points
    common, legend = B._enemy(B._ENEMIES["dust-bunny"], 30, 30, "fire"), B._enemy(B._ENEMIES["comet-drake"], 30, 30, "fire")
    assert legend["hp"] > 2 * common["hp"] and legend["stats"]["attack"] > common["stats"]["attack"]
    assert [len(B._enemy(B._ENEMIES["comet-drake"], level, level, "fire")["moves"]) for level in (1, 10, 20, 30, 40)] == [1, 2, 3, 4, 5]


def test_an_enemy_appears_within_two_levels_and_never_outside_one_to_fifty(conn, alice):
    for level, pick, expected in ((1, 0, 1), (20, 0, 18), (20, 4, 22), (50, 4, 50)):
        _set(conn, alice, level=level)
        fight = _start(conn, alice, rng=Script(pick=pick))
        assert fight["enemy"]["level"] == expected
        _turn(conn, alice, fight, "run", Script([0.1]))


# --------------------------------------------------------------------------- fights, saved
def test_one_fight_at_a_time_that_waits_and_refuses_stale_buttons(conn, alice):
    fight = _start(conn, alice)
    assert fight["turn"] == 0 and fight["status"] == "active" and fight["log"] == [f"A wild {fight['enemy']['name']} appears!"]
    with pytest.raises(lumipet.PetError, match="already in a fight"):
        _start(conn, alice)
    with transaction(conn):
        assert lumipet.state(conn, alice, T0 + 30 * 24 * lumipet.HOUR)["fight"]["id"] == fight["id"]      # a month later, still waiting
    after = _turn(conn, alice, fight, "defend")
    assert after["turn"] == 1
    with pytest.raises(lumipet.PetError, match="already happened"):
        _turn(conn, alice, fight, "move0")                                      # the old page's button
    with pytest.raises(lumipet.PetError, match="doesn't have that move"):
        _turn(conn, alice, after, "move3")
    bob = users.create_user(conn, "bob", "password123")
    with transaction(conn), pytest.raises(lumipet.PetError, match="isn't Lumi's"):
        lumipet.fight_act(conn, bob, fight["id"], 1, "defend", T0)


def test_a_win_pays_exp_and_coins_once_and_levels_her_up(conn, alice):
    _set(conn, alice, level=30)
    fight = _start(conn, alice, rng=Script(pick=2))                             # same level, third common
    while fight["status"] == "active":
        fight = _turn(conn, alice, fight, "move0", Script(rest=0.01))
    gained = 15 + 30 * 3
    assert fight["status"] == "victory" and fight["reward"] == {"exp": gained, "coins": int(gained * 0.6), "level": 30, "levels": 0}
    assert coins.peek(conn, alice) == 1000 + int(gained * 0.6)
    with transaction(conn):
        state = lumipet.state(conn, alice, T0)
    assert state["pet"]["exp"] == gained and state["pet"]["wins"] == 1 and state["fight"] is None
    assert conn.execute("SELECT ref FROM coin_transactions WHERE type = 'reward' AND game = 'lumi-pet'").fetchone()[0] == f"lumi-battle:{fight['id']}"
    with pytest.raises(lumipet.PetError, match="is over"):
        _turn(conn, alice, fight, "move0")
    with transaction(conn):
        assert coins.prize(conn, alice, 500, "lumi-pet", f"lumi-battle:{fight['id']}") == 0     # that win can never pay again


def test_levels_follow_the_curve_and_stop_at_fifty(conn, alice):
    _set(conn, alice, level=1)
    fight = _start(conn, alice)
    while fight["status"] == "active":
        fight = _turn(conn, alice, fight, "move0", Script(rest=0.01))
    assert fight["reward"]["level"] == 1 and B.level_threshold(1) == 35           # 18 EXP of the 35 needed
    pet = _pet(49)
    pet["exp"] = B.level_threshold(49) - 1
    state = {"enemy": {"level": 50, "tier": "legendary"}, "choice": "gamble", "boost": "luck", "player": {"stats": {"coins": 20}}, "log": []}
    with transaction(conn):
        coins.held(conn, alice)
        B._reward(conn, alice, pet, state, 999)
    gained = int(int((15 + 150 + 50) * 1.2) * 1.25)
    assert (pet["level"], pet["exp"]) == (50, 0) and state["reward"]["exp"] == gained
    assert state["reward"]["coins"] == int(int(int(int(215 * 0.6) * 1.2) * 1.25) * 1.2)       # gamble, lucky treat, Lucky Whiskers


def test_a_defeat_is_a_free_ten_minute_nap_or_fifty_coins(conn, alice):
    _set(conn, alice, level=1)
    fight = _start(conn, alice, "legendary")
    while fight["status"] == "active":
        fight = _turn(conn, alice, fight, "defend", Script(rest=0.01))
    assert fight["status"] == "defeat" and "nap for ten minutes" in fight["log"][-1] and coins.peek(conn, alice) == 1000
    with pytest.raises(lumipet.PetError, match="still napping"):
        _start(conn, alice, now=T0 + B.FAINT_MS - 1)
    assert _start(conn, alice, now=T0 + B.FAINT_MS)["status"] == "active"       # free once the nap is over
    _set(conn, alice, faint_until=T0 + 5000)
    conn.execute("DELETE FROM lumi_battles")
    assert _do(conn, alice, "heal")["said"] == {"bought": "heal", "count": 1, "paid": 50}
    assert coins.peek(conn, alice) == 950 and _start(conn, alice)["status"] == "active"
    with pytest.raises(lumipet.PetError, match="already up"):
        _do(conn, alice, "heal")


def test_running_away_costs_and_pays_nothing(conn, alice):
    fight = _turn(conn, alice, _start(conn, alice), "run", Script([0.1]))
    with transaction(conn):
        pet = lumipet.state(conn, alice, T0)["pet"]
    assert fight["status"] == "ran" and (pet["wins"], pet["losses"], pet["faint_until"], pet["exp"]) == (0, 0, 0, 0)
    assert _start(conn, alice)["status"] == "active"


def test_potions_are_bought_between_fights_and_only_used_when_they_help(conn, alice):
    assert _do(conn, alice, "potions", count=3)["coins"] == 1000 - 60
    with pytest.raises(lumipet.PetError, match="up to 10"):
        _do(conn, alice, "potions", count=8)
    fight = _start(conn, alice, "rare")
    with pytest.raises(lumipet.PetError, match="full health"):
        _turn(conn, alice, fight, "potion")
    fight = _turn(conn, alice, fight, "defend")
    hurt = fight["player"]["hp"]
    assert hurt < fight["player"]["hpMax"]
    fight = _turn(conn, alice, fight, "potion", Script(rest=0.999))             # the enemy's reply misses
    with transaction(conn):
        assert lumipet.state(conn, alice, T0)["pet"]["potions"] == 2
    assert fight["player"]["hp"] > hurt and "potion restores" in " ".join(fight["log"])
    _set(conn, alice, potions=0)
    with pytest.raises(lumipet.PetError, match="No potions left"):
        _turn(conn, alice, _turn(conn, alice, fight, "defend"), "potion")


def test_a_treats_boost_is_used_by_the_next_fight(conn, alice):
    _set(conn, alice, boost="power")
    fight = _start(conn, alice)
    with transaction(conn):
        assert lumipet.state(conn, alice, T0)["pet"]["boost"] is None
    assert fight["boost"] == "power"
    plain, boosted = _fight(_pet(10), "moss-golem"), _fight(_pet(10), "moss-golem", boost="power")
    for state in (plain, boosted):
        B._strike(state, state["player"], state["enemy"], state["player"]["moves"][0], Script())
    assert boosted["enemy"]["hp"] < plain["enemy"]["hp"]


def test_spells_need_mp(conn, alice):
    _set(conn, alice, level=30, skills=TREES["mage"], moves=["starfall"])
    fight = _start(conn, alice, "epic")
    mp = fight["player"]["mp"]
    for _ in range(mp // 14):
        fight = _turn(conn, alice, fight, "move0", Script(rest=0.999))          # every blow misses; only the MP goes
    assert fight["player"]["mp"] == mp % 14
    with pytest.raises(lumipet.PetError, match="Not enough MP"):
        _turn(conn, alice, fight, "move0")


# --------------------------------------------------------------------------- skills, moves and mods
def test_skill_points_come_one_a_level_and_trees_are_learned_in_order(conn, alice):
    _set(conn, alice, level=5)                                                  # 4 points
    with pytest.raises(lumipet.PetError, match="Armor Break needs Heavy Paw first"):
        _do(conn, alice, "learn", item="armor-break")
    first = _do(conn, alice, "learn", item="heavy-paw")
    assert first["progress"]["points"] == 3 and first["pet"]["moves"] == ["pounce", "glow-spark", "heavy-paw"]
    out = _do(conn, alice, "learn", item="armor-break")                       # a branch: no need for Tough Fur on the other arm
    assert out["progress"]["points"] == 1 and len(out["pet"]["moves"]) == 4
    with pytest.raises(lumipet.PetError, match="Parry needs Tough Fur first"):
        _do(conn, alice, "learn", item="parry")
    _do(conn, alice, "learn", item="tough-fur")
    for item, why in (("brawn", "Not enough skill points"), ("heavy-paw", "already knows"), ("apple", "isn't a skill")):
        with pytest.raises(lumipet.PetError, match=why):
            _do(conn, alice, "learn", item=item)
    assert B.points(_pet(50)) == 49 and B.points(_pet(50, TREES["fighter"] + TREES["mage"])) == 5        # two whole trees and change


def test_passives_and_mods_lift_her_numbers_and_respec_hands_points_back(conn, alice):
    plain, built = B.stats(_pet(20)), B.stats(_pet(20, TREES["fighter"][:6]))
    assert built["attack"] == int(plain["attack"] * 1.10) and built["defense"] == int(plain["defense"] * 1.10) and built["hp"] == int(plain["hp"] * 1.12)
    _set(conn, alice, level=20, skills=TREES["fighter"][:6], moves=["heavy-paw"])
    out = _do(conn, alice, "respec")
    assert out["said"]["paid"] == 100 and out["pet"]["skills"] == [] and out["pet"]["moves"] == ["pounce", "glow-spark"] and out["progress"]["points"] == 19
    with pytest.raises(lumipet.PetError, match="no skill points to hand back"):
        _do(conn, alice, "respec")


def test_she_carries_up_to_four_moves_and_at_least_one(conn, alice):
    _set(conn, alice, level=30, skills=TREES["fighter"], moves=["pounce"])
    with pytest.raises(lumipet.PetError, match="at least one"):
        _do(conn, alice, "equip", item="pounce")
    for move in ("heavy-paw", "armor-break", "parry"):
        _do(conn, alice, "equip", item=move)
    with pytest.raises(lumipet.PetError, match="Take one off"):
        _do(conn, alice, "equip", item="whirl-claw")
    assert _do(conn, alice, "equip", item="pounce")["pet"]["moves"] == ["heavy-paw", "armor-break", "parry"]
    with pytest.raises(lumipet.PetError, match="hasn't learned"):
        _do(conn, alice, "equip", item="starfall")


def test_mod_slots_open_by_level_take_their_own_kind_and_a_mod_is_bought_once(conn, alice):
    _set(conn, alice, level=12)
    assert [slot["open"] for slot in _do(conn, alice, "glow", glow="rose")["progress"]["slots"]] == [True, False, False, False, False]
    with pytest.raises(lumipet.PetError, match="opens at level 15"):
        _do(conn, alice, "mod", item="aether-coil", slot=1)
    with pytest.raises(lumipet.PetError, match="takes a cybertek mod"):
        _do(conn, alice, "mod", item="aether-coil", slot=0)
    bought = _do(conn, alice, "mod", item="servo-claws", slot=0)
    assert bought["said"]["paid"] == 400 and bought["coins"] == 600 and bought["progress"]["stats"]["attack"] == int(B.base_stats(12)["attack"] * 1.06)
    _set(conn, alice, level=30)
    moved = _do(conn, alice, "mod", item="servo-claws", slot=2)                 # hers already: moving it is free, and it leaves the old slot
    assert moved["said"] is None and moved["coins"] == 600 and moved["pet"]["mods"] == [None, None, "servo-claws", None, None]
    assert _do(conn, alice, "mod", item=None, slot=2)["pet"]["mods"] == [None] * 5
    with pytest.raises(lumipet.PetError, match="Choose a mod slot"):
        _do(conn, alice, "mod", item="servo-claws", slot=7)


def test_five_mods_are_worth_about_what_the_plan_budgeted():
    fitted = _pet(50, mods=["servo-claws", "aether-coil", "nano-plating", "ward-lattice", "neural-whiskers"])
    assert B.stats(fitted)["hp"] == int(B.base_stats(50)["hp"] * 1.15) and B.bonuses(fitted)["attack"] == 6 and B.bonuses(fitted)["crit"] == 6
    assert B.bonuses(_pet(9, mods=["servo-claws"]))["attack"] == 0              # a slot that isn't open yet gives nothing


def test_every_enemy_can_be_fought_to_a_finish_at_every_tenth_level():
    rng = random.Random(4)
    for enemy in B.CATALOG["enemies"]:
        for level in (1, 10, 20, 30, 40, 50):
            state, turns = _fight(_pet(level, glow=rng.choice(list(B.GLOW_ELEMENT))), enemy["id"], level), 0
            while not state["outcome"] and turns < 200:
                B.resolve(state, rng.choice(["move0", "move1", "defend"]), rng)
                turns += 1
            assert state["outcome"] in ("victory", "defeat"), (enemy["id"], level)
            assert len(state["log"]) <= 8 and min(state["player"]["hp"], state["enemy"]["hp"]) == 0


# --------------------------------------------------------------------------- API
@pytest.fixture
def on(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    return client


def test_a_fight_through_the_page(on):
    state = on.get("/api/media/games/lumi-pet/state").json()
    assert state["fight"] is None and state["progress"]["points"] == 0 and len(state["battle"]["trees"]) == 6
    assert "enemies" not in state["battle"]                                     # what she might meet stays a surprise
    fight = on.post("/api/media/games/lumi-pet/battle", json={"tier": "common"}).json()["fight"]
    assert fight["status"] == "active" and on.get("/api/media/games/lumi-pet/state").json()["fight"]["id"] == fight["id"]
    assert on.post("/api/media/games/lumi-pet/battle", json={"tier": "common"}).status_code == 400
    turn = on.post("/api/media/games/lumi-pet/battle/act", json={"battle": fight["id"], "turn": 0, "action": "defend"})
    assert turn.status_code == 200 and turn.json()["fight"]["turn"] == 1
    assert on.post("/api/media/games/lumi-pet/battle/act", json={"battle": fight["id"], "turn": 0, "action": "defend"}).status_code == 400
    for body in ({"battle": fight["id"], "turn": 1, "action": "move0", "damage": 9999}, {"battle": fight["id"], "turn": 1, "action": "win"},
                 {"tier": "common", "enemy": "dust-bunny"}):
        url = "/api/media/games/lumi-pet/battle" + ("" if "tier" in body else "/act")
        assert on.post(url, json=body).status_code == 422                       # the page can't name an enemy, a result or a number
    on.cookies.clear()
    assert on.post("/api/media/games/lumi-pet/battle", json={"tier": "common"}).status_code == 401


def test_every_tree_branches_in_its_own_shape_and_old_builds_still_stand(conn, alice, tmp_path):
    shapes = set()
    for tree in B.CATALOG["trees"]:
        nodes, seen = tree["nodes"], set()
        assert nodes[0]["needs"] == [] and all(node["needs"] for node in nodes[1:])
        for node in nodes:
            assert set(node["needs"]) <= seen                                   # only skills listed before it: no loops
            seen.add(node["id"])
        assert any(len(node["needs"]) > 1 for node in nodes) or tree["id"] in ("thief", "monk")      # most trees have a skill that needs two
        assert len({tuple(node["at"]) for node in nodes}) == 8
        assert any(sum(1 for other in nodes if node["id"] in other["needs"]) > 1 for node in nodes), tree["id"]     # and every tree forks somewhere
        shapes.add(tuple((tuple(nodes.index(next(n for n in nodes if n["id"] == need)) for need in node["needs"]), *node["at"]) for node in nodes))
    assert len(shapes) == 6                                                     # no two classes share a shape
    for tree in B.CATALOG["trees"]:                                             # a tree learned top to bottom under the old rule is still a build
        _set(conn, alice, level=50, skills=[], moves=["pounce"])
        for node in tree["nodes"]:
            _do(conn, alice, "learn", item=node["id"])
    cheap = ["ember", "frost-shard", "gust", "shadow-bolt", "spirit-lance", "starfall"]            # the Mage's last spell without the middle school
    _set(conn, alice, level=50, skills=[], moves=["pounce"])
    for item in cheap:
        _do(conn, alice, "learn", item=item)
    assert B.spent(_pet(50, cheap)) == 22 - 2 - 3                               # Deep Mind and Mana Well left for later
    good = json.loads((B.Path(B.__file__).parent / "lumipet_data" / "battle.json").read_text(encoding="utf-8"))
    for change in (lambda d: d["trees"][0]["nodes"][1].update(needs=["comet-cleave"]),          # a skill that comes later
                   lambda d: d["trees"][0]["nodes"][1].update(needs=[]),                        # a second root
                   lambda d: d["trees"][0]["nodes"][1].update(at=[1, 0]),                       # two skills in one place
                   lambda d: d["trees"][0]["nodes"][1].update(at=[3, 9]),
                   lambda d: d["trees"][0]["nodes"][1].update(at=[0, 0])):                      # beside what it needs, not below it
        data = json.loads(json.dumps(good))
        change(data)
        (tmp_path / "battle.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="Lumi Pet's battles"):
            B.load_catalog(tmp_path / "battle.json")
