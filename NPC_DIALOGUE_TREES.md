# Casino dialogue and status copy

There are no NPC dialogue trees in the current Media games. The blackjack dealer is a rules-controlled hand, not a conversational character.

Current status copy lives in `mnemerynne/web/static/games/casino/game.js`: betting, playing, waiting, result returns, reconnect errors and next-round instructions. Static lobby copy lives in `templates/games/casino.html`; server refusal messages live in `mnemerynne/casino.py`. Keep these messages consistent with the actual phase and accessible through the existing live regions. Player names are rendered as text.

Future dialogue should preserve the TV's arrow/Enter controls and avoid obscuring bets, deadlines or payout information.
