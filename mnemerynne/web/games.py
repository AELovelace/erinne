"""Media → Games: the built-in games (mnemerynne/games.py). The list for Media's Games view, and each game's page,
which Media's game layer frames (media.js games). Both sit behind Media's own guard (media._media_on): Media on,
and the person (or the TV's owner) in the Media group."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from mnemerynne import bfgguf, casino, casino_remote, claude_chat, coins, games, lumidungeon, lumipet, lumitales, prism, push
from mnemerynne.db import transaction
from mnemerynne.media import remotes, screens
from mnemerynne.web.deps import _session_user, get_db
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


@router.get("/api/media/games/coins")
def coin_balance(user=Depends(media_user), conn=Depends(get_db)):
    """This account's coins (a paired TV shows its owner's). Also brings home stakes from any table that has expired."""
    casino.sweep(conn)
    return {"coins": coins.peek(conn, user["id"])}


class PrismDrop(BaseModel):
    """The player only says which pocket: the server rolls the drop and keeps the score."""
    model_config = ConfigDict(extra="forbid")
    pick: int = Field(ge=1, le=prism.WIDTH, strict=True)


@router.get("/api/media/games/prism-drop/run")
def prism_run(user=Depends(media_user), conn=Depends(get_db)):
    """The game in progress, so a reload or another device picks it up."""
    return prism.current(conn, user["id"])


@router.post("/api/media/games/prism-drop/drop")
def prism_drop(body: PrismDrop, user=Depends(media_user), conn=Depends(get_db)):
    """One drop; the tenth finishes the game and pays its coins."""
    return prism.play(conn, user["id"], body.pick)


@router.post("/api/media/games/lumi-pet/visit")
def pet_visit(user=Depends(media_user), conn=Depends(get_db)):
    """The first time someone spends a moment with Lumi today. Takes no numbers from the page, so there's nothing to fake."""
    with transaction(conn):
        paid = coins.pet_visit(conn, user["id"])
        return {"paid": paid, "coins": coins.peek(conn, user["id"])}


class PetAction(BaseModel):
    """What the person chose to do with Lumi. Only choices: which thing and how many. The server decides every result."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["cuddle", "feed", "play", "nap", "wake", "buy", "decorate", "wear", "capsule", "glow", "import", "storyteller",
                    "learn", "respec", "equip", "mod", "potions", "heal"]
    item: str | None = Field(default=None, max_length=40)
    count: int = Field(default=1, ge=1, le=lumipet.MAX_HELD, strict=True)
    moments: int = Field(default=0, ge=0, le=lumipet.MAX_MOMENTS, strict=True)   # only read by "import", once
    glow: str | None = Field(default=None, max_length=20)
    slot: int | None = Field(default=None, ge=0, le=9, strict=True)             # which mod slot, for "mod"


@router.get("/api/media/games/lumi-pet/state")
def pet_state(user=Depends(media_user), conn=Depends(get_db)):
    """Lumi as she is right now, caught up from the last time anyone looked. A paired TV shows its owner's pet."""
    with transaction(conn):
        return lumipet.state(conn, user["id"])


@router.post("/api/media/games/lumi-pet/act")
def pet_act(body: PetAction, user=Depends(media_user), conn=Depends(get_db)):
    """One care action or purchase. A refusal (too soon, not enough coins) changes nothing and says why."""
    try:
        with transaction(conn):
            return lumipet.act(conn, user["id"], body.action, item=body.item, count=body.count, moments=body.moments,
                               glow=body.glow, slot=body.slot)
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
    action: Literal["move0", "move1", "move2", "move3", "defend", "potion", "run"]


@router.post("/api/media/games/lumi-pet/battle")
def pet_fight(body: FightStart, user=Depends(media_user), conn=Depends(get_db)):
    """Start a fight. One at a time; it waits for as long as it takes."""
    try:
        with transaction(conn):
            return lumipet.fight_start(conn, user["id"], body.tier)
    except lumipet.PetError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None


@router.post("/api/media/games/lumi-pet/battle/act")
def pet_fight_turn(body: FightTurn, user=Depends(media_user), conn=Depends(get_db)):
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
    action: Literal["move", "take", "back", "unlock", "sokoban", "lights", "rune", "cup", "flip", "potion", "descend", "leave", "abandon"]
    way: Literal["n", "e", "s", "w"] | None = None
    moves: str = Field(default="", max_length=600, pattern="^[udlr]*$")
    picks: list[int | str] = Field(default_factory=list, max_length=200)


@router.post("/api/media/games/lumi-pet/dungeon")
def pet_dungeon(body: DungeonStart, user=Depends(media_user), conn=Depends(get_db)):
    """Set out on a dungeon trip. One at a time; it waits for as long as it takes."""
    try:
        with transaction(conn):
            return lumipet.dungeon_start(conn, user["id"], body.env)
    except lumipet.PetError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None


@router.post("/api/media/games/lumi-pet/dungeon/act")
def pet_dungeon_step(body: DungeonStep, user=Depends(media_user), conn=Depends(get_db)):
    """Take one step, or make one choice, in the dungeon."""
    try:
        with transaction(conn):
            return lumipet.dungeon_act(conn, user["id"], body.action, way=body.way, moves=body.moves, picks=body.picks)
    except lumipet.PetError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None


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
def pet_tale_source(request: Request, user=Depends(media_user), conn=Depends(get_db)):
    """Whether a live storyteller is there for this person, and which, so Settings can say."""
    found = _storyteller(request, conn, user)
    return {"live": found[0] if found else None}


@router.post("/api/media/games/lumi-pet/dungeon/tale")
async def pet_tale(request: Request, user=Depends(media_user), conn=Depends(get_db)):
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
def pet_push_on(body: PushDevice, user=Depends(media_user), conn=Depends(get_db)):
    """Lumi may send this device reminders. Asking again from the same browser refreshes it."""
    try:
        with transaction(conn):
            push.subscribe(conn, user["id"], body.endpoint, body.p256dh, body.auth, body.utc_offset)
    except push.PushError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None
    return {"on": True}


@router.post("/api/media/games/lumi-pet/push/off")
def pet_push_off(body: PushDevice, user=Depends(media_user), conn=Depends(get_db)):
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
def game_page(slug: str, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    """A game's page: its canvas and its scripts (static/games/<slug>/), in the person's theme. No `user` in the
    template, so the page carries none of the app's own scripts (messages, notifications) inside the game frame."""
    game = games.get(slug)
    if game is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such game")
    paired = _screen(request, conn)
    tv_play = paired is not None and not paired["speaker"] and paired["user_id"] == user["id"] and screens.may_use(conn, user, request.app.state.directory)
    return request.app.state.templates.TemplateResponse(request, "game.html", {"game": game, "tv_play": tv_play})


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
def table_list(slug: str, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    actor, _ = _table_identity(request, conn, user)
    paired = _screen(request, conn)
    display = None
    if paired is not None and not paired["speaker"] and paired["user_id"] == user["id"] and screens.may_use(conn, user, request.app.state.directory):
        display = casino_remote.actor("screen", paired)  # A fresh tab can resume its TV without consuming a player seat.
    return {"rooms": casino.rooms(conn, slug, actor, display)}


@router.post("/api/media/games/{slug}/rooms")
def table_create(slug: str, request: Request, body: TableCreate | None = None,
                 user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    if body is not None and body.mode == "tv":
        screen, actor = _tv_identity(request, conn, user)
        return _casino_call(casino.create, conn, slug, actor, screen["name"], couch=True)
    actor, name = _table_identity(request, conn, user)
    return _casino_call(casino.create, conn, slug, actor, name, account=user["id"])   # a TV here is its paired account


@router.get("/api/media/games/{slug}/rooms/{code}")
def table_state(slug: str, code: str, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    actor, name = _table_identity(request, conn, user)
    return _casino_call(casino.room, conn, slug, code, actor, name, account=user["id"])


@router.post("/api/media/games/{slug}/rooms/{code}")
def table_move(slug: str, code: str, body: TableMove, request: Request,
               user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    actor, name = _table_identity(request, conn, user)
    return _casino_call(casino.room, conn, slug, code, actor, name, account=user["id"], **body.model_dump())


@router.get("/api/media/games/{slug}/rooms/{code}/tv")
def tv_table(slug: str, code: str, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    _, actor = _tv_identity(request, conn, user)
    return _casino_call(casino.couch, conn, slug, code, actor)


@router.post("/api/media/games/{slug}/rooms/{code}/tv/input")
def tv_input(slug: str, code: str, body: TVInput, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    screen, display = _tv_identity(request, conn, user)
    try:
        proof = casino_remote.read(request.app.state.settings.secret_key, body.token)
    except casino_remote.BadData as exc:
        raise HTTPException(403, "That remote input expired or could not be verified. Press again.") from exc
    remote = remotes.get(conn, proof["remote"])
    if (remote is None or remote["user_id"] != screen["user_id"] or proof["screen"] != display
            or proof["pairing"] != casino_remote.pairing(remote)):
        raise HTTPException(403, "This remote is no longer paired to this TV's owner.")
    command = {"id": proof["id"], "action": proof["action"], "actor": casino_remote.actor("remote", remote), "name": remote["name"]}
    return _casino_call(casino.couch, conn, slug, code, display, command=command,
                        expected_round=body.round, expected_phase=body.phase)


@router.post("/api/media/games/{slug}/rooms/{code}/tv/manage")
def tv_manage(slug: str, code: str, body: TVManage, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    _, actor = _tv_identity(request, conn, user)
    return _casino_call(casino.couch, conn, slug, code, actor, close=body.action == "close",
                        remove=body.seat if body.action == "remove" else None, revision=body.revision)
