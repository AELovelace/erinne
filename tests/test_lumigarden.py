"""Lumi Pet's Stardust Garden (mnemerynne/lumigarden.py): flowers and their ladder, the press and its daily price,
helpers and Lumi's build, abilities, the two minigames, gifts, moonrise, reminders, and the page only sending choices."""

import json
import random

import pytest

from mnemerynne import coins, lumibattle, lumigarden as G, lumipet
from mnemerynne.db import transaction
from test_lumidungeon import Dice
from test_media import (_login, _no_network, _pair, _screens_for, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings)
from test_push import NOON, _needs, _sweep, alice, phone, service  # noqa: F401

T0 = 1_800_000_000_000
MIN, HOUR = 60_000, 3_600_000
QUIET = Dice(chance=0.99)               # no lucky presses and no gifts


def _g(conn, uid, action, now=T0, rng=QUIET, **kwargs):
    with transaction(conn):
        return lumipet.garden_act(conn, uid, action, now=now, rng=rng, **kwargs)


def _look(conn, uid, now=T0):
    with transaction(conn):
        return lumipet.state(conn, uid, now)["garden"]


def _saved(conn, uid):
    return json.loads(conn.execute("SELECT state FROM lumi_gardens WHERE user_id = ?", (uid,)).fetchone()[0])


def _set(conn, uid, garden=None, **fields):
    """Put the saved garden in a known condition. `garden`: fields of the garden being shown."""
    state = _saved(conn, uid)
    state.update(fields)
    state["gardens"][state["at"]].update(garden or {})
    conn.execute("UPDATE lumi_gardens SET state = ? WHERE user_id = ?", (json.dumps(state), uid))


def _set_pet(conn, uid, **fields):
    pet = json.loads(conn.execute("SELECT data FROM lumi_pets WHERE user_id = ?", (uid,)).fetchone()[0])
    pet.update(fields)
    conn.execute("UPDATE lumi_pets SET data = ? WHERE user_id = ?", (json.dumps(pet), uid))


def _pet(**fields):
    return {"level": 1, "skills": [], "moves": ["pounce", "glow-spark"], "mods": [None] * 5, "potions": 0, "inventory": {}, **fields}


LADDER = [flower["id"] for flower in G.CATALOG["flowers"]]


# --------------------------------------------------------------------------- the catalog
def test_the_ladder_starts_free_runs_in_order_and_ends_in_perennials():
    flowers = G.CATALOG["flowers"]
    assert len(flowers) >= 10 and flowers[0]["coins"] == flowers[0]["stardust"] == 0
    sorts = [flower["sort"] for flower in flowers]
    assert sorts == sorted(sorts) and sorts[0] == "cut" and sorts[-1] == "perennial"       # every cut flower comes before every perennial
    assert any(flower["stardust"] for flower in flowers) and any(flower["coins"] for flower in flowers)      # some for coins, boutique ones for powder
    best_cut = max(f["powder"] / f["minutes"] for f in flowers if f["sort"] == "cut")
    assert all(f["powder"] / f["minutes"] < best_cut for f in flowers if f["sort"] == "perennial")           # tending pays more than leaving
    tulip, fern = G._FLOWERS["dusk-tulip"], G._FLOWERS["moon-fern"]
    assert tulip["minutes"] == fern["minutes"] and tulip["powder"] > fern["powder"]


def test_bad_garden_catalogs_are_refused(tmp_path):
    good = json.loads((G.Path(G.__file__).parent / "lumipet_data" / "garden.json").read_text(encoding="utf-8"))

    def broken(change):
        data = json.loads(json.dumps(good))
        change(data)
        path = tmp_path / "garden.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="Lumi's garden"):
            G.load_catalog(path)

    broken(lambda d: d["flowers"][0].update(coins=5))                           # nothing to plant in a new garden
    broken(lambda d: d["flowers"][3].update(coins=5, stardust=5))               # one price or the other
    broken(lambda d: d["flowers"][3].update(needs=0))                           # out of ladder order
    broken(lambda d: d["flowers"][2].update(colour="red"))
    broken(lambda d: d["helpers"].pop())
    broken(lambda d: d["water"].update(rounds=12, share=20))                    # would skip a whole flower
    broken(lambda d: d["press"].update(band=0))


# --------------------------------------------------------------------------- flowers
def test_a_cut_flower_is_ready_on_time_waits_and_then_overgrows_to_half(conn, alice):
    assert _g(conn, alice, "plant", plot=0, item="moondaisy")["said"] == {"planted": "moondaisy"}
    shown = _look(conn, alice, T0 + MIN)["plots"][0]
    assert (shown["flower"], shown["stage"], shown["ready_in"]) == ("moondaisy", "growing", MIN)
    with pytest.raises(lumipet.PetError, match="ready to trim"):
        _g(conn, alice, "trim", T0 + 2 * MIN - 1, plot=0)
    said = _g(conn, alice, "trim", T0 + 2 * MIN, plot=0)["said"]
    assert said == {"trimmed": 1, "powder": 6, "gift": None}
    after = _look(conn, alice, T0 + 2 * MIN)
    assert after["plots"][0] == {"flower": None, "last": "moondaisy", "stage": "empty", "ready_in": 0, "grow": 0} and after["powder"] == 6 and after["given"] == 6
    _g(conn, alice, "plant", T0 + 2 * MIN, plot=0, item="moondaisy")
    assert _look(conn, alice, T0 + 8 * MIN)["plots"][0]["stage"] == "ready"      # ready at +4, fresh for twice its growing time
    assert _look(conn, alice, T0 + 8 * MIN + 1)["plots"][0]["stage"] == "overgrown"
    assert _g(conn, alice, "trim", T0 + 9 * MIN, plot=0)["said"]["powder"] == 3  # half, and never lost
    for body in ({"plot": 9, "item": "moondaisy"}, {"plot": 0, "item": "glowbell"}, {"plot": 0, "item": "rose"}):
        with pytest.raises(lumipet.PetError):
            _g(conn, alice, "plant", **body)


def test_a_perennial_grows_back_and_harvest_all_replants_cut_flowers(conn, alice):
    _look(conn, alice)
    _set(conn, alice, garden={"unlocked": LADDER})
    _g(conn, alice, "plant", plot=0, item="moon-fern")
    _g(conn, alice, "plant", plot=1, item="dusk-tulip")
    _g(conn, alice, "plant", plot=2, item="moondaisy")
    with pytest.raises(lumipet.PetError, match="already growing"):
        _g(conn, alice, "plant", plot=0, item="moondaisy")
    said = _g(conn, alice, "trim_all", T0 + HOUR)["said"]
    assert said["trimmed"] == 3 and said["powder"] == 150 + 280 + 3      # the daisy has sat there overgrown for most of the hour
    plots = _look(conn, alice, T0 + HOUR)["plots"]
    assert [(p["flower"], p["stage"], p["ready_in"]) for p in plots[:3]] == [("moon-fern", "growing", HOUR), ("dusk-tulip", "growing", HOUR), ("moondaisy", "growing", 2 * MIN)]
    with pytest.raises(lumipet.PetError, match="Nothing is ready"):
        _g(conn, alice, "trim_all", T0 + HOUR)
    assert _g(conn, alice, "trim", T0 + HOUR + 2 * MIN, plot=2)["said"]["powder"] == 6
    assert _look(conn, alice, T0 + HOUR + 2 * MIN)["plots"][2]["flower"] is None  # trimmed one at a time, a cut flower leaves its plot


def test_each_garden_climbs_the_ladder_itself_with_coins_or_starpowder(conn, alice):
    shown = _look(conn, alice)
    assert [f["id"] for f in shown["flowers"] if f["unlocked"]] == ["moondaisy"] and [f["id"] for f in shown["flowers"] if f["next"]] == ["glowbell"]
    with pytest.raises(lumipet.PetError, match="given 40 starpowder"):
        _g(conn, alice, "unlock", item="glowbell")
    _set(conn, alice, garden={"given": 40})
    with pytest.raises(lumipet.PetError, match="before it first"):
        _g(conn, alice, "unlock", item="starpoppy")
    assert _g(conn, alice, "unlock", item="glowbell")["coins"] == 970           # an ordinary flower: coins
    with pytest.raises(lumipet.PetError, match="can unlock"):
        _g(conn, alice, "unlock", item="glowbell")
    _set(conn, alice, garden={"given": 3000, "unlocked": LADDER[:5]}, powder=799)
    with pytest.raises(lumipet.PetError, match="level 5"):
        _g(conn, alice, "unlock", item="nebula-rose")
    _set_pet(conn, alice, level=5)
    with pytest.raises(lumipet.PetError, match="Not enough starpowder"):
        _g(conn, alice, "unlock", item="nebula-rose")
    _set(conn, alice, powder=800)
    out = _g(conn, alice, "unlock", item="nebula-rose")                         # a boutique flower: starpowder only
    assert out["coins"] == 970 and out["garden"]["powder"] == 0 and out["garden"]["flowers"][5]["unlocked"]


def test_plots_cost_more_each_time_and_a_garden_holds_sixteen(conn, alice):
    assert _look(conn, alice)["plot_price"] == 25 and len(_look(conn, alice)["plots"]) == 4
    out = _g(conn, alice, "plot")
    assert out["coins"] == 975 and len(out["garden"]["plots"]) == 5 and out["garden"]["plot_price"] == 31
    _set(conn, alice, garden={"plots": [G._empty() for _ in range(16)]})
    assert _look(conn, alice)["plot_price"] is None
    with pytest.raises(lumipet.PetError, match="every plot"):
        _g(conn, alice, "plot")
    conn.execute("UPDATE coin_accounts SET balance = 3")
    _set(conn, alice, garden={"plots": [G._empty() for _ in range(4)]})
    with pytest.raises(lumipet.PetError, match="Not enough coins"):
        _g(conn, alice, "plot")


# --------------------------------------------------------------------------- the press
def test_presses_are_counted_at_a_human_rate_and_squeeze_powder_into_coins(conn, alice):
    _look(conn, alice)
    _set(conn, alice, powder=1000)
    out = _g(conn, alice, "press", T0 + 1000, count=1000)                       # a second's worth, however many were claimed
    assert out["said"] == {"pressed": 15, "lucky": 0, "powder": 45, "coins": 1}
    assert out["coins"] == 1001 and out["garden"]["powder"] == 955 and out["garden"]["squeezed"] == 5 and out["garden"]["coins_today"] == 1
    lucky = _g(conn, alice, "press", T0 + 2000, rng=Dice(chance=0.0), count=15)["said"]
    assert lucky == {"pressed": 15, "lucky": 15, "powder": 45, "coins": 5}      # 5 + 45 x 5 = 230 squeezed: five coins and 30 over
    assert _g(conn, alice, "press", T0 + 2000, count=50)["said"]["pressed"] == 3     # no time has passed: only the slack
    idle = _g(conn, alice, "press", T0 + 10 * HOUR, count=5000)["said"]
    assert idle["pressed"] == 123                                               # a long wait banks ten seconds, not ten hours
    _set(conn, alice, powder=0, squeezed=0)
    assert _g(conn, alice, "press", T0 + 11 * HOUR, count=10)["said"] == {"pressed": 10, "lucky": 0, "powder": 0, "coins": 0}     # an empty hopper


def test_each_further_hundred_coins_in_a_day_costs_twice_the_powder(conn, alice):
    _look(conn, alice)
    _set(conn, alice, powder=10_000, coins_today=99, day=coins.today(conn))
    out = _g(conn, alice, "press", T0 + 1000, count=15)
    assert out["said"]["coins"] == 1 and out["garden"]["coins_today"] == 100 and out["garden"]["squeezed"] == 5     # the hundredth at 40
    assert out["garden"]["price"] == 80                                         # and the next hundred at double
    _set(conn, alice, coins_today=250)
    assert _look(conn, alice, T0 + 1000)["price"] == 160
    _set(conn, alice, day="2000-01-01", coins_today=250, squeezed=0)
    again = _g(conn, alice, "press", T0 + 2000, count=15)                       # a new day: quick again
    assert again["garden"]["coins_today"] == 1 and again["garden"]["price"] == 40


def test_the_monk_squeezes_alone_the_same_in_one_look_or_many_and_time_away_is_capped(conn, alice):
    _look(conn, alice)
    _set(conn, alice, powder=10 ** 6, helpers={**dict.fromkeys(G.TREES, 0), "monk": 5})     # one powder a second
    for step in range(1, 7):
        stepped = _look(conn, alice, T0 + step * 10_000)
    assert stepped["powder"] == 10 ** 6 - 60 and stepped["coins_today"] == 1 and stepped["squeezed"] == 20 and stepped["away"] is None
    _set(conn, alice, powder=10 ** 6, squeezed=0, coins_today=0, tick=T0)
    once = _look(conn, alice, T0 + 60_000)
    assert (once["powder"], once["squeezed"], once["coins_today"]) == (10 ** 6 - 60, 20, 1)
    _set(conn, alice, powder=10 ** 6, squeezed=0, coins_today=0, tick=T0)
    away = _look(conn, alice, T0 + 10 * HOUR)                                   # five minutes in full, then eight hours at half
    assert away["powder"] == 10 ** 6 - (300 + 8 * 3600 * 0.5) and away["away"]["ms"] == 10 * HOUR and away["away"]["powder"] == 14_700
    assert away["away"]["coins"] == away["coins_today"] > 100


def test_lumis_skills_mods_and_level_lift_her_helpers_and_a_respec_takes_it_back():
    state = G.fresh(T0)
    plain = G.rates(state, _pet(), T0)
    assert plain["tap"] == 3 and plain["idle"] == 0 and plain["lucky"] == 0.03 and plain["growth"] == 1 and plain["fresh"] == 2 and plain["kept"] == 0.5
    skilled = G.rates(state, _pet(skills=["heavy-paw", "tough-fur"]), T0)       # two Fighter skills; tough-fur is +10% defense, the Paladin's stat
    assert skilled["tap"] == pytest.approx(3 * 1.25 ** 2)
    assert G.rates(state, _pet(level=11), T0)["tap"] == pytest.approx(3 * 1.2)
    fitted = G.rates(state, _pet(level=10, mods=["servo-claws", None, None, None, None]), T0)      # +6% attack, in a slot that opens at 10
    assert fitted["tap"] == pytest.approx(3 * 1.18 * 1.06)
    assert G.rates(state, _pet(level=9, mods=["servo-claws", None, None, None, None]), T0)["tap"] == pytest.approx(3 * 1.16)      # slot not open yet
    state["helpers"].update(fighter=2, thief=4, monk=5, mage=10, cleric=5, paladin=4)
    state["upgrades"] = {"strong-press": 2, "rich-soil": 1, "long-bloom": 1, "night-shift": 2}
    built = G.rates(state, _pet(), T0)
    assert built["tap"] == pytest.approx(3 * 2 * 1.2 * 1.5) and built["idle"] == pytest.approx(0.2 * 5 * 1.2 * 1.5) and built["lucky"] == pytest.approx(0.07)
    assert built["growth"] == pytest.approx(1 / 1.3 * 0.95) and built["fresh"] == pytest.approx(2 * 1.5 * 1.25) and built["kept"] == pytest.approx(0.65)
    assert built["away_ms"] == 10 * HOUR and built["yield"] == pytest.approx(1.2)
    assert G.rates(state, _pet(skills=["twin-paws"]), T0)["idle"] == pytest.approx(built["idle"] * 1.25)         # a Monk skill lifts the Monk's helper
    assert G.rates(state, _pet(skills=[]), T0)["idle"] == pytest.approx(built["idle"])                          # and a respec takes it back


def test_helpers_are_levelled_with_starpowder_and_cost_more_each_time(conn, alice):
    assert [helper["cost"] for helper in _look(conn, alice)["helpers"]] == [30, 60, 50, 120, 100, 250]
    with pytest.raises(lumipet.PetError, match="Not enough starpowder"):
        _g(conn, alice, "helper", item="fighter")
    _set(conn, alice, powder=100)
    out = _g(conn, alice, "helper", item="fighter")
    assert out["said"] == {"helper": "fighter", "level": 1} and out["garden"]["powder"] == 70 and out["garden"]["helpers"][0]["cost"] == 40
    assert out["garden"]["rates"]["tap"] == 4.5 and out["coins"] == 1000        # starpowder, never coins
    with pytest.raises(lumipet.PetError, match="helpers"):
        _g(conn, alice, "helper", item="wizard")


# --------------------------------------------------------------------------- abilities
def test_her_equipped_moves_are_abilities_with_cooldowns(conn, alice):
    moves = _look(conn, alice)["moves"]
    assert [(m["id"], m["tree"], m["name"], m["strength"]) for m in moves] == [("pounce", "fighter", "Frenzy", 0.5), ("glow-spark", "mage", "Sunburst", 0.5)]
    with pytest.raises(lumipet.PetError, match="move ready"):
        _g(conn, alice, "ability", item="starfall")                             # not learned, not equipped
    with pytest.raises(lumipet.PetError, match="Nothing is growing"):
        _g(conn, alice, "ability", item="glow-spark")                           # and no cooldown spent on nothing
    _g(conn, alice, "plant", plot=0, item="moondaisy")
    assert _g(conn, alice, "ability", item="glow-spark")["said"] == {"ability": "mage", "moved": 1}
    assert _look(conn, alice)["plots"][0]["ready_in"] == 2 * MIN - int(0.04 * 0.5 * 2 * MIN)
    with pytest.raises(lumipet.PetError, match="ready again in 600 seconds"):
        _g(conn, alice, "ability", item="glow-spark")
    frenzy = _g(conn, alice, "ability", item="pounce")["garden"]
    assert frenzy["effects"] == {"frenzy": 30_000} and frenzy["rates"]["tap"] == 3 * 2 and frenzy["moves"][0]["ready_in"] == 180_000
    assert _look(conn, alice, T0 + 30_000)["rates"]["tap"] == 3                 # and then it wears off


def test_deeper_moves_are_stronger_and_each_tree_does_its_own_thing(conn, alice):
    _look(conn, alice)
    learned = ["heavy-paw", "quick-swipe", "twin-paws", "mend", "holy-paw"]
    _set_pet(conn, alice, skills=learned, moves=["quick-swipe", "twin-paws", "mend", "holy-paw"])
    _set(conn, alice, powder=5000, helpers={**dict.fromkeys(G.TREES, 0), "monk": 5})
    assert _g(conn, alice, "ability", item="quick-swipe")["garden"]["rates"]["lucky"] == 1.0          # Thief: every press is lucky
    lump = _g(conn, alice, "ability", item="twin-paws")["said"]                 # Monk: five minutes of the press, and twenty presses' worth
    assert lump["ability"] == "monk" and lump["powder"] == pytest.approx(0.2 * 5 * 1.25 * 300 + 3 * 1.25 * 20)     # each lifted by its own tree's skill
    with pytest.raises(lumipet.PetError, match="Nothing is overgrown"):
        _g(conn, alice, "ability", item="mend")
    _g(conn, alice, "plant", plot=0, item="moondaisy")
    assert _g(conn, alice, "ability", T0 + HOUR, item="mend")["said"] == {"ability": "cleric", "moved": 1}       # Cleric: fresh again
    assert _look(conn, alice, T0 + HOUR)["plots"][0]["stage"] == "ready"
    blessed = _g(conn, alice, "ability", T0 + HOUR, item="holy-paw")["garden"]  # Paladin: everything doubled
    assert blessed["rates"]["double"] == 2 and blessed["effects"]["double"] == 30_000
    assert _g(conn, alice, "trim", T0 + HOUR, plot=0)["said"]["powder"] == 12
    assert G._ability(_pet(), "starfall") == ("mage", 1 + 0.25 * 7)             # the last skill of a tree is the strongest


# --------------------------------------------------------------------------- the minigames
def test_watering_is_a_growing_sequence_the_server_keeps(conn, alice):
    _g(conn, alice, "plant", plot=0, item="moondaisy")
    with pytest.raises(lumipet.PetError, match="Start a round"):
        _g(conn, alice, "water_answer", picks=[0])
    shown = _g(conn, alice, "water_start", rng=Dice(2, 0, 3, 1, 2, 2, 0, 1))["garden"]["water"]
    seq = [2, 0, 3, 1, 2, 2, 0, 1]
    assert shown["show"] == [2] and shown["rounds"] == 8                        # only this round's part is ever sent
    for round_ in range(1, 8):
        out = _g(conn, alice, "water_answer", picks=seq[:round_])
        assert out["said"] == {"water": "more", "round": round_ + 1} and out["garden"]["water"]["show"] == seq[:round_ + 1]
    done = _g(conn, alice, "water_answer", picks=seq)
    assert done["said"] == {"water": "done", "rounds": 8, "moved": 1, "right": True} and done["garden"]["water"]["show"] is None
    assert done["garden"]["plots"][0]["ready_in"] == 2 * MIN - int(0.4 * 2 * MIN)       # 5% a round
    with pytest.raises(lumipet.PetError, match="ready again in 1800 seconds"):
        _g(conn, alice, "water_start")
    later = T0 + 31 * MIN
    _g(conn, alice, "trim", later, plot=0)
    _g(conn, alice, "plant", later, plot=0, item="moondaisy")
    _g(conn, alice, "water_start", later, rng=Dice(1, 1, 1, 1, 1, 1, 1, 1))
    _g(conn, alice, "water_answer", later, picks=[1])
    _g(conn, alice, "water_answer", later, picks=[1, 1])
    wrong = _g(conn, alice, "water_answer", later, picks=[1, 1, 0])
    assert wrong["said"] == {"water": "done", "rounds": 2, "moved": 1, "right": False}  # a slip keeps what was earned
    assert wrong["garden"]["plots"][0]["ready_in"] == 2 * MIN - int(0.1 * 2 * MIN)


def test_fertilizing_counts_ten_seconds_of_presses_and_no_more(conn, alice):
    _look(conn, alice)
    _set(conn, alice, garden={"unlocked": LADDER})
    _g(conn, alice, "plant", plot=0, item="dusk-tulip")
    with pytest.raises(lumipet.PetError, match="Start a round"):
        _g(conn, alice, "feed_end", count=50)
    assert _g(conn, alice, "feed_start")["garden"]["feed"]["until_in"] == 10_000
    out = _g(conn, alice, "feed_end", T0 + 10_000, count=1000)
    assert out["said"] == {"feed": "done", "presses": 123, "minutes": 10, "moved": 1}   # 12 a second at most, and ten minutes at most
    assert out["garden"]["plots"][0]["ready_in"] == HOUR - 10_000 - 10 * MIN and out["garden"]["feed"]["until_in"] is None
    with pytest.raises(lumipet.PetError, match="ready again"):
        _g(conn, alice, "feed_start", T0 + 20_000)
    later = T0 + 31 * MIN
    _g(conn, alice, "feed_start", later)
    assert _g(conn, alice, "feed_end", later + 5000, count=34)["said"]["minutes"] == 3
    _g(conn, alice, "feed_start", later + 31 * MIN)
    assert _g(conn, alice, "feed_end", later + 32 * MIN, count=100)["said"]["presses"] == 0      # far too late to count


# --------------------------------------------------------------------------- gifts
def test_a_harvest_sometimes_turns_up_a_gift(conn, alice):
    def harvest(now, *rolls):
        _g(conn, alice, "plant", now, plot=0, item="moondaisy")
        return _g(conn, alice, "trim", now + 2 * MIN, rng=Dice(*rolls, chance=0.0), plot=0)

    token = harvest(T0, 0)
    assert token["said"]["gift"] == {"kind": "token", "coins": 150} and token["coins"] == 1150      # a Star Capsule's worth
    again = harvest(T0 + HOUR, 0)                                               # not twice in a week: a burst of powder instead
    assert again["said"]["gift"] == {"kind": "burst", "powder": 30} and again["coins"] == 1150
    potion = harvest(T0 + 2 * HOUR, 3)
    assert potion["said"]["gift"] == {"kind": "potion"} and potion["pet"]["potions"] == 1
    _set_pet(conn, alice, potions=lumibattle.POTION_CAP)
    assert harvest(T0 + 3 * HOUR, 3)["said"]["gift"]["kind"] == "burst"         # she can't carry another
    snack = harvest(T0 + 4 * HOUR, 15, 0)
    assert snack["said"]["gift"] == {"kind": "snack", "item": "banana"} and snack["pet"]["inventory"]["banana"] == 1
    assert harvest(T0 + 5 * HOUR, 99)["said"]["gift"]["kind"] == "burst"
    assert harvest(T0 + 8 * 24 * HOUR, 0)["said"]["gift"]["kind"] == "token"    # a week on, the rare one can come again
    assert _g(conn, alice, "plant", T0 + 9 * 24 * HOUR, plot=0, item="moondaisy") and \
        _g(conn, alice, "trim", T0 + 9 * 24 * HOUR + 2 * MIN, plot=0)["said"]["gift"] is None       # and mostly, nothing


# --------------------------------------------------------------------------- cosmetics and moonrise
def test_cosmetics_cost_starpowder_only(conn, alice):
    assert not any(thing["owned"] for thing in _look(conn, alice)["cosmetics"])
    with pytest.raises(lumipet.PetError, match="Not enough starpowder"):
        _g(conn, alice, "cosmetic", item="picket-fence")
    _set(conn, alice, powder=300)
    out = _g(conn, alice, "cosmetic", item="picket-fence")
    assert out["coins"] == 1000 and out["garden"]["powder"] == 0 and out["garden"]["cosmetics"][0]["owned"]
    with pytest.raises(lumipet.PetError, match="something new"):
        _g(conn, alice, "cosmetic", item="picket-fence")


def test_moonrise_replants_one_garden_for_an_upgrade_or_another_garden(conn, alice):
    _look(conn, alice)
    with pytest.raises(lumipet.PetError, match="isn't ready for a moonrise"):
        _g(conn, alice, "moonrise", item="strong-press")
    full = {"plots": [G._empty() for _ in range(16)], "unlocked": LADDER, "given": 100_000}
    _set(conn, alice, garden=full, powder=500, cosmetics=["garden-gnome"], helpers={**dict.fromkeys(G.TREES, 0), "monk": 3})
    assert _look(conn, alice)["moonrise"] == {"needs": 100_000, "ready": True, "count": 0}
    with pytest.raises(lumipet.PetError, match="Choose a permanent upgrade"):
        _g(conn, alice, "moonrise", item="moondaisy")
    out = _g(conn, alice, "moonrise", item="strong-press")["garden"]
    assert len(out["plots"]) == 4 and [f["id"] for f in out["flowers"] if f["unlocked"]] == ["moondaisy"] and out["given"] == 0     # the ladder starts over
    assert out["upgrades"][0]["count"] == 1 and out["helpers"][2]["level"] == 3 and out["cosmetics"][3]["owned"] and out["powder"] == 500
    assert out["moonrise"] == {"needs": 200_000, "ready": False, "count": 1} and out["gardens"] == 1
    _set(conn, alice, garden={**full, "given": 200_000})
    two = _g(conn, alice, "moonrise", item="garden")["garden"]
    assert two["gardens"] == 2 and two["at"] == 0 and len(two["plots"]) == 4
    _set(conn, alice, garden={"unlocked": LADDER[:3]})                          # the first garden climbs again...
    other = _g(conn, alice, "garden", count=1)["garden"]
    assert other["at"] == 1 and [f["id"] for f in other["flowers"] if f["unlocked"]] == ["moondaisy"]      # ...and the new one has to as well
    with pytest.raises(lumipet.PetError, match="hasn't unlocked"):
        _g(conn, alice, "plant", plot=0, item="glowbell")
    with pytest.raises(lumipet.PetError, match="doesn't have that garden"):
        _g(conn, alice, "garden", count=2)
    _set(conn, alice, upgrades={"rich-soil": 6}, garden={**full, "given": 10 ** 9})
    with pytest.raises(lumipet.PetError, match="Choose a permanent upgrade"):
        _g(conn, alice, "moonrise", item="rich-soil")                           # that one is as high as it goes


def test_the_garden_waits_while_lumi_is_exploring(conn, alice):
    with transaction(conn):
        lumipet.dungeon_start(conn, alice, "moonlit-shrine", T0, random.Random(1))
    with pytest.raises(lumipet.PetError, match="out exploring"):
        _g(conn, alice, "press", count=5)


# --------------------------------------------------------------------------- reminders
def _garden_pings(service, phone):          # noqa: F811
    return [need for need in _needs(service, phone) if need == "garden"]


def test_slow_flowers_ready_while_away_are_mentioned_once(conn, settings, alice, phone, service):      # noqa: F811
    _look(conn, alice, NOON)
    _set(conn, alice, garden={"unlocked": LADDER})
    _g(conn, alice, "plant", NOON, plot=0, item="comet-clover")                 # half an hour
    _g(conn, alice, "plant", NOON, plot=1, item="moondaisy")
    _sweep(conn, settings, NOON + 10 * MIN)
    assert _garden_pings(service, phone) == []                                  # a two-minute flower alone is never news
    for minutes in (30, 31, 40, 300):
        _sweep(conn, settings, NOON + minutes * MIN)
    assert _garden_pings(service, phone) == ["garden"]                          # once, however often the worker looks
    assert _saved(conn, alice)["tick"] == NOON                                  # and the worker never runs the press


def test_looking_at_the_garden_or_turning_its_reminders_off_keeps_it_quiet(conn, settings, alice, phone, service):   # noqa: F811
    _look(conn, alice, NOON)
    _set(conn, alice, garden={"unlocked": LADDER})
    _g(conn, alice, "plant", NOON, plot=0, item="comet-clover")
    _g(conn, alice, "look", NOON + 31 * MIN)                                    # she saw them herself
    _sweep(conn, settings, NOON + 32 * MIN)
    assert _garden_pings(service, phone) == []
    _g(conn, alice, "trim", NOON + 32 * MIN, plot=0)
    _g(conn, alice, "plant", NOON + 32 * MIN, plot=0, item="comet-clover")
    with transaction(conn):
        assert lumipet.act(conn, alice, "garden_reminders", now=NOON + 33 * MIN)["pet"]["garden_reminders"] is False
    _sweep(conn, settings, NOON + 70 * MIN)
    assert _garden_pings(service, phone) == []
    with transaction(conn):
        lumipet.act(conn, alice, "garden_reminders", now=NOON + 71 * MIN)
    _sweep(conn, settings, NOON + 72 * MIN)
    assert _garden_pings(service, phone) == ["garden"]


# --------------------------------------------------------------------------- API
@pytest.fixture
def on(client, conn, directory):        # noqa: F811
    _login(client, conn, directory)
    _turn_on(client)
    return client


def test_the_garden_through_the_page_takes_choices_and_never_amounts(on, conn):
    url = "/api/media/games/lumi-pet/garden"
    garden = on.get("/api/media/games/lumi-pet/state").json()["garden"]
    assert len(garden["plots"]) == 4 and garden["powder"] == 0 and garden["water"]["show"] is None
    planted = on.post(url, json={"action": "plant", "plot": 0, "item": "moondaisy"}).json()
    assert planted["said"] == {"planted": "moondaisy"} and planted["garden"]["plots"][0]["stage"] == "growing"
    assert on.post(url, json={"action": "trim", "plot": 0}).status_code == 400  # not ready: refused with words
    assert on.post(url, json={"action": "press", "count": 5}).json()["said"]["coins"] == 0
    for body in ({"action": "press", "count": 5, "powder": 999}, {"action": "harvest"}, {"action": "press", "count": 99999}, {"action": "plant", "plot": 16, "item": "moondaisy"},
                 {"action": "water_answer", "picks": [4]}, {"action": "water_answer", "picks": [0] * 13}, {"action": "trim", "plot": "0"}, {"action": "press", "coins": 5}):
        assert on.post(url, json=body).status_code == 422                       # only choices, never amounts or results
    on.cookies.clear()
    assert on.post(url, json={"action": "look"}).status_code == 401


# --------------------------------------------------------------------------- pace
def test_the_numbers_keep_a_gentle_pace():
    rules, flowers = G.CATALOG["plots"], G.CATALOG["flowers"]
    plots = sum(round(rules["price"] * rules["growth"] ** n) for n in range(rules["max"] - rules["start"]))
    assert plots + sum(flower["coins"] for flower in flowers) < 4000            # a whole garden's coin cost: a couple of weeks, not months
    state = {"coins_today": 0}
    powder, made = 8 * 280 * 4, 0                                               # a casual day: eight Dusk Tulips, trimmed four times
    while powder >= G.price(state):
        powder -= G.price(state)
        state["coins_today"] += 1
        made += 1
    assert 100 <= made <= 250                                                   # about a hundred coins, and the second hundred is slower
    assert 4 * flowers[0]["powder"] * 5 >= flowers[1]["needs"]                  # ten minutes of daisies opens the second flower
    best = 16 * max(f["powder"] / f["minutes"] for f in flowers) * 60 * 24
    assert G.CATALOG["moonrise"]["needs"] < best * 3                            # a first moonrise is days of a full garden, not weeks
