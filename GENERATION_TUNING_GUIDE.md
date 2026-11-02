# Casino rules and tuning

`mnemerynne/casino.py` is the authority. Randomness comes from `secrets.SystemRandom`; browser animation is decorative. These games do not use generated narrative content or a model.

| Setting | Current behavior |
| --- | --- |
| Seats / rooms | 8 seats per room; 100 live rooms; create blocked at 3 joined rooms per identity per game |
| Chips | 1,000 per new seat; free refill below the minimum spin/round cost (30 for three-line slots, 10 otherwise) between rounds |
| Bets | 10–500, multiples of 10; slots use a per-line bet and reserve 3× that amount; replaced bets refund the full prior reservation |
| Timers | 45s betting; 30s blackjack; 6s wheel/race/reels; 10s results |
| Expiry | 2h without a table request; stale rows removed on room creation |
| Blackjack | One 52-card deck each round; dealer stands on soft 17; naturals 3:2; normal wins 1:1; ties push |
| Roulette | Uniform 0–36; straight return 36×; outside return 2×; zero loses outside bets |
| Horses | Six equal chances; winner returns 5× stake |
| Slots | 3 columns × 3 rows, six equally likely symbols per cell; top/middle/bottom pay separately; per line: stars triple 24×, other triples 12×, pair 1× the line bet |

Returns include the stake, which was deducted when the bet was placed. Update the client rules text, Media help and payout tests with every rule change. No external wallet or money integration exists. Server deadlines advance on the next table request; a reconnect resumes the persisted phase and advances an elapsed deadline.

Three-line slots store `slot_lines: 3` on the round, a row-major 3×3 `reels` array per player, and three `line_payouts`. Total return is their sum; net is total return minus `bet × slot_lines`. For example, 50 per line costs 150, and a single ordinary triple returns 600 (net +450 if the other lines lose). Columns and diagonals never pay. A saved round without `slot_lines` keeps its old single-line wager and flat reels through settlement; the next round adopts three lines. No database migration is needed.

Roulette's SVG uses the European single-zero pocket order in `static/games/casino/roulette.js`. Its six-second spin follows the server deadline; after receiving the result, a 1.4-second decorative landing places the ball at that number, including the rotor's rotation. This occurs within the existing results window and never delays or determines settlement. Reopening a settled round and reduced-motion mode show the final position immediately. Pick highlights cover the pending choice, while the winning pocket gets a gold highlight.

## Lumi Pet

This companion uses authored copy and native SVG/CSS, with no model or image-generation dependency. Tune `static/games/lumi-pet/rules.js`: automatic sleep is 22:00–07:00 in device-local time, naps are two minutes, and interactions keep Lumi awake for fifteen minutes. Each activity adds one happy moment (capped at 999,999); nothing decays. Palette changes award nothing. Rose, Lagoon and Starlight are cosmetic choices.

The version-1 localStorage key is `mnemerynne-game:lumi-pet:companion`. It keeps the moment count, last activity, palette and temporary sleep override. Malformed or unsupported saves fall back to defaults; expired overrides use the clock again. There is no account sync, migration or backup on the server. Browser profiles share a companion, including across account changes. Clearing browser storage clears its memories. Keep the visible storage-failure message if persistence is unavailable. Presentation updates once a second while visible; activity expressions last four seconds, and reduced motion disables CSS animation.

## Shared-TV casino controls

Each remote gets 1,000 chips, independent `amount` (initially 50), `pick` (first option) and `hand` (Hit) fields. Arrow edits do not place a bet; Enter joins first, then submits bets, confirms Hit/Stand or refills when below the game's minimum. Play belongs to the first seated player. The existing 8-seat cap includes remote and ordinary account guests, with no extra TV seat. A late join watches the active round. Shared slot cards show all three lines per player, including the full three-line stake.

Pair every controller to the TV owner's account using existing screen/remote pairing. Signed inputs expire after 90 seconds; persisted deduplication receipts last 120 seconds and are bounded to 1,024 per room. Hitting the receipt limit refuses inputs until old receipts expire, rather than allowing replay. The client queue holds at most 128 pending inputs. Round/phase changes require the player to press again. Remote re-pairing or removal invalidates its old proof/identity; remove the old seat from the TV if needed. Empty TV tables remain resumable until ended or idle expiry. End TV table deletes the entire room; Remove player forfeits that seat's balance and stake and transfers host to the first remaining player.
