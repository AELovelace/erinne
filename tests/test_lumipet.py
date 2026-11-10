"""Lumi Pet on the server (mnemerynne/lumipet.py): the decay curve, catch-up, care, the pantry and the shop.
The care cases are ported from MommyBot's test/littlepottchi-care.test.js."""

import json

import pytest

from mnemerynne import coins, lumipet, users
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


# --------------------------------------------------------------------------- the house
def test_a_new_house_has_each_spots_free_first_object(conn, alice):
    house = _state(conn, alice, T0)
    assert house["pet"]["house"] == {spot["id"]: spot["items"][0]["id"] for spot in lumipet.HOUSE["spots"]}
    assert len(house["house"]["spots"]) == 10 and all(len(spot["items"]) == 5 for spot in house["house"]["spots"])


def test_decorating_buys_once_then_swaps_for_free_like_an_outfit(conn, alice):
    bought = _act(conn, alice, "decorate", T0, item="rug-moon")
    assert bought["said"] == {"bought": "rug-moon", "count": 1, "paid": 100} and bought["coins"] == 900
    assert bought["pet"]["house"]["rug"] == "rug-moon" and bought["pet"]["decor"] == ["rug-moon"]
    back = _act(conn, alice, "decorate", T0, item="rug-oval")                   # the free first object is always there
    assert back["said"] is None and back["pet"]["house"]["rug"] == "rug-oval" and back["coins"] == 900
    again = _act(conn, alice, "decorate", T0, item="rug-moon")                  # already hers: nothing to pay
    assert again["said"] is None and again["coins"] == 900 and again["pet"]["decor"] == ["rug-moon"]
    row = conn.execute("SELECT amount FROM coin_transactions WHERE type = 'lumi-pet_decor'").fetchall()
    assert [r["amount"] for r in row] == [100]


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
    assert after["coins"] == 50 and after["pet"]["house"]["bed"] == "bed-cushion" and after["pet"]["decor"] == []


def test_a_save_naming_an_object_that_no_longer_exists_falls_back_to_the_free_one(conn, alice):
    _state(conn, alice, T0)
    _set(conn, alice, house={"rug": "rug-retired", "bed": "rug-moon", "attic": "bed-moon"})   # gone, wrong spot, no such spot
    house = _state(conn, alice, T0)["pet"]["house"]
    assert house["rug"] == "rug-oval" and house["bed"] == "bed-cushion" and "attic" not in house


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
    with pytest.raises(lumipet.PetError, match="only comes from the Star Capsule"):
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


def test_the_capsule_never_repeats_and_stops_before_charging_when_she_has_it_all(conn, alice):
    with transaction(conn):
        coins.reward(conn, alice, 9000, "x", 9000)
    everything = sorted(lumipet._WEAR)
    won = []
    for _ in everything:
        with transaction(conn):
            won.append(lumipet.act(conn, alice, "capsule", now=T0, rng=Dice(99, 0))["said"])   # always rolls "legendary"
    assert sorted(w["capsule"] for w in won) == everything                      # 21 capsules, 21 different things
    assert [w["rarity"] for w in won][:2] == ["legendary", "epic"]              # then the nearest rarity with something new
    before = coins.peek(conn, alice)
    with pytest.raises(lumipet.PetError, match="already has everything"):
        _act(conn, alice, "capsule", T0)
    assert coins.peek(conn, alice) == before == 10000 - 150 * len(everything)


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
    broken(lambda d: d["slots"][0]["items"][0].update(rarity="mythic"))
    broken(lambda d: d["slots"][1]["items"].append(dict(d["slots"][0]["items"][0])))
    broken(lambda d: d["slots"][0].update(items=[]))


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
