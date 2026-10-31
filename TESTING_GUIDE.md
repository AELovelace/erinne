# Built-in games testing

Run the server and Media regression tests from the repository root:

```text
.venv/Scripts/python.exe -m pytest tests/test_casino.py tests/test_media.py -q -p no:cacheprovider
node --check mnemerynne/web/static/games/casino/game.js
```

`test_casino.py` covers aces, blackjack payouts, roulette zero, slot returns, shared race/spin settlement, private hole cards, automatic standing, round rollover, room limits, identity, wager replacement, host transfer, concurrent requests, reconnects, expiry and refills. Media tests cover registry/page integration, signed-in and paired-screen access, input validation, cross-site refusal, access revocation and the existing Prism Drop Node suite.

For real-browser checks, install/use Puppeteer and Chrome, then run `node tests/connected-browser.mjs`. Set `PUPPETEER_MODULE` to an existing Puppeteer module path and `CHROME_PATH` to a browser executable if needed. The runner starts `python/casino_preview.py` on a free loopback port with a temporary database and two test accounts. It never reads the project's `.env` or live data. Screenshots go under ignored `runs/casino-browser/`.

The browser checks play all four games with two isolated authenticated contexts, compare settlement, refresh a seat, check 390px mobile overflow, exercise the remote message input, and verify host handoff. They also open the Media Games cards and verify the embedded game's parent remote input and status report. They use simulated visibility for both headless tabs; physically test two set-top boxes and their remote panels before release. Also check the available themes and reduced-motion settings on target displays.
