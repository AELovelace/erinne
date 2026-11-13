"""Lumi Dungeons with friends (mnemerynne/lumiparty.py): the lobby, votes, the puzzle circle, fights, finds and going
home together, and what the page is told."""

import json
import random

import pytest
from starlette.testclient import TestClient

from mnemerynne import coins, lumibattle, lumidungeon as D, lumiparty as P, lumipet, users
from mnemerynne.db import transaction
from test_lumidungeon import Dice
from test_media import (_login, _no_network, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings)

T0 = 1_800_000_000_000
S = 1000                                                    # one second, in the party's milliseconds


@pytest.fixture
def trio(conn):
    """Three accounts with a pet each: Doll (who makes the party), Jane and Mina."""
    ids = [users.create_user(conn, name, "password123") for name in ("doll", "jane", "mina")]
    for uid in ids:
        with transaction(conn):
            lumipet.load(conn, uid, T0)
    return ids


def _act(conn, code, uid, action, now=T0, rng=None, **kwargs):
    with transaction(conn):
        return P.act(conn, code, uid, action, now, rng or random.Random(3), **kwargs)


def _party(conn, trio, size=3):
    """A party of `size`, everyone ready, set out on a hand-made floor (see _floor)."""
    with transaction(conn):
        state = P.create(conn, trio[0], "doll", T0)
    code = state["code"]
    for uid, name in list(zip(trio, ("doll", "jane", "mina")))[1:size]:
        _act(conn, code, uid, "join", name=name)
    for uid in trio[:size]:
        _act(conn, code, uid, "ready")
    _act(conn, code, trio[0], "start")
    _floor(conn, code)
    return code


def _floor(conn, code, lock=None, game=None):
    """A small known floor:   start(0,0) - hall(1,0) - vault(2,0)
                                              |
                              game(1,1) - creature(2,1) - stairs(3,1)
       with a chest in the hall and a key room under the game."""
    state = _state(conn, code)
    rooms = {
        "0,0": {"kind": "start", "exits": ["e"], "seen": True, "done": True},
        "1,0": {"kind": "treasure", "exits": ["e", "s", "w"], "seen": False, "done": False, "loot": {"coins": 20, "potions": 1}},
        "2,0": {"kind": "vault", "exits": ["w"], "seen": False, "done": False,
                "lock": lock or {"kind": "runes", "code": ["sun", "moon", "star", "leaf"], "tries": []},
                "loot": {"coins": 90, "potions": 1}},
        "1,1": {"kind": "game", "exits": ["e", "n", "s"], "seen": False, "done": False,
                "game": game or {"kind": "cups", "prize": 1}},
        "1,2": {"kind": "key", "exits": ["n"], "seen": False, "done": False, "loot": {"keys": 1}},
        "2,1": {"kind": "enemy", "exits": ["e", "w"], "seen": False, "done": False, "enemy": {"id": D.CATALOG and lumibattle.CATALOG["enemies"][0]["id"]}},
        "3,1": {"kind": "stairs", "exits": ["w"], "seen": False, "done": True},
    }
    state["run"].update(rooms=rooms, at="0,0", prev=None, facing=None, puzzle=None, fighting=False, vote_since=None)
    _put(conn, state)


def _state(conn, code):
    return json.loads(conn.execute("SELECT state FROM lumi_parties WHERE code = ?", (code,)).fetchone()[0])


def _put(conn, state):
    conn.execute("UPDATE lumi_parties SET state = ? WHERE code = ?", (json.dumps(state), state["code"]))


def _walk(conn, code, trio, way, now=T0):
    """Two of three vote the same way: a majority, so the party goes at once."""
    _act(conn, code, trio[0], "move", now, way=way)
    return _act(conn, code, trio[1], "move", now, way=way)


# ------------------------------------------------------------------ the lobby
def test_a_party_is_three_at_most_and_sets_out_when_everyone_is_ready(conn, trio):
    fourth = users.create_user(conn, "zoe", "password123")
    with transaction(conn):
        code = P.create(conn, trio[0], "doll", T0)["code"]
    _act(conn, code, trio[1], "join", name="jane")
    _act(conn, code, trio[2], "join", name="mina")
    with pytest.raises(P.PartyError, match="full"):
        _act(conn, code, fourth, "join", name="zoe")
    with pytest.raises(P.PartyError, match="already in a party"):
        with transaction(conn):
            P.create(conn, trio[1], "jane", T0)
    _act(conn, code, trio[0], "ready")
    with pytest.raises(P.PartyError, match="host"):
        _act(conn, code, trio[1], "start")
    with pytest.raises(P.PartyError, match="Not everyone"):
        _act(conn, code, trio[0], "start")
    _act(conn, code, trio[1], "ready")
    _act(conn, code, trio[2], "ready")
    seen = _act(conn, code, trio[0], "start")
    assert seen["phase"] == "trip" and seen["run"]["depth"] == 1
    assert [m["name"] for m in seen["members"]] == ["doll", "jane", "mina"] and seen["you"] == trio[0]
    with pytest.raises(P.PartyError, match="already set out"):
        _act(conn, code, fourth, "join", name="zoe")


def test_a_party_member_cant_go_exploring_alone_or_the_other_way_round(conn, trio):
    with transaction(conn):
        lumipet.dungeon_start(conn, trio[2], "moonlit-shrine", T0, random.Random(1))   # Mina is out on her own
        code = P.create(conn, trio[0], "doll", T0)["code"]
    with pytest.raises(P.PartyError, match="on her own"):
        _act(conn, code, trio[2], "join", name="mina")
    _act(conn, code, trio[1], "join", name="jane")
    with pytest.raises(lumipet.PetError, match="in a party"):
        with transaction(conn):
            lumipet.dungeon_start(conn, trio[1], "moonlit-shrine", T0, random.Random(1))


# ------------------------------------------------------------------ votes
def test_a_majority_settles_a_vote_at_once_and_one_vote_waits(conn, trio):
    code = _party(conn, trio)
    seen = _act(conn, code, trio[0], "move", way="e")
    assert seen["run"]["at"] == "0,0" and seen["run"]["party"]["votes"] == {str(trio[0]): "e"}
    assert seen["run"]["party"]["ends"] == T0 + P.VOTE_SECONDS * S
    seen = _act(conn, code, trio[1], "move", way="e")
    assert seen["run"]["at"] == "1,0" and seen["run"]["party"]["votes"] == {}
    assert "The party chooses east." in seen["run"]["say"]


def test_after_the_vote_time_the_most_votes_win_and_a_tie_goes_to_the_dice(conn, trio):
    code = _party(conn, trio)
    _walk(conn, code, trio, "e")                            # into the hall: west, east or south
    _act(conn, code, trio[0], "move", T0, way="w")
    with transaction(conn):
        assert P.look(conn, code, trio[0], T0 + 5 * S)["run"]["at"] == "1,0"      # still time to vote
        seen = P.look(conn, code, trio[0], T0 + P.VOTE_SECONDS * S)
    assert seen["run"]["at"] == "0,0" and "The party chooses west." in seen["run"]["say"]   # one vote, but the time is up

    _walk(conn, code, trio, "e")
    _act(conn, code, trio[0], "move", T0, way="e")
    _act(conn, code, trio[1], "move", T0, way="s")
    seen = _act(conn, code, trio[2], "move", T0, Dice(1), way="w")       # one each: settled at once, by the dice
    assert seen["run"]["at"] == "1,1" and "The dice choose south." in seen["run"]["say"]


def test_voting_again_takes_a_vote_back(conn, trio):
    code = _party(conn, trio)
    _act(conn, code, trio[0], "move", way="e")
    seen = _act(conn, code, trio[0], "move", way="e")
    assert seen["run"]["party"]["votes"] == {} and seen["run"]["party"]["ends"] is None
    with pytest.raises(P.PartyError, match="choices"):
        _act(conn, code, trio[0], "move", way="n")         # not a way out of this room


# ------------------------------------------------------------------ the puzzle circle
def _at_vault(conn, code, trio, lock):
    _floor(conn, code, lock=lock)
    _walk(conn, code, trio, "e")
    seen = _walk(conn, code, trio, "e")                     # the vault door: a puzzle to pick someone for
    assert seen["run"]["facing"]["at"] == "2,0" and seen["run"]["party"]["puzzle"]["phase"] == "pick"
    return seen


def _tries(conn, code, counts):
    state = _state(conn, code)
    for m, n in zip(state["members"], counts):
        m["puzzles"] = n
    _put(conn, state)


def test_the_vote_picks_who_goes_first_then_the_fewest_tried_then_the_last(conn, trio):
    code = _party(conn, trio)
    _at_vault(conn, code, trio, {"kind": "runes", "code": ["sun", "moon", "star", "leaf"], "tries": []})
    _tries(conn, code, [0, 4, 1])                           # Jane has tried most, Mina once, Doll never
    _act(conn, code, trio[0], "vote", target=f"m{trio[1]}")
    seen = _act(conn, code, trio[2], "vote", target=f"m{trio[1]}")     # two of three choose Jane
    puzzle = seen["run"]["party"]["puzzle"]
    assert puzzle["phase"] == "attempt" and puzzle["order"] == [trio[1], trio[0], trio[2]] and puzzle["holder"] == trio[1]
    assert "jane's turn at the runes (3 guesses)." in seen["run"]["say"]

    # Only Jane's moves count; each turn is three guesses; the circle goes round and starts again unchanged.
    with pytest.raises(P.PartyError, match="jane's turn"):
        _act(conn, code, trio[0], "rune", picks=["sun", "sun", "sun", "sun"])
    wrong = ["wave", "wave", "wave", "wave"]
    holders = []
    for _ in range(4):
        before = _state(conn, code)["run"]["puzzle"]
        who = before["order"][before["turn"]]
        holders.append(who)
        for _ in range(3):
            seen = _act(conn, code, who, "rune", picks=wrong)
    assert holders == [trio[1], trio[0], trio[2], trio[1]]
    assert seen["run"]["party"]["puzzle"]["order"] == [trio[1], trio[0], trio[2]]
    assert [m["puzzles"] for m in seen["members"]] == [0 + 2, 4 + 2, 1 + 1]   # each turn begun counts (Doll's 2nd has begun)

    who = seen["run"]["party"]["puzzle"]["holder"]
    answer = _state(conn, code)["run"]["rooms"]["2,0"]["lock"]["code"]      # after 8 guesses the door chose a new code
    seen = _act(conn, code, who, "rune", picks=answer)      # solved: everyone walks in
    assert seen["run"]["at"] == "2,0" and seen["run"]["party"]["puzzle"] is None and "opens the door" in seen["run"]["say"]


def test_a_tie_for_who_goes_first_goes_to_whoever_has_tried_fewest(conn, trio):
    code = _party(conn, trio)
    _at_vault(conn, code, trio, {"kind": "runes", "code": ["sun", "moon", "star", "leaf"], "tries": []})
    _tries(conn, code, [3, 1, 1])
    _act(conn, code, trio[0], "vote", target=f"m{trio[0]}")
    _act(conn, code, trio[1], "vote", target=f"m{trio[2]}")
    seen = _act(conn, code, trio[2], "vote", target=f"m{trio[1]}")      # one each: Jane and Mina tied on 1, Jane sits first
    assert seen["run"]["party"]["puzzle"]["order"] == [trio[1], trio[2], trio[0]]


def test_crates_are_watched_move_by_move_and_time_ends_a_turn(conn, trio):
    level = D.SOKOBAN[0]
    code = _party(conn, trio)
    _at_vault(conn, code, trio, {"kind": "sokoban", "level": level["id"]})
    _act(conn, code, trio[0], "vote", target=f"m{trio[0]}")
    seen = _act(conn, code, trio[1], "vote", target=f"m{trio[0]}")
    assert seen["run"]["facing"]["lock"]["rows"] == level["rows"] and "level" not in seen["run"]["facing"]["lock"]
    walls, goals, boxes, lumi = D.sokoban_parse(level["rows"])
    step = next(m for m in "udlr" if P._sokoban_progress(level["rows"], m)[0])
    seen = _act(conn, code, trio[0], "sokoban", moves=step)
    assert seen["run"]["party"]["puzzle"]["moves"] == step               # the others replay this to watch
    assert seen["run"]["party"]["puzzle"]["ends"] == T0 + 180 * S
    with transaction(conn):
        seen = P.look(conn, code, trio[1], T0 + 181 * S)
    puzzle = seen["run"]["party"]["puzzle"]
    assert puzzle["holder"] == trio[1] and puzzle["moves"] == "" and "Time's up for doll." in seen["run"]["say"]


def test_pass_it_on_and_a_holder_who_leaves_hand_the_puzzle_on(conn, trio):
    code = _party(conn, trio)
    _at_vault(conn, code, trio, {"kind": "lights", "size": 3, "board": [True] + [False] * 8})
    _act(conn, code, trio[0], "vote", target=f"m{trio[2]}")
    _act(conn, code, trio[1], "vote", target=f"m{trio[2]}")
    seen = _act(conn, code, trio[2], "pass")
    assert seen["run"]["party"]["puzzle"]["holder"] == trio[0] and "mina passes it on." in seen["run"]["say"]
    seen = _act(conn, code, trio[0], "abandon")             # Doll goes home mid-turn: Jane carries on
    assert seen.get("you") is None
    state = _state(conn, code)
    assert state["run"]["puzzle"]["order"] == [trio[2], trio[1]] and P.holder(state) == trio[1]
    assert P.code_of(conn, trio[0]) is None


def test_cards_take_six_flips_a_turn_and_cups_are_one_pick(conn, trio):
    deck = ["sun", "moon", "star", "leaf"] * 2
    code = _party(conn, trio)
    _floor(conn, code, game={"kind": "match", "deck": deck, "found": [], "open": None, "flips": 0, "last": None})
    _walk(conn, code, trio, "e")
    seen = _walk(conn, code, trio, "s")
    assert seen["run"]["game"]["kind"] == "match" and set(seen["run"]["party"]["options"]) >= {f"m{trio[0]}", "e", "n", "s"}
    _act(conn, code, trio[0], "vote", target=f"m{trio[0]}")
    _act(conn, code, trio[1], "vote", target=f"m{trio[0]}")
    for pick in (0, 1, 2, 3, 4, 5):                         # three misses: sun/moon, star/leaf, sun/moon
        seen = _act(conn, code, trio[0], "flip", picks=[pick])
    assert seen["run"]["party"]["puzzle"]["holder"] != trio[0]

    code2_floor = {"kind": "cups", "prize": 2}
    _floor(conn, code, game=code2_floor)
    _walk(conn, code, trio, "e")
    _walk(conn, code, trio, "s")
    _act(conn, code, trio[0], "vote", target=f"m{trio[1]}")
    _act(conn, code, trio[1], "vote", target=f"m{trio[1]}")
    seen = _act(conn, code, trio[1], "cup", rng=Dice(10), picks=[2])
    assert seen["run"]["room"]["done"] and seen["run"]["party"]["puzzle"] is None
    assert all(m["satchel"]["coins"] > 0 for m in seen["members"])     # a win pays everyone standing


# ------------------------------------------------------------------ fights
# ------------------------------------------------------------------ the co-op fight
KO = {"name": "Crush", "power": 250, "accuracy": 1000, "magic": False, "element": "none", "effect": None, "hits": 1, "mp": 0}
TAP = {"name": "Tap", "power": 1, "accuracy": 1000, "magic": False, "element": "none", "effect": None, "hits": 1, "mp": 0}


def _into_creature(conn, code, trio):
    """Walk the party into the creature's room: one co-op fight for everyone, kept in the party, no fight rows."""
    _walk(conn, code, trio, "e")
    _walk(conn, code, trio, "s")
    _act(conn, code, trio[0], "move", way="e")
    seen = _act(conn, code, trio[1], "move", way="e")
    assert seen["run"]["at"] == "2,1" and seen["run"]["party"]["fighting"]
    fight = seen["run"]["party"]["fight"]
    assert [lumi["user"] for lumi in fight["lumis"]] == trio and 1 <= len(fight["foes"]) <= 3
    assert (fight["round"], fight["at"], fight["turn"]) == (1, 0, 0) and fight["you"] == 1      # Jane looking: she is seat 1
    assert not any(m["fight"] for m in _state(conn, code)["members"])
    assert all(lumibattle.current(conn, uid) is None for uid in trio)
    return seen


def _rig(conn, code, foes=1, foe_hp=500, move=TAP, **lumis):
    """Make the fight predictable: `foes` creatures with this much health and this one move, and any Lumi's numbers
    set by seat (lumis={"0": {"hp": 1}})."""
    state = _state(conn, code)
    fight = state["run"]["fight"]
    first = fight["foes"][0]
    fight["foes"] = [{**first, "name": first["name"] + (" " + "ABC"[i] if foes > 1 else ""), "hp": foe_hp, "hpMax": foe_hp,
                      "moves": [dict(move)], "dodge": 0, "thorns": 0} for i in range(foes)]
    for seat, numbers in lumis.items():
        fight["lumis"][int(seat)].update(numbers)
    _put(conn, state)


def _turn(conn, code, uid, target, **kwargs):
    turn = _state(conn, code)["run"]["fight"]["turn"]
    return _act(conn, code, uid, "fight", rng=Dice(chance=0.0), target=target, turn=kwargs.pop("turn", turn), **kwargs)


def test_a_party_fight_is_one_fight_taken_in_turns_round_robin(conn, trio):
    code = _party(conn, trio)
    _into_creature(conn, code, trio)
    _rig(conn, code, foes=2)
    with pytest.raises(P.PartyError, match="still fighting"):
        _act(conn, code, trio[0], "move", way="e")
    with pytest.raises(P.PartyError, match="doll's Lumi|It's"):        # Jane can't go before Doll: seat order
        _turn(conn, code, trio[1], "move0")
    with pytest.raises(P.PartyError, match="already happened"):        # a stale page's press changes nothing
        _turn(conn, code, trio[0], "move0", turn=7)
    seen = _turn(conn, code, trio[0], "move0", foe=1)                   # Doll aims at the second creature
    fight = seen["run"]["party"]["fight"]
    assert fight["foes"][1]["hp"] < 500 and fight["foes"][0]["hp"] == 500 and (fight["at"], fight["turn"]) == (1, 1)
    hp = [lumi["hp"] for lumi in fight["lumis"]]
    _turn(conn, code, trio[1], "defend")
    seen = _turn(conn, code, trio[2], "move0")                          # the last Lumi: then both creatures strike
    fight = seen["run"]["party"]["fight"]
    assert fight["round"] == 2 and fight["at"] == 0 and sum(lumi["hp"] for lumi in fight["lumis"]) < sum(hp)
    assert any("Tap" in line for line in fight["log"]) and fight["ends"] == T0 + P.TURN_SECONDS * S
    with pytest.raises(P.PartyError, match="already down"):           # aiming at a creature that is down
        state = _state(conn, code)
        state["run"]["fight"]["foes"][0]["hp"] = 0
        _put(conn, state)
        _turn(conn, code, trio[0], "move0", foe=0)


def test_a_win_gives_every_lumi_the_whole_exp_and_her_own_coins(conn, trio):
    code = _party(conn, trio)
    _into_creature(conn, code, trio)
    _rig(conn, code, foes=2, foe_hp=1)
    fight = _state(conn, code)["run"]["fight"]
    whole = lumibattle.team_reward(fight)[0]
    assert whole == sum(15 + foe["level"] * 3 + lumibattle.TIERS[foe["tier"]][3] for foe in fight["foes"])
    exp = {uid: lumipet.load(conn, uid, T0)["exp"] for uid in trio}
    _turn(conn, code, trio[0], "move0", foe=0)
    seen = _turn(conn, code, trio[1], "move0", foe=1)                   # both down: the room is won
    state = _state(conn, code)
    assert state["run"]["fight"] is None and state["run"]["rooms"]["2,1"]["done"] and not seen["run"]["party"]["fighting"]
    assert seen["run"]["party"]["last_fight"]["outcome"] == "victory" and seen["run"]["party"]["last_fight"]["exp"] == whole
    for uid in trio:                                                    # every Lumi, even Mina who never got a turn, gets all of it
        pet = lumipet.load(conn, uid, T0)
        assert pet["exp"] + sum(lumibattle.level_threshold(lv) for lv in range(1, pet["level"])) == exp[uid] + whole
        assert pet["used"].get("pounce", 0) == (1 if uid in trio[:2] else 0)      # what she used counts towards Auto
    assert [m["satchel"]["coins"] for m in state["members"]] == [int(whole * 0.6)] * 3


def test_a_friend_heals_another_with_her_own_potion_or_lifts_her_with_an_x_item(conn, trio):
    code = _party(conn, trio)
    with transaction(conn):
        pet = lumipet.load(conn, trio[0], T0)
        pet["potions"], pet["kit"] = 2, {"x-attack": 1, "ether": 1}
        lumipet._save(conn, trio[0], pet)
    _into_creature(conn, code, trio)
    _rig(conn, code, **{"1": {"hp": 5, "mp": 0}})
    with pytest.raises(P.PartyError, match="already at full health"):
        _turn(conn, code, trio[0], "potion", ally=2)
    seen = _turn(conn, code, trio[0], "potion", ally=1)                 # Doll's own potion, for Jane
    fight = seen["run"]["party"]["fight"]
    assert fight["lumis"][1]["hp"] > 5 and fight["at"] == 1 and "doll's Lumi gives jane's Lumi a potion" in " ".join(fight["log"])
    assert lumipet.load(conn, trio[0], T0)["potions"] == 1 and lumipet.load(conn, trio[1], T0)["potions"] == 0
    _turn(conn, code, trio[1], "defend")
    _turn(conn, code, trio[2], "defend")
    seen = _turn(conn, code, trio[0], "x-attack", ally=2)              # round two: Mina's attack goes up a stage
    assert seen["run"]["party"]["fight"]["lumis"][2]["stages"] == {"attack": 1}
    with pytest.raises(P.PartyError, match="No X-Attack left"):
        _turn(conn, code, trio[1], "x-attack", ally=1)                 # Jane has none of her own
    assert lumipet.load(conn, trio[0], T0)["kit"]["x-attack"] == 0


def test_a_lumi_down_at_the_end_faints_into_a_ghost_but_still_gets_her_exp(conn, trio):
    code = _party(conn, trio)
    _into_creature(conn, code, trio)
    _rig(conn, code, foe_hp=1, **{"1": {"hp": 0}})                      # Jane fell earlier in the fight
    state = _state(conn, code)
    for m in state["members"]:
        m["satchel"]["coins"] = 5
    _put(conn, state)
    level = lumipet.load(conn, trio[1], T0)
    _turn(conn, code, trio[0], "move0")
    state = _state(conn, code)
    doll, jane, mina = state["members"]
    assert jane["fainted"] and jane["satchel"]["coins"] == 0 and jane["hp"] == 0
    assert not doll["fainted"] and doll["satchel"]["coins"] > 5 and mina["satchel"]["coins"] == doll["satchel"]["coins"]
    after = lumipet.load(conn, trio[1], T0)
    assert (after["level"], after["exp"]) != (level["level"], level["exp"]) and after["wound"] == 1.0   # EXP all the same; she comes home hurt
    seen = _act(conn, code, trio[1], "move", way="e")                   # a ghost still votes
    assert seen["run"]["party"]["votes"] == {str(trio[1]): "e"}
    with pytest.raises(P.PartyError, match="ghost"):
        _act(conn, code, trio[1], "potion")


def test_if_everyone_faints_the_trip_is_over(conn, trio):
    code = _party(conn, trio)
    _into_creature(conn, code, trio)
    _rig(conn, code, foes=3, move=KO, **{"0": {"hp": 1}, "1": {"hp": 1}, "2": {"hp": 1}})   # three creatures: one Lumi each
    for uid in trio:
        _turn(conn, code, uid, "defend")
    state = _state(conn, code)
    assert state["phase"] == "lobby" and state["last"]["how"] == "defeat" and state["run"] is None
    assert all(not m["ready"] for m in state["members"])


def test_a_turn_left_too_long_is_braced_through_and_running_takes_everyone_back(conn, trio):
    code = _party(conn, trio)
    _into_creature(conn, code, trio)
    _rig(conn, code)
    with transaction(conn):
        seen = P.look(conn, code, trio[1], T0 + P.TURN_SECONDS * S)
    fight = seen["run"]["party"]["fight"]
    assert fight["at"] == 1 and fight["log"][-1] == "Time's up! doll's Lumi braces."
    _act(conn, code, trio[1], "fight", T0 + P.TURN_SECONDS * S, Dice(chance=0.0), target="run", turn=fight["turn"])
    state = _state(conn, code)
    assert state["run"]["fight"] is None and state["run"]["at"] == "1,1" and state["run"]["event"]["scenario"] == "fled"


def test_leaving_mid_fight_takes_her_lumi_out_and_passes_her_turn_on(conn, trio):
    code = _party(conn, trio)
    _into_creature(conn, code, trio)
    _rig(conn, code)
    _act(conn, code, trio[0], "abandon")
    fight = _state(conn, code)["run"]["fight"]
    assert fight["lumis"][0]["gone"] and fight["at"] == 1               # Jane's turn now
    seen = _turn(conn, code, trio[1], "defend")
    assert seen["run"]["party"]["fight"]["at"] == 2


# ------------------------------------------------------------------ finds and going home
def test_each_member_gets_their_own_share_and_coins_are_paid_once_at_home(conn, trio):
    code = _party(conn, trio)
    seen = _walk(conn, code, trio, "e")
    assert seen["run"]["event"]["scenario"] == "see_treasure"
    seen = _act(conn, code, trio[2], "take")                # anyone may open it; everyone gets a share
    assert [m["satchel"]["coins"] for m in seen["members"]] == [20, 20, 20]
    assert [m["satchel"]["potions"] for m in seen["members"]] == [1, 1, 1]
    _walk(conn, code, trio, "w")
    _act(conn, code, trio[0], "vote", target="home")
    seen = _act(conn, code, trio[1], "vote", target="home")
    assert seen["phase"] == "lobby" and seen["last"]["how"] == "home"
    assert [m["ended"]["coins"] for m in seen["last"]["members"]] == [20, 20, 20]
    for uid in trio:
        assert coins.peek(conn, uid) == coins.START + 20
        assert lumipet.load(conn, uid, T0)["potions"] >= 1
    refs = conn.execute("SELECT count(*) FROM coin_transactions WHERE ref LIKE ?", (f"lumi-party:{code}:%",)).fetchone()[0]
    assert refs == 3


def test_the_vault_rolls_for_each_member_and_keys_are_shared(conn, trio):
    code = _party(conn, trio)
    _floor(conn, code, lock={"kind": "key"})
    _walk(conn, code, trio, "e")
    _walk(conn, code, trio, "s")
    seen = _walk(conn, code, trio, "s")                      # the key room under the game
    _act(conn, code, trio[1], "take")
    assert _state(conn, code)["run"]["keys"] == 1
    _walk(conn, code, trio, "n")
    _walk(conn, code, trio, "n")
    seen = _walk(conn, code, trio, "e")
    assert seen["run"]["facing"]["lock"] == {"kind": "key"} and seen["run"]["party"]["puzzle"] is None
    seen = _act(conn, code, trio[2], "unlock")
    assert seen["run"]["at"] == "2,0" and seen["run"]["satchel"]["keys"] == 0
    seen = _act(conn, code, trio[0], "take", rng=Dice(5))
    assert all(m["satchel"]["coins"] > 0 for m in seen["members"])


def test_the_page_never_sees_unvisited_rooms_or_answers(conn, trio):
    code = _party(conn, trio)
    seen = _act(conn, code, trio[0], "look")
    assert set(seen["run"]["rooms"]) == {"0,0", "1,0"} and seen["run"]["rooms"]["1,0"] == {"kind": "unknown"}
    _at_vault(conn, code, trio, {"kind": "runes", "code": ["sun", "moon", "star", "leaf"], "tries": []})
    seen = _act(conn, code, trio[0], "look")
    assert "code" not in seen["run"]["facing"]["lock"] and seen["run"]["facing"]["lock"]["kind"] == "runes"
    assert "2,1" not in seen["run"]["rooms"] and "prize" not in json.dumps(seen)


# ------------------------------------------------------------------ through the web app
def test_a_party_through_the_page(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    jane = TestClient(client.app)
    _login(jane, conn, directory, "jane", "jsmith")
    made = client.post("/api/media/games/lumi-pet/parties", json={"mode": "online"})
    assert made.status_code == 200, made.text
    code = made.json()["code"]
    base = f"/api/media/games/lumi-pet/parties/{code}"
    assert jane.get("/api/media/games/lumi-pet/parties").json()["parties"][0]["code"] == code
    assert jane.post(base, json={"action": "join"}).json()["you"] is not None
    for who in (client, jane):
        assert who.post(base, json={"action": "ready"}).status_code == 200
    started = client.post(base, json={"action": "start"}).json()
    assert started["phase"] == "trip"
    state = client.get("/api/media/games/lumi-pet/state").json()
    assert state["party"]["code"] == code and state["dungeon"]["run"]["party"]["code"] == code
    assert client.post(base, json={"action": "vote", "target": "<script>"}).status_code == 422
    assert jane.post("/api/media/games/lumi-pet/dungeon", json={"env": "moonlit-shrine"}).status_code == 400
    assert jane.post(base, json={"action": "abandon"}).status_code == 200
    assert jane.get(base).status_code == 200 and jane.get(base).json()["you"] is None


def test_a_couch_party_seats_each_remote_as_its_own_account(client, conn, directory):
    from test_lumi_tv_player import _couch
    from test_media import _hub, _remote
    owner_id, wife_id, tv, press, screen_id, remote_id = _couch(client, conn, directory)
    panel, auth, own_remote = _remote(client, "Doll's phone")              # the TV owner's own remote

    def doll_press(button="enter"):
        queue = _hub(client).listen(screen_id)
        try:
            assert panel.post(f"/api/media/remote/screens/{screen_id}/command", headers=auth,
                              json={"action": "press", "button": button}).status_code == 200
            sent = queue.get_nowait()
            assert sent["remote"] == own_remote                             # the TV learns which remote pressed
            return sent["controller"]
        finally:
            _hub(client).unlisten(screen_id, queue)

    made = tv.post("/api/media/games/lumi-pet/parties", json={"mode": "couch"})
    assert made.status_code == 200, made.text
    assert made.json()["display"] is True and made.json()["members"] == []
    base = f"/api/media/games/lumi-pet/parties/{made.json()['code']}"
    tap = lambda token, **body: tv.post(base + "/tv/input", json={"token": token, **body})   # noqa: E731

    token = press()
    seated = tap(token, action="ready")                      # an unseated remote's press sits it down first
    assert seated.status_code == 200, seated.text
    assert [(m["user"], m["couch"]) for m in seated.json()["members"]] == [(wife_id, True)]
    assert tap(token, action="ready").json()["revision"] == seated.json()["revision"]   # a replayed press: nothing
    assert tap(doll_press(), action="join").json()["members"][1]["user"] == owner_id
    for who in (press, doll_press):
        assert tap(who(), action="ready").status_code == 200
    started = tap(press(), action="start")                   # Jane sat first, so she is the host
    assert started.status_code == 200, started.text and started.json()["phase"] == "trip"
    assert started.json()["phase"] == "trip" and started.json()["members"][0]["remote"] == remote_id
    exits = started.json()["run"]["room"]["exits"]
    voted = tap(press(), action="move", way=exits[0])
    assert voted.json()["run"]["party"]["votes"] == {str(wife_id): exits[0]}
    assert tv.get(base).json()["display"] is True            # the TV looks as the party's display
    assert TestClient(client.app).post(base + "/tv/close").status_code == 401   # only the TV (its owner) closes it
    assert tv.post(base + "/tv/close").json()["closed"] is True
    assert tv.get(base).status_code == 404


def test_a_look_with_nothing_due_reads_without_the_write_lock(conn, trio, settings, monkeypatch):
    """Every open party tab and TV looks every couple of seconds. A look with nothing to catch up must not queue for
    the one write lock (they all did: "database is locked"); one whose clocks are due still takes it."""
    from mnemerynne import db
    from mnemerynne.web.games import _look_party

    code = _party(conn, trio)
    monkeypatch.setattr(P, "_ms", lambda: T0 + S)            # a second later: nothing due yet
    assert not P.due(conn, code, T0 + S)
    other = db.connect(settings.db_path)
    other.execute("BEGIN IMMEDIATE")                         # someone else holds the write lock
    try:
        conn.execute("PRAGMA busy_timeout = 200")            # would fail fast if the look wanted the lock
        seen = _look_party(conn, code, trio[0], None)
        assert seen["code"] == code and seen["you"] is not None
    finally:
        other.execute("ROLLBACK")
        other.close()
        conn.execute("PRAGMA busy_timeout = 10000")
    later = T0 + P.IDLE_MS + S                               # long idle: due, so the look takes the lock and acts
    monkeypatch.setattr(P, "_ms", lambda: later)
    assert P.due(conn, code, later)
    with pytest.raises(P.PartyError):                        # an idle party breaks up (written under the lock)
        _look_party(conn, code, trio[0], None)
