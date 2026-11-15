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
