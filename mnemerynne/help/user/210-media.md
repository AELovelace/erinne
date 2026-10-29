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
  - **Escape** closes the player.
- **TV Shows:** search shows by name or description at the top, and sort them by **Title**, **New episodes**, **Recently watched** or **Year**. Open a show and press **Play** for the episode to watch next. That's **Resume** if you stopped part-way, otherwise the episode after the last one you finished. **From the first episode** starts over. The season buttons switch between seasons.
  - **Watched:** the circle beside each episode shows ✓ once you've watched it. Press it to mark an episode watched (or not) without playing it, for example ones you saw somewhere else. **Mark season watched** does a whole season, and **Play** then goes on from the next unwatched episode.
  - When an episode ends, **Up next** counts down 10 seconds and then plays the next one. **Play now** skips the wait; **Cancel** closes the player.
  - ⏮ and ⏭ in the player, or **Shift+P** and **Shift+N**, go to the previous or next episode.
- Most films play straight away. Some formats need the server to convert them first, and the player then says **Transcoding** or **Repackaged**. That takes a few seconds to start, and jumping far ahead takes a moment longer.

## Music and radio

- Open an album and press **Play** or **Shuffle**, or choose a track. Music plays in the bar at the bottom while you keep browsing.
- **Radio** lists the stations. **On air** means someone is already listening. Other stations start when you tune in, which can take a few seconds.

## Live TV

**Live TV** lists the streams your admin has added. Choose one to watch it live. YouTube ones play in YouTube's own player, with its controls; press **✕** to close it.

## Games

**Games** (🎮, after Radio) lists the games your admin has set up, such as Lumi Web and Lumi Games. It's there when Games are on for you.

- Choose one and it opens over the whole page. **← Games** (or Escape, when the game doesn't have the keyboard) goes back; **⛶** fills the screen; **⧉** opens it in a tab of its own.
- The games come from another site and run on an address of their own (the arcade), so they can't see anything of yours in Erynne. Opening one signs you in to the arcade for 12 hours; signing out of Erynne signs you out of it too.
- A game may ask you to sign in with its own account (Discord, for the Lumi games). That stays between you and the game.
- Saves and settings a game keeps in your browser belong to the arcade's address, so they don't follow you from the game's own site.
- On a TV, Games works the same way, as the TV's owner. Sending something to the TV from your phone closes the game.

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
- The bar at the bottom shows what the screen is playing, with buttons to pause, skip, change the volume, step the TV's night filter (🌙), go to the next track or stop. **✕** goes back to playing on your phone.
- **Volume:** a screen has its own volume, used by everything it plays, and it remembers it. Change it from the remote bar (🔉 🔊 🔇), the TV remote's volume keys, **+** and **−** on a keyboard, or the 🔉 🔊 buttons on screen. The level shows for a moment in the top corner.
- **Screen size:** if the TV cuts off the edges of the picture, use **Screens → Screen size** on your phone (on a screen box an admin can also use **Kiosk settings → Screen size…** on the TV). Narrow it until the whole pink frame and the arrows in all four corners show, then **Save**. First check the TV's own picture settings for *Just Scan*, *Screen Fit* or *1:1*: that fixes it without shrinking anything.
- **Screens** lists your screens and whether they're on. **Remove** unpairs one.

- **Someone using the TV:** on a screen box, if someone is using another page on the TV, they get 10 seconds to keep it. Your phone says so, then whether it switched or they kept it.

Only you (and admins) can see and control your screens. Screens are only for members of the group your admin chose; without it, there is no **Screens** item in the menu.

## A remote panel

A remote panel is a small touchscreen (the Erynne remote tablet) that controls your screens without a phone. It can play and pause, skip back 10 seconds and forward 30, go to the previous or next track (or TV episode), change the volume, mute, step the night filter, stop, and start a Live TV stream or radio station. It can't browse films or music.

Its **arrows and Enter** work like the TV's own arrow keys while the TV shows its Media page. They move the highlight between tiles and buttons, and **Enter** presses the highlighted one. While a film plays, ◀ ▶ skip back 10 seconds and forward 30, ▲ ▼ change the volume, and Enter plays or pauses. With pictures open, ◀ ▶ go to the previous or next one. When **Up next** shows after an episode, the arrows choose **Play now** or **Cancel** and Enter presses it. While the screen size is being adjusted, they size the picture and Enter saves it. If the TV is on another page (someone is using it), the arrows do nothing.

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

