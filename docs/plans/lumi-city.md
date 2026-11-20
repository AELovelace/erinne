# Lumi City: apartments, two towns, player real estate, a free-walk town

Status: **built 2026-11-19** (one session, at Doll's request: "oneshot this for me, then we're going to test it on the
NAS, then we're going to port it to lumibot and keep the two versions in sync"). Not yet tried on the NAS, a TV box or a
phone. Follows `docs/plans/lumi-pet-social.md` (free walking, local chat, visiting flats).

## What Doll asked for (2026-11-19)
- Player real estate, like FF14: everyone starts in the **apartment** in town; you can **buy a plot of land**, or
  **sell yours at the real-estate market**. The **suggested value rises the closer a plot is to the centre**.
- The map **auto-expands when a town fills**; **street names and navigation**.
- **Two towns: LefTown and Right-City**; you live in one. The **city's entrances are on its left and right**, and
  entering always puts you on the same street tiles, so growing the city never changes how you get around.
- **Start with the apartments**: a lobby, and a door to your own apartment or a friend's.
- **Redo the arcade, market and the rest** for the new free-walk system.
- **Turn off tap-a-Lumi-to-visit**: the virtual world replaces it.
- **30 house styles** to buy at the real-estate store; **5 yard slots** per plot, each with **5 shop items and 10
  capsule-only items** from a **capsule machine at the real-estate office**.

## Doll's answers
| Question | Answer |
|---|---|
| How it looks | Free movement on an "isometric-feeling" 3/4 view like the old town map, but **Lumis may walk only on the streets** |
| Where new land goes | **On the outside, by the gates.** The centre never moves; every tile keeps its coordinates; the newest land is the cheapest |
| Apartment once she owns a house | **Keep both (FF14)**: the apartment is free forever, the house is a second home with its own rooms |

## How it's built
### The map (`mnemerynne/lumipet_data/city.json`, `city-map.js`)
- Tiles of 64 × 40 drawing units, 17 rows. **Downtown** is 31 columns starting at column −15. LefTown's block *n* sits at the
  same columns to its left forever, Right-City's to its right; each block is the same 11-column template with **8 lots**
  (2 on the avenue, 6 on the lanes). A **gate** (4 columns) sits past each town's last block.
- **Streets**: one long avenue through the whole city (Petal Avenue in LefTown, Lumi Way Downtown, Starlight Boulevard in
  Right-City); each block's cross lane is named from a list (LefTown flowers: Daisy Lane, Tulip Lane…; Right-City stars:
  Comet Street, Nova Street…), "Daisy Lane II" once the list runs out. **Addresses**: "3 Daisy Lane, LefTown".
- **Lot ids**: `l-<block>-<lot>` / `r-<block>-<lot>` (block 1 is next to Downtown). Page addresses: `?at=plot-l-1-3`,
  `?at=lobby-l`, `?at=gate-right`, any place id; older ids ("home", "arcade"…) still work.
- `city-map.js`: `build`, `free`, `move`, `reach`, `path` (breadth-first, then straightened), `step` (a TV remote hops
  door to door and corner to corner), `hop` (⏮ ⏭ a block), `arrive`, `streetAt`, `district`.
- **Districts** (who's near, who hears chat): `down`, `l<n>`, `r<n>`; a gate counts as the block after the last.

### The walker (`walker.js`)
`slide`, `toward`, `follow`, `heldWay`, `floor(plan)`. Her flat (`room-ui.js move`), the insides (`town-inside.js`) and
the city all walk with it.

### Drawing (`city-art.js`, `estate-art.js`, `town-inside.js`)
- The city is drawn a chunk (block, Downtown or gate) at a time, only the chunks near the camera, each kept once drawn.
  Each chunk is a ground layer plus one layer per tile row; **every Lumi (hers too) is drawn inside the picture** in the
  row her feet are on, so whoever is further forward is in front. Doorsteps light up with a ring when in reach.
- `estate-art.js`: the 30 houses and 75 yard items (drawn by a helper agent, contact sheets in the session scratchpad).
- `town-inside.js`: Lumi Heights' lobby (front desk, mailboxes, lift), its floors (8 doors with nameplates), Lumi Realty,
  and the Arcade, Casino, Market and Lumi-Soft field (their old pictures, now walked about in).

### The page (`game.js`, the "Lumi City" section)
- Out in the city: free walking (keys, stick, Tab5 pad, middle-click), a press on a door walks there and uses it.
- The list: whatever's in reach first, then **Go to…** (home, her house, every place, everyone's houses, the gates),
  **Where am I?**, Choose where to live, Show me in town, Say something.
- Lists that open: Go to…, the lift's floors, the front desk's residents, the gate's Leave town, and the office's
  Land for sale / Neighbours' plots / My plot (list ◀ ▶ price, take off, sell to the office) / House styles / Yard /
  Keepsake Capsule. Anything that spends coins or gives up a plot asks for a second press.
- Lumi Pet: `?at=house-<id>` is the house on a plot. Her own is her **second home** (`HOME2`): its own rooms
  (`pet.estate.rooms`), decorated with the same Home menu (decorate/paint carry `home: "house"`). Her flat's front door
  now opens onto Lumi Heights' lobby; her house's onto its plot; a visit's way out goes back where she came from
  (`G.store "visit-back"`).

### The server
- `mnemerynne/lumicity.py`: the city and estate catalog checks, apartments (`floor = 1 + (id − 1) // 8`, door letter A–H),
  suggested prices, the city view and every estate action. Migration `migrations/games/0003_lumi_city.sql`
  (`lumi_city_towns`, `lumi_plots`; an unowned lot has no row).
- Routes: `GET city`, `POST estate`, `GET heights/<town>`, `GET heights/<town>/<floor>`, `houses/<id>?home=house`,
  `POST town` with `district, x, y, face`; the stream's new `city` event.
- `coins.transfer` (a player buying a neighbour's listing pays them) and `coins.payout` (the office buying a plot back).

## My picks Doll hasn't confirmed
- Each town has **its own apartment building** at Downtown's ends: **Lumi Heights West** (LefTown) and **East**
  (Right-City). Home town is chosen on the first trip out (or by buying a plot there).
- **One plot per account.**
- Selling both ways: **list it** for players at 50–300% of the suggested price, or **sell to the office for 50%** at once.
- Prices: block 1 = **5000**, ×**0.88** per block outward, never under **1500**, **+20%** on the avenue, rounded to 50.
- **2 blocks** per town to start; a new block when **every** lot in that town is owned (a small server may never grow).
- Names: **Lumi City** (the app), **Lumi Realty**, **Keepsake Capsule** (200 coins, odds 55/25/14/5/1), the lane lists.
- **Gates**: coming into town from nowhere in particular lands on your own town's gate; walking up to a gate offers
  Home to my flat / Leave Lumi City / Stay.
- Furniture is owned once and **placed separately in each home**; one **free starter house** (Starter Cottage).
- The tavern, garden and café still open their own apps (no walk-about inside for them yet).
- House style and yard things **stay yours** when you sell (they come back with your next plot).

## Not done / later
- Per-app script lists (Lumi City still loads house.js: `room-styles.js` and `collection-art.js` need it at load).
- A TV box frame-rate check in the city; never tried on a phone or with a real remote.
- **Port to LumiBot** and keep the two in sync (Doll's next step). The pure modules (`walker.js`, `city-map.js`,
  `city-art.js`, `estate-art.js`, `town-inside.js`, `city.json`, `estate.json`) are self-contained on purpose so they
  can be copied as they are.
