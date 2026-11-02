// Test the promise that absence never hurts Lumi, and the boundaries of local clock/save handling.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const R = createRequire(import.meta.url)("../../mnemerynne/web/static/games/lumi-pet/rules.js");
const at = (hour, minute = 0) => new Date(2026, 10, 2, hour, minute).getTime();

test("night follows local time, including the exact wake and sleep boundaries", () => {
  const fresh = R.normalize(null, at(12));
  for (const [hour, minute, asleep] of [[6, 59, true], [7, 0, false], [21, 59, false], [22, 0, true], [0, 0, true]]) {
    assert.deepEqual(R.scene(fresh, at(hour, minute)), { night: asleep, asleep });
  }
});

test("months away preserve every happy moment, memory and cosmetic choice", () => {
  let saved = R.interact(null, "snack", at(12));
  saved = R.nextPalette(saved, at(12));
  const later = at(12) + 180 * 86400000;
  const returned = R.normalize(saved, later);
  assert.equal(returned.moments, 1);
  assert.equal(returned.memory, "snack");
  assert.equal(returned.palette, "lagoon");
  assert.equal(returned.rest, "auto");
  assert.equal(R.scene(returned, later).asleep, false);
});

test("naps finish on time and waking at night lasts fifteen minutes", () => {
  const nap = R.interact(null, "rest", at(12));
  assert.equal(R.scene(nap, at(12) + R.NAP_MS - 1).asleep, true);
  assert.equal(R.scene(nap, at(12) + R.NAP_MS).asleep, false);
  const wake = R.interact(null, "rest", at(23));
  assert.equal(R.scene(wake, at(23)).asleep, false);
  assert.equal(R.scene(wake, at(23) + R.AWAKE_MS).asleep, true);
  const gentle = R.interact(nap, "cuddle", at(12, 1));
  assert.equal(R.scene(gentle, at(12, 1)).asleep, false);
  assert.equal(gentle.moments, 2);
});

test("damaged, unknown-version and future-clock saves cannot trap the pet or inject fields", () => {
  const fresh = R.normalize(null, at(12));
  for (const value of [null, [], "bad", 42, { version: 8, moments: 9 }]) assert.deepEqual(R.normalize(value, at(12)), fresh);
  const invalid = R.normalize({ version: 1, moments: Infinity, palette: "<script>", memory: {}, rest: "nap", restUntil: at(12) + 86400000 }, at(12));
  assert.deepEqual(invalid, fresh);
  assert.equal(R.normalize({ version: 1, moments: -4 }, at(12)).moments, 0);
  assert.equal(R.normalize({ version: 1, moments: 9999999 }, at(12)).moments, 999999);
});

test("cosmetics and invalid actions award nothing, and valid actions do not mutate the input", () => {
  const initial = R.normalize(null, at(12)), saved = JSON.stringify(initial);
  assert.deepEqual(R.interact(initial, "unknown", at(12)), initial);
  for (const action of R.ACTIONS) assert.equal(R.interact(initial, action, at(12)).moments, 1);
  let colours = initial;
  for (let i = 0; i < R.PALETTES.length; i++) colours = R.nextPalette(colours, at(12));
  assert.deepEqual(colours, initial);
  assert.equal(JSON.stringify(initial), saved);
});
