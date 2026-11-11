// Included by connected-browser.mjs so the pet is checked against the real, disposable Media application.
// Lumi is one pet in three apps (mnemerynne/games.py): Lumi Pet (/media/games/lumi-pet: her room), Lumi Dungeons
// (/media/games/lumi-dungeons: dungeon, battle, skills) and Lumi's Garden (/media/games/lumi-garden). Each check
// below opens the app that has the tab it is checking; checkLumiApps checks the ways between them.
import assert from "node:assert/strict";

const text = (page, id) => page.$eval(id, el => el.textContent);
const row = key => `[data-lp-row="${key}"]`;
async function moveTo(page, key) { // Move the list's cursor onto a row with the keyboard, without choosing it.
  for (let i = 0; i < 60; i++) {
    if (await page.$(`[data-lp-row="${key}"][aria-pressed="true"]`)) return;
    await page.keyboard.press("ArrowDown");
  }
  throw new Error("The list's cursor never reached " + key);
}
const places = row("env-moonlit-shrine") + ", " + row("continue"); // Lumi Dungeons' first list: the places to explore, or (on a device that hasn't read it yet) how the last trip ended.
const said = (page, words) => page.waitForFunction(w => document.getElementById("lp-message").textContent.includes(w), {}, words);

export async function checkLumiPetCapsules(page, base) { // Run on the second account, whose purse the casino checks don't count on to the coin.
  await page.goto(base + "/media/games/lumi-pet");
  await page.waitForSelector(row("cuddle"));
  await page.click(row("cuddle"));
  await page.waitForFunction(() => document.getElementById("lp-coins").textContent === "1,025"); // The day's first visit pays its coins.
  await page.click('[data-lp-tab="closet"]'); // The dearer capsule: it charges its own price, and the list stays on its row for another go.
  await page.waitForSelector(row("capsule-moon"));
  await moveTo(page, "capsule-moon");
  await page.keyboard.press("Enter");
  await said(page, "The capsule pops open");
  assert.equal(await text(page, "#lp-coins"), "725");
  assert.match(await text(page, "#lp-message"), /\((rare|epic|legendary)\)/);
  assert.ok(await page.$('[data-lp-row="capsule-moon"][aria-pressed="true"]'), "Still on the Moon Capsule's row");
  await page.click('[data-lp-tab="home"]'); // A prize she hasn't won is locked in the spot's full list, and says where it comes from.
  await page.waitForSelector(row("rug"));
  await page.click(row("rug"));
  await page.waitForSelector(row("rug-braid"));
  assert.match(await page.$eval('[data-lp-row="rug-braid"] small', el => el.textContent), /only from a capsule/);
  console.log("Lumi Pet: the Moon Capsule charges its price and stays on its row; an unwon prize is locked");
}

export async function checkLumiPet(browser, page, base) { // Exercise real controls, the server save and graceful failures.
  await page.evaluate(() => localStorage.setItem("mnemerynne-game:lumi-pet:companion", JSON.stringify({ version: 1, moments: 7, palette: "lagoon" })));
  await page.goto(base + "/media/games/lumi-pet");
  await page.waitForSelector(row("cuddle"));
  assert.equal(await text(page, "#lp-moments"), "7", "The old browser save's moments move to the account");
  assert.equal(await page.$eval("#lp-room", el => el.dataset.palette), "lagoon");
  assert.equal(await text(page, "#lp-hunger-n"), "85");
  assert.equal(await page.$eval("#lp-hunger", el => el.parentNode.getAttribute("aria-valuenow")), "85");

  await page.click(row("cuddle"));
  await page.waitForFunction(() => document.getElementById("lp-moments").textContent === "8");
  await page.waitForFunction(() => document.getElementById("lp-coins").textContent === "1,025"); // The day's first visit pays its coins.
  await page.click(row("cuddle")); // Too soon: the server's own words, and nothing counted.
  await said(page, "still enjoying");
  assert.equal(await text(page, "#lp-moments"), "8");

  await page.focus(row("cuddle")); // Arrow navigation must supersede stale focus on a different control.
  await page.keyboard.press("PageDown"); // Next category: Pantry. (On a remote this is the Next button.)
  await page.waitForSelector(row("apple"));
  await page.keyboard.press("Enter");
  await said(page, "Apple!");
  assert.equal(await text(page, "#lp-memory"), "A shared snack");
  assert.equal(await text(page, "#lp-hunger-n"), "100");

  await page.click('[data-lp-tab="shop"]');
  await page.click(row("cookie"));
  await said(page, "Bought: Cookie");
  assert.equal(await text(page, "#lp-coins"), "1,017");
  await page.click('[data-lp-tab="pantry"]');
  await page.waitForFunction(() => document.querySelector('[data-lp-row="cookie"] small').textContent.includes("1 left"));

  assert.equal(await page.$('[data-lp-tab="battle"]'), null, "Fights and dungeons are Lumi Dungeons' now, not a tab of Lumi Pet");
  await page.goto(base + "/media/games/lumi-dungeons"); // The same Lumi, in her other app: it opens on the list of places to explore.
  await page.waitForSelector(row("env-moonlit-shrine"));
  assert.deepEqual(await page.$$eval("[data-lp-tab]", tabs => tabs.map(tab => tab.dataset.lpTab)), ["dungeon", "battle", "skills", "settings"]);
  assert.equal(await text(page, "#lp-coins"), "1,017", "One purse and one pet across her apps");
  await page.click('[data-lp-tab="battle"]'); // A fight: find one, see the enemy, play a turn, then run (so no coins or EXP move).
  assert.equal(await text(page, "#lp-level"), "1 · 0/35 EXP");
  assert.equal(await text(page, "#lp-health-n"), "58/58"); // Her health: full, so nothing to mend and no potion row in Care.
  assert.equal(await page.$eval("#lp-health", el => el.parentNode.getAttribute("aria-valuenow")), "100");
  await page.click(row("skills"));
  await page.waitForSelector(row("tree-fighter"));
  await page.click(row("tree-fighter"));
  await page.click(row("heavy-paw")); // No skill points at level 1: the server says so.
  await said(page, "Not enough skill points");
  await page.click('[data-lp-tab="battle"]');
  await page.click(row("fight"));
  await page.click(row("tier-common"));
  await said(page, "A wild");
  assert.equal(await page.$eval("#lp-room", el => el.dataset.fight), "true");
  assert.ok(await page.$eval("#lp-foe", el => el.children.length > 2), "The enemy is drawn");
  assert.match(await text(page, "#lp-f-foe"), / · Lv [123] · Common · \d+\/\d+ HP$/);
  await page.keyboard.press("ArrowDown"); // Pounce, Glow Spark, then Defend: the remote reaches every button.
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => /braces|guarded|missed/.test(document.getElementById("lp-message").textContent)); // Her Defend, or the enemy's reply to it.
  await page.reload(); // A fight waits: the page comes back on the Battle tab, mid-fight.
  await page.waitForSelector(row("run"));
  assert.equal(await page.$eval('[data-lp-tab="battle"]', el => el.getAttribute("aria-selected")), "true");
  for (let tries = 0; tries < 20 && !(await page.$(row("continue"))); tries++) {
    if (await page.$(row("run"))) await page.click(row("run"));
    await new Promise(done => setTimeout(done, 300));
  }
  const ended = await page.$eval(row("continue"), el => el.textContent);
  assert.match(ended, /Safe and sound|up again soon/, "The fight ended by running (or, rarely, a defeat)");
  await page.screenshot({ path: "runs/casino-browser/lumi-pet-fight.png", fullPage: true });
  await page.click(row("continue"));
  await page.waitForFunction(() => document.getElementById("lp-fight").hidden);
  if (ended.includes("up again")) { // The rare defeat: wake her (50 coins would upset the sums below, so wait it out by healing later).
    throw new Error("Lumi lost a fight she only defended and ran from; re-run the suite.");
  }

  await page.click('[data-lp-tab="dungeon"]'); // A dungeon trip: set out, see the room, take a step, and give up (so no coins or EXP move).
  await page.waitForSelector(row("env-moonlit-shrine"));
  await page.click(row("env-rooftop-garden")); // Not open at level 1: it says when.
  await said(page, "opens at level 35");
  await page.click(row("env-moonlit-shrine"));
  await page.waitForSelector(row("leave"));
  assert.equal(await page.$eval("#lp-room", el => el.dataset.dungeon), "true");
  assert.equal(await text(page, "#lp-d-title"), "Moonlit Shrine · floor 1");
  assert.match(await text(page, "#lp-d-label"), /^58\/58 HP · \d+ MP · satchel: 0 coins$/);
  assert.ok(await page.$eval("#lp-dungeon", el => getComputedStyle(el).display !== "none" && el.children.length > 20), "The room is drawn over the house");
  assert.ok((await text(page, "#lp-message")).length > 20, "The room has something to say");
  const arrival = await text(page, "#lp-message");
  await page.reload(); // A trip waits: the page comes back on the Dungeon tab, in the same room, saying the same thing.
  await page.waitForSelector(row("leave"));
  assert.equal(await page.$eval('[data-lp-tab="dungeon"]', el => el.getAttribute("aria-selected")), "true");
  assert.equal(await text(page, "#lp-message"), arrival);
  assert.equal(await page.$eval(".lp-tabs", el => getComputedStyle(el).display), "none", "Exploring, the right panel is the dungeon's own: the tabs fold away");
  assert.match(await text(page, "#lp-keys"), /walk.*choose from this list.*drink a potion/);
  assert.ok(await page.$('[data-lp-row="leave"][aria-pressed="true"]'));
  await page.keyboard.press("PageDown"); // Next moves the list's cursor here, since the arrows are busy walking.
  assert.equal(await page.$eval('[data-lp-row="leave"]', el => el.getAttribute("aria-pressed")), "false");
  await page.keyboard.press("PageUp");
  assert.ok(await page.$('[data-lp-row="leave"][aria-pressed="true"]'));
  await page.keyboard.press("p"); // Play/Pause drinks a potion; at full health the server says so.
  await said(page, "already at full health");
  await page.click(row("care")); // "Look after Lumi": on a trip that is a snack or a potion, and nothing else.
  await page.waitForSelector(row("explore"));
  assert.ok(await page.$(row("apple")) && await page.$(row("potion")));
  assert.equal(await page.$(row("cuddle")), null);
  assert.equal(await page.$eval(".lp-tabs", el => getComputedStyle(el).display), "none", "The other tabs wait until she is home");
  await page.keyboard.press("]"); // Next stays in this list: it never reaches another tab on a trip.
  assert.ok(await page.$('[data-lp-tab="dungeon"][aria-selected="true"]'));
  await page.click(row("apple"));
  await page.waitForFunction(() => /munches happily|still enjoying/.test(document.getElementById("lp-message").textContent)); // The server answered: fed, or too soon after her last snack.
  await page.click(row("explore"));
  await page.waitForSelector(row("leave"));
  assert.ok((await text(page, "#lp-story .lp-scene")).length > 10, "The story panel says what this room is like");
  const unlooked = await text(page, "#lp-message"); // Look around: a new detail below, and the line before it joins the story above.
  await page.click(row("look"));
  await page.waitForFunction(was => document.getElementById("lp-message").textContent !== was, {}, unlooked);
  assert.ok(await page.$$eval("#lp-story p", lines => lines.length >= 2 && lines[lines.length - 1].textContent.length > 3));
  const sides = await page.evaluate(() => [innerWidth].concat([".lp-intro", ".lp-controls"].map(panel => document.querySelector(panel).getBoundingClientRect()).map(box => [Math.round(box.left), Math.round(innerWidth - box.right), Math.round(box.width)])));
  if (sides[0] > 1000) { // A wide screen: the panels sit at its edges, as wide as they always were, and the room has the rest.
    assert.ok(sides[1][0] <= 10 && sides[2][1] <= 10, "The panels sit at the screen's edges: " + JSON.stringify(sides));
    const wide = Math.min(sides[0], 1800) - 2 * Math.min(40, Math.max(12, sides[0] * .03)) - 2 * Math.min(32, Math.max(12, sides[0] * .02));
    assert.ok(Math.abs(sides[1][2] - wide * .85 / 3.5) <= 2 && Math.abs(sides[2][2] - wide / 3.5) <= 2, "The panels kept their width: " + JSON.stringify(sides));
  }
  const panel = await page.$eval(".lp-controls", el => el.getBoundingClientRect().bottom), keys = await page.$eval("#lp-keys", el => el.getBoundingClientRect().bottom);
  assert.ok(panel - keys < 40, "The choices and the key legend sit at the bottom of the panel, leaving the top to the story");
  const way = await page.$eval('[data-lp-row^="go-"]', el => el.dataset.lpRow.slice(3)); // One arrow, one room.
  const standing = await text(page, "#lp-message"); // What the room says now; the step is over when it says something else.
  await page.keyboard.press({ n: "ArrowUp", e: "ArrowRight", s: "ArrowDown", w: "ArrowLeft" }[way]);
  await page.waitForFunction(was => document.getElementById("lp-message").textContent !== was, {}, standing);
  for (let tries = 0; tries < 20 && (await page.$(row("run"))); tries++) { // A creature lived there: run until she is clear of it.
    await page.click(row("run"));
    await new Promise(done => setTimeout(done, 300));
  }
  if (await page.$(row("continue"))) await page.click(row("continue")); // The fight's result, read.
  if (await page.$(row("back"))) { await page.click(row("back")); await page.waitForSelector(row("abandon")); } // Or a locked door: step back.
  if (await page.$(row("abandon"))) {
    await page.screenshot({ path: "runs/casino-browser/lumi-pet-dungeon.png", fullPage: true });
    await page.click(row("abandon")); // One press only asks; nothing is lost to a stray one.
    await said(page, "Sure?");
    assert.equal(await page.$eval("#lp-room", el => el.dataset.dungeon), "true");
    await page.click(row("abandon"));
  }
  await page.waitForSelector(row("continue")); // How the trip ended (given up, or, rarely, carried home).
  assert.match(await page.$eval(row("continue"), el => el.textContent), /Came home early from floor 1|Carried home from floor 1/);
  await page.click(row("continue"));
  await page.waitForSelector(row("env-moonlit-shrine"));
  assert.equal(await page.$eval("#lp-room", el => el.dataset.dungeon), "false");
  assert.equal(await text(page, "#lp-coins"), "1,017");

  await page.goto(base + "/media/games/lumi-pet"); // Home again: the rest is in her room.
  await page.waitForSelector(row("cuddle"));
  await page.click('[data-lp-tab="toys"]');
  await page.click(row("star-ribbon"));
  await page.waitForFunction(() => document.getElementById("lp-state").textContent.startsWith("Playing · 1:"));
  assert.equal(await page.$eval("#lp-room", el => el.dataset.action), "play");
  await page.click('[data-lp-tab="care"]');
  await page.click(row("nap")); // One thing at a time.
  await said(page, "busy playing");
  await page.screenshot({ path: "runs/casino-browser/lumi-pet.png", fullPage: true });

  await page.click('[data-lp-tab="closet"]'); // Dressing up: ▲ ▼ pick a place on her, ◀ ▶ try things on, Enter buys or wears.
  await page.waitForSelector(row("slot-head"));
  assert.equal(await page.$eval("#lp-acc-front", el => el.children.length), 0);
  await moveTo(page, "slot-head");
  await page.keyboard.press("ArrowRight"); // Nothing → Ribbon bow: she tries it on before it's bought.
  await page.waitForFunction(() => document.querySelector('#lp-acc-front [data-acc="head"]'));
  assert.equal(await page.$eval('[data-lp-row="slot-head"] small', el => el.textContent), "◀ Ribbon bow ▶ · Common · 60 coins · Enter buys it");
  assert.equal(await text(page, "#lp-coins"), "1,017");
  await page.keyboard.press("Enter");
  await said(page, "Bought: Ribbon bow");
  assert.equal(await text(page, "#lp-coins"), "957");
  for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowRight"); // On to the crown, which only the capsule gives.
  assert.match(await page.$eval('[data-lp-row="slot-head"] small', el => el.textContent), /Starlight crown ▶ · Legendary · only from a capsule/);
  await page.keyboard.press("Enter");
  await said(page, "only comes from a capsule");
  assert.equal(await text(page, "#lp-coins"), "957");
  await page.keyboard.press("ArrowLeft"); // Left goes back the other way: the witch hat.
  assert.match(await page.$eval('[data-lp-row="slot-head"] small', el => el.textContent), /Witch hat/);
  await moveTo(page, "slot-back");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight"); // Backpack, then cape.
  await page.keyboard.press("Enter");
  await said(page, "Bought: Hero cape");
  assert.equal(await page.$eval("#lp-acc-back", el => el.children.length), 1);
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft"); // Back round to Nothing: Enter takes the cape off.
  assert.equal(await page.$eval('[data-lp-row="slot-back"] small', el => el.textContent), "◀ Nothing ▶ · Enter takes it off");
  await page.keyboard.press("Enter");
  await said(page, "takes it off");
  await page.click(row("slot-head")); // With nothing flipped, Enter (or a tap) opens that place's whole list.
  await page.waitForSelector(row("acc-bow"));
  assert.match(await page.$eval('[data-lp-row="acc-bow"] small', el => el.textContent), /wearing it/);
  await page.click(row("back"));
  await page.waitForSelector(row("slot-head"));
  await page.screenshot({ path: "runs/casino-browser/lumi-pet-closet.png", fullPage: true });
  await page.click('[data-lp-tab="care"]');
  assert.ok(await page.$('#lp-acc-front [data-acc="head"]'), "She keeps the bow on after leaving the tab");

  await page.click('[data-lp-tab="home"]'); // Decorating: open a spot, preview by moving, buy and place with Enter.
  await page.waitForSelector(row("rug"));
  assert.equal(await page.$eval("#lp-room", el => getComputedStyle(el).position), "fixed", "On a wide screen the room is the whole screen");
  assert.ok(await page.$eval("#lp-room", el => el.getBoundingClientRect().width >= innerWidth - 1 && el.getBoundingClientRect().height >= innerHeight - 1));
  assert.equal(await page.$$eval("#lp-wall-mask path", holes => holes.length), 3, "Three windows are cut in the wall");
  const scenery = await page.evaluate(() => getComputedStyle(document.querySelector(".parallax")).display !== "none");
  assert.equal(await page.$eval('#lp-house [data-spot="view"]', el => el.dataset.open === "true"), scenery, "Open windows exactly when the page has scenery behind it");
  if (!scenery) assert.equal(await page.$eval('#lp-house [data-spot="view"] .lp-px-3', el => getComputedStyle(el).animationName), "lp-scroll");
  const lumi = await page.$eval("#lp-pet", el => { const box = el.getBoundingClientRect(); return (box.left + box.right) / 2 / innerWidth; });
  assert.ok(Math.abs(lumi - 0.5) < 0.02, "Lumi stands in the middle of the room");
  await moveTo(page, "view"); // ◀ ▶ flip a spot's object in the room itself. A view other than the free one is painted into every window.
  for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowRight"); // sea, forest, snow
  await page.waitForFunction(() => document.querySelectorAll('#lp-house [data-spot="view"] .lp-fall-snow').length === 3);
  assert.equal(await page.$eval('#lp-house [data-spot="view"] .lp-px-3', el => getComputedStyle(el).animationName), "lp-scroll");
  assert.equal(await page.$eval('[data-lp-row="view"] small', el => el.textContent), "◀ Snowy hills ▶ · 300 coins · Enter buys it");
  for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowLeft"); // Back to what is placed, with nothing bought.
  assert.match(await page.$eval('[data-lp-row="view"] small', el => el.textContent), /The city outside ▶ · in the room/);
  await moveTo(page, "rug");
  const plain = await page.$eval('#lp-house [data-spot="rug"]', el => el.innerHTML);
  await page.keyboard.press("ArrowRight"); // The moon rug, shown in the room before it's bought.
  await page.waitForFunction(was => document.querySelector('#lp-house [data-spot="rug"]').innerHTML !== was, {}, plain);
  assert.equal(await text(page, "#lp-coins"), "777");
  await page.keyboard.press("Enter");
  await said(page, "Bought and put out: Moon rug");
  assert.equal(await text(page, "#lp-coins"), "677");
  await page.waitForFunction(() => document.querySelector('[data-lp-row="rug"] small').textContent === "◀ Moon rug ▶ · in the room");
  await page.click(row("rug")); // Its whole list, for a mouse or a finger.
  await page.waitForFunction(() => document.querySelector('[data-lp-row="rug-moon"] small') && document.querySelector('[data-lp-row="rug-moon"] small').textContent === "In the room now");
  await page.click(row("back"));
  await page.waitForSelector(row("rug"));
  await page.click('[data-lp-tab="care"]'); // Leaving the tab keeps what was placed and drops any preview.
  assert.notEqual(await page.$eval('#lp-house [data-spot="rug"]', el => el.innerHTML), plain);
  await page.screenshot({ path: "runs/casino-browser/lumi-pet-house.png", fullPage: true });

  await page.click('[data-lp-tab="settings"]');
  await page.click(row("glow"));
  await page.waitForFunction(() => document.getElementById("lp-room").dataset.palette === "starlight");
  await page.click(row("sound"));
  await page.waitForFunction(() => document.querySelector('[data-lp-row="sound"]').textContent.includes("Sound: off"));

  await page.reload(); // Everything came from the account, so a reload (or another device) shows the same Lumi.
  await page.waitForSelector(row("cuddle"));
  assert.equal(await text(page, "#lp-moments"), "9");
  assert.equal(await page.$eval("#lp-room", el => el.dataset.palette), "starlight");
  assert.equal(await text(page, "#lp-coins"), "677");
  assert.ok((await text(page, "#lp-state")).startsWith("Playing"));
  assert.ok(await page.$('#lp-acc-front [data-acc="head"]'), "The bow is still on after a reload");
  assert.ok(await page.$eval('#lp-house [data-spot="rug"]', el => el.innerHTML.includes("#f0e6c8")), "The moon rug is still out after a reload");

  await page.focus('[data-lp-tab="care"]');
  await page.keyboard.press("Tab"); // Native Tab then Enter presses the focused control (Pantry), not the remote's selection.
  await page.keyboard.press("Enter");
  await page.waitForSelector(row("apple"));

  await page.setViewport({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Pet must fit a phone");
  await page.screenshot({ path: "runs/casino-browser/lumi-pet-phone.png", fullPage: true });
  await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  assert.equal(await page.$eval("#lp-pet", el => getComputedStyle(el).animationName), "none");
  await page.emulateMediaFeatures([]);
  await page.setViewport({ width: 1440, height: 1000 });

  const blocked = await browser.newPage();
  try {
    await blocked.setCookie(...await page.cookies());
    await blocked.evaluateOnNewDocument(() => { // A private/locked-down browser plays the same: nothing is kept in it.
      Storage.prototype.setItem = function () { throw new DOMException("Blocked", "SecurityError"); };
      Storage.prototype.getItem = function () { throw new DOMException("Blocked", "SecurityError"); };
    });
    await blocked.goto(base + "/media/games/lumi-pet");
    await blocked.waitForSelector(row("cuddle"));
    assert.equal(await text(blocked, "#lp-moments"), "9");
  } finally { await blocked.close(); }
  console.log("Lumi Pet and Lumi Dungeons: account save, old-save import, care, pantry, shop, a fight, a dungeon trip, toys, the closet, the house, settings, mobile, reduced motion and blocked storage passed");
}

export async function checkLumiPetDoors(browser, page, base) { // Every kind of locked door, and the card game, on the real page.
  // A floor is random, so a test can't walk to a given door. Instead the page is shown the server's own state with a
  // door put in front of Lumi, and is driven with the keyboard: the lists must draw, the board must take the arrows,
  // and nothing may throw. (What the server then says about a door that isn't really there doesn't matter here.)
  const levels = JSON.parse(await (await import("node:fs/promises")).readFile(new URL("../mnemerynne/lumipet_data/sokoban.json", import.meta.url), "utf8")).levels;
  const cases = [
    { name: "crates", facing: { kind: "sokoban", rows: levels[0].rows }, title: "PUSH THE CRATES", rows: ["play", "undo", "reset", "back"] },
    { name: "lamps", facing: { kind: "lights", size: 3, board: [true, true, false, true, false, false, false, false, false] }, title: "PUT EVERY LAMP OUT", rows: ["play", "reset", "back"] },
    { name: "runes", facing: { kind: "runes", runes: ["sun", "moon", "star", "leaf", "wave"], slots: 4, tries: [{ guess: ["sun", "sun", "moon", "leaf"], exact: 1, near: 1 }], left: 7 }, title: "FIND THE RUNE CODE", rows: ["try", "rune-sun", "rune-moon", "rune-star", "rune-leaf", "rune-wave", "erase", "back"] },
    { name: "key", facing: { kind: "key" }, title: "IT NEEDS A KEY", rows: ["unlock", "back"] },
    { name: "room", plain: true, title: "", rows: ["go-e", "go-w", "look"] },
    { name: "cards", game: { kind: "match", cards: [null, null, null, null, null, null, null, null], found: [], open: null, last: null, left: 14 }, title: "FIND THE PAIRS", rows: ["play"] },
  ];
  for (const made of cases) {
    const tab = await browser.newPage();
    const errors = [];
    tab.on("pageerror", error => errors.push(String(error)));
    try {
      await tab.setCookie(...await page.cookies());
      await tab.setViewport({ width: 1440, height: 1000 });
      await tab.evaluateOnNewDocument(made => { // Hand the page a trip with this door (or game) in front of Lumi.
        const real = window.fetch;
        window.fetch = async function (url, options) {
          const answer = await real(url, options);
          if (!String(url).endsWith("/lumi-pet/state")) return answer;
          const data = await answer.json(), env = data.dungeon.environments[0];
          data.fight = null;
          data.dungeon.run = { id: 77, env: { id: env.id, name: env.name, family: env.family, palette: env.palette }, depth: 1, at: "1,1", over: null, ended: null,
            rooms: { "1,1": { kind: made.game ? "game" : "empty", done: !made.game, exits: ["e", "w"] }, "2,1": { kind: "vault", done: false, exits: ["w"] }, "0,1": { kind: "start", done: true, exits: ["e"] } },
            room: { kind: made.game ? "game" : "empty", done: !made.game, exits: ["e", "w"] }, satchel: { coins: 12, potions: 0, keys: 0, trophies: [], mods: [] },
            hp: 40, hpMax: 58, mp: 20, mpMax: 22, event: { scenario: "empty" }, say: "A door.", scene: "A quiet room.", story: ["Lumi set out.", "A door."], fighting: false,
            facing: made.facing ? { at: "2,1", lock: made.facing } : null, game: made.game || null };
          return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
        };
      }, made);
      await tab.goto(base + "/media/games/lumi-dungeons");
      await tab.waitForSelector(row(made.rows[0]));
      assert.equal(await tab.$eval('[data-lp-tab="dungeon"]', el => el.getAttribute("aria-selected")), "true", made.name + ": a trip under way opens on the Dungeon tab");
      for (const key of made.rows) assert.ok(await tab.$(row(key)), made.name + " offers " + key);
      assert.ok(await tab.$eval("#lp-dungeon", (el, title) => el.textContent.includes(title), made.title), made.name + " shows its panel");
      if (!made.plain) assert.ok(await tab.$eval(".lp-pet-wrap", el => getComputedStyle(el).opacity === "0"), made.name + ": Lumi steps aside for the panel");
      const sent = (press, words) => Promise.all([ // A mouse press that must ask the server for exactly this.
        tab.waitForRequest(request => request.url().endsWith("/dungeon/act") && request.postData().includes(words)), tab.click('[data-click="' + press + '"]')]);
      const before = await tab.$eval("#lp-dungeon", el => el.innerHTML);
      if (made.name === "crates") { // Arrows push; Enter takes a step back; Next reaches the list.
        for (const key of ["ArrowUp", "ArrowLeft", "ArrowRight", "ArrowDown"]) await tab.keyboard.press(key);
        assert.notEqual(await tab.$eval("#lp-dungeon", el => el.innerHTML), before, "Lumi moved on the crate board");
        const steps = () => tab.$eval('[data-lp-row="play"] small', el => Number(el.textContent.split(" ")[0]));
        const taken = await steps();
        assert.ok(taken >= 1 && taken <= 4);
        await tab.keyboard.press("Enter");
        assert.equal(await steps(), taken - 1, "Enter on the board takes the last step back");
        await tab.keyboard.press("PageDown");
        await tab.waitForFunction(() => document.getElementById("lp-dungeon").textContent.includes("PAUSED"));
        await tab.click(row("reset"));
        await tab.waitForFunction(() => document.querySelector('[data-lp-row="play"] small').textContent.startsWith("0 steps"));
        await tab.keyboard.press("ArrowUp"); // Any arrow goes straight back to the board.
        await tab.waitForFunction(() => document.getElementById("lp-dungeon").textContent.includes("PUSH THE CRATES"));
        await tab.click(row("reset")); // The mouse: pressing a square steps Lumi towards it.
        await tab.waitForFunction(() => document.querySelector('[data-lp-row="play"] small').textContent.startsWith("0 steps"));
        const wide = levels[0].rows[0].length - 1, tall = levels[0].rows.length - 1;
        for (const corner of ["0,0", wide + "," + tall, "0," + tall, wide + ",0"]) await tab.click('[data-click="cell:' + corner + '"]');
        assert.ok(await steps() >= 1, "Pressing squares walks Lumi across the crate board");
      } else if (made.name === "lamps") { // Arrows move the ring, Enter flips lamps; Next reaches the list.
        await tab.keyboard.press("ArrowRight");
        await tab.keyboard.press("Enter");
        assert.notEqual(await tab.$eval("#lp-dungeon", el => el.innerHTML), before, "A lamp was pressed");
        assert.equal(await tab.$eval('[data-lp-row="play"] small', el => el.textContent.slice(0, 9)), "1 presses");
        for (let i = 0; i < 4; i++) await tab.keyboard.press("ArrowDown"); // The bottom edge is just an edge now.
        assert.ok(await tab.$eval("#lp-dungeon", el => el.textContent.includes("PUT EVERY LAMP OUT")));
        await tab.keyboard.press("PageDown");
        await tab.waitForFunction(() => document.getElementById("lp-dungeon").textContent.includes("PAUSED"));
        await tab.keyboard.press("ArrowUp");
        await tab.waitForFunction(() => document.getElementById("lp-dungeon").textContent.includes("PUT EVERY LAMP OUT"));
        await tab.click('[data-click="lamp:8"]'); // The mouse: pressing a lamp flips it.
        assert.equal(await tab.$eval('[data-lp-row="play"] small', el => el.textContent.slice(0, 9)), "2 presses");
      } else if (made.name === "runes") { // The code is built from the list; ▲ ▼ move through it, since there is no board.
        for (const rune of ["sun", "moon", "star"]) await tab.click(row("rune-" + rune));
        await tab.click(row("erase"));
        await tab.click(row("rune-wave"));
        await tab.click(row("rune-leaf"));
        await tab.waitForFunction(() => document.querySelector('[data-lp-row="try"] small').textContent.startsWith("☀ ☾ ≈ ❧"));
        const first = await tab.$eval('[aria-pressed="true"][data-lp-row]', el => el.dataset.lpRow);
        await tab.keyboard.press("ArrowDown");
        assert.notEqual(await tab.$eval('[aria-pressed="true"][data-lp-row]', el => el.dataset.lpRow), first);
      } else if (made.name === "cards") { // Arrows move over the cards; Next reaches the list.
        await tab.keyboard.press("ArrowRight");
        assert.notEqual(await tab.$eval("#lp-dungeon", el => el.innerHTML), before, "The card cursor moved");
        await tab.keyboard.press("PageDown");
        await tab.waitForFunction(() => document.getElementById("lp-dungeon").textContent.includes("PAUSED"));
        await sent("card:5", '"picks":[5]'); // The mouse: pressing a card turns that card.
      } else if (made.name === "room") { // The mouse in a plain room: a door walks her through it.
        assert.ok(await tab.$eval(".lp-pet-wrap", el => getComputedStyle(el).pointerEvents === "none"), "Lumi never hides a door from the mouse");
        await sent("way:w", '"way":"w"');
      }
      assert.equal(await tab.$eval(".lp-tabs", el => getComputedStyle(el).display), "none", made.name + ": the tabs are folded away");
      await tab.click(row("care")); // "Look after Lumi" at a door: snacks and a potion, then back to the door's own list.
      await tab.waitForSelector(row("explore"));
      assert.equal(await tab.$(row("cuddle")), null);
      await tab.click(row("explore"));
      await tab.waitForSelector(row(made.rows[0]));
      assert.deepEqual(errors, [], made.name + ": nothing threw on the page");
      if (made.name === "lamps") await tab.screenshot({ path: "runs/casino-browser/lumi-pet-door.png", fullPage: true });
    } finally { if (errors.length) console.log("Page errors:", errors.join(" | ")); await tab.close(); }
  }
  console.log("Lumi Pet: crate, lamp, rune and key doors and the card game draw, take the keyboard and throw nothing");
}

export async function checkLumiPetFrame(page) { // Parent-origin remote messages must reach Lumi and report status back.
  await page.evaluate(() => {
    window.petReport = "";
    window.addEventListener("message", e => {
      const frame = document.getElementById("mc-game-frame");
      if (e.origin === location.origin && e.source === frame.contentWindow && e.data.type === "mnemerynne-game-state") window.petReport = e.data.sub;
    });
    [...document.querySelectorAll(".mc-card")].find(b => b.querySelector(".mc-label").textContent === "Lumi Pet").click();
  });
  const frame = await (await page.waitForSelector("#mc-game-frame")).contentFrame();
  await frame.waitForSelector(row("cuddle"));
  await page.evaluate(() => { // Previous wraps straight to Settings, and ▼ steps from Glow to Sound.
    const target = document.getElementById("mc-game-frame").contentWindow;
    for (const key of ["MediaTrackPrevious", "ArrowDown"]) target.postMessage({ type: "mnemerynne-game-key", key }, location.origin);
  });
  await page.waitForFunction(() => window.petReport.includes("Settings: Sound"));
  const before = await frame.$eval(row("sound"), el => el.textContent.includes("Sound: on"));
  await page.evaluate(() => document.getElementById("mc-game-frame").contentWindow.postMessage({ type: "mnemerynne-game-key", key: "Enter" }, location.origin));
  await frame.waitForFunction(was => document.querySelector('[data-lp-row="sound"]').textContent.includes("Sound: on") !== was, {}, before);
  assert.equal(await frame.$(row("install")), null, "Inside Media the frame around it is the app: nothing to install");
  await frame.click(row("open-lumi-garden")); // One of Lumi's apps opens another: Media's layer swaps the game, so its bar (and a remote) follow.
  await page.waitForFunction(() => document.getElementById("mc-game-title").textContent === "Lumi’s Garden");
  const garden = await (await page.waitForSelector("#mc-game-frame")).contentFrame();
  await garden.waitForFunction(() => document.getElementById("gm") && document.getElementById("gm").dataset.game === "lumi-garden");
  await garden.waitForSelector(row("press"));
  await garden.focus(row("press"));
  await garden.page().keyboard.press("Escape");
  await page.waitForFunction(() => document.getElementById("mc-game").hidden);
  console.log("Lumi Pet: Media card, frame remote controls, status report, opening Lumi's Garden from inside the frame, and Escape passed");
}

export async function checkLumiPetGarden(browser, page, base) { // The Stardust Garden in a real browser: keyboard, mouse, both minigames, a reload.
  const tab = await browser.newPage();
  const errors = [];
  tab.on("pageerror", error => errors.push(String(error)));
  const asked = (words, act) => Promise.all([tab.waitForResponse(answer => answer.url().endsWith("/lumi-pet/garden") && (answer.request().postData() || "").includes(words)), act()]).then(both => both[0].json());
  const story = () => tab.$eval("#lp-story", el => el.textContent);
  try {
    await tab.setCookie(...await page.cookies());
    await tab.setViewport({ width: 1440, height: 1000 });
    await tab.goto(base + "/media/games/lumi-pet#garden"); // The address an old "flowers are ready" reminder opened, from when the garden was a tab of Lumi Pet: she sends it on.
    await tab.waitForSelector(row("press"));
    assert.equal(await tab.evaluate(() => location.pathname), "/media/games/lumi-garden", "Lumi's Garden is an app of its own");
    assert.deepEqual(await tab.$$eval("[data-lp-tab]", tabs => tabs.map(tab => tab.dataset.lpTab)), ["garden", "settings"]);
    assert.equal(await tab.$eval(".gm-title", el => el.textContent), "LUMI’SGARDEN");
    assert.equal(await tab.$eval('[data-lp-tab="garden"]', el => el.getAttribute("aria-selected")), "true");
    assert.equal(await tab.$eval(".lp-tabs", el => getComputedStyle(el).display), "none", "The garden has the panel to itself, like a dungeon trip");
    assert.equal(await tab.$eval("#lp-room", el => el.dataset.garden), "true");
    assert.match(await tab.$eval("#lp-keys", el => el.textContent), /move over the plots.*presses the powder/);
    await tab.waitForFunction(() => document.querySelectorAll('#lp-dungeon [data-plot="empty"]').length === 4);
    assert.ok(await tab.$(row("press") + '[aria-pressed="true"]'), "The list starts on the press, so Enter squeezes at once");

    await tab.keyboard.press("ArrowRight"); // The arrows walk the plots; Enter plants the chosen seed.
    await tab.waitForSelector('#lp-dungeon [data-cursor="1"]');
    assert.equal(await tab.$(row("press") + '[aria-pressed="true"]'), null, "With the cursor on a plot, Enter belongs to the plot");
    assert.match(await story(), /Plot 2: empty · Enter plants Moondaisy/);
    await tab.keyboard.press("Enter");
    await said(tab, "Planted: Moondaisy");
    await tab.click('[data-click="plot:0"]'); // The mouse plants too.
    await tab.waitForFunction(() => document.querySelectorAll('#lp-dungeon [data-plot="growing"]').length === 2);
    const timer = () => tab.$eval('#lp-dungeon [data-timer="1"]', el => el.textContent); // A timer under every flower, counting down where it stands.
    const ticking = await timer();
    assert.match(ticking, /^[0-2]:\d\d$/);
    await tab.waitForFunction(was => document.querySelector('#lp-dungeon [data-timer="1"]').textContent !== was, { timeout: 5000 }, ticking);
    assert.ok(await tab.$eval("#lp-dungeon", el => ["open", "painted"].includes(el.querySelector("[data-sky]").dataset.sky)), "The sky is the theme's scenery, or painted");
    assert.equal(await tab.$eval("#lp-house", el => getComputedStyle(el).visibility), "hidden", "The garden is outdoors: the house is out of sight");
    const beside = await tab.$eval("#lp-room", el => [el.style.getPropertyValue("--gx"), el.style.getPropertyValue("--gy")]); // Lumi has darted to the plot just planted.
    assert.ok(Number(beside[0]) < 0 && Number(beside[1]) > 150, "Lumi stands beside the first plot: " + beside);
    const lumi = await tab.$eval(".lp-pet-wrap", el => el.getBoundingClientRect().width), plot = await tab.$eval('[data-click="plot:0"]', el => el.getBoundingClientRect().width);
    assert.ok(lumi < plot, "She is small enough to stand beside a flower");
    await tab.keyboard.press("ArrowDown"); // Moving the cursor brings her along (down from plot 2 is the patch for a new plot).
    assert.notDeepEqual(await tab.$eval("#lp-room", el => [el.style.getPropertyValue("--gx"), el.style.getPropertyValue("--gy")]), beside);
    await tab.keyboard.press("ArrowUp");
    await tab.screenshot({ path: "runs/casino-browser/lumi-pet-garden-flowers.png", fullPage: true });
    await tab.click('[data-click="plot:0"]');
    await said(tab, "Moondaisy: ready in"); // Pressing a growing flower only says how long is left.

    await tab.keyboard.press("PageUp"); // Previous and Next move through the list, since the arrows are in the beds.
    assert.ok(await tab.$(row("leave") + '[aria-pressed="true"]'));
    await tab.keyboard.press("PageDown");
    assert.ok(await tab.$(row("press") + '[aria-pressed="true"]'));

    await tab.click(row("feed")); // Fertilizing: ten seconds of presses, by Enter, Play or the big button.
    await tab.waitForFunction(() => document.getElementById("lp-dungeon").textContent.includes("PRESS! PRESS! PRESS!"));
    for (let i = 0; i < 14; i++) await tab.keyboard.press("Enter");
    for (let i = 0; i < 14; i++) await tab.keyboard.press("p");
    const button = await tab.$eval('[data-click="feed"]', el => { const box = el.getBoundingClientRect(); return [box.x + box.width / 2, box.y + box.height / 2]; });
    for (let i = 0; i < 14; i++) await tab.mouse.click(button[0], button[1]); // By where it is: the panel is redrawn as its clock runs down.
    assert.match(await tab.$eval(row("feeding"), el => el.textContent), /42 presses/);
    const fed = await asked("feed_end", () => Promise.resolve());
    assert.deepEqual([fed.said.presses, fed.said.minutes, fed.said.moved], [42, 4, 2]);
    await said(tab, "42 presses: every growing flower is 4 minutes further on");
    await tab.waitForFunction(() => document.querySelectorAll('#lp-dungeon [data-plot="ready"]').length === 2); // Two-minute daisies, four minutes on.
    assert.match(await tab.$eval(row("feed"), el => el.textContent), /ready in/);

    const trimmed = await asked('"trim"', () => tab.click('[data-click="plot:0"]')); // Trim one with the mouse, one with the keyboard.
    assert.equal(trimmed.said.powder, 6);
    await said(tab, "Snip! One flower trimmed: +6 starpowder");
    await tab.keyboard.press("ArrowRight");
    const powder = (await asked('"trim"', () => tab.keyboard.press("Enter"))).garden.powder; // 12, or more if a gift of powder turned up (3 in 100 trims).
    assert.ok(powder >= 12 && powder < 100, "Two daisies' worth of starpowder: " + powder);
    const squeezed = Math.min(powder, 21) + " of 40 squeezed"; // Seven presses at 3 each, or all there is.
    for (let i = 0; i < 6; i++) await tab.keyboard.press("p"); // Play/Pause is the press; presses go to the server in a batch.
    await tab.click('[data-click="press"]');
    await tab.waitForFunction(words => document.getElementById("lp-story").textContent.includes(words), { timeout: 8000 }, squeezed);
    assert.match(await story(), new RegExp("Starpowder waiting: " + Math.max(0, powder - 21) + " "));

    const started = await asked("water_start", () => tab.click(row("water"))); // Watering: the server's sequence, repeated with the arrows.
    const arrows = ["ArrowUp", "ArrowRight", "ArrowDown", "ArrowLeft"], first = started.garden.water.show;
    assert.equal(first.length, 1);
    await tab.waitForFunction(() => document.getElementById("lp-dungeon").textContent.includes("YOUR TURN"));
    const second = await asked("water_answer", () => tab.keyboard.press(arrows[first[0]]));
    assert.deepEqual([second.said.water, second.said.round, second.garden.water.show.length], ["more", 2, 2]);
    await tab.waitForFunction(() => document.getElementById("lp-dungeon").textContent.includes("YOUR TURN · 0 OF 2"));
    await tab.click('[data-click="can:' + second.garden.water.show[0] + '"]'); // A can can be pressed with the mouse as well.
    const ended = await asked("water_answer", () => tab.keyboard.press(arrows[(second.garden.water.show[1] + 1) % 4])); // And one wrong can ends it.
    assert.deepEqual([ended.said.water, ended.said.rounds, ended.said.right], ["done", 1, false]);
    await said(tab, "a slip");
    await tab.waitForSelector(row("press"));

    await tab.click(row("helpers")); // A drawer: the arrows move through it, and its Back returns to the beds.
    await tab.waitForSelector(row("helper-monk"));
    await tab.keyboard.press("ArrowDown");
    assert.ok(await tab.$(row("helper-fighter") + '[aria-pressed="true"]'));
    await tab.click(row("back"));
    await tab.click(row("seeds"));
    assert.match(await tab.$eval(row("seed-glowbell"), el => el.textContent), /given 40 powder \(\d+ so far\)/);
    await tab.click(row("back"));
    await tab.click(row("decor")); // Decorations: fifteen slots, the Seedling Capsule and the store.
    await tab.waitForSelector(row("capsule"));
    assert.equal((await tab.$$('[data-lp-row^="slot-"]')).length, 15);
    await tab.click(row("capsule"));
    await said(tab, "A capsule costs 600 starpowder");
    await tab.click(row("slot-fence"));
    await tab.waitForSelector(row("decor-fence-0-snowy"));
    assert.equal((await tab.$$('[data-lp-row^="decor-fence-"]')).length, 15);
    await tab.click(row("decor-fence-2-royal"));
    await said(tab, "only comes from the Seedling Capsule");
    await tab.click(row("back")); // Back from a slot is the slots, and back again is the beds.
    await tab.waitForSelector(row("store"));
    await tab.click(row("store"));
    await tab.waitForSelector(row("empty"));
    await tab.click(row("back"));
    await tab.click(row("back"));
    await tab.waitForSelector(row("press"));
    await tab.screenshot({ path: "runs/casino-browser/lumi-pet-garden.png", fullPage: true });

    await tab.goto(base + "/media/games/lumi-garden"); // A reload: the same garden, straight away (it is this app's first tab).
    await tab.waitForSelector(row("press"));
    assert.ok((await story()).includes(squeezed), "The same garden after a reload");
    assert.match(await tab.$eval(row("leave"), el => el.textContent), /Settings/, "The garden's way out is this app's Settings, since her room is another app");
    await tab.click(row("leave"));
    await tab.waitForSelector(row("sound"));
    assert.equal(await tab.$eval('[data-lp-tab="settings"]', el => el.getAttribute("aria-selected")), "true");
    assert.equal(await tab.$eval("#lp-room", el => el.dataset.garden), "false");
    assert.notEqual(await tab.$eval(".lp-tabs", el => getComputedStyle(el).display), "none");
    assert.equal(await tab.$eval("#lp-dungeon", el => el.style.display), "none", "In Settings the garden's drawing is put away");
    assert.equal(await tab.$(row("glow")), null, "Her colour is Lumi Pet's and Lumi Dungeons' to change");
    await tab.click('[data-lp-tab="garden"]'); // And back to the beds by the tab.
    await tab.waitForSelector(row("press"));
    assert.deepEqual(errors, [], "Nothing threw on the page");
    console.log("Lumi's Garden: its own app, planting and trimming by keyboard and mouse, the press, fertilizing, watering, drawers, Settings and a reload passed");
  } finally { if (errors.length) console.log("Page errors:", errors.join(" | ")); await tab.close(); }
}

export async function checkLumiPetSkills(browser, page, base) { // The skill tree page and the collection, in a real browser.
  const tab = await browser.newPage();
  const errors = [];
  tab.on("pageerror", error => errors.push(String(error)));
  const cursor = () => tab.$eval("#lp-dungeon [data-cursor]", el => el.dataset.cursor);
  try {
    await tab.setCookie(...await page.cookies());
    await tab.setViewport({ width: 1440, height: 1000 });
    await tab.goto(base + "/media/games/lumi-dungeons"); // The skill trees are Lumi Dungeons'.
    await tab.waitForSelector(places);
    assert.equal((await tab.$$("[data-lp-tab]")).length, 4);
    await tab.click('[data-lp-tab="skills"]'); // All six trees on one page, with the panel to itself like the garden.
    await tab.waitForSelector(row("act"));
    assert.equal(await tab.$eval(".lp-tabs", el => getComputedStyle(el).display), "none");
    assert.equal((await tab.$$("#lp-dungeon [data-skill]")).length, 48);
    assert.equal((await tab.$$("#lp-dungeon [data-tree]")).length, 6);
    assert.ok(await tab.$eval(".lp-pet-wrap", el => getComputedStyle(el).opacity === "0"), "Lumi steps aside for the trees");
    assert.equal(await cursor(), "heavy-paw");
    assert.match(await tab.$eval("#lp-story", el => el.textContent), /skill points? to spend.*Fighter: Heavy Paw/s);
    await tab.keyboard.press("ArrowDown"); // The arrows walk the web.
    assert.equal(await cursor(), "tough-fur");
    await tab.keyboard.press("ArrowRight");
    assert.equal(await cursor(), "armor-break");
    await tab.keyboard.press("Enter"); // A skill behind another says what it needs (or, with points and its root learned, would be learned).
    await tab.waitForFunction(() => /needs Heavy Paw first|costs \d skill points?|Lumi learned/.test(document.getElementById("lp-message").textContent));
    await tab.click('[data-click="skill:starfall"]'); // The mouse: a press looks at a skill.
    await tab.waitForFunction(() => document.querySelector("#lp-dungeon [data-cursor]").dataset.cursor === "starfall");
    assert.match(await tab.$eval("#lp-story", el => el.textContent), /Mage: Starfall.*Costs 5 points · needs/s);
    assert.match(await tab.$eval(row("act"), el => el.textContent), /Starfall/);
    await tab.keyboard.press("PageDown"); // Previous and Next reach the list.
    assert.ok(await tab.$(row("leave") + '[aria-pressed="true"]') || await tab.$(row("respec") + '[aria-pressed="true"]'));
    await tab.screenshot({ path: "runs/casino-browser/lumi-pet-skills.png", fullPage: true });
    assert.match(await tab.$eval(row("leave"), el => el.textContent), /Back to the dungeons/);
    await tab.click(row("leave"));
    await tab.waitForSelector(places); // This app's first tab: the places to explore.
    assert.equal(await tab.$eval("#lp-room", el => el.dataset.skills), "false");
    assert.equal(await tab.$eval("#lp-dungeon", el => el.style.display), "none");

    await tab.goto(base + "/media/games/lumi-pet"); // The Collection is in her room's app.
    await tab.waitForSelector(row("cuddle"));
    assert.equal((await tab.$$("[data-lp-tab]")).length, 8);
    await tab.click('[data-lp-tab="collection"]'); // The Collection: a gallery over the room, like the trees.
    await tab.waitForSelector(row("look"));
    assert.equal(await tab.$eval(".lp-tabs", el => getComputedStyle(el).display), "none");
    assert.equal(await tab.$eval("#lp-room", el => el.dataset.album), "true");
    assert.equal((await tab.$$('#lp-dungeon [data-click^="shelf:"]')).length, 7);
    const cells = (await tab.$$('#lp-dungeon [data-click^="cell:"]')).length;
    assert.ok(cells > 10 && cells <= 36, "A page of pictures: " + cells);
    const fitted = await tab.$$eval("#lp-dungeon svg[data-thumb]", frames => frames.filter(frame => frame.getAttribute("viewBox") !== "0 0 400 500").length);
    assert.ok(fitted >= cells - 2, "Each picture is framed round its own drawing: " + fitted + " of " + cells);
    assert.ok(await tab.$$eval('#lp-dungeon [data-cell="have"]', owned => owned.length) >= 1, "What came with the house is hers");
    assert.match(await tab.$eval("#lp-story", el => el.textContent), /Collected \d+ of \d+.*For the house: \d+ of \d+/s);
    const gallery = () => tab.$eval("#lp-dungeon [data-cursor]", el => el.dataset.cursor);
    assert.equal(await gallery(), "0");
    await tab.keyboard.press("ArrowRight"); // The arrows move over the pictures.
    await tab.keyboard.press("ArrowDown");
    assert.equal(await gallery(), "7");
    await tab.keyboard.press("Enter"); // Enter looks at one large; Enter again goes back.
    await tab.waitForSelector("#lp-dungeon [data-zoom]");
    assert.match(await tab.$eval(row("look"), el => el.textContent), /Back to the shelf/);
    await tab.keyboard.press("Enter");
    await tab.waitForFunction(() => !document.querySelector("#lp-dungeon [data-zoom]"));
    await tab.click('[data-click="shelf:garden"]'); // The mouse: a shelf, a page arrow, a picture.
    await tab.waitForFunction(() => document.getElementById("lp-story").textContent.includes("Garden decorations: "));
    await tab.click('[data-click="page:1"]');
    await tab.waitForFunction(() => document.getElementById("lp-dungeon").textContent.includes("page 2 of 7"));
    await tab.click('[data-click="cell:4"]');
    await tab.waitForFunction(() => document.querySelector("#lp-dungeon [data-cursor]").dataset.cursor === "4");
    await tab.click('[data-click="cell:4"]'); // A second press looks at it large; a press on the large picture puts it away.
    await tab.waitForSelector("#lp-dungeon [data-zoom]");
    await tab.screenshot({ path: "runs/casino-browser/lumi-pet-collection-zoom.png", fullPage: true });
    await tab.click('[data-click="zoom"]');
    await tab.waitForFunction(() => !document.querySelector("#lp-dungeon [data-zoom]"));
    await tab.click(row("shelf-flowers")); // The list reaches every shelf too.
    await tab.waitForFunction(() => document.querySelectorAll('#lp-dungeon [data-click^="cell:"]').length === 36);
    await tab.screenshot({ path: "runs/casino-browser/lumi-pet-collection.png", fullPage: true });
    await tab.click(row("leave"));
    await tab.waitForSelector(row("cuddle"));
    assert.equal(await tab.$eval("#lp-room", el => el.dataset.album), "false");
    assert.deepEqual(errors, [], "Nothing threw on the page");
    console.log("Lumi Dungeons' skills and Lumi Pet's collection: six trees drawn, walked by arrows and mouse; the gallery's shelves, pages, pictures and close look passed");
  } finally { if (errors.length) console.log("Page errors:", errors.join(" | ")); await tab.close(); }
}

export async function checkLumiPetPhone(browser, page, base) { // A phone: one screen, touch controls, and an app of her own.
  const tab = await browser.newPage();
  const errors = [];
  tab.on("pageerror", error => errors.push(String(error)));
  const shot = name => tab.screenshot({ path: "runs/casino-browser/lumi-pet-phone-" + name + ".png" });
  const fits = async what => assert.ok(await tab.evaluate(() => document.scrollingElement.scrollHeight <= innerHeight + 2 && document.scrollingElement.scrollWidth <= innerWidth + 2), what + ": the page itself never scrolls");
  const box = selector => tab.$eval(selector, el => { const b = el.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, left: b.left, right: b.right, width: b.width, height: b.height }; });
  try {
    await tab.setCookie(...await page.cookies());
    await tab.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
    await tab.goto(base + "/media/games/lumi-pet");
    await tab.waitForSelector(row("cuddle"));
    await fits("Care");
    const strip = await box(".lp-intro"), room = await box("#lp-room"), panel = await box(".lp-controls"), tabs = await box(".lp-tabs"), list = await box("#lp-list");
    assert.ok(strip.bottom <= room.top + 1 && room.bottom <= panel.top + 1 && tabs.bottom <= 844 + 1, "Her needs, then her room, then the list, with the tabs along the bottom");
    assert.ok(strip.height < 80 && room.height > 250 && list.height > 150, "The strip is small; the room and the list get the screen: " + JSON.stringify([strip.height, room.height, list.height]));
    assert.ok(room.width >= 388 && list.width > 350, "Edge to edge");
    assert.equal(await tab.$eval("#lp-keys", el => getComputedStyle(el).display), "none", "No key legend on a phone");
    assert.equal(await tab.$eval(".gm-title", el => getComputedStyle(el).display), "none");
    assert.ok(await tab.$eval(".lp-tabs", el => el.scrollWidth > el.clientWidth + 50 && getComputedStyle(el).display === "flex"), "The tabs are a bar to swipe through");
    assert.ok(await tab.$$eval("#lp-list button", buttons => buttons.every(button => button.getBoundingClientRect().height >= 44)), "Every row is big enough for a thumb");
    assert.ok(await tab.$$eval("[data-lp-tab]", buttons => buttons.every(button => button.getBoundingClientRect().height >= 40 && button.getBoundingClientRect().width >= 60)));
    assert.match(await text(tab, "#lp-coins"), /^\d+$/);
    assert.deepEqual(await tab.$$eval("#lp-pad, #lp-squeeze", controls => controls.map(control => getComputedStyle(control).display)), ["none", "none"], "No touch controls where there is nothing for them to do");
    await shot("care");

    assert.match(await tab.$eval('link[rel="manifest"]', el => el.getAttribute("href")), /\/media\/games\/lumi-pet\/manifest\.webmanifest$/); // Her own app.
    const manifest = await tab.evaluate(() => fetch(document.querySelector('link[rel="manifest"]').href).then(answer => answer.json()));
    assert.deepEqual([manifest.name, manifest.start_url, manifest.scope, manifest.display, manifest.icons.length], ["Lumi Pet", "/media/games/lumi-pet", "/media/games/lumi-pet", "standalone", 3]);
    for (const icon of manifest.icons) assert.equal(await tab.evaluate(src => fetch(src).then(answer => answer.status + ":" + answer.headers.get("content-type")), icon.src), "200:image/png");
    await tab.tap('[data-lp-tab="settings"]');
    await tab.waitForSelector(row("install"));
    assert.match(await tab.$eval(row("install"), el => el.textContent), /Install Lumi Pet.*home screen/s);
    // (Not pressed here: where the browser offers to install her, the row opens the browser's own dialog, which a test can't answer.)

    await tab.goto(base + "/media/games/lumi-garden"); // Lumi's Garden: plots under the thumb, and a big button for the press.
    await tab.waitForSelector(row("press"));
    assert.match(await tab.$eval('link[rel="manifest"]', el => el.getAttribute("href")), /\/media\/games\/lumi-garden\/manifest\.webmanifest$/); // An app of its own too.
    const gardenApp = await tab.evaluate(() => fetch(document.querySelector('link[rel="manifest"]').href).then(answer => answer.json()));
    assert.deepEqual([gardenApp.name, gardenApp.start_url, gardenApp.scope], ["Lumi’s Garden", "/media/games/lumi-garden", "/media/games/lumi-garden"]);
    for (const icon of gardenApp.icons) assert.equal(await tab.evaluate(src => fetch(src).then(answer => answer.status + ":" + answer.headers.get("content-type")), icon.src), "200:image/png");
    await fits("Garden");
    await tab.waitForSelector("#lp-squeeze:not([hidden])");
    assert.equal(await tab.$eval(".lp-tabs", el => getComputedStyle(el).display), "flex", "On a phone the tab bar stays out in the garden: another tab is one tap away");
    const squeeze = await box("#lp-squeeze"), garden = await box("#lp-room");
    assert.ok(squeeze.width >= 64 && squeeze.bottom <= garden.bottom && squeeze.right <= 390, "A thumb-sized press button in the corner of the garden");
    const plot = await box('[data-click="plot:0"]');
    assert.ok(plot.width >= 44 && plot.height >= 40, "A plot is big enough to tap: " + JSON.stringify([plot.width, plot.height]));
    await tab.tap("#lp-squeeze");
    await tab.waitForFunction(() => document.getElementById("lp-room").dataset.gardenAct && document.getElementById("lp-room").dataset.gardenAct.startsWith("bounce"));
    await shot("garden");
    await tab.tap(row("leave")); // Its Settings.
    await tab.waitForSelector(row("install"));
    assert.match(await tab.$eval(row("install"), el => el.textContent), /Install Lumi’s Garden/);
    assert.ok(await tab.$eval("#lp-squeeze", el => el.hidden));

    await tab.goto(base + "/media/games/lumi-dungeons"); // The skill trees (Lumi Dungeons) and the collection (Lumi Pet) get most of the screen.
    await tab.waitForSelector(places);
    await fits("Dungeons");
    assert.match(await tab.$eval('link[rel="manifest"]', el => el.getAttribute("href")), /\/media\/games\/lumi-dungeons\/manifest\.webmanifest$/);
    await tab.tap('[data-lp-tab="skills"]');
    await tab.waitForSelector(row("act"));
    await fits("Skills");
    const trees = await box("#lp-room"), node = await box('[data-click="skill:heavy-paw"]');
    assert.ok(trees.height > 420 && node.width >= 24, "The trees are large enough to tap: " + JSON.stringify([trees.height, node.width]));
    await tab.tap('[data-click="skill:ember"]');
    await tab.waitForFunction(() => document.querySelector("#lp-dungeon [data-cursor]").dataset.cursor === "ember");
    await shot("skills");
    await tab.tap(row("leave"));
    await tab.waitForSelector(places);
    await tab.goto(base + "/media/games/lumi-pet");
    await tab.waitForSelector(row("cuddle"));
    await tab.tap('[data-lp-tab="collection"]');
    await tab.waitForSelector(row("look"));
    await fits("Collection");
    await tab.tap('[data-click="cell:8"]');
    await tab.waitForFunction(() => document.querySelector("#lp-dungeon [data-cursor]").dataset.cursor === "8");
    await shot("collection");
    await tab.tap(row("leave"));
    await tab.waitForSelector(row("cuddle"));

    assert.ok(await tab.$eval("#lp-pad", el => el.hidden), "No arrow pad until she is out exploring");
    // The dungeon: an arrow pad to walk with. (She may be busy playing from an earlier check and unable to set out, so, as
    // in the door checks, a second page is shown the server's own state with a trip under way.)
    const out = await browser.newPage();
    out.on("pageerror", error => errors.push(String(error)));
    try {
      await out.setCookie(...await page.cookies());
      await out.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
      await out.evaluateOnNewDocument(() => {
        const real = window.fetch;
        window.fetch = async function (url, options) {
          const answer = await real(url, options);
          if (!String(url).endsWith("/lumi-pet/state")) return answer;
          const data = await answer.json(), env = data.dungeon.environments[0];
          data.fight = null;
          data.dungeon.run = { id: 78, env: { id: env.id, name: env.name, family: env.family, palette: env.palette }, depth: 1, at: "1,1", over: null, ended: null,
            rooms: { "1,1": { kind: "empty", done: true, exits: ["e", "n"] }, "2,1": { kind: "unknown" }, "1,0": { kind: "unknown" } },
            room: { kind: "empty", done: true, exits: ["e", "n"] }, satchel: { coins: 12, potions: 0, keys: 0, trophies: [], mods: [] },
            hp: 40, hpMax: 58, mp: 20, mpMax: 22, event: { scenario: "empty" }, say: "A quiet room.", scene: "Moonlight on old stone.", story: ["Lumi set out.", "A quiet room."],
            fighting: false, facing: null, game: null };
          return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
        };
      });
      await out.goto(base + "/media/games/lumi-dungeons");
      await out.waitForSelector(row("go-e"));
      await out.waitForSelector("#lp-pad:not([hidden])");
      assert.equal(await out.$eval(".lp-tabs", el => getComputedStyle(el).display), "none", "On a trip the tab bar is put away");
      assert.equal(await out.$eval("#lp-squeeze", el => getComputedStyle(el).display), "none");
      assert.ok(await out.evaluate(() => document.scrollingElement.scrollHeight <= innerHeight + 2), "Dungeon: the page itself never scrolls");
      const open = await out.$$eval("#lp-pad [data-pad]:not([hidden]):not(:disabled)", buttons => buttons.map(button => button.dataset.pad));
      assert.deepEqual([...open].sort(), ["right", "up"], "The pad offers exactly the ways there are: east and north");
      assert.ok(await out.$eval('#lp-pad [data-pad="enter"]', el => el.hidden), "No middle button without a puzzle board");
      const key = await out.$eval('#lp-pad [data-pad="up"]', el => { const b = el.getBoundingClientRect(); return [b.width, b.height, b.bottom <= document.getElementById("lp-room").getBoundingClientRect().bottom]; });
      assert.ok(key[0] >= 44 && key[1] >= 44 && key[2], "Pad buttons are thumb-sized and inside the room: " + key);
      await out.screenshot({ path: "runs/casino-browser/lumi-pet-phone-dungeon.png" });
      await Promise.all([out.waitForRequest(request => request.url().endsWith("/dungeon/act") && (request.postData() || "").includes('"way":"n"')), out.tap('#lp-pad [data-pad="up"]')]); // A tap walks her north.
    } finally { await out.close(); }
    assert.deepEqual(errors, [], "Nothing threw on the page");
    console.log("Lumi's apps on a phone: one screen with a tab bar, thumb-sized rows, the press button, the arrow pad, the trees and gallery by touch, and each app's own manifest passed");
  } finally { if (errors.length) console.log("Page errors:", errors.join(" | ")); await tab.close(); }
}

export async function checkLumiApps(browser, page, base) { // One pet in three apps: what each shows, the ways between them, and Lumi being away.
  const tab = await browser.newPage();
  const errors = [];
  tab.on("pageerror", error => errors.push(String(error)));
  const tabs = () => tab.$$eval("[data-lp-tab]", buttons => buttons.map(button => button.dataset.lpTab));
  try {
    await tab.setCookie(...await page.cookies());
    await tab.setViewport({ width: 1440, height: 1000 });
    await tab.goto(base + "/media/games/lumi-pet");
    await tab.waitForSelector(row("cuddle"));
    assert.deepEqual(await tabs(), ["care", "pantry", "toys", "closet", "home", "shop", "collection", "settings"]);
    assert.deepEqual(await tab.$eval("#gm", el => [el.dataset.game, el.dataset.kit, el.dataset.name]), ["lumi-pet", "lumi-pet", "Lumi Pet"]);
    assert.equal(await tab.$eval("#lp-room", el => el.dataset.away), "false");
    const coins = await text(tab, "#lp-coins"), level = await text(tab, "#lp-level");
    await tab.click('[data-lp-tab="settings"]'); // Settings lists her other two apps, and no storyteller: that is the dungeons'.
    await tab.waitForSelector(row("open-lumi-dungeons"));
    assert.ok(await tab.$(row("open-lumi-garden")) && await tab.$(row("glow")) && await tab.$(row("install")));
    assert.equal(await tab.$(row("storyteller")), null);
    assert.equal(await tab.$(row("open-lumi-pet")), null, "An app doesn't offer to open itself");
    await Promise.all([tab.waitForNavigation(), tab.click(row("open-lumi-dungeons"))]);
    await tab.waitForSelector(places);
    if (await tab.$(row("continue"))) { // How her last trip ended is read once on a device: after that the app opens on the places to explore.
      await tab.click(row("continue"));
      await tab.waitForSelector(row("env-moonlit-shrine"));
      await tab.reload();
      await tab.waitForSelector(row("env-moonlit-shrine"));
    }
    assert.equal(await tab.evaluate(() => location.pathname), "/media/games/lumi-dungeons");
    assert.deepEqual(await tabs(), ["dungeon", "battle", "skills", "settings"]);
    assert.equal(await tab.$eval(".gm-title", el => el.textContent), "LUMIDUNGEONS");
    assert.deepEqual([await text(tab, "#lp-coins"), await text(tab, "#lp-level")], [coins, level], "The same Lumi and the same purse");
    assert.equal(await tab.$(row("cuddle")), null);
    await tab.keyboard.press("PageUp"); // Previous wraps to this app's Settings: the storyteller lives here, reminders don't.
    await tab.waitForSelector(row("storyteller"));
    assert.ok(await tab.$(row("glow")) && await tab.$(row("open-lumi-pet")) && await tab.$(row("open-lumi-garden")));
    assert.equal(await tab.$(row("reminders")), null);
    assert.match(await tab.$eval(row("install"), el => el.textContent), /Install Lumi Dungeons/);
    await Promise.all([tab.waitForNavigation(), tab.click(row("open-lumi-garden"))]);
    await tab.waitForSelector(row("press"));
    assert.deepEqual(await tabs(), ["garden", "settings"]);
    await tab.click(row("leave"));
    await tab.waitForSelector(row("open-lumi-pet"));
    await Promise.all([tab.waitForNavigation(), tab.click(row("open-lumi-pet"))]);
    await tab.waitForSelector(row("cuddle"));
    assert.equal(await tab.evaluate(() => location.pathname), "/media/games/lumi-pet");
    for (const slug of ["lumi-pet", "lumi-dungeons", "lumi-garden"]) { // Each is an app of its own, with its own icons.
      const manifest = await tab.evaluate(slug => fetch("/media/games/" + slug + "/manifest.webmanifest").then(answer => answer.json()), slug);
      assert.deepEqual([manifest.id, manifest.scope, manifest.shortcuts.length], ["/media/games/" + slug, "/media/games/" + slug, 2]);
    }

    // Lumi out on a trip, as her other two apps see it. (As in the door checks, the page is shown the server's own state with a trip under way.)
    for (const slug of ["lumi-pet", "lumi-garden"]) {
      const away = await browser.newPage();
      away.on("pageerror", error => errors.push(String(error)));
      try {
        await away.setCookie(...await page.cookies());
        await away.setViewport({ width: 1440, height: 1000 });
        await away.evaluateOnNewDocument(() => {
          const real = window.fetch;
          window.fetch = async function (url, options) {
            const answer = await real(url, options);
            if (!String(url).endsWith("/lumi-pet/state")) return answer;
            const data = await answer.json(), env = data.dungeon.environments[0];
            data.fight = null; data.pet.task = null; // (She may still be playing with a toy from an earlier check.)
            data.dungeon.run = { id: 79, env: { id: env.id, name: env.name, family: env.family, palette: env.palette }, depth: 2, at: "1,1", over: null, ended: null,
              rooms: { "1,1": { kind: "empty", done: true, exits: ["e"] }, "2,1": { kind: "unknown" } }, room: { kind: "empty", done: true, exits: ["e"] },
              satchel: { coins: 12, potions: 0, keys: 0, trophies: [], mods: [] }, hp: 40, hpMax: 58, mp: 20, mpMax: 22, event: { scenario: "empty" },
              say: "A quiet room.", scene: "Moonlight on old stone.", story: ["Lumi set out.", "A quiet room."], fighting: false, facing: null, game: null };
            return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
          };
        });
        await away.goto(base + "/media/games/" + slug);
        await away.waitForSelector(row("away"));
        assert.match(await away.$eval(row("away"), el => el.textContent), /Lumi is out exploring.*Open Lumi Dungeons/s, slug);
        assert.equal(await away.$(row(slug === "lumi-pet" ? "cuddle" : "press")), null, slug + ": nothing to do with her until she is home");
        assert.deepEqual(await away.$eval("#lp-room", el => [el.dataset.away, el.dataset.dungeon, el.dataset.garden]), ["true", "false", "false"], slug + ": her room waits, and no dungeon is drawn here");
        assert.equal(await away.$eval(".lp-pet-wrap", el => getComputedStyle(el).opacity), "0", slug + ": she isn't in the room");
        assert.equal(await away.$eval("#lp-dungeon", el => getComputedStyle(el).display), "none");
        assert.equal(await text(away, "#lp-state"), "Exploring · floor 2");
        assert.match(await text(away, "#lp-message"), /Lumi is out exploring/);
        assert.notEqual(await away.$eval(".lp-tabs", el => getComputedStyle(el).display), "none", slug + ": its tabs stay in reach");
        if (slug === "lumi-pet") { // The rest of her room's app still works while she is out: the pantry can send her a snack.
          await away.click('[data-lp-tab="pantry"]');
          await away.waitForSelector(row("apple"));
          await away.click('[data-lp-tab="care"]');
          await away.waitForSelector(row("away"));
        }
        await Promise.all([away.waitForNavigation(), away.click(row("away"))]);
        assert.equal(await away.evaluate(() => location.pathname), "/media/games/lumi-dungeons", slug + ": the row goes to where she is");
        await away.waitForSelector(row("go-e")); // And Lumi Dungeons picks the trip up, in the dungeon.
        assert.equal(await away.$eval("#lp-room", el => el.dataset.dungeon), "true");
      } finally { await away.close(); }
    }
    assert.deepEqual(errors, [], "Nothing threw on the page");
    console.log("Lumi's three apps: each one's own tabs and Settings, the Open rows between them, one pet and purse, three manifests, and Lumi away on a trip passed");
  } finally { if (errors.length) console.log("Page errors:", errors.join(" | ")); await tab.close(); }
}
