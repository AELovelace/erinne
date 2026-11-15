# Retro: console games in Media

Doll asked on 2026-11-14 for "snes, gba, gbc, nes, and genesis emulators", a share for ROMs, and a page to browse and play them. Her order: **GBA first, SNES second, Game Boy third**.

## Decisions (Lumi's picks; Doll didn't choose otherwise)

- **Where it lives:** inside Media, as **Media → Retro**, a page of its own (`/media/retro`), linked from Media's menu like Remote. It runs wherever Media runs: the media service, else the web app.
- **Who plays:** whoever may use Media. Each game follows the share's permissions, like Files.
- **Saves:** on the server, per person:
  - the game's own save,
  - four save-state slots with screenshots,
  - an automatic state for Continue.
- **Off by default:** Snes9x is non-commercial only, and the same code runs on work installs.
- **The emulator:** EmulatorJS 4.2.3 from npm, vendored, unminified. It's pinned and checked by `python/fetch_emulatorjs.py`. No CDN.

## Progress

| Step | What | State |
| --- | --- | --- |
| 1 | GBA (mGBA): the Games folder, shelves, player, saves, admin switch, help, tests | Built 2026-11-14 |
| 2 | SNES (Snes9x): a `CONSOLES` row, a `CORES` pin, the fetch run again, a cartridge colour | Next |
| 3 | Game Boy and Game Boy Color (Gambatte): one core for both; folders `Game Boy` and `Game Boy Color` | After |
| 4 | NES (FCEUmm) and Genesis (Genesis Plus GX), from Doll's first list | Later |
| 5 | On a paired TV, with a phone or pad as the controller | Idea |
| 6 | Box art looked up online (libretro-thumbnails, by No-Intro name) when *online metadata* is on | Idea |

## How a console is added

1. In `python/fetch_emulatorjs.py`, add the core to `CORES` with its `npm view @emulatorjs/core-<name>@4.2.3 dist.integrity`. Run the script, then `--check`.
2. Add a row to `CONSOLES` in `mnemerynne/media/retro.py`:
   - `key` is EmulatorJS's system name (`snes`, `gb`, `nes`, `segaMD`).
   - Also its folder, core and suffixes.
3. Add a cartridge colour in `static/retro.css` (`.rt-game[data-console=...]`).
4. Update the folder table in `help/user/212-retro.md` and the first line of `help/admin/260-media.md`'s Retro section.
5. Tests:
   - `tests/test_retro.py` checks that each console has its core.
   - A homebrew ROM for the new console could join `python/make_retro_test_rom.py` and `tests/retro-browser.mjs`.

Turning Retro on again makes the new console's folder, because `ensure_library` adds missing ones.

## Things learned building step 1

- EmulatorJS names the ROM after the last part of its URL, and keeps the game's own save in the browser under that name. ROM URLs therefore end in the ROM's sha256, never `game`, or one game would load another's save.
- `takeScreenshot()` returns `{blob}`, but its save-state event handler reads `{screenshot}`, so the screenshot arrives empty. Retro takes its own from RetroArch (`gameManager.screenshot()`), while the game runs, because a paused game never draws the frame.
- mGBA's states hold the battery save, so loading a state puts the save back as it was.
- The zip and 7z extractors `eval` (old Emscripten `cwrap`). The player's CSP allows `'unsafe-eval'` and keeps `connect-src 'self'`.
- The emulator asks for the ROM with `HEAD` first.
