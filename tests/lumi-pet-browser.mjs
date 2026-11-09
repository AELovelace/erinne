// Included by connected-browser.mjs so the pet is checked against the real, disposable Media application.
import assert from "node:assert/strict";

export async function checkLumiPet(browser, page, base) { // Exercise real controls, persistence and graceful failures.
  await page.goto(base + "/media/games/lumi-pet");
  await page.waitForSelector("#lp-pet");
  assert.equal(await page.$eval("#lp-moments", el => el.textContent), "0");
  await page.click('[data-lp-action="cuddle"]');
  assert.equal(await page.$eval("#lp-moments", el => el.textContent), "1");
  await page.waitForFunction(() => document.getElementById("lp-coins").textContent === "1,025"); // The day's first visit pays its coins.
  await page.focus("#lp-sound"); // Arrow navigation must supersede stale focus on a different control.
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  assert.equal(await page.$eval("#lp-memory", el => el.textContent), "A shared star biscuit");
  await page.click('[data-lp-action="play"]');
  assert.equal(await page.$eval("#lp-room", el => el.dataset.action), "play");
  await page.click('[data-lp-action="rest"]');
  assert.equal(await page.$eval("#lp-room", el => el.dataset.sleep), "true");
  await page.screenshot({ path: "runs/casino-browser/lumi-pet-nap.png", fullPage: true });
  await page.click('[data-lp-action="rest"]');
  assert.equal(await page.$eval("#lp-room", el => el.dataset.sleep), "false");
  await page.keyboard.press("ArrowUp");
  assert.equal(await page.$eval("#lp-room", el => el.dataset.palette), "lagoon");
  await page.keyboard.press("ArrowDown");
  assert.equal(await page.$eval("#lp-sound", el => el.getAttribute("aria-pressed")), "false");
  await page.reload();
  assert.equal(await page.$eval("#lp-moments", el => el.textContent), "5");
  assert.equal(await page.$eval("#lp-room", el => el.dataset.palette), "lagoon");
  assert.equal(await page.$eval("#lp-sound", el => el.getAttribute("aria-pressed")), "false");
  await page.focus('[data-lp-action="play"]');
  await page.keyboard.press("Tab"); // Native Tab then Enter should nap, even though the selected activity is Cuddle.
  await page.keyboard.press("Enter");
  assert.equal(await page.$eval("#lp-room", el => el.dataset.sleep), "true");
  assert.equal(await page.$eval("#lp-moments", el => el.textContent), "6");
  await page.click('[data-lp-action="cuddle"]');
  await page.screenshot({ path: "runs/casino-browser/lumi-pet.png", fullPage: true });
  await page.setViewport({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Pet must fit a phone");
  await page.screenshot({ path: "runs/casino-browser/lumi-pet-phone.png", fullPage: true });
  await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  await page.click('[data-lp-action="play"]');
  assert.equal(await page.$eval("#lp-pet", el => getComputedStyle(el).animationName), "none");
  await page.emulateMediaFeatures([]);
  await page.setViewport({ width: 1440, height: 1000 });

  const blocked = await browser.newPage();
  try {
    await blocked.setCookie(...await page.cookies());
    await blocked.evaluateOnNewDocument(() => { // A private/locked-down browser must stay playable and explain saving.
      Storage.prototype.setItem = function () { throw new DOMException("Blocked", "SecurityError"); };
    });
    await blocked.goto(base + "/media/games/lumi-pet");
    await blocked.waitForFunction(() => document.getElementById("lp-save").textContent.includes("unavailable"));
    const before = await blocked.$eval("#lp-moments", el => Number(el.textContent));
    await blocked.click('[data-lp-action="cuddle"]');
    assert.equal(await blocked.$eval("#lp-moments", el => Number(el.textContent)), before + 1);
  } finally { await blocked.close(); }

  await page.evaluate(() => localStorage.setItem("mnemerynne-game:lumi-pet:companion", "not-json"));
  await page.reload();
  assert.equal(await page.$eval("#lp-moments", el => el.textContent), "0");
  await page.click('[data-lp-action="cuddle"]');
  assert.equal(await page.$eval("#lp-moments", el => el.textContent), "1");
  console.log("Lumi Pet: activities, nap/wake, direct keys, Tab, local saves, mobile, reduced motion and blocked storage passed");
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
  await frame.waitForSelector("#lp-pet");
  const before = await frame.$eval("#lp-moments", el => Number(el.textContent));
  await page.evaluate(() => {
    const target = document.getElementById("mc-game-frame").contentWindow;
    for (const key of ["ArrowRight", "Enter"]) target.postMessage({ type: "mnemerynne-game-key", key }, location.origin);
  });
  await frame.waitForFunction(count => Number(document.getElementById("lp-moments").textContent) === count + 1, {}, before);
  assert.equal(await frame.$eval("#lp-memory", el => el.textContent), "A shared star biscuit");
  await page.waitForFunction(() => window.petReport.includes("Snack") && window.petReport.includes("happy moments"));
  await frame.focus('[data-lp-action="snack"]');
  await frame.page().keyboard.press("Escape");
  await page.waitForFunction(() => document.getElementById("mc-game").hidden);
  console.log("Lumi Pet: Media card, frame remote controls, status report and Escape passed");
}
