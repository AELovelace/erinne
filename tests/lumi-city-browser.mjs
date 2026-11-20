// Included by connected-browser.mjs (LUMI_CITY_ONLY=1, with PREVIEW_COINS so plots are affordable): Lumi City
// (Doll's design, 2026-11-19; docs/plans/lumi-city.md) against the real, disposable app. Walks Lumi Heights' lobby, a
// floor of flats and the lift, out into the city, chooses a home town, Go to… the office, buys a plot and a house
// style, walks into her house (her second home, in Lumi Pet), and checks a second player sees her in the city.
import assert from "node:assert/strict";

const row = key => `[data-lp-row="${key}"]`;
const where = page => page.$eval("#lp-room", el => el.dataset.town || "");                  // city, lobby, hall, office, games...
const waitWhere = (page, kind, ms) => page.waitForFunction(k => document.getElementById("lp-room").dataset.town === k, { timeout: ms || 20000 }, kind);
const said = (page, words, ms) => page.waitForFunction(w => document.getElementById("lp-message").textContent.includes(w), { timeout: ms || 15000 }, words);
const city = page => page.evaluate(async () => (await fetch("/api/media/games/lumi-pet/city")).json()); // GET city, as the page sees it
const gx = page => page.evaluate(() => Number(document.getElementById("lp-room").style.getPropertyValue("--gx")) + 500 + Number(document.getElementById("lp-house").getAttribute("viewBox").split(" ")[0])); // where she stands in the room (the camera may follow her)
async function press(page, key) { // a row in the panel (on failure, says what the panel did show)
  try { await page.waitForSelector(row(key), { timeout: 20000 }); }
  catch (e) {
    const seen = await page.evaluate(() => ({ rows: Array.from(document.querySelectorAll("[data-lp-row]")).map(b => b.dataset.lpRow), said: document.getElementById("lp-message").textContent, town: document.getElementById("lp-room").dataset.town }));
    await page.screenshot({ path: "runs/casino-browser/lumi-city-failed.png" });
    throw new Error("No row " + key + ": " + JSON.stringify(seen));
  }
  await page.$eval(row(key), el => el.click());                         // (in the page: the list may redraw under a mouse mid-click)
}

export async function checkLumiCity(alice, bob, base) {
  const errors = [];                                                    // anything the page throws, said at the end
  [alice, bob].forEach(p => p.on("pageerror", e => errors.push(String(e && e.message || e))));
  await alice.evaluate(async () => { // connected-browser.mjs turns room mode off for the list-driven checks: her house is walked in room mode
    const st = await (await fetch("/api/media/games/lumi-pet/state")).json();
    if (st.pet.room_mode === false) await fetch("/api/media/games/lumi-pet/act", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "room_mode" }) });
  });
  // ---- Lumi Heights: the lobby, the lift, her floor -------------------------------------------------------------
  await alice.goto(base + "/media/games/lumi-town?at=lobby-l");
  await waitWhere(alice, "lobby");
  assert.match(await alice.$eval("#lp-place", el => el.textContent), /LUMI HEIGHTS WEST/);
  await alice.screenshot({ path: "runs/casino-browser/lumi-city-lobby.png" });
  const before = await gx(alice);
  await alice.keyboard.down("ArrowLeft"); await new Promise(r => setTimeout(r, 600)); await alice.keyboard.up("ArrowLeft"); // held: she walks freely
  assert.ok(await gx(alice) < before - 40, "a held key walks her across the lobby");
  await press(alice, "thing-lift");                                     // walk to the lift and use it
  await alice.waitForSelector('[data-lp-row^="floor-"]', { timeout: 20000 });
  const floor = await alice.$eval('[data-lp-row^="floor-"]:not([data-lp-row="floor-lobby"])', el => el.dataset.lpRow);
  await press(alice, floor);
  await waitWhere(alice, "hall");
  await alice.waitForFunction(() => Array.from(document.querySelectorAll("[data-lp-row]")).some(b => /My front door/.test(b.textContent)), { timeout: 15000 });
  await alice.screenshot({ path: "runs/casino-browser/lumi-city-hall.png" });
  await press(alice, "thing-lift");
  await press(alice, "floor-lobby");
  await waitWhere(alice, "lobby");
  await press(alice, "thing-exit");                                     // out onto the street
  await waitWhere(alice, "city");
  await alice.waitForSelector('#lp-house [data-id="me"]');               // she's drawn in the city's own picture
  await alice.screenshot({ path: "runs/casino-browser/lumi-city-street.png" });

  // ---- Where she lives, Go to… the office, buying a plot and a house ---------------------------------------------
  await press(alice, "home-town");
  await press(alice, "town-left");
  await said(alice, "LefTown");
  assert.equal((await city(alice)).me.home_town, "left");
  await press(alice, "goto");
  await press(alice, "go-office");                                      // she walks there by herself and goes in
  await waitWhere(alice, "office", 30000);
  await alice.screenshot({ path: "runs/casino-browser/lumi-city-office.png" });
  await press(alice, "thing-land");
  await alice.waitForSelector('[data-lp-row^="buy-"]', { timeout: 20000 });
  const plotRow = await alice.$eval('[data-lp-row^="buy-"]', el => el.dataset.lpRow), plot = plotRow.slice(4);
  const coins = (await city(alice)).me.coins;
  await press(alice, plotRow); await said(alice, "Choose it again");    // the first press asks...
  await press(alice, plotRow);                                          // ...the second buys
  await alice.waitForFunction(async p => (await (await fetch("/api/media/games/lumi-pet/city")).json()).me.plot === p, { timeout: 15000 }, plot);
  const after = await city(alice), lot = after.lots.find(l => l.id === plot);
  assert.equal(after.me.coins, coins - lot.suggested, "the office was paid its suggested price");
  assert.equal(lot.style, after.catalog.styles[0].id, "a Starter Cottage goes up on a new plot");
  await press(alice, "list-back");
  await press(alice, "thing-styles");
  const style = after.catalog.styles[1];
  await press(alice, "style-" + style.id); await said(alice, "Choose it again");
  await press(alice, "style-" + style.id);                              // bought...
  await alice.waitForFunction(async s => (await (await fetch("/api/media/games/lumi-pet/city")).json()).me.styles.includes(s), { timeout: 15000 }, style.id);
  await press(alice, "style-" + style.id);                              // ...and put up
  await alice.waitForFunction(async (p, s) => (await (await fetch("/api/media/games/lumi-pet/city")).json()).lots.find(l => l.id === p).style === s, { timeout: 15000 }, plot, style.id);

  // ---- Another player sees her out in the city ----------------------------------------------------------------
  await press(alice, "list-back");
  await press(alice, "thing-exit");
  await waitWhere(alice, "city");
  const aliceId = after.me.id;
  await bob.goto(base + "/media/games/lumi-town?at=casino");
  await waitWhere(bob, "games");
  await press(bob, "thing-exit");
  await waitWhere(bob, "city");
  await bob.waitForSelector('#lp-house [data-id="guest-' + aliceId + '"]', { timeout: 20000 }); // Downtown, both of them: she's there
  await bob.screenshot({ path: "runs/casino-browser/lumi-city-friends.png" });
  await bob.click(row("say")).catch(() => {});                          // (Say something opens the phrases: just checking it doesn't break walking)

  // ---- Her house: walk up to it and go in --------------------------------------------------------------------
  await press(alice, "goto");
  await press(alice, "go-house");
  await alice.waitForFunction(() => location.pathname === "/media/games/lumi-pet" && /^\?at=house-\d+$/.test(location.search), { timeout: 40000 });
  await alice.waitForFunction(() => /second home/.test(document.getElementById("lp-message").textContent), { timeout: 20000 });
  await alice.screenshot({ path: "runs/casino-browser/lumi-city-house.png" });
  await alice.waitForFunction(() => document.getElementById("gm").dataset.rooms === "true", { timeout: 10000 }).catch(async () => {
    throw new Error("her house isn't room mode: " + JSON.stringify(errors) + " " + JSON.stringify(await alice.evaluate(() => ({ rooms: document.getElementById("gm").dataset.rooms, place: document.getElementById("lp-place").textContent, href: location.href }))) + " " + JSON.stringify(await alice.evaluate(async () => (await (await fetch("/api/media/games/lumi-pet/state")).json()).pet.room_mode)));
  });
  assert.ok(["city", "lobby", "office", "hall", "games"].indexOf(await where(bob)) >= 0);
  assert.deepEqual(errors, [], "no page errors");
}
