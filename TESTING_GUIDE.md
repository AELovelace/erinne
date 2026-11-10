# Built-in games testing

## H.265 playback regression

Run `.venv/Scripts/python.exe -m pytest tests/test_media.py -q -k hevc_sessions -p no:cacheprovider` with ffmpeg/ffprobe and the libx265 encoder installed. This generates a disposable 72-second HEVC/AAC sample and checks both repackaging and software H.264 conversion: a complete playlist, retained segments, and at least 400 decoded frames between 32 and 67 seconds. It checks the server output beyond the initial buffer; it does not establish that a particular TV/browser, source file, network or hardware decoder plays correctly.

## Sound and subtitle tracks

Run `.venv/Scripts/python.exe -m pytest tests/test_media.py -q -k track -p no:cacheprovider`. The unit tests cover ffprobe track listing (ffmpeg's own `0:s:n` numbers survive skipped teletext), the sound-track choice (pick, then remembered language, then default), and the ffmpeg commands for a picked sound track and for burned-in picture subtitles on each backend. With ffmpeg installed, `test_films_offer_their_sound_and_subtitle_tracks` builds a two-sound-track MP4 with a text subtitle. It checks direct play of the first track, a remuxed session that really carries the second (mono) track, and the WebVTT conversion and its cache.

Not automated: burned-in PGS was checked once by hand. It was a hand-made `.sup` muxed into an MKV and sent through the software burn-in command, with the pixels checked: the box shows from 1 s to 3 s, and a session started at 1.5 s skips the line already on screen, as documented. The player UI was checked once in headless Chrome with a throwaway preview and Puppeteer. It covered the lists, cue alignment in direct and remuxed sessions, the A and C keys, and the remembered languages. On a real TV, check that 🗣 and CC appear, and that the phone remote's 🗣 and 💬 switch tracks.

## Files: Select all and zip downloads

Run `.venv/Scripts/python.exe -m pytest tests/test_zip_downloads.py -q -p no:cacheprovider`. It covers:
- a folder's zip: structure, empty folders, CRCs, deflated text and stored pictures;
- several items: shared top level, same names kept apart as "name (2)", zip naming;
- everything in a place, with nothing from the recycle bin;
- refusals to other people, tampered links and signed-out requests;
- a folder that is only on the way down to a granted one (no files of its own);
- the 20,000-file cap;
- a file vanishing while zipping (named in `Not included.txt`);
- zip names that can't climb out of the folder they unzip into.

Not automated: the page was checked once in headless Chrome with a throwaway preview and Puppeteer:
- **Select all** ↔ **Select none**;
- Download of everything here and of one folder (right-click → **Download as zip**), with both zips' contents checked;
- the folder details' **Download as zip**;
- the 390 px phone layout with no overflow.

On the NAS, download a multi-gigabyte share folder through the public hostname. Check that it starts at once and finishes, and that 7-Zip and Windows' **Extract all** both open it.

## SoundCloud in Media

Run `.venv/Scripts/python.exe -m pytest tests/test_soundcloud.py -q -p no:cacheprovider`. It uses a stand-in for SoundCloud's API and covers:
- profile names, including lookalike hosts and short links;
- one token reused, shared under load, replaced once after a 401, and not retried with a bad key;
- the token never leaving `api.soundcloud.com`, even from a hostile `next_href` or redirect, and never reaching a browser;
- no stream lookups while listing, the daily stop, and a 429 stopping every call;
- blocked tracks left out, previews flagged, albums told apart from playlists;
- the key absent from the API answers and the log;
- whose profile shows on a screen, a paired computer and a phone;
- casting rebuilt on the server, and bad list keys refused;
- search of tracks, playlists and profiles, its words staying a query value, and no stream lookups;
- stations leaving out their own track and previews, and starting with that track when cast;
- a screen's saved status not keeping track names;
- Kiosk settings changing one field at a time, so older box helpers keep working.

`tests/test_screen_wifi.py` covers the box helper's field.

Not automated: checked once in headless Chrome with a throwaway preview, Puppeteer and a real key:
- the tabs, list layout, Load more and album pages;
- real audio playing, seek, next track and the credit links;
- the 390 px phone layout, the Screens row, and the TV page with arrow keys.

Also not automated: checked once in headless Chrome against a stand-in SoundCloud with one-second clips:
- search and its Tracks, Playlists and Profiles tabs, and opening a profile from the results;
- a station running well past its first batch without repeating a song, Next pressed quickly, and a plain list ending at its end;
- the search box and station button at 390 px.

On the NAS, add the key, then check a real TV box with its remote, **Play on** from a phone, an iPhone (Safari plays the stream without hls.js) and the Kiosk settings field after a box update. Start a station with the real key too: SoundCloud's reference doesn't say whether related tracks work with a server key.

## Running everything quickly

`PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider -n logical` runs the whole suite on every CPU thread (`pytest-xdist`, a dev dependency). On a 16-core machine it takes about a minute instead of seven. Leave out `-n logical` to debug one test. `PYTHONUTF8=1` matters on Windows when the account name isn't plain ASCII.

## Big uploads and the database

`tests/test_share.py` and `tests/test_sync.py` check that a file's bytes reach the share with the database free, that browser uploads go straight into their folder under a hidden name, that refused or over-limit uploads leave nothing behind, that a folder renamed during the copy still gets the file, and that the scan ignores uploads in progress and clears abandoned ones.

Not automated: on the NAS, upload a file of a few GB to a library while using Erynne elsewhere. Other pages should keep saving. In Windows, check that someone with access to that folder over SMB can open the finished file.

## Game checks

Run the server and Media regression tests from the repository root:

```text
.venv/Scripts/python.exe -m pytest tests/test_casino.py tests/test_casino_tv.py tests/test_coins.py tests/test_prism.py tests/test_media.py -q -p no:cacheprovider
node --check mnemerynne/web/static/games/casino/game.js
node --test tests/js/roulette.test.mjs
```

`test_coins.py` covers the shared purse: new purses, refusing an overdraft, bets to the house and wins back, reading a settled round twice, two tables sharing one balance, seats on one account, refills, forfeits, refunds from expired tables, local tables never touching a purse, daily caps, once-only payments, a TV spending its paired account, and the API refusing any amount or score a browser sends. `test_prism.py` covers Prism Drop's rules, which now run on the server, including the moving platforms: where they are at each step, turning at the walls, bouncing only a ball that's falling onto them, the breakout-style direction, and the two-bounce limit that lets every ball come down.

`test_casino.py` covers aces, blackjack payouts, roulette zero, slot returns, shared race/spin settlement, private hole cards, automatic standing, round rollover, room limits, identity, wager replacement, host transfer, concurrent requests, reconnects, expiry and refills. Media tests cover registry/page integration, signed-in and paired-screen access, input validation, cross-site refusal, access revocation and the Node rule tests.

Slots tests cover wins on each of the three horizontal lines, combined returns, losing diagonals/columns, charging and replacing all three stakes, insufficient funds, the 30-coin refill threshold, and finishing old single-line rounds at their original cost before upgrading. Browser checks assert nine symbols, three lines, the displayed total before betting, the full balance deduction, individual line returns/highlights, net results, and shared results across accounts; screenshots include spinning and settled 3×3 layouts.

For real-browser checks, install/use Puppeteer and Chrome, then run `node tests/connected-browser.mjs`. Set `PUPPETEER_MODULE` to an existing Puppeteer module path and `CHROME_PATH` to a browser executable if needed. The runner starts `python/casino_preview.py` on a free loopback port with a temporary database and two test accounts. It never reads the project's `.env` or live data. Screenshots go under ignored `runs/casino-browser/`.

The browser checks play all four games with two isolated authenticated contexts, compare settlement, refresh a seat, check 390px mobile overflow, and verify host handoff. They check direct Left/Right picks, Up/Down stakes, Enter betting despite stale Leave-button focus, host-only Play, and blackjack Hit/Stand selection. They play a Prism Drop game through the server, reload partway to confirm it carries on, and check the coins it pays. They also open the Media Games cards, verify parent media-key routing and status reports, and pair a real test screen to send arrow/Enter/Play commands through its SSE stream. The roulette checks inspect all 37 labels, motion, persistent rotor identity and the server-result landing. Node geometry tests check the ball's final alignment for every possible result, zero included.

The browser checks also cover bet limits, Play before betting, and reduced-motion roulette settlement. They use simulated visibility for both headless tabs; physically test two set-top boxes and their remote panels before release. Also check the available themes on target displays. A roulette snapshot during a round must keep its spin position; repeated polling must not recreate the rotor or replay its landing.

## Lumi Pet

Run `.venv/Scripts/python.exe -m pytest tests/test_lumipet.py -q -p no:cacheprovider` for the server: the decay curve (last quarter in exactly one day, the same in one step or many), catch-up while away, rewards landing when a nap or play finished, cooldowns, the pantry and its limit, toys, the shop refusing what can't be afforded, the ledger, the one-time browser import, catalog validation, and the API refusing any result the page tries to name. Run `node --test tests/js/lumi_pet.test.mjs` for the page's rules: night boundaries, nap and wake overrides, moods, and reading the old browser save. The Media pytest suite runs all Node rule tests and checks Lumi's authenticated page, static assets, registry entry and paired-TV access. Syntax-check `static/games/lumi-pet/game.js` and `rules.js` under `mnemerynne/web/` with `node --check`.

Run `.venv/Scripts/python.exe -m pytest tests/test_push.py -q -p no:cacheprovider` and `node --test tests/js/service_worker.test.mjs` for reminders. A stand-in phone decrypts what the server sealed (RFC 8291), the VAPID signature is verified, and a stand-in push service checks delivery, a gone device being forgotten, and an outage not raising. Reminder rules: each need once, nobody without a subscription, quiet hours by the phone's clock, several needs as one notification, three hours between notifications, nothing for a need already seen or fixed, a nap's end, retry after a failed send, and batching. The service worker test checks fixed wording, ignored injected text and links, and that it has no fetch handler. **Not covered: a real phone.** Before release, install the app from the https address on Android, switch on Settings → Reminders, lower a meter (edit the pet's JSON in `lumi_pets`, set `hunger` to 20 and `low` to `{}`), wait a minute for the worker, and check the notification arrives and opens Lumi. Also check iPhone from the Home Screen app.

Run `.venv/Scripts/python.exe -m pytest tests/test_lumibattle.py tests/test_lumi_editor.py -q -p no:cacheprovider` for battles and the catalog editor. Battles: the roster (20 enemies in five tiers, six trees of eight), refused bad catalogs, the damage formula, weak/resisted/immune/glow-matched elements, physical against defense and spells against ward, the Prism Shard copying her glow, Defend, speed order and first-strike moves, poison and wards wearing off, stuns, thorns, heals, Run's odds, scaling by level and tier, one waiting fight and stale buttons, a win paying once, the level curve and cap, defeat's nap and paid wake-up, potions, treat boosts, MP, skills learned in order, respec, four moves, mod slots, and every enemy fought to a finish at six levels. `node --test tests/js/*.mjs` adds the art checks: every house object, accessory and enemy body has a drawing, unknown ids fall back safely, and the Battle tab's lists send only choices. `python/lumi_pet_editor.py`'s window is not tested automatically; open it once and save an unchanged entry before relying on it.

Run `.venv/Scripts/python.exe -m pytest tests/test_lumidungeon.py tests/test_lumitales.py -q -p no:cacheprovider` for the dungeon and its dialogue. Dungeon: the catalogs; every Sokoban level solved by search and answers replayed strictly; 400 generated floors each whole, with their promised rooms, a dead-end vault and everything else reachable without it; solvable lamp boards; tougher enemies and more coins with depth; one trip at a time that waits; the page never told about rooms she hasn't entered or any puzzle's answer; treasure once; banking at the entrance paying once; trophies and dungeon mods not for sale; giving up; stairs; a fight carrying her health in and out and paying into the satchel; defeat emptying it but keeping EXP; running stepping back; potions; each lock kind; both games. Dialogue: the line check, the library and its bad-file refusals, every scenario in every environment, the generator against a stand-in Claude (one request per environment, the schema, the fallback, repeats and hand edits kept, a refusal skipped, a cut-off reply reported), live mode (facts only, unfit lines dropped, puzzle reports never sent, BFGguf), and the routes (the setting, one live line per moment, the rate limit). `tests/js/lumi_dungeon.test.mjs` covers the lists, Sokoban and lamps as played on the page, and the art. The browser runner sets out, reloads mid-trip, takes a step, runs from any fight and gives up with a second press. **Not covered by any automated test:** a real Claude key (run `mnemerynne lumi-tales --env moonlit-shrine --lines 3 --out runs/tales` once and read the file; try Live in Settings), a puzzle door solved in a real browser, and the remote on a TV box.

The connected browser runner also checks Lumi against the real server: the old browser save's moments arriving on the account, cuddle and its cooldown message, feeding by remote keys after stale focus, buying a cookie, a skill refused for lack of points, a fight (enemy drawn, bars, Defend by remote keys, picked up mid-fight after a reload, Run), a timed play with its countdown, one-thing-at-a-time refusal, the closet (try on, buy, capsule-only refusal, take off), the house (the room filling a wide screen, three window holes in the wall, open windows exactly when the theme has a backdrop, a painted view previewed in all three, Lumi centred, buy and place), glow and sound in Settings, a reload showing the same pet, Tab activation, mobile overflow, reduced motion, blocked storage, and Media-frame remote messages. Screenshots `lumi-pet.png`, `lumi-pet-fight.png`, `lumi-pet-closet.png`, `lumi-pet-house.png` and `lumi-pet-phone.png` are saved in `runs/casino-browser/`. The fight step only defends and runs so coin sums stay fixed; about once in several hundred runs a common enemy could still win, and the step then asks for a re-run. Not covered by any automated test: hours of real decay on a live server, two devices open at once, a real phone's notifications, and smooth scrolling on a real TV box.

## Rescan media

`tests/test_media.py::test_rescan_media_asks_the_worker_to_scan_now` covers the button's API and the worker picking it up. On the NAS, with the worker running: copy a new film into the Media folder, press **Settings → Entertainment → Media → Rescan media**, and check that the note reads **Scanned.** and the film count goes up within a few seconds. Half-typed text in the other Media settings must survive it.

## Phone remote mode

`tests/test_media.py::test_a_phone_pairs_itself_as_a_remote_and_gets_a_panels_powers` covers pairing, refusing a name in use, the casino seat proof, working after sign-out and the screens group.

On a real phone, with Media open on a TV: open **Media → Remote**, pair, and check each button against the panel's list in the `Mnemerynne-tab` README. The arrows should move the TV's highlight and flash cyan. Sign out and reopen the page: it should still control the TV. Join a casino table from the remote and check it gets its own seat. Then **Screens → Unpair** twice, and confirm it's gone from **Media → Screens**. Removing it from another device should send the phone back to the pair page within a few seconds. Install it from **Media → Remote** in the browser on Android and on an iPhone. Both should get a second icon named **Remote** beside Erynne, opening on the buttons with no browser bar. On the iPhone, pair the installed app with its code. Installing one app must not replace or remove the other. In a phone browser, **☰** should end with **Install Erynne** and **Install TV Remote**, stacked. Inside the installed Erynne app it should show only **Install TV Remote**, which copies a link. Someone outside the screens group should see only **Install Erynne**.

## Multiple remotes on one TV

`tests/test_casino_tv.py` checks eight distinct seats with no spectator slot, independent drafts and wagers for all four games, host-only starts, duplicate input receipts, concurrent database connections, round/phase changes, blackjack choices and hidden hole cards, refills, host transfer, empty displays and explicit table closure. API tests use the actual panel pairing/command flow and verify signed inputs, cross-screen refusal, malformed/expired proofs, re-pair revocation and TV removal. Existing Media tests verify the additional controller envelope without changing normal remote commands.

`tests/casino-tv-browser.mjs`, included by `tests/connected-browser.mjs`, pairs three distinct bearer-authenticated panels to one TV. For every casino game it sends real SSE commands through Media and the iframe, checks independent concurrent bets, host-only Play, refresh, late joins, shared results, narrow layouts and host transfer. Captures are `runs/casino-browser/<game>-one-tv.png`. Physically test two or more hardware panels on one set-top box before release, especially named-seat readability from the couch and switching a panel between TVs. No remote firmware change is required for the existing arrow/Enter/Play commands.

The same browser check also removes the roulette session pointer to verify **Resume on this TV**, then fills slots with eight remotes and checks that all eight player cards fit the TV viewport without scrolling.

Set `CASINO_TV_ONLY=1` when running `node tests/connected-browser.mjs` to run only the shared-TV browser scenarios against the same isolated preview. Omit it for the complete pet/casino/Media browser suite. A failed browser run captures the visible game and its error copy under `runs/casino-browser/failure.png`.

## 1080p and 4K game sizing

Set `GAME_SCALING_ONLY=1` for the isolated browser scaling checks in `tests/game-scaling-browser.mjs`. They open all six games in the actual Media iframe on a paired TV, compare 1920×1080, 2560×1440 and 3840×2160 root fonts and board/control geometry, and resize back to verify no accumulated scaling. They also check saved manual size, live Bigger/Automatic/Cancel through the TV command stream, table preservation, canvas pixel density, horizontal overflow and unchanged unpaired desktop sizing. Screenshots include `slots-1080p.png`, `slots-4k.png`, `lumi-pet-1080p.png` and `lumi-pet-4k.png` in `runs/casino-browser/`.

The shared-TV browser check also verifies eight active slot-player cards at 4K, with a `slots-eight-remotes-4k.png` capture. The Media pytest suite sends the real `Sec-Fetch-Dest: iframe` header to all game routes and checks inherited scale, no duplicated kiosk chrome/underscan, saved settings and unchanged non-game frames. Physically compare 1080p and 4K TVs after release; OS scaling and browser zoom change the CSS viewport that Automatic uses.
