"""Lumi City (mnemerynne/lumicity.py, docs/plans/lumi-city.md, 2026-11-19): the map and the estate catalog are checked
at start; flats in Lumi Heights are numbered by account; plots are bought from the office (one per account), listed
and bought between players, or sold back; a full town grows a block at its gate; house styles, yard pieces and the
Keepsake Capsule; the house on a plot is a second home with its own rooms and its own folk."""

import copy
import json

import pytest
from starlette.testclient import TestClient

from mnemerynne import coins, db, events, lumicity, lumihouse, lumipet, lumitown, users
from mnemerynne.db import transaction
from test_media import _login, _no_network, _turn_on, checker, client, conn, directory, root, settings  # noqa: F401

API = "/api/media/games/lumi-pet/"
CITY_JSON = json.loads((lumicity.Path(lumicity.__file__).parent / "lumipet_data" / "city.json").read_text(encoding="utf-8"))
ESTATE_JSON = json.loads((lumicity.Path(lumicity.__file__).parent / "lumipet_data" / "estate.json").read_text(encoding="utf-8"))


class Dice:
    """A scripted dice for the capsule: randrange gives the listed numbers in turn (as in test_lumipet)."""

    def __init__(self, *rolls):
        self.rolls = list(rolls)

    def randrange(self, n):
        return min(self.rolls.pop(0), n - 1)


@pytest.fixture
def alice(conn, games_conn):
    return users.create_user(conn, "alice", "password123")


@pytest.fixture
def bob(conn, games_conn):
    return users.create_user(conn, "bob", "password123")


def _rich(games, uid, amount=50_000):
    """Give someone plenty of coins (plots cost more than a new purse holds)."""
    with transaction(games):
        coins.prize(games, uid, amount, "test", f"test-rich:{uid}:{amount}")


def _act(games, uid, action, **kwargs):
    with transaction(games):
        return lumicity.act(games, uid, action, **kwargs)


def _row(games, plot):
    town, block, lot = lumicity.parse(plot)
    return games.execute("SELECT * FROM lumi_plots WHERE town = ? AND block = ? AND lot = ?", (town, block, lot)).fetchone()


def _save(games, uid):
    return json.loads(games.execute("SELECT data FROM lumi_pets WHERE user_id = ?", (uid,)).fetchone()[0])


def _set(games, uid, **fields):
    """Put a pet save into a known condition."""
    pet = _save(games, uid)
    pet.update(fields)
    games.execute("UPDATE lumi_pets SET data = ? WHERE user_id = ?", (json.dumps(pet), uid))


# --------------------------------------------------------------------------- the map and the catalog
def test_the_shipped_city_and_estate_are_valid():
    assert lumicity.load_city()["start_blocks"] >= 1
    estate = lumicity.load_estate()
    assert len(estate["styles"]) == 30 and estate["styles"][0]["price"] == 0 and lumicity.STARTER == estate["styles"][0]["id"]
    assert [s["id"] for s in estate["slots"]] == ["fence", "mailbox", "path", "lamp", "ornament"]
    for slot in lumicity.SLOTS:
        pieces = estate["yard"][slot]
        assert len(pieces) == 15 and sum(1 for p in pieces if p["price"]) == 5               # 5 sold
        assert sum(1 for p in pieces if "rarity" in p and not p["price"]) == 10               # 10 from the capsule only
    assert len(lumicity._KEEPSAKES) == 50


def _broken_city(tmp_path, change):
    data = copy.deepcopy(CITY_JSON)
    change(data)
    path = tmp_path / "city.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        lumicity.load_city(path)


def _row_set(rows, y, x, ch):
    rows[y] = rows[y][:x] + ch + rows[y][x + 1:]


@pytest.mark.parametrize("change", [
    lambda d: d["template"]["rows"].__setitem__(0, d["template"]["rows"][0] + "T"),          # a ragged row
    lambda d: d["template"]["rows"].pop(),                                                     # too few rows
    lambda d: _row_set(d["template"]["rows"], 0, 0, "?"),                                      # an unknown symbol
    lambda d: _row_set(d["template"]["rows"], 4, 2, "~"),                                      # lot a isn't one solid rectangle
    lambda d: _row_set(d["template"]["rows"], 0, 0, "a"),                                      # ...or is in two pieces
    lambda d: _row_set(d["template"]["rows"], 6, 3, "~"),                                      # lot a's door opens onto grass
    lambda d: _row_set(d["downtown"]["rows"], 6, 3, "T"),                                      # Lumi Heights West's door is blocked
    lambda d: _row_set(d["template"]["rows"], 7, 5, "~"),                                      # the avenue is broken in the block
    lambda d: _row_set(d["gate"]["rows"], 8, 1, "T"),                                          # ...or at the gate
    lambda d: d["gate"].update(arrive=[0, 0]),                                                 # arriving in the trees
    lambda d: d["downtown"]["rows"].__setitem__(9, "TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT") or d["downtown"]["rows"].__setitem__(15, "TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT"),   # the square cut off
    lambda d: d["towns"]["left"].update(lanes=[]),                                             # no lane names
    lambda d: d["towns"]["right"].update(district="x"),                                        # a district the pages can't send
    lambda d: d["price"].pop("decay"),                                                         # a price key missing
    lambda d: d["price"].update(list_min=2),                                                   # a listing range upside down
    lambda d: d["template"]["lots"].pop(),                                                     # only seven lots
    lambda d: d["downtown"]["places"]["A"].update(id="Arcade!"),                               # a place id pages can't send
    lambda d: d.update(start_blocks=0),
], ids=["ragged", "short", "unknown symbol", "lot not a rectangle", "lot in two pieces", "lot door on grass",
        "place door blocked", "avenue broken", "gate avenue broken", "arrive in trees",
        "square cut off", "no lanes", "bad district", "price key missing", "list range", "seven lots", "bad place id",
        "no start blocks"])
def test_a_bad_city_is_refused(tmp_path, change):
    _broken_city(tmp_path, change)


def test_a_bad_estate_is_refused(tmp_path):
    def broken(change):
        data = copy.deepcopy(ESTATE_JSON)
        change(data)
        path = tmp_path / "estate.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            lumicity.load_estate(path)

    broken(lambda d: d["styles"].pop())                                                # 29 styles
    broken(lambda d: d["styles"][0].update(price=10))                                  # the starter isn't free
    broken(lambda d: d["styles"][5].update(price=0))                                   # two free styles
    broken(lambda d: d["styles"][1].update(id=d["styles"][2]["id"]))                    # a repeated id
    broken(lambda d: d["yard"]["lamp"][0].update(id=d["styles"][3]["id"]))              # ...even across styles and yard
    broken(lambda d: d["styles"][1].update(id="Bad Id"))                                # an id the routes can't send
    broken(lambda d: d["slots"].reverse())                                             # slots out of order
    broken(lambda d: d["yard"]["fence"].pop())                                         # 14 pieces
    broken(lambda d: d["yard"]["path"][-1].update(price=10))                           # a capsule piece sold too
    broken(lambda d: d["yard"]["path"][0].update(price=0))                             # a free piece with no rarity
    broken(lambda d: d["capsule"]["odds"].update(common=50))                           # odds that don't add up to 100
    broken(lambda d: d["values"].pop("epic"))                                          # a rarity with no value
    broken(lambda d: d["styles"][4].pop("hint"))


def test_capsule_yard_pieces_never_join_lumi_pets_capsules_or_her_refund(games_conn, alice):
    ours = [*lumipet._WEAR.values(), *lumipet._ITEMS.values(), *lumipet._DECOR.values()]   # Lumi Pet's own catalogs
    assert all(any(prize is piece for piece in ours) for prize in lumipet._PRIZES)     # the Star and Moon pools hold only those
    assert not any(prize is piece for prize in lumipet._PRIZES for piece in lumicity._YARD.values())
    assert all(prize.get("slot") not in lumicity.SLOTS or "spot" in prize for prize in lumipet._PRIZES)
    _rich(games_conn, alice)
    _act(games_conn, alice, "capsule", rng=Dice(0, 0))                                 # a Keepsake piece goes to her estate...
    save = _save(games_conn, alice)
    assert save["estate"]["yard"] == [lumicity._KEEPSAKES[0]["id"]] and save["decor"] == []   # ...never to her furniture (the move-in refund's list)


# --------------------------------------------------------------------------- numbers
def test_flats_are_numbered_by_account_and_never_move():
    assert lumicity.apartment(1) == {"floor": 1, "door": 1, "label": "1A"}
    assert lumicity.apartment(8) == {"floor": 1, "door": 8, "label": "1H"}
    assert lumicity.apartment(9) == {"floor": 2, "door": 1, "label": "2A"}
    assert lumicity.apartment(12) == {"floor": 2, "door": 4, "label": "2D"}
    assert lumicity.apartment(801)["label"] == "101A"


def test_suggested_prices_fall_away_from_downtown():
    assert lumicity.suggested("left", 1, 1) == 6000 and lumicity.suggested("right", 1, 2) == 6000   # block 1 on the avenue
    assert lumicity.suggested("left", 1, 3) == 5000                                    # block 1 on a lane
    assert lumicity.suggested("left", 2, 3) == 4400 and lumicity.suggested("left", 2, 1) == 5300   # x0.88, then x1.2, to the 50
    assert lumicity.suggested("left", 3, 5) == 3850                                    # 3872 rounds to the nearest 50
    assert lumicity.suggested("right", 11, 4) == 1500 == lumicity.suggested("left", 40, 1)   # never below the floor
    assert all(lumicity.suggested("left", b, 3) >= lumicity.suggested("left", b + 1, 3) for b in range(1, 30))


def test_lot_ids_and_street_names():
    assert lumicity.lots({"left": 1, "right": 2})[:2] == ["l-1-1", "l-1-2"] and len(lumicity.lots({"left": 1, "right": 2})) == 24
    assert lumicity.parse("r-12-8") == ("right", 12, 8)
    for bad in ("x-1-1", "l-0-1", "l-1-9", "l-1", None):
        with pytest.raises(lumipet.PetError):
            lumicity.parse(bad)
    assert lumicity.street("left", 1) == "Daisy Lane" and lumicity.street("right", 2) == "Nova Street"
    assert lumicity.street("left", 21) == "Daisy Lane II" and lumicity.street("left", 42) == "Tulip Lane III"   # round again


# --------------------------------------------------------------------------- buying and selling
def test_buying_from_the_office(games_conn, alice):
    _rich(games_conn, alice)
    before = coins.peek(games_conn, alice)
    out = _act(games_conn, alice, "buy", plot="l-1-3")
    assert out["said"]["paid"] == 5000 and out["said"]["bought"] == "l-1-3" and "Daisy Lane" in out["said"]["text"]
    assert coins.peek(games_conn, alice) == before - 5000
    row = _row(games_conn, "l-1-3")
    assert (row["user_id"], row["style"], row["yard"], row["paid"], row["list_price"], row["version"]) == (alice, lumicity.STARTER, "{}", 5000, None, 1)
    save = _save(games_conn, alice)
    assert save["home_town"] == "left" and save["estate"]["styles"] == [lumicity.STARTER]   # her home town, and the starter house
    assert out["me"]["plot"] == "l-1-3" and out["me"]["home_town"] == "left"
    lot = next(l for l in out["lots"] if l["id"] == "l-1-3")
    assert lot["owner"] == {"id": alice, "name": "alice"} and lot["style"] == lumicity.STARTER and lot["suggested"] == 5000
    assert set(lot["yard"]) == set(lumicity.SLOTS) and not any(lot["yard"].values())
    office = next(l for l in out["lots"] if l["id"] == "l-1-4")
    assert (office["owner"], office["style"], office["list_price"], office["version"]) == (None, "", None, None)   # office land
    ledger = games_conn.execute("SELECT * FROM coin_transactions WHERE type = 'lumi-city_plot'").fetchall()
    assert [(r["from_account"], r["to_account"], r["amount"]) for r in ledger] == [(f"user:{alice}", coins.HOUSE, 5000)]


def test_one_plot_each_and_a_taken_lot_is_refused(games_conn, alice, bob):
    _rich(games_conn, alice)
    _rich(games_conn, bob)
    _act(games_conn, alice, "buy", plot="r-1-1")
    with pytest.raises(lumipet.PetError, match="already got a home on Comet Street"):
        _act(games_conn, alice, "buy", plot="r-1-2")
    with pytest.raises(lumipet.PetError, match="Someone already lives there"):
        _act(games_conn, bob, "buy", plot="r-1-1")
    with pytest.raises(lumipet.PetError, match="already mine"):
        _act(games_conn, alice, "buy", plot="r-1-1")
    with pytest.raises(lumipet.PetError, match="isn't built yet"):
        _act(games_conn, bob, "buy", plot="r-3-1")                                     # two blocks to start with
    assert _act(games_conn, bob, "buy", plot="r-2-1")["me"]["home_town"] == "right"


def test_someone_who_cant_afford_it_changes_nothing(games_conn, alice):
    with pytest.raises(lumipet.PetError, match="can't afford that yet! It's 6000"):
        _act(games_conn, alice, "buy", plot="l-1-1")                                   # a new purse holds 1000
    assert _row(games_conn, "l-1-1") is None and coins.peek(games_conn, alice) == coins.START
    assert games_conn.execute("SELECT count(*) FROM lumi_pets WHERE user_id = ?", (alice,)).fetchone()[0] == 0   # nothing at all


def test_a_full_town_grows_a_block_at_its_gate_and_keeps_every_lot(games_conn, alice, monkeypatch):
    monkeypatch.setitem(lumicity.CITY, "start_blocks", 1)
    _rich(games_conn, alice)
    with transaction(games_conn):
        assert lumicity.towns(games_conn) == {"left": 1, "right": 1}
        for lot in range(1, 8):                                                        # seven lots owned by neighbours
            games_conn.execute("INSERT INTO lumi_plots(town, block, lot, user_id, bought_at) VALUES ('left', 1, ?, ?, 0)", (lot, 9000 + lot))
    before = lumicity.lots({"left": 1, "right": 1})
    out = _act(games_conn, alice, "buy", plot="l-1-8")                                 # the last office lot in LefTown
    assert out["said"]["expanded"] is True and out["towns"] == {"left": 2, "right": 1}
    ids = [lot["id"] for lot in out["lots"]]
    assert ids[:8] == before[:8] and ids[8:16] == [f"l-2-{n}" for n in range(1, 9)] and ids[16:] == before[8:]   # nothing renumbered
    assert next(l for l in out["lots"] if l["id"] == "l-1-1")["owner"]["name"] == "a neighbour"   # (an account that's gone quiet)
    assert _act(games_conn, alice, "home_town", item="left")["towns"] == {"left": 2, "right": 1}   # no growth without a sale


def test_the_town_only_grows_when_every_lot_is_owned(games_conn, alice, monkeypatch):
    monkeypatch.setitem(lumicity.CITY, "start_blocks", 1)
    _rich(games_conn, alice)
    out = _act(games_conn, alice, "buy", plot="l-1-8")
    assert out["said"]["expanded"] is False and out["towns"] == {"left": 1, "right": 1}


def test_listing_is_kept_within_its_range(games_conn, alice):
    _rich(games_conn, alice)
    with pytest.raises(lumipet.PetError, match="haven't got a plot"):
        _act(games_conn, alice, "list", price=6000)
    _act(games_conn, alice, "buy", plot="l-1-1")                                       # suggested 6000: 3000 to 18000
    for bad in (2999, 18001, None):
        with pytest.raises(lumipet.PetError, match="list it for 3000 to 18000 coins"):
            _act(games_conn, alice, "list", price=bad)
    assert _act(games_conn, alice, "list", price=3000)["said"]["price"] == 3000
    out = _act(games_conn, alice, "list", price=18000)
    lot = next(l for l in out["lots"] if l["id"] == "l-1-1")
    assert (lot["list_price"], lot["version"]) == (18000, 3)                           # every change moves the version on
    assert _act(games_conn, alice, "unlist")["said"]["unlisted"] == "l-1-1"
    assert (_row(games_conn, "l-1-1")["list_price"], _row(games_conn, "l-1-1")["version"]) == (None, 4)
    with pytest.raises(lumipet.PetError, match="isn't on the market"):
        _act(games_conn, alice, "unlist")


def test_buying_a_listing_pays_the_seller_and_a_stale_look_is_refused(games_conn, alice, bob):
    _rich(games_conn, alice)
    _rich(games_conn, bob)
    _act(games_conn, alice, "buy", plot="l-1-2")
    _act(games_conn, alice, "style_buy", item=lumicity.ESTATE["styles"][1]["id"])
    _act(games_conn, alice, "style_set", item=lumicity.ESTATE["styles"][1]["id"])
    _act(games_conn, alice, "list", price=7000)                                        # version 2
    with pytest.raises(lumipet.PetError, match="own plot"):
        _act(games_conn, alice, "buy_listing", plot="l-1-2", price=7000, version=2)
    for price, version in ((6000, 2), (7000, 1), (7000, None)):                        # a stale price, an old version
        with pytest.raises(lumipet.PetError, match="Someone changed the price"):
            _act(games_conn, bob, "buy_listing", plot="l-1-2", price=price, version=version)
    with pytest.raises(lumipet.PetError, match="Someone changed the price"):
        _act(games_conn, bob, "buy_listing", plot="l-1-3", price=7000, version=2)      # office land isn't a listing
    seller, buyer = coins.peek(games_conn, alice), coins.peek(games_conn, bob)
    out = _act(games_conn, bob, "buy_listing", plot="l-1-2", price=7000, version=2)
    assert out["said"]["paid"] == 7000 and out["me"]["plot"] == "l-1-2"
    assert (coins.peek(games_conn, alice), coins.peek(games_conn, bob)) == (seller + 7000, buyer - 7000)
    trade = games_conn.execute("SELECT * FROM coin_transactions WHERE type = 'lumi-city_trade'").fetchall()
    assert [(r["from_account"], r["to_account"], r["amount"]) for r in trade] == [(f"user:{bob}", f"user:{alice}", 7000)]
    row = _row(games_conn, "l-1-2")
    assert (row["user_id"], row["list_price"], row["listed_at"], row["version"], row["style"], row["yard"]) == (bob, None, None, 3, lumicity.STARTER, "{}")
    assert _save(games_conn, bob)["home_town"] == "left" and lumicity.STARTER in _save(games_conn, bob)["estate"]["styles"]
    assert lumicity.ESTATE["styles"][1]["id"] in _save(games_conn, alice)["estate"]["styles"]   # the seller keeps her styles
    with pytest.raises(lumipet.PetError, match="Someone changed the price"):
        _act(games_conn, alice, "buy_listing", plot="l-1-2", price=7000, version=2)    # sold: that listing has gone


def test_a_buyer_who_already_has_a_home_or_too_few_coins_is_refused(games_conn, alice, bob):
    _rich(games_conn, alice)
    _act(games_conn, alice, "buy", plot="l-2-5")
    _act(games_conn, alice, "list", price=10_000)
    with pytest.raises(lumipet.PetError, match="can't afford that yet"):
        _act(games_conn, bob, "buy_listing", plot="l-2-5", price=10_000, version=2)
    assert _row(games_conn, "l-2-5")["user_id"] == alice and coins.peek(games_conn, bob) == coins.START


def test_selling_to_the_office_pays_half_once_and_keeps_her_things(games_conn, alice):
    _rich(games_conn, alice)
    piece = next(p for p in lumicity.ESTATE["yard"]["lamp"] if p["price"])
    _act(games_conn, alice, "buy", plot="r-2-1")                                       # suggested 5300
    _act(games_conn, alice, "yard_buy", item=piece["id"])
    _act(games_conn, alice, "list", price=6000)
    before = coins.peek(games_conn, alice)
    out = _act(games_conn, alice, "sell")
    assert out["said"]["paid"] == 2650 and coins.peek(games_conn, alice) == before + 2650
    assert _row(games_conn, "r-2-1") is None and out["me"]["plot"] is None             # office land again
    sold = games_conn.execute("SELECT * FROM coin_transactions WHERE type = 'lumi-city_sell'").fetchall()
    assert [(r["from_account"], r["amount"]) for r in sold] == [(coins.HOUSE, 2650)] and sold[0]["ref"].startswith("lumi-plot-sell:r-2-1:")
    assert piece["id"] in out["me"]["yard"] and lumicity.STARTER in out["me"]["styles"]
    with pytest.raises(lumipet.PetError, match="haven't got a plot"):
        _act(games_conn, alice, "sell")                                                # nothing left to sell
    _act(games_conn, alice, "buy", plot="r-2-1")                                       # bought back, and sold again: a new sale pays
    assert _act(games_conn, alice, "sell")["said"]["paid"] == 2650


def test_forgetting_an_account_hands_its_plot_back_to_the_office(games_conn, alice, settings):
    _rich(games_conn, alice)
    _act(games_conn, alice, "buy", plot="l-1-5")
    assert ("lumi_plots", "user_id") in db.FEATURE_DBS["games"].user_columns
    db.forget_user(settings.db_path, alice)
    assert _row(games_conn, "l-1-5") is None


def test_home_town_follows_her_house(games_conn, alice):
    assert _act(games_conn, alice, "home_town", item="right")["me"]["home_town"] == "right"
    with pytest.raises(lumipet.PetError, match="LefTown or Right-City"):
        _act(games_conn, alice, "home_town", item="middle")
    _rich(games_conn, alice)
    _act(games_conn, alice, "buy", plot="l-1-4")                                       # her house moves her to LefTown
    with pytest.raises(lumipet.PetError, match="My house is in LefTown"):
        _act(games_conn, alice, "home_town", item="right")
    assert _act(games_conn, alice, "home_town", item="left")["me"]["home_town"] == "left"


# --------------------------------------------------------------------------- styles, yards and the Keepsake Capsule
def test_house_styles_are_bought_once_and_put_up_on_her_own_plot(games_conn, alice):
    style = lumicity.ESTATE["styles"][2]
    with pytest.raises(lumipet.PetError, match="isn't a house style"):
        _act(games_conn, alice, "style_buy", item="castle-of-nowhere")
    with pytest.raises(lumipet.PetError, match="can't afford"):
        _act(games_conn, alice, "style_buy", item=lumicity.ESTATE["styles"][-1]["id"])
    _rich(games_conn, alice)
    with pytest.raises(lumipet.PetError, match="haven't got that house style"):
        _act(games_conn, alice, "style_set", item=style["id"])
    before = coins.peek(games_conn, alice)
    assert _act(games_conn, alice, "style_buy", item=style["id"])["said"]["paid"] == style["price"]
    assert coins.peek(games_conn, alice) == before - style["price"]
    with pytest.raises(lumipet.PetError, match="already got that one"):
        _act(games_conn, alice, "style_buy", item=style["id"])
    with pytest.raises(lumipet.PetError, match="need a plot first"):
        _act(games_conn, alice, "style_set", item=style["id"])                        # owned, but nowhere to put it
    _act(games_conn, alice, "buy", plot="l-1-6")
    out = _act(games_conn, alice, "style_set", item=style["id"])
    assert next(l for l in out["lots"] if l["id"] == "l-1-6")["style"] == style["id"]
    assert _row(games_conn, "l-1-6")["version"] == 1                                    # how it looks isn't part of a deal
    assert _act(games_conn, alice, "style_set", item=lumicity.STARTER)["said"]["style"] == lumicity.STARTER   # the starter is always hers


def test_yard_pieces_are_sold_or_rolled_and_go_in_their_own_slot(games_conn, alice):
    sold = next(p for p in lumicity.ESTATE["yard"]["fence"] if p["price"])
    rolled = next(p for p in lumicity.ESTATE["yard"]["fence"] if not p["price"])
    _rich(games_conn, alice)
    with pytest.raises(lumipet.PetError, match="only comes from the Keepsake Capsule"):
        _act(games_conn, alice, "yard_buy", item=rolled["id"])
    with pytest.raises(lumipet.PetError, match="isn't something for a yard"):
        _act(games_conn, alice, "yard_buy", item=lumicity.STARTER)
    assert _act(games_conn, alice, "yard_buy", item=sold["id"])["said"]["paid"] == sold["price"]
    with pytest.raises(lumipet.PetError, match="need a plot first"):
        _act(games_conn, alice, "yard_set", slot="fence", item=sold["id"])
    _act(games_conn, alice, "buy", plot="l-1-7")
    with pytest.raises(lumipet.PetError, match="doesn't go there"):
        _act(games_conn, alice, "yard_set", slot="lamp", item=sold["id"])
    with pytest.raises(lumipet.PetError, match="haven't got that one"):
        _act(games_conn, alice, "yard_set", slot="fence", item=rolled["id"])
    with pytest.raises(lumipet.PetError, match="isn't a spot"):
        _act(games_conn, alice, "yard_set", slot="moat", item=sold["id"])
    out = _act(games_conn, alice, "yard_set", slot="fence", item=sold["id"])
    assert next(l for l in out["lots"] if l["id"] == "l-1-7")["yard"]["fence"] == sold["id"]
    out = _act(games_conn, alice, "yard_set", slot="fence", item="")
    assert next(l for l in out["lots"] if l["id"] == "l-1-7")["yard"]["fence"] == "" and _row(games_conn, "l-1-7")["yard"] == "{}"


def test_the_keepsake_capsule_rolls_a_rarity_then_a_piece_and_repeats_pay_back_half(games_conn, alice):
    _rich(games_conn, alice)
    commons = [p for p in lumicity._KEEPSAKES if p["rarity"] == "common"]
    legendaries = [p for p in lumicity._KEEPSAKES if p["rarity"] == "legendary"]
    before = coins.peek(games_conn, alice)
    first = _act(games_conn, alice, "capsule", rng=Dice(0, 0))["said"]
    assert (first["won"], first["rarity"], first["duplicate"], first["buyback"], first["paid"]) == (commons[0]["id"], "common", False, 0, 200)
    assert first["slot"] == commons[0]["slot"] and coins.peek(games_conn, alice) == before - 200
    again = _act(games_conn, alice, "capsule", rng=Dice(0, 0))["said"]                 # the same piece: bought back for half
    assert (again["duplicate"], again["buyback"]) == (True, lumicity.ESTATE["values"]["common"] // 2)
    assert coins.peek(games_conn, alice) == before - 400 + lumicity.ESTATE["values"]["common"] // 2
    assert _save(games_conn, alice)["estate"]["yard"] == [commons[0]["id"]]            # owned once
    top = _act(games_conn, alice, "capsule", rng=Dice(99, 1))["said"]                  # the very top of the odds: legendary
    assert (top["won"], top["rarity"]) == (legendaries[1]["id"], "legendary")
    assert games_conn.execute("SELECT count(*) FROM coin_transactions WHERE type = 'lumi-city_capsule'").fetchone()[0] == 3


def test_the_keepsake_capsule_stops_before_charging_when_everything_is_hers(games_conn, alice):
    _act(games_conn, alice, "home_town", item="left")                                  # (her save exists)
    with transaction(games_conn):
        _set(games_conn, alice, estate={"styles": [], "yard": [p["id"] for p in lumicity._KEEPSAKES], "rooms": {}})
    before = coins.peek(games_conn, alice)
    with pytest.raises(lumipet.PetError, match="already got everything the Keepsake Capsule holds"):
        _act(games_conn, alice, "capsule", rng=Dice(0, 0))
    assert coins.peek(games_conn, alice) == before


def test_the_star_capsule_still_rolls_exactly_as_before(games_conn, alice):
    pool = [p for p in lumipet._PRIZES if p["rarity"] in lumipet.CLOSET["capsule"]["odds"]]
    assert lumipet._roll(pool, lumipet.CLOSET["capsule"]["odds"], Dice(0, 0)) == ([p for p in pool if p["rarity"] == "common"][0], "common")
    assert lumipet._roll(pool, lumipet.CLOSET["capsule"]["odds"], Dice(99, 0))[1] == "legendary"


# --------------------------------------------------------------------------- her save, and the second home
def test_old_saves_gain_a_home_town_and_an_estate(games_conn, alice):
    with transaction(games_conn):
        lumipet.state(games_conn, alice)
        pet = _save(games_conn, alice)
        del pet["estate"], pet["home_town"]
        games_conn.execute("UPDATE lumi_pets SET data = ? WHERE user_id = ?", (json.dumps(pet), alice))
        snap = lumipet.state(games_conn, alice)
    assert snap["pet"]["home_town"] is None and snap["pet"]["estate"]["styles"] == [] and snap["pet"]["estate"]["yard"] == []
    assert isinstance(snap["pet"]["estate"]["rooms"], dict)
    with transaction(games_conn):
        _set(games_conn, alice, estate={"styles": "nope", "rooms": []}, home_town="middle")   # a broken one is mended
        snap = lumipet.state(games_conn, alice)
    assert (snap["pet"]["estate"]["styles"], snap["pet"]["home_town"]) == ([], None)


def test_the_house_has_its_own_rooms_furnished_from_the_same_furniture(games_conn, alice):
    piece = next(i for i in lumipet._SPOTS["rug"]["items"] if i["price"] and "rarity" not in i and not i.get("found") and "prize" not in i)
    _rich(games_conn, alice)
    with pytest.raises(lumipet.PetError, match="haven't got a house yet"):
        with transaction(games_conn):
            lumipet.act(games_conn, alice, "decorate", item=piece["id"], home="house")
    _act(games_conn, alice, "buy", plot="l-1-3")
    before = coins.peek(games_conn, alice)
    with transaction(games_conn):
        out = lumipet.act(games_conn, alice, "decorate", item=piece["id"], home="house")   # bought, and put in the house
        lumipet.act(games_conn, alice, "paint", spot="wallpaper", colour="#112233", home="house")
    assert coins.peek(games_conn, alice) == before - piece["price"] and piece["id"] in out["pet"]["decor"]
    assert out["pet"]["estate"]["rooms"]["rug"] == piece["id"] and out["pet"]["house"].get("rug") != piece["id"]   # the flat is untouched
    house = lumipet.house_view(games_conn, alice, "house")
    assert house["house"]["rug"] == piece["id"] and house["house"]["wallpaper"] == "#112233" and house["boxes"] == 0
    flat = lumipet.house_view(games_conn, alice)
    assert flat["house"].get("rug") != piece["id"] and flat["boxes"] > 0
    with transaction(games_conn):
        again = lumipet.act(games_conn, alice, "decorate", item=piece["id"])          # owned once: into the flat for nothing
    assert again["said"] is None and again["pet"]["house"]["rug"] == piece["id"]
    with pytest.raises(lumipet.PetError, match="only has a flat and a house"):
        with transaction(games_conn):
            lumipet.act(games_conn, alice, "decorate", item=piece["id"], home="castle")


def test_house_folk_and_flat_folk_never_mix():
    houses = lumihouse.Houses(clock=lambda: 1000.0)
    assert lumihouse.place(1, "den") == "house:1:den" and lumihouse.place(1, "den", "house") == "home:1:den"
    assert houses.visit(1, 1, "den", 500, 430, 1) is True                              # the host, in her flat
    assert houses.visit(2, 1, "den", 400, 430, 1, "house") is True                     # a guest, in her house
    assert houses.where(2) == "home:1:den" and houses.where(1) == "house:1:den"
    assert [p["id"] for p in houses.inside(1, home="house")] == [2] and [p["id"] for p in houses.inside(1, home="flat")] == [1]
    assert {p["home"] for p in houses.inside(1)} == {"flat", "house"}                  # both, when asked for either
    assert houses.home(1) and houses.home(1, "flat") and not houses.home(1, "house")
    assert houses.visit(2, 1, "den", 400, 430, 1, "flat") is True                      # the other home is somewhere new


# --------------------------------------------------------------------------- through the API
@pytest.fixture
def fresh(monkeypatch):
    """Fresh town and homes, no rate limits left over, and every event kept."""
    monkeypatch.setattr(lumitown, "TOWN", lumitown.Town())
    monkeypatch.setattr(lumihouse, "HOUSES", lumihouse.Houses())
    for uid in range(1, 30):
        for key in (f"lumi-say:{uid}", f"lumi-say-burst:{uid}", f"lumi-knock:{uid}"):
            events.forget(key)
    heard = []
    monkeypatch.setattr(events, "publish_sync", lambda channel, data: heard.append(data) or 1)
    return heard


def _two(client, conn, directory):
    """boss and jane, each signed in on their own browser, each with a Lumi."""
    _login(client, conn, directory)
    _turn_on(client)
    jane = TestClient(client.app)
    _login(jane, conn, directory, "jane", "jsmith")
    for who in (client, jane):
        assert who.get(API + "state").status_code == 200
    boss_id = client.post(API + "town", json={"at": "lobby-l", "inside": True}).json()["you"]
    jane_id = jane.post(API + "town", json={"at": "lobby-l", "inside": True}).json()["you"]
    return client, jane, boss_id, jane_id


def test_the_city_and_the_estate_through_the_api(client, conn, directory, games_conn, fresh):
    boss, jane, boss_id, jane_id = _two(client, conn, directory)
    city = jane.get(API + "city")
    assert city.status_code == 200, city.text
    city = city.json()
    assert set(city) == {"towns", "lots", "me", "catalog", "layout"} and city["towns"] == {"left": 2, "right": 2}
    assert len(city["lots"]) == 32 and city["me"]["plot"] is None and city["me"]["apt"] == lumicity.apartment(jane_id)
    assert city["layout"]["price"]["base"] == 5000 and len(city["catalog"]["styles"]) == 30
    broke = jane.post(API + "estate", json={"action": "buy", "plot": "l-1-3"})
    assert broke.status_code == 400 and "can't afford" in broke.json()["detail"]
    assert fresh == [] or {"kind": "city"} not in fresh                                # a refusal tells nobody
    _rich(games_conn, jane_id)
    bought = jane.post(API + "estate", json={"action": "buy", "plot": "l-1-3"})
    assert bought.status_code == 200, bought.text
    assert bought.json()["said"]["bought"] == "l-1-3" and {"kind": "city"} in fresh   # everyone's page looks again
    seen = boss.get(API + "city").json()
    assert next(l for l in seen["lots"] if l["id"] == "l-1-3")["owner"] == {"id": jane_id, "name": "jane"}
    for bad in ({"action": "buy", "plot": "x-1-1"}, {"action": "buy", "plot": "l-1-9"}, {"action": "list", "price": 0},
                {"action": "dig"}, {"action": "buy", "plot": "l-1-1", "sneaky": 1}, {"action": "style_buy", "item": "Bad!"},
                {"action": "buy_listing", "plot": "l-1-3", "price": 100, "version": "2"}):
        assert jane.post(API + "estate", json=bad).status_code == 422, bad
    fresh.clear()
    assert jane.post(API + "estate", json={"action": "capsule"}).status_code == 200
    assert {"kind": "city"} not in fresh                                               # a capsule changes nothing on the map
    state = jane.get(API + "state").json()["pet"]
    assert state["home_town"] == "left" and lumicity.STARTER in state["estate"]["styles"] and len(state["estate"]["yard"]) == 1


def test_a_house_is_a_second_home_with_its_own_folk(client, conn, directory, games_conn, fresh):
    boss, jane, boss_id, jane_id = _two(client, conn, directory)
    assert jane.get(API + f"houses/{boss_id}?home=house").status_code == 404          # no land, no house
    _rich(games_conn, boss_id)
    boss.post(API + "estate", json={"action": "buy", "plot": "r-1-4"})
    boss.post(API + "act", json={"action": "door", "item": "open"})
    view = jane.get(API + f"houses/{boss_id}?home=house").json()
    assert view["plot"] == "r-1-4" and view["access"] == "in" and view["boxes"] == 0 and isinstance(view["house"], dict)
    assert view["town"] == "right" and view["apt"] == lumicity.apartment(boss_id)
    here = lambda who, host, home, room="bedroom": who.post(API + "houses/here", json={"host": host, "home": home, "room": room, "x": 500, "y": 430})
    assert here(boss, None, "flat").status_code == 200                                  # boss is in his flat
    inside = here(jane, boss_id, "house").json()
    assert inside["place"] == f"home:{boss_id}:bedroom" and inside["here"] == [] and inside["home"] is False   # not in his house
    said = jane.post(API + "town/say", json={"phrase": "hi"}).json()["line"]
    assert said["place"] == f"home:{boss_id}:bedroom"
    assert here(boss, None, "flat").json()["lines"] == []                              # the flat never hears the house
    assert [p["home"] for p in here(boss, None, "house").json()["here"]] == ["house"]   # boss walks over: there she is
    assert here(jane, boss_id, "house").json()["home"] is True
    with transaction(games_conn):
        lumicity.act(games_conn, boss_id, "sell")
    assert here(jane, boss_id, "house").status_code == 404                              # sold: the house has gone


def test_lumi_heights_lists_floors_and_hides_closed_doors(client, conn, directory, games_conn, fresh):
    boss, jane, boss_id, jane_id = _two(client, conn, directory)
    heights = boss.get(API + "heights/left").json()
    floor = lumicity.apartment(jane_id)["floor"]
    assert heights["town"] == "left" and floor in heights["floors"] and heights["me"] == {"apt": lumicity.apartment(boss_id), "town": "left"}
    assert boss.get(API + "heights/right").json()["floors"] == []                     # nobody has picked Right-City yet
    assert boss.get(API + "heights/middle").status_code == 422
    hall = boss.get(API + f"heights/left/{floor}").json()
    assert (hall["town"], hall["floor"], len(hall["doors"])) == ("left", floor, 8)
    door = hall["doors"][lumicity.apartment(jane_id)["door"] - 1]
    assert (door["id"], door["name"], door["label"], door["open"]) == (jane_id, "jane", lumicity.apartment(jane_id)["label"], False)   # knock first
    assert door["lumi"] == "Lumi"
    mine = boss.get(API + f"heights/left/{lumicity.apartment(boss_id)['floor']}").json()["doors"][lumicity.apartment(boss_id)["door"] - 1]
    assert (mine["id"], mine["open"]) == (boss_id, True)                               # your own door is always open to you
    jane.post(API + "act", json={"action": "door", "item": "closed"})
    shut = boss.get(API + f"heights/left/{floor}").json()["doors"][lumicity.apartment(jane_id)["door"] - 1]
    assert (shut["id"], shut["name"], shut["lumi"]) == (None, None, None) and shut["label"] == door["label"]   # an unnamed door
    jane.post(API + "estate", json={"action": "home_town", "item": "right"})
    assert floor in boss.get(API + "heights/right").json()["floors"]                   # she moved to Right-City's building
    empty = boss.get(API + "heights/left/500").json()["doors"]
    assert all(d["id"] is None for d in empty) and empty[0]["label"] == "500A"
    flats = boss.get(API + "houses").json()
    assert flats["me"] == {"apt": lumicity.apartment(boss_id), "town": "left"}
