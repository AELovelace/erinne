# Signage

**Access → Signage** turns the screens paired under Media (TVs, kiosk boxes) into digital signage: playlists of pictures, clips, PDFs, text, web pages and announcements, scheduled per screen or screen group, with each screen's state in view. It needs Media on (Settings → Media), which also makes the **Signage** folder on the share.

## The content browser

The left side lists two places: the **Signage folder** at the top of the share (anything a screen can show: pictures, MP4 clips, PDFs) and the Media library's **Pictures** and **Movies**. Pick a folder to see its files as thumbnails. In the Signage folder you can **Upload** files and make a **New folder**; people can also drop files into it from Windows, like any share folder. The worker reads new files within a minute or two (the same catalog as Media, so clips get their length and PDFs their page count).

Tick files and press **Add to playlist ▸**, drag them onto the playlist editor, or double-click one, to make them slides of the playlist you are editing. A clip that would need converting (not MP4 H.264) is greyed out: screens play files as they are.

## Playlists

A playlist is an ordered list of slides, each shown for the playlist's seconds unless it sets its own:

- **A picture**, **a video** (with or without sound), **a PDF** (a page a slide).
- **Pictures and clips from a folder**, optionally with its subfolders: pictures added to the folder appear by themselves.
- **Text**: a title and a few lines on a colour or over a picture.
- **A web page**, for a set time (many sites refuse to be shown inside another page; try it on a screen).
- **A YouTube video**, muted unless you say otherwise.
- **Announcements for everyone**: each current announcement without a group becomes a slide. Announcements for a group never appear, because anyone walking past can see the TV.

Each playlist has a **transition** (fade, cut, slide, zoom) and a **fit** (whole picture, fill the screen, whole picture over a blur); any slide may override both, and may be limited under **Settings ▾** to weekdays, hours and dates, or switched off without removing it. Drag the **⠿** handle to reorder. **Preview** plays the playlist in the browser with the same code the screens run; **Copy** duplicates it.

## Schedule

The schedule says what plays where and when. Each entry names a playlist and is **for** all screens, one group or one screen, on chosen **days** between **From** and **To** (the same: all day; To before From: past midnight), optionally between two dates, and either:

- **When the screen is idle**: it starts once nothing has played and nobody has pressed a key for the screen's idle time; any key, or anything cast from a phone, stops it.
- **Always (signage hours)**: it runs whenever nothing is cast to the screen, and keys don't stop it. Casting still interrupts.

A screen's own entry beats its group's, which beats one for all screens; among those, the higher **priority** wins, then the newer entry. The week grid shows what a screen or group would play, with the entries it inherits in a paler colour; click one to change it. Screens get their programme ranked and decide by their own clock, so they keep going while the server is briefly away. Changes reach screens that are on within seconds.

## Screens

Every paired screen, whoever owns it, grouped by its **group**. Each card shows whether it is online, what it is showing, when it was last heard from, its player version and any slides it couldn't show. Set its **group**, **location**, **orientation** and **idle time**. The buttons send to the screen at once: **Identify** shows its name on it for a few seconds, **Reload** reloads its page, **Stop signage** and **Start signage** hold and release its programme. Re-pairing a screen under the same name keeps all of this.

## CRT effects

An optional look for a screen: scanlines, curved glass, colour fringe, flicker, glow and a tube warm-up when a playlist starts, like an old monitor. Set the default for newly paired screens under **Settings**; each screen can turn it on or off and fine-tune the parts and the strength; a playlist can insist on CRT on or off wherever it plays. The effects are cheap to draw (no filter touches a playing video), and the flicker stays off for anyone who asked their device for reduced motion.

## Settings

The name of the **Signage folder** at the top of the share (default `Signage`; it can't be the Media folder), and the default **CRT effects**. Screens show the playlists' files even if their owner couldn't open those folders: admins chose them. Everything done here is logged under `signage.*` in **Recent changes**.
