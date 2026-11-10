# Quest integration boundaries

The Media games currently have no quests or campaign progress. Rewards exist in one place, `mnemerynne/coins.py`: online casino tables settle against the account's coins, a finished Prism Drop game and the day's first Lumi Pet visit pay a capped reward, and local multiplayer credits belong only to their table and award nothing.

A new reward must go through `coins.reward()` with a daily cap, and pay only for something the server itself saw.

If quests are added later, derive completion from a server-validated round transition in `mnemerynne/casino.py`, with an idempotent round identifier. Browser animations and displayed scores must never grant rewards. Document new objectives and persistence in this guide, the player checklist and the testing guide before shipping them.

The roulette ball's landing is a visual presentation of an already settled server result. Neither animation completion nor pressing Play is a quest-completion event.

Three-line slots settle once per player per round. Their three line returns are components of that settlement, not three independent rounds or reward events.

Lumi Pet has no quests or streaks. Its progress is levels: fights pay EXP (`15 + 3 × enemy level`, plus 5/12/25/50 for uncommon to legendary; Gamble and a lucky treat add more), a level needs `20 + 15 × level` EXP, and the cap is 50. Each level gives one skill point for six trees of eight skills, and levels 10, 15, 25, 35 and 45 open mod slots. Twenty enemies in five tiers stand in for quest content: seven common, five uncommon, four rare, three epic and the legendary Comet Drake. A new enemy is an entry in `lumipet_data/battle.json` (tier, body, colour, element, weak/resist/immune, shape, moves by level) and, if it needs a new body, a drawing in `enemies.js`. Her needs run down while away but there is no fail state, and a lost fight costs only a ten-minute nap. The digital ASCII neon fireplace is a planned ambient toy, with no objectives or reward hooks.

## Making dungeon content

The dungeon (`mnemerynne/lumidungeon.py`) is where Lumi Pet has places, treasure and puzzles. Floors are generated, so content is added as catalog entries, not as hand-built maps.

- **An environment** is an entry in `lumipet_data/dungeon.json`: `id`, `name`, `family` (mystical, fantasy or cyberpunk), `about` (one sentence, also given to the dialogue writer), `opens` (the level it unlocks at; one must be 1), `elements` (which creatures favour it) and a four-colour `palette`. It also needs a motif in `static/games/lumi-pet/dungeon-art.js` and, ideally, its own `tales/<id>.json`. The catalog allows more than the twelve shipped.
- **A trophy** is an entry in the `trophy` spot of `house.json` with `"price": 0` and `"found": "<environment id>"`, plus a drawing in `house.js`. Vaults give a trophy of their environment that she doesn't have yet.
- **A capsule prize** is an entry in a `house.json` spot with `"price": 0` and a `"rarity"`, a `closet.json` accessory with a `"rarity"` (each needs a drawing in `house.js` or `closet.js`), or a toy in `items.json` with `"price": 0` and a `"rarity"` (an emoji icon, no drawing). A house prize can't also be sold or found in the dungeon, and it can't be the spot's first object. The Star Capsule and Moon Capsule read their prices and odds from `closet.json`, whose `values` block sets what a prize with no shop price pays back as a repeat (half). Every spot except trophies, every closet slot and the toy box keep at least ten prizes or items, and a test checks that even an owner of everything loses coins on average.
- **A dungeon-only mod** is an entry in `battle.json` `mods` with `"price": 0`.
- **Sokoban levels** come from `python/make_lumi_sokoban.py`, which builds each backwards from its solved position and keeps only those a search can solve in a sensible number of steps. Hand-made levels may be added to `sokoban.json` in the usual `#  .$*@+` format; `tests/test_lumidungeon.py` solves every level, so one that can't be finished fails the suite.
- **Rewards** follow depth, not authored tables: see the tuning guide. Anything found is only kept if Lumi walks out with it.

In shared-TV casino mode, a remote is a separate seat, not a separate account or reward recipient. Signed input receipts prevent duplicate controls; they are not quest events. All players still share one authoritative round, and settlement remains the only potential future reward boundary. Pairing, joining, draft changes and player removal must not award anything.

TV text-size changes and canvas resize/redraw events are presentation-only. They must preserve active rounds, seat identities and pet memories and must never count as interactions, quest progress or rewards.
