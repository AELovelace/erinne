# Casino rules and tuning

`mnemerynne/casino.py` is the authority. Randomness comes from `secrets.SystemRandom`; browser animation is decorative. These games do not use generated narrative content or a model.

| Setting | Current behavior |
| --- | --- |
| Seats / rooms | 8 seats per room; 100 live rooms; create blocked at 3 joined rooms per identity per game |
| Chips | 1,000 per new seat; free refill below 10 between rounds |
| Bets | 10–500, multiples of 10; replaced bets refund the prior reservation |
| Timers | 45s betting; 30s blackjack; 6s wheel/race/reels; 10s results |
| Expiry | 2h without a table request; stale rows removed on room creation |
| Blackjack | One 52-card deck each round; dealer stands on soft 17; naturals 3:2; normal wins 1:1; ties push |
| Roulette | Uniform 0–36; straight return 36×; outside return 2×; zero loses outside bets |
| Horses | Six equal chances; winner returns 5× stake |
| Slots | Three independent reels, six equally likely symbols; stars triple 24×, other triples 12×, pair 1× |

Returns include the stake, which was deducted when the bet was placed. Update the client rules text, Media help and payout tests with every rule change. No external wallet or money integration exists. Server deadlines advance on the next table request; a reconnect resumes the persisted phase and advances an elapsed deadline.

Roulette's SVG uses the European single-zero pocket order in `static/games/casino/roulette.js`. Its six-second spin follows the server deadline; after receiving the result, a 1.4-second decorative landing places the ball at that number, including the rotor's rotation. This occurs within the existing results window and never delays or determines settlement. Reopening a settled round and reduced-motion mode show the final position immediately. Pick highlights cover the pending choice, while the winning pocket gets a gold highlight.
