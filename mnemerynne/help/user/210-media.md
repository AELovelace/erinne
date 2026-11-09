# Media

**Media** is a media centre for films, TV shows, music and pictures, laid out like a TV app. It also has live TV and internet radio. It appears in the top bar once an admin has turned it on.

## Adding things

Everything comes from the **Media** folder on the file share, or the folder your admin chose instead; the Media page's tips name it. Copy files into it from Windows Explorer and they show up a minute or two later, with their artwork:

- **Movies:** one file per film, named like `Blade Runner (1982).mkv`. A `poster.jpg` (or `Blade Runner (1982)-poster.jpg`) beside it becomes its poster, and `fanart.jpg` its backdrop. Without one, a frame from the film is used, or the poster is looked up online if your admin allows it.
- **TV shows:** one folder per show in **TV**, with a folder for each season inside, for example `TV\Bluey\Season 1\Bluey S01E01 Magic Xylophone.mkv`.
  - The season and episode come from the file name: `S01E02`, `1x02`, `E05`, or a leading number like `05 - Title`. Without a season in the name, the folder's name gives it (`Season 2`, `Series 2` or `S02`).
  - The episode's title is whatever follows the number. Without one, it's called *Episode 2*.
  - Put specials in a `Specials` folder. They're listed after the seasons and aren't played on from the other episodes.
  - A `poster.jpg` (or `folder.jpg`) in the show's folder becomes its poster, and `fanart.jpg` its backdrop. You can add them any time; they show up a minute or two later. Without a poster, one is looked up online if your admin allows it, otherwise a still from an episode is used.
  - With online lookups on, a show also gets its year and a description, and its episodes their descriptions. An episode whose file has no title gets its real one. Name the show's folder as TMDB knows it, with the year if two shows share a name (`Doctor Who (2005)`).
  - Episodes put straight into **TV**, outside a show's folder, don't appear.
- **Music:** one folder per album, for example `Music\Daft Punk\Discovery\01 One More Time.mp3`. Titles, artists, albums and track numbers come from the files' tags. The cover is a `cover.jpg` or `folder.jpg` in the album's folder, or the art inside the files.
- **Pictures:** photos and short clips. Each folder becomes an album, and photos are sorted by when they were taken.

You only see what you may open in that folder on the share. Who may open it is set in Windows, as for any shared folder.

## Getting around

- The menu on the left has **Home**, **Movies**, **TV Shows**, **Music**, **Pictures**, **Live TV** and **Radio**. **Home** shows what you were watching (with the next episode of each show you're following) and what was added lately.
- Use the **arrow keys** to move between tiles, **Enter** to open one, and **Escape** or **Backspace** to go back. A mouse or touch works too.

## Watching

- Open a film and press **Play**. If you stopped part-way, **Resume** starts where you left off; Erynne remembers that for each film.
- In the player:
  - **Space** pauses;
  - **←** goes back 10 seconds and **→** forward 30;
  - **↑** and **↓** change the volume;
  - **F** switches full screen;
  - **N** steps the night filter (off, low, medium, high); the 🌙 slider sets it exactly. It makes the picture warmer and dimmer for watching late, YouTube included, and each device remembers its own level;
  - **A** steps to the next sound track and **C** to the next subtitles (then off);
  - **Escape** closes the player.
- **Sound and subtitle tracks:** a film with more than one sound track shows **🗣** in the player's bar, and a film with subtitles shows **CC**. Press either for a list, then pick with the mouse or with **↑**, **↓** and **Enter**. **Escape** closes the list. **CC** is lit while subtitles show.
  - Picking a sound track other than the film's first restarts it from the same place. The server repackages it (the player says **Repackaged**), which takes a second or two.
  - Text subtitles (SRT, ASS, the subtitles inside MP4s) show straight away. The first time, the server reads the whole file to find them, so on a big film they can take a few seconds to appear. ASS styling (fonts, colours, positions) isn't kept.
  - Picture subtitles (Blu-ray PGS, DVD) are marked **burned in**. The server re-encodes the film with them drawn onto the picture, so they count as a transcode. After a jump, the line already on screen at that moment is skipped; the next one shows as normal.
  - Each device remembers the languages you last picked and uses them for the next film or episode. Picture subtitles are never turned on by themselves, though: pick them from **CC** each time.
  - Subtitles in separate files next to the film (`Film.en.srt`) aren't offered yet; only tracks inside the film file are.
- **TV Shows:** search shows by name or description at the top, and sort them by **Title**, **New episodes**, **Recently watched** or **Year**. Open a show and press **Play** for the episode to watch next. That's **Resume** if you stopped part-way, otherwise the episode after the last one you finished. **From the first episode** starts over. The season buttons switch between seasons.
  - **Watched:** the circle beside each episode shows ✓ once you've watched it. Press it to mark an episode watched (or not) without playing it, for example ones you saw somewhere else. **Mark season watched** does a whole season, and **Play** then goes on from the next unwatched episode.
  - When an episode ends, **Up next** counts down 10 seconds and then plays the next one. **Play now** skips the wait; **Cancel** closes the player.
  - ⏮ and ⏭ in the player, or **Shift+P** and **Shift+N**, go to the previous or next episode.
- Most films play straight away. Some formats need the server to convert them first, and the player then says **Transcoding** or **Repackaged**. That takes a few seconds to start, and jumping far ahead takes a moment longer.

## Music and radio

- Open an album and press **Play** or **Shuffle**, or choose a track. Music plays in the bar at the bottom while you keep browsing.
- **Radio** lists the stations. **On air** means someone is already listening. Other stations start when you tune in, which can take a few seconds.

## SoundCloud

**SoundCloud** in the menu shows someone's profile: **Tracks**, **Albums**, **Playlists**, **Likes**, **Liked sets** and **Reposts**.

- The first time, type the name after `soundcloud.com/` or paste the profile's address. **Change profile** switches to another. It's saved for you, and it's your own: other people pick theirs.
- Press **Play** or **Shuffle**, or choose a track. It plays in the bar at the bottom while you keep browsing, like your music.
- **Load more** at the bottom of a long list brings the next tracks. **↗** opens a track on SoundCloud.
- **Search SoundCloud** at the top finds **Tracks**, **Playlists** and **Profiles**, and works before you've picked a profile. Opening a profile from the results doesn't change the one saved for you. A TV's remote can't type, so search on your phone with **Play on** set to the TV.
- **📻** beside a track starts a station: that track, then ones like it, topping itself up for as long as it plays. On a TV, press right on a track to reach it.
- With **Play on** set to a TV, it plays there. Speakers can't play SoundCloud.
- On a TV, the profile is set for that screen, in **Screens** on your phone or in **Kiosk settings**. With none set, it shows the screen owner's.

A few tracks only have a 30-second **Preview only**, and some can't be played here at all (the list says how many). If SoundCloud's daily limit for the server is reached, the page says when to try again.

## Live TV

**Live TV** lists the streams your admin has added. Choose one to watch it live. YouTube ones play in YouTube's own player, with its controls; press **✕** to close it.

## Games

**Games** (🎮, after Radio) lists the games that come with Erynne. Everyone who can use Media can play them, and so can a TV, as its owner.

- Choose one and it opens over the whole page. **← Games** (or Escape or Backspace) goes back; **⛶** fills the screen; **⧉** opens it in a tab of its own.
- The bar at the top shows the game's controls. They're the same on a keyboard and on a **remote panel**: its arrows and **Enter** play the game while it's open on the TV, and the panel shows the score under the game's name. The panel's **Stop**, or sending anything to the TV from your phone, closes the game.
- Scores and chips are only for fun. Prism Drop remembers its best score on this device; multiplayer table balances and rounds are saved on Erynne's server.

**Prism Drop** 🔮: pick one of ten pockets and drop a neon ball through twenty rows of pins. A game is ten drops.

- **◀ ▶** pick the pocket, **Enter** drops (after the tenth drop, Enter starts a new game), **▲** replays the last drop without scoring, **▼** turns the sound on or off. On a phone, tap a column to pick it and tap it again to drop.
- Points for where it lands: **20** in your pocket, **15** one pocket away, **10** two away, nothing further.
- Each drop gets a fresh field of special pegs: **blocks** shove the ball two columns sideways, **bombs** blast it two or three spaces in any direction (each fires once), and **coin pegs** add 1–5 bonus points (once each), even if the landing misses.
- The ball enters at pin 4, 5, 6 or 7 (marked in cyan) at random.

All built-in games on a paired TV follow **Screens → Text size**, just like the Media UI. Automatic keeps the 1080p size and enlarges games at higher resolutions (1.5× at 2560×1440, 2× at 3840×2160, using the browser's CSS viewport). A saved custom size takes precedence. Changes apply to an open game without restarting it.

### Lumi Pet

Visit a little glowing fairy cat in **Games → Lumi Pet**. **Left/Right** selects Cuddle, Snack, Play or Nap/Wake gently; **Enter** interacts. You can also tap the activity buttons. **Up** changes the glow colour; **Down** toggles sound. Keyboard Tab reaches individual controls, and Escape/Back returns to Games.

Lumi sleeps from 22:00 to 07:00 on your device's clock. A daytime nap lasts two minutes; any activity can gently wake Lumi for fifteen minutes. There are no hunger meters, streaks, chores or penalties for being away. Happy moments and the latest memory stay in this browser profile, shared by anyone using it. They do not sync to other devices or accounts; clearing browser storage clears them. If your browser blocks saving, Lumi still plays and the page explains that the visit will not be saved.

### Multiplayer casino tables

**Blackjack**, **Roulette**, **Horse Racing**, and **Slot Machines** support up to eight players per table. Open a game and choose **Create table**. Friends open the same game and select that table's code in the lobby. Tables are visible to everyone who can use Media.

**Several players on one TV:** pair the TV and each remote to the same owner's account, give the remotes distinct names, and select that TV on each panel. On the paired TV choose **One TV, multiple remotes**. Each remote presses **Enter once to join** its own named seat. Left/Right chooses that player's pick, Up/Down adjusts their stake, and **Enter again places the bet**. The first player is host; their Play/Pause starts the round. Each blackjack player independently chooses Hit/Stand and confirms with Enter. Every player's balance, draft, wager and results appear on the TV; slots show a separate three-line board for each player. The TV itself uses no player slot. Other devices may still join the table code.

Refresh keeps these seats and selections; a fresh tab can choose **Resume on this TV** in the lobby. Enter during betting refills a seat below the minimum chip balance. A late join watches until the next round. TV keyboard/mouse/touch controls can **Remove player** or **End TV table**, forfeiting the removed chips and wagers; the next player becomes host. Back to Games preserves the table, while a remote's Stop closes the game view for everyone on that TV. Re-pairing a remote changes its identity; remove any old seat on the TV. Ordinary **Create table** keeps the original shared TV seat controlled by all its remotes.

- Each account has one seat per table. A paired TV without a signed-in session gets its own seat, so two TVs owned by the same person can play together. A remote panel controls its TV's seat.
- Everyone starts with **1,000 free play chips**. Bet 10–500 in steps of 10, within your balance. For slots this is **per line**, and the displayed total covers all three lines (50 per line costs 150). These chips have no cash value and are separate from SadGirlCoin. They belong to this table, not a global wallet.
- Choose your pick and stake, then **Place bet**. You can update the bet while betting is open. The host can **Start round**, or it starts automatically after 45 seconds when someone has bet. Players joining mid-round watch until betting reopens.
- Blackjack offers **Hit** and **Stand**, with a 30-second hand timer; unfinished hands stand automatically. The dealer stands on all 17s. Naturals pay 3:2, ordinary wins 1:1, ties return the stake. No splitting, doubling or insurance.
- Roulette uses one zero. A number pays 35:1 and an outside bet pays 1:1; zero loses outside bets. The six equally likely horses pay 4:1 for the winner.
- Slots have **three reels and three horizontal paylines**: top, middle, bottom. All three lines play on every spin. Each line returns 24× its line bet for three stars, 12× for another triple, or 1× for a pair. Paying lines light up and display their returns; these are added together. Returns include the original line stake. Columns and diagonals do not pay.
- Spins and races take six seconds, with a shared result; slots give each player their own reels. Results stay for ten seconds before betting reopens. Horse movement is decorative; the server determines the finish.
- Refresh to resume the same seat. Going back to Games keeps it available in the lobby. **Leave table** removes your seat and forfeits its remaining chips and active wager; the next player becomes host. Empty tables close immediately; tables untouched for two hours expire. Below the minimum wager (30 chips for three-line slots, ten for other games), use **Refill free chips** between rounds. A previously saved single-line slot round finishes at its original price, then switches to three lines.
- At a table, **◀ ▶ change the pick**, **▲ ▼ change the bet by ten chips**, and **Enter sets the bet**. The host's **Play/Play-Pause** button starts the round (**P** on a keyboard). In blackjack after dealing, ◀ ▶ choose Hit/Stand and Enter confirms. In the lobby, arrows choose a table and Enter joins. Mouse and touch buttons work too; keyboard Tab reaches individual buttons. The remote shows your table code, chip balance and round phase.
- Roulette displays a full 37-pocket wheel and a counter-rotating ball. Your pending pick lights the covered pockets; the ball lands in the server's winning pocket when the result arrives. Reduced-motion mode shows the result without spinning.

## Playing on the TV from your phone

A **screen** is a browser left showing Media, such as the TV's, that you send things to from your phone.

**Pair the TV once with a code** (no sign-in or keyboard on the TV):

1. On the TV, open `/media/screen` on Erynne's address. The Erynne screen box does this by itself. The TV shows a code and a QR code.
2. On your phone, scan the QR code, or open **Media → Screens** and type the code under **Pair a TV**.
3. Name the screen (for example *Living room*) and press **Pair**. The TV switches to screen mode within a few seconds.

A code lasts ten minutes; the TV shows a new one by itself when it runs out.

**Or pair from the TV itself:** sign in on the TV, open **Media → Screens**, name it under **Use this browser as a screen** and press **Pair**.

Either way, the TV stays paired until you remove it, even after any sign-in runs out. Press **Start** on the TV once, so it may play sound; the Erynne screen box doesn't need this.

**Send things from your phone:**

- **Play on** at the bottom of the menu chooses where things play. With a screen chosen, every **Play** sends there instead: films, TV episodes, albums, tracks, pictures, Live TV and radio.
- **A whole show:** with a screen chosen, **Play** on a show sends the episode to watch next. The TV then plays on through the show by itself, with **Up next** between episodes. ⏭ and ⏮ on the remote bar skip to the next or previous episode. The TV can also browse **TV Shows** itself with its remote's arrows.
- To play a YouTube link, share it straight from the YouTube app on Android: **Share → Erynne**. Media opens with the link filled in; press **Play**. This needs Erynne installed on the phone (below). Otherwise, copy the link and use **Media → Screens → Paste**.
- The bar at the bottom shows what the screen is playing, with buttons to pause, skip, change the volume, step the TV's night filter (🌙), switch the film's sound track (🗣) or subtitles (💬, then off), go to the next track or stop. **✕** goes back to playing on your phone.
- **Volume:** a screen has its own volume, used by everything it plays, and it remembers it. Change it from the remote bar (🔉 🔊 🔇), the TV remote's volume keys, **+** and **−** on a keyboard, or the 🔉 🔊 buttons on screen. The level shows for a moment in the top corner.
- **Screen size:** if the TV cuts off the edges of the picture, use **Screens → Screen size** on your phone (on a screen box an admin can also use **Kiosk settings → Screen size…** on the TV). Narrow it until the whole pink frame and the arrows in all four corners show, then **Save**. First check the TV's own picture settings for *Just Scan*, *Screen Fit* or *1:1*: that fixes it without shrinking anything.
- **Text size:** if text and buttons on the TV are too small (or too big) to read from the couch, use **Screens → Text size** on your phone (on a screen box, also **Kiosk settings → Text size…**). **Bigger** and **Smaller** change the TV as you go; **Save** keeps it, **Automatic** goes back to the size picked from the TV's width (bigger on a 4K TV). It applies to every page the TV opens, not only Media.
- **Screens** lists your screens and whether they're on. **Remove** unpairs one.

- **Someone using the TV:** on a screen box, if someone is using another page on the TV, they get 10 seconds to keep it. Your phone says so, then whether it switched or they kept it.

Only you (and admins) can see and control your screens. Screens are only for members of the group your admin chose; without it, there is no **Screens** item in the menu.

## A remote panel

A remote panel is a small touchscreen (the Erynne remote tablet) that controls your screens without a phone. It can play and pause, skip back 10 seconds and forward 30, go to the previous or next track (or TV episode), change the volume, mute, step the night filter, stop, and start a Live TV stream or radio station. It can't browse films or music.

Its **arrows and Enter** work like the TV's own arrow keys while the TV shows its Media page. They move the highlight between tiles and buttons, and **Enter** presses the highlighted one. While a film plays, ◀ ▶ skip back 10 seconds and forward 30, ▲ ▼ change the volume, and Enter plays or pauses. With pictures open, ◀ ▶ go to the previous or next one. When **Up next** shows after an episode, the arrows choose **Play now** or **Cancel** and Enter presses it. While the screen size or text size is being adjusted, they change it and Enter saves it. If the TV is on another page (someone is using it), the arrows do nothing.

**Pair it once with a code,** like a TV: the panel shows a code and a QR code. Scan it, or type the code under **Media → Screens → Pair a TV or remote**, name the panel (for example *Kitchen tablet*) and press **Pair**.

- The panel controls only your screens, and only while you may use screens. It shows each one and what it is playing; tap **Screens** on it to choose another.
- **Screens** on your phone lists your remotes too. **Remove** unpairs one; do that if a panel is lost. The panel's own **Screens → Unpair** does the same from the panel.

**Every panel has a speaker** (the 7" CrowPanel, the M5Stack Tab5 and the small Freenove one), so each pairs as a **speaker** too. One code makes it a remote and one of your screens, marked 🔊. A panel paired before speakers existed stays a plain remote until you **Unpair** it and pair it again. On the CrowPanel, the switch on the back must be set to **MIC&SPK** for sound.
- **Casting to it:** choose it under **Play on this screen** and cast music or a radio station, as you would to a TV. It can't play films, live TV or YouTube, and says so.
- **Controlling it:** from your phone, or from any panel (its own included), with play/pause, skipping, next/previous, volume, mute and stop.
- **What it can reach:** it plays only what is cast to it. It can't browse or fetch anything else.
- **Removing it:** removing the remote removes its speaker as well.

## Signage on the screens

An admin can give screens playlists to show while nobody is using them (Access → Signage): pictures and clips from the **Signage** folder on the share, PDFs, text, web pages and the announcements for everyone. Casting from your phone always interrupts; what you cast plays, and the signage comes back afterwards.

## Erynne as an app on your phone

Install it from Chrome's **⋮ → Install app** (Android) or Safari's **Share → Add to Home Screen** (iPhone): it then opens like an app and, on Android, appears in the **Share** menu of other apps such as YouTube. See [Erynne on a phone](phone.md).

