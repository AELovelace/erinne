// The Skills tab (skills-ui.js, skills-art.js) and the Collection tab (collection-ui.js), against the server's
// catalogs (battle.json, house.json, closet.json, items.json, garden.json).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const UI = require("../../mnemerynne/web/static/games/lumi-pet/skills-ui.js");
const Art = require("../../mnemerynne/web/static/games/lumi-pet/skills-art.js");
const Album = require("../../mnemerynne/web/static/games/lumi-pet/collection-ui.js");
const GardenArt = require("../../mnemerynne/web/static/games/lumi-pet/garden-art.js");
const data = name => JSON.parse(readFileSync(new URL("../../mnemerynne/lumipet_data/" + name, import.meta.url), "utf8"));
const battle = data("battle.json"), house = data("house.json"), closet = data("closet.json"), items = data("items.json"), garden = data("garden.json");
const rules = { costs: [1, 1, 2, 2, 3, 3, 5, 5], respec: 100, moves: 4 };

function snap(pet = {}, points = 3) {
  return { pet: { level: 5, skills: [], moves: ["pounce", "glow-spark"], decor: [], closet: [], toys: [], mods_owned: [], ...pet }, progress: { points },
    battle: { trees: battle.trees, basics: battle.basics, mods: battle.mods, rules } };
}
const names = svg => [...svg.matchAll(/data-click="([^"]+)"/g)].map(m => m[1]);
const states = svg => Object.fromEntries([...svg.matchAll(/data-skill="([^"]+)" data-state="(\w+)"/g)].map(m => [m[1], m[2]]));

test("all six trees fit on one page, each in its own place and shape", () => {
  const spots = UI.layout(battle);
  assert.equal(spots.length, 48);
  assert.equal(new Set(spots.map(spot => spot.x + "," + spot.y)).size, 48, "No two skills share a place");
  assert.ok(spots.every(spot => spot.x >= 345 && spot.x <= 655 && spot.y >= 60 && spot.y <= 480), "Everything is in the middle of the room, clear of the side panels");
  for (const tree of battle.trees) { // A skill always sits below every skill it needs, so the wires run downwards.
    const here = spots.filter(spot => spot.tree === tree.id);
    for (const spot of here) for (const need of spot.node.needs) assert.ok(here.find(other => other.id === need).y < spot.y, spot.id + " is below " + need);
  }
  assert.equal(new Set(Object.values(UI.COLOURS)).size, 6);
});

test("a skill is learned, ready, short of points, or locked behind what leads to it", () => {
  const fresh = snap();
  const at = id => UI.state(UI.find(battle, id), fresh.pet, fresh.progress, rules);
  assert.deepEqual(["heavy-paw", "tough-fur", "comet-cleave"].map(at), ["ready", "locked", "locked"]);
  const some = snap({ skills: ["heavy-paw", "armor-break"] }, 1);
  const then = id => UI.state(UI.find(battle, id), some.pet, some.progress, rules);
  assert.deepEqual(["heavy-paw", "tough-fur", "brawn", "parry", "ember"].map(then), ["learned", "ready", "short", "locked", "ready"], "Brawn is open but costs 2; Parry waits on the other arm");
  assert.deepEqual(UI.choice(some, "tough-fur"), { send: { action: "learn", item: "tough-fur" }, name: "Tough Fur" });
  assert.match(UI.choice(some, "brawn").say, /costs 2 skill points, and Lumi has 1/);
  assert.match(UI.choice(some, "parry").say, /Parry needs Tough Fur first/);
  assert.match(UI.choice(snap(), "comet-cleave").say, /needs Iron Hide and Whirl Claw first/);
});

test("Enter on a learned move carries it or puts it away, within her four", () => {
  const knows = snap({ skills: ["heavy-paw", "tough-fur", "armor-break"], moves: ["pounce", "glow-spark", "heavy-paw"] });
  assert.deepEqual(UI.choice(knows, "heavy-paw").send, { action: "equip", item: "heavy-paw" });
  assert.deepEqual(UI.choice(knows, "armor-break").send, { action: "equip", item: "armor-break" });
  assert.match(UI.choice(knows, "tough-fur").say, /always at work/);
  const full = snap({ skills: ["heavy-paw", "armor-break", "ember"], moves: ["pounce", "glow-spark", "heavy-paw", "ember"] });
  assert.match(UI.choice(full, "armor-break").say, /carries 4 moves/);
  assert.match(UI.choice(snap({ skills: ["heavy-paw"], moves: ["heavy-paw"] }), "heavy-paw").say, /at least one move/);
});

test("the arrows walk the web, within a tree and across to the next", () => {
  assert.equal(UI.move(battle, "heavy-paw", "down"), "tough-fur", "Down from the root takes an arm");
  assert.equal(UI.move(battle, "tough-fur", "right"), "armor-break");
  assert.equal(UI.move(battle, "armor-break", "down"), "brawn");
  assert.equal(UI.move(battle, "heavy-paw", "up"), "heavy-paw", "Nothing above the top row");
  assert.equal(UI.find(battle, UI.move(battle, "armor-break", "right")).tree, "thief", "Right off the edge of one tree lands in the next");
  assert.equal(UI.find(battle, UI.move(battle, "comet-cleave", "down")).tree, "mage", "And down from the top row of trees reaches the bottom row");
  const reached = new Set(["heavy-paw"]);
  for (let pass = 0; pass < 12; pass++) for (const id of [...reached]) for (const way of ["up", "down", "left", "right"]) reached.add(UI.move(battle, id, way));
  assert.equal(reached.size, 48, "Every skill can be reached with the arrows");
});

test("the list beside the page offers the one thing Enter would do, and the words say what a skill is", () => {
  const went = [];
  const list = UI.rows({ snap: snap({ skills: ["heavy-paw"] }), cursor: "armor-break", go: change => went.push(change) });
  assert.deepEqual(list.map(r => r.key), ["act", "respec", "leave"]);
  assert.deepEqual([list[0].name, list[0].send], ["Learn Armor Break (2 points)", { action: "learn", item: "armor-break" }]);
  assert.ok(list[1].confirm, "Handing every point back asks twice");
  list[2].local();
  assert.deepEqual(went, [{ leave: true }]);
  assert.deepEqual(UI.rows({ snap: snap(), cursor: "comet-cleave", go() {} }).map(r => r.key), ["act", "leave"], "Nothing to hand back yet");
  assert.match(UI.rows({ snap: snap(), cursor: "comet-cleave", go() {} })[0].note, /needs Iron Hide and Whirl Claw/);
  const lines = UI.lines(snap({ skills: ["heavy-paw"] }), "comet-cleave");
  assert.match(lines[0], /3 skill points to spend · level 5 · 1 of 48 skills learned/);
  assert.equal(lines[1], "Fighter: Comet Cleave");
  assert.match(lines[2], /^Move · power \d+ · physical · cuts through armour/);
  assert.match(lines[3], /Costs 5 points · needs Iron Hide and Whirl Claw/);
  assert.match(lines[4], /Carried moves \(2 of 4\): Pounce, Glow Spark/);
  assert.match(UI.describe(battle.trees[0].nodes[1]), /^Passive · \+10% defense$/);
  assert.match(UI.lines(snap({ skills: ["heavy-paw"] }), "tough-fur")[3], /needs ✓ Heavy Paw/);
});

test("the page draws every skill, lights the wires she has walked, and can be pressed", () => {
  const svg = Art.scene(snap({ skills: ["heavy-paw", "armor-break"] }, 1), { cursor: "brawn", focused: true });
  assert.equal(names(svg).length, 48);
  assert.ok(names(svg).includes("skill:starfall"));
  const shown = states(svg);
  assert.deepEqual([shown["heavy-paw"], shown["armor-break"], shown["tough-fur"], shown.brawn, shown.parry], ["learned", "learned", "ready", "short", "locked"]);
  assert.equal((svg.match(/data-wire="lit"/g) || []).length, 1, "One wire is lit: Heavy Paw to Armor Break");
  assert.ok(svg.includes('data-cursor="brawn"') && svg.includes("FIGHTER") && svg.includes("PALADIN") && svg.includes('data-points="1"'));
  assert.ok(!Art.scene(snap(), { cursor: "brawn", focused: false }).includes("data-cursor"));
  assert.equal((svg.match(/<circle [^>]*data-skill/g) || []).length, battle.trees.flatMap(tree => tree.nodes).filter(node => node.kind === "move").length, "Moves are circles; passives are diamonds");
  assert.ok(!/NaN|undefined/.test(svg));
});

// ---- The Collection tab
function album(pet = {}, mine = {}) {
  const flowers = garden.flowers.map((flower, i) => ({ ...flower, unlocked: i < 2 }));
  return { ...snap(pet), house, closet, catalog: items, garden: { flowers, decor: { owned: mine, placed: {}, spare: {} } }, garden_decor: garden.decor,
    dungeon: { environments: [{ id: "moonlit-shrine", name: "Moonlit Shrine" }] } };
}

test("the collection counts everything there is, by shelf, and what she has of it", () => {
  const shelves = Album.shelves(album());
  assert.deepEqual(shelves.map(shelf => shelf.id), ["home", "finds", "closet", "toys", "mods", "flowers", "garden"]);
  const count = id => shelves.find(shelf => shelf.id === id).items.length;
  const all = house.spots.flatMap(spot => spot.items);
  assert.equal(count("home") + count("finds"), all.length - all.filter(piece => /-none$/.test(piece.id)).length, "Every house object, but for the empty bracket");
  assert.equal(count("finds"), all.filter(piece => piece.found).length);
  assert.equal(count("closet"), closet.slots.flatMap(slot => slot.items).length);
  assert.equal(count("toys"), items.toys.length);
  assert.equal(count("mods"), battle.mods.length);
  assert.deepEqual([count("flowers"), count("garden")], [42, 225]);
  const owned = id => shelves.find(shelf => shelf.id === id).items.filter(item => item.owned).length;
  assert.equal(owned("home"), house.spots.filter(spot => !/-none$/.test(spot.items[0].id)).length, "A new Lumi has what came with the house: the first thing in each spot (an empty spot is nothing to collect)");
  assert.deepEqual([owned("finds"), owned("closet"), owned("mods"), owned("flowers"), owned("garden")], [0, 0, 0, 2, 0]);
  assert.equal(owned("toys"), items.toys.filter(toy => !toy.price && !toy.rarity).length);
});

test("the album's lists open a shelf, say where each thing comes from, and show it", () => {
  const went = [], first = house.spots[0].items.find(piece => piece.rarity), bow = closet.slots[0].items[0];
  const view = album({ decor: [first.id], closet: [bow.id] }, { "fence-1-starry": 2 });
  const top = Album.rows({ snap: view, open: null, go: change => went.push(change) });
  assert.deepEqual(top.map(r => r.key), ["all", "shelf-home", "shelf-finds", "shelf-closet", "shelf-toys", "shelf-mods", "shelf-flowers", "shelf-garden"]);
  assert.match(top[0].name, /^Collected: \d+ of \d+$/);
  assert.match(top[0].hint, /^\d+% of everything/);
  assert.match(top.find(r => r.key === "shelf-flowers").hint, /^2 of 42/);
  top.find(r => r.key === "shelf-garden").local();
  assert.deepEqual(went, [{ open: "garden" }]);
  assert.ok(top.every(r => !r.send), "An album: nothing in it can be bought or changed");

  const home = Album.rows({ snap: view, open: "home", go: change => went.push(change) });
  home[0].local();
  assert.deepEqual(went.pop(), { open: null, pick: 1 }, "Back lands on the shelf it came from");
  const won = home.find(r => r.key === "have-" + first.id);
  assert.deepEqual([won.icon, won.preview], ["✓", [house.spots[0].id, first.id]], "A house object is shown in the room");
  assert.match(won.hint, /from a capsule/);
  const sold = home.find(r => r.hint.includes("coins") && r.icon === "·");
  assert.match(sold.note, /isn't hers yet/);
  const finds = Album.rows({ snap: view, open: "finds", go() {} });
  assert.ok(finds.slice(1).every(r => /found in /.test(r.hint) && r.icon === "·"));
  assert.ok(finds.some(r => r.hint.includes("Moonlit Shrine")), "A find names its place");
  const wear = Album.rows({ snap: view, open: "closet", go() {} }).find(r => r.key === "have-" + bow.id);
  assert.deepEqual([wear.icon, wear.wear], ["✓", [closet.slots[0].id, bow.id]], "Clothes are shown on Lumi");
  const dressing = Album.rows({ snap: view, open: "garden", go() {} });
  const fence = dressing.find(r => r.key === "have-fence-1-starry");
  assert.deepEqual([fence.icon, fence.name, fence.card], ["✓", "Starry bamboo fence ×2", { kind: "decor", slot: "fence", id: "fence-1-starry" }]);
  assert.match(dressing.find(r => r.key === "have-fence-0-snowy").hint, /Decorations, 300 starpowder/);
  const flower = Album.rows({ snap: view, open: "flowers", go() {} }).find(r => r.key === "have-comet-orchid");
  assert.deepEqual(flower.card, { kind: "flower", id: "comet-orchid" });
});

test("a flower or a garden decoration is shown large on a card", () => {
  const view = album();
  const flower = GardenArt.card({ kind: "flower", id: "comet-orchid" }, view.garden, garden.decor);
  assert.ok(flower.includes("FLOWER") && flower.includes("GROWS BACK") && flower.includes(garden.flowers.find(f => f.id === "comet-orchid").colour));
  assert.notEqual(flower, GardenArt.card({ kind: "flower", id: "moondaisy" }, view.garden, garden.decor));
  const fence = GardenArt.card({ kind: "decor", slot: "fence", id: "fence-1-starry" }, view.garden, garden.decor);
  assert.ok(fence.includes("DECORATION") && fence.includes('data-card="fence-1-starry"') && fence.includes('data-decor="fence"'));
  assert.ok(!/NaN|undefined/.test(flower + fence));
  assert.equal(GardenArt.card({ kind: "flower", id: "no-such" }, view.garden, garden.decor), "");
});
