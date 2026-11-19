// Room mode (room-ui.js, room-art.js; Doll's design, 2026-11-19): Lumi Pet without menus. Every tab is a thing in her
// flat, walking takes her thing to thing and room to room, and a card is the tab's own rows with the way back.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const Ru = require("../../mnemerynne/web/static/games/lumi-pet/room-ui.js");
const RA = require("../../mnemerynne/web/static/games/lumi-pet/room-art.js");
const H = require("../../mnemerynne/web/static/games/lumi-pet/house.js");
const PET_TABS = ["care", "pantry", "toys", "closet", "home", "shop", "collection", "settings"]; // games.py: Lumi Pet's tabs

test("every one of Lumi Pet's tabs is reached from some thing in her flat, and nothing opens a tab she doesn't have", () => {
  assert.deepEqual([...Ru.tabs()].sort(), [...PET_TABS].sort());
  const door = Ru.room("bedroom").things.find(t => t.id === "door");
  assert.equal(door.act, "door", "The front door goes straight out to Lumi Town, as the room's door always has");
});

test("everything pressable is spread across what a computer shows, stands clear of the lower bar, and is where she stands", () => {
  for (const r of Ru.ROOMS) for (const t of r.things) {
    const [x, y, w, h] = t.box;
    assert.ok(x >= 180 && x + w <= 820, r.id + "/" + t.id + " is inside x 180-820");
    assert.ok(y + h <= RA.FLOOR + 6, r.id + "/" + t.id + ": its foot is above the lower bar (y " + (y + h) + ")");
    assert.ok(y >= 0 && y + h <= 500 && w > 0 && h > 0, r.id + "/" + t.id + " is inside the picture");
    assert.ok(t.x >= x - 20 && t.x <= x + w + 20, r.id + "/" + t.id + ": she stands by it");
  }
});

test("Lumi speaks for herself: every thing and every way through has her own first-person words", () => {
  for (const r of Ru.ROOMS) for (const [at, s] of Ru.layout(r.id).entries()) {
    assert.match(s.say, /^(My |This way to )/, r.id + "/" + (s.id || s.go) + ": her bubble is in her own words");
    assert.doesNotMatch(s.say + " " + (s.open || ""), /(Lumi|her|she)/i, r.id + "/" + (s.id || s.go) + ": never about herself in the third person");
    assert.equal(Ru.guide({ room: r.id, at: at }), s.say);
    if (s.tab) assert.ok(s.open, r.id + "/" + s.id + " has a line for when its menu opens");
  }
  const rows = Ru.rows({ room: "kitchen", at: 0 }, { open() {}, walk() {}, door() {} });
  assert.deepEqual(rows.map(row => row.say.slice(0, 7)), ["My frid", "My bowl", "My poti", "My pain", "This wa"], "The lower bar gets her words with each row");
});

test("room mode stands every drawn thing and her furniture back from the front edge, clear of the lower bar", () => {
  const sketched = { fridge: 472, bowl: 466, counter: 418, potions: 330, paints: 470, wardrobe: 472, terminal: 472, capsules: 472 }; // each thing's foot as first drawn
  for (const [room, things] of Object.entries(RA.PLACE)) for (const [name, at] of Object.entries(things))
    assert.ok(sketched[name] + at[1] <= RA.FLOOR + 2, room + "/" + name + " stands at or behind the floor line");
  const furniture = { toychest: 473, bed: 460, boxes: 492, rug: 470 }; // house.js's lowest pieces, as drawn for the classic room
  for (const [spot, foot] of Object.entries(furniture)) assert.ok(foot + RA.BEDROOM_SHIFT[spot][1] <= RA.FLOOR + 2, spot + " is stood back in room mode");
  assert.ok(Ru.hits("bedroom", 1).includes('cy="' + (RA.FLOOR + 2) + '"'), "Her ring is on the floor line");
});

test("◀ ▶ walk thing to thing, through to the next room at the ends, and stop at the flat's ends", () => {
  let at = Ru.clean(null);
  assert.deepEqual(at, Ru.START);
  assert.equal(Ru.spot(at).id, "paint");
  at = Ru.step(at, 1); assert.equal(Ru.spot(at).id, "bed");
  at = Ru.step(at, 1); assert.deepEqual([at.room, Ru.spot(at).id], ["den", "wardrobe"], "Past the bed: straight into the den, at its first thing");
  at = Ru.step(at, -1); assert.deepEqual([at.room, Ru.spot(at).id], ["bedroom", "bed"], "And back, at the bedroom's last thing");
  at = Ru.hop(at, -1); assert.deepEqual([at.room, Ru.spot(at).id], ["kitchen", "paint"], "⏮ a whole room left: arriving from the right");
  at = Ru.step(Ru.step(Ru.step(at, -1), -1), -1); assert.deepEqual([at.room, Ru.spot(at).id], ["kitchen", "fridge"], "The kitchen is the flat's left end");
  at = Ru.hop(Ru.hop(Ru.hop(at, 1), 1), 1); assert.equal(at.room, "den", "And the den its right end");
  assert.deepEqual(Ru.clean({ room: "attic", at: 99 }).room, "bedroom", "A remembered place that isn't one comes home to the bedroom");
});

test("the room's rows reach every spot: things open their card, the way through walks, the door goes out", () => {
  const asked = [];
  const ctx = { open: (t, i) => asked.push(["open", t.id, i]), walk: s => asked.push(["walk", s.room]), door: () => asked.push(["door"]) };
  const rows = Ru.rows({ room: "bedroom", at: 0 }, ctx);
  assert.deepEqual(rows.map(r => r.key), ["go-kitchen", "thing-door", "thing-toychest", "thing-trophy", "thing-paint", "thing-bed", "go-den"]);
  rows.forEach(r => r.local());
  assert.deepEqual(asked, [["walk", "kitchen"], ["door"], ["open", "toychest", 2], ["open", "trophy", 3], ["open", "paint", 4], ["open", "bed", 5], ["walk", "den"]]);
  assert.equal(Ru.find("bedroom", "bed"), 5);
  assert.equal(Ru.layout("bedroom")[Ru.nearest("bedroom", 660)].id, "bed", "A press on the floor walks her to the nearest thing");
});

test("a card is the tab's rows, narrowed where the thing says, with the way back last", () => {
  const care = [{ key: "cuddle" }, { key: "nap" }, { key: "drink", send: {} }, { key: "adventure" }];
  const shelf = Ru.room("kitchen").things.find(t => t.id === "potions");
  let back = 0;
  assert.deepEqual(Ru.cardRows(care, shelf, () => back++).map(r => r.key), ["drink", "card-back"]);
  const empty = Ru.cardRows([{ key: "cuddle" }], shelf, () => back++);
  assert.deepEqual(empty.map(r => r.key), ["card-empty", "card-back"], "No potion row (she isn't hurt): a note says why");
  assert.ok(empty[0].note && !empty[0].send, "The note can't be sent anywhere");
  empty[1].local(); assert.equal(back, 1);
  const bed = Ru.room("bedroom").things.find(t => t.id === "bed");
  assert.deepEqual(Ru.cardRows(care, bed, () => {}).map(r => r.key), ["cuddle", "nap", "drink", "adventure", "card-back"], "The bed is all of Care");
});

test("the drawings: every room draws, its things can be pressed, and the bedroom's other rooms wear her own walls", () => {
  for (const r of Ru.ROOMS) {
    const markup = RA.scene(r.id) + Ru.hits(r.id, 1);
    assert.ok(!/undefined|NaN/.test(markup), r.id + " has nothing broken in it");
    for (const t of r.things) assert.ok(markup.includes('data-click="thing:' + t.id + '"'), r.id + "/" + t.id + " can be pressed");
    assert.equal((markup.match(/class="lp-tw-mark"/g) || []).length, 1, r.id + ": one ring where she stands");
  }
  const house = { wallpaper: Object.keys(H.ART).find(k => k.startsWith("wall-")), bed: Object.keys(H.ART).find(k => k.startsWith("bed-")) };
  const shell = H.render(house, false, { only: RA.SHELL });
  assert.ok(shell.includes('data-spot="wallpaper"') && !shell.includes('data-spot="bed"') && !shell.includes('data-spot="door"'),
    "The kitchen and den: her wallpaper, no bed and no door");
  assert.ok(H.render(house, false, {}).includes('data-spot="bed"'), "Her bedroom is drawn whole, as ever");
  const bare = RA.scene("bedroom", {}), owned = RA.scene("bedroom", { bed: "x", toychest: "x", trophy: "x" });
  assert.ok(bare.length > owned.length, "Just moved in: a mattress, a box of toys and a plank stand in for what she hasn't got");
  assert.equal(owned, RA.scene("bedroom", { bed: "x", toychest: "x", trophy: "x", rug: "x" }), "Once she owns them, her own furniture shows instead");
});

const RS = require("../../mnemerynne/web/static/games/lumi-pet/room-styles.js");
const Pk = require("../../mnemerynne/web/static/games/lumi-pet/picker-ui.js");
const houseCatalog = JSON.parse(require("node:fs").readFileSync(new URL("../../mnemerynne/lumipet_data/house.json", import.meta.url), "utf8"));

test("every room thing comes in the catalog's 15 styles, each drawn, unbroken and looking different", () => {
  const roomSpots = houseCatalog.spots.filter(spot => spot.room);
  assert.deepEqual(roomSpots.map(spot => spot.id).sort(), [...RS.THINGS].sort());
  for (const spot of roomSpots) {
    assert.deepEqual(spot.items.map(item => item.id), RS.SETS.map(set => spot.id + "-" + set.id), spot.id + ": a piece per set, in order");
    const drawn = spot.items.map(item => RS.draw(spot.id, item.id));
    drawn.forEach((markup, n) => assert.ok(markup.length > 200 && !/NaN|undefined|Infinity/.test(markup), spot.items[n].id + " draws"));
    assert.equal(new Set(drawn).size, 15, spot.id + ": all 15 look different");
    for (const item of spot.items) assert.equal(typeof H.ART[item.id], "function", item.id + " is in house.js's ART for the Collection");
  }
  assert.equal(RS.draw("fridge", "nonsense"), RS.draw("fridge", "fridge-plain"), "An unknown style is drawn Plain");
});

test("each room shows its own wall and floor, or the bedroom's until it has one, and tries pieces and paint in place", () => {
  const house = { wallpaper: "wall-dusk", floor: "floor-moss", "den-wall": "#ff8ecb", fridge: "fridge-galaxy" };
  assert.deepEqual([RA.view("kitchen", house).wallpaper, RA.view("kitchen", house).floor], ["wall-dusk", "floor-moss"], "The kitchen follows the bedroom");
  assert.equal(RA.view("den", house).wallpaper, "#ff8ecb", "The den is painted pink");
  assert.equal(RA.view("kitchen", house, ["kitchen-floor", "#123456"]).floor, "#123456", "A colour being painted shows as it moves");
  assert.ok(RA.scene("kitchen", house).includes("#2a1b52"), "Her galaxy fridge, in the kitchen");
  assert.ok(RA.scene("den", {}).length > 0 && RA.scene("kitchen", {}).includes(RS.draw("paints", "paints-plain").slice(0, 60)), "Every room has its paint pots");
});

test("each room's paint pots open just that room's spots; a shared wall lists the bedroom's papers, can be painted, and says which room", () => {
  const kitchenPots = Ru.room("kitchen").things.find(t => t.id === "paint"), bedPots = Ru.room("bedroom").things.find(t => t.id === "paint");
  const snap = { pet: { house: { wallpaper: "wall-dusk" }, decor: ["wall-dusk"] }, house: houseCatalog, boxes: { left: 0 }, dungeon: {} };
  const painted = [];
  const rows = Pk.homeRows({ snap, open: null, flip: {}, go() {}, paint: (spot, colour) => painted.push([spot, colour]) });
  assert.deepEqual(Ru.cardRows(rows, kitchenPots, () => {}).map(r => r.key), [...Ru.KITCHEN_SPOTS, "card-back"]);
  const mine = Ru.cardRows(rows, bedPots, () => {}).map(r => r.key);
  assert.ok(mine.includes("wallpaper") && mine.includes("paints") && !mine.includes("fridge") && !mine.includes("den-wall"), "The bedroom's pots: her own furniture");
  const wall = rows.find(r => r.key === "kitchen-wall");
  assert.equal(wall.label, "Dusk plum", "The kitchen shows the bedroom's paper until it has its own");
  const open = Pk.homeRows({ snap, open: "kitchen-wall", flip: {}, go() {}, paint: (spot, colour) => painted.push([spot, colour]) });
  assert.equal(open[1].key, "paint"); open[1].local();
  assert.deepEqual(painted, [["kitchen-wall", null]], "Paint it a colour: the sliders, for the kitchen's wall");
  const stars = open.find(r => r.key === "wall-stars");
  assert.deepEqual(stars.send, { action: "decorate", item: "wall-stars", spot: "kitchen-wall" }, "Bought or put up for this room only");
  assert.deepEqual(Ru.cardRows(open, kitchenPots, () => {}, true).length, open.length + 1, "Inside a spot, all its papers show");
  const paintedSnap = { ...snap, pet: { ...snap.pet, house: { ...snap.pet.house, "kitchen-wall": "#abcdef" } } };
  assert.match(Pk.homeRows({ snap: paintedSnap, open: null, flip: {}, go() {} }).find(r => r.key === "kitchen-wall").hint, /Painted #ABCDEF/);
});
