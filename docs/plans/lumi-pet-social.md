# Lumi Pet: free walking, local chat, and visiting houses

Status: **planning** (2026-11-19). Doll's answers are in (see **Decisions** at the end). Nothing built yet. Builds on room mode (`room-ui.js`, `room-art.js`,
`game.js` ROOM pseudo-tab) and on Lumi Town's presence (`lumitown.py`, `POST /api/media/games/lumi-pet/town`).

Three features, in the order they depend on each other:

1. **Free walking**: WASD (held) and middle-click move Lumi anywhere along the floor, not just thing to thing.
2. **Local chat**: speech bubbles between Lumis who are in the same place (a street in town, or a house).
3. **Visiting**: walk into another player's flat, see it as they decorated it, and chat there.

---

## What exists today (so we build on it, not beside it)

| Piece | Where | What it gives us |
|---|---|---|
| Snap walking | `room-ui.js` `step` / `hop` / `nearest` | Spots in walking order; ◀ ▶ hop thing to thing; edges walk through to the next room |
| Lumi's position | `game.js` `drawRoom` → CSS `--gx` | She's placed by one number; `game.css:393` animates `left` over .7 s |
| Floor press | `game.js` `roomPress` | A left-click on the floor → nearest thing; on a thing → use it |
| Remembered place | `G.store` `"room-at"` = `{room, at}` | Per-device, an index into the layout, not an x |
| Town presence | `lumitown.py` `Town` | In-memory, who's where, 20 s expiry, `town_seen` opt-out; page **polls** every few seconds |
| Live streams | `web/games.py` party SSE, `rooms_bus.py` | SSE with `X-Accel-Buffering: no` already works through nginx; rooms_bus shows the Valkey cross-process pattern |
| A Lumi's look | `lumipet.look(conn, uid)` | Everything needed to draw someone else's Lumi |
| Her house | pet save `house`, `decor`; `lumipet.placed` | Everything needed to draw someone else's flat |

> ⚠ The room-styles work (`room-styles.js`, `python/make_lumi_room_styles.py`, and edits to `room-ui.js` /
> `room-art.js`) is uncommitted right now. Phase 1 touches the same files, so that lands first.

---

## Phase 1: Free walking (solo, no server change)

> **Built 2026-11-19.** How it differs from the sketch below:
> - **No front/behind layering.** Every thing stands against the wall, so each one blocks the floor from its front
>   edge back to the wall (`room-ui.js` `blocks`). She walks into the gaps between things but never behind one, so
>   she's always rightly drawn in front. The kitchen counter blocks too (`EXTRA`). Wall-hung things (`wall: true`:
>   the door, trophy shelf and potion shelf) block nothing.
> - **Depth is her feet's y**, not a 0..1 z: `FRONT` 430 to `BACK` 372 (the wall meets the floor at 352). A place is
>   `{room, at, x, y}`. Old `{room, at}` saves still load.
> - **Enter with nothing in reach** uses the nearest thing (she walks to it), rather than a cuddle.
> - Her bedroom's own furniture that isn't a room-mode thing (lamp, plant, side table, moving boxes) doesn't block yet.
> - Gamepads: `MPad` loads for Lumi Pet only (`games.py` extras). Y / quick phrases wait for phase 2.
> - Doll's look pass: a **soft shadow** (a radial-faded ellipse, `#lp-rm-shadow`) replaced the dashed ring under her,
>   and she's set 17 units × her size lower so her paws rest on it instead of floating.
> - A click-walk that bumps a thing steps toward the front, round its base, and carries on; a spot clicked on a thing
>   becomes the floor in front of it.

### How it feels (Doll's answers, 2026-11-19)
She walks on a **2.5D floor**: left-right (x) *and* toward / away from the window (depth). Further back, she's a
little smaller and drawn behind the furniture in front of her.

| Input | What it does |
|---|---|
| **WASD** held | Free walking: A/D left-right, W away from you (toward the window), S toward you. Diagonals work. ~260 units/s |
| **Arrow keys** held | The same free walking as WASD |
| **Gamepad** (Xbox, PS5, any standard pad) | Left stick = **analog** free walking (push gently, she walks slowly); D-pad = free walking at full speed; A / Cross = use; B / Circle = back; LB / RB = previous / next room |
| **Tab5 / phone panel in gamepad mode** | Its D-pad = free walking (held, using the panel's button-up events); A = use; B = back; L / R = rooms |
| **TV remote** (press mode, and ◀ ▶ forwarded as single presses) | **Snap** walking, thing to thing, as today, so a remote never has to steer |
| **⏮ ⏭ / Page Up / Page Down** | Hop a whole room, as today |
| **Middle-click** on the floor | Walk to exactly that spot (x and depth). Never *uses* anything |
| **Left-click** | On a thing: walk to it and use it. On the floor: walk to that spot |
| **E / Enter / A** | Use the thing in reach |

- **In reach**: whichever thing she's close to (within ~70 units across and ~40 in depth) glows (its press box
  outline) and its bubble lights up in the lower bar. Nothing in reach → Enter gives her cuddle line.
- **Walking past a room's edge** (x < 180 or > 820, the old EDGE values) carries her through to the next room,
  arriving at the matching edge and the same depth.
- **Telling a held key from a remote**: a real keyboard sends `keydown` repeats and a `keyup`; a remote panel's press
  mode sends one press with no release. So: a key with a `keyup` = held (free); a forwarded `mnemerynne-game-key`
  message = a press (snap). Nothing to configure.

### The floor (depth)
- Feet y runs from **FLOOR_BACK ≈ 360** (by the wall) to **FLOOR_FRONT = 430** (room-art.js `FLOOR`, the lower bar
  starts below it). Room-ui.js keeps position as `{room, x, z}` with **z from 0 (front) to 1 (back)**, so the numbers
  never depend on the drawing.
- **Scale**: 1.0 at the front, ~0.82 at the back (CSS `--gz` → the pet wrap's `bottom` and a `scale()`).
- **Walls and furniture block**: each thing gets a **foot box** in room-ui.js (the patch of floor it stands on, in x
  and z). She can't walk into one; she slides along its edge. Small things on the floor (the bowl, paint pots) block,
  wall things (the potion shelf, trophy shelf) don't.
- **In front or behind**: things are drawn in order of their z. She's drawn in a layer between the things behind her
  and the things in front of her. Room-art.js already draws each thing as its own group, so game.js splits the room's
  SVG into "behind" and "in front" groups around her each time she crosses a thing's z (not every frame).
- **Remote snap spots** get a z too (where she stands to use it), so snap walking and free walking agree.

### Changes
**`room-ui.js` (pure, testable):**
- Position becomes `{room, x, z}` instead of `{room, at}`. `clean()` accepts either, so old saves turn `at` into that spot's x and z.
- Each thing gets a `foot` box `[x, z, w, d]` (the floor it blocks) and a `stand` z (where she uses it from).
- `move(pos, dx, dz)` → new `{room, x, z}`: clamped to `REACH` and 0..1, slid along any foot box she'd walk into,
  crossing rooms at the side edges.
- `inReach(room, x, z)` → the thing within reach (or null). Replaces "the spot she's standing at".
- `order(room, z)` → which things are behind her and which in front (for drawing).
- `floorPoint(y)` → z from a press's drawing y (middle-click and left-click on the floor).
- `step`/`hop` keep working for remotes, start from `inReach` / nearest, and return x and z.
- `hits()` draws the ring at x and z (smaller further back), and marks the in-reach thing's box for the glow.

**`gamepad.js` (shared with Lumi Launch etc., stays backwards-compatible):**
- Today it turns the stick into repeating "left"/"right" presses. Add a **held** view alongside, without changing
  what other games get: `MPad.start(onAction, onHold)`, where `onHold(who, {x, y})` reports the left stick's analog
  value (with a ~0.15 dead zone) and the D-pad as ±1, each poll it changes. Games that don't pass `onHold` see no difference.
- Tests in `tests/js/gamepad.test.mjs` for the dead zone and the D-pad-as-axes.

**`common.js` (MGame):**
- Keyboard: add an optional `onHold(direction, down)` from `keydown`/`keyup` for arrows and WASD (WASD only in games
  that ask for it, so Casino etc. never see it). Ignored while typing in an input (chat in Phase 2).
- Remote panel gamepad mode: today only a button *going down* reaches a game. Also hand `down: false` for the D-pad to
  `onHold`, so the Tab5 / phone pad holds to walk. Press mode (`mnemerynne-game-key`) stays a single press → snap.
- A lost focus (`blur`, tab hidden) lets go of everything held, so she never walks off on her own.

**`game.js`:**
- A `requestAnimationFrame` loop that only runs while something is held or a click target is set; stops itself
  otherwise (no idle CPU). The keyboard, a pad and a panel add up into one walking direction.
- While free-walking, set `data-walking` on the room so CSS turns the `.7s` `left` transition off (it would lag).
- Facing: flip the pet sprite with a `--face` variable (`scaleX(-1)`). Depth: `--gz` for her height on the floor and scale.
- Redraws the room's two layers (behind / in front of her) only when `order()` changes.
- `roomPan` already follows any x, so a narrow screen keeps her in view.
- `G.store "room-at"` saves `{room, x, z}`, throttled (on stop, not every frame).
- `mousedown` with `button === 1`: `preventDefault()` so the browser's autoscroll doesn't appear; walk to the point.

**`game.css`:** `[data-walking]` no transition; facing flip; `--gz` height and scale; in-reach glow (reuse
`.lp-rm-hit:hover`'s look).

**`room-art.js` / `room-styles.js`:** each thing's markup in its own `<g data-thing="id">`, so game.js can put it in
front or behind. The bedroom's house.js furniture likewise (house.js `render` gets a `layers` option).

**Tests:** `tests/js/lumi_room.test.mjs`: move/clamp/cross rooms at the same depth, foot boxes block and slide, old
`{at}` saves still load, `inReach` boundaries, every thing reachable on foot *and* by snap, front/behind order. Wiki:
Lumi Pet controls page (keyboard, mouse, gamepad, Tab5, remote).

---

## Phase 2: Places and local chat (start in Lumi Town)

> **Built 2026-11-19** (Lumi Town). How it differs from the sketch below:
> - Town walking is still place to place, so moves stay on the existing `POST .../town` (now with `inside`), sent at
>   once on a move and every 5 s. The stream carries only `said` (a line, filtered to the listener's place) and
>   `moved` (a nudge: the page POSTs again to see who's here). No per-frame positions yet: those come with houses.
> - `lumitown.Town` grew `where`, `said`, `recent`; no new `lumiplaces.py` yet (houses will generalise it).
> - A place is `street:<street>` (out on a street's map) or `in:<place>` (inside one). Visitors are now drawn only
>   where they really are (inside vs. on the map), so what you see matches who hears you.
> - The chat log lives in the right-hand panel under Lumi's message (the picture's sides are under the panels in town).
> - Chat for remotes and pads is the town list itself ("💬 Say something" → phrases, Type, Mute, Back), not a wheel
>   on Y. The POST answer carries `you` so your own lines say "You" after a reload.

Town already knows who's on which street, so chat lands there first, then houses reuse it.

### A "place"
Generalise `lumitown.Town` into **`lumiplaces.py` `Places`**: keyed by place id
(`town:high`, `town:market`, `house:<uid>:<room>`). Each person: `{place, x, face, seen}`.
Same in-memory, same `SEEN_SECONDS`, same `town_seen`-style opt-out, same `MAX_PEOPLE` guard.

### Live, not polled
Free walking makes 3-second polling look jerky. Move to **one SSE stream per open page**:
- `GET  /api/media/games/lumi-pet/place/stream?place=…`: events `here` (people + looks on join), `moved`
  (uid, x, face; batched, at most ~5/s), `said` (chat), `left`, `ping` every 15 s.
- `POST /api/media/games/lumi-pet/place/move`: `{place, x, face}`, client-throttled to ~5/s while walking, plus one on stop.
- `POST /api/media/games/lumi-pet/place/say`: `{place, text}` or `{place, phrase: "<id>"}`.
- Cross-process: publish on the event bus (`events.py`) like `rooms_bus.py` does, so it keeps working after the
  services rewrite splits games into its own process. With Valkey off it's just this process.
- `release_db(request)` before the stream loop (see the get_db/streams lesson).

Why SSE + POST and not a WebSocket: SSE is already proven through nginx here, reconnects by itself, and matches the
party stream. Netplay's websocket is the fallback if ~5 POSTs/s per walker turns out heavy (it shouldn't for this
user count).

### Chat itself
- **Speech bubbles** over the speaker's Lumi, ~6 s, newest on top; plus a small **log** (last 30 lines) you can open.
- **Local** = same place only. A street in town, a room in a house. Leave the room, you stop hearing it.
- **Ephemeral**: kept in memory (last 30 per place) for people who just walked in; never written to a table. (See Q3.)
- **Length**: 140 chars, plain text, drawn with `textContent` only (never innerHTML). Server strips control chars.
- **Rate limit**: 1 line / 1.5 s, 10 / 30 s per person; over it, Lumi says "Slow down, I'm out of breath!"
- **Quick phrases** (authored list: "Hi!", "Love your room!", "Follow me!", 💖 😂 👋…) so a **TV remote / couch**
  can chat without a keyboard. The phrase id is sent, not text.
- **Typing**: **T** or **Enter** (when nothing's in reach) opens the chat box; Esc closes. While it's open, WASD and
  the arrows type, not walk.
- **On a gamepad or the Tab5**: **Y** opens the quick-phrase wheel (stick or D-pad picks, A says it, B closes). X stays free.
- **Mute**: press someone's Lumi → "Mute <name>" (per device, `G.store`). A server-side **block** (they can't visit you,
  you don't see their lines) comes with Phase 3.
- **Kids-ish tone, grown-up app**: no word filter by default; admins can see a recent per-place log in memory for
  moderation via the existing `diagnose`/admin route if needed (Q3).

### Changes
`lumiplaces.py` (new, + tests), `web/games.py` (3 routes; old `/town` kept as a thin wrapper until the page moves),
new `place-chat.js` (pure: bubble layout, phrase list, rate-limit messages) + game.js wiring, `game.css` bubbles,
`tests/test_lumiplaces.py`, `tests/js/lumi_chat.test.mjs`, wiki page.

---

## Phase 3: Visiting houses

> **Built 2026-11-19.** How it differs from the sketch below:
> - `lumihouse.py` (not `lumiplaces.py`): `Houses` (who's in which flat and where they stand, knocks, 3-hour passes)
>   and `access()`. Town presence stays in `lumitown.py`; `_where_now` picks whichever a person reported last.
> - A visit is Lumi Pet at `?at=visit-<id>` (media.js's `at` pattern now allows digits). Before you're in, you stand
>   on their doorstep with Knock / Back to town.
> - Entry is the town list's **🏠 Visit a friend's flat** on each street's map; pressing someone's Lumi in town isn't
>   wired up (the folk layer takes no presses).
> - The others in a flat are drawn on the folk layer at room size and walk live (`walk` events from each page's
>   `POST houses/here`, sent at most four times a second while walking, and every 5 s).
> - "Sit on the bed / play with a toy" guest acts weren't built: things are purely look-only, with a line each.
> - Blocking is **Show <name> out** in the host's 💬 list; **Unblock** rows in Settings. No push notification for knocks.

### Getting there
- **In town**: press someone's Lumi → "Visit <name>'s flat". (Their Lumi is right there, so it's natural.)
- **Lumi Town has a "Flats" door** on the high street → a list of whose flats are open right now / to you.
- **Their front door** leads back to town; **your** front door still leads to town too.

### Who may come in (a door setting on the pet save, `door`)
- `open`: anyone signed in walks straight in.
- `knock` (**default**): the visitor knocks; the host gets a bubble "Bob's Lumi is knocking!" with Let in / Not now
  (and a push notification if they're elsewhere in Mnemerynne?). Not home → "Nobody's in."
- `closed`: no visits; the "Visit" option doesn't show.
- Plus the per-person **block** list (server-side).
- (There's no friends list in Mnemerynne yet; "friends only" would need one. See Q4.)

### What a visitor sees and can do
- The host's three rooms, drawn from **their** `house`, `decor`, room styles and glow, with the visitor's own Lumi
  walking (free walking from Phase 1) and the host's Lumi there if the host is home.
- Things are **look-only**: pressing the fridge → the host's Lumi's line ("That's Alice's fridge! No snacking!").
  No menus, no coins, nothing written to the host's save. A few "guest" acts are cheap and safe:
  sit on the bed, play with a toy (animation only). (The guestbook waits for Phase 4.)
- Room mode's code stays one view: `drawRoom` takes a *house to draw* and a *guest flag*; `Ru.rows` returns
  look-only rows when the flag is set. No second copy of the rooms.

### Server
- `GET /api/media/games/lumi-pet/houses/<uid>`: a **public view** of a house: `house`, `decor`, styles, name,
  colour, look. Never coins, meters, inventory, settings. Checked against `door` + block every call.
- `POST …/houses/<uid>/knock`, `POST …/knock/<id>/answer`: knocks live in memory (expire in 60 s), delivered on the
  host's place stream (`knock` event).
- Places: `house:<uid>:<room>`. A host who turns the door to `closed` while guests are in: guests get a polite
  "Time to go home!" and walk out.

### Changes
`lumipet.py` (`door`, `blocked`, public view), `lumiplaces.py` (knocks), `web/games.py` routes, `room-ui.js`
guest rows, `game.js` visiting state (whose flat, back to town), Settings: door + blocked list, tests, wiki.

---

## Phase 4: Polish (pick and choose)
Emotes with animations (wave, dance, hug when two Lumis stand together), the guestbook, a "who's visiting" badge on
the Lumi Pet tile, house-warming coins for the first visit of the day to someone else's flat (capped, like `pet_visit`),
and party invites from inside a house ("Want to go to the dungeon together?").

---

## Decisions (Doll, 2026-11-19)

1. **Depth**: yes. W/S (and up/down) walk her toward and away from the window on a 2.5D floor.
2. **Arrows**: walk freely, like WASD. **TV remotes snap** thing to thing. **Gamepads** (Xbox One, PS5, any
   standard pad) and the **Tab5 / phone panel in gamepad mode** walk freely too.
3. **Chat history**: ephemeral. Memory only, the last 30 lines per place, gone on restart.
4. **Door default**: `knock`. A friends list is not planned yet.
5. **Guestbook**: Phase 4.

## Order of work
Commit the room-styles work → Phase 1 (one session, mostly front-end) → Phase 2 (server + front-end, town first) →
Phase 3 → Phase 4. Each phase ships on its own and is useful alone.
