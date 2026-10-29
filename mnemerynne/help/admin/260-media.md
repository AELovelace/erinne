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

## Live streams and stations

- **HLS stream** or **RTMP stream:** an address the server pulls, for example `http://10.1.1.21/stream/hls/live.m3u8` or `rtmp://10.1.1.21/live/stream`. The server repackages it for the browser, so streams only reachable on the LAN still play for people outside. The picture is copied, not converted, so it should be H.264.
- **YouTube link:** a video, a playlist, or a channel's `youtube.com/channel/UC…` address, which plays whatever the channel is streaming live. It plays in YouTube's own player, with YouTube's ads and controls. A channel's `@name` address can't be embedded: open the channel and use **About → Share channel → Copy channel ID**. Videos whose owners turned embedding off show **Watch on YouTube** instead of playing.
- **Radio station:** any MP3 or AAC stream address. Stations added here are listed after SGCRelay's.
- **Turn off** hides a source without forgetting it. Changing or removing one stops anyone watching it.
- **Playing now** lists the server's running sessions: what each one is doing, which encoder it uses, and how long it has been idle. A session stops a minute after its last viewer leaves.

Adding, changing and removing sources is logged (`media.*`).

## Signage

Screens double as digital signage: playlists of pictures, clips, PDFs, text, web pages and announcements, scheduled per screen or group, with each screen's state in view. See [Signage](signage.md).

## Screen boxes: Kiosk settings

A TV run by a screen box (`deploy/screen/` in the repository) has **⚙ Kiosk settings** in its menu: the box's Mnemerynne address, system volume, top bar, kiosk PIN and updates. Only admins can open them:

- **From a phone:** the TV shows a code and a QR code. Scan it, or open `/media/kiosk` and type the code, and approve. Only admins can approve.
- **With the kiosk PIN,** if one was set in the settings. It's the only way in while the box can't reach the server.

**Show the top bar** gives that TV's page a short top bar (Ask, Files, the Wiki, **Sign in**), for a box used as a thin client. Anyone who signs in on the TV is signed out when they go back to the TV, and after ten minutes untouched. It's stored on the server for that screen, so changing it needs a phone approval.

**Text size…** makes text and buttons bigger or smaller on that TV's pages (Media, signage's own pages, and Ask, Files and the Wiki from the top bar). It's stored on the server for that screen, like the screen size. Until someone sets it, it's automatic: 1.5× on a screen wider than 2400 pixels and 2× from 3000 (a 4K TV), otherwise normal. Any screen gets it, not only a box: its owner can change it from **Media → Screens → Text size** on their phone.

A box on a 4K TV keeps 4K when it gets 60 Hz, and the automatic text size makes it readable. When the TV only takes 4K at 30 Hz, the box switches it to 1080p at 60 Hz, where the text is normal size. The installer's `--display` option changes that (the box's README, "A 4K TV").

Opening the settings (`kiosk.unlock`) and changing the top bar (`kiosk.top_bar`) are logged, with the admin who approved. The box's README (`deploy/screen/README.md`, step 6) covers the rest.

**On every page, not only the TV page,** a screen box keeps its screen size and text size, has a small bar (**📺 Back to TV**, **‹ ›**), and still hears the remote. Documents open inside a page with that bar. **Backspace** goes back a page, and **Ctrl+Shift+Alt+R** restarts the kiosk.

**Someone using the TV** (on any page but the TV page) gets 10 seconds to keep it when a command comes from a phone: **Keep using the TV**, or Escape. Otherwise an open document is saved as it is and the TV goes back to its page to carry the command out, signing out whoever was signed in on it.

**Admins** see everyone's screens under **Media → Screens** and can play on them. While a TV counts down, the remote offers **Override**, which skips the countdown. Admins' commands from **Access → Signage** never wait. Casting to someone else's screen (`screen.cast`) and overriding (`screen.override`) are logged.
