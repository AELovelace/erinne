// Included by connected-browser.mjs so the pet is checked against the real, disposable Media application.
import assert from "node:assert/strict";

const text = (page, id) => page.$eval(id, el => el.textContent);
const row = key => `[data-lp-row="${key}"]`;
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
  await page.keyboard.press("ArrowRight"); // Pantry
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
  await said(page, "braces");
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

  await page.click('[data-lp-tab="toys"]');
  await page.click(row("star-ribbon"));
  await page.waitForFunction(() => document.getElementById("lp-state").textContent.startsWith("Playing · 1:"));
  assert.equal(await page.$eval("#lp-room", el => el.dataset.action), "play");
  await page.click('[data-lp-tab="care"]');
  await page.click(row("nap")); // One thing at a time.
  await said(page, "busy playing");
  await page.screenshot({ path: "runs/casino-browser/lumi-pet.png", fullPage: true });

  await page.click('[data-lp-tab="closet"]'); // Dressing up: highlight to try on, Enter to buy and wear, Enter again to take off.
  await page.waitForSelector(row("acc-bow"));
  assert.equal(await page.$eval("#lp-acc-front", el => el.children.length), 0);
  await page.keyboard.press("ArrowDown"); // From the Star Capsule to the bow: she tries it on before it's bought.
  await page.waitForFunction(() => document.querySelector('#lp-acc-front [data-acc="head"]'));
  assert.equal(await text(page, "#lp-coins"), "1,017");
  await page.keyboard.press("Enter");
  await said(page, "Bought: Head · Ribbon bow");
  assert.equal(await text(page, "#lp-coins"), "957");
  await page.click(row("acc-crown")); // Capsule-only: it says so, and nothing is spent.
  await said(page, "only comes from the Star Capsule");
  await page.click(row("acc-cape"));
  await said(page, "Bought: Back · Hero cape");
  assert.equal(await page.$eval("#lp-acc-back", el => el.children.length), 1);
  await page.click(row("acc-cape"));
  await said(page, "takes it off");
  await page.screenshot({ path: "runs/casino-browser/lumi-pet-closet.png", fullPage: true });
  await page.click('[data-lp-tab="care"]');
  assert.ok(await page.$('#lp-acc-front [data-acc="head"]'), "She keeps the bow on after leaving the tab");

  await page.click('[data-lp-tab="home"]'); // Decorating: open a spot, preview by moving, buy and place with Enter.
  await page.waitForSelector(row("rug"));
  assert.ok(await page.$eval('#lp-house [data-spot="view"]', el => el.querySelectorAll(".lp-px").length === 3), "The window scrolls three strips");
  assert.equal(await page.$eval('#lp-house [data-spot="view"] .lp-px-3', el => getComputedStyle(el).animationName), "lp-scroll");
  await page.click(row("rug"));
  await page.waitForSelector(row("rug-moon"));
  const plain = await page.$eval('#lp-house [data-spot="rug"]', el => el.innerHTML);
  await page.keyboard.press("ArrowDown"); // From the placed oval to the moon rug: shown in the room before it's bought.
  await page.waitForFunction(was => document.querySelector('#lp-house [data-spot="rug"]').innerHTML !== was, {}, plain);
  assert.equal(await text(page, "#lp-coins"), "777");
  await page.keyboard.press("Enter");
  await said(page, "Bought and put out: Moon rug");
  assert.equal(await text(page, "#lp-coins"), "677");
  await page.waitForFunction(() => document.querySelector('[data-lp-row="rug-moon"] small').textContent === "In the room now");
  await page.click(row("back"));
  await page.waitForFunction(() => document.querySelector('[data-lp-row="rug"] small').textContent === "Moon rug");
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
  console.log("Lumi Pet: account save, old-save import, care, pantry, shop, a fight, toys, the closet, the house, settings, mobile, reduced motion and blocked storage passed");
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
  await page.evaluate(() => { // ▶ to Settings' neighbour and back would do; ◀ wraps straight to Settings, ▼ picks Sound.
    const target = document.getElementById("mc-game-frame").contentWindow;
    for (const key of ["ArrowLeft", "ArrowDown"]) target.postMessage({ type: "mnemerynne-game-key", key }, location.origin);
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
