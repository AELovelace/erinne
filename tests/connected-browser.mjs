// Real-browser multiplayer checks. PUPPETEER_MODULE and CHROME_PATH may point to existing local installations.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import net from "node:net";
import { checkLumiApps, checkLumiPet, checkLumiPetCapsules, checkLumiPetDoors, checkLumiPetFrame, checkLumiPetGarden, checkLumiPetPhone, checkLumiPetSkills } from "./lumi-pet-browser.mjs";
import { checkSharedTV } from "./casino-tv-browser.mjs";
import { checkLumiPartyCouch, checkLumiPartyOnline, checkLumiPartyPuzzle } from "./lumi-party-browser.mjs";
import { checkGameScaling } from "./game-scaling-browser.mjs";

const modulePath = process.env.PUPPETEER_MODULE;
const { default: puppeteer } = await import(modulePath ? pathToFileURL(modulePath).href : "puppeteer");
const port = await new Promise(done => { // Reserve a free loopback port for the disposable preview app.
  const socket = net.createServer(); socket.listen(0, "127.0.0.1", () => { const p = socket.address().port; socket.close(() => done(p)); });
});
const base = `http://127.0.0.1:${port}`;
const python = resolve(process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const server = spawn(python, ["python/casino_preview.py", String(port)], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
let logs = "", browser;
server.stdout.on("data", b => { logs += b; }); server.stderr.on("data", b => { logs += b; });
const errors = [];
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    try { if ((await fetch(base + "/login")).ok) break; } catch (_) { /* Wait for app startup. */ }
    if (server.exitCode !== null) throw new Error(logs);
    if (attempt === 99) throw new Error("Preview did not start: " + logs);
    await new Promise(r => setTimeout(r, 200));
  }
  browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true,
    args: ["--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding"] });
  const aliceContext = await browser.createBrowserContext(), bobContext = await browser.createBrowserContext();
  const alice = await aliceContext.newPage(), bob = await bobContext.newPage();
  for (const [page, name] of [[alice, "alice"], [bob, "bob"]]) {
    page.on("pageerror", e => errors.push(e.stack || e.message)); // Preserve the source line for asynchronous navigation failures.
    await page.setViewport({ width: 1440, height: 1000 });
    await page.goto(base + "/login");
    await page.evaluate(async username => { // Use the actual session login, not a mocked game endpoint.
      const result = await fetch("/login", { method: "POST", body: new URLSearchParams({ username, password: "preview-password" }) });
      if (!result.ok) throw new Error("Login failed");
    }, name);
    await page.evaluateOnNewDocument(() => { Object.defineProperty(document, "hidden", { get: () => false }); }); // Both test tabs act like visible TV screens.
  }
  await mkdir("runs/casino-browser", { recursive: true });
  if (process.env.LUMI_PARTY_ONLY === "1") { // Focused iteration on Lumi Dungeons parties and Who's playing?
    await checkLumiPartyOnline(alice, bob, base);
    await checkLumiPartyPuzzle(browser, alice, base);
    await checkLumiPartyCouch(browser, alice, bob, base);
  } else if (process.env.GAME_SCALING_ONLY === "1") {
    await checkGameScaling(alice, bob, base);
  } else if (process.env.CASINO_TV_ONLY === "1") { // Focused iteration for controller routing without replaying unrelated casino rounds.
    const screen = await alice.evaluate(async () => (await fetch("/api/media/screens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Shared TV test" }) })).json());
    await alice.goto(base + "/media/screen#games");
    await alice.waitForFunction(async () => (await (await fetch("/api/media/screens")).json()).screens.some(s => s.online));
    await alice.waitForFunction(() => document.querySelectorAll(".mc-card").length === 11);
    await alice.evaluate(() => [...document.querySelectorAll(".mc-card")].find(b => b.querySelector(".mc-label").textContent === "Slot Machines").click());
    await checkSharedTV(alice, screen, base);
  } else {
  await checkLumiPet(browser, alice, base);
  await checkLumiPetDoors(browser, alice, base);
  await checkLumiPetGarden(browser, alice, base);
  await checkLumiPetSkills(browser, alice, base);
  await checkLumiPetPhone(browser, alice, base);
  await checkLumiApps(browser, alice, base);
  await checkLumiPetCapsules(bob, base);
  await checkLumiPartyOnline(alice, bob, base);
  await checkLumiPartyPuzzle(browser, alice, base);
  await checkLumiPartyCouch(browser, alice, bob, base);
  await alice.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]); // Skip the ball's animation: the server's drops are what's under test.
  await alice.goto(base + "/media/games/prism-drop");
  await alice.waitForFunction(() => /^[\d,]+$/.test(document.getElementById("pd-coins").textContent));
  const prismCoins = async () => Number((await alice.$eval("#pd-coins", e => e.textContent)).replaceAll(",", ""));
  const dropOnce = async n => { // Each drop comes from the server; wait for it to be scored before pressing again.
    await alice.waitForSelector("#pd-drop-button:not([disabled])");
    await alice.click("#pd-drop-button");
    await alice.waitForFunction(count => document.getElementById("pd-drop").textContent === (count >= 10 ? "Done" : (count + 1) + " / 10"), {}, n);
  };
  for (let n = 1; n <= 3; n++) await dropOnce(n);
  const scoreBefore = await alice.$eval("#pd-score", e => e.textContent);
  await alice.reload();
  await alice.waitForFunction(() => document.getElementById("pd-drop").textContent === "4 / 10"); // An unfinished game carries on after a reload.
  assert.equal(await alice.$eval("#pd-score", e => e.textContent), scoreBefore);
  const coinsBefore = await prismCoins();
  for (let n = 4; n <= 10; n++) await dropOnce(n);
  const finalScore = Number(await alice.$eval("#pd-score", e => e.textContent));
  assert.equal(await prismCoins(), coinsBefore + Math.floor(finalScore / 10), "A finished game pays its score in coins");
  await alice.emulateMediaFeatures([]);
  console.log("prism-drop: server drops, resume after reload, coins paid passed");
  for (const game of ["blackjack", "roulette", "horse-racing", "slots"]) {
    await alice.goto(base + "/media/games/" + game);
    await alice.waitForSelector("#casino-create:not([disabled])");
    await alice.click("#casino-create");
    await alice.waitForFunction(() => !document.getElementById("casino-table").hidden);
    const code = await alice.evaluate(() => sessionStorage.getItem("casino-room:" + document.getElementById("gm").dataset.game));
    await bob.goto(base + "/media/games/" + game);
    await bob.waitForSelector('[data-room="' + code + '"]');
    await bob.click('[data-room="' + code + '"]');
    await bob.waitForFunction(() => document.querySelectorAll(".casino-player").length === 2);
    await alice.waitForFunction(() => document.querySelectorAll(".casino-player").length === 2);
    if (game === "roulette") {
      await alice.keyboard.press("p");
      await alice.waitForFunction(() => document.getElementById("casino-error").textContent.includes("Set a bet"));
      await alice.evaluate(() => { for (let i = 0; i < 60; i++) window.MGame.tap("up"); });
      assert.equal(await alice.$eval("#casino-amount", e => e.textContent), "500");
      await alice.evaluate(() => { for (let i = 0; i < 60; i++) window.MGame.tap("down"); });
      assert.equal(await alice.$eval("#casino-amount", e => e.textContent), "10");
      await alice.evaluate(() => { for (let i = 0; i < 4; i++) window.MGame.tap("up"); });
    }
    await alice.evaluate(() => document.getElementById("casino-leave").focus()); // Stale focus must never turn Enter into Leave.
    await alice.keyboard.press("ArrowUp");
    assert.equal(await alice.$eval("#casino-amount", e => e.textContent), "60");
    await alice.keyboard.press("ArrowDown");
    assert.equal(await alice.$eval("#casino-amount", e => e.textContent), "50");
    if (game === "slots") {
      assert.equal(await alice.$eval("#casino-cost", e => e.textContent), "3 lines × 50 = 150 coins total");
      assert.equal(await alice.$$eval("#casino-stage .casino-slot-line", rows => rows.length), 3);
      assert.equal(await alice.$$eval("#casino-stage .casino-slot-symbol", cells => cells.length), 9);
    }
    if (["roulette", "horse-racing"].includes(game)) {
      const pick = await alice.$eval("#casino-pick", e => e.textContent);
      await alice.keyboard.press("ArrowRight");
      assert.notEqual(await alice.$eval("#casino-pick", e => e.textContent), pick);
      await alice.keyboard.press("ArrowLeft");
      assert.equal(await alice.$eval("#casino-pick", e => e.textContent), pick);
    }
    await alice.keyboard.press("Enter");
    await alice.waitForFunction(() => document.getElementById("casino-bet").textContent === "Update bet");
    if (game === "slots") assert.match(await alice.$eval("#casino-balance", e => e.textContent), /^[\d,]+ coins$/); // An account purse carries over from the earlier tables.
    await bob.waitForFunction(() => document.querySelector(".casino-player").textContent.includes("Bet 50"));
    await bob.click("#casino-bet");
    await bob.waitForFunction(() => document.getElementById("casino-bet").textContent === "Update bet");
    await alice.waitForFunction(() => [...document.querySelectorAll(".casino-player")].every(p => p.textContent.includes("Bet 50")));
    await bob.waitForSelector("#casino-bet:not([disabled])");
    await bob.keyboard.press("p");
    await bob.waitForFunction(() => document.getElementById("casino-error").textContent.includes("host"));
    assert.equal(await bob.$eval("#casino-phase", e => e.textContent), "Place your bets");
    await alice.waitForSelector("#casino-start:not([disabled])");
    await alice.keyboard.press("MediaPlayPause");
    await alice.waitForFunction(() => document.getElementById("casino-phase").textContent !== "Place your bets");
    if (game === "roulette") {
      assert.equal(await alice.$$eval(".roulette-number", labels => labels.length), 37);
      await alice.evaluate(() => { window.testRotor = document.querySelector(".roulette-rotor"); window.testAngle = window.testRotor.getAttribute("transform"); });
      await alice.waitForFunction(() => document.querySelector(".roulette-rotor").getAttribute("transform") !== window.testAngle);
      await alice.screenshot({ path: "runs/casino-browser/roulette-spinning.png", fullPage: true });
    }
    if (game === "slots") {
      assert.equal(await alice.$$eval("#casino-stage .rolling .casino-slot-symbol", cells => cells.length), 9);
      await alice.screenshot({ path: "runs/casino-browser/slots-spinning.png", fullPage: true });
    }
    if (game === "blackjack") {
      for (const page of [alice, bob]) {
        await page.waitForFunction(() => document.getElementById("casino-phase").textContent !== "Place your bets");
        if (await page.$eval("#casino-hand", e => !e.hidden)) { await page.keyboard.press("ArrowRight"); await page.keyboard.press("Enter"); }
        await page.waitForFunction(() => document.getElementById("casino-hand").hidden);
      }
    }
    await alice.waitForFunction(() => document.getElementById("casino-phase").textContent === "Round complete", { timeout: 15000 });
    await bob.waitForFunction(() => document.getElementById("casino-phase").textContent === "Round complete", { timeout: 15000 });
    if (game === "roulette") {
      await alice.waitForSelector(".roulette-wheel[data-landed]");
      assert.ok(await alice.evaluate(() => window.testRotor === document.querySelector(".roulette-rotor")), "Polls must preserve the animated wheel");
      const landed = await alice.$eval(".roulette-wheel", e => e.dataset.landed);
      assert.equal(await alice.$eval(".roulette-pocket.winning", e => e.dataset.number), landed);
      assert.ok((await alice.$eval(".roulette-caption", e => e.textContent)).startsWith(landed + " · "));
    }
    if (game === "slots") {
      const result = await alice.evaluate(async table => (await fetch("/api/media/games/slots/rooms/" + table)).json(), code);
      for (const seat of result.players) {
        assert.equal(seat.reels.length, 3);
        assert.ok(seat.reels.every(row => row.length === 3));
        assert.equal(seat.payout, seat.line_payouts.reduce((sum, value) => sum + value, 0));
      }
      const own = result.players.find(p => p.id === result.you);
      assert.deepEqual(await alice.$$eval("#casino-stage .casino-slot-symbol", cells => cells.map(c => c.getAttribute("aria-label"))), own.reels.flat());
      assert.deepEqual(await alice.$$eval("#casino-stage .casino-slot-line", rows => rows.map(r => Number(r.dataset.payout))), own.line_payouts);
      assert.equal(await alice.$$eval("#casino-stage .casino-slot-line.paid", rows => rows.length), own.line_payouts.filter(value => value > 0).length);
      assert.ok((await alice.$eval("#casino-hint", e => e.textContent)).includes("Net " + (own.payout - own.bet * 3)));
    }
    assert.equal(await alice.$eval("#casino-players", e => e.textContent.replaceAll(" · You", "")), await bob.$eval("#casino-players", e => e.textContent.replaceAll(" · You", "")));
    await alice.screenshot({ path: `runs/casino-browser/${game}.png`, fullPage: true });
    await alice.reload();
    await alice.waitForFunction(() => !document.getElementById("casino-table").hidden);
    assert.equal(await alice.evaluate(() => sessionStorage.getItem("casino-room:" + document.getElementById("gm").dataset.game)), code);
    await alice.setViewport({ width: 390, height: 844 });
    assert.ok(await alice.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Mobile board should not overflow horizontally");
    await alice.screenshot({ path: `runs/casino-browser/${game}-phone.png`, fullPage: true });
    await alice.setViewport({ width: 1440, height: 1000 });
    await alice.click("#casino-leave");
    await alice.waitForFunction(() => !document.getElementById("casino-lobby").hidden);
    await bob.waitForFunction(() => document.querySelectorAll(".casino-player").length === 1);
    assert.ok(await bob.$eval(".casino-player", e => e.textContent.includes("Host")));
    await bob.click("#casino-leave");
    await bob.waitForFunction(() => !document.getElementById("casino-lobby").hidden);
    console.log(`${game}: two accounts, shared settlement, refresh, mobile layout, remote input, host handoff passed`);
  }
  await alice.goto(base + "/media#games");
  // A shelf per kind of game: Lumi's three apps, the arcade games a remote can play, the four tables. A computer also shows Mouse & keyboard.
  await alice.waitForFunction(() => document.querySelectorAll(".mc-shelf .mc-grid").length >= 3);
  assert.deepEqual(await alice.$$eval(".mc-shelf", shelves => shelves.map(shelf => [shelf.querySelector("h2, h3, .mc-shelf-title")?.textContent || "", shelf.querySelector(".mc-grid").children.length])),
    [["Lumi", 3], ["Arcade", 4], ["Table games", 4], ["Mouse & keyboard", 1]]);
  await checkLumiPetFrame(alice);
  await alice.evaluate(() => { // The top bar keeps controls; the remote consumes the iframe's status message.
    window.casinoTestReport = "";
    window.addEventListener("message", e => {
      if (e.origin === location.origin && e.source === document.getElementById("mc-game-frame").contentWindow && e.data.type === "mnemerynne-game-state") window.casinoTestReport = e.data.sub;
    });
  });
  await alice.evaluate(() => [...document.querySelectorAll(".mc-card")].find(b => b.querySelector(".mc-label").textContent === "Roulette").click());
  const frameElement = await alice.waitForSelector("#mc-game-frame");
  const frame = await frameElement.contentFrame();
  await frame.waitForSelector("#casino-create");
  await alice.evaluate(() => document.getElementById("mc-game-frame").contentWindow.postMessage({ type: "mnemerynne-game-key", key: "Enter" }, location.origin));
  await frame.waitForFunction(() => !document.getElementById("casino-table").hidden);
  await alice.waitForFunction(() => window.casinoTestReport.includes("coins"));
  await frame.focus("#casino-leave");
  await alice.evaluate(() => document.getElementById("mc-game-frame").contentWindow.postMessage({ type: "mnemerynne-game-key", key: "ArrowUp" }, location.origin));
  await frame.waitForFunction(() => document.getElementById("casino-amount").textContent === "60");
  await alice.evaluate(() => document.getElementById("mc-game-frame").contentWindow.postMessage({ type: "mnemerynne-game-key", key: "Enter" }, location.origin));
  await frame.waitForFunction(() => document.getElementById("casino-bet").textContent === "Update bet");
  await frame.waitForSelector("#casino-start:not([disabled])");
  await alice.focus("#mc-game-back");
  await alice.keyboard.press("p"); // Play must route into the iframe even with the parent bar focused.
  await frame.waitForFunction(() => document.getElementById("casino-phase").textContent === "Round in progress");
  await alice.click("#mc-game-back");
  await alice.waitForFunction(() => document.getElementById("mc-game").hidden);
  console.log("Media Games cards, embedded game, direct betting, parent Play input and status reporting passed");

  const screen = await alice.evaluate(async () => { // Exercise the actual remote command/SSE route, not just synthetic key events.
    const response = await fetch("/api/media/screens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Casino test TV" }) });
    if (!response.ok) throw new Error("Could not pair the test TV"); return response.json();
  });
  await alice.goto(base + "/media/screen#games");
  await alice.waitForFunction(async () => (await (await fetch("/api/media/screens")).json()).screens.some(s => s.online));
  await alice.waitForFunction(() => document.querySelectorAll(".mc-card").length === 11);
  await alice.evaluate(() => [...document.querySelectorAll(".mc-card")].find(b => b.querySelector(".mc-label").textContent === "Slot Machines").click());
  const tvFrame = await (await alice.$("#mc-game-frame")).contentFrame();
  await tvFrame.waitForSelector("#casino-create:not([disabled])");
  await tvFrame.click("#casino-create");
  await tvFrame.waitForSelector("#casino-bet:not([disabled])");
  async function command(body) { // Send the same press/toggle actions used by the existing remote panel.
    await alice.evaluate(async (id, payload) => {
      const result = await fetch("/api/media/screens/" + id + "/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!result.ok) throw new Error("Remote command failed: " + result.status);
    }, screen.id, body);
  }
  await command({ action: "press", button: "up" });
  await tvFrame.waitForFunction(() => document.getElementById("casino-amount").textContent === "60");
  await command({ action: "press", button: "enter" });
  await tvFrame.waitForFunction(() => document.getElementById("casino-bet").textContent === "Update bet");
  await tvFrame.waitForSelector("#casino-start:not([disabled])");
  await command({ action: "toggle" });
  await tvFrame.waitForFunction(() => document.getElementById("casino-phase").textContent === "Round in progress");
  console.log("Paired TV: real remote arrows, Enter and Play/Pause reach the game through SSE");

  // Lumi Pet takes the remote's Previous and Next itself (they change its category); other games leave them to the music.
  await alice.goto(base + "/media/screen#games");
  await alice.waitForFunction(() => document.querySelectorAll(".mc-card").length === 11);
  await alice.evaluate(() => [...document.querySelectorAll(".mc-card")].find(b => b.querySelector(".mc-label").textContent === "Lumi Pet").click());
  const petFrame = await (await alice.waitForSelector("#mc-game-frame")).contentFrame();
  await petFrame.waitForSelector('[data-lp-row="cuddle"]');
  const category = () => petFrame.$eval('[data-lp-tab][aria-selected="true"]', el => el.dataset.lpTab);
  await command({ action: "next" });
  await petFrame.waitForFunction(() => document.querySelector('[data-lp-tab="pantry"]').getAttribute("aria-selected") === "true");
  await command({ action: "next" });
  await petFrame.waitForFunction(() => document.querySelector('[data-lp-tab="toys"]').getAttribute("aria-selected") === "true");
  await command({ action: "prev" });
  await petFrame.waitForFunction(() => document.querySelector('[data-lp-tab="pantry"]').getAttribute("aria-selected") === "true");
  await command({ action: "press", button: "right" }); // Left and right no longer change category.
  await command({ action: "press", button: "down" });
  await petFrame.waitForFunction(() => document.querySelectorAll('#lp-list [aria-pressed="true"]').length === 1);
  assert.equal(await category(), "pantry");
  console.log("Paired TV: a remote's Previous and Next change Lumi Pet's category through SSE");
  await checkSharedTV(alice, screen, base);

  await bob.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  await bob.goto(base + "/media/games/roulette");
  await bob.waitForSelector("#casino-create:not([disabled])");
  await bob.click("#casino-create");
  await bob.waitForSelector("#casino-bet:not([disabled])");
  await bob.click("#casino-bet");
  await bob.waitForSelector("#casino-start:not([disabled])");
  await bob.keyboard.press("p");
  await bob.waitForFunction(() => document.getElementById("casino-phase").textContent === "Round in progress");
  assert.ok(await bob.evaluate(() => window.MGame.reducedMotion));
  assert.equal(await bob.$eval(".roulette-rotor", e => e.getAttribute("transform")), "rotate(0)");
  await bob.waitForSelector(".roulette-wheel[data-landed]", { timeout: 15000 });
  const reducedResult = await bob.$eval(".roulette-wheel", e => e.dataset.landed);
  assert.equal(await bob.$eval(".roulette-pocket.winning", e => e.dataset.number), reducedResult);
  console.log("Roulette reduced-motion mode skips spinning and displays the correct server result");
  }
  assert.deepEqual(errors, []);
} catch (failure) {
  if (errors.length) console.error("Page errors before the failure:", errors.join(" || ")); // A script error is often the real cause.
  if (process.env.SHOW_SERVER_LOG === "1") console.error("Server log:", logs.slice(-6000));
  if (browser) { // Capture the visible board and error copy when a UI check fails; never print cookies or panel keys.
    for (const page of await browser.pages()) for (const frame of page.frames()) {
      if (frame.url().includes("/media/games/")) {
        console.error("Game at failure:", frame.url(), await frame.evaluate(() => document.body.innerText).catch(() => "Frame closed"));
        await page.screenshot({ path: "runs/casino-browser/failure.png", fullPage: true }).catch(() => {});
      }
    }
  }
  throw failure;
} finally {
  if (browser) await browser.close();
  server.kill();
}
