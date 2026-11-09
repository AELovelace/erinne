# Quest integration boundaries

The Media games currently have no quests or campaign progress. Rewards exist in one place, `mnemerynne/coins.py`: online casino tables settle against the account's coins, a finished Prism Drop game and the day's first Lumi Pet visit pay a capped reward, and local multiplayer credits belong only to their table and award nothing.

A new reward must go through `coins.reward()` with a daily cap, and pay only for something the server itself saw.

If quests are added later, derive completion from a server-validated round transition in `mnemerynne/casino.py`, with an idempotent round identifier. Browser animations and displayed scores must never grant rewards. Document new objectives and persistence in this guide, the player checklist and the testing guide before shipping them.

The roulette ball's landing is a visual presentation of an already settled server result. Neither animation completion nor pressing Play is a quest-completion event.

Three-line slots settle once per player per round. Their three line returns are components of that settlement, not three independent rounds or reward events.

Lumi Pet has no quests, streaks or fail state. Its only reward is the day's first visit, decided by the server's date. Its happy moments are a local keepsake counter, not trusted progress. Cuddles, snacks, star play and rest must stay optional; do not add missed-visit penalties. The digital ASCII neon fireplace is a planned ambient toy, with no objectives or reward hooks.

In shared-TV casino mode, a remote is a separate seat, not a separate account or reward recipient. Signed input receipts prevent duplicate controls; they are not quest events. All players still share one authoritative round, and settlement remains the only potential future reward boundary. Pairing, joining, draft changes and player removal must not award anything.

TV text-size changes and canvas resize/redraw events are presentation-only. They must preserve active rounds, seat identities and pet memories and must never count as interactions, quest progress or rewards.
