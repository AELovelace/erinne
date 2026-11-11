// Included by connected-browser.mjs (ARCADE_ONLY=1 runs just this): Lumi in the arcade games, in a real browser
// against the disposable preview app. Each game is opened, played a little with the keyboard, and photographed under
// runs/casino-browser/ for a person to look at; the page must throw nothing and must draw this player's own Lumi.
import assert from "node:assert/strict";

const sleep = ms => new Promise(done => setTimeout(done, ms));

export async function checkArcadeLumi(browser, page, base) {
  // alice's Lumi wears something and glows aqua, so the pictures show her own look, not a plain one.
  await page.goto(base + "/media/games/lumi-pet");
  await page.waitForSelector('[data-lp-row="cuddle"]');
  const look = await page.evaluate(async () => {
    const post = body => fetch("/api/media/games/lumi-pet/act", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await post({ action: "glow", glow: "lagoon" });
    await post({ action: "wear", item: "acc-flower-crown" });  // bought and worn (the preview's coins are pretend)
    return (await fetch("/api/media/games/lumi-look")).json();
  });
  assert.equal(look.glow, "lagoon", "the arcade asks for this player's own Lumi");
  assert.equal(look.outfit.head, "acc-flower-crown", "and knows what she's wearing");

  const errors = [];
  const tab = await browser.newPage();
  tab.on("pageerror", e => errors.push(String(e)));
  await tab.setCookie(...await page.cookies());
  await tab.setViewport({ width: 1600, height: 900 });
  try {
    for (const [slug, keys, name] of [["snake", ["ArrowDown"], "LUMI CONGA"], ["breakout", ["Enter"], "YARN POP"], ["teacup-tower", ["ArrowLeft"], "TEACUP TOWER"]]) {
      await tab.goto(base + "/media/games/" + slug);
      await tab.waitForSelector("#ar-solo");
      assert.match(await tab.$eval(".ar-name", el => el.textContent.replace(/\s+/g, " ").trim()), new RegExp(name.split(" ")[0]));
      await sleep(600);                                        // her picture loads once
      await tab.screenshot({ path: "runs/casino-browser/arcade-" + slug + "-menu.png" });
      await tab.click("#ar-solo");
      await tab.waitForFunction(() => document.getElementById("ar-panel").hidden);
      for (const key of keys) { await tab.keyboard.press(key); await sleep(400); }
      if (slug === "breakout") { await tab.keyboard.down("ArrowLeft"); await sleep(500); await tab.keyboard.up("ArrowLeft"); }
      await sleep(slug === "snake" ? 200 : 1500);              // the conga heads for a wall: catch it while it dances
      await tab.screenshot({ path: "runs/casino-browser/arcade-" + slug + "-play.png" });
    }
    await tab.goto(base + "/media/games/prism-drop");
    await tab.waitForSelector("#pd-drop-button:not([disabled])");
    await sleep(800);
    await tab.screenshot({ path: "runs/casino-browser/arcade-prism-hold.png" });   // Lumi over the pick, holding the ball
    await tab.keyboard.press("ArrowRight");
    await tab.keyboard.press("Enter");
    await sleep(1400);
    await tab.screenshot({ path: "runs/casino-browser/arcade-prism-dart.png" });   // the ball falling, Lumi darting about
    await tab.waitForSelector("#pd-drop-button:not([disabled])", { timeout: 20000 });
    await sleep(700);
    await tab.screenshot({ path: "runs/casino-browser/arcade-prism-fetch.png" });  // Lumi on her way down, or back up
    assert.deepEqual(errors, []);
  } finally {
    await tab.close();
  }
  console.log("Lumi's arcade: Lumi Conga, Yarn Pop, Teacup Tower and Prism Drop draw her own Lumi and throw nothing (pictures in runs/casino-browser/arcade-*.png)");
}
