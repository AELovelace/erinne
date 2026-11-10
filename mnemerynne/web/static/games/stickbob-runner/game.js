/* StickBob Runner in the browser: loads the art, reads the keyboard and mouse, runs rules.js at 60 steps a second and draws
   the result. The camera, gun positions and blood overlays are ported from O_Player's Step_2 and Draw events. */
(function () {
  "use strict";

  const game = window.MGame, R = window.StickRunner;
  const stage = document.getElementById("sr-stage"), canvas = document.getElementById("sr-view"), view = canvas.getContext("2d");
  const $ = id => document.getElementById(id);

  const MAX_ZOOM = 1.5, BASE_HEIGHT = 240, ROOM_HEIGHT = 768;      // the camera shows 240 world pixels of height, up to 1.5× that at speed
  const STEP_MS = 1000 / 60;                                       // the original game's room speed

  // Where the gun sits on each animation, in pixels from the player: [x, y, drawn flipped].
  const GUN = { sprPlayerSkidLeft: [5, -3, true], sprPlayerRunLeft: [3, 1, true], sprPlayerSlideLeft: [8, 10, true], sprPlayerFallingLeft: [-3, -8, true],
                sprPlayerCrawlLeft: [-7, 8, true], sprPlayerSkid: [-5, -3, false], sprPlayerRun: [1, -1, false], sprPlayerSlide: [-8, 10, false],
                sprPlayerFalling: [-3, -8, false], sprPlayerCrawl: [7, 8, false] };

  const buffer = document.createElement("canvas");                 // the world is drawn small here, then scaled up, for crisp pixels
  buffer.width = 900; buffer.height = 400;                         // big enough for the widest, most zoomed-out view
  const pixels = buffer.getContext("2d");
  pixels.imageSmoothingEnabled = false;

  let data = null, atlas = null, backgrounds = [], world = null;
  let mode = "loading";                                            // loading, title, run, paused or over
  let aspect = 16 / 9, last = 0, spare = 0, reported = -1, overAt = 0;
  const camera = { x: 0, y: 0, w: BASE_HEIGHT * aspect, h: BASE_HEIGHT, look: 0, delay: 0 };
  const held = new Set();                                          // keys and mouse buttons down right now
  const pressed = { kick: false, slash: false };                   // "just pressed" actions waiting for the next step
  const mouse = { x: 0, y: 0 };                                    // the pointer, as a fraction of the canvas

  /* ---------------------------------------------------------------- sound */

  const audio = {};                                                // a few copies of each sound, so quick repeats can overlap
  function loadSounds(base) {
    Object.entries(data.sounds).forEach(function ([name, file]) {
      audio[name] = Array.from({ length: name === "gun" ? 6 : 1 }, function () {
        const a = new Audio(new URL(file, base).href);
        a.preload = "auto";
        return a;
      });
    });
    audio.running[0].loop = true;
  }
  function play(name, volume, offset) {                            // the original skips the first moment of some sounds; so does this
    if (!game.sound.isOn() || document.hidden) return;
    const free = audio[name].find(a => a.paused || a.ended);
    if (!free) return;                                             // all busy: skipping one is better than a pile-up
    free.volume = volume;
    try { free.currentTime = offset || 0; } catch (e) { /* not loaded yet */ }
    free.play().catch(function () { /* the browser wants a key press first */ });
  }
  function footsteps(on) {                                         // the running loop follows the player's feet
    const a = audio.running[0];
    if (on && game.sound.isOn() && !document.hidden) { if (a.paused) a.play().catch(function () {}); }
    else if (!a.paused) a.pause();
  }
  function sounds(events) {                                        // what rules.js says just happened
    events.forEach(function (e) {
      if (e === "jump") play("jump", 0.6, 0.3);
      else if (e === "shoot") play("gun", 0.3);
      else if (e === "slide" && audio.slide[0].paused) play("slide", 0.5, 0.2);
      else if (e === "hit") game.sound.tone(180, 0.12, 0, 0.3, "square", 90);
      else if (e === "kill") game.sound.tone(660, 0.1, 0, 0.2, "triangle", 1040);
      else if (e === "die") game.sound.tone(240, 0.6, 0, 0.3, "sawtooth", 45);
    });
  }

  /* ---------------------------------------------------------------- input */

  const BUTTONS = { ArrowLeft: "left", KeyA: "left", ArrowRight: "right", KeyD: "right", ArrowUp: "up", KeyW: "up", Space: "up", ArrowDown: "down", KeyS: "down" };
  // No Ctrl here: StickBob's knife key would turn W into Ctrl+W, which closes the browser tab. F and the middle button are the knife.
  const ACTIONS = { ShiftLeft: "kick", ShiftRight: "kick", KeyF: "slash" };

  window.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    game.sound.unlock();
    if (e.code === "KeyM" && !e.repeat) { game.sound.toggle(); showSoundState(); return; }
    if (BUTTONS[e.code]) held.add(BUTTONS[e.code]);
    if (ACTIONS[e.code] && !e.repeat) pressed[ACTIONS[e.code]] = true;
  });
  window.addEventListener("keyup", function (e) { if (BUTTONS[e.code]) { if (!stillHeld(BUTTONS[e.code], e.code)) held.delete(BUTTONS[e.code]); keys.delete(e.code); } });
  window.addEventListener("keydown", function (e) { if (BUTTONS[e.code]) keys.add(e.code); });
  const keys = new Set();                                          // physical keys, so letting go of W while Space is down keeps the jump held
  const stillHeld = (button, code) => [...keys].some(k => k !== code && BUTTONS[k] === button);

  function letGo() { held.clear(); keys.clear(); pressed.kick = pressed.slash = false; }   // the window lost focus: no key stays stuck

  canvas.addEventListener("pointermove", function (e) {
    const r = canvas.getBoundingClientRect();
    mouse.x = (e.clientX - r.left) / r.width;
    mouse.y = (e.clientY - r.top) / r.height;
  });
  canvas.addEventListener("pointerdown", function (e) {
    window.focus();                                                // inside Media's frame, a click hands the keyboard to the game
    game.sound.unlock();
    if (e.button === 0) held.add("shoot");
    if (e.button === 2) pressed.kick = true;
    if (e.button === 1) { pressed.slash = true; e.preventDefault(); }   // preventDefault: no autoscroll cursor
  });
  window.addEventListener("pointerup", function (e) { if (e.button === 0) held.delete("shoot"); });
  canvas.addEventListener("contextmenu", e => e.preventDefault()); // right click is the kick
  canvas.addEventListener("mousedown", function (e) { if (e.button === 1) e.preventDefault(); });

  window.addEventListener("blur", function () { letGo(); if (mode === "run") setMode("paused"); });
  document.addEventListener("visibilitychange", function () { if (document.hidden) { letGo(); footsteps(false); if (mode === "run") setMode("paused"); } });

  game.onAction(function (action) {                                // Enter, Space and P arrive here from common.js
    if (action === "play") { if (mode === "run") setMode("paused"); else if (mode === "paused") setMode("run"); }
    if (action === "enter") startOrResume();
  });
  function startOrResume() {
    if (mode === "title" || (mode === "over" && performance.now() - overAt > 500)) newRun();   // the half second stops a held jump skipping the score
    else if (mode === "paused") setMode("run");
  }

  /* ---------------------------------------------------------------- the panels */

  function showSoundState() { $("sr-sound").textContent = game.sound.isOn() ? "M sound on" : "M sound off"; }

  function setMode(next) {
    mode = next;
    const panel = $("sr-panel");
    panel.hidden = next === "run" || next === "loading";
    if (next !== "run") footsteps(false);
    last = 0; spare = 0;                                           // don't try to catch up on time spent away
    if (next === "run") { window.focus(); return; }
    if (next !== "over") $("sr-earned").textContent = "";          // the coin line belongs to the score panel only
    if (next === "title") { $("sr-heading").textContent = "Run as far as you can"; $("sr-result").textContent = ""; $("sr-start").textContent = "Run"; }
    if (next === "paused") { $("sr-heading").textContent = "Paused"; $("sr-result").textContent = ""; $("sr-start").textContent = "Keep running"; }
    if (next === "over") {
      overAt = performance.now();
      $("sr-heading").textContent = world.score >= world.best && world.score > 0 ? "New best!" : "StickBob went down";
      $("sr-result").textContent = world.score + " m · " + world.kills + (world.kills === 1 ? " kill" : " kills") + " · best " + world.best + " m";
      $("sr-start").textContent = "Run again";
      cashIn(world);
    }
  }

  /* Coins (mnemerynne/runner.py): a ticket from the server when a run starts, cashed in with the distance and kills at
     game over. If the server can't be reached the run still plays; it just can't earn. */
  let ticket = null;                                               // a promise of { run } for the run in progress, or null

  function cashIn(finished) {                                      // called once at game over, with that run's world
    const line = $("sr-earned");
    const pending = ticket;
    ticket = null;                                                 // one claim per ticket
    if (!pending) { line.textContent = "This run couldn't earn coins."; return; }
    line.textContent = "Counting coins…";
    pending.then(function (t) {
      return game.api("/api/media/games/stickbob-runner/finish", { run: t.run, meters: finished.score, kills: finished.kills });
    }).then(function (r) {
      $("sr-coins").textContent = r.coins.toLocaleString();
      if (r.refused) line.textContent = r.refused;
      else if (r.capped) line.textContent = "+" + r.paid + " coins (today's limit for StickBob Runner is reached)";
      else line.textContent = r.paid ? "+" + r.paid.toLocaleString() + (r.paid === 1 ? " coin" : " coins") : "No coins this time: 10 m or one kill earns one.";
    }).catch(function (err) { line.textContent = "Coins couldn't be counted: " + err.message; });
  }

  function newRun() {
    world = R.create(data);
    world.best = game.store.get("best", 0);                        // the best distance is kept on this device
    ticket = game.api("/api/media/games/stickbob-runner/start", {}).catch(function () { return null; })
      .then(function (t) { if (!t) throw new Error("the server didn't give this run a ticket"); return t; });
    ticket.catch(function () {});                                  // a failed ticket is reported at game over, not as an error now
    camera.look = 0; camera.delay = 0; camera.w = BASE_HEIGHT * aspect; camera.h = BASE_HEIGHT;
    reported = -1;
    setMode("run");
  }

  function updateHud() {
    $("sr-health").textContent = "♥".repeat(world.player.health) + "♡".repeat(R.MAX_HEALTH - world.player.health);
    $("sr-score").textContent = world.score + " m";
    $("sr-best").textContent = world.best + " m";
    $("sr-kills").textContent = String(world.kills);
    if (world.score !== reported) { reported = world.score; game.report(world.score + " m · " + world.kills + " kills"); }
  }

  /* ---------------------------------------------------------------- one step */

  const lerp = (a, b, t) => a + (b - a) * t;

  function moveCamera() {                                          // O_Player's Step_2: zooms out at speed and looks ahead
    const p = world.player, baseW = BASE_HEIGHT * aspect;
    let tw, th;
    if (Math.abs(p.vx) > 3) {
      const ratio = Math.min(1, Math.abs(p.vx) / 8);
      tw = lerp(camera.w, baseW * (1 + ratio * (MAX_ZOOM - 1)), 0.05);
      th = lerp(camera.h, BASE_HEIGHT * (1 + ratio * (MAX_ZOOM - 1)), 0.05);
      camera.delay = 30;
    } else if (camera.delay > 0) {
      camera.delay--; tw = camera.w; th = camera.h;                // hold the zoom for half a second
    } else {
      tw = lerp(camera.w, baseW, 0.05); th = lerp(camera.h, BASE_HEIGHT, 0.05);
    }
    camera.w = tw; camera.h = th;
    camera.look = lerp(camera.look, Math.abs(p.vx) > 1 ? Math.sign(p.vx) * 120 : 0, 0.12);
    camera.x = Math.max(0, p.x - tw / 2 + camera.look);
    camera.y = Math.max(0, Math.min(ROOM_HEIGHT - th, p.y - th / 2));
  }

  function step() {
    const x = (held.has("right") ? 1 : 0) - (held.has("left") ? 1 : 0), y = (held.has("down") ? 1 : 0) - (held.has("up") ? 1 : 0);
    const input = { x, y, shoot: held.has("shoot"), kick: pressed.kick, slash: pressed.slash,
                    aimX: camera.x + mouse.x * Math.ceil(camera.w), aimY: camera.y + mouse.y * Math.ceil(camera.h) };
    pressed.kick = pressed.slash = false;                          // a press counts once
    R.step(world, input);
    sounds(world.events);
    footsteps(world.player.moving);
    moveCamera();
    if (world.over) {
      game.store.set("best", world.best);
      setMode("over");
    }
  }

  /* ---------------------------------------------------------------- drawing */

  /* draw_sprite_ext on the small canvas: place it by its origin, flip it, and turn it (radians). */
  function sprite(name, frame, x, y, flipX, flipY, angle) {
    const s = data.sprites[name], spot = s.frames[Math.floor(frame) % s.frames.length];
    pixels.save();
    pixels.translate(Math.round(x), Math.round(y));
    if (angle) pixels.rotate(angle);
    pixels.scale(flipX ? -1 : 1, flipY ? -1 : 1);
    pixels.drawImage(atlas, spot[0], spot[1], s.w, s.h, -s.ox, -s.oy, s.w, s.h);
    pixels.restore();
  }

  function stretched(name, frame, o) {                             // level pieces are their sprite stretched to fit
    const s = data.sprites[name], spot = s.frames[frame % s.frames.length];
    pixels.drawImage(atlas, spot[0], spot[1], s.w, s.h, o.x, o.y, o.w, o.h);
  }

  function gun(who, name) {                                        // the arm follows the mouse, or the player for an enemy
    const place = GUN[who.sprite] || [0, 0, Math.abs(who.angle) > Math.PI / 2];
    sprite(name, 0, who.x + place[0], who.y + place[1], false, place[2], who.angle);
  }

  function drawPlayer(p) {
    if (p.melee > 0) sprite(p.sprite, p.frame, p.x, p.y, p.flip < 0, false, 0);
    else sprite(p.sprite, p.frame, p.x, p.y, false, false, 0);
    if (p.sprite === "sprPlayerDie") return;
    if (p.melee <= 0 && p.slash <= 0) gun(p, "sprPlayerGun");
    if (p.slash > 0) {                                             // the knife, with a little glow
      sprite("sprPlayerKnife", p.slashFrame, p.x, p.y, p.facing < 0, false, 0);
      pixels.globalCompositeOperation = "lighter";
      pixels.globalAlpha = 0.3;
      sprite("sprPlayerKnife", Math.max(0, p.slashFrame - 1), p.x, p.y, p.facing < 0, false, 0);
      pixels.globalAlpha = 1;
      pixels.globalCompositeOperation = "source-over";
    }
    const f = p.frame;                                             // the more hurt, the more blood
    if (p.health <= 4) sprite("sprPlayerhit", f, p.x, p.y, false, false, 0);
    if (p.health <= 3) sprite("sprPlayerhit", f, p.x, p.y, true, false, 0);
    if (p.health <= 2) sprite("sprPlayerPuddle", f, p.x, p.y, false, false, 0);
    if (p.health <= 1) sprite("sprPlayerPuddle", f, p.x, p.y, true, false, 0);
  }

  function draw() {
    const cw = Math.min(buffer.width, Math.ceil(camera.w)), ch = Math.min(buffer.height, Math.ceil(camera.h));
    const cx = Math.floor(camera.x), cy = Math.floor(camera.y);
    pixels.fillStyle = "#07020d";
    pixels.fillRect(0, 0, cw, ch);
    backgrounds.forEach(function (b) {                             // farthest first; each layer repeats sideways and drifts at its own speed
      const offset = (b.parallax * camera.x - camera.x) % b.w;
      for (let x = Math.floor(offset > 0 ? offset - b.w : offset); x < cw; x += b.w) pixels.drawImage(b.image, x, b.y - cy);
    });
    pixels.save();
    pixels.translate(-cx, -cy);
    const onScreen = o => o.x + o.w > cx && o.x < cx + cw && o.y + o.h > cy && o.y < cy + ch;
    if (onScreen(world.leftWall)) stretched(world.leftWall.sprite, 0, world.leftWall);
    world.chunks.forEach(function (c) {
      if (c.x + c.w < cx || c.x > cx + cw) return;                 // this whole chunk is off screen
      c.solids.forEach(o => { if (!o.hidden && onScreen(o)) stretched(o.sprite, 0, o); });
      c.bouncers.forEach(o => { if (onScreen(o)) stretched(o.sprite, 0, o); });
      c.hazards.forEach(o => { if (onScreen(o)) stretched("spr_Wall_1", 0, o); });
    });
    pixels.shadowColor = "rgba(255, 60, 60, .85)";                 // the SlopBots' red bloom
    pixels.shadowBlur = 5;
    world.bots.forEach(function (b) {
      sprite(b.sprite, b.frame, b.x, b.y, false, false, 0);
      if (!b.dying) gun(b, b.kind === "gunner" ? "sprMG" : "sprPlayerGun");
    });
    pixels.shadowBlur = 0;
    pixels.shadowColor = "transparent";
    drawPlayer(world.player);
    world.bullets.forEach(s => sprite("sprBullet", s.age, s.x, s.y, false, false, 0));
    pixels.restore();
    view.drawImage(buffer, 0, 0, cw, ch, 0, 0, canvas.width, canvas.height);   // scale the small picture up to the screen
  }

  function frame(now) {
    requestAnimationFrame(frame);
    if (mode === "run") {
      if (last) spare = Math.min(spare + now - last, STEP_MS * 5); // a long hiccup is dropped, not fast-forwarded
      last = now;
      while (spare >= STEP_MS && mode === "run") { step(); spare -= STEP_MS; }
    }
    if (world) { draw(); updateHud(); }
  }

  function resize() {                                              // fill the frame, staying sharp on high-density screens
    const scale = Math.min(2, window.devicePixelRatio || 1), r = stage.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(r.width * scale));
    canvas.height = Math.max(1, Math.round(r.height * scale));
    aspect = Math.max(1.25, Math.min(2.4, r.width / Math.max(1, r.height)));
    view.imageSmoothingEnabled = false;                            // resizing a canvas resets this
  }
  window.addEventListener("resize", resize);

  $("sr-start").addEventListener("click", function () { game.sound.unlock(); startOrResume(); });

  const loadImage = url => new Promise(function (done, fail) {
    const image = new Image();
    image.onload = () => done(image);
    image.onerror = () => fail(new Error("Couldn't load " + url));
    image.src = url;
  });

  async function start() {
    resize();
    showSoundState();
    $("sr-best").textContent = game.store.get("best", 0) + " m";
    game.showCoins("sr-coins");                                    // this account's purse, shared with every game
    try {
      const url = new URL(stage.dataset.world, location.href);     // art and sounds sit beside world.json
      data = await (await fetch(url)).json();
      atlas = await loadImage(stage.dataset.atlas);
      backgrounds = await Promise.all(data.backgrounds.map(async b => Object.assign({}, b, { image: await loadImage(new URL(b.file, url).href) })));
      loadSounds(url);
      world = R.create(data);                                      // a level to show behind the title panel
      world.best = game.store.get("best", 0);
      moveCamera();
      setMode("title");
    } catch (err) {
      $("sr-panel").hidden = false;
      $("sr-heading").textContent = "The game couldn't load";
      $("sr-result").textContent = err.message;
      $("sr-start").hidden = true;
    }
    requestAnimationFrame(frame);
  }
  start();
})();
