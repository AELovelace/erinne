/* StickBob Runner's rules: a line-by-line port of the GameMaker runner (PlayerCharacter.gml, obj_RunnerDirector and the
   SlopBot objects), one step per 1/60 s, with no drawing in it so tests/js/stickbob_runner.test.mjs can run it in Node.
   The level data comes from assets/world.json, which python/export_stickbob.py makes from the GameMaker project.

   It runs only in the browser and never reports a score to the server, so it can't pay coins (mnemerynne/games.py). */
(function (root) {
  "use strict";

  const PLAYER_START = { x: 128, y: 272 };                     // where O_Player sits in rm_Runner
  const KILL_Y = 816;                                          // room height + 48: falling past this is death
  const SPAWN_AHEAD = 1920, DESPAWN_BEHIND = 1280;             // how much level stays loaded around the player
  const PIXELS_PER_METER = 32 / 1.75;                          // the runner's scale: one point per metre
  // How far one chunk may sit above or below the last. GameMaker allowed an 80 px climb at a join, which takes a perfect
  // full jump right at the seam; 48 px is what a running hop clears, so the web game keeps joins to that.
  const MAX_UP_STEP = 48, MAX_DOWN_DROP = 192;
  const MAX_HEALTH = 5, BULLET_SPEED = 10, GUARD = 64;         // GUARD: the most pixels any "nudge until touching" loop may move
  // Falling onto a ledge a few pixels short used to count as hitting a wall and kill all speed. While falling, StickBob
  // steps up this far onto it instead (a change from GameMaker, for a smoother run).
  const LEDGE_CATCH = 16;
  // The wall jump (wallJump below), in steps at 60 a second: the same numbers as the GameMaker game's WALLJUMP_ macros.
  const WALLJUMP_HOLD = 12, WALLJUMP_BUFFER = 4, WALLJUMP_KICK = 3;   // kick 3: gentle enough to come back and climb one wall
  const DIE_SPRITE = "sprPlayerDie", IDLE = "sprPlayerIdle";

  const sign = n => (n > 0 ? 1 : n < 0 ? -1 : 0);                                   // -1, 0 or 1
  const clamp = (n, low, high) => Math.max(low, Math.min(high, n));                 // keeps n between low and high
  const aim = (fromX, fromY, toX, toY) => Math.atan2(toY - fromY, toX - fromX);     // the angle from one point to another, in radians

  /* A new run. rng is Math.random in the game and a fixed sequence in the tests. */
  function create(data, rng) {
    const w = { data, rng: rng || Math.random, chunks: [], bots: [], bullets: [], nextX: 0, lastProfile: null, lastIndex: -1,
                best: 0, kills: 0, score: 0, far: 0, over: false, locked: false, events: [], nextChunkId: 1,
                leftWall: { x: 0, y: 0, w: 16, h: 384, sprite: "spr_Wall", wall: true },   // the wall placed at the room's left edge
                player: { x: PLAYER_START.x, y: PLAYER_START.y, vx: 0, vy: 0, health: MAX_HEALTH, sprite: IDLE, mask: IDLE, frame: 0,
                          canJump: true, canSlide: false, crawling: false, cooldown: 0, facing: 1,
                          wallHold: 0, wallHoldSide: 0, jumpBuffer: 0, prevX: 0, prevY: 0,   // the wall jump (wallJump below)
                          melee: 0, slash: 0, slashFrame: 0, angle: 0, flip: 1, moving: false } };
    resetStream(w, PLAYER_START.x);
    return w;
  }

  /* ---------------------------------------------------------------- the level */

  /* _pick_weighted_slice_index: rarer when hard or a repeat, and never a chunk too high to reach. */
  function pickSlice(w) {
    const slices = w.data.slices, last = w.lastProfile;
    let total = 0;
    const weights = slices.map(function (s, i) {
      const p = s.profile;
      let weight = p.weight;
      if (i === w.lastIndex) weight *= 0.35;                                        // avoid the same chunk twice running
      if (w.nextX < 2000) {                                                         // keep the first stretch easy
        if (p.difficulty >= 6) weight *= 0.08;
        else if (p.difficulty >= 4) weight *= 0.35;
      }
      if (last) {
        if (last.exit >= 0 && p.entry >= 0) {
          const up = last.exit - p.entry, down = p.entry - last.exit;               // screen y grows downward
          if (up > MAX_UP_STEP) weight = 0;                                         // can't be hopped up to at a run
          else if (up > 32) weight *= 0.5;                                          // can, but rather not
          if (down > MAX_DOWN_DROP) weight *= 0.25;
          else if (down > 128) weight *= 0.6;
        } else weight *= 0.7;
        const change = Math.abs(p.difficulty - last.difficulty);                    // no sudden difficulty spikes
        if (change >= 3) weight *= 0.55;
        if (p.difficulty > last.difficulty + 1) weight *= 0.6;
      }
      total += weight;
      return weight;
    });
    if (total <= 0) {                                                               // nothing fits: GameMaker took an easy starter, which can
      let best = 0, lowest = Infinity;                                              // be a sheer wall away; take the smallest climb instead
      slices.forEach(function (s, i) {
        const climb = last && last.exit >= 0 && s.profile.entry >= 0 ? last.exit - s.profile.entry : 0;
        if (climb < lowest) { lowest = climb; best = i; }
      });
      return best;
    }
    let roll = w.rng() * total;
    for (let i = 0; i < weights.length; i++) { roll -= weights[i]; if (roll <= 0) return i; }
    return 0;
  }

  /* _spawn_chunk: copy one slice into the world at nextX. */
  function spawnChunk(w) {
    const index = w.nextX < 480 ? 0 : pickSlice(w);                                 // the opening is always flat
    const s = w.data.slices[index], ox = w.nextX, id = w.nextChunkId++;
    w.chunks.push({ id, x: ox, w: s.w,
      solids: s.solids.map(o => Object.assign({}, o, { x: ox + o.x })),             // the slice's numbers are relative to its left edge
      bouncers: s.bouncers.map(o => Object.assign({}, o, { x: ox + o.x })),
      hazards: s.hazards.map(h => ({ x: ox + h[0], y: h[1], w: 16, h: 16 })) });
    s.bots.forEach(b => w.bots.push(makeBot(b[0], ox + b[1], b[2], id)));
    w.nextX += s.w;
    w.lastProfile = s.profile;
    w.lastIndex = index;
  }

  /* _reset_stream_at: throw the level away and rebuild it around x (the player walked back past what was loaded). */
  function resetStream(w, centerX) {
    w.chunks = []; w.bots = []; w.lastProfile = null; w.lastIndex = -1;
    w.nextX = Math.max(0, Math.floor((centerX - 256) / 16) * 16);                   // stays on the 16 px grid
    while (w.nextX < centerX + SPAWN_AHEAD) spawnChunk(w);
  }

  /* Add level ahead of the player and drop what's far behind. */
  function stream(w) {
    const p = w.player;
    if (!w.chunks.length || p.x + 64 < w.chunks[0].x) resetStream(w, p.x);
    while (w.nextX < p.x + SPAWN_AHEAD) spawnChunk(w);
    while (w.chunks.length && w.chunks[0].x + w.chunks[0].w < p.x - DESPAWN_BEHIND) {
      const gone = w.chunks.shift();
      w.bots = w.bots.filter(b => b.chunk !== gone.id);                             // its enemies go with it
    }
  }

  /* ---------------------------------------------------------------- collisions */

  /* The hitbox of a sprite placed at x, y. GameMaker rounds positions when it checks collisions, so this does too. */
  function boxOf(w, x, y, mask) {
    const b = w.data.sprites[mask].box, rx = Math.round(x), ry = Math.round(y);
    return { l: rx + b[0], t: ry + b[1], r: rx + b[2], b: ry + b[3] };
  }

  /* Do a box and one solid touch? A slope only counts below its surface in each column. */
  function touches(w, box, s) {
    if (box.r <= s.x || box.l >= s.x + s.w || box.b <= s.y || box.t >= s.y + s.h) return false;
    const tops = w.data.sprites[s.sprite].tops;
    if (!tops) return true;                                                         // a plain block
    const sx = s.w / tops.length, sy = s.h / w.data.sprites[s.sprite].h;            // how much this slope is stretched
    for (let col = Math.max(box.l, s.x); col < Math.min(box.r, s.x + s.w); col++) {
      if (box.b > s.y + tops[Math.floor((col - s.x) / sx)] * sy) return true;       // the box reaches below this column's surface
    }
    return false;
  }

  /* place_meeting against every solid near the box. */
  function meets(w, x, y, mask) {
    const box = boxOf(w, x, y, mask);
    if (touches(w, box, w.leftWall)) return true;
    for (const c of w.chunks) {
      if (box.r + 64 <= c.x || box.l - 64 >= c.x + c.w) continue;                   // nowhere near (64 px spare: some pieces poke past their chunk's edge)
      for (const s of c.solids) if (touches(w, box, s)) return true;
    }
    return false;
  }

  const overlapping = (a, r) => !(a.r <= r.x || a.l >= r.x + r.w || a.b <= r.y || a.t >= r.y + r.h);   // a hitbox against a plain rectangle

  /* distance_to_object: the gap between two hitboxes, 0 if they overlap. */
  function gap(a, b) {
    const dx = Math.max(0, a.l - b.r, b.l - a.r), dy = Math.max(0, a.t - b.b, b.t - a.b);
    return Math.hypot(dx, dy);
  }

  /* collision_line against obj_Wall: can a Gunner or Slacker see the player? Checked every 8 px. */
  function wallBetween(w, x1, y1, x2, y2) {
    const steps = Math.ceil(Math.hypot(x2 - x1, y2 - y1) / 8);
    for (let i = 1; i < steps; i++) {
      const x = x1 + (x2 - x1) * i / steps, y = y1 + (y2 - y1) * i / steps, point = { l: x, t: y, r: x + 1, b: y + 1 };
      for (const c of w.chunks) for (const s of c.solids) if (s.wall && touches(w, point, s)) return true;
    }
    return false;
  }

  function fire(w, x, y, angle, distance, owner) {                                  // a bullet appears a little way out from the shooter
    w.bullets.push({ x: x + Math.cos(angle) * distance, y: y + Math.sin(angle) * distance, angle, owner, age: 0 });
    w.events.push("shoot");
  }

  /* ---------------------------------------------------------------- the player */

  /* paddle_movement. Its quirks are the game's feel, so they stay, including gravity being applied twice. The tilemap
     checks are gone because rm_Runner has no tiles, and without them the coyote timer was always 0, so it's gone too. */
  function move(w, input) {
    const p = w.player, accel = 0.3, grav = 0.4, xi = input.x, yi = input.y, mask = p.mask;
    p.vx = clamp(p.vx + xi * accel, -8, 8);
    p.vy = clamp(p.vy + yi * accel, -15, 15);
    p.vy += grav;
    if (xi === 0) p.vx = p.vx > 0 ? Math.max(0, p.vx - accel) : Math.min(0, p.vx + accel);   // friction when no key is held
    if (yi === 0) p.vy = p.vy > 0 ? Math.max(0, p.vy - accel) : Math.min(0, p.vy + accel);
    if (p.vy < 10) p.vy += grav;
    const falling = p.vy > 1;                                                       // noted now: landing below sets vy to 0 (standing still is +0.5)
    wallJump(w, xi, yi);                                                            // may hold on to a wall, or kick off it

    if (meets(w, p.x, p.y + p.vy, mask)) {                                          // floor or ceiling
      p.canJump = true;
      const step = sign(p.vy);
      for (let i = 0; step && i < GUARD && !meets(w, p.x, p.y + step, mask); i++) p.y += step;   // move right up to it
      p.vy = 0;
      if (yi === -1 && meets(w, p.x, p.y + 1, mask) && p.canJump) {                 // standing on something and pressing up
        p.vy = -10; p.canJump = false; w.events.push("jump");
      }
    }

    if (meets(w, p.x + p.vx, p.y, mask)) {                                          // sideways into something: maybe a slope
      const step = sign(p.vx);
      if (step === 0) p.vx = 0;
      else {
        for (let i = 0; i < GUARD && !meets(w, p.x + step, p.y, mask); i++) p.x += step;   // move right up to it
        let rise = 0;
        const reach = falling ? LEDGE_CATCH : 8;                                    // 8 px on foot, as in GameMaker; more when landing
        while (meets(w, p.x + p.vx, p.y - rise, mask) && rise < reach) rise++;      // can it be stepped over?
        if (!meets(w, p.x + p.vx, p.y - rise, mask)) p.y -= rise;                   // yes: walk up it
        else p.vx = 0;                                                              // no: it's a wall
      }
    }

    if (yi === 0) { p.canSlide = true; p.crawling = false; p.mask = IDLE; }         // upright again
    if (yi === 1) {
      if (p.canSlide && p.vx > 0) p.vx += 2;                                        // a slide's speed boost, once per press
      else if (p.canSlide && p.vx < 0) p.vx -= 2;
      else if (p.vx >= -0.3 && p.vx < 0.3) p.crawling = true;                       // pressing down while still: crawl
      p.canSlide = false;
    }
    p.x += p.vx;
    p.y += p.vy;
    if (p.cooldown > 0) p.cooldown--;
  }

  /* The wall jump, Super Metroid style (the same as PlayerCharacter.gml since 2026-11-11). In the air against any wall,
     press AWAY from it and StickBob holds on for WALLJUMP_HOLD steps; press jump during that time to kick up and away.
     A jump pressed up to WALLJUMP_BUFFER steps before pressing away counts too. Pressing back toward the wall and away
     again climbs a single wall. */
  function wallJump(w, xi, yi) {
    const p = w.player, mask = p.mask;
    const jumpPressed = yi === -1 && p.prevY !== -1;                                // jump went down this step
    p.jumpBuffer = jumpPressed ? WALLJUMP_BUFFER : Math.max(0, p.jumpBuffer - 1);
    const airborne = !meets(w, p.x, p.y + 1, mask);
    const side = meets(w, p.x + 2, p.y, mask) ? 1 : meets(w, p.x - 2, p.y, mask) ? -1 : 0;   // 1: wall on the right
    if (airborne && side !== 0 && xi === -side && p.prevX !== -side) { p.wallHold = WALLJUMP_HOLD; p.wallHoldSide = side; }   // just pressed away: grab it
    if (p.wallHold > 0) {
      if (!airborne || !meets(w, p.x + p.wallHoldSide * 2, p.y, mask)) p.wallHold = 0;   // landed, or slid past the wall's end
      else if (p.jumpBuffer > 0) {
        p.vx = -p.wallHoldSide * WALLJUMP_KICK;                                     // kick away from the wall...
        p.vy = -10;                                                                 // ...as high as a normal jump
        p.canJump = false; p.wallHold = 0; p.jumpBuffer = 0;
        w.events.push("jump", "walljump");
      } else {
        p.vx = 0;                                                                   // hold on: don't drift away yet
        p.vy = Math.min(p.vy, 2);                                                   // and slide down slowly
        p.wallHold--;
      }
    }
    p.prevX = xi;                                                                   // for next step's "just pressed" checks
    p.prevY = yi;
  }

  /* playerSpriteIndexer: which animation to show. Slides and crawls also change the hitbox. */
  function chooseSprite(w, input) {
    const p = w.player, xi = input.x, yi = input.y, side = p.vx < 0 ? "Left" : "";
    if (w.locked) { /* a kick or a death owns the sprite */ }
    else if (Math.abs(p.vx) < 3.5 && Math.abs(p.vx) > 0.05 && xi === 0 && !p.crawling) p.sprite = "sprPlayerSkid" + side;
    else if (p.vx > 0.1 && xi === 1 && p.canSlide) p.sprite = "sprPlayerRun";
    else if (p.vx < -0.1 && xi === -1 && p.canSlide) p.sprite = "sprPlayerRunLeft";
    else if (p.crawling) {
      p.sprite = p.vx < 0 && yi === 1 ? "sprPlayerCrawlLeft" : "sprPlayerCrawl";
      p.mask = p.sprite;
    } else if (p.vx !== 0 && yi === 1) {
      p.sprite = "sprPlayerSlide" + side;
      p.mask = p.sprite;
      w.events.push("slide");
    } else if (p.vy > 0.5 && yi !== -1) p.sprite = "sprPlayerFalling" + side;
    else if (xi === 0 && yi === 0 && p.vx === 0) p.sprite = IDLE;
    if (xi > 0 || p.vx > 0.1) p.facing = 1;                                         // which way a kick or knife faces
    else if (xi < 0 || p.vx < -0.1) p.facing = -1;
  }

  function hurtBot(w, b) {                                                          // an enemy has one hit point, whatever hit it
    b.dying = true; b.frame = 0; b.sprite = DIE_SPRITE; b.vx = 0; b.vy = 0;
    w.kills++;
    w.events.push("kill");
  }

  /* playerMelee and playerSlash. The kick lands on one frame, with reach either side; the knife hits anything touching. */
  function attacks(w, input) {
    const p = w.player;
    if (input.kick && p.melee <= 0) { p.melee = 32; p.frame = 0; p.flip = p.facing; }
    if (p.melee > 0) {
      p.sprite = "sprPlayerKick";
      w.locked = true;
      p.melee--;
      if (p.melee === 28) {                                                         // the second frame of the animation
        const box = boxOf(w, p.x, p.y, p.mask), reach = box.r - box.l;
        const wide = { l: box.l - reach, t: box.t, r: box.r + reach, b: box.b };
        w.bots.forEach(b => { if (!b.dying && hits(w, wide, b)) hurtBot(w, b); });
      }
      if (p.melee === 0) { w.locked = false; p.flip = 1; }
    }
    if (input.slash && p.slash <= 0) p.slash = 22;
    if (p.slash > 0) {
      p.slashFrame = Math.min(Math.floor((22 - p.slash) / 2), 10);                  // read by the drawing code
      p.slash--;
      const box = boxOf(w, p.x, p.y, p.mask);
      w.bots.forEach(b => { if (!b.dying && hits(w, box, b)) hurtBot(w, b); });
    }
  }
  const hits = (w, box, b) => { const o = boxOf(w, b.x, b.y, IDLE); return !(box.r <= o.l || box.l >= o.r || box.b <= o.t || box.t >= o.b); };   // a box against an enemy

  /* Lose health, or start dying when it runs out. */
  function damage(w, amount) {
    const p = w.player;
    if (p.sprite === DIE_SPRITE) return;
    p.health -= amount;
    w.events.push("hit");
    if (p.health <= 0) die(w);
  }

  function die(w) {
    const p = w.player;
    p.health = 0; p.sprite = DIE_SPRITE; p.frame = 0; w.locked = true; w.events.push("die");
  }

  function stepPlayer(w, input) {
    const p = w.player, previous = { x: p.x, y: p.y }, dying = p.sprite === DIE_SPRITE;
    const held = dying ? { x: 0, y: 0, shoot: false, kick: false, slash: false, aimX: p.x, aimY: p.y } : input;   // the dead don't steer
    move(w, held);
    p.angle = aim(p.x, p.y, held.aimX, held.aimY);
    if (held.shoot && p.cooldown <= 0 && p.melee <= 0 && !held.kick && p.slash <= 0) { fire(w, p.x, p.y, p.angle, 32, "player"); p.cooldown = 10; }
    chooseSprite(w, held);
    if (!dying && p.melee <= 0) w.locked = false;
    attacks(w, held);
    if (p.y > KILL_Y && p.sprite !== DIE_SPRITE) die(w);                            // fell out of the world

    const box = boxOf(w, p.x, p.y, p.mask);
    for (const c of w.chunks) {
      c.hazards = c.hazards.filter(function (h) {                                   // spikes hurt once, then they're gone
        if (!overlapping(box, h)) return true;
        damage(w, 1);
        return false;
      });
      for (const pad of c.bouncers) {
        if (!overlapping(box, pad)) continue;
        if (pad.dir === "up") { p.vy -= 25; p.x = previous.x; p.y = previous.y; }   // objBouncyBottom is a solid object, so GameMaker puts you back
        else if (pad.dir === "down") p.vy += 15;
        else if (pad.dir === "left") p.vx -= 15;
        else p.vx += 15;
        p.canJump = true;
      }
    }

    const frames = w.data.sprites[p.sprite].frames.length;
    p.frame += w.data.sprites[p.sprite].fps / 60;                                   // animations run at their own speed
    if (p.frame >= frames) {
      if (p.sprite === DIE_SPRITE) { w.over = true; p.frame = frames - 1; }         // the death animation finished: game over
      else p.frame %= frames;
    }

    p.moving = !dying && held.x !== 0 && held.y === 0 && meets(w, p.x, p.y + 1, p.mask);   // the footsteps sound
    if (!dying) {                                                                   // the score is the farthest point reached
      const reached = Math.max(0, p.x - PLAYER_START.x);
      if (reached > w.far) { w.far = reached; w.score = Math.floor(reached / PIXELS_PER_METER); w.best = Math.max(w.best, w.score); }
    }
  }

  /* ---------------------------------------------------------------- the enemies */

  function makeBot(kind, x, y, chunk) {                                             // the three SlopBots differ only in these numbers
    const gunner = kind === "gunner", patrol = kind === "patrol";
    return { kind, x, y, chunk, vx: 0, vy: 0, dir: -1, angle: 0, dying: false, sprite: gunner ? "sprPlayerNoArm" : IDLE, frame: 0,
             fireEvery: gunner ? 10 : 30, cooldown: gunner ? 10 : 30, muzzle: patrol ? 48 : 32, chase: patrol ? 250 : gunner ? 350 : 100,
             turned: false };
  }

  function stepBot(w, b) {
    const p = w.player;
    if (b.dying) {                                                                  // play the death animation, then vanish
      b.frame += w.data.sprites[DIE_SPRITE].fps / 60;
      if (b.frame >= w.data.sprites[DIE_SPRITE].frames.length) b.gone = true;
      return;
    }
    const distance = gap(boxOf(w, b.x, b.y, IDLE), boxOf(w, p.x, p.y, IDLE));
    const shootAt = () => {                                                         // count down, then fire if nobody is locked in a kick or a death
      b.angle = aim(b.x, b.y, p.x, p.y);
      if (b.cooldown > 0) b.cooldown--;
      if (b.cooldown <= 0 && !w.locked && (b.kind !== "patrol" || distance < 100)) { fire(w, b.x, b.y, b.angle, b.muzzle, "bot"); b.cooldown = b.fireEvery; }
    };

    if (b.kind === "gunner") {                                                      // a turret: it never moves
      if (distance < b.chase && !wallBetween(w, b.x, b.y, p.x, p.y)) shootAt();
      b.frame = (b.frame + w.data.sprites[b.sprite].fps / 60) % w.data.sprites[b.sprite].frames.length;
      return;
    }

    b.vy += 0.3;
    if (b.kind === "slacker" && b.vy < 10) b.vy += 0.3;
    if (b.kind === "patrol") {
      if (distance > 50) b.vx = 2 * b.dir;
      const pad = nearestPad(w, b);                                                 // patrollers turn back at bounce pads
      if (pad !== null) {
        if (pad <= 100 && !b.turned) { b.dir *= -1; b.turned = true; }
        if (pad >= 100) b.turned = false;
      }
    }
    if (meets(w, b.x + b.vx, b.y, IDLE)) {                                          // walked into something: hop back and turn round
      if (b.vx > 0) { b.x -= 20; b.dir = -1; b.angle = Math.PI; }
      if (b.vx < 0) { b.x += 20; b.dir = 1; b.angle = 0; }
    }
    if (meets(w, b.x, b.y + b.vy, IDLE)) {                                          // land on the ground
      for (let i = 0, step = sign(b.vy); step && i < GUARD && !meets(w, b.x, b.y + step, IDLE); i++) b.y += step;
      b.vy = 0;
    }
    if (!meets(w, b.x + 2 * b.dir, b.y + 2, IDLE)) b.dir *= -1;                     // never walk off a ledge

    const sees = b.kind === "patrol" ? distance > 50 && distance < b.chase : distance < b.chase && !wallBetween(w, b.x, b.y, p.x, p.y);
    if (sees) {
      b.dir = p.x < b.x ? -1 : 1;                                                   // turn to face the player
      if (p.y < b.y - 32 && b.vy === 0) b.vy = -7;                                  // jump if the player is above
      if (b.kind === "slacker") b.vx = 2 * b.dir;
      shootAt();
    }
    if (b.kind === "patrol" && meets(w, b.x + b.vx, b.y, IDLE)) {                   // the same slope climbing the player has
      for (let i = 0, step = sign(b.vx); step && i < GUARD && !meets(w, b.x + step, b.y, IDLE); i++) b.x += step;
      let rise = 0;
      while (meets(w, b.x + b.vx, b.y - rise, IDLE) && rise < 8) rise++;
      if (!meets(w, b.x + b.vx, b.y - rise, IDLE)) b.y -= rise; else b.vx = 0;
    }
    b.x += b.vx;
    b.y += b.vy;
    b.sprite = b.vx > 0.1 ? "sprPlayerRun" : b.vx < -0.1 ? "sprPlayerRunLeft" : IDLE;
    b.frame = (b.frame + w.data.sprites[b.sprite].fps / 60) % w.data.sprites[b.sprite].frames.length;
    if (b.y > KILL_Y) b.gone = true;                                                // fell out of the world
  }

  function nearestPad(w, b) {                                                       // how far away the closest upward bounce pad is, or null
    let best = null;
    for (const c of w.chunks) for (const pad of c.bouncers) {
      if (pad.dir !== "up") continue;
      const d = Math.hypot(pad.x - b.x, pad.y - b.y);
      if (best === null || d < best) best = d;
    }
    return best;
  }

  /* ---------------------------------------------------------------- bullets */

  function stepBullets(w) {
    const p = w.player;
    w.bullets = w.bullets.filter(function (s) {
      s.x += Math.cos(s.angle) * BULLET_SPEED;
      s.y += Math.sin(s.angle) * BULLET_SPEED;
      s.age++;
      if (s.age > 120) return false;                                                // two seconds is well off screen
      const box = { l: Math.round(s.x) - 2, t: Math.round(s.y) - 2, r: Math.round(s.x) + 2, b: Math.round(s.y) + 2 };
      if (touches(w, box, w.leftWall)) return false;
      for (const c of w.chunks) {
        if (box.r > c.x && box.l < c.x + c.w) for (const o of c.solids) if (touches(w, box, o)) return false;   // walls stop bullets
      }
      if (s.owner !== "player" && p.sprite !== DIE_SPRITE && overlapsPlayer(w, box)) { damage(w, 1); return false; }
      for (const b of w.bots) {
        if (b.dying || !hits(w, box, b)) continue;
        if (s.owner === "player") hurtBot(w, b);                                    // your shots count as kills
        else { b.dying = true; b.frame = 0; b.sprite = DIE_SPRITE; b.vx = 0; b.vy = 0; }   // friendly fire is real, but earns you nothing
        return false;
      }
      return true;
    });
  }
  const overlapsPlayer = (w, box) => { const o = boxOf(w, w.player.x, w.player.y, w.player.mask); return !(box.r <= o.l || box.l >= o.r || box.b <= o.t || box.t >= o.b); };   // uses the slide's lower hitbox, so sliding ducks bullets

  /* One 1/60 s of the game. input: x and y (-1, 0, 1), shoot (held), kick and slash (just pressed), aimX and aimY (the mouse in world coordinates). */
  function step(w, input) {
    w.events = [];                                                                  // this step's sounds, for game.js
    if (w.over) return w;
    stepPlayer(w, input);
    w.bots.forEach(b => stepBot(w, b));
    w.bots = w.bots.filter(b => !b.gone);
    stepBullets(w);
    stream(w);
    return w;
  }

  const api = { create, step, boxOf, meets, pickSlice, MAX_HEALTH, KILL_Y, PLAYER_START, PIXELS_PER_METER, MAX_UP_STEP };
  root.StickRunner = api;
  if (typeof module !== "undefined") module.exports = api;                          // for the Node tests
})(typeof self !== "undefined" ? self : globalThis);
