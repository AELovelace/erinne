// Prism Drop's rules (mnemerynne/web/static/games/prism-drop/rules.js), run with `node --test "tests/js/*.test.mjs"`.
// tests/test_media.py runs this too when Node is installed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const R = require("../../mnemerynne/web/static/games/prism-drop/rules.js");

function seeded(seed) { // A repeatable draw(n) for the tests (a small LCG): the same seed, the same drop.
  let s = seed >>> 0;
  return n => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s % n; };
}

test("a field has 28 special pegs in unique cells, none on the entry row", () => {
  const field = R.randomObstacles(seeded(1));
  assert.equal(field.length, 28);
  assert.deepEqual(Object.fromEntries(["block", "bomb", "coin"].map(t => [t, field.filter(p => p.type === t).length])), { block: 11, bomb: 9, coin: 8 });
  assert.equal(new Set(field.map(p => p.row + ":" + p.column)).size, 28);
  for (const p of field) { assert.ok(p.row >= 1 && p.row < R.HEIGHT); assert.ok(p.column >= 1 && p.column <= R.WIDTH); }
});

test("every drop enters at pin 4-7, stays inside the field and lands in a pocket", () => {
  for (let seed = 0; seed < 500; seed++) {
    const round = R.play(5, seeded(seed));
    const entry = round.trajectory[0];
    assert.equal(entry.row, 0);
    assert.ok(entry.column >= 4 && entry.column <= 7);
    for (const p of round.trajectory) assert.ok(p.column >= 1 && p.column <= R.WIDTH && p.row >= 0 && p.row <= R.HEIGHT);
    assert.equal(round.trajectory.at(-1).row, R.HEIGHT);
    assert.ok(round.landing >= 1 && round.landing <= R.WIDTH);
    assert.ok(round.trajectory.length < 200);                      // single-use bombs: gravity always wins
  }
});

test("landing points: 20, 15, 10, then nothing", () => {
  assert.equal(R.landingPoints(5, 5), 20);
  assert.equal(R.landingPoints(5, 4), 15);
  assert.equal(R.landingPoints(5, 7), 10);
  assert.equal(R.landingPoints(1, 4), 0);
  assert.equal(R.landingPoints(10, 1), 0);
});

test("a drop's points are its landing points plus each coin peg's 1-5, collected once", () => {
  for (let seed = 0; seed < 300; seed++) {
    const round = R.play(3, seeded(seed));
    const coins = round.trajectory.filter(p => p.hit === "coin");
    assert.equal(new Set(coins.map(p => p.row + ":" + p.column)).size, coins.length);   // never twice
    for (const p of coins) assert.ok(p.coins >= 1 && p.coins <= 5);
    assert.equal(round.bonus, coins.reduce((sum, p) => sum + p.coins, 0));
    assert.equal(round.points, R.landingPoints(3, round.landing) + round.bonus);
  }
});

test("a bomb fires once per drop, and a block shoves two columns", () => {
  const field = [{ row: 1, column: 5, type: "block" }];
  const round = R.drop(seeded(7), field);
  const hit = round.trajectory.find(p => p.hit === "block");
  if (hit) assert.equal(Math.abs(round.trajectory[round.trajectory.indexOf(hit) + 1].column - hit.column) % 2, 0);
  for (let seed = 0; seed < 300; seed++) {
    const r = R.play(5, seeded(seed));
    const bombs = r.trajectory.filter(p => p.hit === "bomb").map(p => p.row + ":" + p.column);
    assert.equal(new Set(bombs).size, bombs.length);
  }
});

test("the browser's draw stays below its bound", () => {
  if (!globalThis.crypto?.getRandomValues) return;               // Node 19+ has it
  for (const n of [1, 2, 4, 10, 190]) for (let i = 0; i < 200; i++) { const v = R.cryptoDraw(n); assert.ok(Number.isInteger(v) && v >= 0 && v < n); }
});
