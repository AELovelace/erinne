"""Media → Games: the built-in games (mnemerynne/games.py). The list for Media's Games view, and each game's page,
which Media's game layer frames (media.js games). Both sit behind Media's own guard (media._media_on): Media on,
and the person (or the TV's owner) in the Media group."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from mnemerynne import arcade, lobby, lumisoft, runner, showdown
from mnemerynne.rooms_bus import sse
from mnemerynne import bfgguf, casino, casino_remote, claude_chat, coins, games, lumi_tv, lumidungeon, lumiparty, lumipet, lumitales, prism, push, users
from mnemerynne.db import read_transaction, transaction
from mnemerynne.media import remotes, screens
from mnemerynne.web.deps import _session_user, feature_pool, get_games_db, release_db
from mnemerynne.web.media import _media_on, _screen, media_user, screen_device

STATIC = Path(__file__).parent / "static"


def _uncached(response: Response):
    """A shared URL still represents this caller's seat and the current round, never a cached snapshot."""
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(dependencies=[Depends(_media_on), Depends(_uncached)])


@router.get("/api/media/games")
def media_games(user=Depends(media_user)):
    """The games Media → Games shows, for a signed-in person or a paired screen (the TV)."""
    return {"games": games.listing()}


@router.get("/api/media/games/lumi-look")
def lumi_look(user=Depends(media_user), conn=Depends(get_games_db)):
    """How this account's Lumi looks (glow and outfit), for the arcade games to draw her. Only reads her save."""
    return arcade.lumi_look(conn, user["id"])


@router.get("/api/media/games/coins")
def coin_balance(user=Depends(media_user), conn=Depends(get_games_db)):
    """This account's coins (a paired TV shows its owner's). Also brings home stakes from any table that has expired."""
    casino.sweep(conn)
    return {"coins": coins.peek(conn, user["id"])}


# ------------------------------------------------- Who's playing? (lumi_tv.py)
# A TV is paired to one account, but in the Lumi apps each person plays their own pet there: a remote's OK press is
# swapped for a play pass, and the Lumi routes below load the pass holder's account instead of the TV owner's.

def _pass_holder(request: Request, conn, token: str):
    """The person a play pass is for, if it is still good on this TV: the pass is signed and fresh, it was made for this
    very screen (and pairing), and its remote may still drive this TV (_live_remote). None otherwise."""
    screen = _screen(request, conn)
    if screen is None or screen["speaker"]:
        return None                                                   # only a TV page can play with a pass
    try:
        held = lumi_tv.read(request.app.state.settings.secret_key, token)
    except lumi_tv.BadData:
        return None                                                   # tampered with, or older than PASS_SECONDS
    display = casino_remote.actor("screen", screen)
    if held.get("screen") != screen["id"] or held.get("display") != display:
        return None                                                   # made for another TV, or this TV was re-paired
    remote = _live_remote(request, conn, {"remote": held["remote"], "screen": display, "pairing": held["pairing"]},
                          screen, display)
    if remote is None or remote["user_id"] != held["user"]:
        return None                                                   # re-paired, removed, or no longer a guest
    return users.get_user_by_id(conn, held["user"])


def pet_user(request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    """Whose Lumi this request is about: the signed-in person or the TV's owner (media_user), unless the TV page
    sends a play pass (lumi_tv.HEADER), when it's the person holding the remote. A pass that no longer holds is
    refused (403), never quietly swapped for the TV owner's pet: the page then asks Who's playing? again."""
    token = request.headers.get(lumi_tv.HEADER)
    if not token or _session_user(request, conn) is not None:
        return user                                                   # no pass, or someone signed in on this browser
    holder = _pass_holder(request, conn, token[:2000])
    if holder is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Who's playing? Press OK on your remote to carry on.")
    return holder


class PlayerPick(BaseModel):
    """A remote's OK press, as the TV page received it: the server's own signed proof (casino_remote.issue)."""
    model_config = ConfigDict(extra="forbid")
    controller: str = Field(min_length=1, max_length=2000)


@router.post("/api/media/games/lumi-pet/tv/player")
def pet_tv_player(body: PlayerPick, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    """Who's playing? The TV page hands over a remote's OK press; the person holding that remote (the TV's owner, or
    one of their TV guests) gets a play pass for their own Lumi on this TV."""
    screen = screen_device(request, conn)
    if screen["speaker"] or screen["user_id"] != user["id"] or _session_user(request, conn) is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Who's playing? is for a TV itself, with nobody signed in on it.")
    try:
        proof = casino_remote.read(request.app.state.settings.secret_key, body.controller)
    except casino_remote.BadData:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That press is too old. Press OK on your remote again.") from None
    if proof.get("action") != "enter":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Press OK on your remote to play.")
    display = casino_remote.actor("screen", screen)
    remote = _live_remote(request, conn, proof, screen, display)
    if remote is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This remote can't play on this TV. The TV's owner can make you "
                                                       "a TV guest under Media → Screens.")
    holder = users.get_user_by_id(conn, remote["user_id"])
    token = lumi_tv.issue(request.app.state.settings.secret_key, screen, remote, holder["id"])
    return {"pass": token, "name": holder["username"], "owner": holder["id"] == screen["user_id"],
            "idle": lumi_tv.IDLE_SECONDS}


class PrismDrop(BaseModel):
    """The player only says which pocket: the server rolls the drop and keeps the score."""
    model_config = ConfigDict(extra="forbid")
    pick: int = Field(ge=1, le=prism.WIDTH, strict=True)


@router.get("/api/media/games/prism-drop/run")
def prism_run(user=Depends(media_user), conn=Depends(get_games_db)):
    """The game in progress, so a reload or another device picks it up."""
    return prism.current(conn, user["id"])


@router.post("/api/media/games/prism-drop/drop")
def prism_drop(body: PrismDrop, user=Depends(media_user), conn=Depends(get_games_db)):
    """One drop; the tenth finishes the game and pays its coins."""
    return prism.play(conn, user["id"], body.pick)


class RunnerFinish(BaseModel):
    """What the page says one StickBob Runner run did. runner.finish checks it against the run's start before paying."""
    model_config = ConfigDict(extra="forbid")
    run: int = Field(ge=1, strict=True)                     # the ticket from /start
    meters: int = Field(ge=0, le=10_000_000, strict=True)
    kills: int = Field(ge=0, le=1_000_000, strict=True)


@router.post("/api/media/games/stickbob-runner/start")
def runner_start(user=Depends(media_user), conn=Depends(get_games_db)):
    """A ticket for the run that's starting; the server notes the time."""
    with transaction(conn):
        return {"run": runner.start(conn, user["id"])}


@router.post("/api/media/games/stickbob-runner/finish")
def runner_finish(body: RunnerFinish, user=Depends(media_user), conn=Depends(get_games_db)):
    """Game over: pay the run's coins once, if its distance and kills fit the time it took."""
    with transaction(conn):
        return runner.finish(conn, user["id"], body.run, body.meters, body.kills)


# ------------------------------------------------------------------ Lumi-Soft (lumisoft.py)
def _soft_call(fn, *args, **kwargs):
    """A Lumi-Soft refusal becomes a small error the page can show; nothing was changed."""
    try:
        return fn(*args, **kwargs)
    except lumisoft.Refused as exc:
        raise HTTPException(exc.status, str(exc)) from exc


class SoftStart(BaseModel):
    """Which run to start. Never a score, a price or a balance."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["training", "story", "stage", "daily"]
    stage: str | None = Field(None, max_length=40)


class SoftRun(BaseModel):
    """A run's ticket, for a continue."""
    model_config = ConfigDict(extra="forbid")
    run: int = Field(ge=1, strict=True)


class SoftFinish(BaseModel):
    """A finished run's log, exactly what rules.js recorded: each shot's step and aim point, each reload's step, how many
    continues it used and the step it ended on. The server replays it; nothing here is a score."""
    model_config = ConfigDict(extra="forbid")
    run: int = Field(ge=1, strict=True)
    shots: list[tuple[Annotated[int, Field(ge=0, le=10_000_000)], Annotated[float, Field(ge=-5000, le=10_000)],
                      Annotated[float, Field(ge=-5000, le=10_000)]]] = Field(default_factory=list, max_length=20_000)
    reloads: list[Annotated[int, Field(ge=0, le=10_000_000)]] = Field(default_factory=list, max_length=10_000)
    continues: int = Field(default=0, ge=0, le=1000, strict=True)
    end: int = Field(ge=0, le=10_000_000, strict=True)


@router.get("/api/media/games/lumi-soft/state")
def soft_state(user=Depends(media_user), conn=Depends(get_games_db)):
    """The title screen: the stages, prices, this person's bests and prizes, and today's Daily Range with its board."""
    return lumisoft.state(conn, user["id"])


@router.post("/api/media/games/lumi-soft/start")
def soft_start(body: SoftStart, user=Depends(media_user), conn=Depends(get_games_db)):
    """A ticket for a new run (it costs lumisoft.ENTRY coins unless it's free practice)."""
    with transaction(conn):
        return _soft_call(lumisoft.start, conn, user["id"], body.mode, body.stage)


@router.post("/api/media/games/lumi-soft/continue")
def soft_continue(body: SoftRun, user=Depends(media_user), conn=Depends(get_games_db)):
    """Game over: pay for a continue (each costs more; the score stays)."""
    with transaction(conn):
        return _soft_call(lumisoft.carry_on, conn, user["id"], body.run)


@router.post("/api/media/games/lumi-soft/finish")
def soft_finish(body: SoftFinish, user=Depends(media_user), conn=Depends(get_games_db)):
    """The run is over: the server replays its shots, pays its score once, keeps bests and gives any prizes it won."""
    with transaction(conn):
        return _soft_call(lumisoft.finish, conn, user["id"], body.run, body.model_dump())


@router.post("/api/media/games/lumi-pet/visit")
def pet_visit(user=Depends(pet_user), conn=Depends(get_games_db)):
    """The first time someone spends a moment with Lumi today. Takes no numbers from the page, so there's nothing to fake."""
    with transaction(conn):
        paid = coins.pet_visit(conn, user["id"])
        return {"paid": paid, "coins": coins.peek(conn, user["id"])}


class PetAction(BaseModel):
    """What the person chose to do with Lumi. Only choices: which thing and how many. The server decides every result."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["cuddle", "feed", "play", "nap", "wake", "buy", "decorate", "wear", "capsule", "glow", "import", "storyteller",
                    "learn", "respec", "equip", "mod", "potions", "heal", "drink", "garden_reminders",
                    "unpack", "name", "colour", "stat", "stats_auto", "auto_stats", "stats_reset", "kit"]
    item: str | None = Field(default=None, max_length=40)
    count: int = Field(default=1, ge=1, le=lumipet.MAX_HELD, strict=True)
    moments: int = Field(default=0, ge=0, le=lumipet.MAX_MOMENTS, strict=True)   # only read by "import", once
    glow: str | None = Field(default=None, max_length=20)
    slot: int | None = Field(default=None, ge=0, le=9, strict=True)             # which mod slot, for "mod"
    name: str | None = Field(default=None, max_length=40)                       # what to call her, for "name"
    colour: str | None = Field(default=None, max_length=7)                      # "#rrggbb" or "" (her glow's own), for "colour"


@router.get("/api/media/games/lumi-pet/state")
def pet_state(user=Depends(pet_user), conn=Depends(get_games_db)):
    """Lumi as she is right now, caught up from the last time anyone looked. A paired TV shows its owner's pet."""
    with transaction(conn):
        return lumipet.state(conn, user["id"])


@router.post("/api/media/games/lumi-pet/act")
def pet_act(body: PetAction, user=Depends(pet_user), conn=Depends(get_games_db)):
    """One care action or purchase. A refusal (too soon, not enough coins) changes nothing and says why."""
    try:
        with transaction(conn):
            return lumipet.act(conn, user["id"], body.action, item=body.item, count=body.count, moments=body.moments,
                               glow=body.glow, slot=body.slot, name=body.name, colour=body.colour)
    except lumipet.PetError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None


class FightStart(BaseModel):
    """Which kind of opponent to look for. The server picks the enemy and its level."""
    model_config = ConfigDict(extra="forbid")
    tier: Literal["common", "uncommon", "rare", "epic", "legendary", "gamble"]


class FightTurn(BaseModel):
    """One button in a fight: which fight, the turn the page was showing, and the button. Never a number of damage."""
    model_config = ConfigDict(extra="forbid")
    battle: int = Field(ge=1, strict=True)
    turn: int = Field(ge=0, strict=True)
    action: Literal["move0", "move1", "move2", "move3", "defend", "potion", "ether", "x-attack", "x-defend", "x-speed", "run"]


@router.post("/api/media/games/lumi-pet/battle")
def pet_fight(body: FightStart, user=Depends(pet_user), conn=Depends(get_games_db)):
    """Start a fight. One at a time; it waits for as long as it takes."""
    try:
        with transaction(conn):
            return lumipet.fight_start(conn, user["id"], body.tier)
    except lumipet.PetError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None


@router.post("/api/media/games/lumi-pet/battle/act")
def pet_fight_turn(body: FightTurn, user=Depends(pet_user), conn=Depends(get_games_db)):
    """Play one turn. A stale or repeated press is refused and changes nothing."""
    try:
        with transaction(conn):
            return lumipet.fight_act(conn, user["id"], body.battle, body.turn, body.action)
    except lumipet.PetError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None


class DungeonStart(BaseModel):
    """Where to explore. The server makes the floor."""
    model_config = ConfigDict(extra="forbid")
    env: str = Field(min_length=1, max_length=40)


class DungeonStep(BaseModel):
    """One choice in the dungeon: which way, which cup or card or lamps, or the moves that solve a Sokoban door.
    Never a room, a reward or a result: the server replays puzzle answers itself."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["move", "look", "take", "back", "unlock", "sokoban", "lights", "rune", "cup", "flip", "potion", "descend", "leave", "abandon"]
    way: Literal["n", "e", "s", "w"] | None = None
    moves: str = Field(default="", max_length=600, pattern="^[udlr]*$")
    picks: list[int | str] = Field(default_factory=list, max_length=200)


@router.post("/api/media/games/lumi-pet/dungeon")
def pet_dungeon(body: DungeonStart, user=Depends(pet_user), conn=Depends(get_games_db)):
    """Set out on a dungeon trip. One at a time; it waits for as long as it takes."""
    try:
        with transaction(conn):
            return lumipet.dungeon_start(conn, user["id"], body.env)
    except lumipet.PetError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None


class GardenStep(BaseModel):
    """One choice in the Stardust Garden: which plot, which flower or helper, how many presses, or a watering answer.
    Never an amount of powder or coins: the server works those out."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["look", "press", "plot", "plant", "dig", "trim", "trim_all", "unlock", "decor_buy", "decor_place", "decor_sell", "capsule", "helper", "ability", "water_start", "water_answer",
                    "feed_start", "feed_end", "moonrise", "garden"]
    item: str | None = Field(default=None, max_length=40)
    plot: int | None = Field(default=None, ge=0, le=15, strict=True)
    count: int = Field(default=0, ge=0, le=2000, strict=True)                   # presses in this batch, or which garden to show
    picks: list[Literal[0, 1, 2, 3]] = Field(default_factory=list, max_length=12)   # the watering cans, in order


@router.post("/api/media/games/lumi-pet/garden")
def pet_garden(body: GardenStep, user=Depends(pet_user), conn=Depends(get_games_db)):
    """Tend the garden or work the press."""
    try:
        with transaction(conn):
            return lumipet.garden_act(conn, user["id"], body.action, item=body.item, plot=body.plot, count=body.count, picks=body.picks)
    except lumipet.PetError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None


@router.post("/api/media/games/lumi-pet/dungeon/act")
def pet_dungeon_step(body: DungeonStep, user=Depends(pet_user), conn=Depends(get_games_db)):
    """Take one step, or make one choice, in the dungeon."""
    try:
        with transaction(conn):
            return lumipet.dungeon_act(conn, user["id"], body.action, way=body.way, moves=body.moves, picks=body.picks)
    except lumipet.PetError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None


# ------------------------------------------------------- Lumi Dungeons with friends (lumiparty.py)
# A party of up to three, each with their own Lumi, on one shared floor. Online, each member plays from their own
# device (pet_user: a TV plays as its Who's playing? pass). On the couch the TV owns the party and every remote's
# press arrives signed (party_tv_input), as its holder's own member.

class PartyCreate(BaseModel):
    """Online (each on their own device) or couch (one TV, the remotes around it)."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["online", "couch"] = "online"


PARTY_ACTIONS = Literal["join", "leave", "ready", "env", "start", "vote", "move", "back", "descend", "take", "look", "unlock",
                        "potion", "sokoban", "lights", "rune", "cup", "flip", "pass", "abandon", "fight"]
# What a co-op fight turn (action "fight") may be: a move, Defend, Run, or an item (lumiparty.FIGHT).
FIGHT_TARGET = "move[0-3]|defend|run|potion|ether|x-attack|x-defend|x-speed"


class PartyMove(BaseModel):
    """One choice at the party: a vote, a step that is a vote, a puzzle move, or the lobby's buttons. Never a result."""
    model_config = ConfigDict(extra="forbid")
    action: PARTY_ACTIONS
    way: Literal["n", "e", "s", "w"] | None = None
    target: str = Field(default="", max_length=20, pattern="^(|[nesw]|back|descend|home|m[0-9]{1,12}|" + FIGHT_TARGET + ")$")
    item: str = Field(default="", max_length=40)
    moves: str = Field(default="", max_length=600, pattern="^[udlr]*$")
    picks: list[int | str] = Field(default_factory=list, max_length=200)
    foe: int | None = Field(default=None, ge=0, le=2, strict=True)              # a fight move: which creature it is aimed at
    ally: int | None = Field(default=None, ge=0, le=2, strict=True)             # a fight item: which Lumi (by seat) it is for
    turn: int | None = Field(default=None, ge=0, strict=True)                   # a fight turn: the one the page was showing


class PartyPress(BaseModel):
    """A couch remote's press, relayed by the TV: the server's own signed proof of the press, and what that member
    chose with it (their cursor lives on the TV). A fight turn says which turn the TV was showing."""
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=2000)
    action: PARTY_ACTIONS | Literal["fight"]
    way: Literal["n", "e", "s", "w"] | None = None
    target: str = Field(default="", max_length=20, pattern="^(|[nesw]|back|descend|home|m[0-9]{1,12}|" + FIGHT_TARGET + ")$")
    item: str = Field(default="", max_length=40)
    moves: str = Field(default="", max_length=600, pattern="^[udlr]*$")
    picks: list[int | str] = Field(default_factory=list, max_length=200)
    turn: int = Field(default=0, ge=0, strict=True)
    foe: int | None = Field(default=None, ge=0, le=2, strict=True)
    ally: int | None = Field(default=None, ge=0, le=2, strict=True)


def _party_call(fn, *args, **kwargs):
    """A refusal becomes a small error the page can show; nothing was changed."""
    try:
        return fn(*args, **kwargs)
    except lumiparty.PartyError as exc:
        raise HTTPException(exc.status, str(exc)) from None


def _look_party(conn, code: str, user_id: int | None, display: str | None) -> dict:
    """A party look, taking the write lock only when the party's clocks are due. Every open party tab and TV looks
    every couple of seconds; with each look holding the one write lock, they queued for it ("database is locked")."""
    now = lumiparty._ms()                              # one moment for the check and the look, so they agree
    with read_transaction(conn):                       # no write lock: just a consistent read
        if not lumiparty.due(conn, code, now):
            return lumiparty.look(conn, code, user_id, now=now, display=display)   # nothing to catch up: read only
    with transaction(conn):                            # its clocks are due: the write lock, and look again inside it
        return lumiparty.look(conn, code, user_id, display=display)


def _party_display(request: Request, conn, owner) -> str | None:
    """This browser's couch identity: the TV itself (its screen key), as its owner (also with its owner signed in on its top
    bar, as _tv_identity allows). None otherwise."""
    screen = _screen(request, conn)
    if screen is None or screen["speaker"] or screen["user_id"] != owner["id"]:
        return None
    return casino_remote.actor("screen", screen)


@router.get("/api/media/games/lumi-pet/parties")
def party_list(user=Depends(pet_user), conn=Depends(get_games_db)):
    """Parties waiting in their lobby, and this person's own."""
    with transaction(conn):
        lumiparty.sweep(conn)
    return {"parties": lumiparty.parties(conn, user["id"]), "mine": lumiparty.code_of(conn, user["id"])}


@router.post("/api/media/games/lumi-pet/parties")
def party_create(request: Request, body: PartyCreate | None = None, user=Depends(pet_user), owner=Depends(media_user),
                 conn=Depends(get_games_db)):
    """A new party: online with this person as its host, or a couch party owned by this TV."""
    now = lumipet.now_ms()
    with transaction(conn):
        if body is not None and body.mode == "couch":
            screen, display = _tv_identity(request, conn, owner)        # only the TV itself, as its owner
            state = _party_call(lumiparty.create, conn, None, "", now, mode="couch", display=display, screen=screen["name"])
            return lumiparty.view(conn, state, None, now=now, display=True)
        state = _party_call(lumiparty.create, conn, user["id"], user["username"], now)
        return lumiparty.view(conn, state, user["id"], now=now)


@router.get("/api/media/games/lumi-pet/parties/{code}")
def party_look(code: str, request: Request, user=Depends(pet_user), owner=Depends(media_user), conn=Depends(get_games_db)):
    """The party as this member, or its couch TV, sees it."""
    display = _party_display(request, conn, owner)
    return _party_call(_look_party, conn, code, user["id"], display)   # the write lock only when due


@router.post("/api/media/games/lumi-pet/parties/{code}")
def party_move(code: str, body: PartyMove, user=Depends(pet_user), conn=Depends(get_games_db)):
    """One thing this member does at the party (or joining it)."""
    with transaction(conn):
        return _party_call(lumiparty.act, conn, code, user["id"], body.action, name=user["username"], way=body.way,
                           target=body.target, item=body.item, moves=body.moves, picks=body.picks,
                           foe=body.foe, ally=body.ally, turn=body.turn)


@router.post("/api/media/games/lumi-pet/parties/{code}/tv/input")
def party_tv_input(code: str, body: PartyPress, request: Request, owner=Depends(media_user), conn=Depends(get_games_db)):
    """A couch remote's press. The proof says which remote pressed, for this very TV; the remote's own account (the
    TV owner's, or one of their TV guests') is the member who acts, with their own Lumi."""
    screen, display = _tv_identity(request, conn, owner)
    try:
        proof = casino_remote.read(request.app.state.settings.secret_key, body.token)
    except casino_remote.BadData:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That press is too old. Press again.") from None
    remote = _live_remote(request, conn, proof, screen, display)
    if remote is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This remote can't play on this TV.")
    holder = users.get_user_by_id(conn, remote["user_id"])
    fields = {"way": body.way, "target": body.target, "item": body.item, "moves": body.moves, "picks": body.picks}
    if body.action == "fight":
        fields |= {"foe": body.foe, "ally": body.ally}      # a co-op fight turn: aimed at a creature, or an item for a friend
    with transaction(conn):
        return _party_call(lumiparty.couch, conn, code, display, user_id=holder["id"], name=holder["username"],
                           remote=remote["id"], press=proof["id"], action=body.action, turn=body.turn, **fields)


@router.post("/api/media/games/lumi-pet/parties/{code}/tv/close")
def party_tv_close(code: str, request: Request, owner=Depends(media_user), conn=Depends(get_games_db)):
    """The couch TV ends its party (a trip still out is given up)."""
    _, display = _tv_identity(request, conn, owner)
    with transaction(conn):
        return _party_call(lumiparty.close, conn, code, display)


@router.get("/api/media/games/lumi-pet/parties/{code}/stream")
async def party_stream(code: str, request: Request, user=Depends(pet_user), owner=Depends(media_user), conn=Depends(get_games_db)):
    """The party, live: "party" whenever it changes (and every few seconds while its clocks run), a ping every 15 s,
    and "end" once it has broken up or this member has left. While it's open the member (or the couch TV) counts
    as there."""
    display = _party_display(request, conn, owner)
    pool = feature_pool(request.app, "games")

    def look():
        c = pool.get()
        try:
            return _look_party(c, code, user["id"], display)      # every 2 s per open stream: lock only when due
        finally:
            pool.put(c)

    try:
        first = await asyncio.to_thread(look)
    except lumiparty.PartyError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    if first.get("you") is None and not first.get("display"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join this party first.")
    actor = display if first.get("display") else f"user:{user['id']}"

    async def events():
        table = ("lumi-party", first["code"])
        lobby.PRESENCE.opened(table, actor)
        wake = asyncio.Event()
        lumiparty.HUB.listen(first["code"], wake)
        try:
            yield sse("party", first)
            shown = (first["revision"], [m["away"] for m in first["members"]])
            seen = lumiparty.HUB.version(first["code"])[0]
            looked = beat = time.monotonic()
            while True:
                try:
                    await asyncio.wait_for(wake.wait(), timeout=2)
                except TimeoutError:
                    pass
                wake.clear()
                if await request.is_disconnected():
                    return
                version = lumiparty.HUB.version(first["code"])[0]
                if version != seen or time.monotonic() - looked >= 2:      # clocks (votes, turns) need a look too
                    seen, looked = version, time.monotonic()
                    try:
                        now = await asyncio.to_thread(look)
                    except lumiparty.PartyError:
                        yield sse("end", {"reason": "The party has broken up."})
                        return
                    if now.get("you") is None and not now.get("display"):
                        yield sse("end", {"reason": "You've left the party."})
                        return
                    mark = (now["revision"], [m["away"] for m in now["members"]])
                    if mark != shown:
                        shown = mark
                        yield sse("party", now)
                if time.monotonic() - beat >= 15:
                    beat = time.monotonic()
                    yield b": ping\n\n"
        finally:
            lumiparty.HUB.unlisten(first["code"], wake)
            lobby.PRESENCE.closed(table, actor)

    release_db(request)     # the stream's look() borrows its own connection: don't hold this one while it stays open
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


TALES_A_MINUTE = 12                     # live lines one person may ask for; a room is one line, so this is generous
_tale_times: dict[int, list[float]] = {}


def _storyteller(request: Request, conn, user) -> tuple[str, object] | None:
    """Who can write live lines for this person: ("claude", (key, model)) with their own saved key, or
    ("bfgguf", config) if they are in that group and it is set up. None: nobody, and the library carries on alone."""
    own = claude_chat.credentials(conn, request.app.state.settings, user["id"])
    if own:
        return "claude", own
    if bfgguf.available(conn, user, request.app.state.directory) == "ok":
        return "bfgguf", bfgguf.load_config(conn)
    return None


@router.get("/api/media/games/lumi-pet/dungeon/tale")
def pet_tale_source(request: Request, user=Depends(pet_user), conn=Depends(get_games_db)):
    """Whether a live storyteller is there for this person, and which, so Settings can say."""
    found = _storyteller(request, conn, user)
    return {"live": found[0] if found else None}


@router.post("/api/media/games/lumi-pet/dungeon/tale")
async def pet_tale(request: Request, user=Depends(pet_user), conn=Depends(get_games_db)):
    """A freshly written line for the moment Lumi is in, in live mode. Takes nothing from the page: the moment is the
    run's own. Answers {"line": null} whenever there is nothing new to say (the library line simply stays)."""
    run = lumidungeon.current(conn, user["id"])
    found = _storyteller(request, conn, user)
    if run is None or found is None or run.get("said_for") == run["id"]:
        return {"line": None}
    recent = [t for t in _tale_times.get(user["id"], []) if t > time.monotonic() - 60]
    if len(recent) >= TALES_A_MINUTE:
        return {"line": None}
    _tale_times[user["id"]] = recent + [time.monotonic()]
    moment = run["event"]
    if found[0] == "claude":
        told = await lumitales.live_line(run, claude=found[1])
    else:
        client = bfgguf.client(request.app.state.settings, found[1], request.app.state.balancer, getattr(request.app.state, "bfgguf_transport", None))
        try:
            told = await lumitales.live_line(run, bfgguf=client)
        finally:
            await client.aclose()
    with transaction(conn):                               # the run may have moved on while the model was writing
        now = lumidungeon.current(conn, user["id"])
        if told is None or now is None or now["event"] != moment:
            return {"line": None}
        now["said_for"] = now["id"]
        lumitales.remember(conn, user["id"], now, told)
    return {"line": told}


class PushDevice(BaseModel):
    """A browser that said yes to notifications: where its push service is, and the keys to seal messages for it."""
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=10, max_length=2000)
    p256dh: str = Field(default="", max_length=200)
    auth: str = Field(default="", max_length=100)
    utc_offset: int = Field(default=0, ge=-14 * 60, le=14 * 60, strict=True)   # minutes east of UTC, for quiet hours


@router.get("/api/media/games/lumi-pet/push")
def pet_push_key(request: Request, user=Depends(media_user)):
    """The key a browser needs to subscribe to this server's notifications."""
    return {"key": push.public_key(request.app.state.settings.data_dir)}


@router.post("/api/media/games/lumi-pet/push")
def pet_push_on(body: PushDevice, user=Depends(media_user), conn=Depends(get_games_db)):
    """Lumi may send this device reminders. Asking again from the same browser refreshes it."""
    try:
        with transaction(conn):
            push.subscribe(conn, user["id"], body.endpoint, body.p256dh, body.auth, body.utc_offset)
    except push.PushError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None
    return {"on": True}


@router.post("/api/media/games/lumi-pet/push/off")
def pet_push_off(body: PushDevice, user=Depends(media_user), conn=Depends(get_games_db)):
    """No more reminders on this device."""
    with transaction(conn):
        push.unsubscribe(conn, user["id"], body.endpoint)
    return {"on": False}


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    """The service worker has to be served from the top of the site to receive pushes for it. It handles pushes only:
    it has no fetch handler, so it can never change how a page loads."""
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@router.get("/media/games/{slug}", response_class=HTMLResponse)
def game_page(slug: str, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    """A game's page: its canvas and its scripts (static/games/<slug>/), in the person's theme. No `user` in the
    template, so the page carries none of the app's own scripts (messages, notifications) inside the game frame."""
    game = games.get(slug)
    if game is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such game")
    paired = _screen(request, conn)
    tv_play = paired is not None and not paired["speaker"] and paired["user_id"] == user["id"] and screens.may_use(conn, user, request.app.state.directory)
    # Who's playing? (lumi_tv.py): a Lumi app on a TV whose owner has TV guests asks whose Lumi to show, by a remote's OK.
    who_plays = (tv_play and game.kit == "lumi-pet" and _session_user(request, conn) is None
                 and bool(screens.guests(conn, user["id"])))
    return request.app.state.templates.TemplateResponse(request, "game.html", {"game": game, "tv_play": tv_play,
                                                                               "who_plays": who_plays})


class TableCreate(BaseModel):
    """TV mode is explicit so existing single-seat and cross-device tables keep their controls."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["individual", "tv"] = "individual"


class TVInput(BaseModel):
    """The screen relays a signed key, never an arbitrary remote ID, balance or wager."""
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=2000)
    round: int = Field(ge=1, strict=True)
    phase: Literal["betting", "playing", "results"]


class TVManage(BaseModel):
    """Only the owning TV may explicitly remove a seat or end its whole table."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["remove", "close"]
    seat: str = Field(default="", max_length=32)
    revision: int = Field(ge=1, strict=True)


def _live_remote(request, conn, proof, screen, display):
    """The remote a verified press came from, if it may still drive this TV: the press was made for this screen, the
    remote hasn't been re-paired since, and it is the TV owner's own or a TV guest's (screens.drivable) whose account
    is still in the screens group. None otherwise."""
    remote = remotes.get(conn, proof["remote"])
    if remote is None or proof["screen"] != display or proof["pairing"] != casino_remote.pairing(remote):
        return None                                                     # removed, re-paired, or meant for another TV
    if screens.drivable(conn, remote["user_id"], screen["id"]) is None:
        return None                                                     # no longer a guest of this TV's owner
    holder = users.get_user_by_id(conn, remote["user_id"])
    if holder is None or not holder["active"] or not screens.may_use(conn, holder, request.app.state.directory):
        return None                                                     # their account left the screens group
    return remote


def _tv_identity(request, conn, user):
    """Check the live screen pairing and its owner even when someone has signed in on the TV."""
    screen = screen_device(request, conn)
    if screen["user_id"] != user["id"] or screen["speaker"]:
        raise HTTPException(403, "Use this TV as its owner to host remote players.")
    return screen, casino_remote.actor("screen", screen)


class TableMove(BaseModel):
    """Only player intentions enter the rules engine; cards and payouts never do."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["join", "bet", "start", "hit", "stand", "leave", "refill"]
    revision: int | None = Field(default=None, ge=1, strict=True)
    amount: int = Field(default=0, ge=0, le=500, strict=True)
    choice: str = Field(default="", max_length=12)


def _table_identity(request, conn, user):
    """Give each paired TV its own seat, while signed-in browsers play as their account."""
    if _session_user(request, conn) is None:
        screen = _screen(request, conn)
        if screen is not None:
            return f"screen:{screen['id']}:{user['id']}", screen["name"]
    return f"user:{user['id']}", user["username"]


def _casino_game(slug):
    """Keep table endpoints scoped to the four multiplayer registry entries."""
    if slug not in casino.GAMES:
        raise HTTPException(404, "No multiplayer tables for this game")


def _casino_call(fn, *args, **kwargs):
    """Translate a rules refusal into a small client-visible API error."""
    try:
        return fn(*args, **kwargs)
    except casino.Refused as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/api/media/games/{slug}/rooms")
def table_list(slug: str, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    _casino_game(slug)
    actor, _ = _table_identity(request, conn, user)
    paired = _screen(request, conn)
    display = None
    if paired is not None and not paired["speaker"] and paired["user_id"] == user["id"] and screens.may_use(conn, user, request.app.state.directory):
        display = casino_remote.actor("screen", paired)  # A fresh tab can resume its TV without consuming a player seat.
    return {"rooms": casino.rooms(conn, slug, actor, display)}


@router.post("/api/media/games/{slug}/rooms")
def table_create(slug: str, request: Request, body: TableCreate | None = None,
                 user=Depends(media_user), conn=Depends(get_games_db)):
    _casino_game(slug)
    if body is not None and body.mode == "tv":
        screen, actor = _tv_identity(request, conn, user)
        return _casino_call(casino.create, conn, slug, actor, screen["name"], couch=True)
    actor, name = _table_identity(request, conn, user)
    return _casino_call(casino.create, conn, slug, actor, name, account=user["id"])   # a TV here is its paired account


@router.get("/api/media/games/{slug}/rooms/{code}")
def table_state(slug: str, code: str, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    _casino_game(slug)
    actor, name = _table_identity(request, conn, user)
    return _casino_call(casino.room, conn, slug, code, actor, name, account=user["id"])


@router.post("/api/media/games/{slug}/rooms/{code}")
def table_move(slug: str, code: str, body: TableMove, request: Request,
               user=Depends(media_user), conn=Depends(get_games_db)):
    _casino_game(slug)
    actor, name = _table_identity(request, conn, user)
    return _casino_call(casino.room, conn, slug, code, actor, name, account=user["id"], **body.model_dump())


@router.get("/api/media/games/{slug}/rooms/{code}/tv")
def tv_table(slug: str, code: str, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    _casino_game(slug)
    _, actor = _tv_identity(request, conn, user)
    return _casino_call(casino.couch, conn, slug, code, actor)


@router.post("/api/media/games/{slug}/rooms/{code}/tv/input")
def tv_input(slug: str, code: str, body: TVInput, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    _casino_game(slug)
    screen, display = _tv_identity(request, conn, user)
    try:
        proof = casino_remote.read(request.app.state.settings.secret_key, body.token)
    except casino_remote.BadData as exc:
        raise HTTPException(403, "That remote input expired or could not be verified. Press again.") from exc
    remote = _live_remote(request, conn, proof, screen, display)        # the owner's remote, or a TV guest's
    if remote is None:
        raise HTTPException(403, "This remote is no longer paired to this TV's owner.")
    command = {"id": proof["id"], "action": proof["action"], "actor": casino_remote.actor("remote", remote), "name": remote["name"]}
    return _casino_call(casino.couch, conn, slug, code, display, command=command,
                        expected_round=body.round, expected_phase=body.phase)


@router.post("/api/media/games/{slug}/rooms/{code}/tv/manage")
def tv_manage(slug: str, code: str, body: TVManage, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    _casino_game(slug)
    _, actor = _tv_identity(request, conn, user)
    return _casino_call(casino.couch, conn, slug, code, actor, close=body.action == "close",
                        remove=body.seat if body.action == "remove" else None, revision=body.revision)


# ------------------------------------------------------- Breakout and Snake
# Ported from Doll's GameMaker arcade (arcade.py, showdown.py). A solo run costs arcade.ENTRY coins and pays its score
# once the server has checked it; a showdown table seats two racers and up to five watchers, who see both boards live
# through the table's stream. The stream is also how the server knows a player is still there (lobby.PRESENCE).

def _arcade_game(slug):
    """Keep these endpoints to the two arcade games."""
    if slug not in arcade.GAMES:
        raise HTTPException(404, "No such arcade game")


def _arcade_call(fn, *args, **kwargs):
    """A refusal becomes a small error the page can show; nothing was changed."""
    try:
        return fn(*args, **kwargs)
    except arcade.Refused as exc:
        raise HTTPException(exc.status, str(exc)) from exc


class RunFinish(BaseModel):
    """What a finished run claims. Snake sends the moves it turned on and the server plays the game again; Breakout
    sends its score and level, which the server checks against the levels it builds from the run's seed."""
    model_config = ConfigDict(extra="forbid")
    run: int = Field(ge=1, strict=True)
    moves: int = Field(default=0, ge=0, le=200_000, strict=True)                  # Snake: how many moves it made
    turns: list[tuple[int, Annotated[int, Field(ge=0, le=3)]]] = Field(default_factory=list, max_length=50_000)
    died: bool = False                                                            # Snake: the last move killed it
    score: int = Field(default=0, ge=0, le=1_000_000, strict=True)                # Breakout: bricks; Teacup Tower: floor
    level: int = Field(default=1, ge=1, le=500, strict=True)                      # Breakout: the level it ended on


@router.post("/api/media/games/{slug}/run")
def arcade_run(slug: str, user=Depends(media_user), conn=Depends(get_games_db)):
    """Start a solo run: it costs arcade.ENTRY coins, and hands back the seed the game is built from."""
    _arcade_game(slug)
    with transaction(conn):
        started = _arcade_call(arcade.start, conn, user["id"], slug)
        return {**started, "coins": coins.peek(conn, user["id"])}


@router.post("/api/media/games/{slug}/run/finish")
def arcade_finish(slug: str, body: RunFinish, user=Depends(media_user), conn=Depends(get_games_db)):
    """A run ended: the server checks it and pays a solo run its score; a race's result goes to its table."""
    _arcade_game(slug)
    with transaction(conn):
        result = _arcade_call(arcade.finish, conn, user["id"], slug, body.run, body.model_dump())
        if result["room"]:
            showdown.record(conn, slug, result["room"], body.run, result)
        return result


class ShowdownMove(BaseModel):
    """Only what a player chose: sit down, get up, get ready, and which racer a bet is on. Never a score or a balance."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["join", "leave", "ready", "seat", "stand", "bet"]
    target: Literal["", "p1", "p2"] = ""
    amount: int = Field(default=0, ge=0, le=showdown.BET_LIMIT, strict=True)


Unit = Annotated[float, Field(ge=0, le=1)]


class Board(BaseModel):
    """A racer's live board, for the others to watch. Only ever drawn, never scored: the result comes from the run."""
    model_config = ConfigDict(extra="forbid")
    score: int = Field(default=0, ge=0, le=1_000_000, strict=True)
    level: int = Field(default=0, ge=0, le=500, strict=True)                      # Breakout
    lives: int = Field(default=0, ge=0, le=9, strict=True)
    bat: Unit = 0.5
    wide: bool = False
    balls: list[tuple[Unit, Unit]] = Field(default_factory=list, max_length=8)
    bricks: str = Field(default="", max_length=108, pattern="^[0-6]*$")           # 18 x 6, colour codes
    length: int = Field(default=0, ge=0, le=400, strict=True)                     # Snake
    alive: bool = True
    points: list[Unit] = Field(default_factory=list, max_length=600)              # body, head first, x and y pairs
    food: list[Annotated[int, Field(ge=0, le=20)]] = Field(default_factory=list, max_length=3)   # column, row, kind
    kinds: str = Field(default="", max_length=400, pattern="^[1-4]*$")            # Lumi Conga: each dancer's colour, head first
    height: float = Field(default=0, ge=0, le=1e9)                                # Teacup Tower: how far up the view is
    frame: int = Field(default=0, ge=0, le=100_000_000, strict=True)              # ...and its frame, for the sliding cups


@router.get("/api/media/games/{slug}/tables")
def showdown_list(slug: str, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    """This game's showdown tables."""
    _arcade_game(slug)
    actor, _ = _table_identity(request, conn, user)
    return {"tables": showdown.tables(conn, slug, actor)}


@router.post("/api/media/games/{slug}/tables")
def showdown_create(slug: str, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    """A new table, with whoever opened it in the P1 seat."""
    _arcade_game(slug)
    actor, name = _table_identity(request, conn, user)
    return _arcade_call(showdown.create, conn, slug, actor, name, user["id"])


@router.get("/api/media/games/{slug}/tables/{code}")
def showdown_look(slug: str, code: str, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    """A table as this player sees it."""
    _arcade_game(slug)
    actor, _ = _table_identity(request, conn, user)
    return _arcade_call(showdown.look, conn, slug, code, actor)


@router.post("/api/media/games/{slug}/tables/{code}")
def showdown_move(slug: str, code: str, body: ShowdownMove, request: Request, user=Depends(media_user),
                  conn=Depends(get_games_db)):
    """One thing a player does at the table."""
    _arcade_game(slug)
    actor, name = _table_identity(request, conn, user)
    return _arcade_call(showdown.act, conn, slug, code, actor, name, user["id"], body.action,
                        target=body.target, amount=body.amount)


@router.post("/api/media/games/{slug}/tables/{code}/board", status_code=status.HTTP_204_NO_CONTENT)
def showdown_board(slug: str, code: str, body: Board, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    """A racer's live board, a few times a second. Kept in memory only; refused once their race is over for them."""
    _arcade_game(slug)
    actor, _ = _table_identity(request, conn, user)
    seat = showdown.racing_seat(conn, slug, code, actor)
    if seat is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You aren't racing at this table right now.")
    lobby.PRESENCE.seen(("arcade", code), actor)
    showdown.HUB.push(code, seat, body.model_dump())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/media/games/{slug}/tables/{code}/stream")
async def showdown_stream(slug: str, code: str, request: Request, user=Depends(media_user), conn=Depends(get_games_db)):
    """The table, live, for a seated player: "table" whenever it changes, "boards" as the racers play (at most about
    eight times a second), a ping every 15 s, and "end" if the seat is gone. While it's open the player counts as
    there; once it closes they have showdown.DROP_SECONDS to come back."""
    _arcade_game(slug)
    actor, _ = _table_identity(request, conn, user)
    pool = feature_pool(request.app, "games")

    def look():
        c = pool.get()
        try:
            return showdown.look(c, slug, code, actor)
        finally:
            pool.put(c)

    try:
        first = await asyncio.to_thread(look)
    except arcade.Refused as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    if first.get("you") is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join this table first.")

    async def events():
        table = ("arcade", code)
        lobby.PRESENCE.opened(table, actor)
        wake = asyncio.Event()
        showdown.HUB.listen(code, wake)
        try:
            yield sse("table", first)
            shown = {k: v for k, v in first.items() if k != "server_time"}
            seen_table, seen_boards = showdown.HUB.version(code)
            looked = beat = time.monotonic()
            while True:
                try:
                    await asyncio.wait_for(wake.wait(), timeout=2)
                except TimeoutError:
                    pass
                wake.clear()
                if await request.is_disconnected():
                    return
                table_v, boards_v = showdown.HUB.version(code)
                if table_v != seen_table or time.monotonic() - looked >= 2:   # timers and away flags need a look too
                    seen_table, looked = table_v, time.monotonic()
                    try:
                        now = await asyncio.to_thread(look)
                    except arcade.Refused:
                        yield sse("end", {"reason": "This table has closed."})
                        return
                    if now.get("you") is None:
                        yield sse("end", {"reason": "Your seat was given up."})
                        return
                    view = {k: v for k, v in now.items() if k != "server_time"}
                    if view != shown:
                        shown = view
                        yield sse("table", now)
                if boards_v != seen_boards:
                    seen_boards = boards_v
                    yield sse("boards", showdown.HUB.snapshot(code))
                    await asyncio.sleep(0.12)                                   # about eight board updates a second
                if time.monotonic() - beat >= 15:
                    beat = time.monotonic()
                    yield b": ping\n\n"
        finally:
            showdown.HUB.unlisten(code, wake)
            lobby.PRESENCE.closed(table, actor)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
