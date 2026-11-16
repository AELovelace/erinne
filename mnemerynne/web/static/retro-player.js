/* Media → Retro's player (templates/retro_play.html, web/retro.py player_page).
 *
 * EmulatorJS (static/vendor/emulatorjs) plays the game; this file starts it and keeps every save on the server,
 * so a game goes on from any browser:
 *  - the game's own save (what the cartridge's battery kept): put back before the game boots, and sent each time
 *    the emulator writes it (every 30 seconds while it changes, and on leaving);
 *  - save states: Save and Load (in the bar here, or the emulator's own buttons) open four slots, each kept with a
 *    screenshot;
 *  - an automatic state every few minutes of play and on leaving, which Continue starts from next time.
 * Nothing here talks to anything but this server; the page's Content-Security-Policy makes sure of it.
 */
(function () {
  "use strict";

  const game = JSON.parse(document.getElementById("rp-data").textContent);   // web/retro.py player_page
  const DATA = "/static/vendor/emulatorjs/";
  // The scripts EmulatorJS's own loader.js would load, in its order (python/fetch_emulatorjs.py keeps these).
  const SCRIPTS = ["emulator.js", "nipplejs.js", "shaders.js", "storage.js", "gamepad.js", "GameManager.js",
    "compression.js"];
  const SAVES = "/api/media/retro/saves/" + game.id + "/";
  const AUTO_EVERY_MS = 3 * 60 * 1000;     // an automatic state this often, while the game is being played
  const SLOT_NAMES = { auto: "Automatic", 1: "Slot 1", 2: "Slot 2", 3: "Slot 3", 4: "Slot 4" };
  // Controllers: player 1 keeps EmulatorJS's own keys and pad buttons; players 2, 3 and 4 get the same pad buttons
  // (EmulatorJS leaves them unmapped), so a second controller on a TV plays player 2 straight away. Each pad joins
  // the next free player when it's first pressed. (2026-11-14, Bluetooth controllers on the TV boxes.)
  const PAD = ["BUTTON_2", "BUTTON_4", "SELECT", "START", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "BUTTON_1",
    "BUTTON_3", "LEFT_TOP_SHOULDER", "RIGHT_TOP_SHOULDER", "LEFT_BOTTOM_SHOULDER", "RIGHT_BOTTOM_SHOULDER", "LEFT_STICK",
    "RIGHT_STICK", "LEFT_STICK_X:+1", "LEFT_STICK_X:-1", "LEFT_STICK_Y:+1", "LEFT_STICK_Y:-1", "RIGHT_STICK_X:+1",
    "RIGHT_STICK_X:-1", "RIGHT_STICK_Y:+1", "RIGHT_STICK_Y:-1"];
  const KEYS = ["x", "s", "v", "enter", "up arrow", "down arrow", "left arrow", "right arrow", "z", "a", "q", "e", "tab", "r",
    "", "", "h", "f", "g", "t", "l", "j", "k", "i"];   // player 1's keyboard, as EmulatorJS's initControlVars has it
  // Nintendo's pads have A on the right and B at the bottom (the SNES's Y on the left, X on top): the other way round
  // from an Xbox pad's letters, which EmulatorJS's map follows. On those consoles a controller plays by place, as
  // RetroArch does: its bottom button (standard 0) is the console's B, the right one (1) its A, the left (2) its Y,
  // the top (3) its X. (Doll, 2026-11-15: Zero Mission's A and B were the wrong way round.)
  const NINTENDO = { nes: true, snes: true, gb: true, gbc: true, gba: true };   // media/retro.py's console keys
  const BY_LABEL = { 0: "BUTTON_2", 1: "BUTTON_4", 8: "BUTTON_1", 9: "BUTTON_3" };   // EmulatorJS input (B, Y, A, X) -> pad button: its own map
  const BY_PLACE = { 0: "BUTTON_1", 1: "BUTTON_3", 8: "BUTTON_2", 9: "BUTTON_4" };   // the same inputs, by where the buttons sit
  const byPlace = NINTENDO[game.console] === true;
  function padButton(i) { return byPlace && BY_PLACE[i] ? BY_PLACE[i] : PAD[i]; }   // the pad button for EmulatorJS input i

  // EmulatorJS keeps each game's controls in the browser once it has run, and they win over defaultControllers: a
  // Nintendo game played before keeps the letter map. A player whose face buttons are still exactly that map moves to
  // the map by place; one somebody set up differently in the Controls menu is left alone.
  function byPlaceForPlayedGames() {
    if (!byPlace || !emu || !emu.controls) return;
    let moved = false;
    for (let p = 0; p < 4; p++) {
      const mine = emu.controls[p];
      if (!mine) continue;
      [[0, 8], [1, 9]].forEach(function (pair) {   // B and A, then Y and X: each pair turned round only if it's stock
        const stock = pair.every(function (i) { return mine[i] && mine[i].value2 === BY_LABEL[i]; });
        if (!stock) return;
        pair.forEach(function (i) { mine[i].value2 = BY_PLACE[i]; });
        moved = true;
      });
    }
    if (moved) { try { emu.saveSettings(); } catch (_) { /* kept for this visit anyway */ } }
  }

  function controllers() {
    const one = {}, pad = {};
    PAD.forEach(function (_, i) {
      const button = padButton(i);
      one[i] = KEYS[i] ? { value: KEYS[i], value2: button } : { value2: button };
      pad[i] = { value2: button };
    });
    one[24] = { value: "1" }; one[25] = { value: "2" }; one[26] = { value: "3" };
    return { 0: one, 1: Object.assign({}, pad), 2: Object.assign({}, pad), 3: Object.assign({}, pad) };
  }

  const $ = function (id) { return document.getElementById(id); };
  let emu = null;               // the EmulatorJS instance, once its scripts are in
  let started = false;          // the game is running (its "start" event)
  let saves = game.saves || []; // [{slot, size, updated_at, has_shot}]: what the server holds for this game
  let sentSram = null;          // fingerprint of the game's own save the server has, so an unchanged one isn't sent
  let sramChain = Promise.resolve(true);   // sends of the game's own save, one after another
  let autoFrame = -1;           // the frame of the last automatic state: none again until the game moves on
  let leaving = false;
  let netGuest = false;         // playing in someone else's netplay room (retro-netplay.js): their game, not ours to save
  let member = null;            // an archive holding several ROMs: the one picked in "Which ROM?" (its saves are its own)

  // A save's address: the slot (or the list), for the ROM picked inside an archive if there is one.
  function saveUrl(path, extra) {
    const q = [];
    if (member) q.push("member=" + encodeURIComponent(member));
    if (extra) q.push(extra);
    return SAVES.slice(0, -1) + (path ? "/" + path : "") + (q.length ? "?" + q.join("&") : "");
  }

  function status(text) { $("rp-status").textContent = text || ""; }   // the bar's short message

  function fingerprint(bytes) {   // FNV-1a and the length: enough to tell a changed save from the one sent
    let h = 0x811c9dc5;
    for (let i = 0; i < bytes.length; i++) { h ^= bytes[i]; h = Math.imul(h, 0x01000193); }
    return (h >>> 0).toString(16) + ":" + bytes.length;
  }

  async function failure(response) {   // the server's words for a refusal, else its status
    try { const body = await response.json(); if (body && body.detail) return String(body.detail); } catch (_) { /* not JSON */ }
    return "the server answered " + response.status;
  }

  async function getBytes(url) {   // a save's bytes; null if there is none
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(await failure(response));
    return new Uint8Array(await response.arrayBuffer());
  }

  async function putBytes(url, bytes, type) {   // keeps bytes in a slot (or its screenshot)
    const response = await fetch(url, { method: "PUT", body: bytes, credentials: "same-origin",
      headers: { "Content-Type": type || "application/octet-stream" } });
    if (!response.ok) throw new Error(await failure(response));
  }

  async function refreshSaves() {   // the slots' sizes and times, after a change
    const response = await fetch(saveUrl(""), { credentials: "same-origin", cache: "no-store" });
    if (response.ok) saves = (await response.json()).saves;
  }

  function saveOf(slot) { return saves.find(function (s) { return s.slot === String(slot); }) || null; }

  function when(iso) {   // "today 14:05", "12 Nov 09:30"
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return d.toDateString() === new Date().toDateString() ? "today " + time
      : d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " + time;
  }

  // ------------------------------------------------------------- the game's own save
  function sendSram(bytes) {   // queued after any send still going; resolves true once the server has it
    if (netGuest) return sramChain.then(function () { return true; });   // a guest's game is the host's: kept by the host
    if (!bytes || !bytes.length) return sramChain;
    const mark = fingerprint(bytes);
    if (mark === sentSram) return sramChain;
    sentSram = mark;
    const copy = bytes.slice();   // the emulator's buffer can change under a slow send
    sramChain = sramChain.then(function () { return putBytes(saveUrl("sram"), copy); }).then(function () {
      status("Game saved");
      return true;
    }, function (err) {
      if (sentSram === mark) sentSram = null;   // send it again next time
      status("Couldn't save the game: " + err.message);
      return false;
    });
    return sramChain;
  }

  function writeFile(FS, path, bytes) {   // into the emulator's file system, making its folders first
    let at = "";
    path.split("/").slice(1, -1).forEach(function (part) {
      at += "/" + part;
      if (!FS.analyzePath(at).exists) FS.mkdir(at);
    });
    if (FS.analyzePath(path).exists) FS.unlink(path);
    FS.writeFile(path, bytes);
  }

  function sramPathBeforeBoot() {   // where RetroArch will look: /data/saves/<the core's folder>/<the ROM's name>.srm
    const base = game.rom.split("/").pop().replace(/\.[^.]*$/, "");
    return "/data/saves/" + (game.save_folder ? game.save_folder + "/" : "") + base + "." + (emu.saveFileExt || "srm");
  }

  // ---------------------------------------------------------------- states
  async function screenshot() {   // a PNG of the screen from RetroArch, or nothing within two seconds
    try {
      const shot = await Promise.race([emu.gameManager.screenshot(),
        new Promise(function (resolve) { setTimeout(function () { resolve(null); }, 2000); })]);
      return shot && shot.length ? shot : null;
    } catch (_) { return null; }
  }

  async function capture() {   // the state and the screen now, while the game runs (a screenshot needs a frame)
    return { state: emu.gameManager.getState(), shot: await screenshot() };
  }

  async function keepState(slot, taken) {   // a captured moment into a slot, then its screenshot; throws if it couldn't
    await putBytes(saveUrl(slot), taken.state);
    if (taken.shot) await putBytes(saveUrl(slot + "/shot"), taken.shot, "image/png").catch(function () { /* the state is kept */ });
    await refreshSaves();
  }

  async function autoState(always) {   // the automatic state, unless nothing was played since the last one
    if (!started || !emu.gameManager || netGuest) return;
    const frame = emu.gameManager.getFrameNum();
    if (!always && frame === autoFrame) return;
    autoFrame = frame;
    await keepState("auto", await capture());
  }

  // ----------------------------------------------------------------- slots
  let slotMode = "save", wasPlaying = false;
  let pending = null;   // Save: the moment Save was pressed, kept until a slot is picked

  function slotRow(slot) {   // one slot in the dialog: its screenshot, its time, and what can be done with it
    const kept = saveOf(slot);
    const row = document.createElement("li");
    row.className = "rp-slot";
    const pic = document.createElement(kept && kept.has_shot ? "img" : "div");
    pic.className = "rp-slot-shot";
    if (kept && kept.has_shot) { pic.src = saveUrl(slot + "/shot", "v=" + encodeURIComponent(kept.updated_at)); pic.alt = ""; }
    else pic.textContent = kept ? "💾" : "—";
    const words = document.createElement("div");
    words.className = "rp-slot-words";
    const name = document.createElement("strong");
    name.textContent = SLOT_NAMES[slot];
    const time = document.createElement("span");
    time.className = "muted small";
    time.textContent = kept ? when(kept.updated_at) : "Empty";
    words.append(name, time);
    const buttons = document.createElement("div");
    buttons.className = "rp-slot-buttons";
    if (slotMode === "save") {
      const here = button(kept ? "Replace" : "Save here", function () {
        if (kept && here.dataset.sure !== "1") { here.dataset.sure = "1"; here.textContent = "Replace it?"; return; }
        doSave(slot);
      });
      buttons.append(here);
    } else if (kept) {
      buttons.append(button("Load", function () { doLoad(slot); }));
    }
    if (kept) {
      const drop = button("Delete", function () {
        if (drop.dataset.sure !== "1") { drop.dataset.sure = "1"; drop.textContent = "Delete it?"; return; }
        doDelete(slot);
      });
      drop.className = "link small";
      buttons.append(drop);
    }
    row.append(pic, words, buttons);
    return row;
  }

  function button(text, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.addEventListener("click", onClick);
    return b;
  }

  function drawSlots() {   // the automatic state only shows for loading: saving goes into 1 to 4
    const list = $("rp-slot-list");
    list.replaceChildren();
    const slots = (slotMode === "load" ? ["auto"] : []).concat(game.slots);
    slots.forEach(function (slot) { list.append(slotRow(slot)); });
  }

  async function openSlots(mode) {   // pauses the game while the slots are open
    if (!started || !$("rp-slots").hidden) return;
    if (mode === "save") {   // what gets saved is this moment, not the one a slot is picked in
      try { pending = await capture(); }
      catch (err) { status("Couldn't save the state: " + err.message); return; }
    }
    slotMode = mode;
    $("rp-slots-title").textContent = mode === "save" ? "Save state" : "Load state";
    drawSlots();
    wasPlaying = !emu.paused;
    if (wasPlaying) emu.pause();
    $("rp-slots").hidden = false;
    const first = $("rp-slot-list").querySelector("button");
    if (first) first.focus();
  }

  function closeSlots() {
    $("rp-slots").hidden = true;
    if (wasPlaying && emu.paused) emu.play();
  }

  async function doSave(slot) {
    status("Saving to " + SLOT_NAMES[slot] + "…");
    try { await keepState(slot, pending); status("Saved to " + SLOT_NAMES[slot]); closeSlots(); }
    catch (err) { status("Couldn't save the state: " + err.message); drawSlots(); }
  }

  async function doLoad(slot) {
    status("Loading " + SLOT_NAMES[slot] + "…");
    try {
      const state = await getBytes(saveUrl(slot));
      if (!state) throw new Error("it's gone");
      emu.gameManager.loadState(state);
      if (window.RetroNetplay) window.RetroNetplay.loaded();   // a host's load: everyone starts again from it
      status("Loaded " + SLOT_NAMES[slot]);
      closeSlots();
    } catch (err) { status("Couldn't load that state: " + err.message); }
  }

  async function doDelete(slot) {
    try {
      const response = await fetch(saveUrl(slot), { method: "DELETE", credentials: "same-origin" });
      if (!response.ok && response.status !== 404) throw new Error(await failure(response));
      await refreshSaves();
      drawSlots();
    } catch (err) { status("Couldn't delete it: " + err.message); }
  }

  // ------------------------------------------------------------- start
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = function () { reject(new Error("couldn't load " + src.split("/").pop())); };
      document.head.appendChild(s);
    });
  }

  function loadStyle(href) {
    return new Promise(function (resolve) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      link.onload = link.onerror = resolve;   // without it the player still works, only plainer
      document.head.appendChild(link);
    });
  }

  function accent() {   // the theme's colour for the emulator's menus, when it's a plain #rrggbb
    const v = getComputedStyle(document.documentElement).getPropertyValue("--rose").trim();
    return /^#[0-9a-f]{6}$/i.test(v) ? v : "";
  }

  // EmulatorJS gives a controller a player only when its GamepadHandler announces it ("connected"), and that
  // handler looks at the browser's controllers the first time inside its own constructor, before EmulatorJS has
  // started listening. So a controller already there when the game loads (on a TV, the one that just picked the
  // game) was announced to nobody, never got a player, and every press of it was dropped ("Gamepad not set
  // anywhere"): Metroid's buttons did nothing while the remote worked. Each controller the handler knows that no
  // player has gets the next free one, as a newly connected one would. (2026-11-15)
  function seatPads() {
    const pads = emu && emu.gamepad && emu.gamepad.gamepads, seats = emu && emu.gamepadSelection;
    if (!pads || !seats || !seats.length) return;   // EmulatorJS hasn't built its players yet
    let changed = false;
    pads.forEach(function (pad) {
      if (!pad) return;
      const key = pad.id + "_" + pad.index;           // how EmulatorJS names a controller in its players
      if (seats.indexOf(key) >= 0) return;            // already playing
      const free = seats.indexOf("");
      if (free < 0) return;                            // four players already
      seats[free] = key;
      changed = true;
    });
    if (changed && typeof emu.updateGamepadLabels === "function") emu.updateGamepadLabels();   // its Controls menu shows it
  }

  // "Which ROM?": an archive holding several ROMs (an original and its hacks) would play whichever the emulator
  // unpacked first. Only the one picked goes through as it unpacks (EmulatorJS's checkCompression hands each file to
  // a callback: the other ROMs are left out, anything else goes through), so it's the only one the emulator sees.
  function onlyThePickedRom(e) {
    if (!member || !game.rom_exts) return;
    const exts = game.rom_exts;
    const unpack = e.checkCompression.bind(e);
    e.checkCompression = function (data, msg, fileCb) {
      if (typeof fileCb !== "function") return unpack(data, msg, fileCb);   // the core's own files
      return unpack(data, msg, function (name, bytes) {
        const ext = String(name).split(".").pop().toLowerCase();
        if (name !== member && exts.includes(ext)) return;                   // another ROM in the archive: left out
        return fileCb(name, bytes);
      });
    };
  }

  function chooseRom() {   // the archive's ROMs, a button each; picking one goes on to its own Continue / Start
    const box = $("rp-which");
    box.replaceChildren();
    const head = document.createElement("h2");
    head.textContent = "Which ROM?";
    const hint = document.createElement("p");
    hint.className = "muted small";
    hint.textContent = "This archive holds " + game.members.length + " ROMs. Each keeps its own saves.";
    box.append(head, hint);
    game.members.forEach(function (m) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "rp-which-rom secondary";
      b.dataset.member = m.name;
      const name = document.createElement("strong");
      name.textContent = m.title;
      const file = document.createElement("span");
      file.className = "muted small";
      file.textContent = m.name;
      b.append(name, file);
      if (m.original) {   // the server put it first: it looks like the original dump, not a hack
        const star = document.createElement("span");
        star.className = "rp-which-original small";
        star.textContent = "⭐ Original";
        name.append(" ", star);
      }
      const auto = (m.saves || []).find(function (sv) { return sv.slot === "auto"; });
      if (auto) {
        const tag = document.createElement("span");
        tag.className = "rp-which-tag small";
        tag.textContent = "▶ " + when(auto.updated_at);
        b.append(tag);
      }
      b.addEventListener("click", function () { pickRom(m); });
      box.append(b);
    });
    box.hidden = false;
    $("rp-continue").hidden = $("rp-fresh").hidden = $("rp-other").hidden = true;
    const first = box.querySelector("button");
    if (first) first.focus();
  }

  function pickRom(m) {   // this ROM: its saves, then the usual Continue / Start
    member = m.name;
    saves = m.saves || [];
    $("rp-which").hidden = true;
    $("rp-other").hidden = false;
    $("rp-fresh").hidden = false;
    showStart();
  }

  async function boot(resume) {   // the choice is the click browsers want before a page may play sound
    $("rp-start").hidden = true;
    status("Loading…");
    let sram = null, auto = null;
    try {
      [sram, auto] = await Promise.all([getBytes(saveUrl("sram")), resume ? getBytes(saveUrl("auto")) : null]);
      await loadStyle(DATA + "emulator.css");
      for (const name of SCRIPTS) await loadScript(DATA + "src/" + name);
    } catch (err) {
      status("Couldn't start: " + err.message);
      $("rp-start").hidden = false;
      return;
    }
    sentSram = sram ? fingerprint(sram) : null;
    // Its update check asks emulatorjs.org (only on localhost); the page's policy refuses that, so it isn't asked.
    window.EmulatorJS.prototype.checkForUpdates = function () {};
    emu = new window.EmulatorJS("#rp-game", {
      // The game's own core (a PS1 .chd plays on another one than the console's usual): EmulatorJS finds the
      // console from it for its controls and menus.
      gameUrl: game.rom, dataPath: DATA, system: game.core, gameName: game.title, color: accent(),
      startOnLoad: true, volume: 0.5, threads: false,
      biosUrl: game.bios || "",            // beside the game, under the name the core looks for
      externalFiles: game.files,           // a .cue's .bin tracks, beside it under the names it gives them
      // The game's own save goes to the emulator's file every 30 s while it changes; each write reaches us
      // (saveSaveFiles below) and the server.
      defaultOptions: { "save-save-interval": "30" },
      // Its exit button restarts the game in place, and netplay talks to emulatorjs.org: both hidden.
      buttonOpts: { exitEmulation: false, netplay: false },
      defaultControllers: controllers(),
    });
    setInterval(seatPads, 500);   // a controller that was already here when the game loaded still gets a player
    onlyThePickedRom(emu);
    // The game's own save, put back before the game boots: games read it once, at the start.
    emu.on("saveDatabaseLoaded", function (FS) {
      if (sram) { try { writeFile(FS, sramPathBeforeBoot(), sram); } catch (_) { /* checked again at start */ } }
    });
    emu.on("saveSaveFiles", function (bytes) { sendSram(bytes); });
    emu.on("saveState", function () { openSlots("save"); });   // the emulator's own buttons open our slots
    emu.on("loadState", function () { openSlots("load"); });
    emu.on("start", function () {
      started = true;
      byPlaceForPlayedGames();   // a Nintendo game's saved letter map, turned round (EmulatorJS has loaded it by now)
      $("rp-save").disabled = $("rp-load").disabled = false;
      fetch("/api/media/retro/played/" + game.id, { method: "POST", credentials: "same-origin" }).catch(function () {});
      const gm = emu.gameManager;
      const path = gm.getSaveFilePath();
      if (sram && path) {   // a zip's game is named by what's inside: the save then goes in now, and the game restarts
        let here = null;
        try { here = gm.FS.analyzePath(path).exists ? gm.FS.readFile(path) : null; } catch (_) { here = null; }
        if (!here || fingerprint(here) !== fingerprint(sram)) {
          writeFile(gm.FS, path, sram);
          gm.loadSaveFiles();
          gm.restart();
        }
      }
      if (auto) {
        setTimeout(function () { gm.loadState(auto); status("Continued from " + when(saveOf("auto").updated_at)); }, 50);
      } else status("");
      setInterval(function () { autoState(false).catch(function () { /* tried again next time */ }); }, AUTO_EVERY_MS);
    });
  }

  // -------------------------------------------------------------- leaving
  async function leave(then) {   // the game's own save and an automatic state reach the server first; then the shelves (or `then`: the TV's)
    if (typeof then !== "function") then = null;           // a click's event isn't a "then"
    if (leaving) return;
    leaving = true;
    status("Saving…");
    let ok = true;
    if (started) {
      try {
        await autoState(true);                                 // while it still runs: the screenshot needs a frame
        emu.pause();
        ok = await sendSram(emu.gameManager.getSaveFile());   // writes it out (and the event sends it too)
      } catch (err) { ok = false; status("Couldn't save: " + err.message); }
    }
    if (!ok) {
      const back = $("rp-back");
      if (back.dataset.sure !== "1") {   // a second press leaves anyway
        back.dataset.sure = "1";
        back.textContent = "← Leave without saving";
        leaving = false;
        emu.play();
        return;
      }
    }
    if (then) { leaving = false; then(); } else location.href = "/media/retro";
  }

  // For retro-tv.js (Retro on a TV: the remote, the controller's menu, and leaving when the TV says so).
  window.RetroPlayer = {
    started: function () { return started; }, emu: function () { return emu; }, leave: leave, openSlots: openSlots,
    pause: function () { if (emu && started) emu.pause(); }, resume: function () { if (emu && started) emu.play(); },
    // retro-netplay.js: "guest" in someone's room (no saving, no states of our own), "host" or null otherwise.
    netplay: function (role) {
      netGuest = role === "guest";
      $("rp-save").disabled = $("rp-load").disabled = netGuest || !started;
    },
    status: status,
    member: function () { return member; },   // retro-netplay.js: everyone in a room plays the same ROM
  };

  document.addEventListener("visibilitychange", function () {   // a phone switching apps may never come back
    if (document.visibilityState !== "hidden" || !started || leaving) return;
    try { sendSram(emu.gameManager.getSaveFile()); } catch (_) { /* tried again on the next write */ }
    autoState(false).catch(function () {});
  });

  // ---------------------------------------------------------------- the bar
  $("rp-back").addEventListener("click", leave);
  $("rp-save").addEventListener("click", function () { openSlots("save"); });
  $("rp-load").addEventListener("click", function () { openSlots("load"); });
  $("rp-slots-close").addEventListener("click", closeSlots);
  $("rp-slots").addEventListener("keydown", function (e) { if (e.key === "Escape") { e.stopPropagation(); closeSlots(); } });
  $("rp-full").addEventListener("click", function () {
    const page = $("rp");
    if (document.fullscreenElement) document.exitFullscreen().catch(function () {});
    else if (page.requestFullscreen) page.requestFullscreen().catch(function () {});
  });
  $("rp-continue").addEventListener("click", function () { boot(true); });
  $("rp-fresh").addEventListener("click", function () { boot(false); });

  // A game that can't start says why, instead of a start button that ends in the emulator's error.
  if (game.missing && game.missing.length) {
    status("Can't start: its .cue names " + game.missing.join(", ") + ", which isn't beside it on the share.");
    return;
  }
  if (game.needs_bios) {
    status("Can't start: this kind of " + game.console_name + " game needs a BIOS file (" + game.bios_names.join(", ") +
      ") in the " + game.console_name + " folder on the share.");
    return;
  }

  function showStart() {   // Continue (the automatic state) or a fresh start, for the game (or the ROM picked)
    const auto = saveOf("auto");
    $("rp-continue").hidden = !auto;
    if (auto) $("rp-continue-when").textContent = "(" + when(auto.updated_at) + ")";
    $("rp-fresh").textContent = auto ? "Start over" : "▶ Start";
    $("rp-start").hidden = false;
    (auto ? $("rp-continue") : $("rp-fresh")).focus();
  }
  $("rp-other").addEventListener("click", chooseRom);
  if (game.members && game.members.length > 1) {   // an archive of several ROMs: which one first
    $("rp-start").hidden = false;
    chooseRom();
  } else {
    showStart();
  }
})();
