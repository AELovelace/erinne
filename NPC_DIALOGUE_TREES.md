# Casino dialogue and status copy

There are no branching NPC dialogue trees in the current Media games. The blackjack dealer is a rules-controlled hand, not a conversational character. Lumi Pet has authored companion responses, described below.

Current status copy lives in `mnemerynne/web/static/games/casino/game.js`: betting, playing, waiting, result returns, reconnect errors and next-round instructions. Roulette captions and accessible wheel descriptions live in `static/games/casino/roulette.js`. Static lobby copy lives in `templates/games/casino.html`; server refusal messages live in `mnemerynne/casino.py`. Keep these messages consistent with the actual phase and accessible through the existing live regions. Player names are rendered as text.

Future dialogue should preserve the TV's direct controls (Left/Right pick, Up/Down bet, Enter submit, Play start) and avoid obscuring bets, deadlines or payout information. The blackjack hand prompt switches to Left/Right choosing Hit/Stand and Enter confirming.

Status copy says **coins** at online tables and **credits** at local multiplayer tables; `unit()` in `game.js` and `_unit()` in `casino.py` choose the word from the table. Slots label the selected amount as **Coins per line** (or **Credits per line**) and show the total for all three lines before betting. Result rows display each line's return; the overall result uses all three stakes when reporting the net. Old saved one-line rounds are labeled until their next round starts.

## Lumi Pet companion copy

`static/games/lumi-pet/game.js` holds brief responses for cuddles, snacks, playing with stars, napping and gently waking. They are authored locally, not generated conversations. Alternate the activity lines without introducing obligations or guilt about absence. The live status region announces interactions; the remote gets the sleep state, happy-moment count and selected activity. `templates/games/lumi-pet.html` holds the greeting and controls. Clock-driven sleep is peaceful, never an illness or failure. The day's first activity adds a short "+25 coins" line to the status; keep it a small thank-you, never a reason to come back.

## Shared-TV casino messages

The TV's guide must explain that the first Enter joins and a later Enter submits a bet. Player cards identify the remote by its paired name and show the draft separately from the submitted stake. Host-only Play errors and invalid wagers belong to that player's card, not another player's controls. Reconnection/expired-input messages ask the player to check the TV before pressing again. A late join is described as watching until the next round. Remote panels continue to show the shared table summary; their individual draft and hand prompts are on the TV. Keep removal copy explicit about forfeiting credits and the distinction between leaving the view and ending the table.

Game dialogue, status copy and player-card prompts follow the TV's existing Text size setting, including inside game frames. Keep this text in scalable DOM/rem styles; 4K sizing must not require separate dialogue strings or truncate the player's betting/hand instructions.
