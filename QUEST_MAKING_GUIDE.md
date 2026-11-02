# Quest integration boundaries

The Media casino games currently have no quests, campaign progress or reward hooks. Their play chips belong only to a table and do not award account currency or unlock other content.

If quests are added later, derive completion from a server-validated round transition in `mnemerynne/casino.py`, with an idempotent round identifier. Browser animations and displayed scores must never grant rewards. Document new objectives and persistence in this guide, the player checklist and the testing guide before shipping them.

The roulette ball's landing is a visual presentation of an already settled server result. Neither animation completion nor pressing Play is a quest-completion event.
