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

test("everything pressable sits in the middle every screen shows, and its box is around where she stands", () => {
  for (const r of Ru.ROOMS) for (const t of r.things) {
    const [x, y, w, h] = t.box;
    assert.ok(x >= 230 && x + w <= 715, r.id + "/" + t.id + " is inside x 230-715");
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
  assert.deepEqual(rows.map(row => row.say.slice(0, 7)), ["My frid", "My bowl", "My poti", "This wa"], "The lower bar gets her words with each row");
});

test("◀ ▶ walk thing to thing, through to the next room at the ends, and stop at the flat's ends", () => {
  let at = Ru.clean(null);
  assert.deepEqual(at, Ru.START);
  assert.equal(Ru.spot(at).id, "paint");
  at = Ru.step(at, 1); assert.equal(Ru.spot(at).id, "bed");
  at = Ru.step(at, 1); assert.deepEqual([at.room, Ru.spot(at).id], ["den", "wardrobe"], "Past the bed: straight into the den, at its first thing");
  at = Ru.step(at, -1); assert.deepEqual([at.room, Ru.spot(at).id], ["bedroom", "bed"], "And back, at the bedroom's last thing");
  at = Ru.hop(at, -1); assert.deepEqual([at.room, Ru.spot(at).id], ["kitchen", "potions"], "⏮ a whole room left: arriving from the right");
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
