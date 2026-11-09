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

Media → Games includes Lumi Pet, a cozy companion using the existing Media permissions and paired-screen access. It needs no service or migration. Memories are saved locally in the browser profile, shared by its users; they do not sync to accounts and are not part of server backups. It has no currency or reward integration. Browser storage may be cleared to start fresh. The digital ASCII neon fireplace is currently a design-list item, not an installed game.

## Built-in multiplayer games

Embedded games inherit the paired TV's automatic/manual UI scale without duplicating the kiosk toolbar or screen margins. Casino layout limits and Lumi Pet's room size scale with the text. If a 4K TV still looks small, check **Screens → Text size** for a saved manual override; Automatic uses the CSS viewport after OS/browser scaling.

Media's **Games** tab includes Blackjack, Roulette, Horse Racing and Slot Machines alongside Prism Drop. They use the existing Media group; paired TVs also require their owner's screens-group access. No additional service, API key, currency integration or external game site is needed.

Migration **0061** creates `casino_rooms`. It stores server-authoritative round state and free play-chip balances; normal database backups include it. Rooms survive app restarts, expire after two idle hours, and are removed on the next table creation. At most 100 rooms exist at once, eight seats per room, and three joined rooms per identity per game may be held before creating another. A signed-out paired TV has its own seat; signed-in devices share their account's seat.

On a paired TV, **One TV, multiple remotes** creates a spectator display with up to eight independently controlled player seats. Pair all panels to the same owner as the TV; the existing firmware's arrows/Enter/Play work unchanged. Remote names become player labels. First Enter joins; subsequent keys affect only that remote's seat. Other-owner panels cannot reach the TV. Signing in as someone other than the TV owner disables shared-TV hosting. Player removal and table closure are explicit controls on the TV. Re-pairing/deleting a panel invalidates its old inputs; its abandoned seat can be removed on the display.

This mode needs no new migration or external service: controller drafts, private display identity and short-lived deduplication receipts are part of the room JSON. Signed inputs are checked against live pairings, exact destination screen, key, age and round/phase. The deployment's existing secret key signs them; no device bearer keys are forwarded to the TV. Restarting the app preserves seats/drafts, while the existing remote stream reconnects.

Clients poll active tables approximately once a second and lobbies every three seconds while visible. All table users see names, bets, cards after dealing (except the dealer's hole card), balances and results. Tables are discoverable by everyone with Media access. These chips have no cash value and never connect to SadGirlCoin.

Slots now play three horizontal lines on a 3×3 board. The wager is per line and the full three-line cost is shown before placing it. This needs no new database migration: existing saved one-line rounds finish at their original cost and change to three lines at the next round.

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

**On every page, not only the TV page,** a screen box keeps its screen size and text size, has a small bar (**📺 Back to TV**, **‹ ›**), and still hears the remote. Documents open inside a page with that bar. **Backspace** goes back a page, and **Ctrl+Shift+Alt+R** restarts the kiosk.

**Someone using the TV** (on any page but the TV page) gets 10 seconds to keep it when a command comes from a phone: **Keep using the TV**, or Escape. Otherwise an open document is saved as it is and the TV goes back to its page to carry the command out, signing out whoever was signed in on it.

**Admins** see everyone's screens under **Media → Screens** and can play on them. While a TV counts down, the remote offers **Override**, which skips the countdown. Admins' commands from **Access → Signage** never wait. Casting to someone else's screen (`screen.cast`) and overriding (`screen.override`) are logged.
