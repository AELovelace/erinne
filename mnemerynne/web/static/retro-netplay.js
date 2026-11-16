/* Retro netplay: playing one game together, each in their own browser (Doll, 2026-11-15). Loaded on the player page
 * after retro-player.js; the server side is web/retro.py's websocket and media/netplay.py's rooms.
 *
 * Lockstep. Every browser runs the game; they stay identical because each frame's buttons are the same everywhere:
 *  - A player's own presses (whatever reaches EmulatorJS's gameManager.simulateInput for player 1: their pad, the
 *    keyboard, a TV remote) aren't applied at once. They're sent to everyone for frame now + DELAY, and kept here.
 *  - After each frame (Emscripten's Module.postMainLoop), every browser sends its presses for frame now + DELAY (an
 *    empty list too: "nothing changed" is news), then runs the next frame only once it has every player's presses
 *    for it, applying them as that player's controller (slot 0 = player 1, ...). Anyone behind holds everyone up:
 *    the emulator is paused until their message comes, and "Waiting for …" shows.
 *  - The host starts (and restarts) everyone from one save state: "start" with the delay, then the state itself as
 *    a binary message. Everyone loads it and counts frames from there. Someone joining a started room, the host
 *    loading a state, or a checksum that doesn't match (each browser sends one of its state every CHECK_EVERY
 *    frames) starts everyone again from the host's state: a resync.
 *  - A guest plays the host's game: retro-player.js doesn't save it for them (RetroPlayer.netplay("guest")).
 * EmulatorJS's own netplay is left off: its control syncing is broken by its own account (media/netplay.py).
 */
(function () {
  "use strict";

  const $ = function (id) { return document.getElementById(id); };
  const DELAYS = { 3: "Low (LAN)", 5: "Normal", 8: "Smooth (far away)" };   // frames of input delay the host picks
  const CHECK_EVERY = 300;            // frames between state checksums (5 s at 60 fps)
  const RESYNC_GAP_MS = 20000;        // the most often a mismatch restarts everyone
  const WAIT_SHOW_MS = 400;           // a stall this long says who it's waiting for
  const LIST_EVERY_MS = 3000;         // the room list, while the panel's open and we're in none

  const game = JSON.parse(document.getElementById("rp-data").textContent);
  const player = function () { return window.RetroPlayer || null; };

  const np = {
    ws: null, code: "", slot: -1, host: false, players: [], delay: 5, started: false,
    base: 0,                  // the emulator's frame number when play started from the shared state
    inputs: {},               // frame -> {slot: [[index, value], ...]}
    local: [],                // this player's presses since the last frame was sent
    sentUpTo: -1,             // the last frame whose presses went out
    applied: -1,              // the last frame whose presses were applied (a loop that didn't move a frame: nothing)
    epoch: 0,                 // which start this is: presses from before a resync are dropped
    gate: false,              // paused waiting for someone's presses
    waitingFor: -1, waitSince: 0,
    userPaused: false, internal: false,
    sums: {},                 // frame -> {slot: checksum}
    lastResync: 0, resyncs: 0,  // restarts sent (the host) or taken (a guest), for the panel and the tests
    checkEvery: CHECK_EVERY,
    hooked: false, origSim: null, origPlay: null, origPause: null,
  };

  /* -------------------------------------------------------------------- the panel */
  let panel = null, listTimer = 0;
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function buildPanel() {   // a dialog like the save slots (retro-tv.js moves around it the same way)
    panel = el("div", "rp-slots rp-together");
    panel.id = "rp-together";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "Play together");
    panel.hidden = true;
    panel.append(el("div", "rp-slots-box"));
    panel.addEventListener("keydown", function (e) { if (e.key === "Escape") { e.stopPropagation(); closePanel(); } });
    document.body.append(panel);
  }
  function openPanel() {
    if (!panel) buildPanel();
    panel.hidden = false;
    draw();
    const first = panel.querySelector("button, select");
    if (first) first.focus();
    refreshList();
  }
  function closePanel() {
    if (!panel || panel.hidden) return;
    panel.hidden = true;
    clearTimeout(listTimer);
    const b = $("rp-together-open");
    if (b) b.focus();
  }

  let openRooms = [], listError = "";
  async function refreshList() {   // the rooms open for this game (while the panel shows and we're in none)
    clearTimeout(listTimer);
    if (!panel || panel.hidden || np.ws) return;
    try {
      const r = await fetch("/api/media/retro/netplay/rooms?game=" + game.id, { credentials: "same-origin", cache: "no-store" });
      if (!r.ok) throw new Error("the server answered " + r.status);
      openRooms = (await r.json()).rooms;
      listError = "";
    } catch (err) { listError = "Couldn't list the rooms: " + err.message; }
    draw();
    listTimer = setTimeout(refreshList, LIST_EVERY_MS);
  }

  function draw() {
    if (!panel) return;
    const box = panel.querySelector(".rp-slots-box");
    const keep = document.activeElement && box.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    box.replaceChildren();
    box.append(el("h2", "", "👥 Play together"));
    if (!np.ws) {
      box.append(el("p", "muted small", "Each player opens this game in their own browser or TV. One opens a room; " +
        "the others join it here. Up to four players: the host is player 1."));
      const row = el("div", "rp-np-row");
      const pick = el("select");
      pick.dataset.key = "delay";
      pick.setAttribute("aria-label", "Input delay");
      Object.keys(DELAYS).forEach(function (d) {
        const o = el("option", "", DELAYS[d] + " · " + d + " frames");
        o.value = d;
        if (Number(d) === np.delay) o.selected = true;
        pick.append(o);
      });
      pick.addEventListener("change", function () { np.delay = Number(pick.value); });
      const open = el("button", "", "Open a room");
      open.type = "button";
      open.dataset.key = "open";
      open.addEventListener("click", function () { connect(""); });
      row.append(open, pick);
      box.append(row);
      box.append(el("h3", "small", "Rooms open for " + game.title));
      if (listError) box.append(el("p", "error small", listError));
      if (!openRooms.length) box.append(el("p", "muted small", "None yet."));
      const list = el("ul", "rp-slot-list");
      openRooms.forEach(function (r) {
        const li = el("li", "rp-np-room");
        li.append(el("span", "", r.host + "'s room · " + r.players + "/" + r.max + (r.started ? " · playing" : "")));
        const join = el("button", r.players >= r.max ? "secondary" : "", r.players >= r.max ? "Full" : "Join");
        join.type = "button";
        join.disabled = r.players >= r.max;
        join.dataset.key = "join-" + r.code;
        join.addEventListener("click", function () { connect(r.code); });
        li.append(join);
        list.append(li);
      });
      box.append(list);
    } else {
      box.append(el("p", "rp-np-code", "Room " + (np.code || "…")));
      const who = el("ul", "rp-slot-list");
      np.players.forEach(function (p) {
        const li = el("li", "rp-np-room");
        li.append(el("span", "", "Player " + p.player + " · " + p.name + (p.slot === 0 ? " (host)" : "") + (p.slot === np.slot ? " · you" : "")));
        if (np.host && p.slot !== 0) {
          const kick = el("button", "secondary small", "Let go");
          kick.type = "button";
          kick.dataset.key = "kick-" + p.slot;
          kick.addEventListener("click", function () { send({ t: "kick", slot: p.slot }); });
          li.append(kick);
        }
        who.append(li);
      });
      box.append(who);
      const row = el("div", "rp-np-row");
      if (np.host) {
        const go = el("button", "", np.started ? "↻ Resync everyone" : "▶ Start together");
        go.type = "button";
        go.dataset.key = "go";
        go.disabled = np.players.length < 2 || !player() || !player().started();
        go.title = np.players.length < 2 ? "Waiting for someone to join" : "Everyone starts from where you are now";
        go.addEventListener("click", function () { startAll("pressed"); });
        row.append(go);
      } else {
        row.append(el("span", "muted small", np.started ? "Playing with " + hostName() + "." : "Waiting for " + hostName() + " to start…"));
      }
      const quit = el("button", "secondary", np.host ? "Close the room" : "Leave the room");
      quit.type = "button";
      quit.dataset.key = "quit";
      quit.addEventListener("click", function () { disconnect(""); });
      row.append(quit);
      box.append(row);
      box.append(el("p", "muted small", "Input delay: " + np.delay + " frames. Everyone's game waits for whoever's behind."));
    }
    const foot = el("div", "rp-slots-foot");
    const close = el("button", "secondary", "Close (Esc)");
    close.type = "button";
    close.dataset.key = "close";
    close.addEventListener("click", closePanel);
    foot.append(close);
    box.append(foot);
    if (keep) { const again = box.querySelector('[data-key="' + keep + '"]'); if (again) again.focus(); }
  }
  function hostName() { const h = np.players.find(function (p) { return p.slot === 0; }); return h ? h.name : "the host"; }

  /* -------------------------------------------------------------------- the connection */
  function connect(code) {
    if (np.ws) return;
    const p = player();
    if (!p || !p.started()) { status("Start the game first, then open or join a room."); return; }
    const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host +
      "/api/media/retro/netplay/ws?game=" + game.id + (code ? "&room=" + encodeURIComponent(code) : "");
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    np.ws = ws;
    np.host = !code;
    ws.onmessage = function (e) { typeof e.data === "string" ? onText(e.data) : onBinary(new Uint8Array(e.data)); };
    ws.onclose = function (e) {
      if (np.ws !== ws) return;
      const why = e.code === 4403 ? "This game isn't open to you for playing together." : np.closedWhy || "The room's connection closed.";
      stop(why);
    };
    ws.onerror = function () { /* onclose says why */ };
    draw();
  }
  function send(obj) { if (np.ws && np.ws.readyState === 1) np.ws.send(JSON.stringify(obj)); }
  function disconnect(why) {
    const ws = np.ws;
    np.closedWhy = why || (np.host ? "You closed the room." : "You left the room.");
    if (ws) try { ws.close(); } catch (_) { /* gone */ }
  }
  function stop(why) {   // out of the room: the game goes on alone, saved as usual again
    np.ws = null; np.code = ""; np.slot = -1; np.players = []; np.closedWhy = "";
    stopLockstep();
    if (player()) player().netplay(null);
    status(why);
    draw();
    refreshList();
  }
  function status(text) { const p = player(); if (p && p.status) p.status(text || ""); }

  function onText(raw) {
    let m;
    try { m = JSON.parse(raw); } catch (_) { return; }
    if (m.t === "welcome") {
      np.code = m.code; np.slot = m.slot; np.players = m.players; np.host = m.slot === 0;
      if (player()) player().netplay(np.host ? "host" : "guest");
      status(np.host ? "Room " + m.code + " is open: others can join from 👥." : "Joined " + hostName() + "'s room.");
      if (np.host) closePanel();
      draw();
    } else if (m.t === "players") {
      const before = np.players.length;
      np.players = m.players;
      if (np.started) {
        for (const f in np.inputs) {   // someone left: their presses aren't waited for any more
          for (const s in np.inputs[f]) if (!np.players.some(function (p) { return String(p.slot) === s; })) delete np.inputs[f][s];
        }
        tryResume();
        if (np.host && np.players.length > before) startAll("joined");   // a newcomer starts from where we are
      }
      draw();
    } else if (m.t === "closed") {
      np.closedWhy = m.why || "The room closed.";
      disconnect(np.closedWhy);
    } else if (m.t === "error") {
      np.closedWhy = m.why;
      disconnect(m.why);
    } else if (m.t === "start") {
      np.pendingStart = { delay: Number(m.delay) || 5, epoch: Number(m.epoch) || 0 };   // the state comes next, as a binary message
      status("Starting together…");
    } else if (m.t === "i") {
      if (!np.started || m.e !== np.epoch) return;   // from before the last start: not this timeline's
      const f = Number(m.f);
      (np.inputs[f] || (np.inputs[f] = {}))[m.from] = Array.isArray(m.c) ? m.c : [];
      tryResume();
    } else if (m.t === "sum") {
      if (m.e === np.epoch) noteSum(Number(m.f), m.from, m.h);
    }
  }
  function onBinary(bytes) {   // the host's state to start from: [the sender's slot][the state]
    if (!np.pendingStart || bytes[0] !== 0) return;
    const opts = np.pendingStart;
    np.pendingStart = null;
    np.epoch = opts.epoch;
    beginFrom(bytes.subarray(1), opts.delay);
    status("Playing together · player " + (np.slot + 1));
  }

  /* -------------------------------------------------------------------- lockstep */
  function emu() { const p = player(); return p ? p.emu() : null; }

  function hook() {   // our hands on EmulatorJS: presses, play / pause, and the end of each frame
    const e = emu();
    if (!e || np.hooked) return;
    np.hooked = true;
    const gm = e.gameManager;
    np.origSim = gm.functions.simulateInput;     // the core itself: (player, index, value)
    const wrapped = gm.simulateInput.bind(gm);
    gm.simulateInput = function (playerIndex, index, value) {
      if (!np.started) return wrapped(playerIndex, index, value);
      if (playerIndex !== 0) return;             // one player per browser: its first controller (and keys, remote)
      np.local.push([index, value]);
    };
    np.origPlay = e.play.bind(e);
    np.origPause = e.pause.bind(e);
    e.play = function (dontUpdate) {
      if (!np.internal) np.userPaused = false;
      if (np.started && np.gate) return;          // waiting for someone: it goes on when they're here
      return np.origPlay(dontUpdate);
    };
    e.pause = function (dontUpdate) {
      if (!np.internal) np.userPaused = true;
      return np.origPause(dontUpdate);
    };
    const before = e.Module.postMainLoop;   // Emscripten's own after-a-frame hook, where a build still calls it
    e.Module.postMainLoop = function () {
      if (typeof before === "function") before.apply(this, arguments);
      tick();
    };
  }

  // The end of each frame. The cores shipped here never call Module.postMainLoop (EmulatorJS's own netplay hangs on
  // it too), so every animation-frame callback is followed by a look at the emulator's frame number: when it has
  // moved, a frame just ran, and the lockstep's turn comes before the next one. The emulator's main loop runs on
  // requestAnimationFrame, so this is right after each of its frames; other callbacks (gamepad.js) cost one look.
  let seenFrame = -1;
  function tick() {
    if (!np.started || !np.ws) return;
    const e = emu();
    if (!e || !e.gameManager) return;
    const n = Number(e.gameManager.getFrameNum());
    if (n === seenFrame) return;
    seenFrame = n;
    afterFrame();
  }
  const rawFrame = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = function (callback) {
    return rawFrame(function (time) {
      try { callback(time); } finally { tick(); }
    });
  };
  function internally(fn) { np.internal = true; try { fn(); } finally { np.internal = false; } }

  function frameNow() { return Number(emu().gameManager.getFrameNum()) - np.base; }   // frames run since the start

  function beginFrom(state, delay) {   // load the shared state and count frames from it
    hook();
    const e = emu();
    np.delay = delay;
    internally(function () { np.origPause(true); });
    e.gameManager.loadState(state);
    np.base = Number(e.gameManager.getFrameNum());
    seenFrame = np.base;                            // frame 0 hasn't run yet
    np.inputs = {}; np.local = []; np.sums = {};
    for (let f = 0; f < np.delay; f++) {           // the first frames: nobody's pressed anything yet
      np.inputs[f] = {};
      np.players.forEach(function (p) { np.inputs[f][p.slot] = []; });
    }
    np.sentUpTo = np.delay - 1;
    np.applied = -1;
    np.resyncs += 1;
    np.started = true; np.gate = false; np.waitingFor = -1;
    sendPresses();                                  // our presses for frame `delay`
    if (applyFrame(0)) { if (!np.userPaused) internally(function () { np.origPlay(true); }); }
    else gateOn(0);
    draw();
  }

  function startAll(why) {   // the host: everyone (us too) from our state now
    const e = emu();
    if (!np.host || !e || !np.ws) return;
    if (why !== "pressed" && Date.now() - np.lastResync < 1500) return;   // two reasons at once: one restart
    np.lastResync = Date.now();
    hook();
    const state = e.gameManager.getState();
    np.epoch += 1;
    send({ t: "start", delay: np.delay, epoch: np.epoch, why: why });
    np.ws.send(new Uint8Array(state));             // the server puts our slot in front
    beginFrom(state, np.delay);
    status(why === "joined" ? "Someone joined: everyone starts again from here." : why === "checksum"
      ? "The games drifted apart: everyone starts again from the host's." : "Playing together.");
  }

  function sendPresses() {   // our presses for the frame `delay` ahead of the one about to run
    const f = frameNow() + np.delay;
    if (f <= np.sentUpTo) return;
    for (let k = np.sentUpTo + 1; k <= f; k++) {   // normally one frame; more only after a stall
      const mine = k === f ? np.local : [];
      (np.inputs[k] || (np.inputs[k] = {}))[np.slot] = mine;
      send({ t: "i", f: k, c: mine, e: np.epoch });
    }
    np.local = [];
    np.sentUpTo = f;
  }

  function ready(f) {   // every player's presses for frame f are here
    const have = np.inputs[f];
    return Boolean(have) && np.players.every(function (p) { return have[p.slot] !== undefined; });
  }

  function applyFrame(f) {   // that frame's presses, as each player's controller; false if someone's are missing
    if (!ready(f)) return false;
    const have = np.inputs[f];
    np.players.forEach(function (p) {
      (have[p.slot] || []).forEach(function (c) { np.origSim(p.slot, c[0], c[1]); });
    });
    delete np.inputs[f];
    np.applied = f;
    return true;
  }

  function gateOn(f) {
    np.gate = true; np.waitingFor = f; np.waitSince = performance.now();
    internally(function () { np.origPause(true); });
    setTimeout(showWaiting, WAIT_SHOW_MS + 20);
  }
  function showWaiting() {
    if (!np.gate || performance.now() - np.waitSince < WAIT_SHOW_MS) return;
    const have = np.inputs[np.waitingFor] || {};
    const late = np.players.filter(function (p) { return have[p.slot] === undefined; }).map(function (p) { return p.name; });
    if (late.length) status("Waiting for " + late.join(", ") + "…");
  }
  function tryResume() {
    if (!np.started || !np.gate) return;
    if (!applyFrame(np.waitingFor)) return;
    np.gate = false; np.waitingFor = -1;
    status("");
    if (!np.userPaused) internally(function () { np.origPlay(true); });
  }

  function afterFrame() {   // the end of a frame: send ours, then the next only with everyone's presses
    np.ticks = (np.ticks || 0) + 1;
    const f = frameNow();
    sendPresses();
    if (f <= np.applied) return;                   // the loop ran without moving a frame: nothing new to apply
    for (let k = np.applied + 1; k < f; k++) applyFrame(k);   // frames run unseen (a skip): their presses, late
    if (f > 0 && f % np.checkEvery === 0) checksum(f);
    if (!applyFrame(f)) gateOn(f);
  }

  /* -------------------------------------------------------------------- staying the same */
  function fnv(bytes) {   // FNV-1a over the state: cheap, and enough to tell two apart
    let h = 0x811c9dc5;
    for (let i = 0; i < bytes.length; i++) { h ^= bytes[i]; h = Math.imul(h, 0x01000193); }
    return (h >>> 0).toString(16);
  }
  function checksum(f) {
    let h;
    try { h = fnv(emu().gameManager.getState()); } catch (_) { return; }
    send({ t: "sum", f: f, h: h, e: np.epoch });
    noteSum(f, np.slot, h);
  }
  function noteSum(f, slot, h) {   // the host compares everyone's checksum for a frame with its own
    if (!np.started) return;
    const at = np.sums[f] || (np.sums[f] = {});
    at[slot] = h;
    if (!np.host || at[0] === undefined) return;
    const differ = Object.keys(at).some(function (s) { return at[s] !== at[0]; });
    if (differ && Date.now() - np.lastResync > RESYNC_GAP_MS) startAll("checksum");
    for (const k in np.sums) if (Number(k) < f - np.checkEvery * 3) delete np.sums[k];   // old ones go
  }

  function stopLockstep() {   // back to playing alone: presses straight in, the game running
    if (!np.started) return;
    np.started = false; np.gate = false; np.inputs = {}; np.local = [];
    if (!np.userPaused && emu()) internally(function () { np.origPlay(true); });
  }

  /* -------------------------------------------------------------------- the page */
  function addButton() {   // 👥 in the player's bar, before Save
    const bar = document.querySelector(".rp-bar");
    if (!bar || $("rp-together-open")) return;
    const b = el("button", "", "👥");
    b.type = "button";
    b.id = "rp-together-open";
    b.title = "Play together";
    b.addEventListener("click", openPanel);
    bar.insertBefore(b, $("rp-save"));
  }
  addButton();

  // For retro-player.js (a host's loaded state) and retro-tv.js (the TV menu's Play together).
  window.RetroNetplay = {
    open: openPanel, close: closePanel, isOpen: function () { return Boolean(panel && !panel.hidden); },
    loaded: function () { if (np.host && np.started) startAll("loaded"); },
    state: function () {
      return { code: np.code, slot: np.slot, host: np.host, players: np.players, started: np.started, delay: np.delay,
               frame: np.started && emu() ? frameNow() : -1, starts: np.resyncs, gate: np.gate, ticks: np.ticks || 0,
               sent: np.sentUpTo, applied: np.applied, local: np.local.length };
    },
    tune: function (opts) { if (opts && opts.checkEvery > 0) np.checkEvery = opts.checkEvery; },   // tests: check sooner
  };
  window.addEventListener("pagehide", function () { if (np.ws) try { np.ws.close(); } catch (_) { /* gone */ } });
})();
