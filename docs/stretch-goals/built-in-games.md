# Stretch goal: Mnemerynne's own games

*Written 2026-10-28 (late) by Lumi for Doll. A stretch goal: nothing here is scheduled or started. It lives in
the repo on purpose, not in IT-OPS.*

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
4. **Beep Bop.** A rhythm tapper on four keys (D F J K) to beats the game makes itself, so there's no music
   licensing; levels made as small JSON files.

### Cozy / idle
1. **Window Garden.** Seeds that grow in *real* time, on the server's clock in the save, so it grows while
   you're away. Water, trim, and pick flowers to put in a vase. Calm, no fail state.
2. **Doll Room.** Decorate a small room from a furniture shelf (drag and drop). Later: a finished room as a
   picture that Signage can show on the TVs.
3. **Lumi Pet.** A little companion that naps at night, wakes in the morning, and likes being visited.
   Nothing bad happens if you don't (no guilt mechanics).

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

## Phases

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
