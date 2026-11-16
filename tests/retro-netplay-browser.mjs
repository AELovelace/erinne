// Retro netplay in two real browsers (Doll, 2026-11-15): alice and bob, each in a browser of their own, play the
// NES test game together. alice opens a room from 👥, bob finds it in his list and joins, alice starts. Then the
// lockstep: both games run, frame for frame; a press of bob's reaches both games as player 2 at the same frame;
// their states' checksums agree (no resync for drifting); bob leaving lets alice play on alone, and alice closing
// the room tells bob, whose game goes on alone too. Over the media service's websocket, through the page's own CSP.
//   PUPPETEER_MODULE=<puppeteer(-core)'s lib/esm/puppeteer/puppeteer(-core).js> CHROME_PATH=<chrome> node tests/retro-netplay-browser.mjs
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import net from "node:net";

const modulePath = process.env.PUPPETEER_MODULE;
const { default: puppeteer } = await import(modulePath ? pathToFileURL(modulePath).href : "puppeteer");
const port = await new Promise(done => {
  const socket = net.createServer(); socket.listen(0, "127.0.0.1", () => { const p = socket.address().port; socket.close(() => done(p)); });
});
const base = `http://127.0.0.1:${port}`;
const python = resolve(process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const server = spawn(python, ["python/retro_preview.py", String(port)], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
let logs = "", browser;
server.stdout.on("data", b => { logs += b; }); server.stderr.on("data", b => { logs += b; });
const errors = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function player(context, name) {   // a signed-in page for `name`, the NES game started, its presses recorded
  const page = await context.newPage();
  page.on("pageerror", e => errors.push(name + ": " + (e.stack || e.message)));
  await page.evaluateOnNewDocument(() => {   // the emulator, for the test (as retro-browser.mjs does)
    let real;
    Object.defineProperty(window, "EmulatorJS", { configurable: true, get: () => real, set: v => {
      real = class extends v { constructor(...a) { super(...a); window.EJS_emulator_for_tests = this; } };
    } });
  });
  await page.goto(base + "/login");
  await page.evaluate(async n => {
    const r = await fetch("/login", { method: "POST", body: new URLSearchParams({ username: n, password: "preview-password" }) });
    if (!r.ok) throw new Error("Sign-in failed for " + n);
  }, name);
  const games = await page.evaluate(async () => (await fetch("/api/media/retro/games", { cache: "no-store" })).json());
  const nes = games.games.find(g => g.console === "nes");
  await page.goto(`${base}/media/retro/play/${nes.id}`);
  await page.waitForSelector("#rp-fresh");
  await page.click("#rp-fresh");
  await page.waitForFunction(() => !document.getElementById("rp-save").disabled, { timeout: 60000 });
  await page.evaluate(() => {   // every press the core gets, with the netplay frame it came in on
    const gm = window.EJS_emulator_for_tests.gameManager, real = gm.functions.simulateInput;
    window.applied = [];
    gm.functions.simulateInput = (p, i, v) => {
      window.applied.push([p, i, v, window.RetroNetplay.state().frame]);
      return real(p, i, v);
    };
    window.RetroNetplay.tune({ checkEvery: 60 });   // compare checksums every 60 frames, not 300
  });
  return page;
}
const np = page => page.evaluate(() => window.RetroNetplay.state());
async function press(page, key, extra = "") {   // a panel button, found and clicked in one go (the list redraws itself)
  const sel = `[data-key="${key}"]${extra}`;
  await page.waitForSelector(sel, { timeout: 15000 });
  await page.evaluate(s => document.querySelector(s).click(), sel);
}

try {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(base + "/login")).ok) break; } catch (_) { /* starting */ }
    if (server.exitCode !== null) throw new Error(logs);
    if (attempt === 149) throw new Error("The preview didn't start: " + logs);
    await sleep(200);
  }
  browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true,
    args: ["--autoplay-policy=no-user-gesture-required", "--enable-unsafe-swiftshader", "--disable-renderer-backgrounding",
           "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows"] });
  const alice = await player(await browser.createBrowserContext(), "alice");
  const bob = await player(await browser.createBrowserContext(), "bob");

  // alice opens a room; bob finds it in his list and joins.
  await alice.click("#rp-together-open");
  await press(alice, "open");
  await alice.waitForFunction(() => window.RetroNetplay.state().code, { timeout: 10000 });
  const code = (await np(alice)).code;
  await bob.click("#rp-together-open");
  await press(bob, `join-${code}`);
  await alice.waitForFunction(() => window.RetroNetplay.state().players.length === 2, { timeout: 10000 });
  assert.deepEqual((await np(bob)).players.map(p => p.name), ["alice", "bob"]);
  assert.equal((await np(bob)).slot, 1);

  // alice starts: both play from her state, frame for frame.
  await alice.click("#rp-together-open");
  await press(alice, "go", ":not([disabled])");
  await bob.waitForFunction(() => window.RetroNetplay.state().started, { timeout: 15000 });
  await sleep(3000);
  const [a1, b1] = [await np(alice), await np(bob)];
  assert.ok(a1.frame > 20 && b1.frame > 20, `both games run: alice ${a1.frame}, bob ${b1.frame}`);
  assert.ok(Math.abs(a1.frame - b1.frame) <= a1.delay + 2, `in step: alice ${a1.frame}, bob ${b1.frame}`);

  // bob presses A (player 1 on his own pad): both games get it as player 2, at the same frame.
  await bob.evaluate(() => { const gm = window.EJS_emulator_for_tests.gameManager; gm.simulateInput(0, 8, 1); });
  await sleep(600);
  await bob.evaluate(() => { const gm = window.EJS_emulator_for_tests.gameManager; gm.simulateInput(0, 8, 0); });
  await sleep(1500);
  const pressA = await alice.evaluate(() => window.applied.filter(c => c[1] === 8));
  const pressB = await bob.evaluate(() => window.applied.filter(c => c[1] === 8));
  assert.equal(pressA.length, 2, "alice's game got bob's press and release: " + JSON.stringify(pressA));
  assert.deepEqual(pressA, pressB, "the same presses, as player 2, at the same frames in both games");
  assert.equal(pressA[0][0], 1, "as player 2");

  // Their states agree: checksums every 60 frames, and nobody had to start again.
  await sleep(6000);
  const [a2, b2] = [await np(alice), await np(bob)];
  assert.ok(a2.frame > a1.frame + 60, "still playing");
  assert.equal(a2.starts, 1, "no resync for drifting apart (alice)");
  assert.equal(b2.starts, 1, "no resync for drifting apart (bob)");

  // bob leaves: alice plays on alone.
  await bob.evaluate(() => window.RetroNetplay.open());
  await press(bob, "quit");
  await alice.waitForFunction(() => window.RetroNetplay.state().players.length === 1, { timeout: 10000 });
  const a3 = (await np(alice)).frame;
  await sleep(1500);
  assert.ok((await np(alice)).frame > a3 + 10, "alice isn't kept waiting for bob");

  // bob joins again (the room's started: alice restarts everyone from where she is), then alice closes it.
  await press(bob, `join-${code}`);
  await bob.waitForFunction(() => window.RetroNetplay.state().started, { timeout: 15000 });
  await alice.evaluate(() => window.RetroNetplay.open());
  await press(alice, "quit");
  await bob.waitForFunction(() => !window.RetroNetplay.state().code, { timeout: 10000 });
  assert.match(await bob.$eval("#rp-status", s => s.textContent), /closed the room|closed/);
  const before = await bob.evaluate(() => window.EJS_emulator_for_tests.gameManager.getFrameNum());
  await sleep(1500);
  assert.ok(await bob.evaluate(() => window.EJS_emulator_for_tests.gameManager.getFrameNum()) > before + 10,
    "bob's game goes on alone");

  assert.deepEqual(errors, [], "no page errors");
  console.log(`ok: room ${code}, alice and bob in lockstep (frame ${a2.frame}), bob's press as player 2 in both, ` +
    "checksums agreed, leaving and closing both fine");
} catch (err) {
  console.error(err);
  console.error(logs.slice(-2000));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  server.kill();
}
