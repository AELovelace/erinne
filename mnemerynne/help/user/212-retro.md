# Retro: console games

**Media → Retro** plays console games right in the browser, with nothing to install. Today it plays **Game Boy Advance** games. Super Nintendo and Game Boy are next.

Retro only appears once an admin has turned it on. The games are ones your household puts on the share: Erynne never downloads any.

## The shelves

- **Continue playing** at the top has the games you played last.
- Below it is a shelf per console.
- **Find a game** searches by title. The console menu shows one console's games.
- **▶ Continue** on a game means it has an automatic save to carry on from. **💾 Saved** means it has its own save.

You see the games your account can read on the share, the same as in Files. A folder closed to you in Windows is closed here too.

## Adding games

Put your own ROMs in the **Games** folder on the share, in the folder for their console:

| Folder | Console | Files |
| --- | --- | --- |
| `GBA` | Game Boy Advance | `.gba`, or a `.zip` or `.7z` holding one |

- Folders inside a console's folder are fine, such as `GBA/RPGs`.
- New games appear after the next share scan, within a few minutes.
- **Box art:** put a picture beside a game with the same name, such as `Golden Sun.gba` and `Golden Sun.png` (`.png`, `.jpg` or `.webp`). Without one, the game shows as a cartridge with its title on the label.
- **Names:** tags like `(USA)`, `(Rev 1)` or `[!]` are left off the title shown.

## Playing

Press a game, then **▶ Start**, or **▶ Continue** to carry on from your last visit.

- **Controls:** a keyboard, or any game controller the browser sees, such as an Xbox, PlayStation or Switch pad. The emulator's **🎮** button, at the bottom of the game, shows and changes the keys. On a phone, buttons appear on the screen.
- **⛶** fills the screen.
- The emulator's own bar at the bottom has pause, restart, volume, settings and cheats.

## Saves

Everything is saved on the server, so a game carries on from any browser or device you sign in on.

- **The game's own save** is saved by playing as you normally would, the way the cartridge's battery kept it. Retro keeps it as the game writes it, every 30 seconds while it changes, and when you leave.
- **💾 Save** in the bar saves a **save state**: the game exactly as it is now, in one of four slots, each with a picture of the screen. **📂 Load** puts one back. A filled slot asks before **Replace** or **Delete** goes ahead. The emulator's own save and load state buttons open the same slots.
- **Automatic:** Retro saves a state every few minutes while you play, and when you leave with **← Retro**. **Continue** starts from it next time, and **📂 Load** lists it as *Automatic*.
- Loading a state also puts back the game's own save as it was at that moment. That's how the emulator works, as in RetroArch: a state is the whole console.
- Leave with **← Retro** rather than closing the tab, so the last minutes are saved. If saving fails, the button says **Leave without saving**; press it again to go anyway.

Saves belong to the game, not the file. Renaming a ROM, moving it or putting it back later keeps its saves, and two copies of one game share them. Your saves are yours alone: someone else playing the same game has their own.

## Where Retro isn't yet

- **On a TV**, as a paired screen: Retro's pages need a person signed in.
- **Netplay**, playing together over the network.
