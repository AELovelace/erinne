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
