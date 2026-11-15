/* Retro on a TV (templates/retro.html and retro_play.html; media.js opens Retro in its game layer on a paired
 * screen, 2026-11-14). Loaded on both Retro pages, after their own script and games/gamepad.js. It adds:
 *
 *  - the remote, through the TV page's messages ({type: "mnemerynne-game-key", key}, as the built-in games get
 *    them): on the shelves and in menus the arrows move between games and buttons and OK opens one; in a running
 *    game the arrows and OK are the console's D-pad and A, and ⏯ opens the menu;
 *  - a controller (Bluetooth or USB, through gamepad.js) in the shelves and menus: the D-pad or stick moves, A
 *    presses, B goes back. In a running game EmulatorJS has the controller to itself (player 1, 2, 3, 4 in the
 *    order they're pressed), except the Xbox / PS button, or Start and Select held together, which opens the menu;
 *  - the menu (on the player): Resume, Save state, Load state, Leave;
 *  - leaving on the TV's say-so ({type: "mnemerynne-game-leave"}: the bar's Back, Escape, the remote's Stop): the
 *    player saves first (retro-player.js leave), then the page tells the TV it's done ("mnemerynne-game-close").
 *
 * Opened on its own in a browser (not framed), only the controller navigation and the menu combo apply.
 */
(function () {
  "use strict";

  const framed = window.parent !== window;
  const player = function () { return window.RetroPlayer || null; };   // retro-player.js, on the player page only
  const DPAD = { ArrowUp: 4, ArrowDown: 5, ArrowLeft: 6, ArrowRight: 7, Enter: 8 };   // EmulatorJS's inputs: the D-pad, and A
  const COMBO_MS = 800;                                                  // Start + Select held this long: the menu
  let menu = null;

  function post(kind) { if (framed) window.parent.postMessage({ type: "mnemerynne-game-" + kind }, location.origin); }

  /* -------------------------------------------------------------- moving around */
  function visible(n) { return n.offsetParent !== null && !n.hidden && !n.disabled; }
  function layer() { // what's on top: the menu, the save slots, the start choice, else the page
    if (menu && !menu.hidden) return menu;
    const slots = document.getElementById("rp-slots");
    if (slots && !slots.hidden) return slots;
    return document;
  }
  function targets() {
    return Array.prototype.filter.call(layer().querySelectorAll("a[href], button, input, select"), visible);
  }
  function move(dir) { // the nearest target that way, like the TV page's own remote navigation
    const all = targets(), cur = document.activeElement;
    if (all.indexOf(cur) < 0) { if (all[0]) all[0].focus(); return; }
    const r = cur.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    let best = null, score = Infinity;
    all.forEach(function (t) {
      if (t === cur) return;
      const b = t.getBoundingClientRect(), dx = b.left + b.width / 2 - cx, dy = b.top + b.height / 2 - cy;
      let main, side;
      if (dir === "right") { if (b.left < r.right - 4) return; main = dx; side = dy; }
      else if (dir === "left") { if (b.right > r.left + 4) return; main = -dx; side = dy; }
      else if (dir === "down") { if (b.top < r.bottom - 4) return; main = dy; side = dx; }
      else { if (b.bottom > r.top + 4) return; main = -dy; side = dx; }
      const s = main + Math.abs(side) * 2.5;
      if (s < score) { score = s; best = t; }
    });
    if (best) { best.focus({ preventScroll: true }); best.scrollIntoView({ block: "nearest", inline: "nearest" }); }
  }
  function press() {
    const f = document.activeElement;
    if (f && f !== document.body && layer().contains(f)) f.click(); else move("down");
  }

  /* -------------------------------------------------------------- the player's menu */
  function inGame() { // a running game with nothing over it: the controller belongs to EmulatorJS
    const p = player();
    return Boolean(p && p.started() && layer() === document);
  }
  function buildMenu() {
    menu = document.createElement("div");
    menu.className = "rp-slots rp-tvmenu";
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", "Game menu");
    menu.hidden = true;
    const box = document.createElement("div");
    box.className = "rp-slots-box";
    const title = document.createElement("h2");
    title.textContent = "Paused";
    box.append(title);
    [["Resume", closeMenu], ["💾 Save state", function () { closeMenu(true); player().openSlots("save"); }],
     ["📂 Load state", function () { closeMenu(true); player().openSlots("load"); }],
     ["Leave the game", function () { closeMenu(true); leave(); }]].forEach(function (item, i) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = item[0];
      if (i) b.className = "secondary";
      b.addEventListener("click", item[1]);
      box.append(b);
    });
    menu.append(box);
    document.body.append(menu);
  }
  function openMenu() {
    const p = player();
    if (!p || !p.started()) return;
    if (!menu) buildMenu();
    if (!menu.hidden) { closeMenu(); return; }
    p.pause();
    menu.hidden = false;
    menu.querySelector("button").focus();
  }
  function closeMenu(keepPaused) {
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    if (!keepPaused && player()) player().resume();
  }

  /* -------------------------------------------------------------- leaving */
  function leave() { // the player saves first; the shelves just go
    const p = player();
    if (p && framed) p.leave(function () { post("close"); });
    else if (p) p.leave();
    else if (framed) post("close");
    else location.href = "/media";
  }
  function back() { // B, or Escape: close what's on top, else up a level
    if (menu && !menu.hidden) { closeMenu(); return; }
    const slots = document.getElementById("rp-slots");
    if (slots && !slots.hidden) { document.getElementById("rp-slots-close").click(); return; }
    if (player() && !player().started()) { location.href = "/media/retro"; return; }   // not playing yet: the shelves
    if (!player()) leave();
  }

  /* -------------------------------------------------------------- the remote (the TV page's messages) */
  function tap(index) { // one press of a console button, as a remote can't hold one
    const p = player();
    if (!p || !p.emu()) return;
    const gm = p.emu().gameManager;
    gm.simulateInput(0, index, 1);
    setTimeout(function () { gm.simulateInput(0, index, 0); }, 120);
  }
  window.addEventListener("message", function (e) {
    if (!framed || e.origin !== location.origin || e.source !== window.parent) return;
    const data = e.data || {};
    if (data.type === "mnemerynne-game-leave") { leave(); return; }
    if (data.type !== "mnemerynne-game-key") return;
    const key = String(data.key || "");
    if (key === "MediaPlay") { if (player()) openMenu(); return; }
    if (inGame()) { if (DPAD[key] !== undefined) tap(DPAD[key]); return; }
    if (key === "Enter") press();
    else if (key.indexOf("Arrow") === 0) move(key.slice(5).toLowerCase());
  });
  // The TV's own keyboard, with focus here: arrows move (not while typing in the search), Escape goes back.
  document.addEventListener("keydown", function (e) {
    if (inGame() || e.ctrlKey || e.altKey || e.metaKey) return;
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test((e.target && e.target.tagName) || "");
    if (e.key.indexOf("Arrow") === 0 && !typing && framed) { e.preventDefault(); move(e.key.slice(5).toLowerCase()); }
    else if (e.key === "Escape" && framed && layer() === document) { e.preventDefault(); back(); }
  });

  /* -------------------------------------------------------------- controllers */
  if (window.MPad) window.MPad.start(function (action) {
    if (inGame()) { if (action === "home") openMenu(); return; }   // EmulatorJS has the controller
    if (action === "home") { if (player() && player().started()) openMenu(); else leave(); }
    else if (action === "back") back();
    else if (action === "enter") press();
    else if (action === "play") { if (player()) openMenu(); }
    else if (["up", "down", "left", "right"].indexOf(action) >= 0) move(action);
  });
  if (navigator.getGamepads) { // Start + Select held together: the menu, for a controller whose middle button the browser hides
    let since = 0;
    (function watch(now) {
      let both = false;
      try {
        Array.prototype.forEach.call(navigator.getGamepads() || [], function (p) {
          if (p && p.buttons[8] && p.buttons[9] && p.buttons[8].pressed && p.buttons[9].pressed) both = true;
        });
      } catch (e) { /* no pads */ }
      if (both && inGame()) { if (!since) since = now; else if (now - since >= COMBO_MS) { since = 0; openMenu(); } }
      else since = 0;
      requestAnimationFrame(watch);
    })(0);
  }

  window.RetroTV = { framed: framed, openMenu: openMenu, leave: leave };   // for tests (tests/retro-browser.mjs)
})();
