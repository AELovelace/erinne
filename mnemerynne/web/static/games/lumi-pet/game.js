/* The companion's controls and presentation. Lumi lives on the account (mnemerynne/lumipet.py): this page only says
   what the person chose (which snack, which toy) and draws what the server answers. Her needs run down while away.

   One file for three apps (mnemerynne/games.py): Lumi Pet (her room: care, pantry, toys, closet, home, shop,
   collection), Lumi Dungeons (dungeon, battle, skills) and Lumi's Garden (garden). The page's tabs say which app this
   is (templates/games/lumi-pet.html), and everything here asks has("dungeon") and the like before offering a thing.
   They are one pet on one save, so all three talk to the same API. */
(function () {
  "use strict";
  const G = window.MGame, R = window.LumiPetRules, H = window.LumiPetHouse, E = window.LumiPetEnemies, Bt = window.LumiPetBattle, W = window.LumiPetCloset, Pk = window.LumiPetPicker, Gd = window.LumiPetGarden, Ga = window.LumiPetGardenArt, Sk = window.LumiPetSkills, Sa = window.LumiPetSkillsArt, Cl = window.LumiPetCollection, Ca = window.LumiPetCollectionArt, Dg = window.LumiPetDungeon, Da = window.LumiPetDungeonArt, Pu = window.LumiPetParty, GT = window.LumiPetGardenTree, GTa = window.LumiPetGardenTreeArt, Ta = window.LumiPetTavernArt, Tw = window.LumiPetTown, TwA = window.LumiPetTownArt, TF = window.LumiPetTownFolk, Ru = window.LumiPetRoom, RA = window.LumiPetRoomArt;
  const $ = function (id) { return document.getElementById(id); };
  const room = $("lp-room"), list = $("lp-list"), houseArt = $("lp-house"), tabButtons = Array.from(document.querySelectorAll("[data-lp-tab]"));
  const API = "/api/media/games/lumi-pet/", OLD_SAVE = "mnemerynne-game:lumi-pet:companion";
  const TABS = tabButtons.map(function (button) { return button.dataset.lpTab; }); // This app's tabs, e.g. Lumi Dungeons: dungeon, battle, skills, settings
  const APP = $("gm").dataset.game, NAME = $("gm").dataset.name; // Which of Lumi's apps this page is ("lumi-pet", "lumi-dungeons", "lumi-garden", "lumi-town") and its name.
  const APPS = { // Lumi's three apps, for the "Open ..." rows in Settings: icon, name, and what is there.
    "lumi-pet": ["✨", "Lumi Pet", "Her room: care, snacks, toys, clothes, her house and the shop"],
    "lumi-dungeons": ["🗝", "Lumi Dungeons", "Dungeon trips, arena fights, skills and mods"],
    "lumi-garden": ["🌸", "Lumi’s Garden", "The Stardust Garden: flowers, starpowder and the press"],
  };
  // What "Back" from a full-room view says where it doesn't lead to her room (goHome): Lumi Dungeons' first tab is the dungeon, and the garden is Lumi's Garden's own first tab.
  const LEAVE = APP === "lumi-dungeons" ? { icon: "🗝", name: "Back to the dungeons" } : APP === "lumi-garden" ? { icon: "⚙", name: "Settings", hint: "Sound, reminders and Lumi's other apps · the garden keeps growing" } : null;
  function has(name) { return TABS.indexOf(name) >= 0; } // Whether this app has that tab.
  // Who's playing? (mnemerynne/lumi_tv.py): on a shared TV whose owner has TV guests, a remote's OK picks whose Lumi
  // is shown. The play pass the server answers is sent with every request, so the whole page is that person's.
  const WHO = $("gm").dataset.who === "1", PLAYER_HEADER = "X-Lumi-Player", PLAYER_KEY = "mnemerynne-lumi-player";
  let player = null, picking = false; // The pass holder ({pass, name, owner, idle, at}); whether the picker is showing.
  // Lumi Dungeons with friends (mnemerynne/lumiparty.py, rows in party-ui.js). `party` is the server's party view, kept
  // live by its stream (or by asking every couple of seconds when a play pass is in use: a stream can't carry its
  // header). On the couch (this TV owns the party) every remote has its own cursor and menu here, by remote id.
  const TV = $("gm").dataset.tv === "1", COUCH_KEY = "mnemerynne-lumi-couch";
  let party = null, partyCode = null, partyFeed = null, openParties = [], partiesAsked = 0, partySending = false, partyNext = null;
  let couchCursor = {}, couchGuess = {}, couchCell = {}, couchConfirm = {}, couchSpot = {}, couchEndArmed = false;
  // The co-op fight (Doll's design, 2026-11-11): the creature this person is aiming at, and the Lumi an item is for
  // (by seat); on the couch, one of each per person. The party browser: which party the cursor is on (null: closed).
  let aim = null, ally = null, couchAim = {}, couchAlly = {}, browsing = null, lastCrew = "", lastTeam = "", lastFightLog = "";
  // Settings → Colour: the red, green and blue being tried (null: the colour drawer is closed). Shown in the room as they move.
  let tint = null;
  const MEMORIES = { cuddle: "A warm little cuddle", snack: "A shared snack", play: "Playtime together", rest: "A quiet moment together" };
  const NEEDS = { hunger: "Tummy rumbling. A snack would help.", energy: "So sleepy. A nap would help.", joy: "A little lonely. Stay and play?" };
  const BUSY = { nap: "napping", play: "playing" };
  const KEYS_HOME = "⏮ ⏭ change tab · ▲ ▼ choose<br>◀ ▶ swap in what she owns · Enter opens the spot, to buy more<br>Keyboard: Page Up / Page Down or [ ]<br>Escape / Back returns to Games";
  const KEYS_DUNGEON = "▲ ▼ ◀ ▶ walk, or work a puzzle<br>⏮ ⏭ choose from this list · Enter does it<br>⏯ drink a potion<br>Keyboard: Page Up / Page Down, P<br>Mouse: press a door, a chest, a card, a lamp or a square";
  const CUDDLES = ["Lumi leans into your hand. A tiny purr, just for you.", "Soft wings wrap around your fingers. We can stay like this a while."];
  // Room mode (room-ui.js, room-art.js; Doll's design, 2026-11-19): Lumi Pet without menus. ROOM is the room's own "tab"
  // (one past the real ones): its rows are the things where she is, and its cursor is where she stands. A thing opens a
  // card: the real tab's rows (roomCard is that thing), with the way back. roomAt is remembered on this device.
  const ROOM = TABS.length, PLACE = $("lp-place").textContent;
  let roomAt = Ru ? Ru.clean(G.store.get("room-at", null)) : null, roomCard = null, lastThings = ""; // (lastThings: what the lower bar last drew)
  // Free walking (see "Free walking" below):
  const SPEED = { x: 260, y: 130 };   // Drawing units a second, across and in depth (the floor is shallow, so depth is slower).
  const holds = {};                   // who → {x, y}: what each keyboard, pad and panel holds right now (common.js, gamepad.js)
  let target = null;                  // A clicked spot {room, x, y} she's walking to, or null
  let walkFrame = 0, walkLast = 0, walkSaved = true, facing = 1; // The loop's frame and clock; whether G.store has her latest place; which way she faces
  function freshPicks() { return TABS.map(function () { return 0; }).concat([0]); } // Every tab's cursor, and the room's.
  let snap = null, offset = 0, tab = 0, picks = freshPicks(), busy = false, visited = false;
  let battleView = null, finished = null, lastFoe = ""; // Which Battle list is open, the fight that just ended, and the enemy last drawn.
  let lastOutfit = "";
  // The dungeon: the puzzle being worked on here, whether arrows drive it ("board") or the list, the finished trip
  // already read, a row waiting for a second press, and what the dungeon scene last drew.
  const KEYS_SKILLS = "▲ ▼ ◀ ▶ move over the skills · Enter learns one<br>Enter on a learned move carries it or puts it away<br>⏮ ⏭ choose from this list<br>Mouse: press a skill to look, again to learn";
  const KEYS_ALBUM = "▲ ▼ ◀ ▶ move over the pictures · ◀ ▶ at an edge turn the page<br>Enter looks at one large<br>⏮ ⏭ choose from this list<br>Mouse: press a shelf, a picture, or a page arrow";
  let skillCursor = null, skillFocus = "board", lastSkills = ""; // The Skills tab's cursor.
  let album = { shelf: null, page: 0, cursor: 0 }, albumZoom = false, albumFocus = "board", lastAlbum = ""; // The Collection tab: which shelf, page and picture, and whether one is shown large.
  const KEYS_GARDEN = "▲ ▼ ◀ ▶ move over the plots · Enter plants or trims<br>⏯ presses the powder · ⏮ ⏭ choose from this list<br>Keyboard: P presses, Page Up / Page Down<br>Mouse: press a plot, the press, or a watering can";
  const CAN_NOTES = [523.25, 659.25, 783.99, 440]; // One note per watering can.
  let lumiMoved = 0, lumiActUntil = 0, lumiFlip = false; // Lumi in the garden: when she last darted somewhere, and the little move she is making.
  let drawer = null, seed = null, plotCursor = 0, gardenFocus = "list", pending = 0, mini = null, miniTimer = null, lastGarden = "", gardenOpen = false; // The garden: see drawGarden.
  const KEYS_GARDEN_PAGE = "▲ ▼ ◀ ▶ move over the page · Enter unlocks, chooses or levels up<br>⏯ presses the powder · ⏮ ⏭ choose from this list<br>Mouse: press something to look, again to choose";
  const pageCursor = {}; // The garden's pages (the Flower tree, Helpers, Moonrise): where each one's cursor was left.
  let play = {}, focus = "list", lastStory = "", caring = false, dismissedRun = G.store.get("read-run", null), pendingConfirm = null, lastDungeon = ""; // (The last trip's ending is read once per device: Lumi Dungeons opens on its Dungeon tab, and shouldn't open on old news.)
  // Home and Closet: the spot or slot whose full list is open (null: one row each), the choice ◀ ▶ has flipped each
  // row to, and what the room last drew.
  let opened = { home: null, closet: null }, flip = {}, lastHouse = "";
  let effectUntil = 0, lastReport = "", lastList = "", lastKeys = "", lastDone = null, timer = null, polled = 0, reminders = "unsupported";
  // Lumi Town (town-ui.js, town-art.js; Doll's design, 2026-11-12): the map behind her room's door. `townAt` is the place
  // she's inside (the Arcade, the Casino or the Lumi-Soft field), or null on the map; the list's cursor is where she
  // stands. The page opens where ?at= says (media.js sends it when a game opened from here closes), and remembers on
  // this device which cabinet or table each place's cursor was on.
  const TOWN = APP === "lumi-town", KEYS_TOWN = "▲ ▼ ◀ ▶ walk to a place · ▶ off the end: Market Street · Enter goes in<br>Inside: ▲ ▼ choose a game or a stall · Enter plays or buys<br>Mouse: press a place, a cabinet, a stall or a signpost<br>Escape / Back returns";
  const arrival = TOWN ? Tw.arrive(new URLSearchParams(location.search).get("at")) : null, townPicks = TOWN ? G.store.get("town-picks", {}) : {};
  // Market Street (2026-11-14): which street's map she's on, and in the Market, the stall she's at (and, at Garden things,
  // which of the garden's decoration drawers is open: garden-ui.js). RETRO_ON: Media → Retro is on, so the café is open.
  let townAt = arrival ? arrival.inside : null, townStreet = arrival ? arrival.street : "high", marketStall = null, marketDrawer = null;
  const RETRO_ON = $("gm").dataset.retro === "1";
  if (arrival) picks[0] = townAt ? Math.max(0, Math.min(Number(townPicks[townAt]) || 0, Tw.place(townAt).games.length - 1)) : arrival.cursor; // Back from a game: on its cabinet.
  const DESK = !TV && window.matchMedia("(any-pointer: fine)").matches; // A mouse and keyboard are here: Lumi-Soft can be played (media.js offers it only there too).

  function serverNow() { return Date.now() + offset; } // The server's clock, so timers agree across devices.

  // ---- Drawing other Lumis (her friends in a party, the party browser): her own shapes from the page, in their
  // colours and clothes. The template's Lumi is copied once with its ids taken off and its two clothing layers marked.
  const LUMI_SHAPES = (function () {
    const copy = $("lp-pet").cloneNode(true);
    copy.querySelectorAll("title").forEach(function (node) { node.remove(); });
    copy.querySelector("#lp-acc-back").setAttribute("data-slot", "back");
    copy.querySelector("#lp-acc-front").setAttribute("data-slot", "front");
    copy.querySelectorAll("[id]").forEach(function (node) { node.removeAttribute("id"); });
    return copy.innerHTML;
  })();

  function lumiStyle(look) { // Her three colours as the CSS variables her shapes use (game.css .lp-body and friends).
    const c = R.looks(look.glow, look.colour);
    return "--pet-glow:" + c.glow + ";--pet-body:" + c.body + ";--pet-shadow:" + c.shadow;
  }

  function lumiMarkup(look) { // One Lumi, as SVG markup for a 320 x 300 box: her shapes, her clothes, her colours. All of it authored here or in closet.js.
    const outfit = look.outfit || {};
    return '<g style="' + lumiStyle(look) + '">' + LUMI_SHAPES.replace('<g data-slot="back"></g>', "<g>" + W.render(outfit, "back") + "</g>")
      .replace('<g data-slot="front"></g>', "<g>" + W.render(outfit, "front") + "</g>") + "</g>";
  }

  function openApp(slug, where) { // Go to another of Lumi's apps (or, from Lumi Town, a game). Inside Media's game layer the layer swaps the game in its frame (media.js), so its bar and remote keys follow; on its own the page just goes there.
    where = where || {}; // at: where that page starts (Lumi Town: a place); back: the town place to come back to when it closes (media.js games.back).
    if (framed) window.parent.postMessage({ type: "mnemerynne-game-open", slug: slug, at: where.at || null, back: where.back || null }, location.origin);
    else location.href = slug === "retro" ? "/media/retro" : "/media/games/" + slug + (where.at ? "?at=" + encodeURIComponent(where.at) : ""); // On its own the browser's Back is the way back (drawTown keeps the town's address current). Retro is Media's own page.
  }

  function adventure() { // Her room's door (Doll's design, 2026-11-11; Lumi Town 2026-11-12): she trots off through it, out into Lumi Town.
    if (room.dataset.leaving === "true") return; // Already on her way: one press is enough.
    room.dataset.leaving = "true"; // game.css: she trots towards the door and fades.
    say("Off into town! 🏘");
    G.sound.unlock(); [523.25, 659.25, 783.99].forEach(function (f, i) { G.sound.tone(f, 0.14, i * 0.09, 0.18); }); // Three little steps up: off she goes.
    setTimeout(function () { openApp("lumi-town", { at: "home" }); }, 700); // Long enough to see her go. She comes out in front of her own house.
    setTimeout(function () { delete room.dataset.leaving; }, 2500); // Back in place if this page is still showing (a refused swap, or the browser's Back).
  }

  // ---- Lumi Town: the map and the places on it (town-ui.js has the words and the lists, town-art.js the drawings).
  const STALL_HELLO = { store: "Everything a little Lumi could want~", capsules: "Turn the handle... what's inside?", seeds: "Every seed here grows in the Stardust Garden.",
                        garden: "Lanterns, planters and pretty things for the garden." };

  function townGo(change) { // A town row that stays in the town: walk into a place or out, round the corner to the other street, or up to a stall.
    if (change.street) { // Off the end of the street, round the corner: she comes out by its first place, or back by the Casino.
      townStreet = Tw.STREETS[change.street] ? change.street : "high"; townAt = null; marketStall = null; marketDrawer = null;
      picks[tab] = townStreet === "market" ? 0 : Tw.index("casino");
      say(townStreet === "market" ? "Market Street! The Retro café and the Market~" : "Back on the High Street. Where next?");
      townHere();
      return;
    }
    if ("stall" in change) { // In the Market: up to a stall, or back from it to the row of stalls.
      const market = Tw.inside(townAt);
      if (!market) return;
      if (change.stall) { marketStall = change.stall; marketDrawer = null; picks[tab] = 0; say(STALL_HELLO[change.stall] || ""); }
      else { picks[tab] = Math.max(0, market.games.findIndex(function (g) { return g.stall === marketStall; })); marketStall = null; marketDrawer = null; }
      return;
    }
    if (change.leave) { const was = townAt; townAt = null; marketStall = null; marketDrawer = null; picks[tab] = Tw.index(was); say("Back out in Lumi Town. Where next?"); townHere(); return; }
    const place = Tw.place(change.enter);
    if (!place) return;
    picks[tab] = Tw.index(place.id); // She walks up to it first.
    if (place.retro) { townOpen("retro", place.id, place.going); return; } // The Retro café: Media → Retro, and back out in front of the café.
    if (place.app) { townOpen(place.app, place.home ? null : place.id, place.going); return; } // Her house, the tavern, the garden: Lumi's own apps. Her house is home: leaving it doesn't come back here.
    townAt = place.id; marketStall = null; marketDrawer = null;
    townHere();
    picks[tab] = Math.max(0, Math.min(Number(townPicks[place.id]) || 0, place.games.length - 1)); // On the cabinet she last played here.
    say(place.hello);
    G.sound.unlock(); G.sound.tone(659.25, .12, 0, .12); G.sound.tone(880, .16, .1, .1); // The shop bell.
  }

  function townOpen(slug, back, words) { // Off to a game or one of Lumi's apps: she hops to the door and it opens. Leaving it walks back to `back` (a place), or nowhere (her house).
    if (room.dataset.leaving === "true") return; // One press is enough.
    if (townAt) { townPicks[townAt] = picks[tab]; G.store.set("town-picks", townPicks); } // Coming back puts her on the same cabinet.
    room.dataset.leaving = "true"; // game.css: a little hop, and she's gone in.
    say(words || "Let's play! ✨");
    G.sound.unlock(); [587.33, 739.99, 880].forEach(function (f, i) { G.sound.tone(f, 0.12, i * 0.08, 0.16); });
    setTimeout(function () { openApp(slug, back ? { back: back } : {}); }, 600);
    setTimeout(function () { delete room.dataset.leaving; }, 2500); // Still here (a refused swap, or the browser's Back): she's back on the map.
  }

  function townMove(action) { // The arrows in the town. On the map ◀ ▶ walk along a row and ▲ ▼ cross to the other row; ▶ off the High Street's
    // right-hand end goes round the corner to Market Street, and ◀ off its left-hand end comes back. Inside, every arrow goes along the line.
    const count = rows().length, by = action === "left" || action === "up" || action === "prev" ? -1 : 1, at = picks[tab];
    if (!count) return;
    if (!townAt) {
      const places = Tw.street(townStreet).length;
      if (townStreet === "high" && action === "right" && at < places && at % 3 === 2) { townGo({ street: "market" }); render(); return; }
      if (townStreet === "market" && action === "left" && at === 0) { townGo({ street: "high" }); render(); return; }
      if ((action === "up" || action === "down") && townStreet === "high" && at < 6) { picks[tab] = (at + 3) % 6; render(); return; } // Two rows of three: straight across the street.
    }
    picks[tab] = (at + by + count) % count;
    render();
  }

  function townPress(event) { // Mouse and touch in Lumi Town: a place on the map, a cabinet or table inside, or the exit mat. It does what its row does.
    const hit = event.target.closest("[data-click]");
    if (!TOWN || !hit || !snap) return;
    const what = hit.dataset.click.split(":"), key = { place: "place-", game: "game-", stall: "stall-", street: "street-" }[what[0]];
    const at = rows().findIndex(function (row) { return row.key === (key ? key + what[1] : what[0]); });
    if (at < 0) return;
    picks[tab] = at; choose();
  }

  function drawTown() { // Lumi Town: a street's map or a place's inside (town-art.js), with Lumi standing where the cursor is.
    const here = Tw.inside(townAt), places = Tw.street(townStreet), way = Tw.STREETS[townStreet], night = sceneNow().night;
    const onPlace = places[picks[tab]] || null; // On a map, the walk and "Show me in town" rows stand her by the signpost.
    const cursor = here && marketStall ? Math.max(0, here.games.findIndex(function (g) { return g.stall === marketStall; })) // at a stall: she stays at it
      : here ? picks[tab] : onPlace ? onPlace.id : "street:" + way.other;
    const at = TwA.spot(townAt, cursor, here ? here.games.length : 0);
    if (!room.dataset.town) { room.dataset.townStill = "true"; requestAnimationFrame(function () { requestAnimationFrame(function () { delete room.dataset.townStill; }); }); } // The first draw: she's simply there (game.css), and walks from then on.
    room.dataset.town = here ? here.id : "map"; room.dataset.street = townStreet; room.dataset.open = "false"; // No windows onto the page's scenery: the town paints its own sky.
    room.style.setProperty("--gx", String(at.x - 500)); room.style.setProperty("--gy", String(at.y)); // game.css walks her there.
    $("lp-place").textContent = here ? here.room : townStreet === "market" ? "MARKET STREET" : "LUMI TOWN";
    const signature = JSON.stringify(["town", townStreet, townAt, cursor, night]);
    if (signature !== lastHouse) { // Only redrawn when she moves, goes in or out, changes street, or day turns to night.
      lastHouse = signature;
      houseArt.innerHTML = TwA.scene({ street: townStreet, at: townAt, cursor: cursor, games: here ? here.games.map(function (g) { return g.slug || g.stall; }) : [],
        label: here ? "" : onPlace ? onPlace.name : way.walk, night: night }); // Markup authored in town-art.js; the label is escaped there.
      try { history.replaceState(null, "", location.pathname + "?at=" + (townAt || (onPlace || places[0]).id)); } catch (e) { /* the address is a nicety */ } // A reload, or the browser's Back from a game, lands here again.
    }
    drawFolk();
  }

  // ---- Shopping at the Market (2026-11-14): Lumi Pet's and Lumi's Garden's own lists, in town (Doll: "shop right in town").
  function shopItems() { // Snacks, treats and toys for sale: the Shop tab, and the Market's general store.
    const pet = snap.pet, c = snap.catalog;
    return c.snacks.concat(c.treats, c.toys).filter(function (thing) { return thing.price && !pet.toys.includes(thing.id); }).map(function (thing) {
      const held = pet.inventory[thing.id] || 0, toy = c.toys.includes(thing);
      return { key: thing.id, icon: thing.icon, name: thing.name, send: { action: "buy", item: thing.id },
        hint: thing.price + " coins" + (toy ? " · a toy to keep" : held ? " · have " + held : "") };
    });
  }

  function powderRow() { // What the garden's things cost: starpowder from Lumi's Garden.
    return { key: "powder", icon: "✦", name: Gd.format(snap.garden.powder) + " starpowder", hint: "From the Stardust Garden's press: seeds and garden things cost it",
      note: "Starpowder comes from Lumi's Garden: trim flowers and use the press." };
  }

  function seedRows() { // The seed stall: every flower not growing in this garden yet, the ones she can unlock first (garden-tree.js says why not).
    const garden = snap.garden, level = snap.pet.level;
    const left = garden.flowers.filter(function (flower) { return GT.state(garden, flower, level) !== "unlocked"; });
    left.sort(function (a, b) { return (GT.state(garden, a, level) === "open" ? 0 : 1) - (GT.state(garden, b, level) === "open" ? 0 : 1); });
    if (!left.length) return [powderRow(), { key: "all-seeds", icon: "🌸", name: "Every seed is growing", hint: "This garden has them all", note: "This garden already grows every flower." }];
    return [powderRow()].concat(left.map(function (flower) {
      const row = { key: "seed-" + flower.id, icon: "🌱", name: flower.name, hint: GT.cost(flower) + " · " + GT.about(flower) };
      if (GT.state(garden, flower, level) === "open") { row.url = "garden"; row.send = { action: "unlock", item: flower.id }; }
      else { row.icon = "🔒"; row.note = GT.why(garden, flower, level); }
      return row;
    }));
  }

  function stallRows(id) { // What one of the Market's stalls sells.
    const garden = snap.garden, decor = snap.garden_decor;
    if (id === "store") return shopItems();
    if (id === "capsules") { // The Star and Moon Capsules (coins), and the Seedling Capsule (starpowder), as their own shops offer them.
      const seedling = Gd.rows({ garden: garden, decor: decor, drawer: "decor", go: function () {} }).find(function (row) { return row.key === "capsule"; });
      return Pk.capsuleRows(snap).concat(seedling ? [Object.assign({}, seedling, { key: "capsule-seed" })] : []);
    }
    if (exploring()) return [{ key: "away", icon: "🗺", name: "Lumi is out exploring", hint: "The garden's stalls wait until she's home", note: "Lumi is out exploring. The garden waits for her, and so does this stall." }];
    if (id === "seeds") return seedRows();
    if (id === "garden") { // The garden's decorations: its slots, a slot's pieces and the store of spares (garden-ui.js), without the garden's own capsule and Back.
      const drawer = marketDrawer || "decor";
      const got = Gd.rows({ garden: garden, decor: decor, drawer: drawer, go: function (change) { marketDrawer = change.drawer || null; } });
      return [powderRow()].concat(drawer === "decor" ? got.filter(function (row) { return row.key !== "back" && row.key !== "capsule"; }) : got);
    }
    return [];
  }

  // ---- Lumi Town's townsfolk, and everyone else's Lumis who are in town now (town-folk.js, mnemerynne/lumitown.py), on
  // their own layer over the town picture (#lp-folk), moved by `transform` only: the picture isn't redrawn for them.
  const folkArt = $("lp-folk"), greeted = new Set(), strolls = {};
  let visitors = [], townPolled = 0, folkSig = "", folkSay = Date.now() + 6000;

  function townHere() { // Tell the server where she is (unless "Show me in town" is off) and hear who else is in town.
    if (!TOWN || !snap) return;
    townPolled = Date.now();
    const places = Tw.street(townStreet), at = townAt || (places[picks[tab]] || places[0]).id;
    G.api(API + "town", { at: at, street: townStreet }).then(function (data) {
      visitors = (data.here || []).filter(function (v) { return v && v.look && Tw.place(v.at); });
      visitors.forEach(function (v) { // A hello, once a visit, for someone on this street.
        if (greeted.has(v.id) || Tw.place(v.at).street !== townStreet) return;
        greeted.add(v.id);
        say(v.name + "'s " + (v.look.name || "Lumi") + " is in town! 👋");
      });
      drawFolk();
    }).catch(function () { /* the town is fine on its own */ });
  }

  function folkFigure(id, look, kind, words) { // One Lumi on the layer: her shapes in her look (lumiMarkup), a name tag, and a speech line.
    const ns = "http://www.w3.org/2000/svg", g = document.createElementNS(ns, "g"), flip = document.createElementNS(ns, "g");
    g.setAttribute("class", "lp-folk-one " + kind); g.dataset.id = id;
    flip.setAttribute("class", "lp-folk-flip");
    flip.innerHTML = '<svg x="-24" y="-45" width="48" height="45" viewBox="0 0 320 300" overflow="visible">' + lumiMarkup(look) + "</svg>"; // Authored shapes; the look is the server's checked one.
    g.append(flip);
    if (words) { const tag = document.createElementNS(ns, "text"); tag.setAttribute("class", "lp-folk-name"); tag.setAttribute("y", "13"); tag.textContent = words; g.append(tag); } // A person's own words: text only.
    const said = document.createElementNS(ns, "text"); said.setAttribute("class", "lp-folk-say"); said.setAttribute("y", "-52"); g.append(said);
    return g;
  }

  function folkPlace(g, x, y, facingLeft) { g.style.transform = "translate(" + x + "px, " + y + "px)"; g.firstChild.dataset.flip = facingLeft ? "true" : "false"; }

  function drawFolk() { // Who's out: the townsfolk on this street's map, and visitors on it (or in the place she's in). Rebuilt only when that changes.
    if (!TOWN || !folkArt) return;
    const night = sceneNow().night, here = townAt, npcs = here ? [] : TF.folkOn(townStreet, night);
    const guests = visitors.filter(function (v) { return here ? v.at === here : Tw.place(v.at).street === townStreet; });
    const signature = JSON.stringify([townStreet, here, night, npcs.map(function (n) { return n.id; }), guests.map(function (v) { return [v.id, v.at, v.name, v.look]; })]);
    if (signature === folkSig) return;
    folkSig = signature;
    folkArt.replaceChildren();
    npcs.forEach(function (n, i) {
      const s = strolls[n.id] || (strolls[n.id] = { x: TF.start(n, i), until: 0 }), g = folkFigure("npc-" + n.id, n.look, "lp-folk-npc", "");
      folkArt.append(g); folkPlace(g, s.x, TF.y(n), false);
    });
    const crowd = {};
    guests.forEach(function (v) { // Visitors stand by the place they're at (inside: by the door), side by side.
      const n = crowd[v.at] = (crowd[v.at] || 0) + 1, base = here ? { x: 500, y: 474 } : TwA.spot(null, v.at), side = n % 2 ? 1 : -1;
      const x = Math.max(TF.WALK_MIN, Math.min(TF.WALK_MAX, base.x + side * (34 + 30 * Math.floor((n - 1) / 2))));
      const g = folkFigure("guest-" + v.id, v.look, "lp-folk-guest", (v.look.name || "Lumi") + " · " + v.name);
      folkArt.append(g); folkPlace(g, x, base.y, side < 0);
    });
  }

  function folkWander() { // Once a second in town: a townsfolk Lumi whose pause is over strolls on, and now and then one says something.
    if (!TOWN || townAt || G.reducedMotion || !folkArt) return;
    const now = Date.now(), night = sceneNow().night, out = TF.folkOn(townStreet, night);
    out.forEach(function (n, i) {
      const s = strolls[n.id] || (strolls[n.id] = { x: TF.start(n, i), until: 0 });
      if (now < s.until) return;
      const to = TF.next(s.x, [Math.random(), Math.random()]), seconds = Math.abs(to - s.x) / 35, g = folkArt.querySelector('[data-id="npc-' + n.id + '"]');
      s.until = now + seconds * 1000 + TF.pause(Math.random()); // A stroll at about 35 units a second, then a while standing about.
      if (g) { g.style.transitionDuration = seconds.toFixed(1) + "s"; folkPlace(g, to, TF.y(n), to < s.x); }
      s.x = to;
    });
    if (now < folkSay || !out.length) return;
    folkSay = now + 9000 + Math.random() * 9000;
    const who = out[Math.floor(Math.random() * out.length)], words = folkArt.querySelector('[data-id="npc-' + who.id + '"] .lp-folk-say');
    if (!words) return;
    words.textContent = TF.line(townStreet, night, Math.random());
    words.parentNode.classList.add("saying");
    setTimeout(function () { words.parentNode.classList.remove("saying"); }, 4200);
  }

  function welcomeHome() { // Lumi Pet, after a dungeon trip: her welcome-home line, once per trip on this device (dungeon-ui.js welcome).
    const run = snap && snap.dungeon ? snap.dungeon.run : null;
    if (APP !== "lumi-pet" || !run || G.store.get("welcomed-run", null) === run.id) return;
    const line = Dg.welcome(run, serverNow());
    if (!line) return; // Not over, or too old to be news.
    G.store.set("welcomed-run", run.id);
    say(line); effect("cuddle", "✨ ♡");
  }

  function doorPress(event) { // Mouse and touch on her room's door (house.js marks it data-click="door").
    const hit = event.target.closest("[data-click]");
    if (!hit || hit.dataset.click !== "door" || !snap || APP !== "lumi-pet") return;
    if (tint || opened.home || opened.closet) return; // Choosing a colour or trying pieces on: the room is a preview just now.
    adventure();
  }

  function goHome() { // "Back" from a full-room view (the garden, the trees, the gallery): this app's first tab, or its Settings when that view is the first tab.
    if (roomCard) { closeCard(); return; } // Room mode: back to the room the card was opened in.
    changeTab(tab === 0 ? Math.max(0, TABS.indexOf("settings")) : 0); picks[tab] = 0;
  }

  function relabel(list) { // The views' own "Back to Lumi" row, in this app's words (LEAVE).
    return !LEAVE ? list : list.map(function (row) { return row.key === "leave" ? Object.assign({}, row, LEAVE) : row; });
  }

  function awayRows() { // Lumi Pet and Lumi's Garden while she is on a dungeon trip or in a fight: she is in Lumi Dungeons, and this row goes to her.
    const fighting = snap.fight && snap.fight.status === "active";
    if (has("dungeon") || !(exploring() || fighting)) return [];
    return [{ key: "away", icon: "🗝", name: exploring() ? "Lumi is out exploring" : "Lumi is in a fight", hint: "Open Lumi Dungeons to be with her" + (exploring() ? " and bring her home" : ""), local: function () { openApp("lumi-dungeons"); } }];
  }

  function take(data) { // Keep the server's answer and how far this device's clock is from the server's.
    snap = data; offset = data.now - Date.now(); polled = Date.now();
    $("lp-coins").textContent = data.coins.toLocaleString();
    syncParty();
  }

  function say(text) { // The live region announces it to screen readers too; in room mode it's also her speech bubble.
    $("lp-message").textContent = text;
    const bubble = $("lp-guide");
    if (bubble) bubble.textContent = text;
  }

  function effect(action, symbol) { // A brief expression in the room after an interaction.
    room.dataset.action = action; $("lp-effect").textContent = symbol; effectUntil = Date.now() + 4000;
    G.sound.unlock();
    G.sound.tone(action === "rest" ? 392 : 659.25, .22, 0, .09);
    G.sound.tone(action === "rest" ? 293.66 : 880, .3, .14, .06);
  }

  function item(id) { // Look an item up in the server's catalog.
    const c = snap.catalog;
    return c.snacks.concat(c.treats, c.toys).find(function (entry) { return entry.id === id; });
  }

  function sceneNow() { // Night follows this device's clock; naps and wakes come from the server, shifted onto this clock.
    return R.scene({ rest: snap.pet.rest, restUntil: snap.pet.restUntil - offset }, Date.now());
  }

  function mendsIn() { // Milliseconds until her health is full, counted down from the server's answer; a nap halves it.
    const napping = snap.pet.task && snap.pet.task.kind === "nap";
    return Math.max(0, snap.health.mends_in / (napping ? snap.battle.rules.nap_mend : 1) - (serverNow() - snap.now));
  }

  function rows() { // What is on offer now, as plain rows: the list and the remote both read from here.
    if (tab === ROOM) return Ru.rows(roomAt, { open: openCard, walk: roomWalk, door: adventure }); // Room mode: the things where she is.
    const list = tabRows();
    return roomCard ? Ru.cardRows(list, roomCard, closeCard, Boolean(opened.home || (tint && tint.spot))) : list; // A card: the tab's rows (some of them), then back to the room.
  }

  function tabRows() { // What the selected tab offers.
    const pet = snap.pet, c = snap.catalog, name = TABS[tab], asleep = sceneNow().asleep;
    if (name === "town") return Tw.rows({ at: townAt, street: townStreet, desk: DESK, away: Boolean(exploring()), retro: RETRO_ON, seen: pet.town_seen !== false,
      stall: marketStall, stallRows: stallRows, go: townGo, open: townOpen }); // Lumi Town: a street's places, the games inside one, or a Market stall's wares.
    if (name === "care") {
      if (awayRows().length) return awayRows(); // She isn't in her room: nothing to cuddle until she is back (the Pantry can still send her a snack).
      const health = snap.health, sore = !health.out && health.hp < health.max; // Hurt, and at home to do something about it.
      const list = [
        { key: "cuddle", icon: "♡", name: "Cuddle", hint: "A little closeness", send: { action: "cuddle" } },
        asleep ? { key: "wake", icon: "☀", name: "Wake gently", hint: "A sleepy little hello", send: { action: "wake" } }
          : { key: "nap", icon: "☾", name: "Nap", hint: "Five minutes of star dreams · +35 energy" + (sore ? " · mends her twice as fast" : ""), send: { action: "nap" } },
      ];
      if (sore) { // A potion from her own pocket; bought food from the Pantry mends her too.
        const potion = { key: "drink", icon: "🧪", name: "Drink a potion (" + pet.potions + ")", hint: health.hp + " of " + health.max + " HP · whole again in " + R.countdown(mendsIn()) + ", sooner with a nap or bought food" };
        if (pet.potions) potion.send = { action: "drink" }; else potion.note = "No potions left. Lumi Dungeons sells them, on its Battle tab.";
        list.push(potion);
      }
      list.push({ key: "adventure", icon: "🏘", name: "Through the door", hint: "Out into Lumi Town: the tavern, the arcade, the casino, the garden and the Lumi-Soft field", local: adventure }); // Her room's door as a row, for remotes, keyboards and phones too narrow to show it.
      return list;
    }
    if (name === "pantry") return c.snacks.concat(c.treats).filter(function (food) { return !food.price || pet.inventory[food.id]; }).map(function (food) {
      return { key: food.id, icon: food.icon, name: food.name, send: { action: "feed", item: food.id },
        hint: R.signed(food.fullness, "hunger") + " · " + R.signed(food.joy, "joy") + (food.price && food.fullness > 0 ? " · +" + food.fullness + "% health" : "") + " · " + (food.price ? pet.inventory[food.id] + " left" : "always in the bowl") };
    });
    if (name === "toys") return c.toys.filter(function (toy) { return (!toy.price && !toy.rarity) || pet.toys.includes(toy.id); }).map(function (toy) { // Free toys, and ones bought or won from a capsule.
      return { key: toy.id, icon: toy.icon, name: toy.name, send: { action: "play", item: toy.id },
        hint: (toy.rarity ? toy.rarity[0].toUpperCase() + toy.rarity.slice(1) + " · " : "") + toy.minutes + " min · " + R.signed(toy.joy, "joy") + " · " + R.signed(toy.energy, "energy") };
    });
    if (name === "home" && tint && tint.spot) return tintRows(); // Painting a wall or floor: the colour sliders.
    if (name === "closet" || name === "home") { // One row per place on Lumi, or per spot in the house; ◀ ▶ flip its choices (picker-ui.js).
      const ctx = { snap: snap, open: opened[name], flip: flip, paint: openPaint, go: function (change) {
        if ("open" in change) { opened[name] = change.open; picks[tab] = change.open ? 1 : (change.pick || 0); }
      } };
      return name === "home" ? Pk.homeRows(ctx) : Pk.closetRows(ctx);
    }
    if (name === "battle") return battleRows();
    if (name === "skills") return relabel(Sk.rows({ snap: snap, cursor: skillCursor || snap.battle.trees[0].nodes[0].id, go: skillsGo }));
    if (name === "collection") return relabel(Cl.rows({ snap: snap, at: album, zoom: albumZoom, go: albumGo }));
    if (name === "garden" && exploring()) return awayRows(); // Lumi's Garden during a dungeon trip: the garden grows without her until she is home (lumipet.garden_act).
    if (name === "garden" && paging()) return relabel(GT.rows(drawer, pageCtx())); // The Flower tree, Helpers or Moonrise page.
    if (name === "garden") return relabel(Gd.rows({ garden: snap.garden, decor: snap.garden_decor, drawer: drawer, seed: seed, cursor: plotCursor, mini: mini, passed: passed(), pending: pending, level: pet.level, go: gardenGo }));
    if (name === "dungeon") { // A dungeon fight and its result use the Battle lists; everything else is the dungeon's own.
      if (couching()) return []; // The couch draws one menu per person instead (drawCouch).
      if ((snap.fight && snap.fight.status === "active") || finished) return battleRows();
      const home = inParty() ? null : function () { openApp("lumi-town", { at: "tavern" }); }; // "Back to town", out the tavern's front door (not from a party: leaving the page would leave her friends).
      const own = Dg.rows({ snap: snap, play: play, dismissed: dismissedRun, care: caring, go: dungeonGo, home: home });
      if (inParty() && party.phase === "lobby") return Pu.lobbyRows(party, partyAct);
      if (inParty() && party.run) return Pu.tripRows(party, own, serverNow(), fightCtx(aim, ally));
      if (!exploring() && !(snap.dungeon.run && snap.dungeon.run.over && dismissedRun !== snap.dungeon.run.id)) { // Choosing where to go: or going with friends.
        askParties();
        if (browsing) return Pu.browserRows(openParties, { at: browsing.at, back: function () { browsing = null; picks[tab] = 0; }, askCode: TV ? null : askCode });
        return (home ? [Dg.homeRow(home)] : []).concat(Pu.startRows({ open: openParties, tv: TV, askCode: askCode, browse: browse }), own); // The way back to town first, then friends, then places.
      }
      return own;
    }
    if (name === "shop") return Pk.capsuleRows(snap).concat(shopItems()); // The capsules first: this is where people look to buy one.
    if (tint) return tintRows(); // The colour drawer: red, green and blue, then keep it or go back.
    const settings = []; // Settings: each app lists what is its own, then the way to Lumi's other apps.
    settings.push({ key: "name", icon: "✎", name: "Name: " + pet.name, hint: "What she's called, and what her friends see in a party", local: askName });
    settings.push({ key: "colour", icon: "🎨", name: "Colour: " + (pet.colour ? pet.colour.toUpperCase() : "her glow's own"), hint: "Red, green and blue sliders for her very own colour", local: openTint });
    if (APP !== "lumi-garden") settings.push({ key: "glow", icon: "✦", name: "Glow: " + pet.glow[0].toUpperCase() + pet.glow.slice(1), hint: "◀ ▶ her colour, and her element in a fight: " + snap.battle.rules.glows[pet.glow], send: { action: "glow", glow: R.nextPalette(pet.glow) },
      turn: function (by) { submit({ key: "glow", send: { action: "glow", glow: Pk.turn(R.PALETTES, pet.glow, by) } }); } });
    if (has("dungeon")) settings.push({ key: "storyteller", icon: "✎", name: "Storyteller: " + (pet.storyteller === "live" ? "Live" : "Library"), hint: "Who writes the dungeon's lines: the game's library, or a model as you go", send: { action: "storyteller" } });
    settings.push({ key: "sound", icon: "♪", name: "Sound: " + (G.sound.isOn() ? "on" : "off"), hint: "Little chimes", local: function () { G.sound.toggle(); } });
    if (APP === "lumi-pet") settings.push({ key: "town-seen", icon: "👀", name: "Show me in Lumi Town: " + (pet.town_seen === false ? "off" : "on"), // Also on the town's own map.
      hint: pet.town_seen === false ? "Nobody sees your Lumi in town; you still see theirs" : "Others in Lumi Town see your Lumi there, in her clothes, with her name", send: { action: "town_seen" } });
    if (APP === "lumi-pet") settings.push({ key: "room-mode", icon: "🏠", name: "Room mode: " + (pet.room_mode === false ? "off" : "on"), // Walk her flat and press things, or these lists.
      hint: pet.room_mode === false ? "Lists, as before · on: walk Lumi's flat and press the fridge, the bed, the wardrobe…" : "Walk Lumi's flat and press things · off: just the lists", send: { action: "room_mode" } });
    if (WHO) settings.push({ key: "switch-player", icon: "⇄", name: "Switch player", hint: "Playing " + (player ? player.name + "'s" : "this TV's own") + " Lumi · someone else presses OK on their remote", local: function () { forgetPlayer("Who's playing next?"); } }); // A shared TV: hand it to the next person.
    if (reminders !== "unsupported" && !has("dungeon")) { // Phones and computers only: a TV has nobody's pocket to buzz. Her needs and her flowers send them; the dungeons never do.
      if (has("garden")) settings.push({ key: "garden-reminders", icon: "❀", name: "Garden reminders: " + (pet.garden_reminders === false ? "off" : "on"), hint: "A nudge when slow flowers are ready to trim (needs Reminders on)", send: { action: "garden_reminders" } });
      settings.push({ key: "reminders", icon: "✉", name: "Reminders: " + reminders, local: toggleReminders,
        hint: reminders === "blocked" ? "Allow notifications for this site in your browser first" : "A nudge on this device when Lumi needs you" });
    }
    Object.keys(APPS).forEach(function (slug) { // Lumi's other two apps.
      if (slug !== APP) settings.push({ key: "open-" + slug, icon: APPS[slug][0], name: "Open " + APPS[slug][1], hint: APPS[slug][2], local: function () { openApp(slug); } });
    });
    if (APP === "lumi-dungeons" || APP === "lumi-garden") settings.push({ key: "town", icon: "🏘", name: "Back to town", hint: "Lumi Town: her house, the arcade, the casino and the Lumi-Soft field", // Out of the tavern's door, or the garden's gate.
      local: function () { openApp("lumi-town", { at: APP === "lumi-garden" ? "garden" : "tavern" }); } });
    if (!inApp && !framed) settings.push({ key: "install", icon: "⬇", name: "Install " + NAME, hint: "Its own icon on your home screen, opening straight here", local: installApp }); // In a browser tab: each of Lumi's apps can be an app of its own.
    return settings;
  }

  // ---- Room mode: walking her flat, opening and closing cards (room-ui.js has the rooms, room-art.js the new things).
  function roomOn() { // Room mode is showing: Lumi Pet, her setting on (old saves count as on), and nobody choosing a player or on the couch.
    return APP === "lumi-pet" && Boolean(Ru) && Boolean(snap) && snap.pet.room_mode !== false && !picking && !couching();
  }

  function syncRoom() { // Keep the tab in step with room mode: the room's own rows when no card is open, the lists when it's off.
    const on = roomOn();
    if (!on) { roomCard = null; if (tab === ROOM) changeTab(0); }
    else if (!roomCard && tab !== ROOM) changeTab(ROOM); // Switched on (from Settings' list), or a card closed.
    if (on) picks[ROOM] = roomAt.at; // The room's cursor is where she stands.
    $("gm").dataset.rooms = String(on); // game.css folds the tabs away.
    return on;
  }

  function roomMove(to) { // She walks to another spot (or room): remembered on this device, with a soft step.
    target = null; // A snap move (a remote, the lower bar, a thing pressed) ends any free walk to a clicked spot.
    const next = Ru.clean(to), crossing = next.room !== roomAt.room, moved = crossing || next.at !== roomAt.at || next.x !== roomAt.x; // Measured before she goes.
    roomAt = next; picks[ROOM] = roomAt.at;
    G.store.set("room-at", roomAt);
    if (moved) { G.sound.unlock(); G.sound.tone(crossing ? 587.33 : 523.25, .08, 0, .06); } // A higher step through a doorway.
  }

  function roomWalk(to) { roomMove(to); lastList = ""; render(); } // A "To the kitchen" row, or an edge arrow.

  function openCard(thing, at) { // Use a thing: she walks to it and its card opens (that tab's rows).
    if (typeof at === "number") roomMove({ room: roomAt.room, at: at });
    if (thing.act === "door") { adventure(); return; } // The front door: straight out into town, as ever.
    const to = TABS.indexOf(thing.tab);
    if (to < 0) return; // Not a tab this app has (can't happen in Lumi Pet; kept safe).
    changeTab(to); roomCard = thing; picks[tab] = 0; lastList = "";
    const offered = rows().filter(function (row) { return row.key !== "card-back"; });
    if (thing.keys && offered.length === 1) { // One choice (the potion shelf): no menu, she just does it.
      const only = offered[0];
      roomCard = null; changeTab(ROOM);
      if (only.note && !only.send) say(only.note); else if (only.local) only.local(); else if (only.send) submit(only);
      return;
    }
    say(Ru.guide(roomAt, thing)); // Lumi the guide: what she says as its menu opens (right-hand panel).
  }

  function closeCard() { // Back to the room, standing where she was. True when a card was open.
    if (!roomCard) return false;
    roomCard = null; changeTab(ROOM); lastList = ""; render();
    startWalk(); // A key still held from the menu walks her on at once.
    return true;
  }

  function roomKey(action, arrow, meta) { // The keys in room mode. True when used here; false lets the usual handling run (a card's list, Enter, Play).
    if (tab === ROOM) {
      if (arrow && meta && meta.held) return true; // A held key, pad or panel button: free walking (onHold) moves her, not a snap step.
      if (arrow) { roomMove(Ru.step(roomAt, action === "left" || action === "up" ? -1 : 1)); lastList = ""; render(); return true; }
      if (action === "next" || action === "prev") { roomMove(Ru.hop(roomAt, action === "prev" ? -1 : 1)); lastList = ""; render(); return true; }
      return false; // Enter uses the spot (its row), Play drinks a potion.
    }
    if (roomCard && (action === "next" || action === "prev")) { closeCard(); return true; } // No tabs to flip through in a card: back to the room.
    return false;
  }

  function roomPress(event) { // Mouse and touch in room mode: a thing (walk to it and use it), or the floor (walk to just there).
    if (!roomOn() || room.dataset.leaving === "true") return;
    const hit = event.target.closest("[data-click]");
    if (hit && hit.dataset.click.indexOf("thing:") === 0) {
      const at = Ru.find(roomAt.room, hit.dataset.click.slice(6)), spot = Ru.layout(roomAt.room)[at];
      if (spot) { pendingConfirm = null; openCard(spot, at); render(); }
      return;
    }
    if (tint || (roomCard && (opened.home || opened.closet))) return; // Trying a colour or a piece on: the room is a preview, so a stray press doesn't walk off.
    walkTo(event);
  }

  function floorAt(event) { // Where on the room's 1000 x 500 drawing a press was (or null where the browser can't say).
    const matrix = houseArt.getScreenCTM && houseArt.getScreenCTM();
    if (!matrix) return null;
    const point = houseArt.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
    return point.matrixTransform(matrix.inverse());
  }

  function walkTo(event) { // A press on the floor (or a middle-click anywhere): she walks there, never using anything on the way.
    const at = floorAt(event);
    if (!at) return;
    if (roomCard) { roomCard = null; changeTab(ROOM); lastList = ""; render(); } // Walking away closes the card.
    target = Ru.clean(Ru.point(roomAt.room, at.x, at.y)); // Clamped to the room's floor, and (clean) out in front of any thing pressed on: somewhere she can stand. She walks round things in the way.
    startWalk();
  }

  // ---- Free walking (docs/plans/lumi-pet-social.md phase 1, Doll's design 2026-11-19). Held arrows and WASD
  // (common.js MGame.onHold), a gamepad (gamepad.js MPad, its stick analog), the Tab5's or a phone's pad in gamepad mode,
  // and a press on the floor all walk her, a little each animation frame, through room-ui.js move() (which keeps her
  // off the things and carries her through to the next room). The loop only runs while something walks her.
  // A TV remote's single presses stay snap steps (roomKey).
  // (Its state is declared with roomAt, near the top: SPEED, holds, target, walkFrame, walkLast, walkSaved, facing.)

  function onHold(who, x, y) { // A keyboard, pad or panel holds a direction (x, y each -1..1), or lets go (0, 0).
    if (x || y) { holds[who] = { x: x, y: y }; target = null; } // The keys take over from a click's walk.
    else delete holds[who];
    startWalk();
  }

  function heldWay() { // Everything held, added up, no faster than one stick pushed all the way (a diagonal isn't quicker).
    let x = 0, y = 0;
    Object.keys(holds).forEach(function (who) { x += holds[who].x; y += holds[who].y; });
    const size = Math.hypot(x, y);
    return size > 1 ? { x: x / size, y: y / size } : { x: x, y: y };
  }

  function canWalk() { return roomOn() && tab === ROOM && !roomCard && room.dataset.leaving !== "true"; } // The room is showing, no menu open, not going out of the door.

  function startWalk() { if (!walkFrame) { walkLast = 0; walkFrame = requestAnimationFrame(walkTick); } } // Start the loop (once).

  function stopWalk() { // Nothing walks her: CSS animations back on, and her place remembered on this device.
    walkFrame = 0; walkLast = 0;
    if (room.dataset.walking) delete room.dataset.walking;
    if (!walkSaved) { walkSaved = true; G.store.set("room-at", roomAt); }
  }

  function walkTick(now) { // One frame of walking.
    walkFrame = 0;
    const dt = walkLast ? Math.min(0.05, (now - walkLast) / 1000) : 0; walkLast = now; // (Capped: a slow frame never jumps her across the room.)
    let way = heldWay(), left = Infinity;
    if (!(way.x || way.y) && target) { // A click's walk: straight toward the spot.
      const dx = target.x - roomAt.x, dy = target.y - roomAt.y, size = Math.hypot(dx, dy);
      if (target.room !== roomAt.room || size < 2) target = null;
      else { way = { x: dx / size, y: dy / size }; left = size; }
    }
    if (!canWalk() || !(way.x || way.y)) { if (!canWalk()) target = null; stopWalk(); return; }
    room.dataset.walking = "true"; // game.css: no easing while she walks (it would trail behind her)
    let sx = way.x * SPEED.x * dt, sy = way.y * SPEED.y * dt;
    const step = Math.hypot(sx, sy);
    if (step > left) { sx *= left / step; sy *= left / step; } // The last step of a click's walk lands exactly on the spot.
    if (sx) facing = sx < 0 ? -1 : 1;
    const before = roomAt, still = function (at) { return at.room === before.room && at.x === before.x && at.y === before.y; };
    let next = Ru.move(roomAt, sx, sy);
    if (target && dt && next.room === before.room) { // A click's walk: did that step get her closer?
      const gap = function (at) { return Math.hypot(target.x - at.x, target.y - at.y); };
      if (gap(before) - gap(next) < Math.hypot(sx, sy) / 2) { // Less than half a step closer: a thing is in the way.
        const around = Ru.move(roomAt, 0, SPEED.y * dt);       // Step toward the front, round its base (the front is always clear)...
        if (!still(around)) next = around;
        else if (still(next)) target = null;                   // ...and if nothing moves her at all, she's as close as she can get.
      }
    }
    roomAt = next; picks[ROOM] = roomAt.at; walkSaved = false;
    if (next.room !== before.room) { G.sound.tone(587.33, .08, 0, .06); target = null; } // Through a doorway: the doorway's step.
    if (next.room !== before.room || next.at !== before.at) { lastList = ""; render(); } // A new room, or a new spot (her bubble, the lower bar, the list)
    else placeLumi();                                                                        // otherwise only where she is
    walkFrame = requestAnimationFrame(walkTick);
  }

  function placeLumi() { // Where she stands, drawn: the pan that keeps her in view, her place, size and facing (game.css), her shadow, the thing in reach.
    const pan = roomPan(roomAt.x);
    houseArt.setAttribute("viewBox", pan + " 0 1000 500");
    room.style.setProperty("--gx", String(Math.round(roomAt.x - 500 - pan))); // from the middle of what's shown
    room.style.setProperty("--gy", String(Math.round(roomAt.y)));             // her feet
    room.style.setProperty("--gs", Ru.scale(roomAt.y).toFixed(3));            // a little smaller further back
    room.style.setProperty("--face", String(facing));
    const shade = document.getElementById("lp-rm-shadow"), r = Ru.shadow(roomAt);
    if (shade) ["cx", "cy", "rx", "ry"].forEach(function (k) { // Her shadow: as attributes (every browser), and as CSS too, so game.css can ease it along with her on a snap step
      shade.setAttribute(k, r[k]); shade.style.setProperty(k, r[k] + "px");
    });
    const near = Ru.reach(roomAt), mark = near >= 0 ? "thing:" + Ru.layout(roomAt.room)[near].id : "";
    houseArt.querySelectorAll(".lp-rm-hit").forEach(function (box) { box.classList.toggle("lp-rm-near", box.dataset.click === mark); });
  }

  function roomPan(x) { // How far to slide the room's drawing sideways so Lumi, at x, is in view: 0 when the screen shows the whole flat's width.
    const box = room.getBoundingClientRect();
    if (!box.width || !box.height) return 0;
    const shown = box.width / Math.max(box.width / 1000, box.height / 500); // Drawing units across the screen (the drawing is cropped to fill it).
    const reach = Math.max(0, (Ru.REACH[1] - Ru.REACH[0] - shown) / 2);        // How far a pan may go before showing past the room's things.
    return Math.round(Math.max(-reach, Math.min(reach, x - 500)));             // Centre on her, never past either end.
  }

  function drawRoom(preview) { // Room mode's picture: the room she's in (house.js walls, room-art.js things), and her standing at her spot.
    const here = Ru.room(roomAt.room);
    const trying = tint && tint.spot ? [tint.spot, tintHex()] : preview || null; // a colour being painted, or a piece tried on
    const house = RA.view(here.id, snap.pet.house, trying); // this room's own wall and floor (else the bedroom's), with that in it
    room.dataset.rooms = here.id;
    $("lp-place").textContent = here.name;
    const scenery = document.querySelector(".parallax"), backdrop = Boolean(scenery) && getComputedStyle(scenery).display !== "none";
    const boxes = here.id === "bedroom" && snap.boxes ? snap.boxes.left : 0;
    const signature = JSON.stringify(["room", here.id, house, backdrop, boxes]);
    if (signature !== lastHouse) { // Only redrawn when the room or what's in it changes: walking just moves her and her shadow (placeLumi).
      lastHouse = signature;
      room.dataset.open = String(backdrop && house.view === "view-city"); // Her windows open onto the page's scenery in every room.
      houseArt.innerHTML = (here.id === "bedroom" ? H.render(house, backdrop, { boxes: boxes, shift: RA.BEDROOM_SHIFT }) : H.render(house, backdrop, { only: RA.SHELL })) + // her furniture spread out (room-art.js)
        RA.scene(here.id, house) + Ru.hits(here.id, roomAt); // All authored in house.js, room-art.js and room-ui.js; nothing a person typed.
    }
    placeLumi(); // A narrow screen follows her across the room (the pan); a wide one shows it all.
  }

  function drawThings(on) { // Room mode's lower bar: the things in this room (and the ways through), each with Lumi's bubble saying what it is.
    const bar = $("lp-things");
    bar.hidden = !on;
    if (!on) { lastThings = ""; return; }
    const list = Ru.rows(roomAt, { open: openCard, walk: roomWalk, door: adventure }); // The same rows the room's list has.
    const current = roomCard ? Ru.find(roomAt.room, roomCard.id) : roomAt.at; // Her spot, or the thing whose menu is open.
    const signature = roomAt.room + "|" + list.map(function (row) { return row.key; }).join("|");
    if (signature !== lastThings) { // Rebuilt only when she changes room, so a button being pressed is never swapped mid-press.
      lastThings = signature;
      bar.replaceChildren.apply(bar, list.map(function (row, index) {
        const button = document.createElement("button"), icon = document.createElement("span"), bubble = document.createElement("span"), name = document.createElement("b");
        button.type = "button"; button.className = "lp-thing"; button.dataset.thing = row.key;
        icon.className = "lp-thing-icon"; icon.textContent = row.icon; icon.setAttribute("aria-hidden", "true");
        bubble.className = "lp-thing-say"; name.textContent = row.name; // Her bubble: what it is, then her words for it.
        bubble.append(name, document.createTextNode(" " + (row.say || row.hint)));
        button.append(icon, bubble);
        button.addEventListener("click", function () { // Walk to it and use it (a menu opens on the right where it needs one).
          pendingConfirm = null;
          if (roomCard) { roomCard = null; changeTab(ROOM); } // Another thing: this menu closes first.
          roomMove({ room: roomAt.room, at: index });
          row.local(); lastList = ""; render();
        });
        return button;
      }));
    }
    Array.from(bar.children).forEach(function (button, index) { button.setAttribute("aria-pressed", String(index === current)); });
  }

  function drawRoomBits(on) { // Room mode's extras around the picture: her speech bubble, the lower bar, the arrows to the next rooms.
    const guide = $("lp-guide"), ways = on && !roomCard ? Ru.neighbours(roomAt) : { left: null, right: null };
    $("gm").dataset.card = String(on && Boolean(roomCard)); // game.css shows the right-hand panel only for an open menu.
    if (!on) { delete room.dataset.rooms; guide.hidden = true; $("lp-place").textContent = PLACE; if (houseArt.getAttribute("viewBox") !== "0 0 1000 500") houseArt.setAttribute("viewBox", "0 0 1000 500"); } // unpanned again
    else { guide.hidden = false; guide.textContent = $("lp-message").textContent; } // Her bubble says what she last said.
    drawThings(on);
    [["left", $("lp-way-left")], ["right", $("lp-way-right")]].forEach(function (pair) {
      const to = ways[pair[0]];
      pair[1].hidden = !to;
      if (to) { // Its label is the room's short name ("kitchen"); its accessible name says where it walks.
        const short = to.name.replace(/^HER |^THE /, "").toLowerCase();
        pair[1].querySelector("[data-way-name]").textContent = short; pair[1].dataset.go = to.id;
        pair[1].setAttribute("aria-label", "Walk to the " + short);
      }
    });
  }

  function changeTab(to) { // Open a category afresh: its sub-lists closed, nothing half-chosen.
    tab = to; opened = { home: null, closet: null }; flip = {}; battleView = null; pendingConfirm = null; albumZoom = false; albumFocus = "board"; skillFocus = "board"; tint = null; browsing = null;
  }

  // ---- Her name and her colour (Settings).
  function askName() { // A keyboard's way to name her; the server checks it (1 to 16 letters, numbers, spaces, ' . -).
    const name = window.prompt("What's her name?", snap.pet.name);
    if (name !== null && name.trim()) submit({ key: "name", send: { action: "name", name: name } });
  }

  function openTint() { // Open the colour drawer on the colour she has now (her own, or her glow's).
    const now = R.hexRgb(R.looks(snap.pet.glow, snap.pet.colour).glow);
    tint = { r: now[0], g: now[1], b: now[2] }; picks[tab] = 1;
  }

  function openPaint(spot, colour) { // Home → a wall or floor → Paint it a colour: the sliders, on its colour now (or a soft lilac).
    const now = R.hexRgb(colour || "#b98aa9");
    tint = { r: now[0], g: now[1], b: now[2], spot: spot }; picks[tab] = 1; lastList = "";
  }

  function tintHex() { return R.rgbHex([tint.r, tint.g, tint.b]); }

  function nudge(part, by) { tint[part] = Math.max(0, Math.min(255, tint[part] + by)); render(); } // One slider moved by a key.

  function tintRows() { // The colour drawer: a row each for red, green and blue (◀ ▶ move it; the sliders do too), then keep it.
    // With tint.spot it paints that wall or floor instead of colouring her (room mode's paint pots), shown in the room as it moves.
    const names = { r: ["Red", "🔴"], g: ["Green", "🟢"], b: ["Blue", "🔵"] }, painting = tint.spot;
    const rows = [painting ? { key: "tint-back", icon: "‹", name: "Back", hint: "The paint stays as it was", local: function () { tint = null; picks[tab] = 1; } }
      : { key: "tint-back", icon: "‹", name: "Back to Settings", hint: "Her colour stays as it was", local: function () { tint = null; picks[tab] = 1; } }];
    ["r", "g", "b"].forEach(function (part) {
      rows.push({ key: "tint-" + part, icon: names[part][1], name: names[part][0] + " " + tint[part], hint: "◀ ▶ less or more · Enter jumps up a step · or slide it below",
        turn: function (by) { nudge(part, by * 8); }, local: function () { tint[part] = (tint[part] + 32) % 288 > 255 ? 0 : tint[part] + 32; } });
    });
    if (painting) { rows.push({ key: "tint-save", icon: "🎨", name: "Paint it " + tintHex().toUpperCase(), hint: "Free · paint over it any time", send: { action: "paint", spot: painting, colour: tintHex() } }); return rows; }
    rows.push({ key: "tint-save", icon: "✓", name: "Keep " + tintHex().toUpperCase(), hint: "Her very own colour, everywhere she goes", send: { action: "colour", colour: tintHex() } });
    if (snap.pet.colour) rows.push({ key: "tint-glow", icon: "✦", name: "Her glow's own colours", hint: "Back to " + snap.pet.glow + "'s colours (her element stays the same)", send: { action: "colour", colour: "" } });
    return rows;
  }

  function drawTint() { // Her colours in the room (and on her), and the sliders while the drawer is open.
    const panel = $("lp-tint"), look = tint && !tint.spot ? R.tint(tintHex()) : snap.pet.colour ? R.tint(snap.pet.colour) : null; // (painting a wall leaves her as she is)
    ["glow", "body", "shadow"].forEach(function (part) { if (look) room.style.setProperty("--pet-" + part, look[part]); else room.style.removeProperty("--pet-" + part); });
    panel.hidden = !tint;
    if (!tint) return;
    panel.querySelectorAll("[data-tint]").forEach(function (slider) { const part = slider.dataset.tint; if (Number(slider.value) !== tint[part]) slider.value = String(tint[part]); });
    panel.querySelectorAll("[data-tint-n]").forEach(function (number) { number.textContent = String(tint[number.dataset.tintN]); });
  }

  $("lp-tint").addEventListener("input", function (event) { // A slider dragged with a finger or the mouse.
    const part = event.target.dataset.tint;
    if (!tint || !part) return;
    tint[part] = Number(event.target.value); lastList = ""; render();
  });

  function battleRows() { // The Battle tab's lists (battle-ui.js); also used by the Dungeon tab while a fight is on.
    return Bt.rows({ snap: snap, view: battleView, finished: finished, now: serverNow(), countdown: R.countdown,
      go: function (view, pick, clear) { battleView = view; picks[tab] = pick || 0; if (clear) finished = null; } });
  }

  function exploring() { const run = snap.dungeon.run; return run && !run.over ? run : null; } // The trip in progress, if any.
  function trip() { return has("dungeon") ? exploring() : null; } // ...as far as this page shows it: only Lumi Dungeons draws and plays a trip. The other two apps say she is away (awayRows).

  function syncPlay() { // Keep the page's copy of a puzzle in step with where Lumi is: start one at a door, drop it on leaving.
    if (TOWN) return; // No puzzles in town, and its first draw mustn't move the cursor off the place she arrived at.
    const run = trip(), lock = run && run.facing ? run.facing.lock : null, cards = run && !run.facing && run.game && run.game.kind === "match" && !run.room.done;
    const turn = run && run.party && run.party.puzzle ? ":" + run.party.puzzle.phase + run.party.puzzle.turn + ":" + run.party.puzzle.holder : ""; // A party's new turn starts the board afresh.
    const id = (!run ? "" : lock ? run.id + ":" + run.facing.at + ":" + lock.kind + (lock.tries ? lock.tries.length : "") : cards ? run.id + ":" + run.at + ":match" : "") + turn;
    if (play.id === id) { if (watching()) play.board = partyBoard(lock, play.board); return; } // Someone else's turn (or the couch): show their moves as they make them.
    if (lock && lock.kind === "sokoban") { play = { id: id, board: Dg.sokobanStart(lock.rows) }; focus = "board"; }
    else if (lock && lock.kind === "lights") { play = { id: id, board: Dg.lightsStart(lock) }; focus = "board"; }
    else if (lock && lock.kind === "runes") { play = { id: id, guess: [] }; focus = "list"; }
    else if (cards) { play = { id: id, cursor: play.cursor || 0 }; focus = "board"; }
    else { play = { id: id }; focus = "list"; }
    if (run && run.party && lock && (lock.kind === "sokoban" || lock.kind === "lights")) play.board = partyBoard(lock, play.board); // A turn picked up halfway (a reload) carries on from the server's moves.
    picks[tab] = 0;
  }

  function dungeonGo(change) { // What a dungeon row that doesn't ask the server can do: move between board and list, edit a puzzle.
    const lock = exploring() && exploring().facing ? exploring().facing.lock : null;
    if (change.focus) focus = change.focus;
    if (change.dismiss) { dismissedRun = change.dismiss; G.store.set("read-run", change.dismiss); picks[tab] = 0; }
    if ("care" in change) { caring = change.care; picks[tab] = 0; focus = "list"; } // The snack-and-potion list, or back to the room's own.
    if (change.undo) play.board = Dg.sokobanUndo(play.board);
    if (change.reset && lock) play.board = lock.kind === "sokoban" ? Dg.sokobanStart(lock.rows) : Dg.lightsStart(lock);
    if ((change.undo || change.reset) && inParty() && lock) partyProgress(lock.kind === "sokoban" ? { action: "sokoban", moves: play.board.moves } : { action: "lights", picks: play.board.presses });
    if (change.rune && play.guess.length < lock.slots) play.guess = play.guess.concat([change.rune]);
    if (change.erase) play.guess = play.guess.slice(0, -1);
    lastDungeon = "";
  }

  function boardKey(action) { // An arrow or Enter on a puzzle board or the cards.
    const run = exploring(), lock = run.facing ? run.facing.lock : null;
    if (lock && lock.kind === "sokoban") { // Arrows push; Enter takes the last step back.
      play.board = action === "enter" ? Dg.sokobanUndo(play.board) : Dg.sokobanMove(play.board, { up: "u", down: "d", left: "l", right: "r" }[action]);
      if (inParty()) { partyProgress({ action: "sokoban", moves: play.board.moves }); return; } // The others watch each step; the server opens the door once it's solved.
      if (Dg.sokobanSolved(play.board)) submit({ key: "solved", url: "dungeon/act", send: { action: "sokoban", moves: play.board.moves } });
      return;
    }
    const board = lock ? play.board : null, count = lock ? board.size * board.size : run.game.cards.length, width = lock ? board.size : 4;
    const at = lock ? board.cursor : play.cursor;
    if (action === "enter") { // Press the lamp, or turn the card, under the cursor.
      if (lock) {
        play.board = Object.assign(Dg.lightsPress(board, at), { cursor: at });
        if (inParty()) { partyProgress({ action: "lights", picks: play.board.presses }); return; }
        if (Dg.lightsSolved(play.board)) submit({ key: "solved", url: "dungeon/act", send: { action: "lights", picks: play.board.presses } });
      } else submit({ key: "flip", url: "dungeon/act", send: { action: "flip", picks: [at] } });
      return;
    }
    const to = Dg.cursorMove(at, { up: "n", down: "s", left: "w", right: "e" }[action], width, count);
    if (to === null) return; // The edge of the board.
    if (lock) play.board = Object.assign({}, board, { cursor: to }); else play.cursor = to;
  }

  // ---- The Stardust Garden (mnemerynne/lumigarden.py; rows and words in garden-ui.js, drawing in garden-art.js).
  // Its tab works like a dungeon trip: the garden is drawn over the room and the right panel is the garden's own.
  // Arrows move over the plots, Enter plants or trims, Play/Pause presses the powder, Prev/Next move through the list.
  // ---- The Skills tab (rules in mnemerynne/lumibattle.py; layout and words in skills-ui.js, drawing in skills-art.js).
  // All six class trees on one page, drawn over the room. Like the garden: the arrows move over the skills, Enter
  // learns one (or carries or puts away a move), and Prev/Next move through the list beside it.
  function treeing() { return TABS[tab] === "skills" && !trip(); }

  function skillEnter() { // Enter, or a second press of the mouse, on the skill under the cursor.
    const what = Sk.choice(snap, skillCursor);
    if (what.say) { say(what.say); return; }
    submit({ key: "act", name: what.name, label: what.name, send: what.send });
  }

  function skillsGo(change) { if (change.leave) goHome(); }

  function skillsKey(action) { // The keyboard and remote on the skill tree.
    const moving = action === "up" || action === "down" || action === "left" || action === "right";
    if (moving) { skillCursor = Sk.move(snap.battle, skillCursor, action); skillFocus = "board"; picks[tab] = 0; }
    else if (action === "next" || action === "prev") { const many = rows().length; picks[tab] = (picks[tab] + (action === "prev" ? many - 1 : 1)) % many; skillFocus = "list"; }
    else if (action === "enter") { if (skillFocus === "board") skillEnter(); else choose(); return; }
    render();
  }

  function skillClick(name) { // A press on a skill: the first press looks at it, a second press on the same one acts.
    const id = name.split(":")[1];
    if (name.indexOf("skill:") !== 0 || busy) return;
    if (id === skillCursor && skillFocus === "board") skillEnter(); else { skillCursor = id; skillFocus = "board"; picks[tab] = 0; }
    render();
  }

  function drawSkills() { // The skill trees over the room while their tab is open.
    const on = Boolean(snap && treeing());
    room.dataset.skills = String(on);
    if (!on) { lastSkills = ""; return; }
    if (!skillCursor || !Sk.find(snap.battle, skillCursor)) skillCursor = snap.battle.trees[0].nodes[0].id;
    $("gm").dataset.walking = "true"; $("lp-keys").innerHTML = KEYS_SKILLS; // Fixed text from this file.
    $("lp-dungeon").style.display = "";
    const lines = Sk.lines(snap, skillCursor), told = "skills|" + lines.join("|");
    if (told !== lastStory) {
      lastStory = told;
      const story = $("lp-story"); story.textContent = "";
      lines.forEach(function (words, index) { const line = document.createElement("p"); line.textContent = words; if (index === 1) line.className = "lp-scene"; story.appendChild(line); });
    }
    const signature = JSON.stringify([snap.pet.skills, snap.pet.moves, snap.progress.points, skillCursor, skillFocus]);
    if (signature === lastSkills) return;
    lastSkills = signature; lastDungeon = ""; lastGarden = ""; lastAlbum = "";
    $("lp-dungeon").innerHTML = Sa.scene(snap, { cursor: skillCursor, focused: skillFocus === "board" }); // Markup authored in skills-art.js; names are drawn as text by the list, not here.
  }

  // ---- The Collection tab (shelves and words in collection-ui.js, drawing in collection-art.js): a gallery of
  // everything there is to collect, drawn over the room like the skill trees. The arrows move over the pictures
  // (off an edge turns the page), Enter looks at one large, and Prev/Next move through the list beside it.
  function albuming() { return TABS[tab] === "collection" && !trip(); }

  function albumGo(change) { // What a row of the album's list (or a press on the page) can do.
    if (change.leave) { goHome(); return; }
    if (change.shelf) { album = { shelf: change.shelf, page: 0, cursor: 0 }; albumZoom = false; }
    if ("page" in change) { album.page = change.page; album.cursor = 0; albumZoom = false; }
    if ("zoom" in change) albumZoom = change.zoom;
  }

  function albumKey(action) { // The keyboard and remote in the gallery.
    const moving = action === "up" || action === "down" || action === "left" || action === "right";
    if (moving) { const to = Cl.move(Cl.view(snap, album), action); album.page = to.page; album.cursor = to.cursor; albumFocus = "board"; }
    else if (action === "next" || action === "prev") { const many = rows().length; picks[tab] = (picks[tab] + (action === "prev" ? many - 1 : 1)) % many; albumFocus = "list"; }
    else if (action === "enter") { if (albumFocus === "board") albumZoom = !albumZoom; else { choose(); return; } }
    render();
  }

  function albumClick(name) { // A press on the gallery: a shelf, a page arrow, or a picture (a second press looks at it large).
    const what = name.split(":"), seen = Cl.view(snap, album);
    if (what[0] === "shelf") albumGo({ shelf: what[1] });
    else if (what[0] === "page") { const to = seen.page + Number(what[1]); if (to >= 0 && to < seen.pages) albumGo({ page: to }); }
    else if (what[0] === "zoom") albumZoom = false;
    else if (what[0] === "cell") { const at = Number(what[1]); if (at === seen.cursor && albumFocus === "board") albumZoom = true; else { album.cursor = at; albumFocus = "board"; } }
    render();
  }

  function fitThumbs() { // Each little picture is drawn wherever its own file puts it; frame it round what was actually drawn.
    $("lp-dungeon").querySelectorAll("svg[data-thumb]").forEach(function (frame) {
      let box = null;
      try { box = frame.firstChild.getBBox(); } catch (_) { /* not on screen yet: the wide frame it was given will do */ }
      if (!box || !box.width || !box.height) return;
      const side = Math.max(box.width, box.height) * 1.08;
      frame.setAttribute("viewBox", (box.x + box.width / 2 - side / 2) + " " + (box.y + box.height / 2 - side / 2) + " " + side + " " + side);
    });
  }

  function drawAlbum() { // The gallery over the room while its tab is open.
    const on = Boolean(snap && albuming());
    room.dataset.album = String(on);
    if (!on) { lastAlbum = ""; return; }
    const seen = Cl.view(snap, album);
    album = { shelf: seen.shelf.id, page: seen.page, cursor: seen.cursor }; // Kept within what there is.
    $("gm").dataset.walking = "true"; $("lp-keys").innerHTML = KEYS_ALBUM; // Fixed text from this file.
    $("lp-dungeon").style.display = "";
    const lines = Cl.lines(seen), told = "album|" + lines.join("|");
    if (told !== lastStory) {
      lastStory = told;
      const story = $("lp-story"); story.textContent = "";
      lines.forEach(function (words, index) { const line = document.createElement("p"); line.textContent = words; if (index === 2) line.className = "lp-scene"; story.appendChild(line); });
    }
    const signature = JSON.stringify([album, albumZoom, albumFocus, seen.cells.map(function (item) { return item.id + (item.owned ? item.count || 1 : 0); })]);
    if (signature === lastAlbum) return;
    lastAlbum = signature; lastDungeon = ""; lastGarden = ""; lastSkills = "";
    $("lp-dungeon").innerHTML = Ca.scene(snap, { shelf: album.shelf, page: album.page, cursor: album.cursor, zoom: albumZoom, focused: albumFocus === "board" }); // Markup authored in collection-art.js and the art files it borrows from.
    fitThumbs();
  }

  function gardening() { return TABS[tab] === "garden" && !exploring(); }
  function passed() { return serverNow() - snap.now; } // How long ago the server told us its times.

  function lumiTo(spot, act) { // Lumi darts to a place in the garden (a plot, the press) and does a little something there.
    room.style.setProperty("--gx", String(Math.round(spot.x - 500))); room.style.setProperty("--gy", String(Math.round(spot.y)));
    lumiMoved = Date.now();
    if (!act) return;
    lumiFlip = !lumiFlip; // The same little move twice in a row still plays twice (game.css has each under two names).
    room.dataset.gardenAct = act + (lumiFlip ? "" : "-again"); lumiActUntil = Date.now() + 800;
    $("lp-effect").textContent = { dig: "❀", snip: "✂", bounce: "✦", sniff: "♡", cheer: "✧ ✦ ✧" }[act] || "♡"; effectUntil = Date.now() + 1200;
  }

  function lumiCheers(send, note) { // What Lumi does when the server answers a garden choice.
    if (send.action === "dig") lumiTo(Ga.spot(send.plot), "dig"); // Digging a flower up looks like planting one.
    else if (send.action === "plant" || send.action === "plot") lumiTo(Ga.spot(send.action === "plot" ? snap.garden.plots.length - 1 : send.plot), "dig");
    else if (send.action === "trim") lumiTo(Ga.spot(send.plot), note && note.gift ? "cheer" : "snip");
    else if (["trim_all", "capsule", "helper", "ability", "unlock", "moonrise", "decor_buy", "decor_place"].indexOf(send.action) >= 0 || (note && (note.water === "done" || note.feed === "done"))) lumiTo(Ga.spot(plotCursor), "cheer");
  }

  function lumiWanders() { // Left alone for a while, she trots to a flower and has a sniff: a ready one if there is one.
    if (mini || Date.now() - lumiMoved < 6000) return;
    const gone = passed(), planted = [], ready = [];
    snap.garden.plots.forEach(function (plot, index) { if (plot.flower) { planted.push(index); if (Gd.stage(plot, gone) !== "growing") ready.push(index); } });
    const among = ready.length ? ready : planted;
    if (among.length) lumiTo(Ga.spot(among[Math.floor(Math.random() * among.length)]), "sniff"); else lumiMoved = Date.now();
  }

  function pressOnce() { // One squeeze of the press: counted here, sent to the server in a batch a moment later.
    pending += 1;
    lumiTo(Ga.PRESS, "bounce"); // She runs to the machine and jumps on it with every press.
    G.sound.unlock(); G.sound.tone(480 + (pending % 6) * 36, .05, 0, .05);
  }

  function flushPresses() { // Send the presses counted so far. The server decides how many it believes and what they made.
    if (busy || !pending || !snap) return;
    const count = pending;
    pending = 0; busy = true;
    G.api(API + "garden", { action: "press", count: count }).then(function (data) {
      take(data);
      const words = Gd.said({ action: "press" }, data.said, data.garden);
      if (words) { say(words); G.sound.tone(880, .12, 0, .06); }
    }).catch(function (refused) { say(refused.message); }).then(function () { busy = false; render(); });
  }

  function gardenSend(send) { // One garden choice. Presses still waiting go first, so the server sees things in order.
    if (busy) return;
    const count = pending;
    pending = 0; busy = true; pendingConfirm = null;
    (count ? G.api(API + "garden", { action: "press", count: count }) : Promise.resolve()).then(function () {
      return G.api(API + "garden", send);
    }).then(function (data) {
      take(data); lastList = "";
      const words = Gd.said(send, data.said, data.garden, function (id) { return item(id).name; }, data.garden_decor);
      if (words) say(words);
      lumiCheers(send, data.said);
    }).catch(function (refused) {
      say(refused.message);
      if (mini && mini.kind === "water") mini.answer = []; // The answer never arrived: let it be given again.
    }).then(function () { busy = false; render(); });
  }

  function paging() { return gardening() && GT.isPage(drawer) && !mini; } // One of the garden's pages is open (a minigame takes the screen back).

  function pageCtx() { return { garden: snap.garden, cursor: pageAt(), seed: Gd.seedFor(snap.garden, null, seed), level: snap.pet.level, go: gardenGo }; }

  function pageAt() { // The open page's cursor, put somewhere sensible if it isn't on anything.
    if (!GT.find(drawer, snap.garden, pageCursor[drawer])) pageCursor[drawer] = GT.first(drawer, snap.garden, Gd.seedFor(snap.garden, null, seed));
    return pageCursor[drawer];
  }

  function pageEnter() { // Enter, or a second press of the mouse, on what a page's cursor is on.
    const what = GT.choice(drawer, pageCtx(), pageAt()), key = "page:" + drawer + ":" + pageAt();
    if (what.say) { say(what.say); return; }
    if (what.change) { gardenGo(what.change); return; }
    if (busy) return;
    if (what.confirm && pendingConfirm !== key) { pendingConfirm = key; say(what.confirm); return; } // A moonrise replants the garden: never on one stray press.
    gardenSend(what.send);
  }

  function gardenGo(change) { // What a garden row that doesn't ask the server can do.
    if ("drawer" in change) { drawer = change.drawer; picks[tab] = 0; gardenFocus = GT.isPage(drawer) ? "board" : "list"; pendingConfirm = null; } // A page opens with the arrows on it, like Skills.
    if (change.seed) { seed = change.seed; say("Seed chosen. Enter on an empty plot plants it."); }
    if (change.press) pressOnce();
    if (change.feed) feedPress();
    if (change.leave) goHome();
  }

  function plotEnter(index) { // Enter, or a press of the mouse, on a plot: plant, trim, dig, or say how long is left.
    const choice = Gd.plotChoice(snap.garden, index, seed, passed());
    if (choice.say) { say(choice.say); lumiTo(Ga.spot(index), "sniff"); return; }
    if (choice.confirm && pendingConfirm !== "plot") { pendingConfirm = "plot"; say(choice.confirm + " Press again to dig."); return; }
    gardenSend(choice.send);
  }

  function syncMini() { // Keep the page's minigame in step with what the server says is going on.
    const garden = snap.garden, show = gardening() ? garden.water.show : null, feeding = gardening() && garden.feed.until_in !== null && garden.feed.until_in - passed() > -4000;
    if (show) { if (!mini || mini.kind !== "water" || mini.show.length !== show.length) mini = { kind: "water", show: show, phase: "show", started: Date.now(), answer: [], lit: -1, flash: 0, heard: -1 }; }
    else if (feeding) { if (!mini || mini.kind !== "feed") mini = { kind: "feed", until: Date.now() + garden.feed.until_in - passed(), whole: garden.feed.seconds * 1000, left: garden.feed.until_in, count: 0, sent: false }; }
    else mini = null;
    if (mini && !miniTimer) miniTimer = setInterval(stepMini, 100);
    if (!mini && miniTimer) { clearInterval(miniTimer); miniTimer = null; }
  }

  function stepMini() { // Ten times a second while a minigame is on: light the next can, or run the fertilizer's clock down.
    if (!mini || !snap) return;
    if (mini.kind === "water" && mini.phase === "show") {
      const since = Date.now() - mini.started - 500, at = Math.floor(since / 650);
      if (since >= 0 && at >= mini.show.length) { mini.phase = "answer"; mini.lit = -1; lastList = ""; }
      else {
        mini.lit = since >= 0 && since % 650 < 470 ? mini.show[at] : -1;
        if (mini.lit >= 0 && mini.heard !== at) { mini.heard = at; G.sound.tone(CAN_NOTES[mini.lit], .2, 0, .07); }
      }
    } else if (mini.kind === "water") {
      if (mini.flash && Date.now() > mini.flash) { mini.lit = -1; mini.flash = 0; }
    } else {
      mini.left = mini.until - Date.now();
      if (mini.left <= 0 && !mini.sent && !busy) { mini.sent = true; gardenSend({ action: "feed_end", count: mini.count }); }
    }
    render();
  }

  function canPress(can) { // One can of the answer; the last one sends the whole answer.
    if (!mini || mini.kind !== "water" || mini.phase !== "answer" || busy || mini.answer.length >= mini.show.length) return;
    mini.answer.push(can); mini.lit = can; mini.flash = Date.now() + 220;
    G.sound.unlock(); G.sound.tone(CAN_NOTES[can], .14, 0, .07);
    if (mini.answer.length === mini.show.length) gardenSend({ action: "water_answer", picks: mini.answer.slice() });
  }

  function feedPress() { // One press of fertilizer, while the clock is running.
    if (!mini || mini.kind !== "feed" || mini.left <= 0) return;
    mini.count += 1;
    G.sound.unlock(); G.sound.tone(392 + (mini.count % 8) * 30, .04, 0, .05);
  }

  function gardenKey(action) { // The keyboard and remote in the garden.
    if (mini) { // A minigame has the keys: the arrows are the four cans, and Enter or Play feed.
      if (mini.kind === "water") { const can = ["up", "right", "down", "left"].indexOf(action); if (can >= 0) canPress(can); }
      else if (action === "enter" || action === "play") feedPress();
      render(); return;
    }
    const count = Gd.cells(snap.garden), moving = action === "up" || action === "down" || action === "left" || action === "right";
    if (paging()) { // A page: the arrows move over it, Enter acts on what they are on (or on the list), Prev/Next walk the list.
      if (moving) { pageCursor[drawer] = GT.move(drawer, snap.garden, pageAt(), action); gardenFocus = "board"; picks[tab] = 0; pendingConfirm = null; }
      else if (action === "next" || action === "prev") { const many = rows().length; picks[tab] = (picks[tab] + (action === "prev" ? many - 1 : 1)) % many; gardenFocus = "list"; }
      else if (action === "enter") { if (gardenFocus === "board") pageEnter(); else choose(); render(); return; }
      else if (action === "play") pressOnce();
      render(); return;
    }
    if (action === "play") pressOnce();
    else if (action === "enter") { if (gardenFocus === "board" && !drawer) plotEnter(plotCursor); else choose(); return; }
    else if (action === "next" || action === "prev" || (drawer && (action === "up" || action === "down"))) { // The list (a drawer has no plots to walk over).
      const many = rows().length, back = action === "prev" || action === "up";
      picks[tab] = (picks[tab] + (back ? many - 1 : 1)) % many; gardenFocus = "list";
    } else if (moving && !drawer) { plotCursor = Gd.moveCursor(Math.min(plotCursor, count - 1), action, count); gardenFocus = "board"; lumiTo(Ga.spot(plotCursor)); } // She follows the cursor.
    render();
  }

  function gardenClick(name) { // A press of the mouse or a finger on the garden (garden-art.js names each shape).
    const what = name.split(":"), n = Number(what[1]);
    if (what[0] === "press") pressOnce();
    else if (what[0] === "can") canPress(n);
    else if (what[0] === "feed") feedPress();
    else if (what[0] === "plot" && !mini) { plotCursor = n; gardenFocus = "board"; plotEnter(n); }
    else if (what[0] === "tree" && paging()) { // A page: the first press looks, a second press on the same thing acts.
      if (what[1] === pageAt() && gardenFocus === "board") pageEnter(); else { pageCursor[drawer] = what[1]; gardenFocus = "board"; picks[tab] = 0; pendingConfirm = null; }
    }
    render();
  }

  function drawGarden() { // The garden over the room while its tab is open: the plots, the press, and the lines about them.
    const on = Boolean(snap && gardening());
    room.dataset.garden = String(on);
    if (!on) { lastGarden = ""; gardenOpen = false; delete room.dataset.gardenAct; if (mini) syncMini(); return; }
    if (!gardenOpen) { // Just walked in: say what the press did meanwhile, and tell the server she has seen her flowers.
      gardenOpen = true; drawer = null; gardenFocus = "list";
      lumiTo(Ga.spot(Math.min(plotCursor, snap.garden.plots.length - 1)));
      say(Gd.away(snap.garden) || "The Stardust Garden. Plant flowers, trim them when they're ready, and press their powder into coins.");
      gardenSend({ action: "look" });
    }
    syncMini();
    const garden = snap.garden, gone = passed(), count = Gd.cells(garden);
    plotCursor = Math.min(plotCursor, count - 1);
    $("gm").dataset.walking = "true"; $("lp-keys").innerHTML = KEYS_GARDEN; // Fixed text from this file.
    $("lp-dungeon").style.display = ""; $("lp-dhud").hidden = false;
    $("lp-d-title").textContent = "Stardust Garden" + (garden.gardens > 1 ? " " + (garden.at + 1) + " of " + garden.gardens : "");
    $("lp-d-hp").style.width = Math.min(100, 100 * garden.squeezed / garden.price) + "%"; $("lp-d-mp").style.width = "0%"; // The bar is the next coin.
    $("lp-d-label").textContent = Gd.format(Math.max(0, garden.powder - pending * garden.rates.tap)) + " starpowder · " + garden.coins_today + " coins today";
    room.dataset.gardenPage = paging() ? drawer : ""; // Lumi steps off the screen while a page is up (game.css).
    if (paging()) { drawPage(garden); return; }
    const lines = Gd.status(garden, plotCursor, seed, gone, pending), told = "garden|" + lines.join("|");
    if (told !== lastStory) {
      lastStory = told;
      const story = $("lp-story"); story.textContent = "";
      lines.forEach(function (words, index) { const line = document.createElement("p"); line.textContent = words; if (!index) line.className = "lp-scene"; story.appendChild(line); });
    }
    const steps = garden.plots.map(function (plot) { return Gd.stage(plot, gone) + (plot.grow ? Math.floor(10 * Math.max(0, 1 - (plot.ready_in - gone) / plot.grow)) : ""); }); // Redraw only when a flower visibly changes.
    const playing = !mini ? null : mini.kind === "water" ? [mini.phase, mini.lit, mini.answer.length, mini.show.length] : [mini.count, Math.ceil(mini.left / 250)];
    const scenery = document.querySelector(".parallax"), sky = Boolean(scenery) && getComputedStyle(scenery).display !== "none"; // The theme's drifting scenery is the garden's sky, where there is some.
    if (lumiActUntil && Date.now() > lumiActUntil) { delete room.dataset.gardenAct; lumiActUntil = 0; }
    const signature = JSON.stringify([garden.at, steps, garden.plot_price, garden.decor.placed, Math.round(garden.squeezed), garden.price, garden.powder > 0.5, plotCursor, gardenFocus, drawer, playing, sky]);
    if (signature === lastGarden) { // Nothing to redraw: just let every flower's timer tick, where it stands.
      Ga.timers(garden, gone).forEach(function (timer) { const words = $("lp-dungeon").querySelector('[data-timer="' + timer[0] + '"]'); if (words && words.textContent !== timer[1]) words.textContent = timer[1]; });
      return;
    }
    lastGarden = signature; lastDungeon = "";
    $("lp-dungeon").innerHTML = Ga.scene(garden, { cursor: plotCursor, focused: gardenFocus === "board" && !drawer, passed: gone, mini: mini, decor: snap.garden_decor, sky: sky }); // Markup authored in garden-art.js; colours are the server's checked #rrggbb.
  }

  function drawPage(garden) { // The Flower tree, Helpers or Moonrise page over the garden, and the lines about what the cursor is on.
    $("lp-keys").innerHTML = KEYS_GARDEN_PAGE; // Fixed text from this file.
    const at = pageAt(), lines = GT.lines(drawer, pageCtx(), at), told = "gpage|" + lines.join("|");
    if (told !== lastStory) {
      lastStory = told;
      const story = $("lp-story"); story.textContent = "";
      lines.forEach(function (words, index) { const line = document.createElement("p"); line.textContent = words; if (index === 1) line.className = "lp-scene"; story.appendChild(line); });
    }
    const signature = JSON.stringify(["page", drawer, at, gardenFocus, pageCtx().seed, snap.pet.level, garden.flowers.map(function (f) { return f.unlocked ? 1 : f.next ? (garden.given >= f.needs ? 3 : 2) : 0; }).join(""),
      Math.floor(garden.powder), garden.helpers.map(function (h) { return h.level; }), garden.upgrades.map(function (u) { return u.count; }), garden.moonrise, garden.given, garden.gardens]);
    if (signature === lastGarden) return;
    lastGarden = signature; lastDungeon = "";
    $("lp-dungeon").innerHTML = GTa.scene(drawer, garden, { cursor: at, focused: gardenFocus === "board", seed: pageCtx().seed, level: snap.pet.level }); // Markup authored in garden-tree-art.js; text escaped there, colours the server's checked #rrggbb.
  }

  function walking() { // In the dungeon with the Dungeon tab open and no fight on: the arrows are Lumi's feet.
    return TABS[tab] === "dungeon" && exploring() && !(snap.fight && snap.fight.status === "active") && !finished && !teamFight();
  }

  function teamFight() { return party && party.run && party.run.party.fight ? party.run.party.fight : null; } // The party's co-op fight going on, if any.

  function fightCtx(aimAt, allyOf) { // What the co-op fight's rows need (party-ui.js teamRows).
    return { aim: aimAt, ally: allyOf, now: serverNow(), describe: Bt.describeMove, items: snap.battle.rules.kit };
  }

  function walk(action) { // One arrow, one room: up is north.
    const way = { up: "n", right: "e", down: "s", left: "w" }[action], run = exploring();
    caring = false; // Walking on puts the snacks away.
    if (run.room.exits.indexOf(way) < 0) { say("There's no way " + Dg.WAYS[way][0] + " from here."); return; }
    submit({ key: "go-" + way, url: "dungeon/act", send: { action: "move", way: way } });
  }

  function drinkPotion() { // The Play/Pause button: a potion, in a fight or between rooms.
    if (!has("battle")) return; // Only where the fights are.
    const team = teamFight();
    if (team) { // The co-op fight: a potion for herself, on her turn.
      if (team.you !== null && team.at === team.you) partyAct({ action: "fight", target: "potion", ally: team.you, turn: team.turn });
      return;
    }
    const fight = snap.fight && snap.fight.status === "active" ? snap.fight : null;
    if (fight) submit({ key: "potion", url: "battle/act", send: { battle: fight.id, turn: fight.turn, action: "potion" } });
    else if (exploring()) submit({ key: "potion", url: "dungeon/act", send: { action: "potion" } });
  }

  function drawDungeon() { // The dungeon scene over the house while she is out, and its line of health and satchel.
    const run = trip(), art = $("lp-dungeon"), bar = $("lp-dhud");
    room.dataset.dungeon = String(Boolean(run));
    room.dataset.away = String(Boolean(snap) && awayRows().length > 0 && !gardening()); // In the other two apps her room is empty while she is out (game.css).
    if (!run) caring = false;
    $("gm").dataset.walking = String(Boolean(run)); // The dungeon's own, taller right panel, with the tabs folded away for the whole trip (game.css). The garden does the same (drawGarden).
    $("lp-keys").innerHTML = snap && walking() ? KEYS_DUNGEON : TOWN ? KEYS_TOWN : KEYS_HOME; // Fixed text from this file.
    const here = TABS[tab] === "dungeon"; // A puzzle's panel covers the room (and Lumi) only while this tab can play it.
    room.dataset.puzzle = String(Boolean(here && run && (run.facing || (run.game && run.game.kind === "match" && !run.room.done))));
    art.style.display = run ? "" : "none"; bar.hidden = !run;
    const told = run ? JSON.stringify([run.id, run.scene, run.story]) : "";
    if (told !== lastStory && !(snap && (gardening() || treeing() || albuming()))) { // The story panel: what this room is like, then the tale so far (its newest line is the message under it).
      lastStory = told;
      const story = $("lp-story"); story.textContent = "";
      (run ? [run.scene].concat((run.story || []).slice(0, -1)) : []).forEach(function (words, index) {
        if (!words) return;
        const line = document.createElement("p"); line.textContent = words; if (!index) line.className = "lp-scene"; // Text only, never markup.
        story.appendChild(line);
      });
      story.scrollTop = story.scrollHeight;
    }
    if (!run) { lastDungeon = ""; return; }
    const hud = Dg.hud(run), signature = JSON.stringify([run.at, run.depth, run.rooms, run.facing, run.game, run.room, play, focus, run.satchel.keys, here]);
    $("lp-d-title").textContent = hud.title; $("lp-d-label").textContent = hud.label;
    $("lp-d-hp").style.width = hud.hp + "%"; $("lp-d-mp").style.width = hud.mp + "%";
    if (signature === lastDungeon) return;
    lastDungeon = signature;
    art.innerHTML = Da.scene(run, play, focus, here); // Markup authored in dungeon-art.js; colours are the server's checked #rrggbb.
  }

  function tellLive(moment) { // Live storyteller: the library's line is already showing; swap in a fresh one if it comes.
    if (snap.pet.storyteller !== "live" || !exploring() || exploring().party) return; // A party trip tells its story from the library.
    G.api(API + "dungeon/tale", {}).then(function (data) {
      const run = snap.dungeon.run;
      if (data.line && run && JSON.stringify(run.event) === moment) { run.say = data.line; say(data.line); }
    }).catch(function () { /* the library line stays */ });
  }

  function drawHouse(preview) { // Draw the room: what the server says is placed, with the highlighted object tried on.
    if (TOWN) { drawTown(); return; } // Lumi Town draws the map, or the place she's in.
    if (roomOn()) { drawRoom(preview); return; } // Room mode: the kitchen, her bedroom or the den.
    if (APP === "lumi-dungeons") { // Lumi Dungeons' lobby is the Lantern & Paw tavern (tavern-art.js), not her apartment (Doll's request, 2026-11-11).
      const night = sceneNow().night, signature = "tavern:" + night;
      if (signature === lastHouse) return; // Only redrawn when day turns to night or back.
      lastHouse = signature;
      room.dataset.open = "false"; // No windows onto the page's scenery here: the tavern paints its own.
      houseArt.innerHTML = Ta.scene({ night: night }); // Markup authored in tavern-art.js; nothing a person typed goes into it.
      return;
    }
    const house = Object.assign({}, snap.pet.house);
    if (preview) house[preview[0]] = preview[1];
    if (tint && tint.spot) house[tint.spot] = tintHex(); // Painting her wall or floor: the colour as it moves (other rooms' show in room mode).
    const scenery = document.querySelector(".parallax"), backdrop = Boolean(scenery) && getComputedStyle(scenery).display !== "none"; // The neon theme's drifting cities.
    const boxes = snap.boxes ? snap.boxes.left : 0, signature = JSON.stringify(house) + backdrop + boxes;
    if (signature === lastHouse) return; // Redrawing would restart the window's scrolling, so only when it changed.
    lastHouse = signature;
    room.dataset.open = String(backdrop && house.view === "view-city"); // The windows are open onto the page's own scenery.
    houseArt.innerHTML = H.render(house, backdrop, { boxes: boxes }); // Markup authored in house.js; nothing a person typed goes into it.
  }

  function drawOutfit(preview) { // Dress her: what the server says she wears, with the highlighted accessory tried on.
    const outfit = Object.assign({}, snap.pet.outfit);
    if (preview && preview[1]) outfit[preview[0]] = preview[1]; else if (preview) delete outfit[preview[0]]; // "Nothing" tries it off.
    const signature = JSON.stringify(outfit);
    if (signature === lastOutfit) return;
    lastOutfit = signature;
    $("lp-acc-back").innerHTML = W.render(outfit, "back"); // Markup authored in closet.js.
    $("lp-acc-front").innerHTML = W.render(outfit, "front");
  }

  function drawFight() { // The fight scene over the room: the enemy, and both sides' bars. Shown for a fight in progress or just ended.
    const team = TABS[tab] === "dungeon" ? teamFight() : null;
    room.dataset.team = String(Boolean(team)); // A co-op fight: every Lumi in one row (game.css).
    $("lp-team").hidden = !team;
    $("lp-fight").classList.toggle("lp-fight-team", Boolean(team));
    if (team) { drawTeam(team); return; }
    lastTeam = "";
    const fight = has("battle") ? snap.fight || finished : null; // Fights are Lumi Dungeons' to show.
    room.dataset.fight = String(Boolean(fight));
    $("lp-fight").hidden = !fight;
    if (!fight) { lastFoe = ""; return; }
    const foe = fight.enemy, bars = Bt.bars(fight), signature = foe.id + foe.colour;
    if (signature !== lastFoe) { lastFoe = signature; $("lp-foe").innerHTML = E.draw(foe.body, foe.colour); } // Art authored in enemies.js.
    $("lp-foe").dataset.down = String(foe.hp <= 0);
    $("lp-f-lumi").textContent = bars.lumi.label; $("lp-f-foe").textContent = bars.foe.label;
    $("lp-f-hp").style.width = bars.lumi.hp + "%"; $("lp-f-mp").style.width = bars.lumi.mp + "%"; $("lp-f-foehp").style.width = bars.foe.hp + "%";
  }

  function drawTeam(fight) { // The party's co-op fight: every Lumi's bars down the left, the creatures on the right (enemies.js).
    room.dataset.fight = "true";
    $("lp-fight").hidden = false;
    const target = couching() ? null : Pu.aimOf(fight, aim);
    const signature = JSON.stringify([fight.lumis.map(function (f) { return [f.name, f.hp, f.hpMax, f.mp, f.mpMax, f.stages, f.guarding, f.gone]; }),
      fight.foes.map(function (f) { return [f.hp, f.status]; }), fight.at, target]);
    if (signature === lastTeam) return;
    const fresh = !lastTeam;
    lastTeam = signature;
    function pct(now, max) { return Math.max(0, Math.min(100, Math.round(100 * now / Math.max(1, max)))) + "%"; }
    $("lp-team-lumis").replaceChildren.apply($("lp-team-lumis"), fight.lumis.map(function (f, seat) { // Text only: names are people's own words.
      const li = document.createElement("li"), name = document.createElement("span"), hp = document.createElement("div"), mp = document.createElement("div");
      li.dataset.turn = String(fight.at === seat); li.dataset.down = String(f.hp <= 0); li.dataset.you = String(fight.you === seat);
      name.textContent = f.name + " · " + (f.gone ? "gone home" : f.hp + "/" + f.hpMax) + (Pu.standing([f]).length && f.guarding ? " 🛡" : "");
      hp.className = "lp-bar"; hp.innerHTML = '<i style="width:' + pct(f.hp, f.hpMax) + '"></i>';
      mp.className = "lp-bar lp-bar-mp"; mp.innerHTML = '<i style="width:' + pct(f.mp, f.mpMax) + '"></i>';
      li.append(name, hp, mp);
      return li;
    }));
    const foes = $("lp-team-foes");
    if (fresh || foes.children.length !== fight.foes.length) foes.replaceChildren.apply(foes, fight.foes.map(function (f, i) { // Each creature, drawn once.
      const box = document.createElement("figure"), art = document.createElementNS("http://www.w3.org/2000/svg", "svg"), label = document.createElement("figcaption"), bar = document.createElement("div");
      box.dataset.click = "aim:" + i;
      art.setAttribute("viewBox", "0 0 200 200"); art.innerHTML = E.draw(f.body, f.colour); // Art authored in enemies.js; colours are the server's checked #rrggbb.
      bar.className = "lp-bar lp-bar-foe"; bar.innerHTML = "<i></i>";
      box.append(art, label, bar);
      return box;
    }));
    fight.foes.forEach(function (f, i) {
      const box = foes.children[i];
      box.dataset.down = String(f.hp <= 0); box.dataset.aim = String(i === target);
      box.querySelector("figcaption").textContent = f.name + " · Lv " + f.level + " · " + f.hp + "/" + f.hpMax;
      box.querySelector(".lp-bar i").style.width = pct(f.hp, f.hpMax);
    });
  }

  // ---- In a party: every friend's Lumi beside yours (on the couch, everyone's), in her own colours and clothes.
  function drawCrew() {
    const box = $("lp-crew"), on = Boolean(party) && (couching() || inParty()) && TABS[tab] === "dungeon" && !browsing;
    const crew = on ? party.members.filter(function (m) { return couching() || m.user !== party.you; }) : [];
    const team = teamFight();
    room.dataset.crew = String(crew.length);
    room.dataset.couchCrew = String(on && couching());
    box.hidden = !crew.length;
    const signature = JSON.stringify(crew.map(function (m) { return [m.user, m.name, m.pet, m.glow, m.colour, m.outfit, m.fainted, team ? team.at : null]; }));
    if (signature === lastCrew) return;
    lastCrew = signature;
    box.replaceChildren.apply(box, crew.map(function (m) {
      const figure = document.createElement("figure"), name = document.createElement("figcaption");
      const seat = team ? team.lumis.findIndex(function (f) { return f.user === m.user; }) : -1;
      figure.className = "lp-crew-lumi"; figure.dataset.ghost = String(m.fainted); figure.dataset.turn = String(seat >= 0 && team.at === seat);
      figure.innerHTML = '<svg viewBox="0 0 320 300" aria-hidden="true">' + lumiMarkup(m) + "</svg>"; // Authored markup; the name goes in as text below.
      name.textContent = (m.pet && m.pet !== "Lumi" ? m.pet + " · " : "") + m.name;
      figure.appendChild(name);
      return figure;
    }));
  }

  // ---- The party browser: every open party, full screen, each with its Lumis (party-ui.js browserScene).
  function browse() { browsing = { at: 0 }; partiesAsked = 0; picks[tab] = 0; askParties(); }

  function browserList() { return openParties.filter(function (p) { return !p.mine && p.phase === "lobby"; }); }

  function drawBrowser() {
    const on = Boolean(snap && browsing && TABS[tab] === "dungeon" && !trip() && !party);
    room.dataset.browse = String(on);
    if (!on) { if (browsing && party) browsing = null; return; }
    if (Date.now() - partiesAsked > 4000) askParties(); // It keeps watching while open.
    const open = browserList();
    browsing.at = Math.max(0, Math.min(browsing.at, open.length - 1));
    $("gm").dataset.walking = "true"; $("lp-dungeon").style.display = "";
    lastDungeon = ""; lastGarden = ""; lastSkills = ""; lastAlbum = "";
    $("lp-dungeon").innerHTML = Pu.browserScene(openParties, { at: browsing.at, lumi: lumiMarkup }); // Authored markup; every name is escaped by party-ui.js.
  }

  function browserKey(action) { // The arrows in the browser move over the cards, and the list's cursor with them (its join rows are in the same order), so Enter joins.
    const count = browserList().length, move = { left: -1, right: 1, up: -1, down: 1 }[action]; // One column of cards: every arrow steps one.
    if (move === undefined || !count) return false;
    browsing.at = Math.max(0, Math.min(count - 1, browsing.at + move)); picks[tab] = browsing.at;
    render(); return true;
  }

  function drawList(current) { // Rebuild the buttons only when the rows change, so focus and the remote's place survive a refresh.
    const signature = TABS[tab] + "|" + current.map(function (row) { return row.key + row.name + row.hint; }).join("|");
    const keys = TABS[tab] + "|" + current.map(function (row) { return row.key; }).join("|");
    if (signature !== lastList && keys === lastKeys && list.children.length === current.length) { // The same rows with new words (a countdown, a count):
      lastList = signature; // change the words where they stand, so a button being pressed is never swapped for another mid-press.
      Array.from(list.children).forEach(function (button, index) {
        const row = current[index], text = button.lastChild;
        button.firstChild.textContent = row.icon; text.firstChild.nodeValue = row.name; text.lastChild.textContent = row.hint;
      });
    } else if (signature !== lastList) {
      lastList = signature; lastKeys = keys;
      list.replaceChildren.apply(list, current.map(function (row, index) {
        const button = document.createElement("button"), icon = document.createElement("span"), text = document.createElement("span"), hint = document.createElement("small");
        button.type = "button"; button.dataset.lpRow = row.key;
        icon.textContent = row.icon; icon.setAttribute("aria-hidden", "true");
        text.append(document.createTextNode(row.name), hint); hint.textContent = row.hint; // The name stays a text node of its own, so it can be changed in place.
        button.append(icon, text);
        button.addEventListener("click", function () { picks[tab] = index; choose(); }); // Mouse and touch act directly.
        return button;
      }));
    }
    const boardHasIt = (walking() && focus === "board" && (play.board || play.cursor !== undefined)) || (gardening() && gardenFocus === "board" && (!drawer || paging())) || (treeing() && skillFocus === "board") || (albuming() && albumFocus === "board"); // Enter would act on the puzzle or the plot, not the list.
    Array.from(list.children).forEach(function (button, index) {
      const chosen = index === picks[tab] && !boardHasIt;
      if (chosen && button.getAttribute("aria-pressed") !== "true" && list.scrollHeight > list.clientHeight) button.scrollIntoView({ block: "nearest" }); // Long lists follow the remote.
      button.setAttribute("aria-pressed", String(chosen));
    });
  }

  function render() { // Refresh the room, meters and list without flooding the remote's status line.
    if (!snap) return;
    if (trip() && TABS[tab] !== "dungeon") changeTab(TABS.indexOf("dungeon")); // A trip (even one begun on another device) keeps Lumi Dungeons in the dungeon.
    const rooming = syncRoom(); // Room mode on or off, and the tab to match.
    const pet = snap.pet, now = Date.now(), scene = sceneNow(), mood = R.mood(pet), task = pet.task;
    room.dataset.palette = pet.glow;
    $("lp-name").textContent = pet.name || "Lumi";
    room.dataset.night = String(scene.night);
    room.dataset.sleep = String(scene.asleep || snap.pet.faint_until > serverNow());
    room.dataset.mood = mood.sad ? "sad" : "fine";
    $("lp-moments").textContent = pet.moments.toLocaleString();
    $("lp-memory").textContent = MEMORIES[pet.memory] || "Our first hello";
    $("lp-level").textContent = pet.level + (snap.progress.next ? " · " + pet.exp + "/" + snap.progress.next + " EXP" : " · the very top");
    const resting = pet.faint_until > serverNow(); // Napping off a lost fight: free in ten minutes.
    R.METERS.forEach(function (meter) { // Each meter is a bar and a number; low ones change colour.
      const bar = $("lp-" + meter), value = Math.round(pet[meter]);
      bar.style.width = value + "%"; bar.parentNode.setAttribute("aria-valuenow", String(value));
      bar.parentNode.dataset.low = String(pet[meter] < R.LOW);
      $("lp-" + meter + "-n").textContent = value;
    });
    const health = snap.health, share = Math.round(100 * health.hp / health.max); // Her health: a bar, and "42/90".
    $("lp-health").style.width = share + "%"; $("lp-health").parentNode.setAttribute("aria-valuenow", String(share));
    $("lp-health").parentNode.dataset.low = String(share < R.LOW);
    $("lp-health-n").textContent = health.hp + "/" + health.max;
    $("lp-clock").textContent = scene.night ? "Moonlight hours" : "A little daytime glow";
    const left = task ? R.countdown(task.finishesAt - serverNow()) : "";
    $("lp-state").textContent = task ? (task.kind === "nap" ? "Napping · " + left : "Playing · " + left)
      : snap.fight || teamFight() ? "In a fight!" : exploring() ? "Exploring · floor " + exploring().depth : resting ? "Napping off a fight · " + R.countdown(pet.faint_until - serverNow())
      : !health.out && health.hp < health.max && mendsIn() > 0 ? "Mending · " + R.countdown(mendsIn())
      : scene.asleep ? "Dreaming in good company" : mood.need ? NEEDS[mood.need] : "Happy to be here with you";
    $("lp-pet-title").textContent = (pet.name || "Lumi") + (scene.asleep ? ", a glowing fairy cat, sleeping peacefully" : ", a glowing fairy cat, awake");
    if (task && task.kind === "play") room.dataset.action = "play"; // She keeps playing for as long as the toy lasts.
    else if (now >= effectUntil) { delete room.dataset.action; $("lp-effect").textContent = scene.asleep ? "z z z" : mood.sad ? "…" : "♡"; }
    tabButtons.forEach(function (button, index) { button.setAttribute("aria-selected", String(index === tab)); });
    syncPlay();
    syncMini(); // Before the list is made: a minigame that just began or ended changes what the list offers.
    const current = rows();
    picks[tab] = Math.min(picks[tab], Math.max(0, current.length - 1)); // A list that shrank keeps a valid selection.
    drawList(current);
    const pick = current[picks[tab]];
    drawHouse(TABS[tab] === "home" && pick && pick.preview);
    drawOutfit(TABS[tab] === "closet" && pick && pick.wear);
    drawDungeon();
    if (APP === "lumi-pet") drawRoomBits(rooming); // After the dungeon's keys: room mode's own.
    drawGarden();
    drawSkills();
    drawAlbum();
    drawBrowser();
    drawPad();
    drawFight();
    drawCrew();
    drawTint();
    drawCouch();
    const doing = snap.fight ? "fighting " + snap.fight.enemy.name : task ? BUSY[task.kind] : scene.asleep ? "dreaming" : mood.need ? "needs " + mood.need : "content";
    const report = "Lumi · " + doing + " · " + (tab === ROOM ? Ru.room(roomAt.room).name.toLowerCase() : roomCard ? roomCard.name : tabButtons[tab].textContent) + ": " + (pick ? pick.name : "nothing yet") + " · Enter to choose";
    if (report !== lastReport) { G.report(report); lastReport = report; }
  }

  function announceDone() { // A nap or a play that finished (even while away) is mentioned once, when first seen.
    const done = snap.pet.done;
    if (!done || done.at === lastDone) { if (lastDone === null) lastDone = 0; return; }
    const first = lastDone === null; lastDone = done.at;
    if (first && serverNow() - done.at > 60000) return; // Old news on opening the page stays quiet.
    say(done.kind === "nap" ? "A stretch, a blink, a happy little hello. Lumi feels rested." : "What a game! Lumi flops down, very pleased with the " + item(done.item).name.toLowerCase() + ".");
    effect("cuddle", "✧ ♡");
  }

  var refreshFails = 0, refreshAfter = 0; // Failed asks in a row, and when the next may go (ms): a struggling server gets room to recover.
  function refresh() { // Ask the server how Lumi is; a failure keeps the last picture and tries again later.
    if (picking) return Promise.resolve(); // Nobody's Lumi is chosen yet: nothing to ask about.
    if (Date.now() < refreshAfter) return Promise.resolve(); // Still waiting out the last failure: keep the last picture.
    return G.api(API + "state").then(function (data) { refreshFails = 0; refreshAfter = 0; take(data); announceDone(); welcomeHome(); render(); }).catch(function () {
      refreshFails += 1;
      refreshAfter = Date.now() + Math.min(30000, 1000 * Math.pow(2, refreshFails)); // 2 s, 4 s, 8 s... at most 30 s between tries.
      if (!snap) say("Lumi can't be reached just now. She'll be here when the connection is back.");
    });
  }

  function greet() { // A real moment with Lumi, not just opening the page, counts as today's visit.
    if (visited) return;
    visited = true;
    G.api(API + "visit", {}).then(function (data) {
      $("lp-coins").textContent = data.coins.toLocaleString(); snap.coins = data.coins;
      if (data.paid) say($("lp-message").textContent + " +" + data.paid + " coins for visiting today.");
    }).catch(function () { /* Lumi is just as happy either way */ });
  }

  function choose() { // Act on the selected row: the server's words if it refuses.
    if (!snap) return;
    const row = rows()[picks[tab]];
    if (!row) return;
    if (row.local) { pendingConfirm = null; row.local(); lastList = ""; render(); return; } // Nothing to ask the server: it works at once, even while an answer is on its way.
    if (row.cycle && !row.send) { setCycle(row, 1); return; } // The co-op fight's Aim row: Enter aims at the next creature.
    if (busy) return; // One request at a time.
    if (!row.send) { say(row.note || "That's already in the room."); return; }
    if (row.ask && pendingConfirm !== row.key) { pendingConfirm = row.key; say(row.ask); render(); return; } // Try before you buy: the first press shows it, the second buys it.
    if (row.confirm && pendingConfirm !== row.key) { pendingConfirm = row.key; say("Sure? Choose it once more to go. " + row.hint + "."); return; } // Nothing is lost to one stray press.
    submit(row);
  }

  function setCycle(row, by) { // ◀ ▶ (or Enter on the Aim row) in the co-op fight: the next creature to aim at, or Lumi to give to.
    const to = row.next(by);
    if (row.cycle === "aim") aim = to; else ally = to;
    lastList = ""; render();
  }

  function submit(row) { // Send one choice to the server, one request at a time, and show what came of it.
    if (row.url === "garden") { gardenSend(row.send); return; } // The garden's choices have their own words (garden-ui.js said).
    if (row.url === "party" || (row.url === "dungeon/act" && inParty() && party.run)) { partyAct(row.send); return; } // Out with friends: every dungeon choice is the party's (a step is a vote).
    if (row.url === "party-new") { partyNew(row.send.mode); return; }
    if (row.url === "party-join") { partyJoin(row.send.code); return; }
    if (busy) return;
    pendingConfirm = null;
    busy = true;
    G.api(API + (row.url || "act"), row.send).then(function (data) {
      take(data);
      flip = {}; // What was being tried on is now either bought or put back.
      if (row.url && row.url.indexOf("dungeon") === 0) { // A step in the dungeon: the room's line, and a fight if she walked into one.
        const run = data.dungeon.run;
        lastList = ""; picks[tab] = 0;
        say(run.say);
        if (data.fight && data.fight.status === "active") effect("play", "!");
        else effect(run.over === "defeat" ? "rest" : "cuddle", { treasure: "✦", vault: "✧ ✦ ✧", key: "🗝", game_win: "✧ ✦", home: "⌂", descend: "⤓" }[run.event.scenario] || "·");
        tellLive(JSON.stringify(run.event));
        greet(); return;
      }
      if (row.url) { // A fight began, or a turn was played: say what just happened, from the fight's own log.
        const fight = data.fight;
        battleView = null;
        say(fight.log.slice(row.url === "battle" ? 0 : -3).join(" "));
        if (fight.status !== "active") {
          finished = fight; snap.fight = null; picks[tab] = 0;
          if (fight.dungeon && snap.dungeon.run) { say(fight.log.slice(-2).join(" ") + " " + snap.dungeon.run.say); tellLive(JSON.stringify(snap.dungeon.run.event)); } // What it meant for the trip.
        }
        else if (row.url === "battle") picks[tab] = 0;
        effect(fight.status === "victory" ? "play" : fight.status === "defeat" ? "rest" : "cuddle", fight.status === "victory" ? "✧ ✦ ✧" : fight.status === "defeat" ? "z z z" : "✦");
        greet(); return;
      }
      const send = row.send, thing = send.item ? item(send.item) : null;
      if (send.action === "cuddle") { say(CUDDLES[data.pet.moments % CUDDLES.length]); effect("cuddle", "♡ ♡"); }
      else if (send.action === "feed") { say(thing.name + "! Lumi munches happily" + (thing.boost ? " and sparkles with a little " + thing.boost + " for her next adventure." : ".")); effect("snack", "✦"); }
      else if (send.action === "play") { say("Lumi pounces on the " + thing.name.toLowerCase() + ". " + thing.minutes + " minutes of play!"); effect("play", "✧ ✦"); }
      else if (send.action === "drink") { say("Glug glug! Lumi has " + data.health.hp + " of " + data.health.max + " HP."); effect("snack", "✦"); }
      else if (send.action === "nap") { say("Lumi curls up on the cushion. Five minutes of little star dreams."); effect("rest", "z z z"); }
      else if (send.action === "wake") { say("A stretch, a blink, a happy little hello. Lumi settles beside you."); effect("rest", "☀"); }
      else if (send.action === "decorate") { say(data.said ? "Bought and put out: " + row.label + ". " + data.said.paid + " coins well spent." : row.label + " is out. Lumi approves."); effect("cuddle", "✧"); }
      else if (send.action === "capsule") { say("The capsule pops open: " + data.said.name + "! (" + data.said.rarity + ")" + (data.said.duplicate ? " Lumi already has one, so it pays back " + data.said.buyback + " coins." : "")); effect("play", "✧ ✦ ✧"); } // The list stays on this capsule's row, so Enter opens the same kind again.
      else if (send.action === "wear") { say(data.said ? "Bought: " + row.label + ". Lumi tries it on at once." : data.pet.outfit[row.wear[0]] === send.item ? "Lumi puts on the " + row.label.toLowerCase() + "." : "Lumi takes it off."); effect("cuddle", "✧"); }
      else if (send.action === "storyteller") {
        if (data.pet.storyteller !== "live") say("The storyteller is the library: the lines that came with the game.");
        else G.api(API + "dungeon/tale").then(function (found) { say(found.live ? "Live storytelling is on. Each dungeon moment gets a fresh line from " + (found.live === "claude" ? "Claude, with your own key." : "BFGguf.") : "Live storytelling needs your own Claude key (Preferences) or BFGguf. Until then the library tells the story."); }).catch(function () { /* the setting is saved either way */ });
      }
      else if (send.action === "learn") { say("Lumi learned " + (row.label || row.name) + "!"); effect("play", "✧ ✦"); }
      else if (send.action === "equip") { say(data.pet.moves.includes(send.item) ? (row.label || row.name) + " is ready for the next fight." : (row.label || row.name) + " is put away."); }
      else if (send.action === "mod") { say(send.item ? (data.said ? "Bought and fitted: " : "Fitted: ") + row.name + "." : "That slot is empty now."); effect("cuddle", "⚙"); }
      else if (send.action === "potions") { say("One potion, tucked away. Lumi carries " + data.pet.potions + "."); }
      else if (send.action === "heal") { say("A stretch and a shake. Lumi is up and ready."); effect("rest", "☀"); }
      else if (send.action === "respec") { say("Every skill point is back. Her trees start afresh."); battleView = null; }
      else if (send.action === "buy") { say("Bought: " + thing.name + ". " + data.said.paid + " coins well spent."); effect("cuddle", "✧"); }
      else if (send.action === "unpack") { const got = data.said; say("Box open! " + got.name + (got.put_out ? ": Lumi puts it out at once." : ". Something's already there, so she tucks it away for now (Home).") + (got.left ? " " + got.left + " boxes to go." : " That's the last box. She's all moved in!")); effect("play", "📦"); }
      else if (send.action === "name") { say("Hello, " + data.pet.name + "! What a lovely name."); effect("cuddle", "♡"); }
      else if (send.action === "paint") { tint = null; picks[tab] = 1; say("Fresh paint! I love it."); effect("cuddle", "✧ 🎨"); }
      else if (send.action === "colour") { tint = null; picks[tab] = 1; say(data.pet.colour ? "Ooh, " + data.pet.colour.toUpperCase() + "! That's her very own colour now." : "Back to her glow's own colours."); effect("cuddle", "✧ ✦"); }
      else if (send.action === "stat") { const names = snap.battle.rules.stat_names; say(names[send.item] + " is " + data.progress.stats[send.item] + " now · " + data.progress.stat_points.left + " points left."); effect("cuddle", "↑"); }
      else if (send.action === "stats_auto") { const names = snap.battle.rules.stat_names, placed = data.said.placed; say("Auto placed them: " + Object.keys(placed).map(function (key) { return "+" + placed[key] + " " + names[key].toLowerCase(); }).join(", ") + "."); effect("play", "✨"); }
      else if (send.action === "auto_stats") { say(data.progress.stat_points.auto ? "Auto is on: new points place themselves as she levels." : "Auto is off: you place every new point yourself."); }
      else if (send.action === "stats_reset") { say("Every point is back to place again" + (data.said.paid ? " (" + data.said.paid + " coins)." : ", free this once.")); }
      else if (send.action === "kit") { const kit = snap.battle.rules.kit[send.item]; say("Bought: " + kit.name + ". Lumi carries " + data.progress.kit[send.item] + "."); effect("cuddle", kit.icon); }
      if (["cuddle", "feed", "play", "nap", "wake"].includes(send.action)) greet(); // Shopping and colours aren't a visit; time together is.
    }).catch(function (refused) { say(refused.message); }).then(function () { busy = false; render(); });
  }

  function onKey(action, controller, remote, meta) { // Keyboard, TV remote, gamepad, and the on-screen arrow pad. Everywhere: ◀ ▶ change tab, ▲ ▼ or ⏮ ⏭ move through the list, Enter chooses.
    // meta.held: a direction that free walking (onHold) is also moving her by (common.js, gamepad.js).
    if (couching()) { couchKey(action, controller, remote); return; } // A couch party: every remote is its own person.
    if (picking) { pickPlayer(action, controller); return; } // Who's playing? waits for someone's OK.
    if (player && Date.now() - player.at > player.idle * 1000) { forgetPlayer("Nobody pressed anything for a while. Who's playing?"); return; } // A long-idle TV asks again.
    if (player) { player.at = Date.now(); keepPlayer(); } // Still here: the idle clock starts over.
    // In the dungeon the arrows are Lumi's own: they walk her from room to room, or work the puzzle in front of her.
    // The list is then reached with ⏮ ⏭ (Prev/Next, or Page Up/Down, or [ ]), and ⏯ drinks a potion.
    if (!snap) return;
    const arrow = ["left", "right", "up", "down"].indexOf(action) >= 0;
    if (action === "next" || action === "prev" || action === "play" || arrow) pendingConfirm = null; // Only a second Enter confirms.
    if (roomOn() && syncRoom() && roomKey(action, arrow, meta)) return; // Room mode: the arrows walk her flat; in a card, ⏮ ⏭ go back.
    if (TOWN && (arrow || action === "next" || action === "prev")) { townMove(action); return; } // Lumi Town: every arrow walks her (it has no other tabs to change to).
    if (albuming()) { albumKey(action); return; } // So has the Collection's gallery.
    if (room.dataset.browse === "true" && arrow && browserKey(action)) return; // The party browser: arrows move over the cards.
    if (treeing()) { skillsKey(action); return; } // The skill tree has its own keys too: the arrows move over the skills.
    if (gardening()) { gardenKey(action); return; } // The garden has its own keys: the arrows walk the plots, Play is the press.
    if ((action === "next" || action === "prev") && trip()) { // On a trip: the list's cursor (the arrows walk, and the other tabs wait at home).
      const count = rows().length;
      if (count) picks[tab] = (picks[tab] + (action === "prev" ? count - 1 : 1)) % count;
      focus = "list"; render(); return;
    }
    if (action === "next" || action === "prev") { changeTab((tab + (action === "prev" ? TABS.length - 1 : 1)) % TABS.length); render(); return; } // The next category.
    if (action === "play") { drinkPotion(); return; }
    if (walking()) {
      if (busy) return;
      const board = play.board || play.cursor !== undefined; // Crates, lamps or cards are in front of her.
      if (arrow && board && watching()) { say("It's " + holderName() + "'s turn. Yours comes round if they don't solve it."); return; } // A party's puzzle: only the holder moves it.
      if (arrow && board) { focus = "board"; boardKey(action); render(); return; }
      if (arrow && exploring().facing) { // A key or rune door has no board: up and down just move through its list.
        if (action === "up" || action === "down") { const count = rows().length; picks[tab] = (picks[tab] + (action === "up" ? count - 1 : 1)) % count; render(); }
        return;
      }
      if (arrow) { walk(action); return; }
      if (action === "enter" && board && focus === "board") { boardKey("enter"); render(); return; }
    }
    if (action === "left" || action === "right") { // A row's own options: its next choice, tried on before anything is bought.
      const row = rows()[picks[tab]], by = action === "left" ? -1 : 1;
      if (row && row.cycle) { setCycle(row, by); return; }
      if (row && row.turn) { row.turn(by); return; }
      if (row && row.options) flip[row.flipKey] = Pk.turn(row.options, row.shown, by);
    } else if (action === "up" || action === "down") {
      const count = rows().length;
      if (count) picks[tab] = (picks[tab] + (action === "up" ? count - 1 : 1)) % count;
    } else if (action === "enter") { choose(); return; }
    render();
  }
  G.onAction(onKey, { directEnter: function () { return true; } });
  if (G.onBack) G.onBack(function () { return roomOn() && closeCard(); }); // Escape, Backspace or a pad's B closes a card (and only then leaves the game).
  if (G.onHold) G.onHold(onHold); // Held arrows, WASD and a panel's pad: free walking in room mode (elsewhere the loop just stops at once).
  if (window.MPad) window.MPad.start(function (action, who, meta) { // A gamepad (Xbox, PlayStation, most USB pads; gamepad.js, loaded for Lumi Pet).
    G.sound.unlock();
    if (action === "back") { if (roomOn()) closeCard(); return; } // B / Circle: out of a menu, back to the room
    if (action === "home") return;                                // the Xbox / PS button: the browser's, not ours
    onKey(action, undefined, undefined, meta);                    // A, Start, LB / RB and the D-pad, as the keys
  }, onHold);                                                     // the stick (analog) and the D-pad walk her
  document.addEventListener("keydown", function (event) { // E uses the thing in reach, as Enter does (room mode only; never while typing).
    if ((event.key !== "e" && event.key !== "E") || event.ctrlKey || event.metaKey || event.altKey || !roomOn() || tab !== ROOM) return;
    if (event.target && event.target.matches && event.target.matches("input, select, textarea")) return;
    event.preventDefault(); onKey("enter");
  });

  // ---- Touch: the arrow pad and the press button (templates/games/lumi-pet.html), and installing this app (each of Lumi's three has its own manifest: web/app.py lumi_manifest).
  const touchy = window.matchMedia("(pointer: coarse), (max-width: 700px)"); // A touch screen, or a phone-sized window.
  const framed = window.top !== window; // Inside Media's game layer: the frame around it is the app, not this page.
  const inApp = window.matchMedia("(display-mode: standalone)").matches || window.matchMedia("(display-mode: fullscreen)").matches || window.navigator.standalone === true;
  let installOffer = null; // The browser's install prompt, once it gives us one.

  function drawPad() { // Show the touch controls where there are arrows to press: walking, and puzzle boards.
    const pad = $("lp-pad"), squeeze = $("lp-squeeze"), on = Boolean(snap) && touchy.matches, out = on && walking() ? exploring() : null;
    const board = Boolean(out) && Boolean(play.board || play.cursor !== undefined);
    pad.hidden = !out || (Boolean(out.facing) && !board); // A key or rune door has nothing for arrows: its list does it all.
    if (!pad.hidden) pad.querySelectorAll("[data-pad]").forEach(function (button) {
      const way = { up: "n", right: "e", down: "s", left: "w" }[button.dataset.pad];
      if (button.dataset.pad === "enter") button.hidden = !board; // The middle button turns a card, flips a lamp, or takes a crate step back.
      else button.disabled = !board && out.room.exits.indexOf(way) < 0; // Walking: only the ways there are.
    });
    squeeze.hidden = !(on && gardening() && !mini && !drawer);
  }

  $("lp-pad").addEventListener("pointerdown", function (event) { // On the way down, so a quick thumb is never missed.
    const button = event.target.closest("[data-pad]");
    if (!button || button.disabled) return;
    event.preventDefault();
    onKey(button.dataset.pad);
  });
  $("lp-squeeze").addEventListener("pointerdown", function (event) { event.preventDefault(); if (snap && gardening()) { pressOnce(); render(); } });
  touchy.addEventListener("change", function () { if (snap) render(); });
  window.addEventListener("resize", function () { if (snap && roomOn()) { lastHouse = ""; render(); } }); // Room mode: a new shape, a new pan.

  window.addEventListener("beforeinstallprompt", function (event) { // The browser can install this page as an app: keep its offer for the Settings row.
    event.preventDefault(); installOffer = event; lastList = "";
    if (snap) render();
  });
  window.addEventListener("appinstalled", function () { installOffer = null; say(NAME + " is on your home screen now."); });

  function installApp() { // Settings → Install (this app): the browser's own prompt if it gave one, otherwise where to find it.
    if (installOffer) {
      const asked = installOffer;
      installOffer = null; // A prompt can only be used once.
      asked.prompt();
      asked.userChoice.then(function (answer) { say(answer.outcome === "accepted" ? NAME + " is on your home screen now." : "No hurry. The row stays in Settings."); });
      return;
    }
    const iPhone = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    say(iPhone ? "Tap Share, then Add to Home Screen, and " + NAME + " gets an icon of its own." : "Open your browser's menu and choose Install app (or Add to Home screen). If it isn't there, " + NAME + " is already installed.");
  }

  $("lp-dungeon").addEventListener("click", function (event) { // Mouse and touch in the dungeon: the room and its boards answer a press (dungeon-art.js presses).
    const hit = event.target.closest("[data-click]");
    if (!hit || !snap || busy || !walking()) return; // (The garden listens for the press itself, below.)
    const what = hit.dataset.click.split(":"), kind = what[0], n = Number(what[1]);
    pendingConfirm = null;
    if (kind === "way") walk({ n: "up", e: "right", s: "down", w: "left" }[what[1]]); // A door: through it.
    else if (kind === "take") submit({ key: "take", url: "dungeon/act", send: { action: "take" } }); // The chest or the key.
    else if (kind === "cup") submit({ key: "cup-" + n, url: "dungeon/act", send: { action: "cup", picks: [n] } });
    else if (kind === "card") { play.cursor = n; focus = "board"; boardKey("enter"); } // Turn the card that was pressed.
    else if (kind === "lamp") { play.board = Object.assign({}, play.board, { cursor: n }); focus = "board"; boardKey("enter"); }
    else if (kind === "cell") { // Crates: one step towards the square that was pressed.
      const xy = what[1].split(",").map(Number), dx = xy[0] - play.board.at[0], dy = xy[1] - play.board.at[1];
      if (dx || dy) { focus = "board"; boardKey(Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up")); }
    }
    render();
  });

  houseArt.addEventListener("click", function (event) { if (roomOn()) roomPress(event); else doorPress(event); }); // Room mode's things and floor; otherwise her room's door, through to Lumi Town.
  houseArt.addEventListener("mousedown", function (event) { // A middle-click in room mode: she walks just there (and never uses anything).
    if (event.button !== 1 || !roomOn() || room.dataset.leaving === "true") return;
    event.preventDefault(); // (no browser autoscroll)
    pendingConfirm = null; walkTo(event);
  });
  houseArt.addEventListener("auxclick", function (event) { if (event.button === 1 && roomOn()) event.preventDefault(); }); // Nor a middle-click's other browser habits.
  $("lp-pet").addEventListener("click", function () { // Room mode: press Lumi herself for a cuddle (the bed's card has it too).
    if (!roomOn() || busy || room.dataset.leaving === "true") return;
    submit({ key: "cuddle", send: { action: "cuddle" } });
  });
  ["lp-way-left", "lp-way-right"].forEach(function (id) { // The arrows at the room's edges: walk through to the next room.
    $(id).addEventListener("click", function () {
      if (!roomOn() || !$(id).dataset.go) return;
      roomWalk(Ru.enter($(id).dataset.go, id === "lp-way-left" ? "right" : "left"));
    });
  });
  houseArt.addEventListener("click", townPress); // Lumi Town: its places, cabinets and tables.
  window.addEventListener("pageshow", function (event) { // Back from a game (or another app) with the browser's Back: the page comes back as it was left, mid-hop through a door.
    if (event.persisted) delete room.dataset.leaving; // She's here again, and the next press goes somewhere at once.
  });

  $("lp-team").addEventListener("click", function (event) { // Mouse and touch on a creature in the co-op fight: aim at it.
    const hit = event.target.closest("[data-click]");
    if (!hit || couching()) return;
    aim = Number(hit.dataset.click.split(":")[1]); lastList = ""; render();
  });

  $("lp-dungeon").addEventListener("pointerdown", function (event) { // Mouse and touch in the party browser: a card, then the same card again to join.
    const hit = event.target.closest("[data-click]");
    if (!hit || !browsing || hit.dataset.click.indexOf("join:") !== 0) return;
    const code = hit.dataset.click.slice(5), at = browserList().findIndex(function (p) { return p.code === code; });
    if (at === browsing.at) partyJoin(code); else { browsing.at = at; picks[tab] = at; render(); }
  });

  $("lp-dungeon").addEventListener("pointerdown", function (event) { // Mouse and touch in the garden. On the way down, not on the click: the drawing is
    const hit = event.target.closest("[data-click]"); // redrawn as flowers grow, and a click that began on a shape since replaced would be lost.
    if (hit && snap && gardening()) gardenClick(hit.dataset.click);
    else if (hit && snap && treeing()) skillClick(hit.dataset.click);
    else if (hit && snap && albuming()) albumClick(hit.dataset.click);
  });

  tabButtons.forEach(function (button, index) { // Tabs can be tapped too.
    button.addEventListener("click", function () { changeTab(index); render(); });
  });

  function tick() { // Once a second: count timers down, collect a finished nap or play, and look in on her now and then.
    if (!snap) return;
    const task = snap.pet.task;
    if ((task && serverNow() >= task.finishesAt) || Date.now() - polled > 15000) { polled = Date.now(); refresh(); }
    if (TOWN) { folkWander(); if (Date.now() - townPolled > 5000) townHere(); } // Lumi Town: the townsfolk, and who else is in town (every five seconds).
    if (gardening()) { // The garden: send the presses made this second, and look in more often so the numbers move.
      lumiWanders();
      flushPresses();
      if (!busy && !mini && Date.now() - polled > 5000) { polled = Date.now(); refresh(); }
    }
    render();
  }

  function visibility() { // Hidden frames stop timers and CSS motion; returning asks the server afresh.
    clearInterval(timer); timer = null;
    room.classList.toggle("lp-hidden", document.hidden);
    if (!document.hidden) { if (snap) refresh(); timer = setInterval(tick, 1000); }
  }
  document.addEventListener("visibilitychange", visibility);
  window.addEventListener("pagehide", function () { clearInterval(timer); });
  window.addEventListener("pageshow", visibility);

  // ---- Reminders: push notifications on this device (mnemerynne/push.py, static/sw.js). Each device opts in itself.
  function pushable() { // Needs a modern browser on HTTPS; paired TVs are left out.
    return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window && window.isSecureContext &&
      !document.documentElement.classList.contains("tv");
  }

  function keyBytes(text) { // The server's key arrives as base64url; the browser wants bytes.
    const plain = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(plain, function (c) { return c.charCodeAt(0); });
  }

  function tell(subscription) { // Hand the server this device's subscription and its clock offset, for quiet hours.
    const json = subscription.toJSON();
    return G.api(API + "push", { endpoint: json.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth, utc_offset: -new Date().getTimezoneOffset() });
  }

  function setReminders(value) { reminders = value; lastList = ""; render(); }

  function checkReminders() { // On opening: is this device already subscribed? If so, refresh it quietly.
    if (!pushable()) return;
    if (Notification.permission === "denied") { setReminders("blocked"); return; }
    setReminders("off");
    navigator.serviceWorker.getRegistration("/").then(function (registration) {
      return registration ? registration.pushManager.getSubscription() : null;
    }).then(function (subscription) {
      if (subscription) return tell(subscription).then(function () { setReminders("on"); });
    }).catch(function () { /* reminders are a nicety; Lumi plays on without them */ });
  }

  function toggleReminders() { // Must start from a tap or key press, which is what lets the browser ask permission.
    if (reminders === "on") {
      navigator.serviceWorker.getRegistration("/").then(function (registration) {
        return registration ? registration.pushManager.getSubscription() : null;
      }).then(function (subscription) {
        if (!subscription) return;
        return G.api(API + "push/off", { endpoint: subscription.endpoint }).then(function () { return subscription.unsubscribe(); });
      }).then(function () { setReminders("off"); say("Reminders are off on this device."); })
        .catch(function () { say("Reminders couldn't be switched off just now. Try again in a moment."); });
      return;
    }
    Notification.requestPermission().then(function (answer) {
      if (answer !== "granted") { setReminders(answer === "denied" ? "blocked" : "off"); say("No reminders, then. Lumi will wait for you here."); return; }
      return Promise.all([navigator.serviceWorker.register("/sw.js", { scope: "/" }), G.api(API + "push")]).then(function (both) {
        return navigator.serviceWorker.ready.then(function (registration) {
          return registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(both[1].key) });
        });
      }).then(tell).then(function () {
        setReminders("on"); say("Reminders are on for this device. Lumi will nudge you when she needs you, never at night.");
      });
    }).catch(function (problem) { say(problem && problem.status ? problem.message : "Reminders couldn't be switched on in this browser."); });
  }

  function bringOldMemories() { // Happy moments counted in this browser before pets lived on the account move over, once.
    if (snap.pet.imported) return Promise.resolve();
    let old = null;
    try { old = JSON.parse(localStorage.getItem(OLD_SAVE)); } catch (_) { /* nothing to bring */ }
    const kept = R.legacy(old), body = { action: "import", moments: kept.moments };
    if (kept.glow) body.glow = kept.glow;
    return G.api(API + "act", body).then(take).catch(function () { /* she starts fresh; nothing else depends on it */ });
  }

  function begin() { G.api(API + "state").then(function (data) { // First hello: load her, bring any old memories, then start the clock.
    take(data);
    return bringOldMemories();
  }).then(function () {
    if (location.hash === "#garden" && !has("garden")) { location.replace("/media/games/lumi-garden"); return; } // An old "flowers are ready" reminder or home-screen shortcut, from when the garden was a tab of Lumi Pet.
    if (trip()) { tab = TABS.indexOf("dungeon"); say(snap.fight ? snap.fight.log.slice(-3).join(" ") : trip().say); } // A trip was waiting: pick it up where it stopped.
    else if (snap.fight && has("battle")) { tab = TABS.indexOf("battle"); say(snap.fight.log.slice(-3).join(" ")); } // A fight was waiting: pick it up where it stopped.
    else if (TOWN) say(townAt ? Tw.place(townAt).hello : exploring() ? "Lumi Town! Lumi is out exploring: the Lantern & Paw is where she set off from." : "Lumi Town! Where shall we go?"); // Out of a door, or back from a game.
    else if (awayRows().length) say(exploring() ? "Lumi is out exploring. Lumi Dungeons is where she is." : "Lumi is in the middle of a fight, over in Lumi Dungeons."); // She is busy in another of her apps.
    else if (APP === "lumi-dungeons") say("Where shall we go? Pick a place to explore, or find a fight in the arena.");
    announceDone(); welcomeHome(); render(); // (A fresh trip home outranks a finished nap: it is said last.)
  }).catch(function () {
    say("Lumi can't be reached just now. She'll be here when the connection is back.");
  }); }

  // ---- Lumi Dungeons with friends (mnemerynne/lumiparty.py; rows in party-ui.js).
  function inParty() { return Boolean(party && !party.display && party.you !== null && has("dungeon")); } // A member here, on their own device.
  function couching() { return Boolean(party && party.display); } // This TV is a couch party's screen.
  function partyHolder() { const p = party && party.run && party.run.party.puzzle; return p && p.phase === "attempt" ? p.holder : null; }
  function holderName() { const m = Pu.member(party, partyHolder()); return m ? m.name : "someone"; }
  function watching() { return Boolean(party && party.run && partyHolder() !== null && (couching() || partyHolder() !== party.you)); } // Someone else works the puzzle: show their moves.

  function partyBoard(lock, was) { // The puzzle as the holder has left it so far: replay their moves on a fresh board.
    const puzzle = party && party.run && party.run.party.puzzle;
    if (!lock || !puzzle) return was;
    let board = lock.kind === "sokoban" ? Dg.sokobanStart(lock.rows) : Dg.lightsStart(lock);
    if (lock.kind === "sokoban") puzzle.moves.split("").forEach(function (move) { board = Dg.sokobanMove(board, move); });
    else puzzle.presses.forEach(function (cell) { board = Dg.lightsPress(board, cell); });
    const cursor = couching() ? couchCell[puzzle.holder] : was && was.cursor; // Lamps: the holder's own cursor.
    return Object.assign(board, { cursor: cursor || 0 });
  }

  function syncParty() { // After each snapshot: follow the party this person is in (Lumi Dungeons only), or stop.
    if (couching()) { if (party.run) snap.dungeon.run = party.run; return; } // The couch TV shows the party's trip, whoever's Lumi is underneath.
    if (!has("dungeon")) return;
    const code = snap.party ? snap.party.code : null;
    if (code !== partyCode) followParty(code);
    if (party && party.run && snap.dungeon.run && snap.dungeon.run.party) snap.dungeon.run = party.run; // The stream's copy is the freshest.
  }

  function followParty(code) { // Open (or close) the party's live feed: its stream, or asking every 2 s with a play pass.
    if (partyFeed) { if (partyFeed.close) partyFeed.close(); else clearInterval(partyFeed); }
    partyFeed = null; partyCode = code;
    if (!code) { party = null; return; }
    const url = API + "parties/" + code;
    if (player || !window.EventSource) { partyPoll(url); return; }
    partyFeed = partyStream(url, function () { if (partyCode === code) partyPoll(url); });
  }

  function partyPoll(url) { // Ask every two seconds (a play pass can't ride on a stream, and a stuck stream falls back to this).
    if (partyFeed && partyFeed.close) partyFeed.close();
    partyFeed = setInterval(function () { G.api(url).then(takeParty).catch(partyGone); }, 2000);
  }

  function partyStream(url, stuck) { // The party's live stream. If it says nothing for 6 s or drops for good (a proxy holding it back, too many open tabs), stuck() switches to asking.
    const feed = new EventSource(url + "/stream");
    let heard = false;
    const watchdog = setTimeout(function () { if (!heard) { feed.close(); stuck(); } }, 6000);
    feed.addEventListener("party", function (e) { heard = true; clearTimeout(watchdog); takeParty(JSON.parse(e.data)); });
    feed.addEventListener("end", function (e) { clearTimeout(watchdog); feed.close(); partyGone({ message: JSON.parse(e.data).reason }); });
    feed.addEventListener("error", function () { if (feed.readyState === 2) { clearTimeout(watchdog); stuck(); } }); // 2: closed for good
    return feed;
  }

  function partyGone(why) { // The party broke up, or this member left it: back to their own Lumi.
    if (why && why.status && why.status !== 404 && why.status !== 403) return; // A blip: the next ask tries again.
    if (why instanceof Error && !why.status) return; // No answer at all (connection reset, timeout): a blip too, not a goodbye. Leaving here used to reload state and reopen the feed at once, over and over.
    const was = party;
    followParty(null);
    try { sessionStorage.removeItem(COUCH_KEY); } catch (e) { /* nothing kept */ }
    if (was && why && why.message) say(why.message);
    if (snap && snap.dungeon.run && snap.dungeon.run.party) snap.dungeon.run = null;
    lastList = ""; refresh();
  }

  function takeParty(view) { // A fresh party view: draw it, fetch a fight that just began or ended, say what happened.
    if (!view || view.closed || (!view.display && view.you === null)) { partyGone({ message: view && view.closed ? "The party has broken up." : "" }); return; }
    const before = party;
    party = view; partyCode = view.code;
    if (snap) {
      if (view.run) snap.dungeon.run = view.run;
      else if (snap.dungeon.run && snap.dungeon.run.party) snap.dungeon.run = null; // The trip just ended: the lobby again.
    }
    const me = Pu.member(view, view.you), fighting = Boolean(snap && snap.fight && snap.fight.status === "active");
    const team = view.run && view.run.party.fight, own = Boolean(me && me.fighting && !team); // An older one-each fight is the member's own (fetched with her state).
    if (me && own !== fighting) refresh(); // Such a fight began (or ended) for this member: fetch it.
    const said = view.run ? view.run.say : (view.notes || []).slice(-1)[0];
    if (said && (!before || said !== (before.run ? before.run.say : (before.notes || []).slice(-1)[0]))) say(said);
    if (team) { // The co-op fight: say what just happened, from its log, as it happens.
      const log = team.log.join("|");
      if (log !== lastFightLog) { const was = lastFightLog.split("|"); say(team.log.filter(function (line) { return was.indexOf(line) < 0; }).slice(-3).join(" ") || team.log.slice(-1)[0]); lastFightLog = log; }
      if (team.at !== null && team.at === team.you && (!before || !before.run || !before.run.party.fight || before.run.party.fight.at !== team.at)) { G.sound.unlock(); G.sound.tone(784, .12, 0, .08); picks[tab] = 0; } // Your turn: a little chime.
    } else if (lastFightLog) {
      lastFightLog = ""; aim = null; ally = null;
      const result = view.run && view.run.party.last_fight;
      if (result && result.outcome === "victory") { say("Victory! Every Lumi gains " + result.exp + " EXP." + (result.levels[String(view.you)] ? " Lumi levels up!" : "")); effect("play", "✧ ✦ ✧"); refresh(); }
    }
    if (!before || before.revision !== view.revision) lastList = "";
    render();
  }

  function partyAct(body) { // One move at the party, from this member's own device.
    if (busy || !party) return;
    busy = true; pendingConfirm = null;
    G.api(API + "parties/" + party.code, body).then(function (view) {
      takeParty(view); picks[tab] = Math.min(picks[tab], Math.max(0, rows().length - 1));
      if (view.run && view.run.event && body.action !== "vote" && body.action !== "move") effect("cuddle", "✦");
    }).catch(function (refused) { say(refused.message); }).then(function () { busy = false; render(); });
  }

  function partyProgress(body) { // The holder's crates or lamps, as they go. One request at a time; the latest board wins.
    if (partySending) { partyNext = body; return; }
    partySending = true;
    G.api(API + "parties/" + party.code, body).then(takeParty).catch(function (refused) { say(refused.message); }).then(function () {
      partySending = false;
      if (partyNext) { const next = partyNext; partyNext = null; partyProgress(next); }
    });
  }

  function partyNew(mode) { // Make a party: online with me as host, or this TV's couch party.
    if (busy) return;
    busy = true;
    G.api(API + "parties", { mode: mode }).then(function (view) {
      if (view.display) { startCouch(view); say("Party on this TV! Everyone presses OK on their own remote to join. Code " + view.code + "."); }
      else { followParty(view.code); takeParty(view); say("Party " + view.code + " is open. Friends join from Lumi Dungeons → Dungeon, with this code."); }
    }).catch(function (refused) { say(refused.message); }).then(function () { busy = false; render(); });
  }

  function partyJoin(code) { // Join a party by its code.
    if (busy || !code) return;
    busy = true;
    G.api(API + "parties/" + encodeURIComponent(code), { action: "join" }).then(function (view) {
      browsing = null;
      followParty(view.code); takeParty(view); say("You're in " + view.code + "! Choose I'm ready when you are.");
    }).catch(function (refused) { say(refused.message); }).then(function () { busy = false; render(); });
  }

  function askCode() { // A keyboard's way in: type the code a friend shares.
    const code = window.prompt("The party's code:");
    if (code) partyJoin(code.trim().toUpperCase().replace(/[^0-9A-F]/g, ""));
  }

  function askParties() { // The parties waiting to be joined, looked up now and then while the places list is showing.
    if (Date.now() - partiesAsked < 8000) return;
    partiesAsked = Date.now();
    G.api(API + "parties").then(function (found) {
      openParties = found.parties;
      if (found.mine && found.mine !== partyCode) followParty(found.mine); // In a party already (made on another device): follow it.
      lastList = ""; render();
    }).catch(function () { /* the list simply stays as it was */ });
  }

  // ---- The couch: this TV is the party's screen; each remote is its holder's own member, with its own cursor here.
  function startCouch(view) {
    party = view; partyCode = view.code; couchCursor = {}; couchGuess = {}; couchCell = {}; couchConfirm = {}; couchSpot = {}; couchAim = {}; couchAlly = {};
    try { sessionStorage.setItem(COUCH_KEY, view.code); } catch (e) { /* it just won't survive a reload */ }
    if (picking) { picking = false; if ($("lp-who")) $("lp-who").hidden = true; }
    const url = API + "parties/" + view.code;
    partyFeed = partyStream(url, function () { if (couching() && partyCode === view.code) partyPoll(url); });
    if (has("dungeon")) changeTab(TABS.indexOf("dungeon"));
    takeParty(view);
  }

  function couchPress(controller, body) { // One remote's press, with what it chose, for the server to check and do.
    G.api(API + "parties/" + party.code + "/tv/input", Object.assign({ token: controller }, body)).then(takeParty).catch(function (refused) {
      if (refused.status === 404) partyGone(refused); else say(refused.message);
    });
  }

  function couchMenu(m) { // This person's menu. A new moment (lobby or trip, another room, a fight, a puzzle turn) starts their cursor at the top.
    const run = party.run, p = run && run.party;
    const spot = [party.phase, run ? run.at + ":" + (run.facing ? run.facing.at : "") : "", m.fight ? "fight" : "", p && p.fighting, p && p.puzzle ? p.puzzle.phase + p.puzzle.holder : "",
      p && p.fight ? "turn" + p.fight.at : ""].join("|"); // ...and a new turn in the co-op fight.
    if (couchSpot[m.user] !== spot) { couchSpot[m.user] = spot; couchCursor[m.user] = 0; couchConfirm[m.user] = null; }
    return Pu.couchMenu(party, m, { guess: couchGuess[m.user] || [], now: serverNow(), fight: snap ? fightCtx(couchAim[m.user], couchAlly[m.user]) : null });
  }

  function couchKey(action, controller, remote) { // A key at a couch party.
    if (!controller) { // The TV's own keyboard: Enter twice ends the party here.
      if (action !== "enter") return;
      if (!couchEndArmed) { couchEndArmed = true; say("Press Enter again to end the party on this TV."); return; }
      couchEndArmed = false;
      G.api(API + "parties/" + party.code + "/tv/close", {}).catch(function () { /* it ends by itself in time */ }).then(function () { partyGone({ message: "The party on this TV has ended." }); });
      return;
    }
    const m = party.members.find(function (x) { return x.remote === remote; });
    if (!m) { if (action === "enter") couchPress(controller, { action: "join" }); return; } // A new remote's OK sits it down.
    const puzzle = party.run && party.run.party.puzzle, holding = puzzle && puzzle.phase === "attempt" && puzzle.holder === m.user;
    if (holding && couchBoard(m, action, controller, puzzle)) return;
    if (action === "play") { // ⏯: pass the puzzle on, or drink a potion.
      const team = teamFight(), seat = team ? team.lumis.findIndex(function (f) { return f.user === m.user; }) : -1;
      if (team) { if (seat >= 0 && team.at === seat) couchPress(controller, { action: "fight", target: "potion", ally: seat, turn: team.turn }); return; }
      if (holding) couchPress(controller, { action: "pass" });
      else if (party.run && !m.fainted && !m.fight && m.hp < m.hpMax) couchPress(controller, { action: "potion" });
      return;
    }
    const menu = couchMenu(m);
    let at = Math.min(couchCursor[m.user] || 0, Math.max(0, menu.length - 1));
    if (action === "up" || action === "down") { couchCursor[m.user] = (at + (action === "up" ? menu.length - 1 : 1)) % menu.length; couchConfirm[m.user] = null; drawCouch(); return; }
    const row = menu[at];
    if (!row) return;
    if ((action === "left" || action === "right") && row.cycle) { couchCycle(m, row, action === "left" ? -1 : 1); return; }
    if ((action === "left" || action === "right") && row.turn) { couchPress(controller, row.turn(action === "left" ? -1 : 1)); return; }
    if (action !== "enter") return;
    if (row.cycle && !row.send) { couchCycle(m, row, 1); return; }
    if (row.local) { // A rune added or taken off: kept here until they try the code.
      const guess = couchGuess[m.user] || [];
      couchGuess[m.user] = row.local.erase ? guess.slice(0, -1) : guess.length < party.run.facing.lock.slots ? guess.concat([row.local.rune]) : guess;
      drawCouch(); return;
    }
    if (!row.send) return;
    if (row.confirm && couchConfirm[m.user] !== row.key) { couchConfirm[m.user] = row.key; say(m.name + ": press OK again to leave the party."); drawCouch(); return; }
    if (row.send.action === "rune") couchGuess[m.user] = [];
    couchPress(controller, row.send);
  }

  function couchCycle(m, row, by) { // A couch remote's ◀ ▶ in the co-op fight: their own aim, or who their item is for.
    const to = row.next(by);
    if (row.cycle === "aim") couchAim[m.user] = to; else couchAlly[m.user] = to;
    drawCouch();
  }

  function couchBoard(m, action, controller, puzzle) { // The holder's remote on the crates, lamps or cards. True if it was theirs to take.
    const lock = party.run.facing ? party.run.facing.lock : null;
    const way = { up: "n", down: "s", left: "w", right: "e" }[action];
    if (puzzle.kind === "sokoban" && lock) {
      if (action !== "enter" && !way) return false;
      const board = partyBoard(lock, null);
      const moves = action === "enter" ? board.moves.slice(0, -1) : Dg.sokobanMove(board, { n: "u", s: "d", w: "l", e: "r" }[way]).moves;
      if (moves !== board.moves) couchPress(controller, { action: "sokoban", moves: moves });
      return true;
    }
    if (puzzle.kind === "lights" && lock) {
      const at = couchCell[m.user] || 0, size = lock.size;
      if (way) { const to = Dg.cursorMove(at, way, size, size * size); if (to !== null) { couchCell[m.user] = to; play.board = partyBoard(lock, play.board); render(); } return true; }
      if (action === "enter") { couchPress(controller, { action: "lights", picks: puzzle.presses.concat([at]) }); return true; }
      return false;
    }
    if (puzzle.kind === "match") {
      const cards = party.run.game ? party.run.game.cards.length : 8, at = couchCell[m.user] || 0;
      if (way) { const to = Dg.cursorMove(at, way, 4, cards); if (to !== null) { couchCell[m.user] = to; play.cursor = to; render(); } return true; }
      if (action === "enter") { couchPress(controller, { action: "flip", picks: [at] }); return true; }
    }
    return false;
  }

  function drawCouch() { // One column per person: their Lumi's health, their vote, and their own menu with its cursor.
    const box = $("lp-couch"), on = couching();
    box.hidden = !on; list.hidden = on;
    if (!on) return;
    const columns = party.members.map(function (m) {
      const menu = couchMenu(m), at = Math.min(couchCursor[m.user] || 0, Math.max(0, menu.length - 1));
      return { m: m, menu: menu, at: at };
    });
    const signature = JSON.stringify([party.revision, columns.map(function (c) { return [c.m, c.at, c.menu.map(function (r) { return r.name + (r.hint || ""); }), couchConfirm[c.m.user] || "", couchAim[c.m.user], couchAlly[c.m.user]]; }), Math.floor(serverNow() / 1000)]);
    if (signature === box.dataset.drawn) return;
    box.dataset.drawn = signature;
    const head = document.createElement("p");
    head.className = "lp-couch-head";
    head.textContent = party.phase === "lobby" ? "Party on this TV · press OK on your own remote to join (" + party.members.length + "/3) · Enter on the keyboard twice ends it"
      : party.run ? Pu.status(party, serverNow()).name : "";
    const grid = document.createElement("div");
    grid.className = "lp-couch-grid";
    columns.forEach(function (c) {
      const col = document.createElement("section"), name = document.createElement("h3"), bar = document.createElement("div"), fill = document.createElement("i"), info = document.createElement("small");
      col.className = "lp-couch-col"; col.dataset.ghost = String(c.m.fainted);
      name.textContent = c.m.name + (c.m.host ? " ★" : "") + (party.phase === "lobby" ? (c.m.ready ? " ✓" : " …") : "");
      bar.className = "lp-bar"; fill.style.width = Math.round(100 * c.m.hp / Math.max(1, c.m.hpMax)) + "%"; bar.appendChild(fill);
      info.textContent = c.m.fainted ? "A little ghost" : c.m.hp + "/" + c.m.hpMax + " HP · " + c.m.satchel.coins + " coins" + (c.m.fight ? " · vs " + c.m.fight.enemy.name + " " + c.m.fight.enemy.hp + "/" + c.m.fight.enemy.hpMax : "");
      col.append(name, bar, info);
      if (c.m.fight && c.m.fight.log.length) { const said = document.createElement("small"); said.className = "lp-couch-log"; said.textContent = c.m.fight.log.slice(-1)[0]; col.appendChild(said); }
      const menu = document.createElement("ol");
      c.menu.forEach(function (row, i) {
        const li = document.createElement("li");
        li.textContent = row.icon + " " + (couchConfirm[c.m.user] === row.key ? "Press OK again: " : "") + row.name + (row.hint ? " · " + row.hint : "");
        if (i === c.at) li.setAttribute("aria-current", "true");
        menu.appendChild(li);
      });
      col.appendChild(menu);
      grid.appendChild(col);
    });
    box.replaceChildren(head, grid);
  }

  function resumeCouch() { // A TV page reopened mid-party (a reload, or Lumi's other app): pick the couch party up again.
    let code = null;
    try { code = sessionStorage.getItem(COUCH_KEY); } catch (e) { code = null; }
    if (!code || !TV) return false;
    G.api(API + "parties/" + code).then(function (view) { if (view.display) startCouch(view); else partyGone(null); }).catch(function () {
      try { sessionStorage.removeItem(COUCH_KEY); } catch (e) { /* nothing kept */ }
    });
    return true;
  }

  // ---- Who's playing? (mnemerynne/lumi_tv.py). Only on a shared TV whose owner has TV guests (game.html data-who).
  const rawApi = G.api; // Every request in the Lumi kit goes through G.api: a pass that stopped working sends the TV back to the picker.
  G.api = function (url, body) {
    return rawApi(url, body).catch(function (failure) {
      if (failure.status === 403 && player) forgetPlayer(failure.message); // Re-paired, guest removed, or too old.
      throw failure;
    });
  };

  function keepPlayer() { // The pass lives in this TV tab's session only, so Lumi's other two apps open on the same player.
    try { if (player) sessionStorage.setItem(PLAYER_KEY, JSON.stringify(player)); else sessionStorage.removeItem(PLAYER_KEY); } catch (e) { /* private mode: asked again next app */ }
  }

  function usePlayer(found) { // Play as the pass holder from now on (null: the TV's own Lumi).
    player = found; picking = false;
    G.setHeader(PLAYER_HEADER, player ? player.pass : null);
    keepPlayer();
    const box = $("lp-who");
    if (box) box.hidden = true;
    $("lp-player").hidden = !player;
    $("lp-player").textContent = player ? "Playing: " + player.name + "'s Lumi" : "";
    snap = null; finished = null; play = {}; lastList = lastDungeon = lastHouse = lastGarden = lastSkills = lastAlbum = lastStory = ""; visited = false; tab = 0; picks = freshPicks(); roomCard = null; // Nothing of the last player's Lumi stays on screen.
    begin();
  }

  function forgetPlayer(why) { // Back to the picker: the next person presses OK on their own remote.
    player = null; picking = true;
    G.setHeader(PLAYER_HEADER, null);
    keepPlayer();
    $("lp-player").hidden = true;
    showWho(why);
  }

  function showWho(why) { // The picker over the room: whose Lumi to show on this TV.
    picking = true;
    let box = $("lp-who");
    if (!box) {
      box = document.createElement("section");
      box.id = "lp-who"; box.className = "lp-who gm-panel"; box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Who's playing?");
      box.innerHTML = '<p class="gm-eyebrow">// ONE TV, EVERYONE&rsquo;S LUMI</p><h2>Who&rsquo;s playing?</h2>' +
        '<p class="lp-who-big">Press <b>OK</b> on your own remote to play your Lumi.</p>' +
        '<p class="lp-who-small">Or press Enter on a keyboard for this TV&rsquo;s own Lumi.</p><p class="lp-who-why" id="lp-who-why" aria-live="polite"></p>';
      $("gm").appendChild(box);
    }
    $("lp-who-why").textContent = why || "";
    box.hidden = false;
    G.report("Lumi · Who's playing? Press OK on your remote");
  }

  function pickPlayer(action, controller) { // A press while the picker shows: a remote's OK is a person; a keyboard's Enter is the TV's owner.
    if (action !== "enter") return;
    if (!controller) { usePlayer(null); return; } // No remote identity (keyboard, or the phone's cast controls): the TV's own Lumi.
    rawApi(API + "tv/player", { controller: controller }).then(function (found) {
      usePlayer({ pass: found.pass, name: found.name, owner: found.owner, idle: found.idle, at: Date.now() });
      say("Hello, " + found.name + "! Here is your Lumi.");
    }).catch(function (refused) { $("lp-who-why").textContent = refused.message; });
  }

  if (resumeCouch()) begin(); // A couch party in this TV tab: back to it (the TV's own Lumi underneath).
  else if (WHO) { // A shared TV: pick up the player this TV tab already has, or ask.
    let kept = null;
    try { kept = JSON.parse(sessionStorage.getItem(PLAYER_KEY)); } catch (e) { kept = null; }
    if (kept && kept.pass && Date.now() - kept.at < kept.idle * 1000) usePlayer(kept); else showWho("");
  } else begin();
  visibility();
  checkReminders();
  if ("serviceWorker" in navigator && window.isSecureContext && !framed) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () { /* she works without it; only installing and reminders need it */ });
})();
