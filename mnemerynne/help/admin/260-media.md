# Media

Media is a Kodi-style media centre for films, music and pictures from the share, plus live TV and internet radio. People see it under **Media**; this page is the admin side. Setting up the server (ffmpeg, the GPUs) is covered in the deployment README, section 5k.

## Settings → Media

- **Media is on for this organisation:** Media is off until an admin turns it on. Turning it on:
  - makes the **Media** folder on the share with **Music**, **Movies** and **Pictures** inside, or uses a Media folder that is already there;
  - starts cataloging after each share scan.

  While it's off:
  - nobody sees the Media tab;
  - its pages and API answer as if they didn't exist;
  - anything playing stops.

  The folders, the files and the catalog are kept. Turning it on or off is logged (`settings.media`).
- **Media folder on the share:** **Media** unless you choose another folder at the top of the share, such as an existing `Plex` or `Videos` folder. **Use this folder** takes an existing folder as it is, or makes a new one. Either way, **Music**, **Movies** and **Pictures** are made inside it if they're missing; names in any case count. Set it before turning Media on, and nothing called Media is made.
  - Switching to another folder keeps the old folder's files and catalog, so switching back is instant.
  - Renaming the folder in Windows is fine: Media follows it.
  - Changes are logged (`settings.media_folder`).
- **Who may use Media:** an AD or Entra group, or several separated by `;`. Only members see the Media tab, its pages and its API. Empty means **nobody**, admins included, so name a group after turning Media on. Leaving the group closes Media for that person, and their screens, at once.
- **Who may use screens:** the group whose members may pair a TV as a screen and cast to it from their phone. They must be in the Media group too. Empty means nobody. When an owner leaves either group, their screens stop at once; owners can still remove their own screens.
- **Who sees what:** the share's permissions on the Media folders, set in Windows like any library. A Movies subfolder closed to someone is closed in Media too, including its posters.
- **Transcoding:** how films the browser can't play are converted.
  - **Automatic** tries the AMD card (VAAPI), then the NVIDIA card (NVENC), then the CPU, and keeps the first that works.
  - **Test transcoding** encodes two seconds with each backend and reports which work on this server.
- **Transcodes at once:** how many films may be re-encoded at the same time; **0** turns transcoding off. Copying a film into a browser-friendly package (**Repackaged**) is cheap and doesn't count.
- **Look up artwork online:** with this on, posters, backdrops and plots of films come from TMDB, and album covers from MusicBrainz and the Cover Art Archive.
  - Only film titles and years, and album and artist names, are sent.
  - Art that came with the files is never replaced.
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

**Access → Signage** sets what screens show while nobody is using them, like digital signage.

**Slideshows** are lists of slides:

- **Pictures and clips from a folder** of the Media library, optionally with its subfolders. Pictures added to the folder appear by themselves within a few minutes. Clips play only if the browser can play them as they are, such as MP4 with H.264; others are skipped.
- **A video** from the library, with or without sound.
- **Text:** a title and a few lines, on a colour or a picture.
- **A web page**, shown for a set time. Many sites refuse to be shown inside another page, so try it on a screen first.
- **Announcements for everyone:** each current announcement without a group becomes a slide. Announcements for a group never appear, because anyone walking past can see the TV.

Each slide shows for the slideshow's number of seconds unless it sets its own. Videos play to the end.

**Screens** lists every paired screen, whoever owns it. Each screen has rules, and the first rule that matches the screen's own clock decides what it shows:

- **Days, From and To.** From and To the same means all day. To earlier than From runs past midnight.
- **When idle:** the slideshow starts once nothing has played and nobody has pressed a key for the screen's idle time. Any key, or anything cast from a phone, stops it.
- **Always (signage):** the slideshow runs whenever nothing is cast to the screen, and keys don't stop it. Casting still interrupts, and the slideshow resumes when playback ends.

Screens show the slideshow's pictures and videos even if their owner couldn't open those folders. Changes reach screens that are on within seconds. Re-pairing a screen under the same name keeps its rules.
