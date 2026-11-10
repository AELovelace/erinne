"""The words of Lumi's dungeon: what is said when she steps into a room, finds something, meets someone.

Three ways a line gets written, in the order they were built:

1. The library. lumipet_data/tales/default.json has lines for every scenario, and each environment may have its own
   file (tales/<environment>.json) that is used first. This always works, with no model anywhere.
2. The generator (`mnemerynne lumi-tales`). It asks Claude to write more lines for an environment and adds them to
   that environment's file, for a person to read (and edit) before they are committed. It runs from the command
   line with ANTHROPIC_API_KEY, never from the web app.
3. Live mode. If a person switches their storyteller to Live, a room still shows its library line at once, and the
   page then asks for a fresh one, written by Claude with that person's own key or by BFGguf if they have it.

Whatever wrote it, a line is treated as plain text and has to pass the same check (`clean`): one short line, no
markup, and only the placeholders a scenario may use. A model's words never reach the page any other way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sqlite3
from pathlib import Path

from mnemerynne import lumibattle, lumidungeon

log = logging.getLogger(__name__)

MAX_LINE = 200                          # characters; it has to fit on a TV in large text
PLACEHOLDERS = ("enemy", "item", "place")
# Every scenario a line can be written for, with what is going on, for whoever (or whatever) writes it.
SCENARIOS = {
    "enter": "Lumi has just arrived at {place} at the start of a trip.",
    "start": "Lumi is back at the room she came in by; the way home is here.",
    "empty": "Lumi steps into a room with nothing in it but atmosphere.",
    "again": "Lumi walks back through a room she has already seen.",
    "treasure": "Lumi finds {item} lying in the room.",
    "key": "Lumi finds a key.",
    "vault": "The locked vault is open and Lumi finds {item} inside.",
    "locked_key": "Lumi reaches a locked door that needs a key.",
    "locked_puzzle": "Lumi reaches a door sealed by a puzzle. It could be any kind of puzzle, so the line mustn't say which.",
    "game_cups": "Three cups are set out; something is hidden under one.",
    "game_match": "Cards lie face down, waiting to be matched in pairs.",
    "game_win": "Lumi wins the little game and gets {item}.",
    "game_lose": "Lumi doesn't win the little game, and gets {item} for trying.",
    "enemy": "A wild {enemy} blocks the way and a fight begins.",
    "victory": "Lumi has beaten the {enemy}, who scampers off.",
    "fled": "Lumi ran from the {enemy} back to the room before.",
    "defeat": "The {enemy} was too much; Lumi is carried home, and what she found is left behind.",
    "stairs": "Lumi finds stairs leading further down.",
    "descend": "Lumi goes down the stairs to a deeper floor.",
    "home": "Lumi walks out of {place} and home with everything she found.",
    "abandon": "Lumi gives up the trip and goes home, leaving what she found.",
    "potion": "Lumi drinks a potion and feels better.",
}
# Plain reports of how a puzzle or game is going. These are facts, so they are never handed to a model.
FACTS = {
    "runes_try": "{exact} in the right place, {near} right but in the wrong place.",
    "runes_reset": "The runes spin and settle on a new code. Try again from the start.",
    "match_flip": "One card turned. Now find its pair.",
    "match_pair": "A pair!",
    "match_miss": "Not a pair. They turn back over.",
}
_BRACES = re.compile(r"\{(\w*)\}")
TRANSPORT = None                        # tests put a stand-in here in place of the Anthropic client


class TaleError(ValueError):
    """Refused; the message is safe to show."""


def clean(text: object, scenario: str | None = None, *, filled: bool = False) -> str | None:
    """One line, fit to show, or None. Plain text only: no markup, no line breaks, not too long. A library line may
    use the placeholders its scenario mentions; a live line (`filled`) has none left."""
    if not isinstance(text, str):
        return None
    line = " ".join(text.split()).strip().strip('"')
    if not 3 <= len(line) <= MAX_LINE or any(mark in line for mark in "<>`\\") or "http" in line.lower():
        return None
    used = _BRACES.findall(line)                          # the names inside {braces}
    allowed = () if filled else tuple(p for p in PLACEHOLDERS if "{" + p + "}" in SCENARIOS.get(scenario or "", "") or p == "place")
    if any(name not in allowed for name in used) or line.count("{") != len(used) or line.count("}") != len(used):
        return None
    return line


def load_library(folder: Path | None = None) -> dict[str, dict[str, list[str]]]:
    """{environment or "default": {scenario: [lines]}}. default.json must cover every scenario; a bad line anywhere
    stops the server at start, like a bad price would."""
    folder = folder or Path(__file__).parent / "lumipet_data" / "tales"
    library: dict[str, dict[str, list[str]]] = {}
    for path in sorted(folder.glob("*.json")):
        name = path.stem
        if name != "default" and name not in lumidungeon._ENVS:
            raise ValueError(f"Lumi's tales: {path.name} isn't named after an environment.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Lumi's tales: {path.name} should be scenarios with lists of lines.")
        for scenario, lines in data.items():
            if scenario not in SCENARIOS or not isinstance(lines, list) or any(clean(line, scenario) != line for line in lines):
                raise ValueError(f"Lumi's tales: {path.name} has a bad scenario or line under {scenario!r}.")
        library[name] = data
    if set(library.get("default", {})) != set(SCENARIOS) or any(not lines for lines in library["default"].values()):
        raise ValueError("Lumi's tales: default.json needs at least one line for every scenario.")
    return library


LIBRARY = load_library()


def _item(loot: dict | None, coins: int | None) -> str:
    """What was found, in words: "24 coins, a potion and the Moon bell"."""
    loot = dict(loot or {})
    if coins is not None:
        loot["coins"] = coins
    parts = []
    if loot.get("coins"):
        parts.append(f"{loot['coins']} coins")
    if loot.get("potions"):
        parts.append("a potion")
    if loot.get("keys"):
        parts.append("a key")
    if loot.get("trophy"):
        names = {t["id"]: t["name"] for found in lumidungeon.TROPHIES.values() for t in found}
        parts.append("the " + names.get(loot["trophy"], "trophy"))
    if loot.get("mod"):
        parts.append("a " + lumibattle._MODS[loot["mod"]]["name"] + " mod")
    return (", ".join(parts[:-1]) + " and " + parts[-1]) if len(parts) > 1 else (parts[0] if parts else "nothing much")


def facts(run: dict) -> dict[str, str]:
    """The names a line about this moment may use."""
    event = run["event"]
    return {"place": lumidungeon._ENVS[run["env"]]["name"], "enemy": event.get("enemy", "creature"),
            "item": _item(event.get("loot"), event.get("coins"))}


def line(run: dict, rng=None) -> str:
    """The library's line for what just happened in a run: the environment's own if it has one, else the default."""
    rng = rng or random.SystemRandom()
    scenario = run["event"]["scenario"]
    if scenario in FACTS:
        return FACTS[scenario].format(**{k: v for k, v in run["event"].items() if isinstance(v, int)})
    lines = LIBRARY.get(run["env"], {}).get(scenario) or LIBRARY["default"][scenario]
    told = lines[rng.randrange(len(lines))].format(**facts(run))
    won = _item(run["event"].get("loot"), run["event"].get("coins"))
    return told if "{item}" in SCENARIOS[scenario] and won in told or "{item}" not in SCENARIOS[scenario] else f"{told} ({won}.)"


# --------------------------------------------------------------------------- writing new lines with a model
VOICE = """You write short narration for Lumi Pet, a cosy virtual-pet game on a home media server, played by a \
household on TVs and phones. Lumi is a small glowing fairy cat: curious, brave in a wobbly way, easily delighted. \
She explores gentle dungeons one room at a time, like an old text adventure. The tone is warm, playful and all-ages: \
storybook, not horror. Enemies are "wild" creatures who are beaten and scamper off; nobody is hurt or killed, and \
a lost fight just means Lumi is carried home for a nap.

Each line is shown on its own, as plain text, when something happens. Write in the present tense, third person \
("Lumi ..."), one or two sentences, at most 160 characters. No markup, no emoji, no quotation marks around the line, \
no questions to the player, and nothing that tells the player what to press."""


def _schema(scenarios: list[str]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": scenarios,
            "properties": {name: {"type": "array", "items": {"type": "string"}} for name in scenarios}}


def _brief(env: dict, count: int) -> str:
    """What to write for one environment: the place, then each scenario with the names it may use."""
    listed = "\n".join(f"- {name}: {about}" for name, about in SCENARIOS.items())
    return (f"Write {count} different lines for each scenario below, all set in this place:\n\n"
            f"{env['name']} ({env['family']}). {env['about']}\n\n"
            "The braces are placeholders the game fills in. Use {enemy} and {item} exactly as written, and only in a "
            "scenario whose description mentions them; you may use {place} anywhere, or simply describe the place. "
            "Make the lines feel like this particular place, and make them differ from each other in more than a word.\n\n"
            "The game decides the details, so a line must stay true whatever they turn out to be. Say only what the "
            "scenario says happened: don't describe what a puzzle or a game looks like or how it is solved, what a door "
            "or a key is made of beyond the place's own style, or which way anything lies.\n\n"
            f"{listed}")


async def write_environment(client, env_id: str, count: int, model: str) -> dict[str, list[str]]:
    """Ask Claude for `count` lines per scenario for one environment. Returns only lines that pass `clean`.
    A declined request returns nothing rather than raising, so one environment can't stop the rest."""
    from mnemerynne.clients.claude import FALLBACK_BETA, FALLBACK_MODELS   # the same fallback the chat uses

    env, names = lumidungeon._ENVS[env_id], list(SCENARIOS)
    extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if model in FALLBACK_MODELS else {}
    reply = await client.beta.messages.create(
        model=model, max_tokens=16000, system=VOICE, messages=[{"role": "user", "content": _brief(env, count)}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _schema(names)}}, **extra)
    if reply.stop_reason == "refusal":
        log.warning("Claude declined to write tales for %s.", env_id)
        return {}
    if reply.stop_reason == "max_tokens":
        raise TaleError(f"The reply for {env_id} was cut short; ask for fewer lines.")
    written = json.loads(next(block.text for block in reply.content if block.type == "text"))
    kept = {name: [fit for fit in (clean(text, name) for text in written.get(name, [])) if fit] for name in names}
    return {name: lines for name, lines in kept.items() if lines}


async def generate(client, env_ids: list[str], count: int, out: Path, model: str, concurrency: int = 3) -> dict[str, int]:
    """The generator behind `mnemerynne lumi-tales`: write lines for each environment and add them to its file in
    `out` (keeping what is already there, dropping repeats). Returns how many new lines each environment gained."""
    out.mkdir(parents=True, exist_ok=True)
    gate, gained = asyncio.Semaphore(concurrency), {}

    async def one(env_id: str) -> None:
        async with gate:
            fresh = await write_environment(client, env_id, count, model)
        path = out / f"{env_id}.json"
        held = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        added = 0
        for name, lines in fresh.items():
            have = held.setdefault(name, [])
            for text in lines:
                if text not in have:
                    have.append(text)
                    added += 1
        path.write_text(json.dumps({name: held[name] for name in SCENARIOS if name in held}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        gained[env_id] = added

    await asyncio.gather(*(one(env_id) for env_id in env_ids))
    return gained


# --------------------------------------------------------------------------- live mode
def _live_prompt(run: dict) -> str:
    """One moment, as facts. Nothing about the person playing ever goes in."""
    env, scenario, names = lumidungeon._ENVS[run["env"]], run["event"]["scenario"], facts(run)
    return (f"Place: {env['name']} ({env['family']}). {env['about']}\nFloor: {run['depth']}\n"
            f"What just happened: {SCENARIOS[scenario].format(**names)}\n\nWrite one line for this moment.")


async def live_line(run: dict, *, claude: tuple[str, str] | None = None, bfgguf=None) -> str | None:
    """A freshly written line for what just happened, or None (no storyteller, a refusal, a line that doesn't pass
    `clean`, or any failure: the library line the page already shows simply stays). `claude` is (key, model);
    `bfgguf` is a client with chat(), closed by the caller."""
    if run["event"]["scenario"] not in SCENARIOS:
        return None                                       # puzzle reports are facts, not stories
    try:
        if claude:
            import anthropic

            key, model = claude
            client = TRANSPORT or anthropic.AsyncAnthropic(api_key=key, max_retries=1, timeout=20.0)
            reply = await client.messages.create(model=model, max_tokens=2000, system=VOICE, output_config={"effort": "low"},
                                                 messages=[{"role": "user", "content": _live_prompt(run)}])
            text = None if reply.stop_reason == "refusal" else next((b.text for b in reply.content if b.type == "text"), None)
        elif bfgguf is not None:
            text = await bfgguf.chat([{"role": "system", "content": VOICE}, {"role": "user", "content": _live_prompt(run)}],
                                     temperature=0.9, max_tokens=120)
        else:
            return None
    except Exception:                                     # a storyteller that is down is never the player's problem
        log.warning("Lumi's live storyteller didn't answer.", exc_info=True)
        return None
    return clean(text, filled=True)


def remember(conn: sqlite3.Connection, user_id: int, run: dict, told: str) -> None:
    """Keep a live line with the run, so the same moment never asks twice (a reload shows it again for free)."""
    run["say"] = told
    lumidungeon._save(conn, user_id, run)
