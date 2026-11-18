/* Retro netplay: playing one game together, each in their own browser (Doll, 2026-11-15). Loaded on the player page
 * after retro-player.js; the server side is web/retro.py's websocket and media/netplay.py's rooms.
 *
 * Rollback (every console but the PS1). Every browser runs the game; they stay identical because each frame runs with
 * the same buttons everywhere:
 *  - Frames are counted from one shared start: the host's save state, which everyone loads. Someone joining, the host
 *    loading a state, or a checksum that doesn't match (each browser sends one every CHECK_EVERY frames) starts
 *    everyone again from the host's state: a resync.
 *  - A player's own presses (whatever reaches EmulatorJS's gameManager.simulateInput for player 1: their pad, the
 *    keyboard, a TV remote) count from the frame `delay` ahead (1 by default: a 60th of a second) and go to everyone.
 *  - Nobody waits for the others' presses. A player whose presses for a frame haven't arrived is guessed to hold what
 *    they held last, and the frame runs. When their real presses come and the guess was wrong, the game goes back to
 *    that frame's state (kept for every guessed frame) and runs every frame since again, at once, with the real
 *    presses: a rollback, a few milliseconds, so you see the right game before the next frame. Only someone more
 *    than ROLLBACK frames behind (a stalled connection) makes everyone wait, and "Waiting for ..." shows.
 *  - Whoever runs ahead waits a frame now and then, so nobody keeps guessing further than they need to.
 *  - The emulator's frames are run from here (pump): its own main loop's next-frame request is kept, not passed on.
 * The PS1 replays frames too slowly for that (about 9 ms each), so it plays the same way with ROLLBACK at 0: lockstep,
 * every browser waiting for every player's presses, sent `delay` frames ahead (5 by default).
 * A guest plays the host's game: retro-player.js doesn't save it for them (RetroPlayer.netplay("guest")).
 * EmulatorJS's own netplay is left off: its control syncing is broken by its own account (media/netplay.py).
 */
(function () {
  "use strict";

  const $ = function (id) { return document.getElementById(id); };
  const game = JSON.parse(document.getElementById("rp-data").textContent);
  const LOCKSTEP = game.console === "psx";        // the PS1: too heavy to replay frames (see above)
  // Frames of input delay the host picks: with rollback, a delay only means fewer corrections; in lockstep it has to
  // cover the time presses take to arrive.
  const DELAYS = LOCKSTEP ? { 3: "Low (LAN)", 5: "Normal", 8: "Smooth (far away)" }
    : { 0: "Instant", 1: "Normal", 2: "Steady (fewer corrections)" };
  const ROLLBACK = 8;                 // the most frames a browser runs on guesses before it waits for someone
  const HOTKEYS = 24;                 // simulateInput's indexes from here are EmulatorJS's own keys (save, fast-forward...)
  const LEAD_EVERY = 30;              // frames between "how far ahead am I" notes in our presses
  const CATCH_UP = 2;                 // the most frames one animation frame runs, catching up with the clock
  // Frames a second, each console's own (NTSC; a PAL game plays at these too, a little quick).
  const RATES = { nes: 60.0988, snes: 60.0988, genesis: 59.9227, gb: 59.7275, gbc: 59.7275, gba: 59.7275, psx: 59.94 };
  const CHECK_EVERY = 300;            // frames between state checksums (5 s at 60 fps)
  const RESYNC_GAP_MS = 20000;        // the most often a mismatch restarts everyone
  const RESTART_GAP_MS = 1500;        // the least time between two restarts the host didn't press for
  const WAIT_SHOW_MS = 400;           // a stall this long says who it's waiting for
  const LIST_EVERY_MS = 3000;         // the room list, while the panel's open and we're in none
  const PING_EVERY_MS = 20000;        // a keep-alive the server answers (proxies drop a websocket quiet for 60 s)

  const player = function () { return window.RetroPlayer || null; };

  const np = {
    ws: null, code: "", slot: -1, host: false, players: [], delay: LOCKSTEP ? 5 : 1, started: false,
    window: LOCKSTEP ? 0 : ROLLBACK,   // frames we may run on guesses (0: lockstep)
    cur: 0,                   // the next frame to run, counted from the shared start
    local: {},                // this player's pad as it is now: index -> value
    mine: new Map(),          // frame -> our pad for it (a pad: "index:value,..."; see padOf)
    known: {},                // slot -> {last: the last frame we have their pad for, pads: frame -> pad, now: their frame}
    applied: {},              // slot -> the pad set in the core now (only changes are sent to it)
    states: new Map(),        // frame -> the state before it, for each frame run on a guess
    guessed: new Map(),       // frame -> {slot: the pad we guessed}
    checks: new Map(),        // frame -> its state, to checksum once it's sure
    rollTo: Infinity,         // the earliest frame a wrong guess was found at (a rollback goes back there)
    clock: 0, frameMs: 1000 / 60,   // when frame 0 was due, and how long a frame is (the console's own rate)
    sentUpTo: -1,             // the last frame whose pad went out
    epoch: 0,                 // which start this is: presses from before a resync are dropped
    gate: false,              // stalled: someone's too far behind to guess past
    waitSince: 0,
    drive: false, runner: null,   // we run the emulator's frames (with its own runner, caught in requestAnimationFrame)
    mask: [],                 // state bytes a load itself changes: left out of checksums
    runnerAsked: 0, extra: 0, guesses: 0, wrong: 0, stray: 0, lastFrameNum: -1,
    ticks: 0, rollbacks: 0, deepest: 0, stalls: 0, waits: 0, mismatches: 0,   // for the panel's numbers and the tests
    held: false,              // a guest who just joined: their own game paused until the host's state comes
    startLater: 0,            // the host: a restart put off until RESTART_GAP_MS since the last (a second join)
    sums: {},                 // frame -> {slot: checksum}
    lastResync: 0, resyncs: 0,  // restarts sent (the host) or taken (a guest), for the panel and the tests
    checkEvery: CHECK_EVERY,
    hooked: false, origSim: null, origPlay: null, origPause: null, userPaused: false, internal: false,
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
        go.title = np.players.length < 2 ? "Waiting for someone to join" : "Everyone starts again from where you are now";
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
      box.append(el("p", "muted small", LOCKSTEP
        ? "Input delay: " + np.delay + " frames. Everyone's game waits for whoever's behind."
        : "Input delay: " + np.delay + (np.delay === 1 ? " frame" : " frames") + ". Others' presses that arrive late are put right at once (" +
          np.rollbacks + " so far)."));
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
    const keepAlive = setInterval(function () {   // a quiet room (before Start) would be dropped by nginx after 60 s
      if (np.ws !== ws || ws.readyState > 1) { clearInterval(keepAlive); return; }
      if (ws.readyState === 1) ws.send('{"t":"ping"}');
    }, PING_EVERY_MS);
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
    clearTimeout(np.startLater); np.startLater = 0;   // a restart still waiting for its turn: no room to start now
    const wasHeld = np.held;
    np.held = false;
    stopPlaying();
    if (wasHeld && !np.started && !np.userPaused && emu()) internally(function () { np.origPlay(true); });   // a guest who never got the host's game: their own runs again
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
      status(np.host ? "Room " + m.code + " is open: when someone joins from 👥, you both play from your game."
        : "Joined " + hostName() + "'s room: waiting for their game…");
      if (np.host) closePanel();
      else holdForHost();                             // a guest's own game stops here: the host's replaces it
      draw();
    } else if (m.t === "players") {
      const before = np.players;
      np.players = m.players;
      const newcomers = m.players.filter(function (p) { return !before.some(function (b) { return b.slot === p.slot; }); });
      if (np.started) Object.keys(np.applied).forEach(function (s) {   // someone left: their buttons let go of (and
        if (!np.players.some(function (p) { return String(p.slot) === s; })) setPad(Number(s), "");   // not waited for)
      });
      // Someone joined: everyone (them too) starts from the host's game as it is now. Started or not: the host
      // needn't press anything (Start together sat in a closed panel, so a guest's game used to just run on alone).
      if (np.host && newcomers.length && before.length) startAll("joined");
      draw();
    } else if (m.t === "closed") {
      np.closedWhy = m.why || "The room closed.";
      disconnect(np.closedWhy);
    } else if (m.t === "error") {
      np.closedWhy = m.why;
      disconnect(m.why);
    } else if (m.t === "start") {
      const mine = player() && player().member ? player().member() : null;
      if ((m.member || null) !== mine) {           // an archive of several ROMs: everyone must play the same one
        disconnect("The host is playing " + (m.member || "the archive's first ROM") + ": pick that one in Which ROM? to play together.");
        return;
      }
      np.pendingStart = { delay: Number(m.delay) || 5, epoch: Number(m.epoch) || 0 };   // the state comes next, as a binary message
      status("Starting together…");
    } else if (m.t === "i") {
      if (!np.started || m.e !== np.epoch) return;   // from before the last start: not this timeline's
      onPad(m);
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

  /* -------------------------------------------------------------------- the engine */
  // We run the emulator's frames ourselves. Emscripten's main loop asks for each frame with its runner (named
  // MainLoop_runner); while playing together that request is kept here instead of passed on, and pump() calls the
  // runner when a frame is due, by our own clock at the console's own rate. Fast-forward stays on (ratio 0, every
  // frame drawn: retro-player.js) the whole time, as only then is one call exactly one frame, the same in every
  // browser: a normal call may run a frame or not by the emulator's own timing, and the same presses from the same
  // state come out differently. It's also how a rollback replays frames at once.
  function emu() { const p = player(); return p ? p.emu() : null; }
  const rawFrame = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = function (callback) {
    if (callback && callback.name === "MainLoop_runner") {   // Emscripten's next frame: ours to run while driving
      np.runner = callback;
      if (np.drive) return 0;
      return (np.runnerAsked = rawFrame(callback));   // (asked of the browser: cancelled when we take over)
    }
    return rawFrame(callback);
  };
  // RetroArch switches Emscripten's loop from requestAnimationFrame to setImmediate while fast-forward is on (and to
  // setTimeout in its timed mode), so the runner is caught there too: otherwise each rollback's replay would leave a
  // frame asked for that then ran on its own, unaccounted, a little differently in each browser. Browsers have no
  // setImmediate: this one (a message channel, as Emscripten's own stand-in) is there before the emulator starts, so
  // Emscripten takes it.
  const channel = new MessageChannel(), soon = [];
  channel.port1.onmessage = function () { const fn = soon.shift(); if (fn) fn(); };
  window.setImmediate = function (fn) {
    if (fn && fn.name === "MainLoop_runner") { np.runner = fn; if (np.drive) return 0; }
    soon.push(fn);
    channel.port2.postMessage(0);
    return 0;
  };
  const rawTimeout = window.setTimeout.bind(window);
  window.setTimeout = function (fn) {
    if (typeof fn === "function" && fn.name === "MainLoop_runner") { np.runner = fn; if (np.drive) return 0; }
    return rawTimeout.apply(window, arguments);
  };
  function internally(fn) { np.internal = true; try { fn(); } finally { np.internal = false; } }
  function frameNum() { return Number(emu().gameManager.getFrameNum()); }

  function step() {   // one frame: one call of the emulator's runner (fast-forward on); false if it ran none (paused)
    const before = frameNum();
    if (np.runner) np.runner();
    np.lastFrameNum = frameNum();
    const ran = np.lastFrameNum - before;
    if (ran > 1) np.extra += ran - 1;              // (never seen: a call is one frame; counted for the tests)
    return ran > 0;
  }
  function loadNow(state) {   // a state, in at once. The core takes a loaded state in on its next frame call, which
    emu().gameManager.loadState(state);   // runs nothing else (the state after it is the one loaded, byte for byte)
    step();
  }

  function hook() {   // our hands on EmulatorJS: everyone's presses, and play / pause
    const e = emu();
    if (!e || np.hooked) return;
    np.hooked = true;
    const gm = e.gameManager;
    np.origSim = gm.functions.simulateInput;     // the core itself: (player, index, value)
    const wrapped = gm.simulateInput.bind(gm);
    gm.simulateInput = function (playerIndex, index, value) {
      if (!np.started) return wrapped(playerIndex, index, value);
      if (playerIndex !== 0 || index >= HOTKEYS) return;   // one player per browser; no fast-forward or state keys
      if (value) np.local[index] = value; else delete np.local[index];   // this player's pad as it is now
    };
    np.origPlay = e.play.bind(e);
    np.origPause = e.pause.bind(e);
    e.play = function (dontUpdate) {
      if (!np.internal) np.userPaused = false;
      if (np.held) return;                        // a guest waiting for the host's game: it goes on then
      return np.origPlay(dontUpdate);
    };
    e.pause = function (dontUpdate) {             // (paused while playing together, everyone waits for us)
      if (!np.internal) np.userPaused = true;
      return np.origPause(dontUpdate);
    };
  }

  // A pad is a short string of its pressed buttons, "index:value" by index ("" for none), so two compare as text.
  function padOf(obj) {
    return Object.keys(obj).map(Number).sort(function (a, b) { return a - b; })
      .map(function (i) { return i + ":" + obj[i]; }).join(",");
  }
  function setPad(slot, pad) {   // this player's controller in the core, changing only what differs
    const want = {};
    if (pad) pad.split(",").forEach(function (b) { const kv = b.split(":"); want[kv[0]] = Number(kv[1]); });
    const now = np.applied[slot] || (np.applied[slot] = {});
    Object.keys(now).forEach(function (i) { if (!(i in want)) { np.origSim(slot, Number(i), 0); delete now[i]; } });
    Object.keys(want).forEach(function (i) { if (now[i] !== want[i]) { np.origSim(slot, Number(i), want[i]); now[i] = want[i]; } });
  }
  function releaseAll() { Object.keys(np.applied).forEach(function (s) { setPad(Number(s), ""); }); }

  function seen(slot) {   // what we know of a player's pads: every frame up to `last`, in order
    return np.known[slot] || (np.known[slot] = { last: np.delay - 1, pads: new Map() });
  }
  function padAt(slot, f) {   // a remote player's pad at frame f: theirs, or (not here yet) a guess: as they last held it
    const k = seen(slot);
    if (f <= k.last) return { pad: k.pads.get(f) || "", sure: true };
    return { pad: k.pads.get(k.last) || "", sure: false };
  }
  function others() { return np.players.filter(function (p) { return p.slot !== np.slot; }); }
  function sureUpTo() {   // the last frame everyone's pads are known for
    return others().reduce(function (low, p) { return Math.min(low, seen(p.slot).last); }, Infinity);
  }

  function runFrame(f, replaying) {   // frame f, with everyone's pads; false if the emulator didn't run it (try again)
    const guessed = {};
    let guessing = false;
    np.players.forEach(function (p) {
      if (p.slot === np.slot) { setPad(p.slot, np.mine.get(f) || ""); return; }
      const at = padAt(p.slot, f);
      setPad(p.slot, at.pad);
      if (!at.sure) { guessed[p.slot] = at.pad; guessing = true; }
    });
    if (guessing) np.states.set(f, emu().gameManager.getState());   // to come back to if a guess was wrong
    else np.states.delete(f);
    if (f > 0 && f % np.checkEvery === 0) np.checks.set(f, emu().gameManager.getState());   // compared once it's sure
    if (guessing) np.guessed.set(f, guessed); else np.guessed.delete(f);
    if (guessing && !replaying) np.guesses += 1;
    if (np.trace) np.trace.set(f, np.players.map(function (p) { return p.slot + "=" + (np.applied[p.slot] ? padOf(np.applied[p.slot]) : ""); }).join(" "));
    return step();
  }

  function rollback() {   // a guess turned out wrong: back to that frame, and every frame since again, at once
    const from = np.rollTo;
    np.rollTo = Infinity;
    if (from >= np.cur) return;
    const state = np.states.get(from);
    if (!state) return;                            // (can't happen: a guessed frame always keeps its state)
    loadNow(state);
    for (let f = from; f < np.cur; f++) runFrame(f, true);
    np.rollbacks += 1;
    np.deepest = Math.max(np.deepest, np.cur - from);
  }

  function sendMine() {   // our pad for the frame `delay` ahead of the one about to run (once per frame)
    const f = np.cur + np.delay;
    if (f <= np.sentUpTo) return;
    const pad = padOf(np.local);
    const msg = { t: "i", f: f, e: np.epoch, n: np.cur };
    if (pad !== np.mine.get(f - 1)) msg.c = pad;   // unchanged: no pad sent ("the same as the frame before")
    if (f % LEAD_EVERY === 0) msg.a = leads();     // now and then, how far ahead of each player we think we are
    np.mine.set(f, pad);
    send(msg);
    np.sentUpTo = f;
  }
  function leads() {   // our frame minus each player's last reported one (the time their news took is in it too)
    const out = {};
    others().forEach(function (p) { const k = seen(p.slot); if (k.now !== undefined) out[p.slot] = np.cur - k.now; });
    return out;
  }
  function onPad(m) {   // a player's pad for a frame (in order: one websocket keeps a sender's messages in order)
    const k = seen(m.from), f = Number(m.f);
    if (f <= k.last) return;
    for (let g = k.last + 1; g < f; g++) k.pads.set(g, k.pads.get(k.last) || "");   // (none missing, normally)
    const pad = typeof m.c === "string" ? m.c : (k.pads.get(f - 1) || "");
    k.pads.set(f, pad);
    k.last = f;
    k.now = Number(m.n);
    if (m.a && typeof m.a === "object" && m.a[np.slot] !== undefined) {
      // Each side's lead over the other includes the time news takes, so half their difference is how far ahead we
      // really are. A frame or more: we wait one, so neither keeps running ahead and guessing further.
      const mine = np.cur - k.now, theirs = Number(m.a[np.slot]);
      if ((mine - theirs) / 2 >= 1) { np.clock += np.frameMs; np.waits += 1; }   // our next frame, a frame later
    }
    const was = np.guessed.get(f);
    if (f < np.cur && was && was[m.from] !== undefined && was[m.from] !== pad) { np.rollTo = Math.min(np.rollTo, f); np.wrong += 1; }
  }

  function pump() {   // each animation frame while playing together: put wrong guesses right, then the next frame
    if (!np.drive) return;
    rawFrame(pump);
    if (!np.started || !np.runner) return;
    np.ticks += 1;
    if (frameNum() !== np.lastFrameNum) { np.stray += 1; np.lastFrameNum = frameNum(); }   // (a frame we didn't run: never seen)
    if (np.rollTo < np.cur) rollback();
    tidy();
    sendSums();
    const now = performance.now();
    let due = Math.floor((now - np.clock) / np.frameMs) + 1 - np.cur;   // frames the clock says should have run by now
    if (due > CATCH_UP * 4) { np.clock = now - np.cur * np.frameMs; due = 1; }   // (a hidden tab, a long stall: no sprint)
    for (let n = 0; n < Math.min(due, CATCH_UP); n++) {
      const f = np.cur;
      const behind = others().filter(function (p) { return f > seen(p.slot).last + np.window; });
      if (behind.length) { stall(behind); np.clock = now - np.cur * np.frameMs; return; }   // the clock waits too
      if (np.gate) { np.gate = false; status(""); }
      sendMine();
      if (!runFrame(f, false)) { np.clock = now - np.cur * np.frameMs; return; }   // paused (a menu): the clock waits
      np.cur += 1;
    }
  }
  function stall(behind) {   // someone's presses are too far behind to guess past: everyone waits for them
    if (!np.gate) { np.gate = true; np.waitSince = performance.now(); np.stalls += 1; }
    if (performance.now() - np.waitSince > WAIT_SHOW_MS) status("Waiting for " + behind.map(function (p) { return p.name; }).join(", ") + "…");
  }
  function tidy() {   // what's no longer needed: states and guesses for frames everyone's sure of, old pads
    const sure = sureUpTo(), keep = Math.min(sure, np.cur) - 2;
    np.states.forEach(function (_, f) { if (f <= sure) np.states.delete(f); });
    np.guessed.forEach(function (_, f) { if (f <= sure) np.guessed.delete(f); });
    np.mine.forEach(function (_, f) { if (f < keep) np.mine.delete(f); });
    Object.keys(np.known).forEach(function (s) {
      const k = np.known[s];
      k.pads.forEach(function (_, f) { if (f < keep && f < k.last) k.pads.delete(f); });
    });
  }

  function drive(on) {   // take the emulator's frames over (on), or give them back to it (off)
    const e = emu();
    if (on === np.drive || !e) return;
    np.drive = on;
    const f = e.gameManager.functions;
    if (on) {
      cancelAnimationFrame(np.runnerAsked);        // the frame it already asked the browser for won't run by itself
      f.setFastForwardRatio(0);                    // fast-forward: "a frame per call, no waiting", our clock pacing it
      f.toggleFastForward(1);
      if (e.paused) internally(function () { np.origPlay(true); });   // a live runner to call
      rawFrame(pump);
    } else {
      const ratio = e.settings && e.settings["ff-ratio"];
      f.setFastForwardRatio(ratio === "unlimited" ? 0 : Number(ratio) || 3);   // the player's own fast-forward again
      f.toggleFastForward(e.isFastForward ? 1 : 0);
      if (np.runner && !np.userPaused) rawFrame(np.runner);   // its own loop again, from its next frame
    }
  }

  function beginFrom(state, delay) {   // everyone: load the shared state and count frames from it
    hook();
    if (!np.runner) { disconnect("This game's emulator can't play together here."); return; }
    np.delay = delay;
    np.window = game.console === "psx" ? 0 : ROLLBACK;   // the PS1: too heavy to replay frames, so lockstep
    np.held = false;
    np.started = true;
    np.known = {}; np.mine = new Map(); np.states = new Map(); np.guessed = new Map(); np.checks = new Map(); np.sums = {};
    drive(true);
    for (let s = 0; s < 4; s++) for (let i = 0; i < HOTKEYS; i++) np.origSim(s, i, 0);   // every pad let go of, so
    np.applied = {};                                                                   // nothing held before carries in
    loadNow(state);
    // The few bytes of a state that a load itself changes (a count the core keeps, never read by the game: the
    // Genesis has one) would make two browsers that went back a different number of times disagree. Found by loading
    // twice, and left out of the checksums; everyone finds the same ones, from the same state.
    const once = emu().gameManager.getState();
    loadNow(once);
    const twice = emu().gameManager.getState();
    np.mask = [];
    for (let i = 0; i < once.length; i++) if (once[i] !== twice[i]) np.mask.push(i);
    np.cur = 0; np.sentUpTo = np.delay - 1; np.rollTo = Infinity; np.gate = false;
    np.frameMs = 1000 / (RATES[game.console] || 60);
    np.clock = performance.now();                  // frame f is due at clock + f frames
    for (let f = 0; f < np.delay; f++) np.mine.set(f, "");   // the first frames: nobody's pressed anything yet
    np.resyncs += 1;
    draw();
  }

  function startAll(why) {   // the host: everyone (us too) from our state now
    const e = emu();
    if (!np.host || !e || !np.ws) return;
    const wait = RESTART_GAP_MS - (Date.now() - np.lastResync);
    if (why !== "pressed" && wait > 0) {   // two reasons at once: one restart now, and the next once the gap's passed,
      if (!np.startLater) np.startLater = setTimeout(function () { np.startLater = 0; startAll(why); }, wait);   // so a
      return;                                                     // second newcomer isn't left out (everyone'd wait on them)
    }
    clearTimeout(np.startLater); np.startLater = 0;
    np.lastResync = Date.now();
    (np.why || (np.why = [])).push(why);          // why each start happened (the panel's history, and the tests)
    hook();
    const state = e.gameManager.getState();
    np.epoch += 1;
    send({ t: "start", delay: np.delay, epoch: np.epoch, why: why, member: player().member ? player().member() : null });
    np.ws.send(new Uint8Array(state));             // the server puts our slot in front
    beginFrom(state, np.delay);
    status(why === "joined" ? "Someone joined: everyone starts again from here." : why === "checksum"
      ? "The games drifted apart: everyone starts again from the host's." : "Playing together.");
  }

  function holdForHost() {   // a guest, in: their own game waits, paused, until the host's state (beginFrom)
    hook();
    if (!emu()) return;
    np.held = true;
    internally(function () { np.origPause(true); });
  }

  /* -------------------------------------------------------------------- staying the same */
  function fnv(bytes) {   // FNV-1a over a state, less the bytes a load changes: cheap, and enough to tell two apart
    let h = 0x811c9dc5, skip = 0;
    const mask = np.mask || [];
    for (let i = 0; i < bytes.length; i++) {
      if (skip < mask.length && mask[skip] === i) { skip += 1; continue; }
      h ^= bytes[i]; h = Math.imul(h, 0x01000193);
    }
    return (h >>> 0).toString(16);
  }
  function sendSums() {   // a checked frame's state, once every frame before it is sure (no guess can change it now)
    const sure = Math.min(sureUpTo(), np.cur - 1);
    np.checks.forEach(function (state, f) {
      if (f > sure + 1) return;
      np.checks.delete(f);
      const h = fnv(state);
      send({ t: "sum", f: f, h: h, e: np.epoch });
      noteSum(f, np.slot, h);
    });
  }
  function noteSum(f, slot, h) {   // the host compares everyone's checksum for a frame with its own
    if (!np.started) return;
    const at = np.sums[f] || (np.sums[f] = {});
    at[slot] = h;
    if (!np.host || at[0] === undefined) return;
    const differ = Object.keys(at).some(function (s) { return at[s] !== at[0]; });
    if (differ) np.mismatches += 1;
    if (differ && Date.now() - np.lastResync > RESYNC_GAP_MS) startAll("checksum");
    for (const k in np.sums) if (Number(k) < f - np.checkEvery * 3) delete np.sums[k];   // old ones go
  }

  function stopPlaying() {   // back to playing alone: our own pad straight in, the emulator running its own frames
    if (!np.started) return;
    np.started = false; np.gate = false;
    releaseAll();
    drive(false);
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
               frame: np.started ? np.cur : -1, starts: np.resyncs, why: np.why || [], gate: np.gate, ticks: np.ticks,
               sent: np.sentUpTo, window: np.window, rollbacks: np.rollbacks, deepest: np.deepest, stalls: np.stalls,
               waits: np.waits, mismatches: np.mismatches, driving: np.drive, extra: np.extra,
               guesses: np.guesses, wrong: np.wrong, stray: np.stray };
    },
    tune: function (opts) {   // tests: check sooner; keep what each frame ran with
      if (opts && opts.checkEvery > 0) np.checkEvery = opts.checkEvery;
      if (opts && opts.trace) np.trace = new Map();
    },
    trace: function () { return np.trace ? Array.from(np.trace.entries()) : []; },
  };
  window.addEventListener("pagehide", function () { if (np.ws) try { np.ws.close(); } catch (_) { /* gone */ } });
})();
