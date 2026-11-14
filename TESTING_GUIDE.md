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
node --test tests/js/roulette.test.mjs tests/js/lumi_table.test.mjs
```

`test_coins.py` covers the shared purse: new purses, refusing an overdraft, bets to the house and wins back, reading a settled round twice, two tables sharing one balance, seats on one account, refills, forfeits, refunds from expired tables, local tables never touching a purse, daily caps, once-only payments, a TV spending its paired account, and the API refusing any amount or score a browser sends. `test_prism.py` covers Prism Drop's rules, which now run on the server, including the moving platforms: where they are at each step, turning at the walls, bouncing only a ball that's falling onto them, the breakout-style direction, and the two-bounce limit that lets every ball come down.

`test_casino.py` covers aces, blackjack payouts, roulette zero, slot returns, shared race/spin settlement, private hole cards, automatic standing, round rollover, room limits, identity, wager replacement, host transfer, concurrent requests, reconnects, expiry and refills. `test_each_account_seat_shows_its_own_lumi` checks that a coin seat carries its Lumi's glow and outfit (retired clothes dropped), that someone without a Lumi gets a fresh one's look, that a credit seat gets none, and that looking never changes or creates a save. Media tests cover registry/page integration, signed-in and paired-screen access, input validation, cross-site refusal, access revocation and the Node rule tests.

Slots tests cover wins on each of the three horizontal lines, combined returns, losing diagonals/columns, charging and replacing all three stakes, insufficient funds, the 30-coin refill threshold, and finishing old single-line rounds at their original cost before upgrading. Browser checks assert nine symbols, three lines, the displayed total before betting, the full balance deduction, individual line returns/highlights, net results, and shared results across accounts; screenshots include spinning and settled 3×3 layouts.

For real-browser checks, install/use Puppeteer and Chrome, then run `node tests/connected-browser.mjs`. Set `PUPPETEER_MODULE` to an existing Puppeteer module path and `CHROME_PATH` to a browser executable if needed. The runner starts `python/casino_preview.py` on a free loopback port with a temporary database and two test accounts. It never reads the project's `.env` or live data. Screenshots go under ignored `runs/casino-browser/`.

The browser checks play all four games with two isolated authenticated contexts, compare settlement, refresh a seat, check 390px mobile overflow, and verify host handoff. They check direct Left/Right picks, Up/Down stakes, Enter betting despite stale Leave-button focus, host-only Play, and blackjack Hit/Stand selection. They play a Prism Drop game through the server, reload partway to confirm it carries on, and check the coins it pays. They also open the Media Games cards, verify parent media-key routing and status reports, and pair a real test screen to send arrow/Enter/Play commands through its SSE stream. The roulette checks inspect all 37 labels, motion, persistent rotor identity and the server-result landing. Node geometry tests check the ball's final alignment for every possible result, zero included.

The browser checks also cover bet limits, Play before betting, and reduced-motion roulette settlement. They use simulated visibility for both headless tabs; physically test two set-top boxes and their remote panels before release. Also check the available themes on target displays.

**Lumi's Lounge by eye** (not automated): with two accounts whose Lumis wear different glows and clothes, both players should see the same two Lumis in the same seats. Dealer Lumi's line should change with each phase and name the result. Your Lumi hops with hearts on a win and gets a sweat drop on a loss, with your chips in front of her. On a real TV with eight remotes in slots, Dealer Lumi should sit on the table's top edge without covering a card. The ponies' legs gallop only during the race. With reduced motion on, nothing bounces. A roulette snapshot during a round must keep its spin position; repeated polling must not recreate the rotor or replay its landing. On a 1080p TV, each table should fit with no scrollbar, before a round and during one. Check each with eight online seats: all eight cushions in one row, with Slots' little reels inside each card. Opening **Table rules** makes the seat panel taller; during betting that may add a scrollbar, and it's the only thing that should.

## StickBob Runner

`tests/js/stickbob_runner.test.mjs` runs the rules against the real level data: landing, top speed, jump height, slide hitbox, falling, spikes, shooting, kicking, landing just short of a ledge without losing speed, and a long run that must not pile up level in memory. Its "habit runner" crosses every chunk at full speed with 60 steady jump habits and fails if any chunk lets fewer than a third through; if it fails after a re-export, fix that chunk in the GameMaker project (`scr_runner_slices.gml`, or `world1_chunks.json` with Doll's chunk editor) and export again. `test_game_rules_pass_in_node` in `tests/test_media.py` runs it with the other game tests.

By hand, on a computer with a mouse:

1. Open **Media → Games**. **StickBob Runner** is alone under **Mouse & keyboard**. On a phone or a paired TV that shelf isn't there.
2. Press **Run**. Check A/D, W or Space, S (slide while moving, crawl while still), left click to shoot where the mouse points, right click or Shift to kick, F or middle click for the knife.
3. Hold W and press Ctrl: the tab must stay open. Ctrl isn't a game key, unlike the GameMaker version.
   Wall jump: jump at a wall, and while in the air against it press away from it: StickBob stops and holds on for about a fifth of a second. Jump during that and he kicks up and away. Pushing into the wall and jumping must do nothing. Away, jump, back, away, jump… climbs one wall. The GameMaker game works the same way (`PlayerCharacter.gml`, `WALLJUMP_` macros).
4. Click outside the game or press P: it pauses, and no key stays stuck when you come back.
5. Fall in a pit: the score panel appears, with "+N coins" (1 per 10 m plus 1 per kill), and the Coins count at the top right goes up by that. Your best distance is still there after a reload.
6. As an admin, set **Settings → Entertainment → Media → Games → StickBob Runner coins a day** to a small number, such as 5. Play two runs: the second says today's limit is reached. Set it back to 0 (no limit).

`tests/test_runner.py` covers the coins: a run pays once, a claim faster than StickBob can run pays nothing, a new run drops the old ticket, the cap (off by default) and its checks.

If the GameMaker art or chunks change, run `.venv/Scripts/python.exe python/export_stickbob.py` and commit `static/games/stickbob-runner/assets/`.

## Lumi-Soft

`tests/test_lumisoft.py` replays the recorded runs in `tests/fixtures/lumi_soft_runs.json` on the server and needs every number to match what the page's `rules.js` recorded (score, hits, ranks, the step it ended on). It also covers the entry and continue fees (50, 100, 200, 400, 400), paying once, the wall-clock check, the daily cap and its setting, one run at a time, stage select opening in order, the Daily Range's one scored try and its board, bests, and the prizes landing in Lumi's house and closet (each once). `tests/js/lumi_soft.test.mjs` replays the same file in Node and has the robot (`tests/js/lumi_soft_bot.mjs`) play the runs again, plus scoring, pop-ups, the Popper's timing, wind, friends, ranks, and a drawing for every look, prop and boss. **After any rules or stages change, run `node tests/js/lumi_soft_bot.mjs` to re-record**, then both suites. In Chrome, `tests/lumi-soft-browser.mjs` (`LUMI_SOFT_ONLY=1 node tests/connected-browser.mjs`) clears the whole Training Range with a real mouse through the scope (the last pop ends the run and wins the rug: this once froze the game), plays a paid Stardust Garden run, pauses and ends it, and checks the coins, and opens the Daily Range, Prizes and Settings pages. Screenshots: `runs/casino-browser/lumi-soft-*.png`.

By hand, on a computer with a mouse:

1. **Media → Games → Mouse & keyboard** shows **Lumi-Soft** beside StickBob Runner (not on a phone or TV).
2. Training Range: hold the right button over a can. The scope opens with the crosshair right there, and doesn't drift until you push the mouse further out. Hold Shift: the sway settles and the breath bar drains; let it run out and the sway gets worse until it refills. Pop all three cans, then reload with R.
3. Story or Stage select: pop-ups rise from behind hedges, peekers slide out from walls, walkers cross, balloons float up. A water balloon's ring fills and Spotter Lumi warns you; let one land and a heart goes, with droplets down the screen. Pop a ♡ friend: −250, −5 s and Spotter Lumi is sad.
4. Let the clock run out: the continue panel offers 50 coins, then 100 on the next one, with a 10-second countdown. Continue: the score stays.
5. Teacup Tower: the wind line in the HUD says aim left, and far BBs land right of the crosshair. Deep Dungeon: it's dark until **G**; the glow bar drains.
6. Each boss: its bullseyes open and close; hitting one flashes it and resets its wind-up ring; the hearts over the radar count down.
7. Results show the server's score, ranks, coins (with "today's limit reached" once the cap is hit) and any prizes. Open Lumi Pet: a won piece is in her house list and can be put out for free; one not yet won says "Lumi-Soft prize · …" and can't be bought.
8. Daily Range: the first try is scored and shows on the board for another account; a second try says Practice and pays nothing.

Not tried yet: a real player's feel for difficulty and the coin rate (1 per 250 points is Doll's first guess to tune), and a slow PC's frame rate in the Night Fair.

## Lumi Pet

**Three apps.** Lumi Pet, Lumi Dungeons and Lumi's Garden are one pet in three apps that share one page kit (see the contributor guide). `tests/test_media.py` checks all three are listed on the "Lumi" shelf, that each page is the shared kit showing exactly its own tabs, and that neither new page asks for files of its own. `tests/test_lumigarden.py` checks each app's manifest (its own id, scope, name, icons and shortcuts to the other two), that the icon files exist, and that no other game has a manifest. `tests/js/service_worker.test.mjs` checks the garden reminder opens Lumi's Garden and that the old `#garden` address is still allowed. In real Chrome, `checkLumiApps` in `tests/lumi-pet-browser.mjs` opens each app, checks its tabs and Settings (the storyteller only in Lumi Dungeons, reminders never there, no "Open" row for itself), follows the **Open** rows round all three, compares the coins and level, and then shows Lumi Pet and Lumi's Garden a trip under way: an empty room, only the **Lumi is out exploring** row, the pantry still working, and that row landing in Lumi Dungeons inside the dungeon. `checkLumiPetFrame` opens Lumi's Garden from Lumi Pet inside Media's game window and checks the bar's name changes. The door (2026-11-11):
- `checkLumiApps`:
  - checks her room has a door that nothing covers;
  - presses it with the mouse at a 16:9 TV's size, on the part beside the left panel (at 1440×1000 the panel hides nearly all of it), and lands in Lumi Dungeons;
  - checks **Take her home** is there and goes back;
  - then does the same round trip with Care's last row, **Through the door**.
- `checkLumiPet`:
  - checks Lumi Dungeons opens with **Take her home** first;
  - after giving up a trip, checks the ending screen has it first with the cursor on it;
  - checks it lands in Lumi Pet with her welcome-home line, and that a reload doesn't repeat it.
- `tests/js/lumi_dungeon.test.mjs` checks the home row (first on an ending, which it reads first; not offered without `home`, as in a party) and the welcome line: the three endings, too old, undated, and still out.
- `tests/test_lumidungeon.py` checks each ending is dated (`ended.at`).
- The tavern (Lumi Dungeons' lobby): `tests/js/lumi_tavern.test.mjs` checks it draws by day and by night with no broken numbers or open groups, the window changing with the hour, and every breathing light being a group. `checkLumiApps` checks Lumi Dungeons shows the tavern (none of her furniture) and its name, and saves `runs/casino-browser/lumi-dungeons-tavern.png` to look at.
- Not automated: the trot by eye, and the door on a real TV with a remote. The remote reaches the door only through the Care row. The other checks below now open the app that has the tab they test. Not automated: installing each app from a real phone (three icons side by side on a home screen), an installed Lumi Pet opening Lumi Dungeons (it is outside that app's scope, so a phone may show it in a browser bar), and the Open rows on a real TV with a remote.

Run `.venv/Scripts/python.exe -m pytest tests/test_lumipet.py -q -p no:cacheprovider` for the server: the decay curve (last quarter in exactly one day, the same in one step or many), catch-up while away, rewards landing when a nap or play finished, cooldowns, the pantry and its limit, toys, the shop refusing what can't be afforded, the ledger, the one-time browser import, catalog validation, and the API refusing any result the page tries to name. Run `node --test tests/js/lumi_pet.test.mjs` for the page's rules: night boundaries, nap and wake overrides, moods, and reading the old browser save. The Media pytest suite runs all Node rule tests and checks Lumi's authenticated page, static assets, registry entry and paired-TV access. Syntax-check `static/games/lumi-pet/game.js` and `rules.js` under `mnemerynne/web/` with `node --check`.

Run `node --test tests/js/lumi_skills.test.mjs` for the Skills and Collection tabs: every skill has its own place below what it needs, the four states, what Enter does on a skill, the arrows reaching all 48 skills, the page's wires and shapes, the gallery's counts against every catalog, where each thing comes from, its pages and cursor, and a drawing for every one of its things. `tests/test_lumibattle.py` checks the trees branch, each in its own shape, that old builds still stand and bad shapes are refused. `checkLumiPetSkills` in `tests/lumi-pet-browser.mjs` walks both tabs in real Chrome by keyboard and mouse. `checkLumiPetPhone` runs her at phone size with touch: one screen that never scrolls, thumb-sized rows and tabs, the Press button, the arrow pad offering exactly the ways there are, the trees and gallery by tap, and her own manifest and icons; `tests/test_lumigarden.py` checks the manifest is public and that other games keep the main one. It can't press **Install Lumi Pet** (that opens the browser's own dialog), so installing is a hand check.

Run `.venv/Scripts/python.exe -m pytest tests/test_lumigarden.py -q -p no:cacheprovider` and `node --test tests/js/lumi_garden.test.mjs tests/js/lumi_garden_tree.test.mjs` for the Stardust Garden. Server: the catalog (42 flowers from two minutes to a day, gates in order, cut before perennial, the flower tree: three branches from Moondaisy, quick blooms sooner and shorter at each step for about the same powder a minute, three bridges and the crown, an `after` that loops refused, no two alike, 15 slots of 5 sold and 10 capsule decorations, bad files refused); decorations bought, put out, put away and sold; the Seedling Capsule's rarities and repeats; the first seven cosmetics carried over; a garden saved under the old ladder keeping everything and opening the tree from there; a flower opening through a bridge; a flower ready on time, waiting, then overgrown to half; perennials regrowing and Harvest all replanting cut flowers; a flower dug up (a growing one lost, a ready one paying first, a perennial not growing back, the plot taking another seed); per-garden unlocks from a flower it grows from, by coins or starpowder; plot prices; presses counted at a human rate; the hundredth coin of a day at 40 powder and the next at 80; the Monk's squeezing the same in one look or many and capped while away; rates rising with a learned skill, a fitted mod and a level; abilities and cooldowns; both minigames (the page never sees the whole watering sequence); gifts; cosmetics; Moonrise; the garden waiting during a trip; reminders (once, slow flowers only, quiet after a look or with the toggle off, and never running the press); and the page sending only choices (422). Page: the lists and drawers; the three pages (where each flower, helper and moonrise sits, each flower's state, what Enter says or does, the hops between pages, everything pressable drawn, names escaped); number format, what Enter does on a plot, every flower and cosmetic drawn, and what can be pressed when. `tests/lumi-pet-browser.mjs` `checkLumiPetGarden` plays it in real Chrome: planting and trimming by keyboard and mouse, the press, fertilizing by Enter, P and mouse, watering by arrows and mouse, the Helpers, Flower tree and Moonrise pages by arrows and mouse (Lumi steps off while a page is up), drawers, and a reload; `LUMI_GARDEN_ONLY=1 node tests/connected-browser.mjs` runs just that. By eye on a TV: the flower tree's 42 blooms should be easy to tell apart, the branch names readable, and the cursor easy to follow with the remote.

Run `.venv/Scripts/python.exe -m pytest tests/test_push.py -q -p no:cacheprovider` and `node --test tests/js/service_worker.test.mjs` for reminders. A stand-in phone decrypts what the server sealed (RFC 8291), the VAPID signature is verified, and a stand-in push service checks delivery, a gone device being forgotten, and an outage not raising. Reminder rules: each need once, nobody without a subscription, quiet hours by the phone's clock, several needs as one notification, three hours between notifications, nothing for a need already seen or fixed, a nap's end, retry after a failed send, and batching. The service worker test checks fixed wording, ignored injected text and links, and that it has no fetch handler. **Not covered: a real phone.** Before release, install the app from the https address on Android, switch on Settings → Reminders, lower a meter (edit the pet's JSON in `lumi_pets`, set `hunger` to 20 and `low` to `{}`), wait a minute for the worker, and check the notification arrives and opens Lumi. Also check iPhone from the Home Screen app.

Run `.venv/Scripts/python.exe -m pytest tests/test_lumibattle.py tests/test_lumi_editor.py -q -p no:cacheprovider` for battles and the catalog editor. Battles: the roster (20 enemies in five tiers, six trees of eight), refused bad catalogs, the damage formula, weak/resisted/immune/glow-matched elements, physical against defense and spells against ward, the Prism Shard copying her glow, Defend, speed order and first-strike moves, poison and wards wearing off, stuns, thorns, heals, Run's odds, scaling by level and tier, one waiting fight and stale buttons, a win paying once, the level curve and cap, defeat's nap and paid wake-up, potions, treat boosts, MP, skills learned in order, respec, four moves, mod slots, and every enemy fought to a finish at six levels. `node --test tests/js/*.mjs` adds the art checks: every house object, accessory and enemy body has a drawing, unknown ids fall back safely, and the Battle tab's lists send only choices. `python/lumi_pet_editor.py`'s window is not tested automatically; open it once and save an unchanged entry before relying on it.

Run `.venv/Scripts/python.exe -m pytest tests/test_lumidungeon.py tests/test_lumitales.py -q -p no:cacheprovider` for the dungeon and its dialogue. Health between trips: bruises come home, mend in their share of ten minutes, twice as fast in a nap, by bought food (not free) at home and on a trip, by a potion at home, and wholly by the paid wake. Dungeon: the catalogs; every Sokoban level solved by search and answers replayed strictly; 400 generated floors each whole, with their promised rooms, a dead-end vault and everything else reachable without it; solvable lamp boards; tougher enemies and more coins with depth; one trip at a time that waits; the page never told about rooms she hasn't entered or any puzzle's answer; treasure once; banking at the entrance paying once; trophies and dungeon mods not for sale; giving up; stairs; a fight carrying her health in and out and paying into the satchel; defeat emptying it but keeping EXP; running stepping back; potions; each lock kind; both games; every place's decor set whole, its pieces rare (likelier deeper), never found twice, hidden only in chests and creatures, and dropped by a beaten creature into the satchel and the story. Dialogue: the line check, the library and its bad-file refusals, every scenario in every environment, the generator against a stand-in Claude (one request per environment, the schema, the fallback, repeats and hand edits kept, a refusal skipped, a cut-off reply reported), live mode (facts only, unfit lines dropped, puzzle reports never sent, BFGguf), and the routes (the setting, one live line per moment, the rate limit). `tests/js/lumi_dungeon.test.mjs` covers the lists, Sokoban and lamps as played on the page, and the art. The browser runner sets out, reloads mid-trip, takes a step, runs from any fight and gives up with a second press. **Not covered by any automated test:** a real Claude key (run `mnemerynne lumi-tales --env moonlit-shrine --lines 3 --out runs/tales` once and read the file; try Live in Settings), a puzzle door solved in a real browser, and the remote on a TV box.

The connected browser runner also checks Lumi against the real server: the old browser save's moments arriving on the account, cuddle and its cooldown message, feeding by remote keys after stale focus, buying a cookie, a skill refused for lack of points, a fight (enemy drawn, bars, Defend by remote keys, picked up mid-fight after a reload, Run), a timed play with its countdown, one-thing-at-a-time refusal, the closet (try on, buy, capsule-only refusal, take off), the house (the room filling a wide screen, three window holes in the wall, open windows exactly when the theme has a backdrop, Left/Right on a spot changing nothing while she owns only one thing for it, a painted view tried from the spot's whole list and previewed in all three windows, Lumi centred, a rug tried and bought from its whole list), the controls (Next for the next tab, Left/Right flipping a closet place, and a house spot only through what she owns, the dungeon's own panel with the tabs folded away, the story at its top and the choices at its bottom, Look around, the mouse (a door, a lamp, a card and crate squares pressed in real Chrome) and Look after Lumi offering only snacks and a potion, arrows walking, Previous/Next on its list, Play for a potion, every door kind and the card game driven by keyboard, and a paired TV's real Previous/Next reaching Lumi through the command stream), glow and sound in Settings, a reload showing the same pet, Tab activation, mobile overflow, reduced motion, blocked storage, and Media-frame remote messages. Screenshots `lumi-pet.png`, `lumi-pet-fight.png`, `lumi-pet-closet.png`, `lumi-pet-house.png` and `lumi-pet-phone.png` are saved in `runs/casino-browser/`. The fight step only defends and runs so coin sums stay fixed; about once in several hundred runs a common enemy could still win, and the step then asks for a re-run. Not covered by any automated test: hours of real decay on a live server, two devices open at once, a real phone's notifications, and smooth scrolling on a real TV box.

## TV guests, Who's playing? and Dungeons with friends

**Server tests:**

- `tests/test_tv_guests.py`:
  - a guest's remote drives the owner's TV, as the owner
  - it is refused once removed
  - it takes a casino couch seat, and never drives a speaker
- `tests/test_lumi_tv_player.py`:
  - an OK press plays the guest's own Lumi and pays her visit
  - a pass works only on its own TV and pairing
  - once a pass stops holding it is refused, never swapped for the owner's pet
- `tests/test_lumiparty.py`:
  - the lobby, votes and their timing and dice
  - the puzzle circle and its limits, crates watched move by move, passing and leaving mid-turn
  - cards and cups
  - fights at once, and ghosts
  - each member's own finds, with coins paid once
  - what the page is never told
  - a party through the web app, and a couch party seating each remote as its own account

**Page tests:** `tests/js/lumi_party.test.mjs` covers `party-ui.js`.

**Chrome checks:** `tests/lumi-party-browser.mjs`, run by `connected-browser.mjs` (`LUMI_PARTY_ONLY=1` runs just these):

- two accounts make a party, join, get ready, vote and walk home
- a party's crates on the real page: the holder's moves go out one by one, and a watcher can't move them
- a real TV with nobody signed in: a TV guest's remote picks his own Lumi through Who's playing?, then two remotes play a couch party (screenshot `runs/casino-browser/lumi-couch-party.png`)

Without a project Puppeteer, run `npm install puppeteer-core` in a scratch folder. Set `PUPPETEER_MODULE` to its `lib/esm/puppeteer/puppeteer-core.js` and `CHROME_PATH` to Chrome.

**Not covered by a browser:**

- a party fight played through to the end (the server tests cover its ending)
- lamps and cards at a party
- anything on a real TV box or a physical remote

For those, see PLAYER_CHECKLIST steps 18–20.

## Co-op fights, stat points, fight items and moving in

Server: `tests/test_lumiparty.py` plays the co-op fight: one fight in the party and no fight rows; seat order (Jane can't go before Doll), a stale turn refused, aiming at a chosen creature, the creatures striking only after every Lumi has gone, a creature already down refused; the whole EXP to every Lumi (even one who never had a turn) and coins to each satchel, and what each used remembered for Auto; a potion from Doll's pocket for Jane and an X-Attack for Mina, refused when it would do nothing or the pocket is empty; a Lumi at 0 becoming a ghost who still gets her EXP; everyone fainting ending the trip; a turn left for `TURN_SECONDS` braced through; Run taking everyone back; leaving mid-fight passing the turn on. `tests/test_lumipet.py`: a new house is bare with 14 boxes; one box a day, its piece put out only into an empty spot; a worn piece can't be bought; an older house is emptied once with its coins given back once (bought at price, capsule prizes at their value, finds at nothing); names and colours checked and refused; stat points by hand (to the cap), by Auto and taken back; fight items bought to their cap; an older Lumi's points placed by Auto with one free reset. `tests/test_lumibattle.py` and `tests/test_lumidungeon.py` set levels with points spread evenly, so their numbers are as before.

Page: `tests/js/lumi_battle.test.mjs` (the Stats page, fight items in a fight and in the shop), `lumi_picker.test.mjs` (bare spots, the box row, worn pieces in their boxes, try-before-you-buy, Back landing on its row below the boxes), `lumi_party.test.mjs` (the co-op rows on your turn and someone else's, aiming and choosing a friend, a ghost, the couch menu, the browser's rows and scene with names escaped), `lumi_house.test.mjs` (the worn set drawn).

Real Chrome: `checkLumiPet` unpacks the first box and checks the mattress and the box count in the room, buys a rug, tries the Star rug (a tap shows it, Back puts it back, nothing spent), names her through the browser's prompt, and slides green to its top to see her colour change before keeping it. `checkLumiPartyFight` shows the page a party view with a co-op fight: all three Lumis drawn the same size in one row with their names and their own colours and clothes, both creatures, aiming by the Aim row and by a click, a potion for a friend sent with the right seat and turn, then the wait on a friend's turn with the arrows sending nothing. `checkLumiPartyBrowser` fills the browser with two parties, checks their cards and Lumis, that names are text, and that arrows and Enter join the right one. Screenshots: `lumi-party-fight.png`, `lumi-party-browser.png`.

Not automated: a real co-op fight walked into by three real people on three devices (the fight is driven only with a made-up view in Chrome; the server side is tested end to end in pytest); the couch's co-op fight with real remotes; the move-in on a real old save on the NAS (check the coins came back); a box opening the next real day.

## Star Capsule and Moon Capsule

Lumi Pet's capsules now give clothes, toys and house objects: 120 accessories (20 a slot, of which 105 come only from capsules), 10 Capsule-only toys and 150 Capsule-only house objects, ten or more in every closet slot and house spot except trophies. The newest 60 clothes are the "Star Capsule wardrobe" at the end of `closet.js`: in the Closet, flip through each place and check every piece sits on her properly, including the kitsune tails (which redraw her own tail in front of them). In the Closet, open each capsule a few times.

- The Star Capsule costs 150 coins and can give anything. The Moon Capsule costs 300 and only gives rare, epic or legendary things.
- Clothes go straight onto Lumi, a toy appears in the Toys tab ready to play with, and a house object goes straight into the room. A Capsule-only toy never shows in the Toys tab or the shop before it is won.
- A repeat changes nothing she has and says how many coins it paid back: half the shop price, or 30/60/90/200/500 by rarity for things not sold. Check the coin count matches.
- In **Home**, open a spot's whole list: prizes she hasn't won show 🔒 and say they only come from a capsule, they can't be bought, and once won they join the row and swap freely.
- Look through the new wallpapers and floors across the whole room for visible joins, and watch a room with several animated prizes on a TV box for stutter. Nobody has tried the new art on a TV box yet.

## Dungeon decor sets

Every dungeon place now has a decor set: one piece for each of the 15 house spots (not the trophy bracket), 180 in all. They are rare: a chest holds one 8% of the time on the first floor and 3% more per floor down, and a beaten creature drops one 4% of the time plus 2% per floor.

- In **Home**, open a spot's whole list: each place's piece shows 🔒 and says which place it is found in. It can't be bought.
- On a trip, open chests and win fights until a piece turns up (deeper floors are quicker). A chest names it when opened; a beaten creature's line says what it left behind. The satchel counts it as a thing for the house.
- Walk home and check it is in the room's spot list and swaps freely. Be beaten on another trip while carrying one and check it is lost with the satchel.
- Put a whole set out at once and look at it across the wide room by day and night, and on a TV box for stutter. Nobody has tried this art on a TV box yet.

## Performance on a big share

`tests/test_performance.py` covers the scan's write lock, the permission cache, the music list cache, connection reuse and the indexes. On the NAS, with something copied in or not: press around Files and Media while watching `journalctl -u mnemerynne-worker`, and no page should stall for the length of a scan. Remove someone's access to a folder from Windows: within 30 seconds lists stop showing it, and opening a file in it must fail at once. A brand-new folder must be usable straight away.

## Yarn Pop, Lumi Conga and the table lobby

Yarn Pop and Lumi Conga are the arcade's Breakout and Snake (slugs `breakout` and `snake`), with Lumi in every game since 2026-11-11.

- **Lumi in the arcade:**
  - `tests/js/lumi_sprite.test.mjs` checks every piece Lumi can wear still makes a well-formed SVG picture. Her picture is an image, which is strict XML: an outfit with a repeated attribute would leave her drawn as a plain shape.
  - `tests/arcade-lumi-browser.mjs` (in `connected-browser.mjs`; `ARCADE_ONLY=1` runs just it) dresses alice's Lumi, opens all four games, plays each a little, and saves pictures to `runs/casino-browser/arcade-*.png` for a person to look at.
  - `tests/test_arcade.py` covers `lumi_look`, each showdown seat's look, and the conga's colours on the live board.
- **Automated:**
  - `tests/test_arcade.py`: the server's rules, Python and JavaScript agreeing (needs Node), solo runs, showdown seats, ready, bets, settling and seats let go.
  - `tests/test_lobby.py`: casino polls that write nothing, seats and tables let go, TV tables, restarts.
  - `tests/js/breakout.test.mjs`, `tests/js/snake.test.mjs` and `tests/js/teacup_tower.test.mjs`: the games' rules.
  - **Lumi Conga's waiting Lumis:** `test_snake_food_kinds_are_not_streaky` checks nearly every game meets a +5 or +10 Lumi in its first ten, and the mix stays 70 / 22 / 8 (+1 / +5 / +10). Until 2026-11-11 the rolls crept along, so a third of games were all +1 for a long while. The roll now goes through `scramble` (`arcade.py` and `snake/rules.js`, which must match). By eye: a few solo games should each show gold and aqua Lumis now and then.
  - **Teacup Tower difficulty:** a simple robot that steers toward the next cup climbs 60–100 floors on most towers. If a change makes it much worse, the tower may have stopped being climbable.
- **On the NAS:** play a solo run of each with a keyboard, then with the TV remote. Open a showdown from two devices with a third watching and betting, and race. Then close one racer's game and check their seat empties within about 30 seconds.
- **Through the public hostname:** check the table stream stays live through nginx. Watchers' boards should keep moving during a race; if they freeze until the race ends, nginx is buffering `/api/media/games/*/tables/*/stream`.

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

Set `GAME_SCALING_ONLY=1` for the isolated browser scaling checks in `tests/game-scaling-browser.mjs`. They open six games (of Lumi's three apps, Lumi Pet) in the actual Media iframe on a paired TV, compare 1920×1080, 2560×1440 and 3840×2160 root fonts and board/control geometry, and resize back to verify no accumulated scaling. They also check saved manual size, live Bigger/Automatic/Cancel through the TV command stream, table preservation, canvas pixel density, horizontal overflow and unchanged unpaired desktop sizing. Since 2026-11-11 the four tables must also fit the TV's height with no scrolling at 1080p and 4K (the `tall` measurement). That covers a seated table only; to check a full table of eight, mid-round, by eye, look at the Lumi's Lounge section. Screenshots include `slots-1080p.png`, `slots-4k.png`, `lumi-pet-1080p.png` and `lumi-pet-4k.png` in `runs/casino-browser/`.

The shared-TV browser check also verifies eight active slot-player cards at 4K, with a `slots-eight-remotes-4k.png` capture. The Media pytest suite sends the real `Sec-Fetch-Dest: iframe` header to all game routes and checks inherited scale, no duplicated kiosk chrome/underscan, saved settings and unchanged non-game frames. Physically compare 1080p and 4K TVs after release; OS scaling and browser zoom change the CSS viewport that Automatic uses.

## Microsoft 365: the shared app, set live on Access

Run `.venv/Scripts/python.exe -m pytest tests/test_m365_link.py -q -p no:cacheprovider`. Two tests cover Access → Settings → Microsoft 365 → **Shared app registration**: with nothing in the settings file, an admin saves a client ID and secret and, with no restart, the tenant's consent link, the app's own sign-in (**Check**) and a person's link all send Microsoft the new ID and secret; non-admins are refused; a client ID that isn't one, an empty secret and a Secret ID pasted for its Value are refused with their reasons; the secret never comes back in any answer or the log and is stored encrypted; the server's own settings object is untouched. The second checks that Access wins over the settings file, that a token another process still holds from the old app is not reused, that a tenant's own app is left alone, that a secret the server can no longer read falls back and says so, and that **Remove** returns to the settings file.

Not automated: the form was driven once in headless Chrome with a throwaway preview and Puppeteer (refused when wrong, saved, shown without its secret, used by the consent link, kept over a reload, removed). Never tried against real Microsoft: on the NAS, save the real client ID and secret, press **Check** on a tenant, link an account from Preferences, and confirm the worker's next SharePoint sync and group refresh still work without restarting it.

## The front door and the services' units (services rewrite, Phase 0.5)

Run `.venv/Scripts/python.exe -m pytest tests/test_front_door.py tests/test_updates.py tests/test_roles.py -q -p no:cacheprovider`. `tests/test_front_door.py` checks the nginx config written from the registry (`mnemerynne/services/nginx.py`): each service's paths go to its own upstream on its own port, `/remote/` (websockets) and everything else go to the web app, `keepalive` follows how many processes a service runs on each profile, and a service added to the registry gets its route. It then drives `deploy/linux/front-door.sh` and `deploy/linux/valkey-setup.sh` in Git Bash with `systemctl`, `curl`, `nginx` and the app's Python stood in for: `services` installs the template and the web app's drop-ins (keeping one already there), moves each service to `mnemerynne@<name>`, and writes nginx for the address in the `.env` saved before the front door; a service that doesn't answer, or a different process answering on its port, gets its old unit back; a config nginx refuses puts the old one back; `valkey-setup.sh on` makes Valkey listen on its socket only, keeps the package's config once, adds the app's user to the valkey group, sets `MNEMERYNNE_VALKEY_URL` in both `.env` files and restarts the app's running units; it changes nothing when the app's user can't reach the socket or no config file is found; `off` takes the URL out of both files.

**Not covered: the real box.** Fedora's Valkey package layout (`/etc/valkey/valkey.conf`, `valkey.service`, `/run/valkey`) is written from memory; the script checks each and says which is missing. On the NAS, after `mnemerynne-admin update`: `sudo bash /opt/mnemerynne/deploy/linux/front-door.sh services`, then `status` (every `mnemerynne@` unit and port answers, nginx answers on 192.168.1.5:38200, the backup app still syncs, a Lumi party still plays). Then `sudo bash /opt/mnemerynne/deploy/linux/valkey-setup.sh on` and `status` (the ping as `mnemerynne` says True; each process logs "Events: through Valkey at unix:///run/valkey/valkey.sock"). If SELinux stops Valkey making its socket, `sudo ausearch -m avc -ts recent` shows it.

## nginx sends the files (services rewrite, Phase 1)

Run `.venv/Scripts/python.exe -m pytest tests/test_accel.py -q -p no:cacheprovider`. Through the real routes with `MNEMERYNNE_ACCEL` on: a download that came through the front door (its `X-Mnemerynne-Accel: 1` header) is an empty answer with `X-Accel-Redirect: /_accel/plain/data/...` and the same Content-Type and Content-Disposition; an uploaded picture shown inline goes to the `sandbox` location; the start of a text file is still the app's; without the header, or with the setting off, the app sends the bytes; a file outside the known folders, or one whose route set another policy, stays with the app. The folders (`sendfile.roots`) come from the service's `.env`. The nginx side: every folder gets an internal location per header set with the app's own headers (checked against `SecurityHeaders`' default), `/static/` from disk, local-only probes, the header cleared when accel is off, and `/remote/` repeating the forwarded headers. `front-door.sh accel on` (box stood in for) probes every folder before the app's setting flips, puts nginx and `nginx.conf` back when nginx can't read one, and `accel off` hands the files back; the SELinux module allows exactly the types found. Rate limits count across processes through Valkey (fakeredis) and fall back to this process when Valkey stops answering.

**Not covered: a real nginx.** Not run against nginx on this machine. On the NAS, after `front-door.sh accel on`: a download finishes and opens; a film seeks (Range); a picture preview and an uploaded HTML file shown inline still carry `Content-Security-Policy: default-src 'none'; sandbox` (browser dev tools, or `curl -sI`); a room attachment, a theme picture and the backup app's download work; `/_accel/plain/data/x` from outside answers 404; `accel off` puts everything back.

## games.db (services rewrite, Phase 2)

Run `.venv/Scripts/python.exe -m pytest tests/test_games_db.py -q -p no:cacheprovider`. The games migration has core's columns exactly (types, defaults, keys) and no reference to users. `migrate_all` moves every games row out of a core that has them (counted per table), joins to users still work through the read-only attach, and running it again moves nothing. Deleting a user clears their pet, purse and party seat in games.db; rows left by a user removed some other way are swept. The app moves the tables at start and its games pool reaches them. The worker's reminder sweep and the coin-ledger pruning find the table in core or games.db (`db.table_connection`). A games write doesn't hold core's write lock. `move-back` puts the rows back into core (a coin spent on B shows on A), sets games.db aside, and the next start moves them out again; it refuses when core still has the tables. The game tests that start the app and then read coins or pets directly use the `games_conn` fixture.

**Not covered: the box's real data.** On the NAS after the update: the web app's log says "Moved into games.db: ..." with the row counts; everyone's coins and Lumi are as before; a party, a casino table, an arcade run, Lumi-Soft and the garden all play; a push reminder still arrives; `sudo mnemerynne-admin check-orphans` removes nothing.

## rooms.db and the rooms service (services rewrite, Phase 3)

Run `.venv/Scripts/python.exe -m pytest tests/test_rooms_db.py tests/test_rooms.py -q -p no:cacheprovider`. The rooms migration has core's columns exactly and no reference to users. `migrate_all` moves every Rooms row, and a room's history reads the same afterwards (Rooms on/off is still core's, read through the attach). Deleting a user keeps their messages without their name and drops their membership; a user removed some other way is swept the same. `move-back rooms` gives older code its tables. Live events, with two Buses on one (fake) Valkey: a wake in one process wakes the other's streams; typing pushed in one reaches the other's listener exactly once (a Bus ignores its own echo); someone streaming from another process counts as online for @here and presence; a broken bus leaves presence to this process. A message the rooms service took that asks Erynne is answered by the web app (`rooms:ask`). The rooms service needs Valkey: nginx gives paths only to running services, and `front-door.sh services` leaves (or turns) it off without Valkey. The existing Rooms tests run against rooms.db now (the app moves the tables at start).

**Not covered: two processes on the box.** On the NAS with Valkey on and `front-door.sh services` run: two people in one channel from two browsers see messages, typing and presence at once; @here and @Erynne work; an automation and Claude's `post_to_room` post into a room; with Valkey off again (`valkey-setup.sh off`, `front-door.sh services`), Rooms carries on from the web app.

## media.db (services rewrite, Phase 4a)

Run `.venv/Scripts/python.exe -m pytest tests/test_media_db.py tests/test_media.py -q -p no:cacheprovider`. The media migration has core's columns exactly and no reference to users, files or folders. Every Media row moves, and a catalog row still joins core's files and a TV through the attach (a request connection reads them too). Deleting a user takes their remotes, guest places and progress (their TV stays if it's someone else's). A file or folder deleted on the share leaves the catalog at the next pass (`db.sweep_feature`), which core's references used to do at once. An audit row from a route on media.db still lands in core. `move-back media` gives older code its tables. Media's own tests run against media.db through the `media_conn` fixture.

**Not covered: the box's real catalog.** On the NAS after the update: the log says "Moved into media.db"; films, TV, music and pictures list and play; a TV and a remote pair and take commands; signage plays; a new file appears and a deleted one disappears after the next scan; TV guests and Who's playing? work.
