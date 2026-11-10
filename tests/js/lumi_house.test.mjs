// Lumi's house art (static/games/lumi-pet/house.js) against the server's catalog (lumipet_data/house.json):
// every object that can be bought has a drawing, every spot has a layer, and the markup is well formed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const H = createRequire(import.meta.url)("../../mnemerynne/web/static/games/lumi-pet/house.js");
const catalog = JSON.parse(readFileSync(new URL("../../mnemerynne/lumipet_data/house.json", import.meta.url), "utf8"));
const firsts = Object.fromEntries(catalog.spots.map(spot => [spot.id, spot.items[0].id]));

test("every spot has a layer and every object a drawing with no broken numbers", () => {
  assert.deepEqual([...H.LAYERS].sort(), catalog.spots.map(spot => spot.id).sort());
  for (const spot of catalog.spots) for (const item of spot.items) {
    assert.equal(typeof H.ART[item.id], "function", item.id + " needs art");
    const art = H.ART[item.id]();
    assert.equal(/NaN|undefined|Infinity/.test(art), false, item.id + " drew a broken number");
    if (item.id !== "curtain-none") assert.ok(art.length > 40, item.id + " drew nothing");
  }
});

test("every window frame has an opening for the view to show through", () => {
  for (const item of catalog.spots.find(spot => spot.id === "frame").items) assert.match(H.CLIPS[item.id], /^M[\d .A-Za-z-]+Z$/);
});

test("the room draws back to front, clips the view to the frame, and balances its tags", () => {
  const room = H.render({ ...firsts, frame: "frame-hex" });
  const order = H.LAYERS.map(spot => room.indexOf('data-spot="' + spot + '"'));
  assert.deepEqual(order, [...order].sort((a, b) => a - b));
  assert.ok(order.every(at => at >= 0));
  assert.ok(room.includes(H.CLIPS["frame-hex"]) && room.includes('data-spot="view" clip-path="url(#lp-view-clip)"'));
  assert.equal(room.match(/<g[ >]/g).length, room.match(/<\/g>/g).length);
  assert.equal(room.match(/<defs>/g).length, room.match(/<\/defs>/g).length);
});

test("an object the art doesn't know is left out rather than breaking the room", () => {
  const room = H.render({ ...firsts, rug: "rug-from-the-future", bed: "__proto__", frame: "constructor" });
  assert.ok(room.includes('<g data-spot="rug"></g>') && room.includes('<g data-spot="bed"></g>'));
  assert.ok(room.includes(H.CLIPS["frame-arch"]));
});

test("every view scrolls three strips whose tiles repeat, so the loop has no seam", () => {
  for (const item of catalog.spots.find(spot => spot.id === "view").items) {
    const art = H.ART[item.id]();
    for (const depth of [1, 2, 3]) assert.ok(art.includes('class="lp-px lp-px-' + depth + '"'), item.id + " strip " + depth);
    assert.equal(art.match(/transform="translate\(240 0\)"/g).length, 3);
    assert.ok(art.includes("lp-sky-day") && art.includes("lp-night-veil"));
  }
});
