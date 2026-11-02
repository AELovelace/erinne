/* The companion's controls and presentation. All memories stay on this browser; there is no neglect mechanic. */
(function () {
  "use strict";
  const G = window.MGame, R = window.LumiPetRules;
  const $ = function (id) { return document.getElementById(id); };
  const room = $("lp-room"), buttons = Array.from(document.querySelectorAll("[data-lp-action]"));
  const SAVE_KEY = "mnemerynne-game:lumi-pet:companion";
  const LABELS = { cuddle: "Cuddle", snack: "Snack", play: "Play", rest: "Nap" };
  const MEMORIES = { cuddle: "A warm little cuddle", snack: "A shared star biscuit", play: "Chasing a falling star", rest: "A quiet moment together" };
  const MESSAGES = {
    cuddle: ["Lumi leans into your hand. A tiny purr, just for you.", "Soft wings wrap around your fingers. We can stay like this a while."],
    snack: ["A star biscuit! Lumi saves the sparkliest half for you.", "Crumbs on the cushion. Lumi looks very pleased with this picnic."],
    play: ["One falling star, two little paws. Caught it! Your turn.", "Lumi pounces on a ribbon of light, then proudly brings it back."],
  };
  let state = read(), selected = 0, effectUntil = 0, lastReport = "", timer = null, lastScene = "";

  function read() { // Storage can be blocked or malformed; the companion remains playable either way.
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(SAVE_KEY)); } catch (_) { /* Use a fresh in-memory companion. */ }
    return R.normalize(saved, Date.now());
  }

  function save() { // Tell the player whether this moment survives a reload instead of silently promising a save.
    try {
      localStorage.setItem(SAVE_KEY, JSON.stringify(state));
      $("lp-save").textContent = "Saved in this browser, on this device. Shared by people using this browser profile.";
    } catch (_) {
      $("lp-save").textContent = "Storage is unavailable. Lumi can still play, but this visit won’t be saved.";
    }
  }

  function render() { // Refresh the room and controls without recreating focused buttons or flooding the remote.
    const now = Date.now(), scene = R.scene(state, now);
    room.dataset.palette = state.palette;
    room.dataset.night = String(scene.night);
    room.dataset.sleep = String(scene.asleep);
    $("lp-moments").textContent = state.moments.toLocaleString();
    $("lp-memory").textContent = MEMORIES[state.memory] || "Our first hello";
    $("lp-clock").textContent = scene.night ? "Moonlight hours" : "A little daytime glow";
    $("lp-state").textContent = scene.asleep ? "Dreaming in good company" : "Happy to be here with you";
    $("lp-pet-title").textContent = scene.asleep ? "Lumi, a glowing fairy cat, sleeping peacefully" : "Lumi, a glowing fairy cat, awake and content";
    const sceneKey = String(scene.asleep);
    if (sceneKey !== lastScene) {
      const label = $("lp-rest-label"), hint = document.createElement("small");
      label.textContent = scene.asleep ? "Wake gently" : "Nap";
      hint.textContent = scene.asleep ? "A sleepy little hello" : "Dream a little dream";
      label.appendChild(hint); lastScene = sceneKey;
    }
    if (now >= effectUntil) {
      delete room.dataset.action;
      $("lp-effect").textContent = scene.asleep ? "z z z" : "♡";
    }
    buttons.forEach(function (button, index) { button.setAttribute("aria-pressed", String(index === selected)); });
    $("lp-palette").textContent = "Glow: " + state.palette[0].toUpperCase() + state.palette.slice(1);
    $("lp-sound").textContent = "Sound: " + (G.sound.isOn() ? "on" : "off");
    $("lp-sound").setAttribute("aria-pressed", String(G.sound.isOn()));
    const pick = selected === 3 && scene.asleep ? "Wake gently" : LABELS[R.ACTIONS[selected]];
    const report = "Lumi · " + (scene.asleep ? "dreaming" : "content") + " · " + state.moments + " happy moments · " + pick + " · Enter to interact";
    if (report !== lastReport) { G.report(report); lastReport = report; }
  }

  function interact(action) { // One intentional tap or Enter creates one moment and a brief expressive response.
    const now = Date.now();
    state = R.interact(state, action, now);
    const asleep = R.scene(state, now).asleep;
    $("lp-message").textContent = action === "rest"
      ? (asleep ? "Lumi curls up on the cushion. Two minutes of little star dreams." : "A stretch, a blink, a happy little hello. Lumi settles beside you.")
      : MESSAGES[action][(state.moments - 1) % MESSAGES[action].length];
    room.dataset.action = action;
    $("lp-effect").textContent = { cuddle: "♡ ♡", snack: "✦", play: "✧ ✦", rest: asleep ? "z z z" : "☀" }[action];
    effectUntil = now + 4000;
    G.sound.unlock();
    G.sound.tone(action === "rest" ? 392 : 659.25, .22, 0, .09);
    G.sound.tone(action === "rest" ? 293.66 : 880, .3, .14, .06);
    save(); render();
  }

  G.onAction(function (action) { // Keyboard, TV remote and buttons use the same four activities.
    if (action === "left" || action === "right") selected = (selected + (action === "left" ? 3 : 1)) % 4;
    else if (action === "enter") { interact(R.ACTIONS[selected]); return; }
    else if (action === "up") { state = R.nextPalette(state, Date.now()); save(); }
    else if (action === "down") G.sound.toggle();
    render();
  }, { directEnter: function () { return true; } });

  buttons.forEach(function (button, index) { // Mouse/touch activate directly; Tab then Enter retains native controls.
    button.addEventListener("click", function () { selected = index; interact(R.ACTIONS[index]); });
  });
  $("lp-palette").addEventListener("click", function () { G.tap("up"); });
  $("lp-sound").addEventListener("click", function () { G.tap("down"); });
  window.addEventListener("storage", function (event) { // Another tab's latest saved memory becomes visible here too.
    if (event.key === SAVE_KEY || event.key === null) { state = read(); render(); }
  });

  function visibility() { // Hidden frames stop timers and CSS motion; returning recomputes the clock immediately.
    clearInterval(timer); timer = null;
    room.classList.toggle("lp-hidden", document.hidden);
    if (!document.hidden) { render(); timer = setInterval(render, 1000); }
  }
  document.addEventListener("visibilitychange", visibility);
  window.addEventListener("pagehide", function () { clearInterval(timer); });
  window.addEventListener("pageshow", visibility);
  save(); visibility();
})();
