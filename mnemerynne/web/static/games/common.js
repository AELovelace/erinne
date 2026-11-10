/* Shared by the built-in games (mnemerynne/games.py): one set of actions however they arrive, the talk with
   Media's game layer (media.js games), the theme's colours, sound and the best score kept on this device.

   Actions: "left", "right", "up", "down", "enter", "play", "prev", "next". Prev and Next are a remote's track buttons, or Page Up / Page Down
   or [ and ] on a keyboard; a game that has no use for them ignores them. Keyboard Play is P or a media Play key; other actions use arrows, WASD, Enter, Space, or
   from the TV page, which forwards a remote panel's arrows and Enter here as messages ({type:
   "mnemerynne-game-key", key}). Escape or Backspace inside the frame asks the TV page to close the game.

   To the TV page (same origin only): {type: "mnemerynne-game-state", sub} whenever the score line changes, so a
   remote panel shows it under the game's name; {type: "mnemerynne-game-close"} to go back to Games. */
(function () {
  "use strict";

  const framed = window.parent !== window;
  const KEYS = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down", Enter: "enter", " ": "enter",
                 a: "left", d: "right", w: "up", s: "down", A: "left", D: "right", W: "up", S: "down",
                 MediaPlay: "play", MediaPlayPause: "play", p: "play", P: "play",
                 MediaTrackNext: "next", MediaTrackPrevious: "prev", PageDown: "next", PageUp: "prev", "]": "next", "[": "prev" };
  let onAction = null, directEnter = null, tabNavigation = false;

  function toParent(kind, extra) { // A message to the TV page; nothing when opened on its own.
    if (framed) window.parent.postMessage(Object.assign({ type: "mnemerynne-game-" + kind }, extra || {}), location.origin);
  }

  function act(action, controller) { // A signed panel input keeps its identity; local keys and older games need none.
    sound.unlock();
    if (onAction) onAction(action, controller);
  }

  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "Tab") { tabNavigation = true; return; } // Explicit keyboard focus navigation retains native button activation.
    if (e.target && (e.target.matches("input, select, textarea") || e.target.isContentEditable)) return; // Native form editing keeps its keys.
    if (e.key === "Escape" || e.key === "Backspace") { if (framed) { e.preventDefault(); toParent("close"); } return; }
    const action = KEYS[e.key];
    if (!action) return;
    if (["left", "right", "up", "down"].includes(action)) tabNavigation = false;
    const tag = (e.target && e.target.tagName) || "";
    if (action === "enter" && tag === "BUTTON" && !(directEnter && directEnter() && !tabNavigation)) return; // Casino tables can make Enter submit a bet regardless of old focus.
    e.preventDefault();
    act(action);
  });

  window.addEventListener("message", function (e) { // A remote panel's arrow or Enter, forwarded by the TV page.
    if (e.origin !== location.origin || e.source !== window.parent) return;
    const data = e.data || {};
    if (data.type === "mnemerynne-game-key" && KEYS[data.key]) act(KEYS[data.key], typeof data.controller === "string" ? data.controller : undefined);
    if (data.type === "mnemerynne-game-scale" && document.documentElement.classList.contains("tv") &&
        Number.isFinite(data.scale) && data.scale >= .75 && data.scale <= 3) {
      document.documentElement.style.setProperty("--ui-scale", String(data.scale)); // Reuse the UI's rem scale, never multiply it twice.
      window.dispatchEvent(new Event("resize")); // Canvas games redraw at the new CSS size, retaining device-pixel sharpness.
    }
  });

  function colors() { // The theme's tokens (theme-neon.css by default), so the game matches whatever look is on.
    const css = getComputedStyle(document.documentElement), read = function (name, fallback) {
      return (css.getPropertyValue(name) || "").trim() || fallback;
    };
    return { pink: read("--rose", "#ff2e97"), pinkSoft: read("--rose-deep", "#ff7ac0"), cyan: read("--cyan", "#00f0ff"),
             ink: read("--ink", "#ffe9f5"), muted: read("--muted", "#b98aa9"), night: read("--blush", "#07020d"),
             glow: read("--glow", "255, 46, 151"), font: read("--serif", "system-ui, sans-serif") };
  }

  const game = (document.getElementById("gm") || {}).dataset || {};
  const store = { // This device's own records, per game; a blocked or private browser simply forgets them.
    get: function (name, fallback) {
      try { const v = localStorage.getItem("mnemerynne-game:" + game.game + ":" + name); return v === null ? fallback : JSON.parse(v); }
      catch (e) { return fallback; }
    },
    set: function (name, value) {
      try { localStorage.setItem("mnemerynne-game:" + game.game + ":" + name, JSON.stringify(value)); } catch (e) { /* storage blocked */ }
    },
  };

  const sound = (function () { // Short synthesized notes (Web Audio): no files, a polyphony cap, muting remembered.
    const Ctx = window.AudioContext || window.webkitAudioContext;
    let on = store.get("sound", true), ctx = null, master = null;
    const voices = new Set();
    function unlock() { // Start or resume audio; browsers only allow it after a key or tap (the TV box allows it anyway).
      if (!on || !Ctx || document.hidden) return;
      try {
        if (!ctx) { ctx = new Ctx(); master = ctx.createGain(); master.gain.value = 0.3; master.connect(ctx.destination); }
        if (ctx.state === "suspended") ctx.resume().catch(function () {});
      } catch (e) { ctx = null; }
    }
    function stop() { // Silence everything now (muted, or the page hidden).
      voices.forEach(function (v) { try { v.stop(); } catch (e) { /* already ended */ } });
      voices.clear();
    }
    function tone(freq, duration, delay, volume, type, endFreq) { // One click-free note, sliding from freq to endFreq.
      if (!on || !ctx || ctx.state !== "running" || document.hidden || voices.size >= 32) return;
      try {
        const osc = ctx.createOscillator(), gain = ctx.createGain(), start = ctx.currentTime + (delay || 0);
        osc.type = type || "sine";
        osc.frequency.setValueAtTime(freq, start);
        osc.frequency.exponentialRampToValueAtTime(endFreq || freq, start + duration);
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(volume || 0.2, start + 0.006);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
        osc.connect(gain); gain.connect(master);
        voices.add(osc);
        osc.onended = function () { osc.disconnect(); gain.disconnect(); voices.delete(osc); };
        osc.start(start); osc.stop(start + duration + 0.02);
      } catch (e) { stop(); }   // a missing audio device never stops the game
    }
    function toggle() { on = !on; store.set("sound", on); if (!on) stop(); else unlock(); return on; }
    document.addEventListener("visibilitychange", function () { if (document.hidden) stop(); });
    return { unlock: unlock, stop: stop, tone: tone, toggle: toggle, isOn: function () { return on; } };
  })();

  async function api(url, body) { // A bounded GET, or a POST when there's a body. A refusal throws with its status and the server's words.
    const response = await fetch(url, { method: body === undefined ? "GET" : "POST", credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(10000), cache: "no-store" });
    const data = await response.json();
    if (!response.ok) {
      const failure = new Error(typeof data.detail === "string" ? data.detail : "This move could not be accepted.");
      failure.status = response.status;
      throw failure;
    }
    return data;
  }

  function showCoins(id) { // Put this account's coins in an element; if they can't be read, the game simply plays on.
    api("/api/media/games/coins").then(function (data) {
      const box = document.getElementById(id);
      if (box) box.textContent = data.coins.toLocaleString();
    }).catch(function () { /* the balance is a nicety, never a reason to stop */ });
  }

  window.MGame = {
    api: api, showCoins: showCoins,
    framed: framed,
    reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    onAction: function (fn, options) { onAction = fn; directEnter = options && options.directEnter; }, // Optional direct Enter controls; older games keep native buttons.
    tap: act,                                                // the game's own buttons and taps, as actions
    report: function (sub) { toParent("state", { sub: String(sub || "").slice(0, 300) }); },   // the line a remote shows
    close: function () { toParent("close"); },
    colors: colors, store: store, sound: sound,
  };
})();
