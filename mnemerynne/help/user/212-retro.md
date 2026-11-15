# Retro: console games

**Media → Retro** plays console games right in the browser, with nothing to install:
- NES
- Super Nintendo
- Genesis
- Game Boy and Game Boy Color
- Game Boy Advance
- PlayStation

Retro only appears once an admin has turned it on. The games are ones your household puts on the share: Erynne never downloads any.

## The shelves

- **Continue playing** at the top has the games you played last.
- Below it is a shelf per console.
- **Find a game** searches by title. The console menu shows one console's games.
- **▶ Continue** on a game means it has an automatic save to carry on from. **💾 Saved** means it has its own save.
- **Needs a BIOS** means the game won't start until a BIOS file is put on the share. See [BIOS files](#bios-files).

You see the games your account can read on the share, the same as in Files. A folder closed to you in Windows is closed here too.

## Adding games

Put your own games in the **Games** folder on the share, in the folder for their console:

| Folder | Console | Files |
| --- | --- | --- |
| `NES` | NES (Famicom) | `.nes`, `.unf`; `.fds` (Famicom Disk System) needs a BIOS |
| `SNES` | Super Nintendo | `.sfc`, `.smc`, `.swc`, `.fig` |
| `Genesis` | Genesis (Mega Drive) | `.md`, `.gen`, `.smd`, `.bin` |
| `Game Boy` | Game Boy | `.gb`, `.gbc` |
| `Game Boy Color` | Game Boy Color | `.gbc`, `.gb` |
| `GBA` | Game Boy Advance | `.gba` |
| `PS1` | PlayStation | `.cue` with its `.bin` tracks, `.pbp`, or `.chd` (which needs a BIOS) |

- Any game can also be a `.zip` or `.7z` holding it.
- An existing folder with another usual name counts too, such as `Super Nintendo`, `Mega Drive` or `PlayStation`.
- Folders inside a console's folder are fine, such as `SNES/RPGs`.
- New games appear after the next share scan, within a few minutes.
- **Box art:** put a picture beside a game with the same name, such as `Chrono Trigger.sfc` and `Chrono Trigger.png` (`.png`, `.jpg` or `.webp`). Without one, the game shows as a cartridge in its console's colours (a disc for the PlayStation) with its title on it.
- **Names:** tags like `(USA)`, `(Rev 1)` or `[!]` are left off the title shown.

### PlayStation discs

- **`.cue` and `.bin`:** keep the `.cue` and its `.bin` tracks together in one folder. The `.cue` is the game on the shelf; its tracks come with it when you play. If a track the `.cue` names is missing, the player says which.
- **`.pbp`, or a `.zip` or `.7z` of a disc's files:** play as they are.
- **`.chd`:** compressed discs play on a second PlayStation emulator, which needs a PlayStation BIOS.
- Discs are big. A game downloads whole before it starts, so the first start takes a while, more on a phone. After that, the browser keeps a copy.

### BIOS files

A few kinds of game need the console's BIOS: PlayStation `.chd` discs, and Famicom Disk System (`.fds`) games. Put a BIOS dumped from your own console in that console's folder on the share, any folder inside it:

- **PlayStation:** `scph5501.bin` (North America), `scph5500.bin` (Japan) or `scph5502.bin` (Europe). `scph1001.bin`, `scph7001.bin` and `scph101.bin` also work for `.cue` and `.pbp` games.
- **Famicom Disk System:** `disksys.rom`.

Any letter case works. A PlayStation BIOS also helps `.cue` and `.pbp` games: with one there, they use it instead of the emulator's built-in stand-in, which gets a few more games working.

## Playing

Press a game, then **▶ Start**, or **▶ Continue** to carry on from your last visit.

- **Controls:** a keyboard, or any game controller the browser sees, such as an Xbox, PlayStation or Switch pad. The emulator's **🎮** button, at the bottom of the game, shows and changes the keys for that console. On a phone, buttons appear on the screen.
- **⛶** fills the screen.
- The emulator's own bar at the bottom has pause, restart, volume, settings and cheats.

## Saves

Everything is saved on the server, so a game carries on from any browser or device you sign in on.

- **The game's own save** is saved by playing as you normally would. That's the cartridge's battery save, or a PlayStation's memory card. Retro keeps it as the game writes it, every 30 seconds while it changes, and when you leave.
- **💾 Save** in the bar saves a **save state**: the game exactly as it is now, in one of four slots, each with a picture of the screen. **📂 Load** puts one back. A filled slot asks before **Replace** or **Delete** goes ahead. The emulator's own save and load state buttons open the same slots.
- **Automatic:** Retro saves a state every few minutes while you play, and when you leave with **← Retro**. **Continue** starts from it next time, and **📂 Load** lists it as *Automatic*.
- Loading a state can also put back the game's own save as it was at that moment. The Game Boy Advance's emulator works that way, as in RetroArch: a state is the whole console.
- Leave with **← Retro** rather than closing the tab, so the last minutes are saved. If saving fails, the button says **Leave without saving**; press it again to go anyway.

Saves belong to the game, not the file. Renaming a game, moving it or putting it back later keeps its saves, and two copies of one game share them. Your saves are yours alone: someone else playing the same game has their own.

## Where Retro isn't yet

- **On a TV**, as a paired screen: Retro's pages need a person signed in.
- **Netplay**, playing together over the network.
