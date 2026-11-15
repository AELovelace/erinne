# EmulatorJS (Media → Retro)

[EmulatorJS](https://github.com/EmulatorJS/EmulatorJS) 4.2.3 is RetroArch built for the browser. It plays the games in Media → Retro (`mnemerynne/media/retro.py`, `mnemerynne/web/retro.py`, `static/retro-player.js`).

Everything here comes from `python/fetch_emulatorjs.py`. Don't edit these files by hand.

The script downloads the npm packages pinned in it, checks each one against the registry's sha512, and copies only what the player loads:

| Here | From | What it is |
| --- | --- | --- |
| `src/*.js`, `emulator.css` | `@emulatorjs/emulatorjs` | The player, unminified. `retro-player.js` loads the scripts in order, in place of EmulatorJS's own `loader.js`. |
| `compression/extract7z.js`, `extractzip.js` | `@emulatorjs/emulatorjs` | Opening the cores, which are 7z archives, and zipped ROMs. Both run as `blob:` workers. |
| `cores/<core>-wasm.data`, `<core>-legacy-wasm.data` | `@emulatorjs/core-<core>` | One console's emulator. The legacy build is for browsers without WebGL2. |
| `cores/reports/<core>.json` | `@emulatorjs/core-<core>` | The core's build stamp. Browsers cache a core until its stamp changes. |
| `MANIFEST.json` | the script | The version, the cores and each file's sha256. |

Left out on purpose:
- the minified build, which the npm package doesn't have,
- socket.io (netplay, which is off),
- the RAR extractor (Retro takes `.zip` and `.7z`),
- the translations (the player runs in English),
- every core no console uses.

## Updating, or adding a console's core

1. Change `VERSION`, or add the core to `CORES` with its `npm view @emulatorjs/core-<name>@<version> dist.integrity`.
2. Run `python python/fetch_emulatorjs.py`.
3. Run `python python/fetch_emulatorjs.py --check` to confirm the folder matches the pins. `tests/test_retro.py` runs the same check.

## Licences

- **EmulatorJS** is GPL-3.0 (`LICENSE`). Its source is here as published, unminified.
- **Each core** carries its own licence inside its `.data` archive (`license.txt`, shown in the player's menu):
  - mGBA (Game Boy Advance) is MPL-2.0.
  - FCEUmm (NES), Gambatte (Game Boy), PCSX-ReARMed and Beetle PSX (PlayStation) are GPL-2.0.
  - **Snes9x (Super Nintendo) and Genesis Plus GX (Genesis) are for non-commercial use only.** That's why Retro is off until an admin turns it on.
