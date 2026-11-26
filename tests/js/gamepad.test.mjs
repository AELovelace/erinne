// The gamepad helper (static/games/gamepad.js), with a pretend navigator.getGamepads: presses on fresh buttons, the
// D-pad and the stick turn into MGame's actions, held directions repeat like a keyboard, OK never repeats, and each pad
// says who it is.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const P = createRequire(import.meta.url)("../../mnemerynne/web/static/games/gamepad.js");

function pad(index, pressed, axes) { // a "standard" pad: 17 buttons, two sticks
  const buttons = Array.from({ length: 17 }, (_, n) => ({ pressed: pressed.includes(n), value: pressed.includes(n) ? 1 : 0 }));
  return { index, connected: true, buttons, axes: axes || [0, 0, 0, 0] };
}
function rig() { // a poller over pads we change by hand, recording what it says
  const said = [], state = { pads: [] };
  const poller = P.create(() => state.pads, (action, who) => said.push([action, who]));
  return { said, state, poller };
}

test("A is OK, B is back, Start pauses, the D-pad is the arrows", () => {
  const { said, state, poller } = rig();
  const map = { 0: "enter", 1: "back", 9: "play", 12: "up", 13: "down", 14: "left", 15: "right" };
  let now = 0;
  for (const [button, action] of Object.entries(map)) {
    state.pads = [pad(0, [Number(button)])]; poller.poll(now += 16);
    state.pads = [pad(0, [])]; poller.poll(now += 16);
    assert.deepEqual(said.pop(), [action, "pad:0"]);
  }
});

test("LB and RB are previous and next; the Xbox / PS button is home", () => {
  const { said, state, poller } = rig();
  let now = 0;
  for (const [button, action] of [[4, "prev"], [5, "next"], [16, "home"]]) {
    state.pads = [pad(0, [button])]; poller.poll(now += 16);
    state.pads = [pad(0, [])]; poller.poll(now += 16);
    assert.deepEqual(said.pop(), [action, "pad:0"]);
  }
  state.pads = [pad(0, [16])];
  for (let t = now; t <= now + 1000; t += 16) poller.poll(t);
  assert.equal(said.length, 1, "home never repeats");
});

test("a held button presses once; a held direction repeats like a key", () => {
  const { said, state, poller } = rig();
  state.pads = [pad(0, [0, 15])];
  for (let t = 0; t <= 1000; t += 16) poller.poll(t);
  const oks = said.filter(([a]) => a === "enter").length, rights = said.filter(([a]) => a === "right").length;
  assert.equal(oks, 1);
  assert.ok(rights >= 4 && rights <= 6, "repeats after a wait, then steadily (" + rights + ")");
});

test("the left stick is the arrows, with a little stickiness at the edge", () => {
  const { said, state, poller } = rig();
  state.pads = [pad(0, [], [0, -0.9])]; poller.poll(0);
  assert.deepEqual(said.pop(), ["up", "pad:0"]);
  state.pads = [pad(0, [], [0, -0.4])]; poller.poll(16);                 // still past a third: still held, no new press
  state.pads = [pad(0, [], [0, 0])]; poller.poll(32);
  state.pads = [pad(0, [], [0, -0.4])]; poller.poll(48);                 // from rest, under half-way isn't a press
  assert.equal(said.length, 0);
  state.pads = [pad(0, [], [0.8, 0])]; poller.poll(64);
  assert.deepEqual(said.pop(), ["right", "pad:0"]);
});

test("two pads are two players; a missing or unplugged pad is skipped", () => {
  const { said, state, poller } = rig();
  state.pads = [pad(0, [0]), null, { ...pad(2, [0]), connected: false }, pad(3, [0])];
  poller.poll(0);
  assert.deepEqual(said, [["enter", "pad:0"], ["enter", "pad:3"]]);
});

test("a pad that's unplugged lets go: plugged back in, its next press counts", () => {
  const { said, state, poller } = rig();
  state.pads = [pad(0, [9])]; poller.poll(0);
  state.pads = []; poller.poll(16);                                        // gone from the list, Start still "down" when it went
  state.pads = [pad(0, [9])]; poller.poll(32);
  assert.deepEqual(said, [["play", "pad:0"], ["play", "pad:0"]]);
  assert.deepEqual(poller.pads(), ["pad:0"]);
});

test("a browser that refuses the Gamepad API changes nothing", () => {
  const said = [];
  const poller = P.create(() => { throw new Error("blocked"); }, (a) => said.push(a));
  poller.poll(0);
  assert.deepEqual(said, []);
  assert.equal(P.start(() => {}), false);                                 // no navigator.getGamepads in Node
});

// Held walking (Lumi Pet's free walking, 2026-11-19): opt-in, analog on the stick, full on the D-pad.
function holdRig() { // a poller with onHold, recording both the actions and the walking directions
  const said = [], walks = [], state = { pads: [] };
  const poller = P.create(() => state.pads, (action, who, meta) => said.push([action, who, meta]), (who, x, y) => walks.push([who, x, y]));
  return { said, walks, state, poller };
}

test("without onHold, a direction action carries no third argument (older games see no change)", () => {
  const said = [], state = { pads: [pad(0, [15])] };
  P.create(() => state.pads, (...args) => said.push(args)).poll(0);
  assert.deepEqual(said, [["right", "pad:0"]]);
});

test("with onHold, directions are tagged held; OK isn't", () => {
  const { said, state, poller } = holdRig();
  state.pads = [pad(0, [0, 15])]; poller.poll(0);
  assert.deepEqual(said.find(([a]) => a === "right"), ["right", "pad:0", { held: true }]);
  assert.deepEqual(said.find(([a]) => a === "enter"), ["enter", "pad:0", undefined]);
});

test("the stick walks analog past the dead zone; resting under it is still", () => {
  const { walks, state, poller } = holdRig();
  state.pads = [pad(0, [], [0.1, -0.1])]; poller.poll(0);
  assert.equal(walks.length, 0, "inside the dead zone nothing walks");
  state.pads = [pad(0, [], [1, 0])]; poller.poll(16);
  assert.deepEqual(walks.pop(), ["pad:0", 1, 0]);
  state.pads = [pad(0, [], [0.575, 0])]; poller.poll(32);              // half-way past the dead zone: half speed
  assert.deepEqual(walks.pop(), ["pad:0", 0.5, 0]);
  state.pads = [pad(0, [], [0.575, 0])]; poller.poll(48);
  assert.equal(walks.length, 0, "no news while the direction stays the same");
  state.pads = [pad(0, [], [0, 0])]; poller.poll(64);
  assert.deepEqual(walks.pop(), ["pad:0", 0, 0]);
});

test("the D-pad is a full step and wins over the stick; diagonals and up are -y", () => {
  const { walks, state, poller } = holdRig();
  state.pads = [pad(0, [12, 14], [0.9, 0.9])]; poller.poll(0);
  assert.deepEqual(walks.pop(), ["pad:0", -1, -1]);
});

test("an unplugged pad stops walking", () => {
  const { walks, state, poller } = holdRig();
  state.pads = [pad(1, [15])]; poller.poll(0);
  state.pads = []; poller.poll(16);
  assert.deepEqual(walks, [["pad:1", 1, 0], ["pad:1", 0, 0]]);
});

// A Mayflash MAGIC-S PRO in Switch mode, as Firefox on Windows reported it (python/pad_probe.py, 2026-11-25):
// mapping "", buttons Y B A X L R ZL ZR - + LS RS Home Capture, and the D-pad a hat on axis 9 (1.286 at rest).
function magic(pressed, hat) {
  const buttons = Array.from({ length: 14 }, (_, n) => ({ pressed: pressed.includes(n), touched: pressed.includes(n), value: pressed.includes(n) ? 1 : 0 }));
  return { id: "20d6-a710-Controller", index: 0, connected: true, mapping: "", buttons,
    axes: [0.004, 0.004, 0.004, 0, 0, 0.004, 0, 0, 0, hat === undefined ? 1.286 : hat] };
}
const held = (p) => p.buttons.map((b, i) => (b.pressed ? i : -1)).filter((i) => i >= 0);

test("the MAGIC-S PRO's buttons come out by place, as a standard pad", () => {
  // raw (what the probe saw) -> standard: B 1 -> 0, A 2 -> 1, Y 0 -> 2, X 3 -> 3, L/R 4/5, Select 8, Start 9, Home 12 -> 16
  for (const [raw, std] of [[1, 0], [2, 1], [0, 2], [3, 3], [4, 4], [5, 5], [8, 8], [9, 9], [12, 16]]) {
    const out = P.remap(magic([raw]));
    assert.equal(out.mapping, "standard");
    assert.deepEqual(held(out), [std], `raw ${raw}`);
  }
  assert.deepEqual(P.remap(magic([])).axes, [0.004, 0.004, 0.004, 0.004]);   // left stick, then the right (Z, Rz)
});

test("its hat is the D-pad: the probe's four directions, diagonals, and let go", () => {
  for (const [v, std] of [[-1, [12]], [0.143, [13]], [0.714, [14]], [-0.429, [15]], [-0.714, [12, 15]], [1, [12, 14]], [1.286, []]]) {
    assert.deepEqual(held(P.remap(magic([], v))), std, `hat ${v}`);
  }
});

test("standard pads and pads it doesn't know are left as they come; ids from either browser", () => {
  const std = { ...magic([1]), mapping: "standard" };
  assert.equal(P.remap(std), std);
  const other = { ...magic([1]), id: "dead-beef-Something" };
  assert.equal(P.remap(other), other);
  assert.equal(P.padKind("20d6-a710-MAGIC-S PRO"), "20d6:a710");
  assert.equal(P.padKind("MAGIC-S PRO (Vendor: 20d6 Product: a710)"), "20d6:a710");   // Chrome
  assert.equal(P.padKind("f0d-c1-HORIPAD S"), "0f0d:00c1");                         // Firefox drops leading zeros
});

test("wrapping getGamepads hands every reader the standard copy, once", () => {
  const nav = { getGamepads: () => [magic([2], 0.143), null] };
  assert.equal(P.install(nav), true);
  assert.equal(P.install(nav), false);                                              // never wrapped twice
  const [first, empty] = nav.getGamepads();
  assert.equal(empty, null);
  assert.deepEqual(held(first), [1, 13]);                                           // A (right) and D-pad down
  const said = [];                                                                  // the menus' poller: the right button is back (by place), and down
  const poller = P.create(() => nav.getGamepads(), (action) => said.push(action));
  poller.poll(0);
  assert.deepEqual(said.sort(), ["back", "down"]);
});
