# Retro: console games

**Media → Retro** plays console games right in the browser, with nothing to install:
- NES
- Super Nintendo
- Genesis
- Game Boy and Game Boy Color
- Game Boy Advance
- PlayStation

Retro only appears once an admin has turned it on. The games are ones your household puts on the share: Erynne never downloads any.

## The pages

Retro's library is a few pages. Flip between them with a controller's **LB** and **RB**, the **[** and **]** keys (or Page Up and Page Down), or the tabs at the top:

- **⭐ Top:** **Continue playing** first, the games you played last. Below it is a row for each console with its **50 most popular** games on the share. Scroll a row left and right. The most played comes first, marked **#1**, **#2** and so on. A row's last card, **All**, opens that console's page.
- **A page per console:** all of its games, A to Z.
- **Find a game** searches every console at once by title.

Popularity is how many people have played a game on [RetroAchievements](https://retroachievements.org), shown as **👥** under the game (for example `👥 12k`). It appears once an admin has added a RetroAchievements key, and fills in over the first few hours. A game RetroAchievements doesn't know, like a homebrew, goes after the ranked ones, A to Z.
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

- Any game can also be a `.zip` or `.7z` holding it. One holding several ROMs (an original and its romhacks, say) asks **Which ROM?** when you open it, the original first (marked **⭐ Original**: no hack, translation or odd-dump marks in its name, not in a folder like `hacks/`): nothing starts until you pick one, only that one is played, and each keeps its own saves. **↩ Another ROM** goes back to the list. (A PlayStation disc's zip is one game.)
- An existing folder with another usual name counts too, such as `Super Nintendo`, `Mega Drive` or `PlayStation`.
- Folders inside a console's folder are fine, such as `SNES/RPGs`.
- New games appear after the next share scan, within a few minutes.
- **Box art:** put a picture beside a game with the same name, such as `Chrono Trigger.sfc` and `Chrono Trigger.png` (`.png`, `.jpg` or `.webp`). Without one, the game shows as a cartridge in its console's colours (a disc for the PlayStation) with its title on it.
- **Names:** tags like `(USA)`, `(Rev 1)` or `[!]` are left off the title shown.

### The Unsorted folder

Not sure which folder a game belongs in? Drop it in **Games/Unsorted** (folders inside it are fine). Every few minutes the NAS looks inside each file and moves it to its console's folder:

- **It reads what the game says about itself, not its name:** the NES's iNES tag, the SNES's internal header, `SEGA` in a Genesis cartridge, the Game Boy and GBA headers, a PlayStation disc's volume label.
- **Archives:** it reads inside a `.zip` (its biggest file) or a `.7z`.
- **Game Boy or Color:** a game made for the Color, or that uses its colours, goes to **Game Boy Color**.
- **Extensions:** a game whose extension its console's shelf doesn't list gets the console's own, such as `mystery.bin` → `mystery.gba`. A PlayStation disc image without a `.cue` gets one written beside it.
- **What travels with it:** a `.cue` takes its `.bin` tracks (it waits until they're all there), a picture named like the game goes with it as its box art, and a BIOS goes to its console's folder by its name.
- **What it never does:** replace anything. A name that's taken gets `(2)`, and a game that's already in its folder, byte for byte, stays in Unsorted.
- **What stays in Unsorted:** files still being copied in (it waits two minutes after the last change) and anything it doesn't recognise.

If you delete the Unsorted folder, it isn't made again.

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

- **Controls:** a keyboard, or any game controller the browser sees, such as an Xbox, PlayStation or Switch pad, or a Super Nintendo pad on a Mayflash MAGIC-S PRO (in Switch mode its buttons and D-pad are put the right way round for you). The emulator's **🎮** button, at the bottom of the game, shows and changes the keys for that console. On a phone, buttons appear on the screen.
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

## On a TV

On a paired TV, **🕹 Retro** in the TV's menu opens Retro in the TV's game layer, as the TV's owner (their games and their saves).

- **The remote:** the arrows walk the shelves and OK opens a game. In a running game, the arrows and OK are the console's D-pad and A, and **⏯** opens the game's menu.
- **A game controller** (on a TV box: Media help, *Game controllers on a TV box*) plays the game. A second one is player 2, and up to four can play. Its **Xbox** or **PS** button, or **Start** and **Select** held together, opens the game's menu: **Resume**, **Save state**, **Load state**, **Leave the game**. In the shelves and menus, the D-pad moves, **A** presses and **B** goes back.
- **Leaving:** the TV bar's **Back**, Escape, **Leave the game**, or the remote's **Stop**. It saves first (the game's own save and an automatic state), then closes, as **← Retro** does.

## Playing together (netplay)

Up to four people can play one game together, each in their own browser or on their own TV. It works across the house or between houses.

1. Everyone opens **the same game** in Retro and presses **Start**.
2. One person presses **👥** in the game's bar (on a TV: **👥 Play together** in the game's menu) and **Open a room**. They're the **host** and player 1.
3. The others press **👥** too. The room shows under **Rooms open for** the game: **Join**. They're players 2, 3 and 4, in the order they joined.
4. That's all: as soon as someone joins, everyone's game jumps to where the host's is, and you're playing together. The host doesn't need to press anything. A guest's own game pauses the moment they join, until the host's arrives.

Good to know:

- **Who can join:** only people who can read that game on the share see its rooms.
- **No lag on your own buttons:** your presses reach your game on the next frame, as when playing alone. The others' presses take a moment to arrive, so until they do, your game guesses they're still holding what they held. When the real presses come and the guess was wrong, your game quietly goes back a few frames and plays them again with the real presses, in a few thousandths of a second. Now and then you may see someone's character jump a little: that's a correction. This is **rollback**, as fighting games online do it.
- **Input delay:** the host picks it before opening the room. **Normal** (one frame) suits nearly everyone. **Instant** answers a frame sooner, with a few more corrections. **Steady** waits two frames, for fewer corrections on a slow connection.
- **The PS1 is different:** it's too heavy to play frames again, so its games wait for everyone's presses instead (lockstep). Your own buttons answer a little later there: the host picks **Low**, **Normal** or **Smooth** for how much later, to cover the connection.
- **Waiting:** if someone's connection stalls (more than about eight frames behind), everyone sees **Waiting for …** until it catches up.
- **Saves:** the game is the host's. The host's saves and states work as usual. Guests don't save, so a guest's own progress in that game is left as it was.
- **Joining later:** each new player makes everyone start again from where the host is, even mid-game. So does the host loading a state. Two people joining at almost the same moment start one after the other, a second or two apart.
- **Staying in step:** every few seconds the games check they're still the same. If they've drifted apart, everyone starts again from the host's, by itself. **↻ Resync everyone** does it by hand.
- **Leaving:** **Leave the room** carries on alone. The host's **Close the room** ends it for everyone, and everyone's game carries on alone. The host can **Let go** of a player.
- **Controllers:** each browser plays with its first controller, its keyboard, or a TV's remote. A second controller on the same TV doesn't play in a room.

## When a game runs slowly

If a game can't keep up, the bar says **Running slowly (N fps)** and names the graphics drawing it. The games draw every frame on the graphics card, so a slow game usually means the graphics, not the computer's speed. On a TV box, ask your admin to run the screen check (`install.sh --check`). It says which graphics the TV is on.

## Where Retro isn't yet

- **Netplay with two controllers on one TV** (two players from one TV in a room).
