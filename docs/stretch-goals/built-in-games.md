# Stretch goal: Mnemerynne's own games

*Written 2026-10-28 (late) by Lumi for Doll. A stretch goal. It lives in the repo on purpose, not in IT-OPS.*

## Status (2026-11-10): Teacup Tower

**Teacup Tower** is built, reworked at Doll's request from the stacking idea below into a tower climber with our own rules and art.
- Lumi from Lumi Pet is the climber, drawn from Lumi Pet's own shapes.
- She auto-bounces off teacups, and the faster she moves sideways, the higher she bounces.
- Wall kicks, combo hops, sliding and cracked cups, and a landing saucer every 25 floors.
- From floor 5 the tower rises, faster every 30 seconds.
- It lives in the arcade shell with Breakout and Snake, so it has solo runs at 25 coins plus 1 coin a floor, and showdowns where racers climb the same tower.

## Status (2026-11-10): Breakout and Snake, and a cleaner table lobby

**Breakout** and **Snake** are ports of Doll's own GameMaker arcade games, at Doll's request, so the "no remakes" rule below doesn't apply to them.
- They share one shell, `static/games/arcade/`.
- They keep Roulette's coin rules: 25 coins a run, and each run pays its score.
- Their showdowns keep two racers plus five betting watchers, with the lobby rebuilt. No host can spend anyone's coins, both racers ready up before anyone pays, nobody can take both racer seats, and a racer who leaves forfeits instead of jamming the race. Seats free themselves when a page goes away.
- The casino tables got the same presence handling (`lobby.py`).

## Status (2026-11-10): StickBob Runner and the Mouse & keyboard shelf

**StickBob Runner** is a browser port of the runner mode in Doll's GameMaker game, at Doll's request and from Doll's own art and levels, so the "no remakes" rule below doesn't apply to it. `static/games/stickbob-runner/rules.js` is a line-by-line port of the physics, chunk picker and SlopBots. `python/export_stickbob.py` turns the GameMaker project into `assets/` (37 of the 39 chunks: the other two have floors below the death line). The music isn't included. The GameMaker knife key (Ctrl) is gone, because Ctrl+W would close the browser tab. It runs in the browser only, but **Doll chose to have it pay coins anyway** (the one exception to the rule below): `mnemerynne/runner.py` gives each run a ticket at the start (migration 0070, `runner_runs`), refuses claims faster than StickBob can run, pays a ticket once (1 coin per 10 m plus 1 per kill), and applies an optional daily cap, the Media setting `runner_daily_cap`, which defaults to 0, meaning no limit. A scripted fake within the speed limit would still be paid; the cap is the answer if that happens.

**Flow tuning (2026-11-10, Doll: "hit or miss if the platforms allow you to flow-state").** Tuned for running without stopping, in the web game and, at Doll's request, in the GameMaker game too (Doll's `betterAI` project; its story rooms are unchanged):
- The chunk picker joined chunks by an edge estimate that counted floors buried under terrain and treated slopes as their top edge, so it could put a wall at a join. Both games now use the lowest floor with room to stand on it, reading slopes by their surface (`standable_edge_y` in the exporter, `_estimate_edge_y` in `obj_RunnerDirector`; they agree within 16 px). Joins may climb at most 48 px (was 80), a chunk whose floor is below the room is never picked, and when nothing fits the picker takes the smallest climb instead of a random starter.
- The level edits live in the GameMaker project: `scr_runner_slices.gml` (spikes respaced, stepping stones widened, 64–96 px steps lowered to 48) and `datafiles/infiniteRunner/world1_chunks.json` (five pieces of the cut-out rooms moved or removed). `python/export_stickbob.py` only reads them. Don't re-run `tools/generate_runner_world1_chunks.ps1`: that JSON also holds Doll's hand edits from her chunk editor, which a regenerate would lose.
- Both games catch a ledge up to 16 px above StickBob's feet while he's falling (was 8 px always), so landing just short of a corner no longer kills all speed. In GameMaker only while `obj_RunnerDirector` exists, so story rooms and multiplayer keep their feel.
- `tests/js/stickbob_runner.test.mjs` has a "habit runner" (holds right, jumps a fixed distance before trouble) and fails if any chunk is crossed cleanly by fewer than a third of 60 habits. Before tuning the worst two were 0%; after, the lowest is a spike chunk at 33%. A steady player's median run before the first stop went from about 84–168 m to 116–307 m, depending on the habit.

Games with `mouse_keyboard=True` in `games.py` appear on their own **Mouse & keyboard** shelf, and only where the browser reports a mouse and isn't a paired TV.

## Status (2026-11-10): the Stardust Garden

Lumi Pet has an idle game: a flower farm that feeds a powder press (`mnemerynne/lumigarden.py`, migration 0069, the Garden tab; since 2026-11-10 its own app, Lumi's Garden). Doll's design: Farmville mixed with Cookie Clicker, for someone who loves idle clickers. Plots, twelve flowers (cut flowers early, perennials last), a press that turns starpowder into coins and slows down each day without stopping, watering (Simon Says) and fertilizing (fast presses) for time boosts, six class helpers lifted by Lumi's real skills and mods, her equipped moves as abilities, gifts, cosmetics for starpowder, Moonrise for a permanent upgrade or another garden, and a reminder when slow flowers are ready. Not built: visiting other people's gardens, seasons, weather. Every number is a first guess until someone has played it for a week.

## Status (2026-11-09): shared coins

Every Media game now shares one purse per account, kept on this server (`mnemerynne/coins.py`, migration 0063). It's built the way LumiBot's SadGirlCoin is, but Doll decided each NAS has its own coins, so it isn't connected to SadGirlCoin and the tie-in ideas below are not what shipped. Online casino tables bet coins, and a TV at one spends the account it's paired to. Local multiplayer (remotes around one TV) stays on free credits. Prism Drop's rules moved to the server (`mnemerynne/prism.py`) so a finished game can pay; Lumi Pet pays for the day's first visit. Where this conflicts with older sections below, this section is current.

## Status (2026-11-10): Lumi Pet split into three apps

At Doll's request Lumi Pet became three games on one "Lumi" shelf: **Lumi Pet** (the house and tamagotchi: care, pantry, toys, closet, home, shop, collection), **Lumi Dungeons** (the dungeon crawler and arena: dungeon, battle, skills) and **Lumi's Garden** (the Stardust Garden, slug `lumi-garden`). They are one pet, one save and one purse; the three pages share one code base and differ only in which tabs they show. Each can be installed as its own app with its own icon. Lumi's choices that Doll hasn't confirmed: Skills went with the dungeons and Shop and Collection stayed with the pet; the shelf is called "Lumi"; while Lumi is on a trip her room is empty and the other two apps point to Lumi Dungeons; the garden still can't be tended during a trip.

## Status (2026-11-09): Lumi Pet overhaul, first steps

Doll decided Lumi Pet should be a full pet game, upcycling MommyBot's Littlepottchi care engine and, later, Touhou Trader's battles. Built: Lumi lives on the account (`mnemerynne/lumipet.py`, migrations 0064–0066); her hunger, energy and joy decay along a logarithmic curve (the last quarter takes a day); a coin shop sells snacks, treats and toys; phone push reminders; a house of fifteen paperdoll spots (75 objects) whose windows look out on the theme's scrolling city (this absorbs **Doll Room** below); accessories and a Star Capsule; turn-based battles against 20 enemies with six skill trees and magitek/cybertek mods; and a catalog editor (`python/lumi_pet_editor.py`). This replaces the "no neglect, no decay, device-local" description in the 2026-11-02 section below. A dungeon crawler followed on 2026-11-10 (`mnemerynne/lumidungeon.py`, `lumitales.py`, migration 0067): twelve environments, generated floors explored a room at a time, locked vaults (keys, Sokoban, Lights Out, rune codes), trophies and a 15-piece decor set per environment for the house, and dialogue from a library that Claude can extend, with an optional live storyteller. The full plan is "Lumi Pet Overhaul Plan (2026-11-09).md" in Doll's BeepDrive Agents folder.

**Minigames for Lumi.** Which Cup and Constellation Match now exist as dungeon rooms; Falling Star Catch remains a stretch goal. Only a game whose outcome the server decides may pay coins, as Prism Drop does, because a browser can claim any score; reflex games like Star Catch give joy only.

## Status (2026-11-02): Lumi Pet and the fireplace idea

**Shared-TV casino mode** is also implemented: on a paired TV choose **One TV, multiple remotes**. Up to eight controllers/players have independent seats, credits, drafts and blackjack choices on one display. Each remote must be paired to the TV owner's account. First Enter joins, later Enter submits, and the first player's Play starts the round. Ordinary multi-device tables remain available. Signed panel inputs and server-side deduplication keep players separate and prevent duplicate moves; the player/testing guides cover setup and verification.

**Lumi Pet** is now implemented in Media → Games as a cozy, device-local companion: a glowing fairy cat with cuddles, snacks, star play, naps and three glow colours. It sleeps from 22:00 to 07:00 on the device's clock; any interaction can gently wake it. Happy moments and the latest memory persist in this browser profile, shared by its users. There is no neglect, decay, streak, currency or penalty for time away. This first version does not use server saves or sync between devices.

**Digital ASCII Neon Fireplace** is added to the design list as a future ambient toy: a looping text-character hearth with neon flames, embers and a cozy glow, intended for desktop and TV. Proposed controls: colour palette, flame intensity, pause and optional synthesized crackle. Include a still/reduced-motion mode. It is planned, not implemented; signage integration is a later possibility.

## Status (2026-11-01): shared casino tables

Blackjack, Roulette, Horse Racing and Slot Machines now join Prism Drop in Media → Games, including set-top boxes. Up to eight accounts or paired TVs share a persistent table. The server owns bets, random results, blackjack hands and play-chip settlement. This follows Doll's explicit request for these traditional games; the earlier original-games-only idea below is superseded for this scope.

Implementation: `mnemerynne/casino.py`, the existing Games routes and registry, `static/games/casino/`, `templates/games/casino.html`, and migration `0061_casino_rooms.sql`. Visible clients poll; deadlines advance during requests, including after reconnects. SQLite transactions serialize moves across processes. Snapshot revisions reject duplicate or stale actions. Free chips are per table and have no currency integration.

See the repository's `CONTRIBUTOR_GUIDE.md`, `GENERATION_TUNING_GUIDE.md`, `PLAYER_CHECKLIST.md` and `TESTING_GUIDE.md` for the current implementation and validation. The remaining roadmap below is historical context, not a description of today's multiplayer behavior.

## Status (2026-10-29): started, and the arcade is gone

Doll's calls on 2026-10-29 changed parts of the plan below:

- **The arcade is removed** (a failed experiment): `arcade.py`, `web/arcade.py`, its tests, admin box, help
  page and deploy blocks, and its table (migration 0060). Media → Games lists only built-in games, so the plan's
  "split `may_play`" and the Games admin box aren't needed: games follow Media's own group, and a TV plays as its
  owner.
- **The first game is a port, not one of the ideas below:** **Prism Drop**, from MommyBot's ball drop, without
  the wager. It keeps score only (20/15/10 for landing 0/1/2 pockets from the pick, plus coin pegs), ten drops a
  game, best score per device. It has the neon look, and the **remote panel works** with it: the TV page forwards the
  panel's arrows and Enter into the game frame, and the panel shows the score (screen state kind `game`).
- **Built so far (phase 0, trimmed):**
  - the registry, `mnemerynne/games.py`, with no table;
  - the page, `/media/games/<slug>` (`web/games.py`, `templates/game.html`, plus a partial per game in
    `templates/games/`);
  - `static/games/common.js` and `common.css`: actions from keys, remotes and taps, the frame messages, theme
    colours, sound, and the per-device store;
  - Node tests for the rules (`tests/js/`), which `tests/test_media.py` runs.
- **Not built:** server-side saves and scores (the `game_saves` and `game_scores` tables and their API). "Just
  keep score" didn't need them; add them when a game wants a leaderboard or saves.

**The idea:** small, original games that ship with Mnemerynne itself and open from **Media → Games**,
next to the arcade's (Lumi Web, Lumi Games). Played with **keyboard and mouse** on a PC first. Four kinds:
quick arcade toys, cozy/idle games, party games, and SadGirlCoin tie-ins.

## Decisions so far (2026-10-28)

| Question | Answer |
|---|---|
| Where they live | **Built into Mnemerynne**: static files in the app, no arcade, no other site. |
| How they're played | **Keyboard and mouse** first. Touch, gamepad and the TV remote can come later. |
| Which kinds | Quick arcade toys, cozy/idle, party games, SadGirlCoin tie-ins. |
| Where the plan is kept | This file, in the repo; not in IT-OPS. |

**Rule for every game:** our own ideas, names, art and rules. No remakes or renamed clones of existing games.

## Why built-in is fine here (and the one thing it changes)

The arcade exists because its games are *someone else's code* (another site), so they get an origin of their
own. These are *our* code, reviewed like the rest of the app, so they can run on Mnemerynne's own origin:

- They open in Media's game layer (`#mc-game`, already built for the arcade) as a same-origin page. No ticket
  and no arcade cookie are needed, and the person's Mnemerynne session is already there for saves and scores.
- Because they share the origin, they follow the app's rules: no third-party scripts, no `eval`, no
  `innerHTML` with data, all text through `textContent`. Keep them ready for the `script-src 'self'` CSP that
  Security Audit item 2.3 will bring: no inline scripts, every script a file under `/static/`.
- Same-origin also means the remote panel's arrows and Enter *could* reach them later, which they can't do
  inside the arcade's frame.

## Shape of it

### In the app
- **The registry:** `mnemerynne/games/__init__.py` holds a list of built-in games: slug, name, icon, kind
  (`arcade` | `cozy` | `party` | `coin`), a description, and whether it keeps saves or scores. A game is
  "installed" by being in the list; no admin form is needed.
- **The files:** `mnemerynne/web/static/games/<slug>/` with `index.html`, `game.js` and `game.css`, plus
  `mnemerynne/web/static/games/common.js` shared by all of them. That file provides:
  - the canvas, sized to the frame and crisp on high-DPI screens;
  - a fixed-step game loop that pauses when the tab is hidden;
  - input (keys held, pressed this frame, the mouse position and clicks), with **P/Esc to pause**;
  - the theme's colours read from the page's CSS tokens (`--rose`, `--petal-soft`, `--glass`…), so Sakura,
    Neon and the bubblegum theme all just work;
  - sound through one small Web Audio helper (mute remembered per person);
  - `save()`, `load()` and `score()`, calling the API below.
- **The page:** `GET /games/<slug>/` serves the game's page inside a small template (title, theme, the
  game's files). It needs a signed-in person who may play.
- **Media → Games** lists built-in games first, then the arcade's. A built-in card opens `/games/<slug>/`
  in the game layer directly; ⛶ and ⧉ work as they do now.
- **Who may play:** the same switch and group as the arcade (Entertainment → Games).
  - `arcade.may_play` currently also needs a games site. Split it: the switch and group decide who may play;
    the site matters only for the arcade's games.
  - **Admin:** a "Built-in games" part of the Games box with a switch per game (on or off); nothing else to
    set up.

### Saves and scores (server side)
- **Migration 0057 or later** (check the scratchpad board first):
  - `game_saves(user_id, game, data TEXT, updated_at)`, one row per person per game, JSON up to 64 KiB;
  - `game_scores(id, user_id, game, score, detail TEXT, created_at)`.
- **API:**
  - `GET` and `PUT /api/games/<slug>/save`;
  - `POST /api/games/<slug>/score`;
  - `GET /api/games/<slug>/scores` (top 20, and your best).
- **Retention:** saves stay; scores older than a year go (`retention.py`).
- **Honesty:** a browser can send any score. Scores are just for fun (a per-game leaderboard), never worth
  anything, **unless** the server checks them (see SadGirlCoin below).

## The games (first ideas, all original)

### Quick arcade toys
1. **Petal Drift.** A petal on a spring wind; the mouse steers it through gusts, past thorns, picking up dew.
   One more go in 60 seconds. *A good first game: one input, small, shows off the theme.*
2. **Static Bloom.** Neon dodge-'em-up: WASD moves, the mouse aims a short pulse that turns glitch blocks into
   flowers. Waves get denser; a high score per person.
3. **Teacup Tower.** Drop teacups (mouse) onto a wobbling saucer stack; light physics, one miss and it topples.
   **Built 2026-11-10 as a tower climber instead** (Doll's call): see the status above.
4. **Beep Bop.** A rhythm tapper on four keys (D F J K) to beats the game makes itself, so there's no music
   licensing; levels made as small JSON files.

### Cozy / idle
1. **Window Garden.** Seeds that grow in *real* time, on the server's clock in the save, so it grows while
   you're away. Water, trim, and pick flowers to put in a vase. Calm, no fail state.
2. **Doll Room.** Decorate a small room from a furniture shelf (drag and drop). Later: a finished room as a
   picture that Signage can show on the TVs.
3. **Lumi Pet.** A little companion that naps at night, wakes in the morning, and likes being visited.
   Nothing bad happens if you don't (no guilt mechanics).
   **Implemented 2026-11-02:** see the status above for this version's interactions and local persistence.
4. **Digital ASCII Neon Fireplace.** An ambient text-art fireplace: neon flames and drifting embers,
   palette and intensity controls, optional synthesized crackle, and a calm still mode. **Planned.**

### Party games (keyboard and mouse first, so one machine)
1. **Buzzer Bloom Quiz.** Hot-seat trivia for up to 4 on one keyboard, buzzing with Q, P, Z and M. Question
   packs are JSON files an admin can add to.
2. **Doodle Pass.** Draw-and-guess passed around one mouse.
- **Later**, if touch is wanted: phones as controllers through a room code, the TV as the board. Rooms'
  live-update stream (`rooms_bus`) could carry the moves. That's when touch input comes in.

### SadGirlCoin tie-ins (careful: it's money-like)
- **What exists:** LumiBot's SadGirlCoin API (`C:\Scripts\LumiBot\docs\SADGIRLCOIN_API.md`, base
  `https://sadgirlsclub.wtf/v1`, with its own OAuth in `SGCServer`) and the Lumi Games casino in the arcade.
- **Needs first:**
  - **Linking accounts:** link a Mnemerynne person to their Discord id (SGC's OAuth, once, under Preferences).
  - **A server-to-server API key** for Mnemerynne, stored encrypted like the internal APIs' keys.
- **The rule:** only the *server* pays out, and only for what the *server* can check.
  - Cozy rewards (a daily "you watered your garden" of a few coins) are checked against the save's server
    timestamps.
  - Arcade scores never pay out, because a browser can fake them.
  - Daily caps per person; every payout logged in `access_log`; an admin switch to turn coins off.
- **Ideas:**
  - a garden that sells flowers for a few coins a day;
  - a quiz night where the winner gets a pot the host put in (paid from the host's own balance, so no coins
    are made out of nothing).

## Original phases (historical; see current status above)

| Phase | What | Size |
|---|---|---|
| 0 | Groundwork: the registry, `/games/<slug>/` page, `common.js`, Media listing built-ins, split `may_play`, saves and scores tables and API, help pages, tests | ~1 evening |
| 1 | **Petal Drift** (first arcade toy) with a leaderboard | ~1 evening |
| 2 | **Window Garden** (cozy, server-clock growth, saves) | 1–2 evenings |
| 3 | **Buzzer Bloom Quiz** (party, hot-seat) with a starter question pack | ~1 evening |
| 4 | SadGirlCoin: account link, API key, server-checked garden rewards | 2+ evenings, and a talk first |
| later | More toys (Static Bloom, Teacup Tower, Beep Bop), Doll Room, Lumi Pet, Doodle Pass, phones as controllers, TV remote and gamepad | — |

## Tests and checks
- **Python:**
  - the registry;
  - the page needs sign-in and may-play;
  - saves are capped at 64 KiB and are per person;
  - scores are listed, and one person's can't touch another's;
  - built-ins show in `/api/media/games`, alongside arcade games.
- **JavaScript:** game rules kept in pure modules (no DOM) so `node --test` can check them, e.g. Petal
  Drift's collisions and the garden's growth from timestamps.
- **By hand:** play each in Sakura, Neon and the bubblegum theme; check that pause works when the tab is hidden.

## Open questions for Doll
1. **Party games on phones later?** You picked keyboard and mouse for now, and party games are much better
   with phones as controllers. Add touch when party games come up, or stay hot-seat?
2. **SadGirlCoin:** is Mnemerynne allowed to pay out at all (with caps), or only to *show* balances?
3. **Signage tie-in:** should a finished Doll Room or garden be something the TVs can show?
4. **Which first:** Petal Drift (simplest), or Window Garden (most "you")?

## Shelved tonight (2026-10-28)
- **Option A**, sending lwhittaker.net page loads to beepsquad.wtf on site-chan: written up but not run.
  Nothing was changed, so there's nothing to undo. People use `https://beepsquad.wtf` directly.
