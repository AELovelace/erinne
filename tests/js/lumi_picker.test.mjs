// The Home and Closet lists (picker-ui.js): one row per spot or slot, with ◀ ▶ flipping its choices.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const Pk = createRequire(import.meta.url)("../../mnemerynne/web/static/games/lumi-pet/picker-ui.js");
const data = name => JSON.parse(readFileSync(new URL("../../mnemerynne/lumipet_data/" + name, import.meta.url), "utf8"));
const house = data("house.json"), closet = data("closet.json");
const firsts = Object.fromEntries(house.spots.map(spot => [spot.id, spot.items[0].id]));

function lists(pet = {}, open = null, flip = {}) {
  const went = [];
  const snap = { pet: { house: firsts, decor: [], outfit: {}, closet: [], ...pet }, house, closet };
  const ctx = { snap, open, flip, go: change => went.push(change) };
  const find = rows => key => rows.find(r => r.key === key);
  const home = Pk.homeRows(ctx), wear = Pk.closetRows(ctx);
  return { home, wear, went, spot: find(home), slot: find(wear) };
}

test("turning steps through the choices and wraps at both ends", () => {
  assert.deepEqual([Pk.turn(["a", "b", "c"], "a", 1), Pk.turn(["a", "b", "c"], "c", 1), Pk.turn(["a", "b", "c"], "a", -1), Pk.turn(["a", "b"], "gone", 1)], ["b", "a", "c", "b"]);
  assert.equal(Pk.turn([null, "x"], null, -1), "x");
});

test("the house is one row a spot, showing what is placed; Enter there opens the full list", () => {
  const view = lists();
  assert.deepEqual(view.home.map(r => r.key), house.spots.map(spot => spot.id));
  const rug = view.spot("rug");
  assert.deepEqual([rug.shown, rug.hint, rug.send, rug.preview], ["rug-oval", "◀ Plum oval ▶ · in the room", undefined, ["rug", "rug-oval"]]);
  assert.deepEqual(rug.options, ["rug-oval", "rug-moon", "rug-star", "rug-rainbow", "rug-paw"]);
  rug.local();
  assert.deepEqual(view.went, [{ open: "rug" }]);
});

test("a flipped row previews its choice and Enter buys or places it", () => {
  const priced = lists({}, null, { rug: "rug-moon" }).spot("rug");
  assert.deepEqual([priced.hint, priced.send, priced.preview, priced.label], ["◀ Moon rug ▶ · 100 coins · Enter buys it", { action: "decorate", item: "rug-moon" }, ["rug", "rug-moon"], "Moon rug"]);
  const owned = lists({ decor: ["rug-moon"] }, null, { rug: "rug-moon" }).spot("rug");
  assert.equal(owned.hint, "◀ Moon rug ▶ · yours · Enter puts it out");
  const placed = lists({ decor: ["rug-moon"], house: { ...firsts, rug: "rug-moon" } }, null, { rug: "rug-oval" }).spot("rug");
  assert.deepEqual([placed.hint, placed.send], ["◀ Plum oval ▶ · yours · Enter puts it out", { action: "decorate", item: "rug-oval" }]);
  assert.equal(lists({}, null, { rug: "rug-retired" }).spot("rug").shown, "rug-oval", "A choice that no longer exists falls back to what is placed");
});

test("trophies appear among a spot's choices only once they are found", () => {
  assert.deepEqual(lists().spot("trophy").options, ["trophy-none"]);
  assert.deepEqual(lists({ decor: ["trophy-geode-egg"] }).spot("trophy").options, ["trophy-none", "trophy-geode-egg"]);
  const full = lists({ decor: ["trophy-geode-egg"] }, "trophy");
  assert.equal(full.home.length, 26);
  assert.match(full.spot("trophy-moon-bell").note, /waiting somewhere in the dungeon/);
  assert.deepEqual(full.spot("trophy-geode-egg").send, { action: "decorate", item: "trophy-geode-egg" });
  full.spot("back").local();
  assert.deepEqual(full.went, [{ open: null, pick: house.spots.findIndex(spot => spot.id === "trophy") }]);
});

test("the closet is the capsule and one row a place on Lumi, with nothing as a choice", () => {
  const view = lists();
  assert.deepEqual(view.wear.map(r => r.key), ["capsule", "slot-head", "slot-neck", "slot-wings", "slot-back", "slot-paws", "slot-tail"]);
  const head = view.slot("slot-head");
  assert.deepEqual([head.shown, head.hint, head.wear, head.options.length], [null, "◀ Nothing ▶ · nothing on", ["head", null], 6]);
  head.local();
  assert.deepEqual(view.went, [{ open: "head" }]);
});

test("flipping a place on Lumi tries it on, and Enter buys, wears, takes off or explains", () => {
  const key = { "slot:head": "acc-bow" };
  assert.deepEqual([lists({}, null, key).slot("slot-head").hint, lists({}, null, key).slot("slot-head").send], ["◀ Ribbon bow ▶ · Common · 60 coins · Enter buys it", { action: "wear", item: "acc-bow" }]);
  assert.equal(lists({ closet: ["acc-bow"] }, null, key).slot("slot-head").hint, "◀ Ribbon bow ▶ · Common · hers · Enter puts it on");
  const worn = lists({ closet: ["acc-bow"], outfit: { head: "acc-bow" } });
  assert.deepEqual([worn.slot("slot-head").hint, worn.slot("slot-head").icon, worn.slot("slot-head").send], ["◀ Ribbon bow ▶ · Common · wearing it", "✓", undefined]);
  const off = lists({ closet: ["acc-bow"], outfit: { head: "acc-bow" } }, null, { "slot:head": null }).slot("slot-head");
  assert.deepEqual([off.hint, off.send, off.wear], ["◀ Nothing ▶ · Enter takes it off", { action: "wear", item: "acc-bow" }, ["head", null]]);
  const crown = lists({}, null, { "slot:head": "acc-crown" }).slot("slot-head");
  assert.equal(crown.send, undefined);
  assert.match(crown.note, /only comes from the Star Capsule/);
  assert.deepEqual(lists({ closet: ["acc-crown"] }, null, { "slot:head": "acc-crown" }).slot("slot-head").send, { action: "wear", item: "acc-crown" });
});

test("a place's full list offers every accessory, as before", () => {
  const head = lists({ closet: ["acc-bow"], outfit: { head: "acc-bow" } }, "head");
  assert.deepEqual(head.wear.map(r => r.key), ["back", "acc-bow", "acc-star-clip", "acc-flower-crown", "acc-witch-hat", "acc-crown"]);
  assert.match(head.slot("acc-bow").hint, /wearing it · Enter takes it off/);
  assert.match(head.slot("acc-crown").note, /Star Capsule/);
  assert.deepEqual(head.slot("acc-star-clip").send, { action: "wear", item: "acc-star-clip" });
});
