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

  const $ = function (id) { return document.getElementById(id); };
  let emu = null;               // the EmulatorJS instance, once its scripts are in
  let started = false;          // the game is running (its "start" event)
  let saves = game.saves || []; // [{slot, size, updated_at, has_shot}]: what the server holds for this game
  let sentSram = null;          // fingerprint of the game's own save the server has, so an unchanged one isn't sent
  let sramChain = Promise.resolve(true);   // sends of the game's own save, one after another
  let autoFrame = -1;           // the frame of the last automatic state: none again until the game moves on
  let leaving = false;

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
    const response = await fetch(SAVES.slice(0, -1), { credentials: "same-origin", cache: "no-store" });
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
    if (!bytes || !bytes.length) return sramChain;
    const mark = fingerprint(bytes);
    if (mark === sentSram) return sramChain;
    sentSram = mark;
    const copy = bytes.slice();   // the emulator's buffer can change under a slow send
    sramChain = sramChain.then(function () { return putBytes(SAVES + "sram", copy); }).then(function () {
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
    await putBytes(SAVES + slot, taken.state);
    if (taken.shot) await putBytes(SAVES + slot + "/shot", taken.shot, "image/png").catch(function () { /* the state is kept */ });
    await refreshSaves();
  }

  async function autoState(always) {   // the automatic state, unless nothing was played since the last one
    if (!started || !emu.gameManager) return;
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
    if (kept && kept.has_shot) { pic.src = SAVES + slot + "/shot?v=" + encodeURIComponent(kept.updated_at); pic.alt = ""; }
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
      const state = await getBytes(SAVES + slot);
      if (!state) throw new Error("it's gone");
      emu.gameManager.loadState(state);
      status("Loaded " + SLOT_NAMES[slot]);
      closeSlots();
    } catch (err) { status("Couldn't load that state: " + err.message); }
  }

  async function doDelete(slot) {
    try {
      const response = await fetch(SAVES + slot, { method: "DELETE", credentials: "same-origin" });
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

  async function boot(resume) {   // the choice is the click browsers want before a page may play sound
    $("rp-start").hidden = true;
    status("Loading…");
    let sram = null, auto = null;
    try {
      [sram, auto] = await Promise.all([getBytes(SAVES + "sram"), resume ? getBytes(SAVES + "auto") : null]);
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
    });
    // The game's own save, put back before the game boots: games read it once, at the start.
    emu.on("saveDatabaseLoaded", function (FS) {
      if (sram) { try { writeFile(FS, sramPathBeforeBoot(), sram); } catch (_) { /* checked again at start */ } }
    });
    emu.on("saveSaveFiles", function (bytes) { sendSram(bytes); });
    emu.on("saveState", function () { openSlots("save"); });   // the emulator's own buttons open our slots
    emu.on("loadState", function () { openSlots("load"); });
    emu.on("start", function () {
      started = true;
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
  async function leave() {   // the game's own save and an automatic state reach the server first
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
    location.href = "/media/retro";
  }

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

  const auto = saveOf("auto");
  $("rp-continue").hidden = !auto;
  if (auto) $("rp-continue-when").textContent = "(" + when(auto.updated_at) + ")";
  $("rp-fresh").textContent = auto ? "Start over" : "▶ Start";
  $("rp-start").hidden = false;
  (auto ? $("rp-continue") : $("rp-fresh")).focus();
})();
