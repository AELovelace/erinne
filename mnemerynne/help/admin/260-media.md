# Media

Media is a Kodi-style media centre for films, TV shows, music and pictures from the share, plus live TV and internet radio. People see it under **Media**; this page is the admin side. Setting up the server (ffmpeg, the GPUs) is covered in the deployment README, section 5k.

## Settings → Entertainment → Media

- **Media is on for this organisation:** Media is off until an admin turns it on. Turning it on:
  - makes the **Media** folder on the share with **Music**, **Movies**, **TV** and **Pictures** inside, or uses a Media folder that is already there;
  - starts cataloging after each share scan.

  While it's off:
  - nobody sees the Media tab;
  - its pages and API answer as if they didn't exist;
  - anything playing stops.

  The folders, the files and the catalog are kept. Turning it on or off is logged (`settings.media`).
- **Media folder on the share:** **Media** unless you choose another folder at the top of the share, such as an existing `Plex` or `Videos` folder. **Use this folder** takes an existing folder as it is, or makes a new one. Either way, **Music**, **Movies**, **TV** and **Pictures** are made inside it if they're missing; names in any case count. Set it before turning Media on, and nothing called Media is made.
  - A Media folder made before TV shows existed gets its **TV** folder by itself, once, at the next catalog pass. If you delete it afterwards, it isn't made again.
  - Switching to another folder keeps the old folder's files and catalog, so switching back is instant.
  - Renaming the folder in Windows is fine: Media follows it.
  - Changes are logged (`settings.media_folder`).
- **Rescan media:** finds new and changed files now, instead of waiting for the automatic check (every minute by default, `MNEMERYNNE_SHARE_SCAN_SECONDS`). It scans the whole share, then the catalog, and the counts above it update when it finishes. A big batch of new files is read a little at a time, so some may show as **waiting** for a few minutes. **Read every file again** and **Look everything up online again**, under Playback, are for files already in the catalog. Rescans are logged (`media.scan`).
- **Who may use Media:** an AD or Entra group, or several separated by `;`. Only members see the Media tab, its pages and its API. Empty means **nobody**, admins included, so name a group after turning Media on. Leaving the group closes Media for that person, and their screens, at once.
- **Who may use screens:** the group whose members may pair a TV as a screen and cast to it from their phone. They must be in the Media group too. Empty means nobody. When an owner leaves either group, their screens stop at once; owners can still remove their own screens.
  Remote panels (the `Mnemerynne-tab` firmware on an Elecrow CrowPanel, `mnemerynne-tab-5` on an M5Stack Tab5, or `mnemerynne-tab-fnk0104b` on a Freenove FNK0104B) follow the same group: a panel controls its owner's screens only while its owner may use screens.
  All three builds pair as speakers, so each panel is also one of its owner's screens, so it counts toward the 10 screens. Its key still never opens Media. For each track cast to it, the server hands it a ticket that works for 6 hours, and checks the caster's access again at every fetch. Tracks the panel can't decode (ALAC, for example) are converted to MP3 once and cached under the media cache's `speaker` folder (the last 40 are kept).
- **Who sees what:** the share's permissions on the Media folders, set in Windows like any library. A Movies subfolder closed to someone is closed in Media too, including its posters. The same goes for TV: closing a show's folder hides the show and its poster, and closing a season's folder hides that season.
- **Transcoding:** how films the browser can't play are converted.
  - **Automatic** tries the AMD card (VAAPI), then the NVIDIA card (NVENC), then the CPU, and keeps the first that works.
  - **Test transcoding** encodes two seconds with each backend and reports which work on this server.
- **Transcodes at once:** how many films may be re-encoded at the same time; **0** turns transcoding off. Copying a film into a browser-friendly package (**Repackaged**) is cheap and doesn't count.
- **Sound and subtitle tracks:** the player's 🗣 and **CC** buttons list a film's tracks. ffprobe reads them when the film is played (once per version of the file, kept in memory), so they need no rescan and no migration.
  - A sound track other than the first plays through a **Repackaged** session, because browsers only play a file's first sound track. It doesn't count toward **Transcodes at once**.
  - Text subtitles are converted to WebVTT once and cached under the media cache's `subtitles` folder (the 300 most recently used are kept). Converting one reads the whole film file, so the first time is slow on a big file over the share.
  - Picture subtitles (PGS, DVD, DVB) are **burned in**: the overlay is drawn on the CPU, then the chosen backend encodes. So it's a transcode (it counts, and **0** blocks it), and on VAAPI the picture is decoded on the CPU instead of the card. Teletext and closed captions aren't offered.
  - The phone remote's 🗣 and 💬 send `audio_track` and `subtitles`. Remote panels may send them too (`remotes.ACTIONS`), but the tablet firmware doesn't have buttons for them yet.
- **Look up artwork online:** with this on, the following come from online sources:
  - from TMDB: posters, backdrops and plots of films and TV shows, and episode descriptions (episode names too, where the file gave none);
  - from MusicBrainz and the Cover Art Archive: album covers.

  More about what is looked up and sent:
  - TV episodes are asked for one season at a time, and only once the show itself has been found.
  - Only film titles and years, TV show folder names and season numbers, and album and artist names are sent.
  - Art that came with the files is never replaced. A show's `poster.jpg` added later replaces a poster found online.
  - TMDB needs an API key (themoviedb.org → Settings → API). It is stored encrypted and never shown again.
- **Radio station list:** SGCRelay's station list (`https://sadgirlsclub.wtf/rr/healthz` by default). Empty means no relay stations.
- **Read every file again** and **Look everything up online again** redo the catalog on the next passes. Use them after changing tags or posters in bulk.
- **Files that couldn't be read** lists what the catalog gave up on. A file is tried again when it changes.

## SoundCloud

**Media → SoundCloud** shows a SoundCloud profile's tracks, albums, playlists, likes and reposts, and plays them in the bar at the bottom. It needs a SoundCloud API key, which needs an Artist Pro subscription: get one at [soundcloud.com/you/apps](https://soundcloud.com/you/apps), then put the **client ID** and **client secret** in **Settings → Entertainment → Media**. Both are stored encrypted, browsers never get them, and neither appears in the log. Until they're saved, the tab says SoundCloud isn't set up.

**Whose profile shows:**
- On a phone or a computer, each person types their own on the tab.
- On a TV, the one set for that screen: in its row in **Media → Screens** (any screen, even while it's off), or in **Kiosk settings** on a screen box. A screen with none shows its owner's. Speakers can't play SoundCloud.

**Limits, all SoundCloud's:**
- About 1,500 track starts a day for the whole server. Mnemerynne stops at 1,400 and says when to try again, and a track started again within its link's lifetime doesn't count twice.
- 500 Likes requests a day. Lists are read 200 items at a time and reused for ten minutes, so normal use is far below this.
- 3,000 searches an hour. Searches and a station's related tracks are lists, so neither counts as a track start; each song a station plays counts once, like any other. A station left playing keeps going until someone stops it, so it can use up the day's track starts on its own only if several screens run one nonstop.
- If SoundCloud answers "too many", every SoundCloud call waits until the time it gives.

**What SoundCloud's API terms mean here:** nothing is stored. Lists and links stay in memory for minutes, audio goes straight from SoundCloud to the browser, and a screen's saved status says only "SoundCloud", not the track. Every track shows its uploader and a link back to SoundCloud. The terms limit commercial use and ban ads or sponsorship around tracks. If it plays in a public space, the public-performance licences are yours to hold.

Some tracks are blocked in the server's country or preview-only. Blocked ones are left out and counted under the list; previews say **Preview only**.

## Live streams and stations

- **HLS stream** or **RTMP stream:** an address the server pulls, for example `http://10.1.1.21/stream/hls/live.m3u8` or `rtmp://10.1.1.21/live/stream`. The server repackages it for the browser, so streams only reachable on the LAN still play for people outside. The picture is copied, not converted, so it should be H.264.
- **YouTube link:** a video, a playlist, or a channel's `youtube.com/channel/UC…` address, which plays whatever the channel is streaming live. It plays in YouTube's own player, with YouTube's ads and controls. A channel's `@name` address can't be embedded: open the channel and use **About → Share channel → Copy channel ID**. Videos whose owners turned embedding off show **Watch on YouTube** instead of playing.
- **Radio station:** any MP3 or AAC stream address. Stations added here are listed after SGCRelay's.
- **Turn off** hides a source without forgetting it. Changing or removing one stops anyone watching it.
- **Playing now** lists the server's running sessions: what each one is doing, which encoder it uses, and how long it has been idle. A session stops a minute after its last viewer leaves.

Adding, changing and removing sources is logged (`media.*`).

## Lumi Pet

Media → Games includes Lumi, a companion using the existing Media permissions and paired-screen access. She is three games on one shelf (**Lumi Pet**, her room; **Lumi Dungeons**, her dungeon trips and fights; **Lumi's Garden**, the Stardust Garden), which are one pet on one save: nothing was added to the database when they were split, and all three use the same API under `/api/media/games/lumi-pet/`. Each can be installed as an app of its own (`/media/games/<slug>/manifest.webmanifest`, public like the main manifest). Each account has one pet, kept in this server's database (table `lumi_pets`, migration **0064**; normal backups include it) and shared by that person's phone, computer and paired TVs. Her hunger, energy and joy run down while nobody visits, along a curve whose last quarter takes a day; nothing is lost at zero. Her shop spends the account's coins on snacks, treats and toys, recorded in `coin_transactions` as `lumi-pet_buy`. Items and prices are in `mnemerynne/lumipet_data/items.json`; the server checks that file when it starts and refuses to start on a mistake, naming the item. No service or setting is needed. The digital ASCII neon fireplace is currently a design-list item, not an installed game.

**Reminders (push notifications):** people can let Lumi notify their phone or computer (Web Push to the installed app; migration **0065**, tables `push_subscriptions` and `lumi_push_events`). Nothing needs configuring: the server makes its own signing key the first time, in `push-vapid.pem` in the data folder. Back it up with the rest of that folder; if it is lost, people simply switch reminders on again. The **worker** sends them, looking at subscribed people's pets once a minute, so reminders stop while the worker is down. It must be able to reach the browser makers' push services on port 443 (`fcm.googleapis.com`, `*.push.services.mozilla.com`, `*.notify.windows.com`, `*.push.apple.com`); no other address is ever contacted, and messages are encrypted so those services cannot read them. Browsers only allow notifications on **https**, so they are unavailable from a plain `http://` local address. Set `public_url` to the https address so the push services are given a contact for this server.

**Lumi Pet's fights, house and closet** need no setup. With the neon theme, the room's windows show the theme's own scrolling city backdrop (`static/parallax/`); with a theme that has no backdrop, or with **Scene** set to none or an image, a drawn city is shown in them instead. Fights are kept in `lumi_battles` (migration **0066**), one in progress per person. Coins spent show in `coin_transactions` as `lumi-pet_decor`, `lumi-pet_wear`, `lumi-pet_capsule` (either capsule), `lumi-pet_mod`, `lumi-pet_potion`, `lumi-pet_heal` and `lumi-pet_respec`; fight winnings are type `reward` with a reference `lumi-battle:<id>`, and have no daily limit. Capsule repeats show as `lumi-pet_buyback`, new coins paid back to the account. Everything that can be owned or fought is in four files under `mnemerynne/lumipet_data/`: `items.json`, `house.json`, `closet.json` and `battle.json`. To change prices or numbers, run `python/lumi_pet_editor.py` on the server's copy: it checks an edit with the server's own rules before saving and keeps the previous file as `.bak`. Restart the web service and the worker afterwards. An update replaces these files, so keep a note of local changes.

**Lumi Pet's dungeon** needs no setup either. A trip is kept in `lumi_runs` (migration **0067**), one per person. Coins found are paid only when Lumi walks out, as type `reward` with reference `lumi-run:<user>:<trip>`. Its catalogs are `dungeon.json` (the twelve environments), `sokoban.json` (puzzle levels, made by `python/make_lumi_sokoban.py`) and the trophies and decor sets in `house.json` (its `"found"` entries), all under `mnemerynne/lumipet_data/`. **Dialogue** is plain files of lines in `lumipet_data/tales/`: `default.json` and one per environment. To have Claude write more, run `mnemerynne lumi-tales --env all` on a machine with `ANTHROPIC_API_KEY` set (it never reads a key from the database); it adds lines to those files, about one request per environment, for you to read and edit before restarting the web service. A bad line (markup, too long, a name the game can't fill in) stops the server at start, naming the file. **Live storytelling** is each person's own choice and cost: it uses the Claude key and model they saved under Preferences → Claude, or BFGguf if they are in that group, sends only game facts (the place, the floor, what just happened), and is limited to 12 lines a minute per person. Nothing is sent anywhere unless they switch it on.

**Lumi Dungeons with friends** (parties, `mnemerynne/lumiparty.py`) needs no setup. A party is one JSON document in `lumi_parties`, and `lumi_party_members` keeps each account to one party at a time (migration **0073**). Who is still there is kept in memory, like the casino and arcade tables. A party nobody has touched for two hours breaks up by itself; a trip still out is given up and its satchels are lost. Each member's coins are paid only when the party walks out of the entrance together, as type `reward` with reference `lumi-party:<code>:<trip>:<user>`, into that member's own coins. On a TV too: a party is each pet's own progress, not a casino table, so the "couch tables play for free credits" rule doesn't apply. Party fights are ordinary rows in `lumi_battles`, marked with the party's code. The timings (20 seconds to vote; 3 minutes for crates, 2 for lamps, 3 rune guesses and 6 card flips a turn; 30 seconds before a quiet puzzle holder loses their turn, 60 before a quiet fighter counts as having run) are constants at the top of `lumiparty.py`. The live storyteller isn't used in parties, so nobody's Claude key is billed for a group trip.

**TV guests** (migration **0072**, `media_screen_guests`) let one person's remotes drive another person's TVs. The owner adds or removes them under **Media → Screens → TV guests**, and the access log records `screen.guest_add` and `screen.guest_remove`. A guest's remote commands are built as the **TV's owner**: what a guest starts plays from the owner's library, with the owner's permissions. The TV is told the guest's name as the sender. A guest can't drive the owner's speakers, override someone using the TV, or open a screen box's Kiosk settings. In the Lumi apps, **Who's playing?** turns a remote's signed OK press into a **play pass** (`mnemerynne/lumi_tv.py`). The pass is signed with the app's secret key, lasts at most 12 hours, and is bound to that one screen and that remote's pairing. It is checked again on every request: removing the guest, re-pairing the remote, or using the pass on another TV refuses it at once. Lumi never quietly falls back to the owner's pet. Every press a TV receives from a remote now also carries the remote's id (not secret; the signed proof is still what the server trusts), so a couch game can keep one cursor per remote.

## StickBob Runner

StickBob Runner (Media → Games → Mouse & keyboard) pays coins at the end of each run: 1 per 10 m plus 1 per kill, recorded in `coin_transactions` as type `reward`, game `stickbob-runner`, with a reference `stickbob-runner:<user>:<run>`. Unlike Prism Drop, the run happens in the browser, so the server can only check that a claim is possible: each run gets a ticket when it starts (table `runner_runs`, migration **0070**, at most one per person), and a distance faster than StickBob can run, or a ticket used twice, pays nothing. Someone could still script a believable claim. **Settings → Entertainment → Media → Games → StickBob Runner coins a day** caps what one person can earn from it per day, by the server's date. It is **0, no limit**, unless you set it. Changes are logged with the other Media settings.

## Lumi-Soft

Lumi-Soft (Media → Games → Mouse & keyboard) is a cardboard target gallery built from Doll's GameMaker sniper prototype, played in the browser. Unlike StickBob Runner, its score is checked properly: the page sends every shot (when, and where it was aimed), every reload and how many continues it used, and `mnemerynne/lumisoft.py` replays the whole run with `lumisoft_rules.py` (a copy of the page's `rules.js`) before paying. A run costs 25 coins, each continue costs more (50, 100, 200, then 400), and a run pays 1 coin per 250 points, recorded in `coin_transactions` as game `lumi-soft` with reference `lumi-soft:<run>`. A run claiming more play than the time since it started pays nothing. **Settings → Entertainment → Media → Games → Lumi-Soft coins a day** caps one person's pay per day (**0**, no limit, by default). Migration **0074** adds `lumisoft_runs` (tickets: one live run per person), `lumisoft_bests` (best scores per stage, Story and each day's Daily Range) and `lumisoft_progress` (stages cleared, creatures popped, prizes won). Its prizes are pieces in Lumi Pet's catalogs marked `"prize": "lumi-soft"`: they can't be bought, rolled from a capsule or found, and Lumi-Soft puts them in the player's house or closet when they're won. The stages are `mnemerynne/lumisoft_data/stages.json` (made by `python/make_lumisoft_stages.py`). No setup is needed.

## Lumi Launch

Lumi Launch (Media → Games → Arcade, and a cabinet in Lumi Town's Arcade) fires the player's own Lumi from a star cannon for distance; a go is three launches on one seeded course. Like Lumi-Soft, its distances are checked properly: the page sends only each launch's presses (`[frame, button]`, the buttons being `ok`, `up` and `down`), and `mnemerynne/lumilaunch.py` flies her again with `lumilaunch_rules.py` (a line-for-line copy of the page's `rules.js`) before paying. A go costs 25 coins and pays 1 coin per 20 m, with no daily limit unless **Settings → Entertainment → Media → Games → Lumi Launch coins a day** sets one (`launch_daily_cap`), recorded in `coin_transactions` as game `lumi-launch` with reference `lumi-launch:<go>`, so a go pays once. A go claiming more flying than the time since it started pays nothing. Couch turns on one screen are free play and never reach the server, except to ask, on a paired TV, whose remote pressed OK (`POST /api/media/games/lumi-launch/tv/player`, checked the same way as Who's playing?: the TV's owner's own remotes and their TV guests' remotes only). Its tables are in `games.db` (`migrations/games/0002_lumi_launch.sql`, the first games table born there rather than moved from core): `lumi_launch_runs` (tickets: one live go per person; finished ones are kept a day) and `lumi_launch_bests` (each person's longest go and launch, for the board). It needs no setup. Gamepads work through `static/games/gamepad.js`, a small helper any built-in game can load. Lumi Pet loads it too (2026-11-19) for **free walking** in room mode, through an opt-in held channel: `MPad.start(onAction, onHold)` reports each pad's walking direction (the left stick analog past a 0.15 dead zone, the D-pad a full step), and `MGame.onHold` in `static/games/common.js` does the same for held arrows and WASD and for a remote panel's gamepad mode (its D-pad's button-up events). Direction actions from a held source arrive tagged `{ held: true }`; games that don't call `onHold` see no change. A remote panel's press mode stays single presses, so a TV remote still steps Lumi thing to thing. The walking itself is pure JavaScript in `static/games/lumi-pet/room-ui.js` (`move`, `reach`, `blocks`) and never reaches the server; only where she stands is remembered, in the browser.

Lumi Town's **local chat** (2026-11-19, `mnemerynne/lumitown.py`) lets people in the same place (a street's map, or inside one place) talk: `POST /api/media/games/lumi-pet/town/say` takes typed `text` or a quick-phrase id (`lumitown.PHRASES`), and `GET /api/media/games/lumi-pet/town/stream` is a server-sent-events stream that delivers each line to the others in that place and a `moved` nudge when anyone arrives, leaves or moves. Both travel on the event bus (`events.py`, channel `lumi-town`), so they cross processes when Valkey is on. A shared TV can't carry its play pass on a stream, so it hears lines through `POST .../town`, which also returns the place's recent lines. Chat is **never stored**: each place keeps its last 30 lines in memory for ten minutes. Typed lines are cleaned (control, format and zero-width characters removed, one line, at most 140 characters) and the page shows them as text only. Rate limits use `events.count`: one line per 1.5 s and ten per 30 s per person. Only someone with **Show me in town** on can speak. Muting is per device (the browser's own storage). There is no word filter and no moderation log, by design.

**Visiting flats** (2026-11-19, `mnemerynne/lumihouse.py`): someone's flat is their Lumi Pet room mode, opened for a visitor at `/media/games/lumi-pet?at=visit-<account id>` (Media's game frame passes `at` through; its pattern allows digits for this). `GET /api/media/games/lumi-pet/houses` lists the flats a person may visit; `GET .../houses/<id>` is the public view (`lumipet.house_view`: what stands in each spot, how many moving boxes are left, the Lumi's look, and nothing else from the save) once they may come in. The host's **front door** (`door` in the pet save: `knock`, the default, `open` or `closed`) and **block list** (`blocked`, account ids) decide that (`lumihouse.access`); a blocked person is told the door is closed. `POST .../houses/<id>/knock` needs the host at home, meaning their Lumi Pet room mode is open and reporting itself. The host answers with `POST .../knocks/<id>/answer`, and a yes gives a three-hour pass. Every flat page (the host's own, and each visitor's) reports where its Lumi stands with `POST .../houses/here` every five seconds and as it walks; the answer lists who else is in the flat, the room's recent chat lines and (for the host) knocks waiting. Steps, knocks, answers and "go home" travel on the same event bus channel and stream as town chat, and a TV hears them through its POSTs instead. Closing the door or blocking someone (`act` `door` / `block`) sends the guests home at once. Everything about visits (who's where, knocks, passes) is in memory only: a restart just means visitors knock again.

Phase 4 (2026-11-19) added: **emotes** (`POST .../town/emote`, `lumitown.EMOTES`, one a second, delivered like chat lines but never kept); the **guestbook**, which unlike chat is stored, in the host's pet save (`guestbook`, the last 30 lines, `lumipet.sign`). Visitors who may come in sign it with `POST .../houses/<id>/guestbook`, once per flat per ten minutes, and visitors can read it in the house view (names, never account ids). The host removes lines with `act` `guestbook_remove`. **House-warming coins**: arriving in a friend's flat pays 10 coins the first time each day per flat, up to 30 a day (`coins.reward`, game `lumi-housewarming`, reference `lumi-housewarm:<guest>:<host>:<date>`). In town, other people's Lumis now take a press, which opens a visit to their flat.

**Lumi City** (2026-11-19, `mnemerynne/lumicity.py`, plan `docs/plans/lumi-city.md`) turns Lumi Town into one long city that Lumis walk around freely: LefTown on the left, Downtown in the middle, Right-City on the right, and a gate at each end. Everyone keeps a free flat in their town's **Lumi Heights**, numbered by account (8 doors a floor: account 12 is flat 2D), and may buy **one plot of land** for a second home, a house with its own rooms. Furniture is owned once and placed separately in each home. Migration **games/0003** (`migrations/games/0003_lumi_city.sql`, in `games.db`) adds two tables. `lumi_city_towns` holds how many blocks each town has. `lumi_plots` holds one row per owned plot. A lot with no row is office land, for sale. Deleting an account deletes its row, so the plot goes back to the office. Plots cost coins, recorded in `coin_transactions` as game `lumi-city`:
- `lumi-city_plot`: bought from the office;
- `lumi-city_trade`: bought from another player, paid straight to the seller, reference `lumi-plot:<plot>:<bought>:<version>`;
- `lumi-city_sell`: sold back to the office, paid from the house account, reference `lumi-plot-sell:<plot>:<bought>:<version>`;
- `lumi-city_style`, `lumi-city_yard` and `lumi-city_capsule`: house styles, yard pieces and the Keepsake Capsule;
- `lumi-city_buyback`: capsule repeats.

What each person owns to put on a plot, and their home town, are in their pet save (`estate`, `home_town`). The routes are `GET /api/media/games/lumi-pet/city` (the whole city), `POST .../estate` (buy, list, sell, styles, yard, capsule), `GET .../heights/<town>` and `.../heights/<town>/<floor>` (the apartment buildings; a closed door shows no name), and `GET .../houses/<id>?home=house` (someone's house). In town, `POST .../town` now takes a district (`down`, `l3`, `r2`...) and a point; people are seen, and their steps streamed, within one block either side, while chat and emotes stay within one district or building.

**Tuning Lumi City** means editing two files under `mnemerynne/lumipet_data/`. The server checks both when it starts and refuses to start on a mistake, naming it. Restart the web service afterwards. An update replaces both files, so keep a note of local changes.
- `city.json` is the map, shared with the page's `city-map.js`. Its `start_blocks` (default 2) is how many blocks of 8 lots each town starts with. It only counts the first time a town is created: after that a town grows by itself, one block at its gate, whenever every lot it has is owned. It never shrinks, so lowering it later changes nothing. Its `price` sets the curve. A lot's suggested price is `base` × `decay` for each block out from Downtown, × `avenue` for a lot facing the avenue, rounded to the nearest `round`, and never below `floor`. The defaults are 5000, 0.88, 1.2, 50 and 1500, so block 1 is 6000 on the avenue and 5000 on a lane. The office buys a plot back for `sell_office` (0.5) of that price, and a player may list theirs for `list_min` to `list_max` (0.5 to 3) times it. Prices are only ever worked out by the server. Its tile rows (Downtown, the block template, the gate), places and street names are checked too. Every row must be the same width, each lot and building must be one rectangle with its door on a street, the avenue must run the whole way, and every street must join up.
- `estate.json` holds the 30 house styles (the first is the free starter), the five yard slots (fence, mailbox, path, lamp, ornament) with 15 pieces each, and the **Keepsake Capsule**'s price and odds. Five pieces in each slot are sold, and ten only come from the capsule, which is sold at the real-estate office. A capsule repeat pays back half its rarity's `values`. These pieces are Lumi City's own: they never appear in Lumi Pet's Star or Moon Capsules and don't count in her move-in refund.

## Yarn Pop, Lumi Conga and Teacup Tower

Yarn Pop and Lumi Conga (Media → Games → Arcade) are Breakout and Snake, ported from Doll's GameMaker arcade and dressed up with Lumi on 2026-11-11. Their slugs (`breakout`, `snake`), rules, runs and server checks are unchanged; only their names and looks are new. Each game draws the player's own Lumi from her save through `GET /api/media/games/lumi-look` (`arcade.lumi_look`: her glow and outfit, read only), and each showdown seat keeps its racer's look from when they sat down. Prism Drop and Teacup Tower draw her the same way. `python/export_roulette_arcade.py` copies their power-up icons, banner and sounds from the GameMaker project, and they share one shell (`static/games/arcade/`). They need no setup. Migration **0071** adds two tables:
- `arcade_runs`: one ticket per run, with the seed the server handed out.
- `arcade_rooms`: the showdown tables, one JSON document each, like `casino_rooms`.

**Coins (Roulette's rules):**
- A solo run or a showdown race costs 25 coins, type `<game>_entry`.
- A run pays its score as type `reward`, reference `<game>:<run>`, so it can only pay once.
- Watchers' bets go to the house as `<game>_bet`. A bet on the winner comes back double as `<game>_win`, and refunds are `<game>_refund`.

**Checking scores:** the server never takes a score from the page.
- **Lumi Conga (Snake):** the page sends only the moves it turned on, and `arcade.py` plays the whole game again from the run's seed.
- **Yarn Pop (Breakout):** the page sends its score and level. The server rebuilds every level from the seed and refuses a score that doesn't fit the levels played, or that came faster than 6 bricks a second.
- **Teacup Tower** is our own climber in the same shell, with Lumi from Lumi Pet as the climber. The page sends the highest floor reached, and the server refuses more than 6 floors a second; the fastest real climb is about 4.6. It has no files of its own beyond its scripts, because every sound is synthesized.
- Racers' live boards go through memory only, never the database.

## Built-in multiplayer games

Embedded games inherit the paired TV's automatic/manual UI scale without duplicating the kiosk toolbar or screen margins. Casino layout limits and Lumi Pet's room size scale with the text. If a 4K TV still looks small, check **Screens → Text size** for a saved manual override; Automatic uses the CSS viewport after OS/browser scaling.

Media's **Games** tab includes Blackjack, Roulette, Horse Racing and Slot Machines alongside Prism Drop. They use the existing Media group; paired TVs also require their owner's screens-group access. No additional service, API key or external game site is needed.

**Coins:** each account has one purse shared by every game, kept in this server's database (migration **0063**; normal backups include it). New purses start at 1,000. Online casino tables bet from it, a free refill tops it back to 1,000 when it can't cover the smallest bet, Prism Drop pays 1 coin per 10 points, and Lumi Pet pays 25 for the first visit each day by the server's date. Local multiplayer tables (remotes around one TV) use table-only credits and never touch a purse. A paired TV at an online table spends its owner's coins. Every movement is recorded in `coin_transactions`, kept for `coin_ledger_days` (default 365); balances are kept regardless. Deploying 0063 closes tables that were open at the time. Coins have no cash value and aren't connected to SadGirlCoin.

**Daily coin limits:** **Settings → Entertainment → Media → Games** has a **coins a day** box for each game with a daily limit: **Prism Drop**, **Lumi Launch**, **Lumi-Soft** and **StickBob Runner** (`prism_daily_cap`, `launch_daily_cap`, `lumisoft_daily_cap`, `runner_daily_cap`; the list is `media.DAILY_CAPS`). Each is the most one person can earn from that game per day, by the server's date. **0 is no limit**, and all four are 0 unless you set them. An existing install that saved Lumi-Soft's old 300 keeps it until it's changed there. The other games pay once per win or run and have no daily limit: the arcade games, showdown bets, Lumi's fights, trips and garden.

Migration **0061** creates `casino_rooms`. It stores server-authoritative round state and the credit balances of local tables; normal database backups include it. Rooms survive app restarts, expire after two idle hours (handing back any bets still open), and are removed on the next table creation or balance check. At most 100 rooms exist at once, eight seats per room, and three joined rooms per identity per game may be held before creating another. A signed-out paired TV has its own seat; signed-in devices share their account's seat.

**Who is still at a table** is kept in memory (`mnemerynne/lobby.py`), not written to the database.
- **Casino tables:** every poll counts as being there, and a poll that changes nothing writes nothing. A seat not heard from for two minutes is given up between rounds, and its open stake comes back. A table nobody is at closes, and so does a TV table whose TV has gone; open stakes are refunded either way.
- **Showdown tables:** a seat counts as there while its page holds the table's stream (`/api/media/games/<game>/tables/<code>/stream`) open. It's let go 30 seconds after the stream closes.
- **After a restart** everybody gets the full grace period to come back. Because this lives in memory, the web app must stay a single process.

On a paired TV, **One TV, multiple remotes** creates a spectator display with up to eight independently controlled player seats. Pair all panels to the same owner as the TV; the existing firmware's arrows/Enter/Play work unchanged. Remote names become player labels. First Enter joins; subsequent keys affect only that remote's seat. Other-owner panels cannot reach the TV. Signing in as someone other than the TV owner disables shared-TV hosting. Player removal and table closure are explicit controls on the TV. Re-pairing/deleting a panel invalidates its old inputs; its abandoned seat can be removed on the display.

This mode needs no new migration or external service: controller drafts, private display identity and short-lived deduplication receipts are part of the room JSON. Signed inputs are checked against live pairings, exact destination screen, key, age and round/phase. The deployment's existing secret key signs them; no device bearer keys are forwarded to the TV. Restarting the app preserves seats/drafts, while the existing remote stream reconnects.

Clients poll active tables approximately once a second and lobbies every three seconds while visible. All table users see names, bets, cards after dealing (except the dealer's hole card), balances and results. Tables are discoverable by everyone with Media access. Coins and credits have no cash value and never connect to SadGirlCoin.

Slots now play three horizontal lines on a 3×3 board. The wager is per line and the full three-line cost is shown before placing it. This needs no new database migration: existing saved one-line rounds finish at their original cost and change to three lines at the next round.

## Retro

**Media → Retro** plays console games in the browser: NES, Super Nintendo, Genesis, Game Boy, Game Boy Color, Game Boy Advance and PlayStation. It uses [EmulatorJS](https://github.com/EmulatorJS/EmulatorJS), which is RetroArch built for the browser, served from this server. People's guide: [Retro](../user/retro.md).

- **Turning it on:** **Settings → Entertainment → Media → Retro**. It's off until an admin turns it on, and it's on only while Media is.
- **Who can play:** whoever may use Media, seeing only the games their account can read on the share.
- **The Games folder:** turning Retro on makes a folder at the top of the share with a folder per console inside: `NES`, `SNES`, `Genesis`, `Game Boy`, `Game Boy Color`, `GBA`, `PS1`.
  - A console's folder under another usual name is used instead, not doubled: `Super Nintendo`, `Mega Drive`, `PlayStation` and so on (`CONSOLES` in `mnemerynne/media/retro.py`).
  - A console added in a later version gets its folder from the next catalog pass, once. A folder an admin removes on purpose isn't made again.
  - **Games folder** picks another top-level folder, but not the Media or Signage folder. Nothing is ever deleted.
- **Games:** people bring their own ROMs and discs. Mnemerynne never downloads any.
- **Box art:** a picture beside a game with the same name.
- **No catalogue:** the shelves are read from the share scan's list of files.
- **Unsorted:** a `Unsorted` folder beside the console folders is a drop box. The catalog pass recognises each ROM by its header and moves it into its console's folder (`mnemerynne/media/romsort.py`).
- **Popularity (the Top page):** the library's first page ranks each console's games by how many people played them on RetroAchievements (`mnemerynne/media/popularity.py`).
  - **Setting it up:** a free [RetroAchievements](https://retroachievements.org) account. Copy its **Web API key** (Settings → Keys) into **RetroAchievements key** in Retro's settings. It's stored encrypted and only ever shown as saved or not. Remove it and nothing more is asked; the pages go back to A to Z.
  - **What's sent:** the key, console numbers and game numbers. Never titles, file names or who plays what.
  - **How games are matched:** by RetroAchievements' own ROM fingerprints (an MD5; the NES without its 16-byte header, the SNES without a copier header; a `.zip`'s biggest file). PS1 discs, `.7z` archives and anything whose fingerprint isn't listed are matched by title within their console, exactly, leaving hacks and homebrew out.
  - **How gently:** each console's game list is fetched once a week. Each game's player count is asked once a month, at most 25 a pass, a moment apart. A big library fills in over a few hours. **Settings** shows how many games were found and ranked so far.
- **Netplay (👥 Play together):** up to four people play one game together, each in their own browser, kept in step in the browser (`static/retro-netplay.js`) by **rollback**: each player's own buttons count from the next frame, the others' are guessed until they arrive, and a wrong guess is put right by loading the state kept for that frame and playing the frames since again at once. The PS1 is too slow to replay frames, so it plays in lockstep (everyone waits for every player's presses, sent a few frames ahead). Mnemerynne is only the meeting place and relay (`mnemerynne/media/netplay.py`):
  - **Rooms** live in the media service's memory. They're gone when it restarts, so anyone playing just carries on alone.
  - **The connection** is a websocket at `/api/media/retro/netplay/ws`. Only people who may play that game (Media, Retro, and read access to the ROM) get in; anyone else is refused at the door.
  - **Websockets:** the media service is a websocket service in the registry, so the front door (`front-door.sh`) passes the upgrade on for `/media/` and `/api/media/`. The public front (site-chan's nginx) must pass it on too: `proxy_set_header Upgrade $http_upgrade;` and `proxy_set_header Connection "upgrade";` (with `proxy_http_version 1.1;`) in the location for Mnemerynne's name. Without that, **👥** says the room's connection closed straight away.
  - **What travels:** each player's pad for every frame (a few dozen bytes, the buttons only when they change) and a checksum every five seconds, through the server: about 60 small messages a second per player. A save state goes out when the host starts or resyncs. Nothing about play is kept.
  - **The browser's work:** while playing together the page runs the emulator's frames itself, with RetroArch's fast-forward on and paced at the console's own rate (only then is one call exactly one frame, the same in every browser). It keeps a save state for each frame it guessed (a NES's is 22 KB, a SNES's 800 KB), and a correction replays up to eight frames in a few milliseconds. `retro-player.js` sets `fastforward_frameskip = false` (every frame drawn), hides RetroArch's fast-forward notice, and unbinds RetroArch's own fast-forward keys, so nothing can switch it off mid-game. A slow TV box may stutter a little on corrections in the heavier consoles.
  - **Why not EmulatorJS's own netplay:** it's broken by its own account (the host applies presses at once and everyone else ten frames later, so the games drift apart). It also hangs on a hook (`Module.postMainLoop`) these cores never call, and it would need its own server, open to anyone who can reach it.

**The cores**, one per console:

| Console | Core | Notes |
| --- | --- | --- |
| NES | FCEUmm | `.fds` needs `disksys.rom` in the NES folder |
| Super Nintendo | Snes9x | |
| Genesis | Genesis Plus GX | |
| Game Boy and Game Boy Color | Gambatte | |
| Game Boy Advance | mGBA | |
| PlayStation | PCSX-ReARMed | `.cue` (its `.bin` tracks come along), `.pbp`, `.zip`/`.7z`; no BIOS needed |
| PlayStation `.chd` | Beetle PSX | needs a BIOS (`scph5501.bin`, `scph5500.bin` or `scph5502.bin`) in the PS1 folder |

- A BIOS is found anywhere in its console's folder, in any letter case. It's served to the core under the lower-case name the core looks for, and only to people who can read it.
- A game that needs a BIOS but has none shows **Needs a BIOS** on the shelf, and the player names the files it looks for.
- A `.cue` whose tracks aren't beside it opens, but says which track is missing.
- Disc games download whole before they start, so the browser needs the memory for them.

**Saves** are kept in `media.db`, so the nightly backup has them. There are four kinds:
- the game's own save (a battery save, or a PlayStation memory card),
- four save-state slots, each with a screenshot,
- an automatic state for **Continue**,
- the last time each game was played.

They're keyed by the game's content, not the file. Renaming, moving or re-adding a game keeps its saves. They go with the account when it's removed.

Sizes:
- The game's own save is at most 1 MB. A PlayStation memory card is 128 KB.
- A state is at most 16 MB.
- A screenshot is at most 1 MB.

**The player page** has its own Content-Security-Policy:
- It talks to this server only. `connect-src 'self'` blocks EmulatorJS's update check, its CDN fallback for a missing core, and netplay.
- It needs `'wasm-unsafe-eval'` for the cores and `'unsafe-eval'` for the zip and 7z extractors' older Emscripten code.

**Licences** (each core's own is in its `.data` archive, and shown in the player's menu):
- EmulatorJS is GPL-3.0, shipped unminified with its licence in `static/vendor/emulatorjs`.
- mGBA is MPL-2.0.
- FCEUmm, Gambatte, PCSX-ReARMed and Beetle PSX are GPL-2.0.
- **Snes9x and Genesis Plus GX are for non-commercial use only.** Keep Retro off on an install that's used commercially.

**Updating the emulator or adding a core:** `python python/fetch_emulatorjs.py`. It pins each npm package and checks its sha512. Its `--check` (also run by `tests/test_retro.py`) confirms the folder still matches. See `static/vendor/emulatorjs/README.md`. All seven cores together are about 15 MB.

**Testing:**
- `tests/retro-browser.mjs` plays a homebrew test game per console (`python/make_retro_test_rom.py`, written by hand in each console's machine code) in Chrome.
- Every game must boot and paint its screen pink. Every one but the PlayStation's must count its boots in its own save, and pick up from the server's copy in a fresh browser.
- The GBA also goes through the save slots, Continue and loading a slot.
- Run it with `PUPPETEER_MODULE` set, as for the other browser checks.

## Signage

Screens double as digital signage: playlists of pictures, clips, PDFs, text, web pages and announcements, scheduled per screen or group, with each screen's state in view. See [Signage](signage.md).

## Screen boxes: Kiosk settings

A TV run by a screen box (`deploy/screen/` in the repository) has **⚙ Kiosk settings** in its menu: the box's Mnemerynne address, system volume, display scaling, top bar, kiosk PIN and updates. They open with a keyboard or mouse plugged into the box, never from a phone's or remote panel's buttons, and only admins can unlock them:

- **From a phone:** the TV shows a code and a QR code. Scan it, or open `/media/kiosk` and type the code, and approve. Only admins can approve.
- **With the kiosk PIN,** if one was set in the settings. It's the only way in while the box can't reach the server.

**Show the top bar** gives that TV's page a short top bar (Ask, Files, the Wiki, **Sign in**), for a box used as a thin client. Anyone who signs in on the TV is signed out when they go back to the TV, and after ten minutes untouched. It's stored on the server for that screen, so changing it needs a phone approval.

**Text size…** makes text and buttons bigger or smaller on that TV's pages (Media, signage's own pages, and Ask, Files and the Wiki from the top bar). It's stored on the server for that screen, like the screen size. Until someone sets it, it's automatic: 1.5× on a screen wider than 2400 pixels and 2× from 3000 (a 4K TV), otherwise normal. Any screen gets it, not only a box: its owner can change it from **Media → Screens → Text size** on their phone.

**Display scaling** (100%, 150%, 200% or 300%, like Windows) sizes everything on that box's TV, Kiosk settings and the box's own start page included; it's kept on the box. A box on a 4K TV keeps 4K when it gets 60 Hz: set **200%** there, or leave it at 100% and the automatic text size still makes Mnemerynne's pages readable. When the TV only takes 4K at 30 Hz, the box switches it to 1080p at 60 Hz, where the text is normal size. The installer's `--display` option changes that (the box's README, "A 4K TV").

Opening the settings (`kiosk.unlock`) and changing the top bar (`kiosk.top_bar`) are logged, with the admin who approved. The box's README (`deploy/screen/README.md`, step 6) covers the rest.

**🔗 Controllers** (a screen box only) pairs Bluetooth game controllers, Xbox One and PlayStation 5, to the box, from the TV's menu with a remote. The box's helper does the pairing (BlueZ, through `bluetoothctl`). It pairs only game controllers, never keyboards, mice or phones, and only for the TV's own page, after a one-time pass:
- The server gives the TV page a pass for a remote's OK (a remote that may drive that TV: its owner's or a TV guest's), or for the box's own keyboard or controller (`POST /media/screen/controllers/open`).
- The helper redeems the pass with the server (`POST /api/media/kiosk/controllers/redeem`, rate limited).
- The helper then answers the page for ten minutes of use.

A paired controller drives the TV like the box's keyboard, so unlike a remote it can open Kiosk settings (still behind the phone approval or the PIN). Boxes get BlueZ and Bluetooth turned on from the next screen release (Kiosk settings → Updates); one without a Bluetooth adapter needs a USB one. **Retro** opens in the TV's game layer on a paired screen (as the TV's owner), with the remote and controllers; see Retro below.

**On every page, not only the TV page,** a screen box keeps its screen size and text size, has a small bar (**📺 Back to TV**, **‹ ›**), and still hears the remote. Documents open inside a page with that bar. **Backspace** goes back a page, and **Ctrl+Shift+Alt+R** restarts the kiosk.

**Someone using the TV** (on any page but the TV page) gets 10 seconds to keep it when a command comes from a phone: **Keep using the TV**, or Escape. Otherwise an open document is saved as it is and the TV goes back to its page to carry the command out, signing out whoever was signed in on it.

**Admins** see everyone's screens under **Media → Screens** and can play on them. While a TV counts down, the remote offers **Override**, which skips the countdown. Admins' commands from **Access → Signage** never wait. Casting to someone else's screen (`screen.cast`) and overriding (`screen.override`) are logged.
