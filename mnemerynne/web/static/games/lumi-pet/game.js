/* The companion's controls and presentation. Lumi lives on the account (mnemerynne/lumipet.py): this page only says
   what the person chose (which snack, which toy) and draws what the server answers. Her needs run down while away. */
(function () {
  "use strict";
  const G = window.MGame, R = window.LumiPetRules, H = window.LumiPetHouse, E = window.LumiPetEnemies, Bt = window.LumiPetBattle, W = window.LumiPetCloset, Pk = window.LumiPetPicker, Dg = window.LumiPetDungeon, Da = window.LumiPetDungeonArt;
  const $ = function (id) { return document.getElementById(id); };
  const room = $("lp-room"), list = $("lp-list"), houseArt = $("lp-house"), tabButtons = Array.from(document.querySelectorAll("[data-lp-tab]"));
  const API = "/api/media/games/lumi-pet/", OLD_SAVE = "mnemerynne-game:lumi-pet:companion";
  const TABS = tabButtons.map(function (button) { return button.dataset.lpTab; }); // care, pantry, toys, closet, home, battle, dungeon, shop, settings
  const MEMORIES = { cuddle: "A warm little cuddle", snack: "A shared snack", play: "Playtime together", rest: "A quiet moment together" };
  const NEEDS = { hunger: "Tummy rumbling. A snack would help.", energy: "So sleepy. A nap would help.", joy: "A little lonely. Stay and play?" };
  const BUSY = { nap: "napping", play: "playing" };
  const KEYS_HOME = "⏮ ⏭ change tab · ▲ ▼ choose<br>◀ ▶ flip a row's choices · Enter does it<br>Keyboard: Page Up / Page Down or [ ]<br>Escape / Back returns to Games";
  const KEYS_DUNGEON = "▲ ▼ ◀ ▶ walk, or work a puzzle<br>⏮ ⏭ choose from this list · Enter does it<br>⏯ drink a potion<br>Keyboard: Page Up / Page Down, P";
  const CUDDLES = ["Lumi leans into your hand. A tiny purr, just for you.", "Soft wings wrap around your fingers. We can stay like this a while."];
  let snap = null, offset = 0, tab = 0, picks = TABS.map(function () { return 0; }), busy = false, visited = false;
  let battleView = null, finished = null, lastFoe = ""; // Which Battle list is open, the fight that just ended, and the enemy last drawn.
  let lastOutfit = "";
  // The dungeon: the puzzle being worked on here, whether arrows drive it ("board") or the list, the finished trip
  // already read, a row waiting for a second press, and what the dungeon scene last drew.
  let play = {}, focus = "list", dismissedRun = null, pendingConfirm = null, lastDungeon = "";
  // Home and Closet: the spot or slot whose full list is open (null: one row each), the choice ◀ ▶ has flipped each
  // row to, and what the room last drew.
  let opened = { home: null, closet: null }, flip = {}, lastHouse = "";
  let effectUntil = 0, lastReport = "", lastList = "", lastDone = null, timer = null, polled = 0, reminders = "unsupported";

  function serverNow() { return Date.now() + offset; } // The server's clock, so timers agree across devices.

  function take(data) { // Keep the server's answer and how far this device's clock is from the server's.
    snap = data; offset = data.now - Date.now(); polled = Date.now();
    $("lp-coins").textContent = data.coins.toLocaleString();
  }

  function say(text) { $("lp-message").textContent = text; } // The live region announces it to screen readers too.

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

  function rows() { // What the selected tab offers, as plain rows: the list and the remote both read from here.
    const pet = snap.pet, c = snap.catalog, name = TABS[tab], asleep = sceneNow().asleep;
    if (name === "care") {
      const health = snap.health, sore = !health.out && health.hp < health.max; // Hurt, and at home to do something about it.
      const list = [
        { key: "cuddle", icon: "♡", name: "Cuddle", hint: "A little closeness", send: { action: "cuddle" } },
        asleep ? { key: "wake", icon: "☀", name: "Wake gently", hint: "A sleepy little hello", send: { action: "wake" } }
          : { key: "nap", icon: "☾", name: "Nap", hint: "Five minutes of star dreams · +35 energy" + (sore ? " · mends her twice as fast" : ""), send: { action: "nap" } },
      ];
      if (sore) { // A potion from her own pocket; bought food from the Pantry mends her too.
        const potion = { key: "drink", icon: "🧪", name: "Drink a potion (" + pet.potions + ")", hint: health.hp + " of " + health.max + " HP · whole again in " + R.countdown(mendsIn()) + ", sooner with a nap or bought food" };
        if (pet.potions) potion.send = { action: "drink" }; else potion.note = "No potions left. The Battle tab sells them.";
        list.push(potion);
      }
      return list;
    }
    if (name === "pantry") return c.snacks.concat(c.treats).filter(function (food) { return !food.price || pet.inventory[food.id]; }).map(function (food) {
      return { key: food.id, icon: food.icon, name: food.name, send: { action: "feed", item: food.id },
        hint: R.signed(food.fullness, "hunger") + " · " + R.signed(food.joy, "joy") + (food.price && food.fullness > 0 ? " · +" + food.fullness + "% health" : "") + " · " + (food.price ? pet.inventory[food.id] + " left" : "always in the bowl") };
    });
    if (name === "toys") return c.toys.filter(function (toy) { return !toy.price || pet.toys.includes(toy.id); }).map(function (toy) {
      return { key: toy.id, icon: toy.icon, name: toy.name, send: { action: "play", item: toy.id },
        hint: toy.minutes + " min · " + R.signed(toy.joy, "joy") + " · " + R.signed(toy.energy, "energy") };
    });
    if (name === "closet" || name === "home") { // One row per place on Lumi, or per spot in the house; ◀ ▶ flip its choices (picker-ui.js).
      const ctx = { snap: snap, open: opened[name], flip: flip, go: function (change) {
        if ("open" in change) { opened[name] = change.open; picks[tab] = change.open ? 1 : (change.pick || 0); }
      } };
      return name === "home" ? Pk.homeRows(ctx) : Pk.closetRows(ctx);
    }
    if (name === "battle") return battleRows();
    if (name === "dungeon") { // A dungeon fight and its result use the Battle lists; everything else is the dungeon's own.
      if ((snap.fight && snap.fight.status === "active") || finished) return battleRows();
      return Dg.rows({ snap: snap, play: play, dismissed: dismissedRun, go: dungeonGo });
    }
    if (name === "shop") return c.snacks.concat(c.treats, c.toys).filter(function (thing) { return thing.price && !pet.toys.includes(thing.id); }).map(function (thing) {
      const held = pet.inventory[thing.id] || 0, toy = c.toys.includes(thing);
      return { key: thing.id, icon: thing.icon, name: thing.name, send: { action: "buy", item: thing.id },
        hint: thing.price + " coins" + (toy ? " · a toy to keep" : held ? " · have " + held : "") };
    });
    return [ // settings
      { key: "glow", icon: "✦", name: "Glow: " + pet.glow[0].toUpperCase() + pet.glow.slice(1), hint: "◀ ▶ her colour, and her element in a fight: " + snap.battle.rules.glows[pet.glow], send: { action: "glow", glow: R.nextPalette(pet.glow) },
        turn: function (by) { submit({ key: "glow", send: { action: "glow", glow: Pk.turn(R.PALETTES, pet.glow, by) } }); } },
      { key: "storyteller", icon: "✎", name: "Storyteller: " + (pet.storyteller === "live" ? "Live" : "Library"), hint: "Who writes the dungeon's lines: the game's library, or a model as you go", send: { action: "storyteller" } },
      { key: "sound", icon: "♪", name: "Sound: " + (G.sound.isOn() ? "on" : "off"), hint: "Little chimes", local: function () { G.sound.toggle(); } },
    ].concat(reminders === "unsupported" ? [] : [ // Phones and computers only: a TV has nobody's pocket to buzz.
      { key: "reminders", icon: "✉", name: "Reminders: " + reminders, local: toggleReminders,
        hint: reminders === "blocked" ? "Allow notifications for this site in your browser first" : "A nudge on this device when Lumi needs you" },
    ]);
  }

  function changeTab(to) { // Open a category afresh: its sub-lists closed, nothing half-chosen.
    tab = to; opened = { home: null, closet: null }; flip = {}; battleView = null; pendingConfirm = null;
  }

  function battleRows() { // The Battle tab's lists (battle-ui.js); also used by the Dungeon tab while a fight is on.
    return Bt.rows({ snap: snap, view: battleView, finished: finished, now: serverNow(), countdown: R.countdown,
      go: function (view, pick, clear) { battleView = view; picks[tab] = pick || 0; if (clear) finished = null; } });
  }

  function exploring() { const run = snap.dungeon.run; return run && !run.over ? run : null; } // The trip in progress, if any.

  function syncPlay() { // Keep the page's copy of a puzzle in step with where Lumi is: start one at a door, drop it on leaving.
    const run = exploring(), lock = run && run.facing ? run.facing.lock : null, cards = run && !run.facing && run.game && run.game.kind === "match" && !run.room.done;
    const id = !run ? "" : lock ? run.id + ":" + run.facing.at + ":" + lock.kind + (lock.tries ? lock.tries.length : "") : cards ? run.id + ":" + run.at + ":match" : "";
    if (play.id === id) return;
    if (lock && lock.kind === "sokoban") { play = { id: id, board: Dg.sokobanStart(lock.rows) }; focus = "board"; }
    else if (lock && lock.kind === "lights") { play = { id: id, board: Dg.lightsStart(lock) }; focus = "board"; }
    else if (lock && lock.kind === "runes") { play = { id: id, guess: [] }; focus = "list"; }
    else if (cards) { play = { id: id, cursor: play.cursor || 0 }; focus = "board"; }
    else { play = { id: id }; focus = "list"; }
    picks[tab] = 0;
  }

  function dungeonGo(change) { // What a dungeon row that doesn't ask the server can do: move between board and list, edit a puzzle.
    const lock = exploring() && exploring().facing ? exploring().facing.lock : null;
    if (change.focus) focus = change.focus;
    if (change.dismiss) { dismissedRun = change.dismiss; picks[tab] = 0; }
    if (change.tabs) { changeTab(0); picks[tab] = 0; } // Off to the other tabs for a moment; the trip waits.
    if (change.undo) play.board = Dg.sokobanUndo(play.board);
    if (change.reset && lock) play.board = lock.kind === "sokoban" ? Dg.sokobanStart(lock.rows) : Dg.lightsStart(lock);
    if (change.rune && play.guess.length < lock.slots) play.guess = play.guess.concat([change.rune]);
    if (change.erase) play.guess = play.guess.slice(0, -1);
    lastDungeon = "";
  }

  function boardKey(action) { // An arrow or Enter on a puzzle board or the cards.
    const run = exploring(), lock = run.facing ? run.facing.lock : null;
    if (lock && lock.kind === "sokoban") { // Arrows push; Enter takes the last step back.
      play.board = action === "enter" ? Dg.sokobanUndo(play.board) : Dg.sokobanMove(play.board, { up: "u", down: "d", left: "l", right: "r" }[action]);
      if (Dg.sokobanSolved(play.board)) submit({ key: "solved", url: "dungeon/act", send: { action: "sokoban", moves: play.board.moves } });
      return;
    }
    const board = lock ? play.board : null, count = lock ? board.size * board.size : run.game.cards.length, width = lock ? board.size : 4;
    const at = lock ? board.cursor : play.cursor;
    if (action === "enter") { // Press the lamp, or turn the card, under the cursor.
      if (lock) {
        play.board = Object.assign(Dg.lightsPress(board, at), { cursor: at });
        if (Dg.lightsSolved(play.board)) submit({ key: "solved", url: "dungeon/act", send: { action: "lights", picks: play.board.presses } });
      } else submit({ key: "flip", url: "dungeon/act", send: { action: "flip", picks: [at] } });
      return;
    }
    const to = Dg.cursorMove(at, { up: "n", down: "s", left: "w", right: "e" }[action], width, count);
    if (to === null) return; // The edge of the board.
    if (lock) play.board = Object.assign({}, board, { cursor: to }); else play.cursor = to;
  }

  function walking() { // In the dungeon with the Dungeon tab open and no fight on: the arrows are Lumi's feet.
    return TABS[tab] === "dungeon" && exploring() && !(snap.fight && snap.fight.status === "active") && !finished;
  }

  function walk(action) { // One arrow, one room: up is north.
    const way = { up: "n", right: "e", down: "s", left: "w" }[action], run = exploring();
    if (run.room.exits.indexOf(way) < 0) { say("There's no way " + Dg.WAYS[way][0] + " from here."); return; }
    submit({ key: "go-" + way, url: "dungeon/act", send: { action: "move", way: way } });
  }

  function drinkPotion() { // The Play/Pause button: a potion, in a fight or between rooms.
    const fight = snap.fight && snap.fight.status === "active" ? snap.fight : null;
    if (fight) submit({ key: "potion", url: "battle/act", send: { battle: fight.id, turn: fight.turn, action: "potion" } });
    else if (exploring()) submit({ key: "potion", url: "dungeon/act", send: { action: "potion" } });
  }

  function drawDungeon() { // The dungeon scene over the house while she is out, and its line of health and satchel.
    const run = exploring(), art = $("lp-dungeon"), bar = $("lp-dhud");
    room.dataset.dungeon = String(Boolean(run));
    $("gm").dataset.walking = String(Boolean(snap && walking())); // The dungeon's own, taller right panel (game.css).
    $("lp-keys").innerHTML = snap && walking() ? KEYS_DUNGEON : KEYS_HOME; // Fixed text from this file.
    const here = TABS[tab] === "dungeon"; // A puzzle's panel covers the room (and Lumi) only while this tab can play it.
    room.dataset.puzzle = String(Boolean(here && run && (run.facing || (run.game && run.game.kind === "match" && !run.room.done))));
    art.style.display = run ? "" : "none"; bar.hidden = !run;
    if (!run) { lastDungeon = ""; return; }
    const hud = Dg.hud(run), signature = JSON.stringify([run.at, run.depth, run.rooms, run.facing, run.game, run.room, play, focus, run.satchel.keys, here]);
    $("lp-d-title").textContent = hud.title; $("lp-d-label").textContent = hud.label;
    $("lp-d-hp").style.width = hud.hp + "%"; $("lp-d-mp").style.width = hud.mp + "%";
    if (signature === lastDungeon) return;
    lastDungeon = signature;
    art.innerHTML = Da.scene(run, play, focus, here); // Markup authored in dungeon-art.js; colours are the server's checked #rrggbb.
  }

  function tellLive(moment) { // Live storyteller: the library's line is already showing; swap in a fresh one if it comes.
    if (snap.pet.storyteller !== "live" || !exploring()) return;
    G.api(API + "dungeon/tale", {}).then(function (data) {
      const run = snap.dungeon.run;
      if (data.line && run && JSON.stringify(run.event) === moment) { run.say = data.line; say(data.line); }
    }).catch(function () { /* the library line stays */ });
  }

  function drawHouse(preview) { // Draw the room: what the server says is placed, with the highlighted object tried on.
    const house = Object.assign({}, snap.pet.house);
    if (preview) house[preview[0]] = preview[1];
    const scenery = document.querySelector(".parallax"), backdrop = Boolean(scenery) && getComputedStyle(scenery).display !== "none"; // The neon theme's drifting cities.
    const signature = JSON.stringify(house) + backdrop;
    if (signature === lastHouse) return; // Redrawing would restart the window's scrolling, so only when it changed.
    lastHouse = signature;
    room.dataset.open = String(backdrop && house.view === "view-city"); // The windows are open onto the page's own scenery.
    houseArt.innerHTML = H.render(house, backdrop); // Markup authored in house.js; nothing a person typed goes into it.
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
    const fight = snap.fight || finished;
    room.dataset.fight = String(Boolean(fight));
    $("lp-fight").hidden = !fight;
    if (!fight) { lastFoe = ""; return; }
    const foe = fight.enemy, bars = Bt.bars(fight), signature = foe.id + foe.colour;
    if (signature !== lastFoe) { lastFoe = signature; $("lp-foe").innerHTML = E.draw(foe.body, foe.colour); } // Art authored in enemies.js.
    $("lp-foe").dataset.down = String(foe.hp <= 0);
    $("lp-f-lumi").textContent = bars.lumi.label; $("lp-f-foe").textContent = bars.foe.label;
    $("lp-f-hp").style.width = bars.lumi.hp + "%"; $("lp-f-mp").style.width = bars.lumi.mp + "%"; $("lp-f-foehp").style.width = bars.foe.hp + "%";
  }

  function drawList(current) { // Rebuild the buttons only when the rows change, so focus and the remote's place survive a refresh.
    const signature = TABS[tab] + "|" + current.map(function (row) { return row.key + row.name + row.hint; }).join("|");
    if (signature !== lastList) {
      lastList = signature;
      list.replaceChildren.apply(list, current.map(function (row, index) {
        const button = document.createElement("button"), icon = document.createElement("span"), text = document.createElement("span"), hint = document.createElement("small");
        button.type = "button"; button.dataset.lpRow = row.key;
        icon.textContent = row.icon; icon.setAttribute("aria-hidden", "true");
        text.textContent = row.name; hint.textContent = row.hint; text.appendChild(hint);
        button.append(icon, text);
        button.addEventListener("click", function () { picks[tab] = index; choose(); }); // Mouse and touch act directly.
        return button;
      }));
    }
    const boardHasIt = walking() && focus === "board" && (play.board || play.cursor !== undefined); // Enter would act on the puzzle, not the list.
    Array.from(list.children).forEach(function (button, index) {
      const chosen = index === picks[tab] && !boardHasIt;
      if (chosen && button.getAttribute("aria-pressed") !== "true" && list.scrollHeight > list.clientHeight) button.scrollIntoView({ block: "nearest" }); // Long lists follow the remote.
      button.setAttribute("aria-pressed", String(chosen));
    });
  }

  function render() { // Refresh the room, meters and list without flooding the remote's status line.
    if (!snap) return;
    const pet = snap.pet, now = Date.now(), scene = sceneNow(), mood = R.mood(pet), task = pet.task;
    room.dataset.palette = pet.glow;
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
      : snap.fight ? "In a fight!" : exploring() ? "Exploring · floor " + exploring().depth : resting ? "Napping off a fight · " + R.countdown(pet.faint_until - serverNow())
      : !health.out && health.hp < health.max && mendsIn() > 0 ? "Mending · " + R.countdown(mendsIn())
      : scene.asleep ? "Dreaming in good company" : mood.need ? NEEDS[mood.need] : "Happy to be here with you";
    $("lp-pet-title").textContent = scene.asleep ? "Lumi, a glowing fairy cat, sleeping peacefully" : "Lumi, a glowing fairy cat, awake";
    if (task && task.kind === "play") room.dataset.action = "play"; // She keeps playing for as long as the toy lasts.
    else if (now >= effectUntil) { delete room.dataset.action; $("lp-effect").textContent = scene.asleep ? "z z z" : mood.sad ? "…" : "♡"; }
    tabButtons.forEach(function (button, index) { button.setAttribute("aria-selected", String(index === tab)); });
    syncPlay();
    const current = rows();
    picks[tab] = Math.min(picks[tab], Math.max(0, current.length - 1)); // A list that shrank keeps a valid selection.
    drawList(current);
    const pick = current[picks[tab]];
    drawHouse(TABS[tab] === "home" && pick && pick.preview);
    drawOutfit(TABS[tab] === "closet" && pick && pick.wear);
    drawDungeon();
    drawFight();
    const doing = snap.fight ? "fighting " + snap.fight.enemy.name : task ? BUSY[task.kind] : scene.asleep ? "dreaming" : mood.need ? "needs " + mood.need : "content";
    const report = "Lumi · " + doing + " · " + tabButtons[tab].textContent + ": " + (pick ? pick.name : "nothing yet") + " · Enter to choose";
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

  function refresh() { // Ask the server how Lumi is; a failure keeps the last picture and tries again later.
    return G.api(API + "state").then(function (data) { take(data); announceDone(); render(); }).catch(function () {
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
    if (!snap || busy) return;
    const row = rows()[picks[tab]];
    if (!row) return;
    if (row.local) { pendingConfirm = null; row.local(); lastList = ""; render(); return; }
    if (!row.send) { say(row.note || "That's already in the room."); return; }
    if (row.confirm && pendingConfirm !== row.key) { pendingConfirm = row.key; say("Sure? Choose it once more to go. " + row.hint + "."); return; } // Nothing is lost to one stray press.
    submit(row);
  }

  function submit(row) { // Send one choice to the server, one request at a time, and show what came of it.
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
      else if (send.action === "capsule") { say("The capsule pops open: " + data.said.name + "! (" + data.said.rarity + ")"); effect("play", "✧ ✦ ✧"); picks[tab] = Math.max(0, rows().findIndex(function (r) { return r.key === data.said.capsule; })); }
      else if (send.action === "wear") { say(data.said ? "Bought: " + row.label + ". Lumi tries it on at once." : data.pet.outfit[row.wear[0]] === send.item ? "Lumi puts on the " + row.label.toLowerCase() + "." : "Lumi takes it off."); effect("cuddle", "✧"); }
      else if (send.action === "storyteller") {
        if (data.pet.storyteller !== "live") say("The storyteller is the library: the lines that came with the game.");
        else G.api(API + "dungeon/tale").then(function (found) { say(found.live ? "Live storytelling is on. Each dungeon moment gets a fresh line from " + (found.live === "claude" ? "Claude, with your own key." : "BFGguf.") : "Live storytelling needs your own Claude key (Preferences) or BFGguf. Until then the library tells the story."); }).catch(function () { /* the setting is saved either way */ });
      }
      else if (send.action === "learn") { say("Lumi learned " + row.name + "!"); effect("play", "✧ ✦"); }
      else if (send.action === "equip") { say(data.pet.moves.includes(send.item) ? row.name + " is ready for the next fight." : row.name + " is put away."); }
      else if (send.action === "mod") { say(send.item ? (data.said ? "Bought and fitted: " : "Fitted: ") + row.name + "." : "That slot is empty now."); effect("cuddle", "⚙"); }
      else if (send.action === "potions") { say("One potion, tucked away. Lumi carries " + data.pet.potions + "."); }
      else if (send.action === "heal") { say("A stretch and a shake. Lumi is up and ready."); effect("rest", "☀"); }
      else if (send.action === "respec") { say("Every skill point is back. Her trees start afresh."); battleView = null; }
      else if (send.action === "buy") { say("Bought: " + thing.name + ". " + data.said.paid + " coins well spent."); effect("cuddle", "✧"); }
      if (["cuddle", "feed", "play", "nap", "wake"].includes(send.action)) greet(); // Shopping and colours aren't a visit; time together is.
    }).catch(function (refused) { say(refused.message); }).then(function () { busy = false; render(); });
  }

  G.onAction(function (action) { // Keyboard and TV remote. Everywhere: ◀ ▶ change tab, ▲ ▼ or ⏮ ⏭ move through the list, Enter chooses.
    // In the dungeon the arrows are Lumi's own: they walk her from room to room, or work the puzzle in front of her.
    // The list is then reached with ⏮ ⏭ (Prev/Next, or Page Up/Down, or [ ]), and ⏯ drinks a potion.
    if (!snap) return;
    const arrow = ["left", "right", "up", "down"].indexOf(action) >= 0;
    if (action === "next" || action === "prev" || action === "play" || arrow) pendingConfirm = null; // Only a second Enter confirms.
    if ((action === "next" || action === "prev") && walking()) { // Exploring: the list's cursor (the arrows are busy walking).
      const count = rows().length;
      if (count) picks[tab] = (picks[tab] + (action === "prev" ? count - 1 : 1)) % count;
      focus = "list"; render(); return;
    }
    if (action === "next" || action === "prev") { changeTab((tab + (action === "prev" ? TABS.length - 1 : 1)) % TABS.length); render(); return; } // The next category.
    if (action === "play") { drinkPotion(); return; }
    if (walking()) {
      if (busy) return;
      const board = play.board || play.cursor !== undefined; // Crates, lamps or cards are in front of her.
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
      if (row && row.turn) { row.turn(by); return; }
      if (row && row.options) flip[row.flipKey] = Pk.turn(row.options, row.shown, by);
    } else if (action === "up" || action === "down") {
      const count = rows().length;
      if (count) picks[tab] = (picks[tab] + (action === "up" ? count - 1 : 1)) % count;
    } else if (action === "enter") { choose(); return; }
    render();
  }, { directEnter: function () { return true; } });

  tabButtons.forEach(function (button, index) { // Tabs can be tapped too.
    button.addEventListener("click", function () { changeTab(index); render(); });
  });

  function tick() { // Once a second: count timers down, collect a finished nap or play, and look in on her now and then.
    if (!snap) return;
    const task = snap.pet.task;
    if ((task && serverNow() >= task.finishesAt) || Date.now() - polled > 15000) { polled = Date.now(); refresh(); }
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

  G.api(API + "state").then(function (data) { // First hello: load her, bring any old memories, then start the clock.
    take(data);
    return bringOldMemories();
  }).then(function () {
    if (exploring()) { tab = TABS.indexOf("dungeon"); say(snap.fight ? snap.fight.log.slice(-3).join(" ") : exploring().say); } // A trip was waiting: pick it up where it stopped.
    else if (snap.fight) { tab = TABS.indexOf("battle"); say(snap.fight.log.slice(-3).join(" ")); } // A fight was waiting: pick it up where it stopped.
    announceDone(); render();
  }).catch(function () {
    say("Lumi can't be reached just now. She'll be here when the connection is back.");
  });
  visibility();
  checkReminders();
})();
