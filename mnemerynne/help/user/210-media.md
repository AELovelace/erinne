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
  - **A** steps to the next sound track and **C** to the next subtitles (then off);
  - **Escape** closes the player.
- **Sound and subtitle tracks:** a film with more than one sound track shows **🗣** in the player's bar, and a film with subtitles shows **CC**. Press either for a list, then pick with the mouse or with **↑**, **↓** and **Enter**. **Escape** closes the list. **CC** is lit while subtitles show.
  - Picking a sound track other than the film's first restarts it from the same place. The server repackages it (the player says **Repackaged**), which takes a second or two.
  - Text subtitles (SRT, ASS, the subtitles inside MP4s) show straight away. The first time, the server reads the whole file to find them, so on a big film they can take a few seconds to appear. ASS styling (fonts, colours, positions) isn't kept.
  - Picture subtitles (Blu-ray PGS, DVD) are marked **burned in**. The server re-encodes the film with them drawn onto the picture, so they count as a transcode. After a jump, the line already on screen at that moment is skipped; the next one shows as normal.
  - Each device remembers the languages you last picked and uses them for the next film or episode. Picture subtitles are never turned on by themselves, though: pick them from **CC** each time.
  - Subtitles in separate files next to the film (`Film.en.srt`) aren't offered yet; only tracks inside the film file are.
- **TV Shows:** search shows by name or description at the top, and sort them by **Title**, **New episodes**, **Recently watched** or **Year**. Open a show and press **Play** for the episode to watch next. That's **Resume** if you stopped part-way, otherwise the episode after the last one you finished. **From the first episode** starts over. The season buttons switch between seasons.
  - **Watched:** the circle beside each episode shows ✓ once you've watched it. Press it to mark an episode watched (or not) without playing it, for example ones you saw somewhere else. **Mark season watched** does a whole season, and **Play** then goes on from the next unwatched episode.
  - When an episode ends, **Up next** counts down 10 seconds and then plays the next one. **Play now** skips the wait; **Cancel** closes the player.
  - ⏮ and ⏭ in the player, or **Shift+P** and **Shift+N**, go to the previous or next episode.
- Most films play straight away. Some formats need the server to convert them first, and the player then says **Transcoding** or **Repackaged**. That takes a few seconds to start, and jumping far ahead takes a moment longer.

## Music and radio

- Open an album and press **Play** or **Shuffle**, or choose a track. Music plays in the bar at the bottom while you keep browsing. On a phone, press **♫** beside the menu at the top: the controls slide out from the side, and tapping outside them puts them away.
- **Radio** lists the stations. **On air** means someone is already listening. Other stations start when you tune in, which can take a few seconds.

## SoundCloud

**SoundCloud** in the menu shows someone's profile: **Tracks**, **Albums**, **Playlists**, **Likes**, **Liked sets** and **Reposts**.

- The first time, type the name after `soundcloud.com/` or paste the profile's address. **Change profile** switches to another. It's saved for you, and it's your own: other people pick theirs.
- Press **Play** or **Shuffle**, or choose a track. It plays while you keep browsing, with the same controls as your music.
- **Load more** at the bottom of a long list brings the next tracks. **↗** opens a track on SoundCloud.
- **Search SoundCloud** at the top finds **Tracks**, **Playlists** and **Profiles**, and works before you've picked a profile. Opening a profile from the results doesn't change the one saved for you. A TV's remote can't type, so search on your phone with **Play on** set to the TV.
- **📻** beside a track starts a station: that track, then ones like it, topping itself up for as long as it plays. On a TV, press right on a track to reach it.
- With **Play on** set to a TV, it plays there. Speakers can't play SoundCloud.
- On a TV, the profile is set for that screen, in **Screens** on your phone or in **Kiosk settings**. With none set, it shows the screen owner's.

A few tracks only have a 30-second **Preview only**, and some can't be played here at all (the list says how many). If SoundCloud's daily limit for the server is reached, the page says when to try again.

## Live TV

**Live TV** lists the streams your admin has added. Choose one to watch it live. YouTube ones play in YouTube's own player, with its controls; press **✕** to close it.

## Games

**Games** (🎮, after Radio) lists the games that come with Erynne, on a shelf for each kind: **Lumi Pet** first, then **Arcade**, then **Table games** (blackjack, roulette, horse racing and slots). Everyone who can use Media can play them, and so can a TV, as its owner.

- Choose one and it opens over the whole page. **← Games** (or Escape or Backspace) goes back; **⛶** fills the screen; **⧉** opens it in a tab of its own.
- The bar at the top shows the game's controls. They're the same on a keyboard and on a **remote panel**: its arrows and **Enter** play the game while it's open on the TV, and the panel shows the score under the game's name. The panel's **Stop**, or sending anything to the TV from your phone, closes the game.
- **Coins** are one purse for your whole account, shared by every game and shown at the top of Games. You start with 1,000. They're only for fun: they have no cash value, belong to this Erynne server alone, and aren't SadGirlCoin. A TV uses the coins of the account it's paired to.
- Online casino tables bet your coins. Breakout, Snake and Teacup Tower cost 25 coins a run and pay their score. Prism Drop, StickBob Runner and Lumi Pet pay some, and Lumi's shop spends them. If you can't afford the smallest bet, a casino table offers a free refill back to 1,000.
- Several players on one TV with remotes play for **free credits** instead, and never touch anyone's coins.
- Prism Drop remembers its best score on this device; coins, table balances and rounds are saved on Erynne's server.
- Games under **Mouse & keyboard** can't be played with a remote or a touch screen, so that shelf only shows on a device with a mouse. A TV and a phone don't list them.

**StickBob Runner** 🏃 (Mouse & keyboard): run as far as you can through an endless string of StickBob's levels, past spikes, pits and SlopBots. You have five health, and falling in a pit ends the run. **A** and **D** run, **W** or **Space** jumps, **S** slides, or crawls when you're standing still. The mouse aims and the left button shoots, the right button or **Shift** kicks, the middle button or **F** is the knife. **P** pauses and **M** turns the sound off. To **wall jump**, as in Super Metroid: in the air against a wall, press away from it (StickBob holds on for a moment), then jump. Going back to the wall and doing it again climbs it. Your score is how far you got in metres, and your best stays on this device. When a run ends it pays **1 coin for every 10 m plus 1 for each kill**, shown on the score panel. Your admin may set a daily limit; there's none unless they do. Closing the game mid-run, or starting a new run, gives up the one in progress, and it pays nothing.

**Prism Drop** 🔮: pick one of ten pockets and drop a neon ball through twenty rows of pins. A game is ten drops, and a finished game pays **1 coin for every 10 points**, up to 200 coins a day. A game you leave unfinished carries on when you come back, even on another device.

- **◀ ▶** pick the pocket, **Enter** drops (after the tenth drop, Enter starts a new game), **▲** replays the last drop without scoring, **▼** turns the sound on or off. On a phone, tap a column to pick it and tap it again to drop.
- Points for where it lands: **20** in your pocket, **15** one pocket away, **10** two away, nothing further.
- Each drop gets a fresh field of special pegs: **blocks** shove the ball two columns sideways, **bombs** blast it two or three spaces in any direction (each fires once), and **coin pegs** add 1–5 bonus points (once each), even if the landing misses. Two **platforms** slide left and right and bounce the ball back up like a breakout paddle: the edge it lands on sends it that way, and each platform gives two bounces per drop before it fades.
- The ball enters at pin 4, 5, 6 or 7 (marked in cyan) at random.

**Breakout** 🧱 and **Snake** 🐍 come from Doll's GameMaker arcade. Each opens on a menu: **Play solo** or **Showdown tables**. A solo run costs **25 coins** and pays **1 coin a point** when it ends, once Erynne's server has checked the run. Close the game mid-run and that run's coins are gone.

- **Breakout:** break every brick with the ball. **◀ ▶** move the bat (hold them on a keyboard, or point and drag); **Enter**, **▲** or a tap launches the ball. Every brick is a point. You have three lives; clear a level and the next one is built. Falling icons do things when the bat catches them: a wider bat, multiball, bigger balls, every ball aiming at a brick, or everything slowing down. On a remote, ◀ ▶ nudge the bat along.
- **Snake:** eat and grow without hitting the walls or yourself. The arrows turn (two quick turns wait their turn), and the first one starts the game. Red food is 1 point, yellow 5, aqua 10. On a touch screen, swipe.
- **P** pauses a solo run.

**Teacup Tower** 🫖 is our own climber in the same shell, with Lumi from Lumi Pet as the climber. She bounces off every teacup by herself; **◀ ▶** steer her (hold them on a keyboard, or hold a finger or the mouse to one side of her; each remote press steers for a moment). The faster she's moving sideways, the higher she bounces, and the side walls kick her back a little faster, so a run at a wall can carry her past several floors. Hops of two floors or more in a row build a **combo**. Every 25th floor is a wide landing saucer; higher up some cups slide and some are cracked (one bounce each). From floor 5 the tower starts rising by itself, faster every 30 seconds: fall below the bottom and the climb is over. A run costs 25 coins and pays **1 coin a floor**. Showdowns race up the same tower; the higher floor wins.

All built-in games on a paired TV follow **Screens → Text size**, just like the Media UI. Automatic keeps the 1080p size and enlarges games at higher resolutions (1.5× at 2560×1440, 2× at 3840×2160, using the browser's CSS viewport). A saved custom size takes precedence. Changes apply to an open game without restarting it.

### Lumi Pet

Look after a little glowing fairy cat in **Games → Lumi Pet**. On a TV remote, **Previous/Next** change tab, **Up/Down** move through the list, **Left/Right** flip the choices of the row you are on, and **Enter** does it. On a keyboard, Previous/Next are **Page Up/Page Down** (or **[** and **]**). You can also tap or click anything. The tabs are **Care** (cuddle, nap), **Pantry** (her food), **Toys**, **Closet** (what she wears), **Home** (decorating), **Battle**, **Shop** (snacks, treats and toys for coins) and **Settings** (her glow colour, sound, reminders). Keyboard Tab reaches individual controls, and Escape/Back returns to Games.

Lumi has three needs: **Tummy**, **Energy** and **Joy**. They run down while you're away, quickly when full and slowly near empty: the last quarter takes a whole day. Snacks fill her tummy, a five-minute nap restores energy when it ends, and cuddles and play bring joy. There is always a free apple and a free star ribbon. If a need reaches zero she is sad and sleepy, but she never loses anything, and a snack and a cuddle set her right. Lumi sleeps from 22:00 to 07:00 on your device's clock, and lives on your account, so she is the same on your phone, computer and TV.

**Reminders:** on a phone or computer, **Settings → Reminders** lets Lumi send this device a notification when a need drops into its last quarter, or when she wakes from a nap you started. Your browser asks for permission the first time. Each device is switched on by itself, and TVs are never asked. She sends at most one every three hours and none between 22:00 and 07:00; a need you've already seen, or fixed, is never mentioned. On Android, install Erynne as an app first (see [Erynne on a phone](phone.md)). On an iPhone, reminders only work from the Home Screen app.

**Battles:** in **Battle → Find a fight**, choose how tough an opponent to look for: Common, Uncommon, Rare, Epic, Legendary, or **Gamble** (anything at all, for a fifth more reward). Each turn, pick one of her moves, **Defend** (halves the next hit and gathers MP), a **Potion** (20 coins each between fights; restores half her health) or **Run** (works three times in four). Claws and paws are physical; spells cost MP. Every creature is weak to some elements and shrugs off others, and the fight says so: "super effective" or "resisted". Lumi's own element is her **glow** (Settings): rose is fire, lagoon ice, starlight holy, zephyr wind, wisp spirit, umbra dark. A fight waits for you as long as you like, on any device. Winning pays EXP and coins. If she loses she naps for ten minutes, or wakes at once for 50 coins; nothing else is lost. **Skills:** every level gives one skill point for six trees (Fighter, Thief, Monk, Mage, Cleric, Paladin). Each tree branches in its own shape: a skill needs the one or two skills that lead to it, not every skill above it, so you can head straight down one arm. A whole tree still costs 22 points, so by level 50 she can finish about two; handing all points back costs 100 coins. She carries four moves into a fight (**Moves**). **Mods:** five slots open at levels 10, 15, 25, 35 and 45, three for cybertek and two for magitek; each mod adds a little to one of her numbers.

**Home and Closet:** her house has fifteen spots (wallpaper, floor, wall art, shelf, window view, window frame, curtains, lamp, rug, bed, door, ceiling hanging, side table, plant, toy chest), each with five objects to start with or buy and ten more that only come from capsules (shown with a 🔒 in the spot's whole list until you win them). In **Home**, move to a spot and press **Left/Right** to flip through its objects in the room itself, then Enter to buy or put out the one showing; what you own swaps freely. Enter on a spot you haven't flipped opens its whole list instead. On a TV or computer her room fills the whole screen, with three windows. In the neon theme they look out on the same pixel cities that drift behind every page: the first, free **Window view** is simply "The city outside". The other four views (star sea, cozy forest, snowy hills, rainy rooftops) are painted into the windows instead and follow day and night; in other themes the free view is a painted city too. **Closet** works the same for what she wears, one thing each on her head, neck, wings, back, paws and tail: Left/Right try things on (one of the choices is nothing at all), Enter buys and wears the one showing or takes it off. Clothes are only for looks. **Capsules** are at the top of the Closet. The capsules are sold at the top of both the **Closet** and the **Shop**. The **Star Capsule** (150 coins) can give anything, and the **Moon Capsule** (300 coins) only gives rare, epic or legendary things. A prize is something to wear, a toy, or something for the house, and it goes straight onto Lumi, into her toy box or into the room. Ten toys only come from capsules, from a yarn ball to the legendary wishing star. If you get a repeat, it pays back half its value in coins: half its shop price, or for things that aren't sold 30, 60, 90, 200 or 500 coins from common to legendary. A capsule won't open once she has everything it can give.

**Dungeon:** the **Dungeon** tab sends Lumi exploring. Choose a place (twelve of them, from the Moonlit Shrine to the Rooftop Megacity Garden; more open as she levels up) and she arrives on a floor of about a dozen rooms. While she is exploring, the **arrows walk her**, one room a press (up is north), and the right-hand panel becomes the dungeon's own, with the tabs folded away: **Previous/Next** move through its list, **Enter** does the highlighted thing, and **Play/Pause** drinks a potion. **Look after Lumi** in that list opens a short list of the snacks she has and a potion, and **Back to exploring** returns; the other tabs (cuddles, naps, toys, the closet, the shop) wait until she is home. Each step goes into the next room: some are empty, some hold a creature to fight, some a chest or a key. A chest or key waits where it is: choose **Open the chest** or **Pick up the key** to take it, or leave it for later. One room is a **locked vault**: it opens with a **key** found elsewhere on that floor, or with a puzzle. For **crates**, the arrows push them onto the dots and Enter takes a step back; for **lamps**, move with the arrows and press Enter to flip a lamp and its neighbours until all are out; in both, Previous/Next reach the list (start over, step back); for **runes**, build a four-rune code and each try lights its runes up: **green** is the right rune in the right place, a **gold ring** is a rune that belongs somewhere else in the code, and a **dim** rune isn't in the code at all. You get eight tries before the door thinks of a new code. One room has a small game (pick a cup, or match pairs of cards). A little map shows where she has been. What she finds goes in her **satchel**, and only counts once she is home: **Go home** is in the room she came in by, and banks the coins, potions, trophies and mods. **Stairs** lead down to a harder floor with better treasure, and there is no way back up. Her health carries from fight to fight (drink a potion between rooms or in a fight; one found on the trip can be drunk straight away, and is used before her own), and if she is **beaten** the trip ends and the satchel is left behind; she keeps her EXP. **Give up and go home** also leaves the satchel, and asks twice. A trip waits for you as long as you like, on any device. Trophies go on a bracket in her house (**Home → Trophy**). Every place also has its own **decor set**, a piece for each spot in her house (a wallpaper, a bed, a window view and so on). Now and then a chest or a beaten creature there holds one she hasn't got, more often on deeper floors. In **Home**, a piece still to be found shows 🔒 and the place it waits in. **Settings → Storyteller** chooses who writes the dungeon's lines: **Library** is the set that comes with the game; **Live** also asks a model for a fresh line each moment, using your own Claude key (Preferences → Claude) or BFGguf if you have it.

**The story and the mouse:** on a trip the right-hand panel keeps the story: at the top, in colour, what the room she is standing in is like, then the last few things that happened, with the newest line just above the choices. The choices and the key legend sit at the bottom. **Look around** notices something new about the room each time you choose it; every place has its own details. Everything also works with a mouse or a finger: press a **door** to walk through it, the **chest** or **key** to take it, a **cup** to lift it, a **card** to turn it, a **lamp** to flip it, and any **square** of the crate board to step Lumi towards it. The list on the right does all the same things, so keyboard, mouse and remote can each play the whole game.

**Stardust Garden:** the **Garden** tab is a little flower farm with a powder press. It opens like a dungeon trip: the garden fills the room, the right-hand panel is the garden's own, and **Back to Lumi** returns to the other tabs.

**Skills:** the **Skills** tab shows all six class trees on one page, as webs of skills joined by wires. A circle is a move and a diamond is a passive. A skill glows in its class's colour once learned, is ringed brightly when it can be learned (the number on it is its cost), is dashed when you only lack the points, and is dim while the skills that lead to it are missing. Move over the skills with the **arrows**; the panel says what the skill is, what it costs and what it needs. **Enter** learns it; Enter on a move she knows carries it into fights or puts it away (she carries four). **Previous/Next** reach the list, where **Hand back every skill point** (100 coins) and **Back to Lumi** live. With a mouse, press a skill to look at it and press it again to learn it. Battle → Skills still lists the same trees as plain lists.

**Collection:** the **Collection** tab is an album of everything Lumi can collect: things for the house, dungeon finds, things to wear, toys, mods, the garden's flowers and its decorations. The first row counts everything; each shelf says how many of its things she has. Open a shelf to see every thing in it, ticked if it is hers, with where it comes from. Moving over a house object shows it in her room, clothes are tried on her, and a flower or garden decoration is shown large on a card. Nothing is bought or changed here: it is for looking.

- **Plots and flowers.** You start with four plots and can dig up to sixteen, for coins. Move over the plots with the **arrows**; **Enter** on an empty plot plants the chosen seed (pick one in **Seed**), and Enter on a ready flower trims it for **starpowder**. **Harvest everything ready** trims the lot. There are forty-two flowers, each with its own look, from a two-minute Moondaisy to day-long blooms like the Midnight Rose. Every flower has a timer under it. The first thirty are **cut flowers**: one harvest, then plant again, and they give the most powder for their time. The last twelve are **perennials**: they grow back by themselves and give less, so a garden full of them looks after itself. A ready flower waits for you; left for twice its growing time it is **overgrown** and gives half. Nothing is ever lost.
- **The ladder.** Each garden unlocks its flowers in order, as it gives more starpowder: ordinary ones for coins, **boutique** ones for starpowder only.
- **The press.** **Play/Pause** (or **P**, or Enter on **Press the powder**, or a press of the mouse on the machine) squeezes the waiting starpowder towards coins. The first 100 coins of a day cost 40 powder each, the next 100 twice that, and so on: it slows down but never stops, and powder you don't press keeps.
- **Two games for a time boost**, each about every half hour. **Water the flowers**: watch the watering cans light up, then repeat them with the arrows (or press them); each round you get right moves every growing flower 5% of its time forward, up to eight rounds. **Fertilize**: ten seconds of presses, as fast as you can; every ten presses is a minute for every growing flower, up to ten minutes.
- **Helpers and abilities.** Six helpers, one per class, are levelled with starpowder: the Fighter squeezes more per press, the Thief makes presses lucky and gifts likelier, the Monk works the press by himself (also while you are away, for up to eight hours at half speed), the Mage speeds the flowers, the Cleric keeps them fresh longer and keeps more while you are away, and the Paladin adds a little to everything. Every skill Lumi has really learned in a class's tree makes that helper a quarter stronger, her fitted mods add their percentages, and her level adds a little too. The moves she has equipped (in Battle) are abilities to fire here, each with its own wait.
- **Gifts.** Now and then a harvest turns up a puff of extra powder, a snack, a potion, or (at most once a week) a token worth a Star Capsule's coins.
- **Decorations.** Each garden has fifteen places to dress (fence, path, plot edging, statue, scarecrow, the press, lamps, arch, water, seat, tree, bush, banner, little visitors, and something in the sky), with fifteen things for each. Five are sold for starpowder. The other ten come only from the **Seedling Capsule** (600 starpowder), which may give a repeat. Whatever isn't out in a garden waits in **The store**, where a spare can be put out in another garden or sold back for starpowder. Decorations are for looks only.
- **Lumi helps.** She darts to whichever plot you are on, digs when you plant, hops when you trim, jumps on the press with every squeeze, and wanders off to sniff a flower when left alone. The sky behind the garden is the theme's own drifting scenery.
- **Moonrise.** A garden with all sixteen plots that has given enough starpowder can be replanted under a new moon: it goes back to four plots and the first flower, and you choose either a permanent upgrade for every garden, or **another garden** beside it. Every garden climbs its own ladder, old or new. There is no limit.
- **Reminders.** With Reminders on (Settings), Lumi sends one nudge when flowers that take half an hour or more are ready and you haven't looked. **Garden reminders** in Settings turns just that off. Quick flowers never nudge.

While Lumi is on a dungeon trip the garden can't be opened, but it keeps growing and the Monk keeps pressing.

**Health:** the **Health** bar under her needs is what she has left after a dungeon trip, and she sets out on the next trip with exactly that. It mends by itself: a whole bar takes ten minutes, so half a bar takes five and a small bruise a minute or so. A **Nap** (Care) mends twice as fast. Food you **bought** mends her by its tummy number (a lunch box gives back 45% of her health; the free apple fills her tummy but mends nothing), and **Drink a potion** in Care gives back half. Being beaten in the dungeon leaves her with none, which is the same ten-minute nap as before, and waking her for 50 coins also makes her whole. Fights from the **Battle** tab are friendly: she always starts those at full health and they leave no bruises. On a trip nothing mends her but potions and bought food.

Your first activity with Lumi each day pays **25 coins**. Missing a day costs nothing.

### Multiplayer casino tables

**Blackjack**, **Roulette**, **Horse Racing**, and **Slot Machines** support up to eight players per table. Open a game and choose **Create table**. Friends open the same game and select that table's code in the lobby. Tables are visible to everyone who can use Media.

**Several players on one TV:** pair the TV and each remote to the same owner's account, give the remotes distinct names, and select that TV on each panel. On the paired TV choose **One TV, multiple remotes**. Each remote presses **Enter once to join** its own named seat. Left/Right chooses that player's pick, Up/Down adjusts their stake, and **Enter again places the bet**. The first player is host; their Play/Pause starts the round. Each blackjack player independently chooses Hit/Stand and confirms with Enter. Every player's balance, draft, wager and results appear on the TV; slots show a separate three-line board for each player. The TV itself uses no player slot. Other devices may still join the table code. These tables play for **free credits**, not coins: 1,000 for each player at each table, gone when the table ends.

Refresh keeps these seats and selections; a fresh tab can choose **Resume on this TV** in the lobby. Enter during betting refills a seat below the minimum credit balance. A late join watches until the next round. TV keyboard/mouse/touch controls can **Remove player** or **End TV table**, forfeiting the removed credits and wagers; the next player becomes host. Back to Games preserves the table, while a remote's Stop closes the game view for everyone on that TV. Re-pairing a remote changes its identity; remove any old seat on the TV. Ordinary **Create table** keeps the original shared TV seat controlled by all its remotes.

- Each account has one seat per table. A paired TV without a signed-in session gets its own seat, so two TVs owned by the same person can play together, spending the same coins. A remote panel controls its TV's seat.
- Online tables play for your **coins**, so your balance follows you from table to table. Bet 10–500 in steps of 10, within your balance. For slots this is **per line**, and the displayed total covers all three lines (50 per line costs 150).
- Choose your pick and stake, then **Place bet**. You can update the bet while betting is open. The host can **Start round**, or it starts automatically after 45 seconds when someone has bet. Players joining mid-round watch until betting reopens.
- Blackjack offers **Hit** and **Stand**, with a 30-second hand timer; unfinished hands stand automatically. The dealer stands on all 17s. Naturals pay 3:2, ordinary wins 1:1, ties return the stake. No splitting, doubling or insurance.
- Roulette uses one zero. A number pays 35:1 and an outside bet pays 1:1; zero loses outside bets. The six equally likely horses pay 4:1 for the winner.
- Slots have **three reels and three horizontal paylines**: top, middle, bottom. All three lines play on every spin. Each line returns 24× its line bet for three stars, 12× for another triple, or 1× for a pair. Paying lines light up and display their returns; these are added together. Returns include the original line stake. Columns and diagonals do not pay.
- Spins and races take six seconds, with a shared result; slots give each player their own reels. Results stay for ten seconds before betting reopens. Horse movement is decorative; the server determines the finish.
- Refresh to resume the same seat. Going back to Games keeps your seat for two minutes; after that it's given up, and a bet you'd placed for the next round comes back. **Leave table** removes your seat and forfeits its active wager; your other coins stay yours, and the next player becomes host. Empty tables close immediately; tables untouched for two hours expire and hand back any bets still open. Below the minimum wager (30 coins for three-line slots, ten for other games), use **Refill free coins** between rounds. A previously saved single-line slot round finishes at its original price, then switches to three lines.
- At a table, **◀ ▶ change the pick**, **▲ ▼ change the bet by ten**, and **Enter sets the bet**. The host's **Play/Play-Pause** button starts the round (**P** on a keyboard). In blackjack after dealing, ◀ ▶ choose Hit/Stand and Enter confirms. In the lobby, arrows choose a table and Enter joins. Mouse and touch buttons work too; keyboard Tab reaches individual buttons. The remote shows your table code, balance and round phase.
- Roulette displays a full 37-pocket wheel and a counter-rotating ball. Your pending pick lights the covered pockets; the ball lands in the server's winning pocket when the result arrives. Reduced-motion mode shows the result without spinning.

### Breakout, Snake and Teacup Tower showdowns

**Showdown tables** in Breakout, Snake or Teacup Tower race two players on the same game: the same levels, the same food in the same places, or the same tower. Up to five more can watch both boards live and bet.

- **Start a table**, or choose one in the list. The first two to sit down are **P1** and **P2**; everyone after watches. You can be at one table per game at a time.
- Racers press **Ready to race**. When both have, the race starts and only then is each charged **25 coins**. Nobody can start a race for you.
- A race lasts up to ten minutes. The further run wins: in Breakout each level counts 100 plus every brick; in Snake each point plus 5 for every square of length beyond three; in Teacup Tower the highest floor. If they're level, P1 wins. Each racer is paid their own score, one coin a point, and the winner sits as P1 next time.
- Watchers bet on P1 or P2 in chips of 1, 5, 10, 25 or 100, up to 500 on a race, until it starts. A bet on the winner pays double. If a racer leaves before the race, every bet comes back; if neither racer finishes, so does every bet.
- **Watch instead** gives up your racer seat, and **Take the empty racer seat** fills one. **Leave table** leaves; between races your open bets come back.
- **Close the game and your seat is kept for 30 seconds**, enough for a reload. After that it's let go. A racer gone mid-race forfeits that race.

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
- The remote shows what the screen is playing. It's the bar at the bottom on a computer; on a phone, press **📺** beside the menu at the top and it slides out from the side. It has buttons to pause, skip, change the volume, step the TV's night filter (🌙), switch the film's sound track (🗣) or subtitles (💬, then off), go to the next track or stop. **✕** goes back to playing on your phone.
- **Volume:** a screen has its own volume, used by everything it plays, and it remembers it. Change it from the remote bar (🔉 🔊 🔇), the TV remote's volume keys, **+** and **−** on a keyboard, or the 🔉 🔊 buttons on screen. The level shows for a moment in the top corner.
- **Screen size:** if the TV cuts off the edges of the picture, use **Screens → Screen size** on your phone (on a screen box an admin can also use **Kiosk settings → Screen size…** on the TV). Narrow it until the whole pink frame and the arrows in all four corners show, then **Save**. First check the TV's own picture settings for *Just Scan*, *Screen Fit* or *1:1*: that fixes it without shrinking anything.
- **Text size:** if text and buttons on the TV are too small (or too big) to read from the couch, use **Screens → Text size** on your phone (on a screen box, also **Kiosk settings → Text size…**). **Bigger** and **Smaller** change the TV as you go; **Save** keeps it, **Automatic** goes back to the size picked from the TV's width (bigger on a 4K TV). It applies to every page the TV opens, not only Media.
- **Screens** lists your screens and whether they're on. **Remove** unpairs one.

- **Someone using the TV:** on a screen box, if someone is using another page on the TV, they get 10 seconds to keep it. Your phone says so, then whether it switched or they kept it.

Only you (and admins) can see and control your screens. Screens are only for members of the group your admin chose; without it, there is no **Screens** item in the menu.

## A remote panel

A remote panel is a small touchscreen (the Erynne remote tablet) that controls your screens without a phone. It can play and pause, skip back 10 seconds and forward 30, go to the previous or next track (or TV episode), change the volume, mute, step the night filter, stop, and start a Live TV stream or radio station. It can't browse films or music.

Its **arrows and Enter** work like the TV's own arrow keys while the TV shows its Media page. They move the highlight between tiles and buttons, and **Enter** presses the highlighted one. While a film plays, ◀ ▶ skip back 10 seconds and forward 30, ▲ ▼ change the volume, and Enter plays or pauses. With pictures open, ◀ ▶ go to the previous or next one. When **Up next** shows after an episode, the arrows choose **Play now** or **Cancel** and Enter presses it. While the screen size or text size is being adjusted, they change it and Enter saves it. If the TV is on another page (someone is using it), the arrows do nothing.

**Pair it once with a code,** like a TV: the panel shows a code and a QR code. Scan it, or type the code under **Media → Screens → Pair a TV or remote**, name the panel (for example *Kitchen tablet*) and press **Pair**.

- The panel controls only your screens, and only while you may use screens. It shows each one and what it is playing; tap **Screens** on it to choose another.
- **Screens** on your phone lists your remotes too. **Remove** unpairs one; do that if a panel is lost. The panel's own **Screens → Unpair** does the same from the panel.

**Every panel has a speaker** (the 7" CrowPanel, the M5Stack Tab5 and the small Freenove one), so each pairs as a **speaker** too. One code makes it a remote and one of your screens, marked 🔊. A panel paired before speakers existed stays a plain remote until you **Unpair** it and pair it again. On the CrowPanel, the switch on the back must be set to **MIC&SPK** for sound.
- **Casting to it:** choose it under **Play on this screen** and cast music or a radio station, as you would to a TV. It can't play films, live TV or YouTube, and says so.
- **Controlling it:** from your phone, or from any panel (its own included), with play/pause, skipping, next/previous, volume, mute and stop.
- **What it can reach:** it plays only what is cast to it. It can't browse or fetch anything else.
- **Removing it:** removing the remote removes its speaker as well.

## Your phone as a remote

**Media → Remote** turns your phone into a remote panel, with the same buttons and the same limits: play and pause, back 10 seconds and forward 30, previous and next, the arrows and **Enter**, volume, mute, night filter, stop, **Screens** to choose a TV, and **Channels** to start a Live TV stream or radio station. It takes a seat at game tables the way a panel does.

**Pair it once:** signed in, name it (every phone needs its own name) and press **Pair this phone**. Signed out, it shows a code instead: type that under **Media → Screens → Pair a TV or remote** on any device where you're signed in.

**Install it as its own app:** in your phone's browser, press **☰ → Install TV Remote**, then **Install TV Remote** on the page that opens (on an iPhone, **Share → Add to Home Screen**). It gets its own **Remote** icon and opens straight to the buttons. An iPhone keeps an installed app separate from Safari, so pair it with the code there.

- It stays paired after you sign out, because it has its own key. Anyone holding the unlocked phone can use it to control your screens, and nothing else of yours.
- **Screens → Unpair** (tap it twice) removes it from the phone. If the phone is lost, **Remove** it under **Media → Screens** on another device.
- It counts as one of your ten remotes. Unlike a panel, it doesn't play music cast to it.
- For browsing, or casting films and music, use **Play on** in Media as usual. The **Media** button goes back there.

## Signage on the screens

An admin can give screens playlists to show while nobody is using them (Access → Signage): pictures and clips from the **Signage** folder on the share, PDFs, text, web pages and the announcements for everyone. Casting from your phone always interrupts; what you cast plays, and the signage comes back afterwards.

## Erynne as an app on your phone

Install it from Chrome's **⋮ → Install app** (Android) or Safari's **Share → Add to Home Screen** (iPhone): it then opens like an app and, on Android, appears in the **Share** menu of other apps such as YouTube. See [Erynne on a phone](phone.md).

