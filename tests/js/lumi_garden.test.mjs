// The Stardust Garden's lists and words (garden-ui.js) and its art (garden-art.js), against the server's catalog
// (garden.json) and a garden shaped the way mnemerynne/lumigarden.py view() describes one.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const UI = require("../../mnemerynne/web/static/games/lumi-pet/garden-ui.js");
const Art = require("../../mnemerynne/web/static/games/lumi-pet/garden-art.js");
const catalog = JSON.parse(readFileSync(new URL("../../mnemerynne/lumipet_data/garden.json", import.meta.url), "utf8"));
const MIN = 60000;

function garden(extra = {}) { // A young garden: a daisy growing, a daisy ready, two empty plots; glowbell is next up the ladder.
  const plot = (flower, stage, ready_in, grow) => ({ flower, last: flower, stage, ready_in, grow });
  return { at: 0, gardens: 1, plot_price: 25, given: 12, powder: 120, squeezed: 10, price: 40, coins_today: 3, band: 100, away: null, max_rate: 12,
    plots: [plot("moondaisy", "growing", MIN, 2 * MIN), plot("moondaisy", "ready", -5000, 2 * MIN), plot(null, "empty", 0, 0), plot(null, "empty", 0, 0)],
    flowers: catalog.flowers.map((flower, i) => ({ ...flower, unlocked: i === 0, next: i === 1, powder_now: flower.powder, minutes_now: flower.minutes })),
    rates: { tap: 3, lucky: 0.03, lucky_by: 5, idle: 0, double: 1, growth: 1, fresh: 2, kept: 0.5, yield: 1, gift: 0.03, away_hours: 8 },
    helpers: catalog.helpers.map(helper => ({ id: helper.id, name: helper.name, about: helper.about, level: 0, cost: helper.cost })),
    moves: [{ id: "pounce", move: "Pounce", tree: "fighter", name: "Frenzy", about: "presses count for much more", strength: 0.5, ready_in: 0 }],
    effects: {}, water: { ready_in: 0, rounds: 8, share: 5, show: null }, feed: { ready_in: 0, seconds: 10, per: 10, max: 10, until_in: null },
    cosmetics: catalog.cosmetics.map(thing => ({ ...thing, owned: false })), upgrades: catalog.upgrades.map(thing => ({ ...thing, count: 0 })),
    moonrise: { needs: 100000, ready: false, count: 0 }, ...extra };
}
function open(view, ctx = {}) {
  const went = [];
  const rows = UI.rows({ garden: view, drawer: null, seed: null, cursor: 0, mini: null, passed: 0, pending: 0, level: 1, go: change => went.push(change), ...ctx });
  return { rows, went, row: key => rows.find(r => r.key === key) };
}
const names = svg => [...svg.matchAll(/data-click="([^"]+)"/g)].map(m => m[1]);

test("numbers stay short however big they get", () => {
  assert.deepEqual([0, 7, 7.25, 12.9, 950, 1000, 1234, 99949, 123456, 999960, 3.4e6, 5.6e9, 7.8e12, 1.2e15, 3e18, 1e93].map(UI.format),
    ["0", "7", "7.3", "12", "950", "1.0K", "1.2K", "99.9K", "123K", "1.0M", "3.4M", "5.6B", "7.8T", "1.2aa", "3.0ab", "1.0ba"]);
  assert.deepEqual([0, 999, 65000, 7380000, -5].map(UI.clock), ["0:00", "0:01", "1:05", "2:03:00", "0:00"]);
  assert.deepEqual([2, 60, 90, 720].map(UI.span), ["2 min", "1 h", "1 h 30", "12 h"]);
});

test("the garden's list offers the press, the harvest, both minigames, her moves, and the drawers", () => {
  const list = open(garden());
  assert.deepEqual(list.rows.map(r => r.key), ["press", "trim-all", "seeds", "water", "feed", "move-pounce", "helpers", "shop", "moon", "leave"]);
  list.row("press").local();
  list.row("seeds").local();
  list.row("leave").local();
  assert.deepEqual(list.went, [{ press: 1 }, { drawer: "seeds" }, { leave: true }]);
  assert.deepEqual(list.row("trim-all").send, { action: "trim_all" });
  assert.match(list.row("trim-all").name, /\(1\)/);
  assert.deepEqual(list.row("water").send, { action: "water_start" });
  assert.deepEqual(list.row("feed").send, { action: "feed_start" });
  assert.deepEqual(list.row("move-pounce").send, { action: "ability", item: "pounce" });
  assert.ok(list.rows.filter(r => r.send).every(r => r.url === "garden"), "Every choice goes to the garden's own address");
  assert.ok(list.row("moon").note.includes("100K"), "Moonrise says what it is waiting for");
  const cooling = open(garden({ water: { ready_in: 90000, rounds: 8, share: 5, show: null }, moves: [{ ...garden().moves[0], ready_in: 30000 }] }), { passed: 10000 });
  assert.equal(cooling.row("water").send, undefined);
  assert.equal(cooling.row("water").hint, "ready in 1:20");
  assert.equal(cooling.row("move-pounce").hint, "ready in 0:20");
  assert.equal(open(garden(), { passed: 61000 }).row("trim-all").name, "Harvest everything ready (2)", "A flower that finished since the server spoke counts as ready");
  const two = open(garden({ gardens: 2, moonrise: { needs: 1, ready: true, count: 1 } }));
  assert.deepEqual(two.row("other").send, { action: "garden", count: 1 });
  two.row("moon").local();
  assert.deepEqual(two.went, [{ drawer: "moon" }]);
});

test("the seed drawer climbs the ladder one flower at a time and only ever sends a choice", () => {
  const drawer = open(garden({ given: 40 }), { drawer: "seeds", seed: "moondaisy" });
  assert.equal(drawer.rows.length, 1 + catalog.flowers.length);
  assert.equal(drawer.row("seed-moondaisy").icon, "✓");
  drawer.row("seed-moondaisy").local();
  assert.deepEqual(drawer.went, [{ seed: "moondaisy", drawer: null }]);
  assert.deepEqual(drawer.row("seed-glowbell").send, { action: "unlock", item: "glowbell" });
  assert.match(drawer.row("seed-glowbell").hint, /^30 coins/);
  assert.equal(drawer.row("seed-starpoppy").send, undefined, "The flower after next waits its turn");
  assert.match(open(garden(), { drawer: "seeds" }).row("seed-glowbell").note, /given 40 starpowder/);
  const rose = garden({ given: 5000 });
  rose.flowers.forEach((flower, i) => { flower.unlocked = i < 5; flower.next = i === 5; });
  assert.match(open(rose, { drawer: "seeds", level: 1 }).row("seed-nebula-rose").note, /level 5/);
  const ready = open(rose, { drawer: "seeds", level: 5 }).row("seed-nebula-rose");
  assert.match(ready.hint, /^800 starpowder/, "A boutique flower is priced in starpowder");
  assert.match(open(rose, { drawer: "seeds" }).row("seed-moon-fern").hint, /grows back/);
});

test("helpers, the shop and moonrise are drawers of their own", () => {
  const helpers = open(garden(), { drawer: "helpers" });
  assert.deepEqual(helpers.rows.map(r => r.key), ["back", "helper-fighter", "helper-thief", "helper-monk", "helper-mage", "helper-cleric", "helper-paladin"]);
  assert.deepEqual(helpers.row("helper-monk").send, { action: "helper", item: "monk" });
  helpers.row("back").local();
  assert.deepEqual(helpers.went, [{ drawer: null }]);
  const shop = open(garden({ cosmetics: catalog.cosmetics.map((thing, i) => ({ ...thing, owned: i === 0 })) }), { drawer: "shop" });
  assert.equal(shop.row("look-picket-fence").send, undefined);
  assert.deepEqual(shop.row("look-garden-gnome").send, { action: "cosmetic", item: "garden-gnome" });
  const moon = open(garden({ upgrades: catalog.upgrades.map((thing, i) => ({ ...thing, count: i === 1 ? thing.max : 0 })) }), { drawer: "moon" });
  assert.deepEqual(moon.row("moon-garden").send, { action: "moonrise", item: "garden" });
  assert.ok(moon.row("moon-garden").confirm && moon.row("moon-strong-press").confirm, "Replanting a garden asks twice");
  assert.equal(moon.row("moon-rich-soil").send, undefined, "An upgrade at its top can't be chosen");
});

test("Enter on a plot plants, trims, digs or says how long is left", () => {
  const view = garden();
  assert.deepEqual(UI.plotChoice(view, 2, null, 0), { send: { action: "plant", plot: 2, item: "moondaisy" } });
  assert.deepEqual(UI.plotChoice(view, 1, null, 0), { send: { action: "trim", plot: 1 } });
  assert.equal(UI.plotChoice(view, 0, null, 0).say, "Moondaisy: ready in 1:00.");
  assert.deepEqual(UI.plotChoice(view, 0, null, 60000), { send: { action: "trim", plot: 0 } }, "Ready by this page's own clock");
  assert.deepEqual(UI.plotChoice(view, 4, null, 0).send, { action: "plot" });
  assert.match(UI.plotChoice(view, 4, null, 0).confirm, /25 coins/);
  const more = garden();
  more.flowers[1].unlocked = true;
  assert.equal(UI.seedFor(more, more.plots[2], null), "glowbell", "With nothing chosen and nothing grown there, the best flower unlocked");
  assert.equal(UI.seedFor(more, more.plots[0], null), "moondaisy", "A plot remembers what grew in it");
  assert.equal(UI.seedFor(more, more.plots[0], "glowbell"), "glowbell");
  assert.equal(UI.seedFor(view, null, "comet-orchid"), "moondaisy", "A seed this garden hasn't unlocked is never planted");
  assert.equal(UI.cells(view), 5);
  assert.equal(UI.cells(garden({ plot_price: null })), 4);
  assert.deepEqual(["right", "down", "left", "up"].map(way => UI.moveCursor(0, way, 5)), [1, 4, 0, 0]);
  assert.equal(UI.moveCursor(3, "right", 5), 3, "The grid's edge is an edge");
});

test("the status lines and the words for what happened", () => {
  const lines = UI.status(garden(), 1, null, 0, 2);
  assert.match(lines[0], /Garden 1 · 4 plots · 1 ready to trim/);
  assert.match(lines[1], /Plot 2: Moondaisy · ready! Enter trims/);
  assert.match(lines[2], /Starpowder waiting: 114/, "Presses not yet sent already count on the page");
  assert.match(lines[3], /10 of 40 squeezed · 3 coins today/);
  assert.match(UI.status(garden({ effects: { frenzy: 30000 } }), 0, null, 10000, 0).pop(), /Frenzy 0:20/);
  const view = garden();
  assert.equal(UI.said({ action: "plant", plot: 0, item: "moondaisy" }, { planted: "moondaisy" }, view), "Planted: Moondaisy. Ready in 2 min.");
  assert.equal(UI.said({ action: "trim", plot: 0 }, { trimmed: 1, powder: 6, gift: null }, view), "Snip! One flower trimmed: +6 starpowder.");
  assert.match(UI.said({ action: "trim_all" }, { trimmed: 3, powder: 1500, gift: { kind: "snack", item: "banana" } }, view, id => "Banana"), /3 flowers trimmed: \+1\.5K starpowder\. And a snack for the pantry: Banana!/);
  assert.match(UI.said({ action: "trim" }, { trimmed: 1, powder: 6, gift: { kind: "token", coins: 150 } }, view), /150 coins/);
  assert.equal(UI.said({ action: "press" }, { pressed: 5, lucky: 0, powder: 15, coins: 0 }, view), "", "A press that made no coin says nothing");
  assert.equal(UI.said({ action: "press" }, { pressed: 5, lucky: 2, powder: 15, coins: 2 }, view), "Lucky squeeze! +2 coins.");
  assert.match(UI.said({ action: "water_answer" }, { water: "done", rounds: 2, moved: 1, right: false }, view), /a slip\. 2 rounds of water/);
  assert.match(UI.said({ action: "feed_end" }, { feed: "done", presses: 34, minutes: 3, moved: 2 }, view), /34 presses: every growing flower is 3 minutes further on/);
  assert.match(UI.said({ action: "helper", item: "monk" }, { helper: "monk", level: 2 }, view), /Monk's patience is now level 2/);
  assert.match(UI.away({ away: { ms: 3600000, powder: 1800, coins: 45 } }), /squeezed 1\.8K starpowder into 45 coins/);
  assert.equal(UI.away(garden()), "");
});

test("a minigame takes over the list", () => {
  const water = open(garden(), { mini: { kind: "water", show: [1, 2], phase: "answer", answer: [] } });
  assert.deepEqual(water.rows.map(r => r.key), ["watering"]);
  assert.match(water.rows[0].hint, /Round 2 of 8/);
  const feed = open(garden(), { mini: { kind: "feed", count: 7, left: 4000, whole: 10000 } });
  feed.rows[0].local();
  assert.deepEqual(feed.went, [{ feed: 1 }]);
  assert.match(feed.rows[0].name, /7 presses/);
});

test("every flower is drawn at every stage, and every plot and the press can be pressed", () => {
  const view = garden();
  const svg = Art.scene(view, { cursor: 1, focused: true, passed: 0, pending: 0, mini: null });
  assert.deepEqual(names(svg), ["press", "plot:0", "plot:1", "plot:2", "plot:3", "plot:4"]);
  assert.deepEqual([...svg.matchAll(/data-plot="(\w+)"/g)].map(m => m[1]), ["growing", "ready", "empty", "empty"]);
  assert.ok(svg.includes('data-cursor="1"') && svg.includes("25 coins") && svg.includes('data-near="25"'));
  assert.ok(!Art.scene(view, { cursor: 1, focused: false, passed: 0, pending: 0, mini: null }).includes("data-cursor"));
  const later = Art.scene(view, { cursor: 0, focused: false, passed: 61000, pending: 0, mini: null });
  assert.deepEqual([...later.matchAll(/data-plot="(\w+)"/g)].map(m => m[1]).slice(0, 2), ["ready", "ready"]);
  const full = garden({ plot_price: null, plots: Array.from({ length: 16 }, (_, i) => ({ flower: catalog.flowers[i % 12].id, last: null, stage: ["growing", "ready", "overgrown"][i % 3], ready_in: i % 3 ? -1 : 30000, grow: 60000 })) });
  const all = Art.scene(full, { cursor: 15, focused: true, passed: 0, pending: 0, mini: null });
  assert.equal(names(all).length, 17);
  for (const flower of catalog.flowers) assert.ok(all.includes(flower.colour), flower.id + " is drawn in its own colour");
  assert.ok(Art.scene(garden({ plots: [{ flower: "moondaisy", last: null, stage: "growing", ready_in: 110000, grow: 120000 }] }), { cursor: 0 }).length <
    Art.scene(garden({ plots: [{ flower: "moondaisy", last: null, stage: "ready", ready_in: -1, grow: 120000 }] }), { cursor: 0 }).length, "A sprout is a smaller drawing than a bloom");
});

test("everything bought for looks is drawn", () => {
  assert.deepEqual(catalog.cosmetics.map(thing => thing.id).sort(), [...Art.COSMETICS].sort(), "garden-art.js draws exactly the catalog's cosmetics");
  const bare = Art.scene(garden(), { cursor: 0 });
  for (const thing of catalog.cosmetics) {
    const owned = garden({ cosmetics: catalog.cosmetics.map(other => ({ ...other, owned: other.id === thing.id })) });
    assert.notEqual(Art.scene(owned, { cursor: 0 }), bare, thing.id + " changes the picture");
  }
});

test("the watering cans and the fertilizer button can be pressed only when it is time", () => {
  const view = garden();
  const showing = Art.scene(view, { cursor: 0, mini: { kind: "water", show: [2, 0], phase: "show", answer: [], lit: 2 } });
  assert.ok(showing.includes("WATCH THE CANS") && showing.includes('data-can="lit"'));
  assert.deepEqual(names(showing), [], "Nothing can be pressed while the cans are showing");
  const answering = Art.scene(view, { cursor: 0, mini: { kind: "water", show: [2, 0], phase: "answer", answer: [2], lit: -1 } });
  assert.deepEqual(names(answering), ["can:0", "can:1", "can:2", "can:3"]);
  assert.ok(answering.includes("YOUR TURN · 1 OF 2"));
  const feeding = Art.scene(view, { cursor: 0, mini: { kind: "feed", count: 12, left: 5000, whole: 10000 } });
  assert.deepEqual(names(feeding), ["feed"]);
  assert.ok(feeding.includes(">12<"));
});
