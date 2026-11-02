// Geometry checks cover every possible server result, including zero and the wraparound pocket.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const R = createRequire(import.meta.url)("../../mnemerynne/web/static/games/casino/roulette.js");
const modulo = angle => (angle % 360 + 360) % 360;

test("a full single-zero wheel has every number once and alternating red/black neighbors", () => {
  assert.deepEqual([...R.ORDER].sort((a, b) => a - b), Array.from({ length: 37 }, (_, n) => n));
  assert.equal(R.ORDER[0], 0);
  for (let i = 1; i < 36; i++) assert.notEqual(R.matchesPick(R.ORDER[i], "red"), R.matchesPick(R.ORDER[i + 1], "red"));
  for (const pick of ["red", "black", "odd", "even", "low", "high"]) {
    assert.equal(R.matchesPick(0, pick), false);
    assert.equal(R.ORDER.filter(n => R.matchesPick(n, pick)).length, 18);
  }
  assert.equal(R.matchesPick(0, "0"), true);
});

test("every landing places the ball inside the matching pocket after rotor rotation", () => {
  R.ORDER.forEach((number, index) => {
    const pose = R.settlePose(number, 1), target = R.landing(number);
    const actual = modulo(pose.ball - pose.rotor), expected = index * 360 / 37;
    assert.ok(Math.min(Math.abs(actual - expected), Math.abs(actual - expected - 360)) < 1e-9, "Wrong pocket: " + number);
    assert.equal(pose.radius, target.radius);
    assert.equal(pose.rotor, target.rotor);
    assert.deepEqual(R.settlePose(number, 0), R.spinPose(6));
    assert.ok(R.settlePose(number, .5).radius < R.spinPose(6).radius);
  });
  assert.throws(() => R.landing(37), RangeError);
});

test("spinning uses opposite directions and clamps delayed or early snapshots", () => {
  assert.ok(R.spinPose(1).rotor > R.spinPose(0).rotor);
  assert.ok(R.spinPose(1).ball < R.spinPose(0).ball);
  assert.deepEqual(R.spinPose(-2), R.spinPose(0));
  assert.deepEqual(R.spinPose(30), R.spinPose(6));
});
