/* Gamepads for the built-in games (first used by Lumi Launch, 2026-11-14): any pad the browser sees (navigator.getGamepads,
   the "standard" layout: Xbox, PlayStation, Switch Pro, most USB pads) turns into the same actions MGame's keys and
   remotes give: "left", "right", "up", "down", "enter", "play", plus "back".

   Buttons: A / Cross = enter · B / Circle = back · Start / Options = play · the D-pad and the left stick = arrows ·
   LB / RB (L1 / R1) = prev / next · the Xbox / PS button = home (where the browser passes it on; not every one does).
   A held direction repeats like a keyboard (after REPEAT_WAIT ms, then every REPEAT_EVERY ms), so menus scroll.
   Each pad says who it is ("pad:0", "pad:1", ...), so a couch game can tell its players apart.

   A game loads it through its Game.extras ("gamepad.js") and calls MPad.start(function (action, who) {...}).
   No pad, or a browser without the Gamepad API: nothing happens and nothing breaks. Node can test it through
   MPad.create(getPads, onAction), which polls only when told to (tests/js/gamepad.test.mjs). */
(function (root) {
  "use strict";

  const BUTTONS = { 0: "enter", 1: "back", 4: "prev", 5: "next", 9: "play", 12: "up", 13: "down", 14: "left", 15: "right", 16: "home" };   // the standard layout
  const STICK_ON = 0.5, STICK_OFF = 0.35;                   // the left stick: past half-way is a press; back under a third lets go
  const REPEAT_WAIT = 420, REPEAT_EVERY = 140;               // a held direction repeats, like a held arrow key
  const REPEATS = { up: 1, down: 1, left: 1, right: 1 };   // only directions repeat; OK, back and play never do

  function create(getPads, onAction) {                      // one poller: getPads() is navigator.getGamepads, or a test's fake
    const seen = {};                                        // per pad: what was held last poll, and when each began or repeated

    function held(pad, mine) {                              // the actions this pad holds right now
      const out = new Set();
      Object.keys(BUTTONS).forEach(function (n) {
        const b = pad.buttons && pad.buttons[n];
        if (b && (b.pressed || b.value > 0.5)) out.add(BUTTONS[n]);
      });
      const ax = (pad.axes && pad.axes[0]) || 0, ay = (pad.axes && pad.axes[1]) || 0;
      const was = mine.stick || {};
      const stick = {                                       // a little stickiness, so a stick resting at the edge doesn't chatter
        left: ax < -(was.left ? STICK_OFF : STICK_ON), right: ax > (was.right ? STICK_OFF : STICK_ON),
        up: ay < -(was.up ? STICK_OFF : STICK_ON), down: ay > (was.down ? STICK_OFF : STICK_ON),
      };
      mine.stick = stick;
      Object.keys(stick).forEach(function (k) { if (stick[k]) out.add(k); });
      return out;
    }

    function poll(now) {                                    // look at every pad once; fire an action for each new press (and repeats)
      let pads = [];
      try { pads = Array.from(getPads() || []); } catch (e) { return; }   // a browser that refuses: no pads
      const here = new Set();
      pads.forEach(function (pad) {
        if (!pad || pad.connected === false) return;
        const id = "pad:" + pad.index, mine = seen[id] || (seen[id] = { down: {}, stick: {} });
        here.add(id);
        const now_ = held(pad, mine);
        now_.forEach(function (action) {
          const since = mine.down[action];
          if (since === undefined) {                        // a fresh press
            mine.down[action] = { at: now, next: now + REPEAT_WAIT };
            onAction(action, id);
          } else if (REPEATS[action] && now >= since.next) {   // still held: repeat
            since.next = now + REPEAT_EVERY;
            onAction(action, id);
          }
        });
        Object.keys(mine.down).forEach(function (action) { if (!now_.has(action)) delete mine.down[action]; });   // let go
      });
      Object.keys(seen).forEach(function (id) { if (!here.has(id)) delete seen[id]; });   // an unplugged pad lets go of everything
    }

    return { poll: poll, pads: function () { return Object.keys(seen); } };
  }

  let running = null;
  function start(onAction) {                                // the browser's pads, polled every animation frame
    if (running || typeof navigator === "undefined" || !navigator.getGamepads) return false;
    running = create(function () { return navigator.getGamepads(); }, onAction);
    const loop = function (now) { if (!running) return; running.poll(now); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
    return true;
  }
  function stop() { running = null; }

  const API = { create: create, start: start, stop: stop, BUTTONS: BUTTONS };
  if (typeof module !== "undefined" && module.exports) module.exports = API; else root.MPad = API;
})(this);
