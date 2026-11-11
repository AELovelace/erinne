// Lumi-Soft in real Chrome (run by connected-browser.mjs; LUMI_SOFT_ONLY=1 runs just this): the title screen, the
// Training Range played with a real mouse (the Star Scope on the right button, pops on the left), pausing and ending
// a run, a coin run that pays, and the Daily Range, Prizes and Settings pages. Screenshots go to runs/casino-browser/.
import assert from "node:assert/strict";

const wait = ms => new Promise(r => setTimeout(r, ms));

export async function checkLumiSoft(page, base) {
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto(base + "/media/games/lumi-soft");
  await page.waitForFunction(() => !document.getElementById("ls-title").hidden && /\d/.test(document.getElementById("ls-coins").textContent));
  const coins = await page.$eval("#ls-coins", e => Number(e.textContent.replace(/\D/g, "")));
  await page.screenshot({ path: "runs/casino-browser/lumi-soft-title.png" });

  // The Training Range: free, no clock. Pop the three cans on the first bench through the scope.
  await page.click('[data-go="training"]');
  await page.waitForFunction(() => window.LumiSoftGame.mode() === "play");
  await wait(1200);                                                           // the cans are up
  await page.screenshot({ path: "runs/casino-browser/lumi-soft-wide.png" });
  for (const x of [560, 700, 840]) {
    const at = await page.evaluate((x) => window.LumiSoftGame.toScreen(x, 1250 - 55), x);
    await page.mouse.move(at.x, at.y);
    await page.mouse.down({ button: "right" });                               // the Star Scope opens with the crosshair here
    await page.waitForFunction(() => window.LumiSoftGame.scoped());
    await wait(120);
    if (x === 560) await page.screenshot({ path: "runs/casino-browser/lumi-soft-scope.png" });
    await page.mouse.down({ button: "left" }); await page.mouse.up({ button: "left" });
    await page.mouse.up({ button: "right" });
    await wait(560);                                                          // the Popper's pump
  }
  const trained = await page.evaluate(() => { const s = window.LumiSoftGame.run().s; return { pops: s.pops, shots: s.shots, score: s.score }; });
  assert.equal(trained.shots, 3, "three pops fired");
  const why = await page.evaluate(() => JSON.stringify(window.LumiSoftGame.run().log.shots));   // where the pops went, if they missed
  assert.ok(trained.pops === 3 && trained.score > 0, "the cans popped: " + JSON.stringify(trained) + " " + why);
  await page.keyboard.press("r");                                             // a reload goes in the log
  await page.keyboard.press("p");
  await page.waitForFunction(() => !document.getElementById("ls-pause").hidden);
  await page.click("#ls-quit");
  await page.waitForFunction(() => window.LumiSoftGame.mode() === "results" && /Practice/.test(document.getElementById("ls-results-earned").textContent));
  assert.equal(await page.$eval("#ls-results-score", e => Number(e.textContent.replace(/\D/g, ""))), trained.score, "the server's replay agrees with the page");

  // A Stardust Garden run costs its entry; pop a few things, end it, and see it pay.
  await page.click("#ls-again");
  await page.waitForFunction(() => !document.getElementById("ls-title").hidden);
  await page.click('[data-go="stages"]');
  await page.waitForFunction(() => !document.getElementById("ls-page").hidden);
  assert.ok(await page.$$eval("#ls-page-body button", b => b[0].disabled === false && b[1].disabled === true), "only the first stage is open");
  await page.screenshot({ path: "runs/casino-browser/lumi-soft-stages.png" });
  await page.click("#ls-page-body button");
  await page.waitForFunction(() => window.LumiSoftGame.mode() === "play");
  await wait(2500);
  await page.screenshot({ path: "runs/casino-browser/lumi-soft-garden.png" });
  for (let i = 0; i < 4; i++) {                                               // aim at whatever is showing
    const at = await page.evaluate(() => {
      const g = window.LumiSoftGame, s = g.run().s, R = window.LumiSoftRules;
      const t = s.targets.map(t => ({ t, p: R.pose(t.def, t.age) })).find(o => o.p.ok && o.t.def.kind === "creature");
      return t ? g.toScreen(t.p.x, t.p.top + t.p.h * 0.5) : null;
    });
    if (!at) { await wait(400); continue; }
    await page.mouse.move(at.x, at.y); await page.mouse.down({ button: "right" }); await wait(80);
    await page.mouse.down({ button: "left" }); await page.mouse.up({ button: "left" }); await page.mouse.up({ button: "right" }); await wait(560);
  }
  await page.screenshot({ path: "runs/casino-browser/lumi-soft-garden-play.png" });
  const played = await page.evaluate(() => window.LumiSoftGame.run().s.score);
  await page.keyboard.press("p"); await page.waitForFunction(() => !document.getElementById("ls-pause").hidden);
  await page.click("#ls-quit");
  await page.waitForFunction(() => window.LumiSoftGame.mode() === "results" && /coins/.test(document.getElementById("ls-results-earned").textContent));
  assert.equal(await page.$eval("#ls-results-score", e => Number(e.textContent.replace(/\D/g, ""))), played, "the garden run counted as played");
  const paid = await page.$eval("#ls-results-earned", e => Number((e.textContent.match(/\+(\d+)/) || [0, 0])[1]));
  await page.screenshot({ path: "runs/casino-browser/lumi-soft-results.png" });
  await page.click("#ls-again");
  await page.waitForFunction(c => Number(document.getElementById("ls-coins").textContent.replace(/\D/g, "")) === c, {}, coins - 25 + paid);

  // The Daily Range, Prizes and Settings pages.
  for (const go of ["daily", "prizes", "settings"]) {
    await page.click(`[data-go="${go}"]`);
    await page.waitForFunction(() => !document.getElementById("ls-page").hidden);
    await page.screenshot({ path: `runs/casino-browser/lumi-soft-${go}.png` });
    if (go === "prizes") assert.equal(await page.$$eval(".ls-prize", p => p.length), 17, "seventeen prizes listed");
    await page.click("#ls-back");
    await page.waitForFunction(() => !document.getElementById("ls-title").hidden);
  }
  console.log("Lumi-Soft: title, Training Range by mouse and scope, pause and end, a paid garden run, Daily Range, Prizes and Settings passed");
}
