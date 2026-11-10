// Included by connected-browser.mjs so the pet is checked against the real, disposable Media application.
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
const said = (page, words) => page.waitForFunction(w => document.getElementById("lp-message").textContent.includes(w), {}, words);

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
  await page.click(row("tabs")); // "Look after Lumi": the other tabs, with the trip still under way and Lumi in view.
  await page.waitForSelector(row("cuddle"));
  assert.equal(await page.$eval("#lp-room", el => el.dataset.dungeon + el.dataset.puzzle), "truefalse");
  assert.ok(await page.$eval(".lp-pet-wrap", el => getComputedStyle(el).opacity === "1"));
  assert.notEqual(await page.$eval(".lp-tabs", el => getComputedStyle(el).display), "none");
  await page.click('[data-lp-tab="dungeon"]');
  await page.waitForSelector(row("leave"));
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
  assert.match(await page.$eval('[data-lp-row="slot-head"] small', el => el.textContent), /Starlight crown ▶ · Legendary · only from the Star Capsule/);
  await page.keyboard.press("Enter");
  await said(page, "only comes from the Star Capsule");
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
  console.log("Lumi Pet: account save, old-save import, care, pantry, shop, a fight, a dungeon trip, toys, the closet, the house, settings, mobile, reduced motion and blocked storage passed");
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
            hp: 40, hpMax: 58, mp: 20, mpMax: 22, event: { scenario: "empty" }, say: "A door.", fighting: false,
            facing: made.facing ? { at: "2,1", lock: made.facing } : null, game: made.game || null };
          return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
        };
      }, made);
      await tab.goto(base + "/media/games/lumi-pet");
      await tab.waitForSelector(row(made.rows[0]));
      assert.equal(await tab.$eval('[data-lp-tab="dungeon"]', el => el.getAttribute("aria-selected")), "true", made.name + ": a trip under way opens on the Dungeon tab");
      for (const key of made.rows) assert.ok(await tab.$(row(key)), made.name + " offers " + key);
      assert.ok(await tab.$eval("#lp-dungeon", (el, title) => el.textContent.includes(title), made.title), made.name + " shows its panel");
      assert.ok(await tab.$eval(".lp-pet-wrap", el => getComputedStyle(el).opacity === "0"), made.name + ": Lumi steps aside for the panel");
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
      }
      assert.equal(await tab.$eval(".lp-tabs", el => getComputedStyle(el).display), "none", made.name + ": the tabs are folded away");
      await tab.click(row("tabs")); // "Look after Lumi": another tab, where the room and Lumi show without the panel.
      assert.ok(await tab.$eval("#lp-dungeon", (el, title) => !el.textContent.includes(title), made.title), made.name + ": no panel on another tab");
      assert.ok(await tab.$(row("cuddle")));
      await tab.click('[data-lp-tab="dungeon"]');
      await tab.waitForSelector(row(made.rows[0]));
      assert.deepEqual(errors, [], made.name + ": nothing threw on the page");
      if (made.name === "lamps") await tab.screenshot({ path: "runs/casino-browser/lumi-pet-door.png", fullPage: true });
    } finally { await tab.close(); }
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
  await page.evaluate(() => { // ▶ to Settings' neighbour and back would do; Previous wraps straight to Settings, ▼ ▼ passes Storyteller to Sound.
    const target = document.getElementById("mc-game-frame").contentWindow;
    for (const key of ["MediaTrackPrevious", "ArrowDown", "ArrowDown"]) target.postMessage({ type: "mnemerynne-game-key", key }, location.origin);
  });
  await page.waitForFunction(() => window.petReport.includes("Settings: Sound"));
  const before = await frame.$eval(row("sound"), el => el.textContent.includes("Sound: on"));
  await page.evaluate(() => document.getElementById("mc-game-frame").contentWindow.postMessage({ type: "mnemerynne-game-key", key: "Enter" }, location.origin));
  await frame.waitForFunction(was => document.querySelector('[data-lp-row="sound"]').textContent.includes("Sound: on") !== was, {}, before);
  await frame.focus(row("sound"));
  await frame.page().keyboard.press("Escape");
  await page.waitForFunction(() => document.getElementById("mc-game").hidden);
  console.log("Lumi Pet: Media card, frame remote controls, status report and Escape passed");
}
