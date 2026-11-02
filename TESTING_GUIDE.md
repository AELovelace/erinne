# Built-in games testing

Run the server and Media regression tests from the repository root:

```text
.venv/Scripts/python.exe -m pytest tests/test_casino.py tests/test_casino_tv.py tests/test_media.py -q -p no:cacheprovider
node --check mnemerynne/web/static/games/casino/game.js
node --test tests/js/roulette.test.mjs
```

`test_casino.py` covers aces, blackjack payouts, roulette zero, slot returns, shared race/spin settlement, private hole cards, automatic standing, round rollover, room limits, identity, wager replacement, host transfer, concurrent requests, reconnects, expiry and refills. Media tests cover registry/page integration, signed-in and paired-screen access, input validation, cross-site refusal, access revocation and the existing Prism Drop Node suite.

Slots tests cover wins on each of the three horizontal lines, combined returns, losing diagonals/columns, charging and replacing all three stakes, insufficient funds, the 30-chip refill threshold, and finishing old single-line rounds at their original cost before upgrading. Browser checks assert nine symbols, three lines, the displayed total before betting, the full balance deduction, individual line returns/highlights, net chips, and shared results across accounts; screenshots include spinning and settled 3×3 layouts.

For real-browser checks, install/use Puppeteer and Chrome, then run `node tests/connected-browser.mjs`. Set `PUPPETEER_MODULE` to an existing Puppeteer module path and `CHROME_PATH` to a browser executable if needed. The runner starts `python/casino_preview.py` on a free loopback port with a temporary database and two test accounts. It never reads the project's `.env` or live data. Screenshots go under ignored `runs/casino-browser/`.

The browser checks play all four games with two isolated authenticated contexts, compare settlement, refresh a seat, check 390px mobile overflow, and verify host handoff. They check direct Left/Right picks, Up/Down stakes, Enter betting despite stale Leave-button focus, host-only Play, and blackjack Hit/Stand selection. They also open the Media Games cards, verify parent media-key routing and status reports, and pair a real test screen to send arrow/Enter/Play commands through its SSE stream. The roulette checks inspect all 37 labels, motion, persistent rotor identity and the server-result landing. Node geometry tests check the ball's final alignment for every possible result, zero included.

The browser checks also cover bet limits, Play before betting, and reduced-motion roulette settlement. They use simulated visibility for both headless tabs; physically test two set-top boxes and their remote panels before release. Also check the available themes on target displays. A roulette snapshot during a round must keep its spin position; repeated polling must not recreate the rotor or replay its landing.

## Lumi Pet

Run `node --test tests/js/lumi_pet.test.mjs` for clock boundaries, nap/wake expiry, unchanged memories after months away, invalid saves, cosmetic changes and nonmutating activity transitions. The existing Media pytest suite runs all Node rule tests and checks Lumi's authenticated page, static assets, registry entry and paired-TV access. Syntax-check `static/games/lumi-pet/game.js` and `rules.js` under `mnemerynne/web/` with `node --check`.

The connected browser runner also checks Lumi's activities, direct Enter after stale focus, Tab activation, refresh persistence, colour/sound, mobile overflow, reduced motion, blocked storage, and Media-frame remote messages. Pet screenshots are saved beside the casino captures in `runs/casino-browser/`. Physically verify remote navigation on a set-top box and the day/night appearance on target themes. Server backups do not include this device-local pet.

## Multiple remotes on one TV

`tests/test_casino_tv.py` checks eight distinct seats with no spectator slot, independent drafts and wagers for all four games, host-only starts, duplicate input receipts, concurrent database connections, round/phase changes, blackjack choices and hidden hole cards, refills, host transfer, empty displays and explicit table closure. API tests use the actual panel pairing/command flow and verify signed inputs, cross-screen refusal, malformed/expired proofs, re-pair revocation and TV removal. Existing Media tests verify the additional controller envelope without changing normal remote commands.

`tests/casino-tv-browser.mjs`, included by `tests/connected-browser.mjs`, pairs three distinct bearer-authenticated panels to one TV. For every casino game it sends real SSE commands through Media and the iframe, checks independent concurrent bets, host-only Play, refresh, late joins, shared results, narrow layouts and host transfer. Captures are `runs/casino-browser/<game>-one-tv.png`. Physically test two or more hardware panels on one set-top box before release, especially named-seat readability from the couch and switching a panel between TVs. No remote firmware change is required for the existing arrow/Enter/Play commands.

The same browser check also removes the roulette session pointer to verify **Resume on this TV**, then fills slots with eight remotes and checks that all eight player cards fit the TV viewport without scrolling.

Set `CASINO_TV_ONLY=1` when running `node tests/connected-browser.mjs` to run only the shared-TV browser scenarios against the same isolated preview. Omit it for the complete pet/casino/Media browser suite. A failed browser run captures the visible game and its error copy under `runs/casino-browser/failure.png`.
