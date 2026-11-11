"""Lumi Pet on the server (mnemerynne/lumipet.py): the decay curve, catch-up, care, the pantry and the shop.
The care cases are ported from MommyBot's test/littlepottchi-care.test.js."""

import json

import pytest

from mnemerynne import coins, lumibattle, lumipet, users
from mnemerynne.db import transaction
from test_media import (_login, _no_network, _pair, _screens_for, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings)

HOUR = lumipet.HOUR
T0 = 1_800_000_000_000   # a fixed "now" in milliseconds, so no test depends on the real clock


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


def _act(conn, uid, action, now, **kwargs):
    with transaction(conn):
        return lumipet.act(conn, uid, action, now=now, **kwargs)


def _state(conn, uid, now):
    with transaction(conn):
        return lumipet.state(conn, uid, now)


def _set(conn, uid, **fields):
    """Put a pet into a known condition."""
    pet = json.loads(conn.execute("SELECT data FROM lumi_pets WHERE user_id = ?", (uid,)).fetchone()[0])
    pet.update(fields)
    conn.execute("UPDATE lumi_pets SET data = ? WHERE user_id = ?", (json.dumps(pet), uid))


# --------------------------------------------------------------------------- the curve
@pytest.mark.parametrize("meter", list(lumipet.DECAY))
def test_the_last_quarter_of_every_meter_takes_exactly_one_day(meter):
    b = lumipet.DECAY[meter]
    assert lumipet.decayed(25, 24, b) == pytest.approx(0, abs=1e-9)
    assert lumipet.decayed(25, 23.5, b) > 0


def test_the_curve_is_fast_at_the_top_slow_at_the_bottom_and_matches_the_plan():
    hunger, slow = lumipet.DECAY["hunger"], lumipet.DECAY["energy"]
    assert lumipet.decayed(100, 16.9, hunger) == pytest.approx(25, abs=0.2)     # hunger: full to a quarter in about 17 hours
    assert lumipet.decayed(100, 23.4, slow) == pytest.approx(25, abs=0.2)       # energy and joy: about 23 hours
    assert lumipet.decayed(100, 40.9, hunger) == pytest.approx(0, abs=0.2)
    first_hour = 100 - lumipet.decayed(100, 1, hunger)
    last_hour = lumipet.decayed(25, 23, hunger)
    assert first_hour > 10 * last_hour


def test_decay_in_two_steps_equals_decay_in_one():
    """Catching up on every read must not change how fast she runs down."""
    b = lumipet.DECAY["joy"]
    assert lumipet.decayed(lumipet.decayed(90, 3, b), 5, b) == pytest.approx(lumipet.decayed(90, 8, b))
    assert lumipet.decayed(50, 0, b) == 50 and lumipet.decayed(0, 100, b) == 0


# --------------------------------------------------------------------------- catch-up
def test_a_new_pet_starts_content_and_runs_down_while_away(conn, alice):
    first = _state(conn, alice, T0)["pet"]
    assert (first["hunger"], first["energy"], first["joy"], first["moments"]) == (85, 85, 85, 0)
    later = _state(conn, alice, T0 + 12 * HOUR)["pet"]
    assert later["hunger"] < later["energy"] < 85                               # hunger goes fastest
    gone = _state(conn, alice, T0 + 30 * 24 * HOUR)["pet"]
    assert (gone["hunger"], gone["energy"], gone["joy"]) == (0, 0, 0)
    assert gone["moments"] == 0 and gone["toys"] == []                          # sad and sleepy, but nothing is lost


def test_a_clock_that_runs_backwards_changes_nothing(conn, alice):
    _state(conn, alice, T0)
    assert _state(conn, alice, T0 - 5 * HOUR)["pet"]["hunger"] == 85


# --------------------------------------------------------------------------- care
def test_cuddles_add_joy_and_a_moment_with_a_cooldown(conn, alice):
    _state(conn, alice, T0)
    _set(conn, alice, joy=50.0)
    out = _act(conn, alice, "cuddle", T0)["pet"]
    assert out["joy"] == 55 and out["moments"] == 1 and out["memory"] == "cuddle"
    with pytest.raises(lumipet.PetError, match="still enjoying"):
        _act(conn, alice, "cuddle", T0 + 1000)
    assert _act(conn, alice, "cuddle", T0 + lumipet.COOLDOWN)["pet"]["moments"] == 2


def test_free_food_never_runs_out_and_bought_food_is_used_up(conn, alice):
    _state(conn, alice, T0)
    _set(conn, alice, hunger=10.0, joy=10.0)
    fed = _act(conn, alice, "feed", T0, item="apple")["pet"]
    assert fed["hunger"] == 40 and fed["joy"] == 13 and fed["memory"] == "snack"
    with pytest.raises(lumipet.PetError, match="no cookie left"):
        _act(conn, alice, "feed", T0 + lumipet.COOLDOWN, item="cookie")
    bought = _act(conn, alice, "buy", T0, item="cookie", count=2)
    assert bought["said"] == {"bought": "cookie", "count": 2, "paid": 16} and bought["coins"] == 1000 - 16
    eaten = _act(conn, alice, "feed", T0 + lumipet.COOLDOWN, item="cookie")["pet"]
    assert eaten["inventory"] == {"cookie": 1}
    assert _act(conn, alice, "feed", T0 + 2 * lumipet.COOLDOWN, item="cookie")["pet"]["inventory"] == {}
    for wrong in ("star-ribbon", "nothing", None):
        with pytest.raises(lumipet.PetError, match="pantry"):
            _act(conn, alice, "feed", T0 + HOUR, item=wrong)


def test_a_refused_snack_keeps_the_snack(conn, alice):
    _act(conn, alice, "buy", T0, item="cookie")
    _act(conn, alice, "feed", T0, item="apple")
    with pytest.raises(lumipet.PetError):
        _act(conn, alice, "feed", T0 + 1, item="cookie")                        # too soon after the apple
    assert _state(conn, alice, T0 + 2)["pet"]["inventory"] == {"cookie": 1}


def test_treats_leave_a_boost_for_the_next_fight(conn, alice):
    _act(conn, alice, "buy", T0, item="comet-candy")
    assert _act(conn, alice, "feed", T0, item="comet-candy")["pet"]["boost"] == "power"


def test_meters_never_pass_one_hundred(conn, alice):
    _state(conn, alice, T0)
    assert _act(conn, alice, "feed", T0, item="apple")["pet"]["hunger"] == 100


def test_play_is_timed_and_pays_once_at_the_moment_it_finishes(conn, alice):
    _state(conn, alice, T0)
    _set(conn, alice, hunger=60.0, energy=60.0, joy=40.0)
    started = _act(conn, alice, "play", T0, item="star-ribbon")["pet"]
    assert started["task"]["kind"] == "play" and started["task"]["finishesAt"] == T0 + 120_000 and started["moments"] == 0
    with pytest.raises(lumipet.PetError, match="busy playing"):
        _act(conn, alice, "nap", T0 + 1000)
    assert _state(conn, alice, T0 + 119_000)["pet"]["task"] is not None
    done = _state(conn, alice, T0 + 120_000)["pet"]
    assert done["task"] is None and done["moments"] == 1 and done["done"] == {"kind": "play", "item": "star-ribbon", "at": T0 + 120_000}
    assert done["joy"] == pytest.approx(65, abs=0.5) and done["energy"] == pytest.approx(50, abs=0.5) and done["hunger"] == pytest.approx(55, abs=1)
    assert _state(conn, alice, T0 + 121_000)["pet"]["moments"] == 1             # looking again never pays twice


def test_a_reward_earned_while_away_decays_from_when_it_was_earned(conn, alice):
    """Come back a day after a nap: the nap's energy arrived a day ago and has been running down since."""
    _state(conn, alice, T0)
    _set(conn, alice, energy=40.0)
    _act(conn, alice, "nap", T0)
    back = _state(conn, alice, T0 + 24 * HOUR)["pet"]
    expected = lumipet.decayed(lumipet.decayed(40, 5 / 60, 40.0) + lumipet.NAP_ENERGY, 24 - 5 / 60, 40.0)
    assert back["energy"] == pytest.approx(expected, abs=0.1) and back["task"] is None and back["moments"] == 1


def test_a_nap_cut_short_earns_nothing_and_a_wake_keeps_her_up(conn, alice):
    _state(conn, alice, T0)
    _set(conn, alice, energy=40.0)
    napping = _act(conn, alice, "nap", T0)["pet"]
    assert napping["rest"] == "nap" and napping["restUntil"] == T0 + lumipet.NAP_MINUTES * 60_000
    woken = _act(conn, alice, "wake", T0 + 60_000)["pet"]
    assert woken["task"] is None and woken["rest"] == "awake" and woken["restUntil"] == T0 + 60_000 + lumipet.AWAKE_MS
    assert _state(conn, alice, T0 + 10 * 60_000)["pet"]["energy"] < 40


def test_toys_are_bought_once_and_only_owned_toys_can_be_played_with(conn, alice):
    with pytest.raises(lumipet.PetError, match="toy box"):
        _act(conn, alice, "play", T0, item="bouncy-moon")
    assert _act(conn, alice, "buy", T0, item="bouncy-moon", count=5)["coins"] == 850      # a toy is one toy, whatever was asked
    with pytest.raises(lumipet.PetError, match="already has"):
        _act(conn, alice, "buy", T0, item="bouncy-moon")
    assert _act(conn, alice, "play", T0, item="bouncy-moon")["pet"]["task"]["item"] == "bouncy-moon"


# --------------------------------------------------------------------------- the shop
def test_the_shop_refuses_what_cant_be_afforded_and_takes_nothing(conn, alice):
    with transaction(conn):
        coins.held(conn, alice)
        assert coins.spend(conn, alice, 990, "x")
    with pytest.raises(lumipet.PetError, match="Not enough coins"):
        _act(conn, alice, "buy", T0, item="lunch")                              # 12 coins, 10 held
    assert coins.peek(conn, alice) == 10 and _state(conn, alice, T0)["pet"]["inventory"] == {}
    for wrong in ("apple", "star-ribbon", "nothing"):                           # free things and unknown things aren't for sale
        with pytest.raises(lumipet.PetError, match="doesn't sell"):
            _act(conn, alice, "buy", T0, item=wrong)


def test_the_pantry_has_a_limit(conn, alice):
    with transaction(conn):
        coins.reward(conn, alice, 5000, "x", 5000)
    _act(conn, alice, "buy", T0, item="banana", count=99)
    with pytest.raises(lumipet.PetError, match="holds up to 99"):
        _act(conn, alice, "buy", T0, item="banana")


def test_purchases_go_to_the_house_in_the_ledger(conn, alice):
    _act(conn, alice, "buy", T0, item="cookie")
    row = conn.execute("SELECT * FROM coin_transactions WHERE type = 'lumi-pet_buy'").fetchone()
    assert (row["from_account"], row["to_account"], row["amount"], row["game"]) == (f"user:{alice}", coins.HOUSE, 8, "lumi-pet")


def test_spend_refuses_an_overdraft_without_touching_anything(conn, alice):
    with transaction(conn):
        assert not coins.spend(conn, alice, 1001, "x") and not coins.spend(conn, alice, 0, "x")
        assert coins.spend(conn, alice, 1000, "x")
    assert coins.peek(conn, alice) == 0


# --------------------------------------------------------------------------- glow and the old browser save
def test_glow_changes_without_a_moment(conn, alice):
    out = _act(conn, alice, "glow", T0, glow="lagoon")["pet"]
    assert out["glow"] == "lagoon" and out["moments"] == 0
    with pytest.raises(lumipet.PetError):
        _act(conn, alice, "glow", T0, glow="plaid")


def test_browser_memories_come_across_once(conn, alice):
    first = _act(conn, alice, "import", T0, moments=42, glow="starlight")["pet"]
    assert first["moments"] == 42 and first["glow"] == "starlight" and first["imported"]
    assert _act(conn, alice, "import", T0, moments=5000)["pet"]["moments"] == 42


# --------------------------------------------------------------------------- the catalog
def test_the_shipped_catalog_is_valid_and_bad_ones_are_refused(tmp_path):
    good = lumipet.load_catalog()
    assert {"apple", "banana", "cookie", "peanuts", "lunch"} <= {s["id"] for s in good["snacks"]}   # Littlepottchi's pantry

    def broken(change):
        data = json.loads(json.dumps(good))
        change(data)
        path = tmp_path / "items.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            lumipet.load_catalog(path)

    broken(lambda d: d["snacks"].append(dict(d["snacks"][0])))                  # a repeated id
    broken(lambda d: d["snacks"][0].update(fullness=500))
    broken(lambda d: d["treats"][0].update(boost="flight"))
    broken(lambda d: d["toys"][0].update(price=10))                             # no free toy left
    broken(lambda d: d["toys"][1].update(minutes=0))
    broken(lambda d: d.update(snacks=[]))
    broken(lambda d: d["snacks"][1].update(rarity="rare"))                      # only toys can be capsule prizes
    broken(lambda d: d["toys"][-1].update(price=50))                            # a capsule toy can't also be sold
    broken(lambda d: d["toys"][-1].update(rarity="mythic"))
    broken(lambda d: d.update(toys=[t for t in d["toys"] if t["price"] or "rarity" in t]))   # capsule toys don't count as the free one


# --------------------------------------------------------------------------- the house
def test_a_new_house_is_bare_she_has_just_moved_in(conn, alice):
    house = _state(conn, alice, T0)
    assert house["pet"]["house"] == {"curtains": "curtain-none", "view": "view-city", "trophy": "trophy-none"}   # nothing, the outside, an empty bracket
    assert house["pet"]["decor"] == [] and house["boxes"] == {"left": 14, "total": 14, "next": "Mattress on the floor", "ready": True}
    spots = house["house"]["spots"]
    sold = lambda spot: [item for item in spot["items"] if "rarity" not in item and "found" not in item and not item.get("worn") and "prize" not in item]   # noqa: E731
    assert len(spots) == 16 and all(len(sold(spot)) == 5 for spot in spots[:15])                  # 75 pieces to buy, as before
    assert sum(item.get("worn", False) for spot in spots for item in spot["items"]) == 14            # one worn piece in each box
    assert spots[15]["id"] == "trophy" and len(spots[15]["items"]) == 26                           # 24 found in the dungeon, an empty bracket and the Golden Star Scope
    tier_one = {spot["id"]: next(i for i in spot["items"] if not i.get("worn")) for spot in spots if spot["items"][0].get("worn")}
    assert len(tier_one) == 13 and all(item["price"] >= 40 for item in tier_one.values())          # the old free starters are bought now


def test_one_moving_box_a_day_unpacks_her_own_worn_things(conn, alice):
    first = _act(conn, alice, "unpack", T0)
    assert first["said"] == {"unpacked": "bed-worn", "name": "Mattress on the floor", "put_out": True, "left": 13}
    assert first["pet"]["house"]["bed"] == "bed-worn" and first["pet"]["decor"] == ["bed-worn"] and first["boxes"]["ready"] is False
    with pytest.raises(lumipet.PetError, match="Another box tomorrow"):
        _act(conn, alice, "unpack", T0)
    with pytest.raises(lumipet.PetError, match="still packed in a moving box"):
        _act(conn, alice, "decorate", T0, item="lamp-worn")                       # can't be bought, only unpacked
    _act(conn, alice, "decorate", T0, item="lamp-floor")                          # she buys a lamp before its box comes
    conn.execute("UPDATE lumi_pets SET data = json_set(data, '$.box_day', '2000-01-01') WHERE user_id = ?", (alice,))   # the next day
    second = _act(conn, alice, "unpack", T0)
    assert second["said"]["unpacked"] == "lamp-worn" and second["said"]["put_out"] is False and second["pet"]["house"]["lamp"] == "lamp-floor"
    assert _act(conn, alice, "decorate", T0, item="lamp-worn")["pet"]["house"]["lamp"] == "lamp-worn"   # hers now: it swaps in for free
    conn.execute("UPDATE lumi_pets SET data = json_set(data, '$.unpacked', 14) WHERE user_id = ?", (alice,))
    with pytest.raises(lumipet.PetError, match="all moved in"):
        _act(conn, alice, "unpack", T0)


def test_an_older_house_moves_in_afresh_with_its_coins_given_back_once(conn, alice):
    _state(conn, alice, T0)
    capsule = next(p for p in lumipet._PRIZES if "spot" in p and p["rarity"] == "rare")
    found = next(item for item in lumipet._DECOR.values() if item.get("found"))
    old = json.loads(conn.execute("SELECT data FROM lumi_pets WHERE user_id = ?", (alice,)).fetchone()[0])
    for key in ("moved_in", "unpacked", "box_day"):
        old.pop(key)
    old.update(house={"rug": "rug-moon", "bed": "bed-cushion"}, decor=["rug-moon", capsule["id"], found["id"]])
    conn.execute("UPDATE lumi_pets SET data = ? WHERE user_id = ?", (json.dumps(old), alice))
    after = _state(conn, alice, T0)
    worth = lumipet._DECOR["rug-moon"]["price"] + lumipet.CLOSET["values"]["rare"]   # bought at its price, won at its value, found for nothing
    assert after["coins"] == 1000 + worth and after["pet"]["decor"] == [] and "rug" not in after["pet"]["house"] and "bed" not in after["pet"]["house"]
    assert after["pet"]["moved_in"] == lumipet.MOVE_IN and after["boxes"]["left"] == 14
    assert _state(conn, alice, T0)["coins"] == 1000 + worth                          # only once
    rows = conn.execute("SELECT amount FROM coin_transactions WHERE ref = ?", (f"lumi-move-in:{alice}",)).fetchall()
    assert [r["amount"] for r in rows] == [worth]


def test_her_name_and_colour_are_hers_to_choose(conn, alice):
    assert _state(conn, alice, T0)["pet"]["name"] == "Lumi"
    assert _act(conn, alice, "name", T0, name="  Twinkle   Toes ")["pet"]["name"] == "Twinkle Toes"
    assert _act(conn, alice, "name", T0, name="Ünïcørn-2")["pet"]["name"] == "Ünïcørn-2"
    for bad in ("", "   ", "x" * 17, "<b>hi</b>", "...", "a/b"):
        with pytest.raises(lumipet.PetError, match="1 to 16"):
            _act(conn, alice, "name", T0, name=bad)
    assert _act(conn, alice, "colour", T0, colour="#FF8ECB")["pet"]["colour"] == "#ff8ecb"
    for bad in ("red", "#ff8ec", "#ggg000", "ff8ecb"):
        with pytest.raises(lumipet.PetError, match="six hex digits"):
            _act(conn, alice, "colour", T0, colour=bad)
    assert lumipet.look(conn, alice) == {"glow": "rose", "outfit": {}, "colour": "#ff8ecb", "name": "Ünïcørn-2"}
    assert _act(conn, alice, "colour", T0, colour="")["pet"]["colour"] is None      # back to her glow's own colours


def test_stat_points_placed_by_hand_or_by_auto_and_fight_items_bought(conn, alice):
    _state(conn, alice, T0)
    _set(conn, alice, level=3, stat_points={k: 0 for k in lumibattle.STAT_KEYS})
    progress = _state(conn, alice, T0)["progress"]["stat_points"]
    assert (progress["left"], progress["cap"], progress["auto"]) == (14, 6, False)
    before = _state(conn, alice, T0)["progress"]["stats"]["attack"]
    out = _act(conn, alice, "stat", T0, item="attack", count=4)
    assert out["progress"]["stat_points"]["placed"]["attack"] == 4 and out["progress"]["stats"]["attack"] == before + 4   # a point is half a level's growth: 1 attack
    capped = _act(conn, alice, "stat", T0, item="attack", count=9)                  # only up to the cap: 3 a level, 6 at level 3
    assert capped["progress"]["stat_points"]["placed"]["attack"] == 6
    with pytest.raises(lumipet.PetError, match="as many points as her level allows"):
        _act(conn, alice, "stat", T0, item="attack", count=1)
    auto = _act(conn, alice, "stats_auto", T0)
    assert auto["progress"]["stat_points"]["left"] == 0 and sum(auto["said"]["placed"].values()) == 14 - 6
    free = _act(conn, alice, "stats_reset", T0)                                      # no free reset for a new Lumi: it costs
    assert free["said"]["paid"] == lumibattle.STAT_RESET_PRICE and free["progress"]["stat_points"]["left"] == 14
    bought = _act(conn, alice, "kit", T0, item="x-speed", count=2)
    assert bought["progress"]["kit"]["x-speed"] == 2 and bought["said"]["paid"] == 60
    with pytest.raises(lumipet.PetError, match="up to 9"):
        _act(conn, alice, "kit", T0, item="x-speed", count=8)


def test_an_older_lumi_gets_her_points_placed_by_auto_and_one_free_reset(conn, alice):
    _state(conn, alice, T0)
    old = json.loads(conn.execute("SELECT data FROM lumi_pets WHERE user_id = ?", (alice,)).fetchone()[0])
    for key in ("stat_points", "auto_stats", "stat_reset_free", "used", "kit"):
        old.pop(key)
    old.update(level=11, skills=["heavy-paw"])
    conn.execute("UPDATE lumi_pets SET data = ? WHERE user_id = ?", (json.dumps(old), alice))
    after = _state(conn, alice, T0)["progress"]["stat_points"]
    assert after["left"] == 0 and sum(after["placed"].values()) == 70 and after["free_reset"]
    assert after["placed"]["attack"] > after["placed"]["magic"]                     # a Fighter skill: Auto leans on attack
    reset = _act(conn, alice, "stats_reset", T0)
    assert reset["said"]["paid"] == 0 and reset["coins"] == 1000 and not reset["progress"]["stat_points"]["free_reset"]


def test_decorating_buys_once_then_swaps_for_free_like_an_outfit(conn, alice):
    bought = _act(conn, alice, "decorate", T0, item="rug-moon")
    assert bought["said"] == {"bought": "rug-moon", "count": 1, "paid": 100} and bought["coins"] == 900
    assert bought["pet"]["house"]["rug"] == "rug-moon" and bought["pet"]["decor"] == ["rug-moon"]
    back = _act(conn, alice, "decorate", T0, item="rug-oval")                   # the old starter is tier 1 now: bought too
    assert back["said"]["paid"] == 50 and back["pet"]["house"]["rug"] == "rug-oval" and back["coins"] == 850
    again = _act(conn, alice, "decorate", T0, item="rug-moon")                  # already hers: nothing to pay
    assert again["said"] is None and again["coins"] == 850 and again["pet"]["decor"] == ["rug-moon", "rug-oval"]
    row = conn.execute("SELECT amount FROM coin_transactions WHERE type = 'lumi-pet_decor'").fetchall()
    assert [r["amount"] for r in row] == [100, 50]


def test_decorating_refuses_what_isnt_furniture_or_cant_be_afforded(conn, alice):
    for wrong in ("apple", "star-ribbon", "wallpaper", "nothing", None):
        with pytest.raises(lumipet.PetError, match="isn't something for Lumi's house"):
            _act(conn, alice, "decorate", T0, item=wrong)
    with transaction(conn):
        coins.held(conn, alice)
        coins.spend(conn, alice, 950, "x")
    with pytest.raises(lumipet.PetError, match="Not enough coins"):
        _act(conn, alice, "decorate", T0, item="bed-castle")
    after = _state(conn, alice, T0)
    assert after["coins"] == 50 and "bed" not in after["pet"]["house"] and after["pet"]["decor"] == []   # still bare


def test_a_save_naming_an_object_that_no_longer_exists_falls_back_to_the_free_one(conn, alice):
    _state(conn, alice, T0)
    _set(conn, alice, house={"rug": "rug-retired", "bed": "rug-moon", "attic": "bed-moon"})   # gone, wrong spot, no such spot
    house = _state(conn, alice, T0)["pet"]["house"]
    assert "rug" not in house and "bed" not in house and "attic" not in house       # nothing valid there: bare


def test_the_shipped_house_is_valid_and_bad_ones_are_refused(tmp_path):
    good = lumipet.load_house()

    def broken(change):
        data = json.loads(json.dumps(good))
        change(data)
        path = tmp_path / "house.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            lumipet.load_house(path)

    broken(lambda d: d["spots"][0]["items"][0].update(price=5))                 # a spot with nothing free to start with
    broken(lambda d: d["spots"][1]["items"].append(dict(d["spots"][0]["items"][1])))   # the same object in two spots
    broken(lambda d: d["spots"][0].update(items=[]))
    broken(lambda d: d["spots"].append(dict(d["spots"][0])))
    broken(lambda d: d.update(spots=[]))
    broken(lambda d: d["spots"][0]["items"][0].update(rarity="common"))         # a house's free first object can't be a prize
    broken(lambda d: d["spots"][0]["items"][1].update(rarity="common"))         # this one is sold, and a prize can't be
    broken(lambda d: d["spots"][0]["items"][-1].update(rarity="mythic"))
    soft = lambda d: next(item for item in d["spots"][0]["items"] if "prize" in item)                # noqa: E731  the Lumi-Soft wallpaper
    broken(lambda d: soft(d).update(price=50))                                  # a game prize can't also be sold
    broken(lambda d: soft(d).pop("how"))                                        # it has to say how to win it
    broken(lambda d: soft(d).update(how="  "))
    broken(lambda d: soft(d).update(prize="lumi-golf"))                         # only Lumi-Soft gives prizes
    broken(lambda d: soft(d).update(rarity="common"))                           # and no capsule can roll it
    broken(lambda d: soft(d).update(found="moonlit-shrine"))
    broken(lambda d: d["spots"][0]["items"][0].update(prize="lumi-soft", how="Win"))   # a spot's free first object can't be a prize


def test_lumi_soft_decor_is_refused_until_the_game_grants_it_then_swaps_freely(conn, alice):
    piece = lumipet._DECOR["soft-rug"]
    assert piece["prize"] == "lumi-soft" and piece["price"] == 0 and piece not in lumipet._PRIZES   # never in a capsule
    with pytest.raises(lumipet.PetError) as refused:
        _act(conn, alice, "decorate", T0, item="soft-rug")
    assert str(refused.value) == "That's a Lumi-Soft prize: Finish the Lumi-Soft Training Range."
    assert _state(conn, alice, T0)["coins"] == 1000                             # nothing was charged
    _state(conn, alice, T0)                                                     # her save exists (the refusal rolled back)
    _set(conn, alice, decor=["soft-rug"])                                       # what lumisoft.py does when it is won
    placed = _act(conn, alice, "decorate", T0, item="soft-rug")
    assert placed["pet"]["house"]["rug"] == "soft-rug" and placed["said"] is None and placed["coins"] == 1000


def test_every_spot_has_one_lumi_soft_prize_and_none_is_ever_sold_or_rolled():
    for spot in lumipet.HOUSE["spots"]:
        prizes = [item for item in spot["items"] if "prize" in item]
        assert [item["id"] for item in prizes] == ["soft-" + spot["id"]] and prizes[0]["how"], spot["id"]
    assert all("prize" not in prize for prize in lumipet._PRIZES)


# --------------------------------------------------------------------------- the closet and the Star Capsule
class Dice:
    """A scripted dice for the capsule: randrange gives the listed numbers in turn."""

    def __init__(self, *rolls):
        self.rolls = list(rolls)

    def randrange(self, n):
        return min(self.rolls.pop(0), n - 1)


def test_accessories_are_bought_once_then_put_on_and_taken_off_freely(conn, alice):
    bought = _act(conn, alice, "wear", T0, item="acc-bow")
    assert bought["said"] == {"bought": "acc-bow", "count": 1, "paid": 60} and bought["coins"] == 940
    assert bought["pet"]["outfit"] == {"head": "acc-bow"} and bought["pet"]["closet"] == ["acc-bow"]
    off = _act(conn, alice, "wear", T0, item="acc-bow")
    assert off["pet"]["outfit"] == {} and off["coins"] == 940 and off["said"] is None
    _act(conn, alice, "wear", T0, item="acc-bow")
    swapped = _act(conn, alice, "wear", T0, item="acc-star-clip")              # one accessory per slot: the clip replaces the bow
    assert swapped["pet"]["outfit"] == {"head": "acc-star-clip"} and swapped["coins"] == 880
    both = _act(conn, alice, "wear", T0, item="acc-socks")["pet"]["outfit"]
    assert both == {"head": "acc-star-clip", "paws": "acc-socks"}
    assert conn.execute("SELECT COUNT(*) FROM coin_transactions WHERE type = 'lumi-pet_wear'").fetchone()[0] == 3


def test_capsule_only_accessories_cant_be_bought_and_other_things_cant_be_worn(conn, alice):
    with pytest.raises(lumipet.PetError, match="only comes from a capsule"):
        _act(conn, alice, "wear", T0, item="acc-crown")
    for wrong in ("apple", "rug-moon", "head", "nothing", None):
        with pytest.raises(lumipet.PetError, match="isn't something Lumi can wear"):
            _act(conn, alice, "wear", T0, item=wrong)
    assert _state(conn, alice, T0)["coins"] == 1000


def test_the_capsule_rolls_a_rarity_first_then_something_new_of_it(conn, alice):
    odds = lumipet.CLOSET["capsule"]["odds"]
    with transaction(conn):
        coins.reward(conn, alice, 2000, "x", 2000)
    assert list(odds.values()) == [55, 25, 14, 5, 1]                            # MommyBot's gacha tiers
    for roll, rarity in ((0, "common"), (54, "common"), (55, "uncommon"), (79, "uncommon"), (80, "rare"), (93, "rare"), (94, "epic"), (98, "epic"), (99, "legendary")):
        conn.execute("DELETE FROM lumi_pets")
        with transaction(conn):
            out = lumipet.act(conn, alice, "capsule", now=T0, rng=Dice(roll, 0))
        won = out["said"]
        assert won["rarity"] == rarity and lumipet._WEAR[won["capsule"]]["rarity"] == rarity and won["paid"] == 150
        assert out["pet"]["closet"] == [won["capsule"]] and out["pet"]["outfit"] == {lumipet._WEAR[won["capsule"]]["slot"]: won["capsule"]}


def test_a_repeat_pays_back_half_its_value_and_changes_nothing_she_has(conn, alice):
    with transaction(conn):
        coins.reward(conn, alice, 9000, "x", 9000)
    def open_one(roll):
        with transaction(conn):
            return lumipet.act(conn, alice, "capsule", now=T0, rng=Dice(roll, 0))
    first = open_one(99)                                                        # a legendary with no shop price: worth its rarity's value
    assert first["said"] == {"capsule": "acc-crown", "name": "Starlight crown", "rarity": "legendary", "paid": 150}
    again = open_one(99)
    assert again["said"]["duplicate"] is True and again["said"]["buyback"] == 500
    assert again["coins"] == first["coins"] - 150 + 500
    assert again["pet"]["closet"] == ["acc-crown"] and again["pet"]["outfit"] == {"head": "acc-crown"}
    _set(conn, alice, outfit={})
    assert open_one(99)["pet"]["outfit"] == {}                                  # a repeat doesn't put anything on her
    open_one(0)
    assert open_one(0)["said"]["buyback"] == 30                                 # the ribbon bow is sold for 60, so its shop price is its value
    assert conn.execute("SELECT COUNT(*) FROM coin_transactions WHERE type = 'lumi-pet_buyback'").fetchone()[0] == 3


def test_a_capsule_stops_before_charging_only_when_all_it_could_give_is_hers(conn, alice):
    _state(conn, alice, T0)
    has = lambda rarities: {shelf: [p["id"] for p in lumipet._PRIZES if lumipet._shelf(p) == shelf and p["rarity"] in rarities]
                            for shelf in ("closet", "toys", "decor")}
    _set(conn, alice, **has(("rare", "epic", "legendary")))                     # all the Moon Capsule holds, but no commons
    before = coins.peek(conn, alice)
    with pytest.raises(lumipet.PetError, match="everything the Moon Capsule holds"):
        _act(conn, alice, "capsule", T0, item="moon")
    assert coins.peek(conn, alice) == before
    assert _act(conn, alice, "capsule", T0, rng=Dice(0, 0))["said"]["rarity"] == "common"   # the Star Capsule still has something new
    _set(conn, alice, **has(lumipet.RARITIES))
    with pytest.raises(lumipet.PetError, match="everything the Star Capsule holds"):
        _act(conn, alice, "capsule", T0)


def test_the_moon_capsule_costs_more_and_only_holds_the_rare_end(conn, alice):
    with transaction(conn):
        coins.reward(conn, alice, 90000, "x", 90000)
    assert lumipet.CLOSET["moon"]["price"] == 300
    for roll in range(100):
        conn.execute("DELETE FROM lumi_pets")
        with transaction(conn):
            said = lumipet.act(conn, alice, "capsule", now=T0, item="moon", rng=Dice(roll, 0))["said"]
        assert said["paid"] == 300
        assert said["rarity"] == ("rare" if roll < 70 else "epic" if roll < 95 else "legendary")
    with pytest.raises(lumipet.PetError, match="doesn't know that capsule"):
        _act(conn, alice, "capsule", T0, item="sun")


def test_every_place_has_at_least_ten_capsule_prizes():
    for slot in lumipet.CLOSET["slots"]:
        assert len(slot["items"]) >= 10, slot["id"]
    for spot in lumipet.HOUSE["spots"]:
        if spot["id"] != "trophy":                                              # trophies are found in the dungeon instead
            assert sum("rarity" in item for item in spot["items"]) >= 10, spot["id"]
    assert sum("rarity" in toy for toy in lumipet.CATALOG["toys"]) >= 10


def test_a_capsule_toy_cant_be_played_with_until_it_is_won_then_waits_in_her_toy_box(conn, alice):
    toy = next(p for p in lumipet._PRIZES if lumipet._shelf(p) == "toys" and p["rarity"] == "legendary")
    assert toy["id"] == "wishing-star"
    with pytest.raises(lumipet.PetError, match="toy box"):
        _act(conn, alice, "play", T0, item=toy["id"])
    with pytest.raises(lumipet.PetError, match="doesn't sell"):
        _act(conn, alice, "buy", T0, item=toy["id"])
    with transaction(conn):
        coins.reward(conn, alice, 9000, "x", 9000)
    legendaries = [p for p in lumipet._PRIZES if p["rarity"] == "legendary"]
    won = _act(conn, alice, "capsule", T0, rng=Dice(99, legendaries.index(toy)))
    assert won["said"]["capsule"] == toy["id"] and won["pet"]["toys"] == [toy["id"]] and won["pet"]["outfit"] == {}
    played = _act(conn, alice, "play", T0, item=toy["id"])
    assert played["pet"]["task"]["kind"] == "play" and played["pet"]["task"]["item"] == toy["id"]
    again = _act(conn, alice, "capsule", T0, rng=Dice(99, legendaries.index(toy)))
    assert again["said"]["buyback"] == 500 and again["pet"]["toys"] == [toy["id"]]


def test_capsule_decor_cant_be_bought_and_a_win_goes_straight_into_the_room(conn, alice):
    locked = next(p for p in lumipet._PRIZES if "spot" in p)
    with pytest.raises(lumipet.PetError, match="only comes from a capsule"):
        _act(conn, alice, "decorate", T0, item=locked["id"])
    assert _state(conn, alice, T0)["coins"] == 1000
    out = _act(conn, alice, "capsule", T0, rng=Dice(0, 10_000))                 # a common, the last in the list: clothes come first, so it's decor
    won = lumipet._DECOR[out["said"]["capsule"]]
    assert out["pet"]["decor"] == [won["id"]] and out["pet"]["house"][won["spot"]] == won["id"] and out["pet"]["closet"] == []
    spot = next(spot for spot in lumipet.HOUSE["spots"] if spot["id"] == won["spot"])
    other = next(item for item in spot["items"] if item["price"])["id"]           # something bought, to swap with
    bought = _act(conn, alice, "decorate", T0, item=other)
    assert bought["pet"]["house"][won["spot"]] == other
    back = _act(conn, alice, "decorate", T0, item=won["id"])
    assert back["pet"]["house"][won["spot"]] == won["id"] and back["said"] is None and back["coins"] == bought["coins"]   # hers now, so it swaps freely
    free = other
    _act(conn, alice, "decorate", T0, item=free)
    repeat = _act(conn, alice, "capsule", T0, rng=Dice(0, 10_000))
    assert repeat["said"]["duplicate"] is True and repeat["said"]["buyback"] == 30
    assert repeat["pet"]["house"][won["spot"]] == free and repeat["pet"]["decor"] == [won["id"], other]   # a repeat doesn't rearrange the room


def test_repeats_never_pay_back_as_much_as_the_capsule_costs(conn):
    # Even a player who owns everything must lose coins on average, or opening capsules would print money.
    for kind in (lumipet.CLOSET["capsule"], lumipet.CLOSET["moon"]):
        back = 0
        for rarity, chance in kind["odds"].items():
            prizes = [p for p in lumipet._PRIZES if p["rarity"] == rarity]
            assert prizes, f"{kind['name']} would roll {rarity} with nothing to give"
            back += chance / 100 * sum((p["price"] or lumipet.CLOSET["values"][rarity]) // 2 for p in prizes) / len(prizes)
        assert back < kind["price"] * .6


def test_the_capsule_needs_its_price(conn, alice):
    with transaction(conn):
        coins.held(conn, alice)
        coins.spend(conn, alice, 900, "x")
    with pytest.raises(lumipet.PetError, match="Not enough coins"):
        _act(conn, alice, "capsule", T0)
    assert _state(conn, alice, T0)["pet"]["closet"] == []


def test_a_retired_accessory_is_dropped_from_the_outfit_shown(conn, alice):
    _state(conn, alice, T0)
    _set(conn, alice, outfit={"head": "acc-retired", "neck": "acc-bow", "paws": "acc-socks"})   # gone; wrong slot; fine
    assert _state(conn, alice, T0)["pet"]["outfit"] == {"paws": "acc-socks"}


def test_the_shipped_closet_is_valid_and_bad_ones_are_refused(tmp_path):
    good = lumipet.load_closet()
    assert [slot["id"] for slot in good["slots"]] == ["head", "neck", "wings", "back", "paws", "tail"]

    def broken(change):
        data = json.loads(json.dumps(good))
        change(data)
        path = tmp_path / "closet.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            lumipet.load_closet(path)

    broken(lambda d: d["capsule"]["odds"].update(common=50))                    # no longer adds up to 100
    broken(lambda d: d["capsule"].update(price=0))
    broken(lambda d: d["moon"]["odds"].update(rare=60))                         # no longer adds up to 100
    broken(lambda d: d["moon"].update(price=0))
    broken(lambda d: d["moon"]["odds"].update(mythic=1))
    broken(lambda d: d["values"].pop("epic"))
    broken(lambda d: d["slots"][0]["items"][0].update(rarity="mythic"))
    broken(lambda d: d["slots"][1]["items"].append(dict(d["slots"][0]["items"][0])))
    broken(lambda d: d["slots"][0].update(items=[]))
    goggles = lambda d: next(item for item in d["slots"][0]["items"] if "prize" in item)              # noqa: E731
    broken(lambda d: goggles(d).update(price=60))                               # a game prize can't also be sold
    broken(lambda d: goggles(d).pop("how"))
    broken(lambda d: goggles(d).update(rarity="rare"))                          # or rolled from a capsule
    broken(lambda d: goggles(d).pop("prize"))                                   # without a prize it needs a rarity


def test_the_lumi_soft_goggles_are_refused_until_the_game_grants_them_then_worn_freely(conn, alice):
    assert lumipet._WEAR["acc-soft-goggles"] not in lumipet._PRIZES             # no capsule can give them
    with pytest.raises(lumipet.PetError) as refused:
        _act(conn, alice, "wear", T0, item="acc-soft-goggles")
    assert str(refused.value) == "That's a Lumi-Soft prize: Clear Lumi-Soft's Story mode."
    _state(conn, alice, T0)                                                     # her save exists (the refusal rolled back)
    _set(conn, alice, closet=["acc-soft-goggles"])                              # what lumisoft.py does when they are won
    worn = _act(conn, alice, "wear", T0, item="acc-soft-goggles")
    assert worn["pet"]["outfit"] == {"head": "acc-soft-goggles"} and worn["said"] is None and worn["coins"] == 1000
    assert _act(conn, alice, "wear", T0, item="acc-soft-goggles")["pet"]["outfit"] == {}    # and off again


# --------------------------------------------------------------------------- API
@pytest.fixture
def on(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    return client


def test_the_page_reads_the_pet_and_acts_on_it(on):
    state = on.get("/api/media/games/lumi-pet/state").json()
    assert state["pet"]["hunger"] == 85 and state["coins"] == 1000 and state["catalog"]["snacks"][0]["id"] == "apple"
    fed = on.post("/api/media/games/lumi-pet/act", json={"action": "feed", "item": "apple"})
    assert fed.status_code == 200 and fed.json()["pet"]["moments"] == 1
    again = on.post("/api/media/games/lumi-pet/act", json={"action": "feed", "item": "apple"})
    assert again.status_code == 400 and "still enjoying" in again.json()["detail"]


def test_the_page_cant_name_a_result(on):
    for body in ({"action": "feed", "item": "apple", "hunger": 100}, {"action": "teleport"}, {"action": "buy", "item": "cookie", "count": 0},
                 {"action": "buy", "item": "cookie", "price": 0}, {"action": "import", "moments": 10 ** 9}):
        assert on.post("/api/media/games/lumi-pet/act", json=body).status_code == 422
    assert on.get("/api/media/games/coins").json() == {"coins": 1000}


def test_a_pet_needs_a_signed_in_person(on):
    client = on
    client.cookies.clear()                                                      # Media stays on; the person has gone
    assert client.get("/api/media/games/lumi-pet/state").status_code == 401
    assert client.post("/api/media/games/lumi-pet/act", json={"action": "cuddle"}).status_code == 401
