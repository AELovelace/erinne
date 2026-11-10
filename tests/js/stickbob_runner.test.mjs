// StickBob Runner's rules against its real level data: physics numbers from the GameMaker game, and that a long run holds together.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const R = createRequire(import.meta.url)("../../mnemerynne/web/static/games/stickbob-runner/rules.js");
const data = JSON.parse(readFileSync(new URL("../../mnemerynne/web/static/games/stickbob-runner/assets/world.json", import.meta.url), "utf8"));

function seeded(seed) { return () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296; }   // repeatable "random" numbers
const idle = (w, extra) => Object.assign({ x: 0, y: 0, shoot: false, kick: false, slash: false, aimX: w.player.x + 50, aimY: w.player.y }, extra);   // no keys held
function run(w, frames, extra) { for (let i = 0; i < frames && !w.over; i++) R.step(w, idle(w, extra)); return w; }   // hold the same keys for a while
const flat = () => ({ ...data, slices: [data.slices[0]] });              // a level of nothing but the flat opening, for exact checks

test("the player lands on the floor and rests there", () => {
  const w = run(R.create(flat(), seeded(1)), 120);
  assert.equal(R.boxOf(w, w.player.x, w.player.y, w.player.mask).b, 352);   // feet on the floor's top edge
  assert.equal(w.over, false);
  assert.equal(w.score, 0);
});

test("running reaches StickBob's top speed and scores one point a metre", () => {
  const w = run(run(R.create(flat(), seeded(1)), 60), 120, { x: 1 });
  assert.equal(w.player.vx, 8);
  assert.equal(w.score, Math.floor((w.player.x - R.PLAYER_START.x) / R.PIXELS_PER_METER));
  assert.ok(w.score > 30);
});

test("a jump leaves the ground, rises high enough for the tallest chunk step, and lands", () => {
  const w = run(R.create(flat(), seeded(1)), 60);
  const floor = w.player.y;
  let top = floor, jumped = false;
  for (let i = 0; i < 90; i++) {
    R.step(w, idle(w, { y: i < 45 ? -1 : 0 }));                          // hold jump for a while, as a player would
    jumped = jumped || w.events.includes("jump");
    top = Math.min(top, w.player.y);
  }
  assert.ok(jumped);
  assert.ok(floor - top > 80, `rose ${floor - top}px`);                  // chunks may step up by at most 80
  assert.ok(Math.abs(w.player.y - floor) < 2);
});

test("sliding lowers the hitbox and standing up restores it", () => {
  const w = run(run(R.create(flat(), seeded(1)), 60), 40, { x: 1 });
  run(w, 3, { x: 1, y: 1 });
  assert.equal(w.player.sprite, "sprPlayerSlide");
  assert.equal(w.player.mask, "sprPlayerSlide");
  run(w, 3, { x: 1 });
  assert.equal(w.player.mask, "sprPlayerIdle");
});

test("falling out of the world ends the run after the death animation", () => {
  const pit = { ...data, slices: [{ ...data.slices[0], solids: [] }] };  // no floor at all
  const w = R.create(pit, seeded(1));
  run(w, 400);
  assert.equal(w.player.health, 0);
  assert.equal(w.player.sprite, "sprPlayerDie");
  assert.equal(w.over, true);
});

test("a hazard costs one health and is used up", () => {
  const w = run(R.create(flat(), seeded(1)), 60);
  w.chunks[0].hazards.push({ x: w.player.x - 8, y: w.player.y - 8, w: 16, h: 16 });
  R.step(w, idle(w));
  assert.equal(w.player.health, R.MAX_HEALTH - 1);
  assert.equal(w.chunks[0].hazards.length, 0);
  R.step(w, idle(w));
  assert.equal(w.player.health, R.MAX_HEALTH - 1);
});

test("the mouse aims the gun and a shot kills a SlopBot for one kill", () => {
  const w = run(R.create(flat(), seeded(1)), 60);
  w.bots.push({ kind: "gunner", x: w.player.x + 150, y: w.player.y, chunk: w.chunks[0].id, vx: 0, vy: 0, dir: -1, angle: 0, dying: false,
                sprite: "sprPlayerNoArm", frame: 0, fireEvery: 1000, cooldown: 1000, muzzle: 32, chase: 350, turned: false });
  const target = w.bots[w.bots.length - 1];
  R.step(w, idle(w, { shoot: true, aimX: target.x, aimY: target.y }));
  assert.equal(w.bullets.length, 1);
  assert.ok(Math.abs(w.player.angle) < 0.01);
  run(w, 30, { aimX: target.x, aimY: target.y });
  assert.equal(target.dying || target.gone, true);
  assert.equal(w.kills, 1);
});

test("a kick reaches beside the player but only lands once", () => {
  const w = run(R.create(flat(), seeded(1)), 60);
  w.bots.push({ kind: "gunner", x: w.player.x + 20, y: w.player.y, chunk: w.chunks[0].id, vx: 0, vy: 0, dir: -1, angle: 0, dying: false,
                sprite: "sprPlayerNoArm", frame: 0, fireEvery: 1000, cooldown: 1000, muzzle: 32, chase: 350, turned: false });
  R.step(w, idle(w, { kick: true }));
  run(w, 40);
  assert.equal(w.kills, 1);
});

test("every shipped chunk can be reached: nothing sits below the death line", () => {
  assert.ok(data.slices.length >= 30);
  for (const s of data.slices) assert.ok(Math.max(s.profile.entry, s.profile.exit) < R.KILL_Y);
});

/* The "habit runner": a steady player who holds right at full speed and jumps `lead` px before an edge, wall or spike,
   holding jump for `hold` steps. Used to check that every shipped chunk can be run through without stopping. */
function crossWithHabit(s, lead, hold) {
  const entry = s.profile.entry > 0 ? s.profile.entry : 352, exit = s.profile.exit > 0 ? s.profile.exit : entry;
  const runway = (x, y) => ({ id: 0, x, w: 640, solids: [{ x, y, w: 640, h: 16, sprite: "spr_Wall" }], bouncers: [], hazards: [] });
  const w = R.create({ ...data, slices: [data.slices[0]] }, () => 0.5);
  w.chunks = [runway(0, entry), { id: 1, x: 640, w: s.w, solids: s.solids.map(o => ({ ...o, x: 640 + o.x })),   // run-up, chunk, run-out
                                  bouncers: s.bouncers.map(o => ({ ...o, x: 640 + o.x })), hazards: s.hazards.map(h => ({ x: 640 + h[0], y: h[1], w: 16, h: 16 })) },
              runway(640 + s.w, exit)];
  w.bots = []; w.nextX = 1e9;                                            // no enemies, and nothing streams in
  const p = w.player, m = "sprPlayerIdle";
  Object.assign(p, { x: 300, y: entry - 15, vx: 8 });
  let held = 0, slow = 0;
  for (let i = 0; i < 400; i++) {
    const floorAhead = () => { for (let dy = 1; dy <= 40; dy += 3) if (R.meets(w, p.x + lead, p.y + dy, m)) return true; return false; };
    const spikeAhead = () => w.chunks.some(c => c.hazards.some(h => Math.abs(h.x + 8 - (p.x + lead)) <= 16 && Math.abs(h.y - p.y) < 40));
    if (R.meets(w, p.x, p.y + 1, m) && held <= 0 && (!floorAhead() || R.meets(w, p.x + lead, p.y - 9, m) || spikeAhead())) held = hold;
    const health = p.health;
    R.step(w, idle(w, { x: 1, y: held-- > 0 ? -1 : 0 }));
    if (p.health < health) return false;                                 // a spike, or a fall
    slow = p.x > 700 && Math.abs(p.vx) < 6 ? slow + 1 : 0;
    if (slow > 10) return false;                                         // had to stop
    if (p.x > 640 + s.w + 40) return true;
  }
  return false;
}

test("every chunk can be run through at full speed by at least a third of steady players", () => {
  const habits = [];
  for (let lead = 8; lead <= 96; lead += 8) for (const hold of [6, 12, 20, 30, 45]) habits.push([lead, hold]);
  const poor = data.slices.map((s, i) => ({ i, source: s.source, rate: habits.filter(([l, h]) => crossWithHabit(s, l, h)).length / habits.length }))
    .filter(r => r.rate < 0.3);
  assert.deepEqual(poor, [], "tune these in python/export_stickbob.py");
});

test("landing a few pixels short of a ledge keeps your speed", () => {
  const ledge = { ...data.slices[0], w: 640, solids: [{ x: 0, y: 352, w: 176, h: 16, sprite: "spr_Wall" }, { x: 240, y: 352, w: 400, h: 16, sprite: "spr_Wall" }] };
  const w = R.create({ ...data, slices: [ledge] }, seeded(1));
  Object.assign(w.player, { x: 225, y: 330, vx: 8, vy: 10.1, canJump: false });   // falling, about to clip the far ledge's top corner
  run(w, 5, { x: 1 });                                                   // it clips the corner on the third step
  assert.equal(w.player.vx, 8);
  assert.equal(R.boxOf(w, w.player.x, w.player.y, w.player.mask).b, 352);   // standing on it
});

/* A tall wall on a flat floor, with StickBob in mid-air against its left face (the wall is on his right). */
function atWall() {
  const room = { ...data.slices[0], w: 640, solids: [{ x: 0, y: 352, w: 640, h: 16, sprite: "spr_Wall" }, { x: 400, y: 100, w: 32, h: 252, sprite: "spr_Wall" }] };
  const w = R.create({ ...data, slices: [room] }, seeded(1));
  run(w, 60);                                                            // settle on the floor first
  Object.assign(w.player, { x: 393, y: 250, vx: 0, vy: 0, canJump: false, prevX: 1, prevY: 0 });   // right edge (x + 7) touches the wall at 400
  return w;
}

test("wall jump: press away from the wall, then jump, and StickBob kicks up and away", () => {
  const w = atWall(), startY = w.player.y;
  R.step(w, idle(w, { x: -1 }));                                         // press away: he holds on instead of drifting off
  assert.equal(w.player.x, 393);
  run(w, 4, { x: -1 });
  assert.equal(w.player.x, 393);
  R.step(w, idle(w, { x: -1, y: -1 }));                                  // then jump
  assert.ok(w.events.includes("walljump"));
  run(w, 15, { x: -1, y: -1 });
  assert.ok(w.player.x < 393 - 60, `went ${393 - w.player.x}px away`);
  assert.ok(w.player.y < startY - 40, `rose ${startY - w.player.y}px`);
});

test("wall jump: a jump pressed just before pressing away still counts", () => {
  const w = atWall();
  R.step(w, idle(w, { y: -1 }));                                         // jump first (nothing happens in mid-air)...
  R.step(w, idle(w, { x: -1, y: -1 }));                                  // ...then away, one step later
  assert.ok(w.events.includes("walljump"));
});

test("wall jump: jumping while pushing into the wall (the old way) does nothing", () => {
  const w = atWall();
  run(w, 3, { x: 1 });
  R.step(w, idle(w, { x: 1, y: -1 }));
  assert.ok(!w.events.includes("walljump"));
  assert.ok(w.player.vy >= 0);
});

test("wall jump: the hold lets go after a moment, and a late jump does nothing", () => {
  const w = atWall();
  run(w, 25, { x: -1 });                                                 // press away and keep waiting
  assert.ok(w.player.x < 393, "he let go and drifted away");
  R.step(w, idle(w, { x: -1, y: -1 }));
  assert.ok(!w.events.includes("walljump"));
});

test("wall jump: bouncing back and away climbs a single wall", () => {
  const w = atWall(), startY = w.player.y;
  let jumps = 0;
  for (let i = 0; i < 4; i++) {
    R.step(w, idle(w, { x: -1 })); R.step(w, idle(w, { x: -1, y: -1 }));   // away, jump
    jumps += w.events.includes("walljump") ? 1 : 0;
    for (let k = 0; k < 60 && !R.meets(w, w.player.x + 2, w.player.y, w.player.mask); k++) R.step(w, idle(w, { x: 1 }));   // back to the wall
  }
  assert.equal(jumps, 4);
  assert.ok(w.player.y < startY - 30, `climbed ${startY - w.player.y}px`);
});

test("no chunk is ever chosen that's too high to jump to", () => {
  const w = R.create(data, seeded(7));
  for (let i = 0; i < 4000; i++) {
    const before = w.lastProfile;
    w.nextX = 4000;                                                      // past the easy opening
    const picked = data.slices[R.pickSlice(w)].profile;
    if (before.exit >= 0 && picked.entry >= 0) assert.ok(before.exit - picked.entry <= R.MAX_UP_STEP);
    w.lastProfile = picked;
    w.lastIndex = data.slices.findIndex(s => s.profile === picked);
  }
});

test("a long run keeps only a bounded amount of level in memory", () => {
  for (const seed of [2, 3, 5]) {
    const w = R.create(data, seeded(seed));
    let most = 0;
    for (let i = 0; i < 12000; i++) {
      w.player.health = R.MAX_HEALTH;                                    // bullets and pits can't cut this soak test short
      w.player.x += 8; w.player.y = 200; w.player.vy = 0;                // carried along, so the level has to keep up
      R.step(w, idle(w, { x: 1, y: i % 50 < 25 ? -1 : 0 }));
      most = Math.max(most, w.chunks.length);
      assert.ok(Number.isFinite(w.player.x) && Number.isFinite(w.player.y));
    }
    assert.equal(w.over, false);
    assert.ok(w.player.x > 90000, `reached ${w.player.x}`);
    assert.ok(most <= 12, `${most} chunks`);
    assert.ok(w.bots.every(b => w.chunks.some(c => c.id === b.chunk)));  // no enemies left over from dropped chunks
  }
});
