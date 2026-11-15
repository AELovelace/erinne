# Retro: console games in Media

Doll asked on 2026-11-14 for "snes, gba, gbc, nes, and genesis emulators", a share for ROMs, and a page to browse and play them. Her order: **GBA first, SNES second, Game Boy third**.

## Decisions (Lumi's picks; Doll didn't choose otherwise)

- **Where it lives:** inside Media, as **Media → Retro**, a page of its own (`/media/retro`), linked from Media's menu like Remote. It runs wherever Media runs: the media service, else the web app.
- **Who plays:** whoever may use Media. Each game follows the share's permissions, like Files.
- **Saves:** on the server, per person:
  - the game's own save,
  - four save-state slots with screenshots,
  - an automatic state for Continue.
- **Off by default:** Snes9x and Genesis Plus GX are non-commercial only, and the same code runs on work installs.
- **The emulator:** EmulatorJS 4.2.3 from npm, vendored, unminified. It's pinned and checked by `python/fetch_emulatorjs.py`. No CDN.

## Progress

| Step | What | State |
| --- | --- | --- |
| 1 | GBA (mGBA): the Games folder, shelves, player, saves, admin switch, help, tests | Built 2026-11-14 (e694b2f); Doll: "this works perfect" |
| 2 | SNES (Snes9x), Game Boy and Game Boy Color (Gambatte), NES (FCEUmm), Genesis (Genesis Plus GX) | Built 2026-11-14 |
| 3 | PS1, Doll's stretch goal: PCSX-ReARMed for `.cue` (+ tracks), `.pbp` and archives; Beetle PSX for `.chd` with a BIOS | Built 2026-11-14 |
| 4 | On a paired TV, with a phone or pad as the controller | Idea |
| 5 | Box art looked up online (libretro-thumbnails, by No-Intro name) when *online metadata* is on | Idea |
| 6 | Master System and Game Gear: Genesis Plus GX plays them already, so a `CONSOLES` row each | Idea |

## How a console is added

1. In `python/fetch_emulatorjs.py`, add the core to `CORES` with its `npm view @emulatorjs/core-<name>@4.2.3 dist.integrity`. Run the script, then `--check`.
2. Add a row to `CONSOLES` in `mnemerynne/media/retro.py`:
   - `key` names it in URLs and on the shelf; `system` is EmulatorJS's name for it (`snes`, `gb`, `nes`, `segaMD`, `psx`).
   - Also its folder (and other usual names as `aliases`), core, suffixes, and any `bios` it needs for some formats.
   - The core's save folder goes in `SAVE_FOLDERS`: the `path` the probe in a browser shows (`gameManager.getSaveFilePath()`).
3. Add a cartridge colour in `static/retro.css` (`.rt-game[data-console=...]`).
4. Update the folder table in `help/user/212-retro.md` and the first line of `help/admin/260-media.md`'s Retro section.
5. Tests:
   - `tests/test_retro.py` checks that each console has its core.
   - A homebrew ROM for the new console could join `python/make_retro_test_rom.py` and `tests/retro-browser.mjs`.

Turning Retro on again makes the new console's folder, because `ensure_library` adds missing ones.

## Things learned

- EmulatorJS names the ROM after the last part of its URL, and keeps the game's own save in the browser under that name. ROM URLs therefore end in the ROM's sha256, never `game`, or one game would load another's save.
- `takeScreenshot()` returns `{blob}`, but its save-state event handler reads `{screenshot}`, so the screenshot arrives empty. Retro takes its own from RetroArch (`gameManager.screenshot()`), while the game runs, because a paused game never draws the frame.
- mGBA's states hold the battery save, so loading a state puts the save back as it was.
- The zip and 7z extractors `eval` (old Emscripten `cwrap`). The player's CSP allows `'unsafe-eval'` and keeps `connect-src 'self'`.
- The emulator asks for the ROM with `HEAD` first.
- Every core keeps the game's own save in `/data/saves/<core's name>/` (`retro.SAVE_FOLDERS`). The player puts the server's copy there before boot. If a core's name changes, the check at start still finds the save and restarts the game once.
- FCEUmm fills the NES picture chip's RAM with a pattern at power-on, so a homebrew test must set every palette entry, not only the backdrop.
- PCSX-ReARMed's build here has no `.chd` support (its `core.json` lists none), so `.chd` goes to Beetle PSX, which needs a real BIOS. PCSX-ReARMed runs a bare PS-X EXE without one, which is how the PS1 test game works.
- A loose `.cue`'s tracks reach the emulator through EmulatorJS's `externalFiles`, written beside the `.cue` under the names it gives them. The BIOS is written beside the game under its URL's last part, so that part is the lower-case name the core looks for.
- Snes9x and Genesis Plus GX are non-commercial only; the GPL-2 cores' "noncommercial" wording is only the GPL's clause about passing on source code.
