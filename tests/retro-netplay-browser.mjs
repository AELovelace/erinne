// Retro netplay in real browsers (Doll, 2026-11-15): alice and bob, each in a browser of their own, play the NES test
// game together (python/make_retro_test_rom.py: it reads both pads every frame and keeps a running total of them, so
// its state depends on every press of both players at the frame it came).
//  - alice opens a room from 👥, bob finds it in his list and joins, and that starts them both from alice's game (she
//    presses nothing). The emulator's frames are run by retro-netplay.js (rollback).
//  - Each browser's incoming messages are held back LAG_MS, as a connection across town would, so each runs ahead on
//    guesses of the other's presses and puts them right when the real ones come (rollbacks).
//  - Both press away at once for a few seconds. Their states' checksums are the same at every check (every press of
//    both landed at the same frame in both games, wrong guesses and all), nobody had to start again, and both did
//    roll back. A player's own press reaches their own game the next frame (the input delay, 1), not five frames on.
//  - bob leaving lets alice play on alone; bob and carol joining a second apart both get alice's game (the second
//    start waits out the gap instead of being dropped); alice closing the room tells bob, whose game goes on alone.
// Over the media service's websocket, through the page's own CSP.
//   PUPPETEER_MODULE=<puppeteer(-core)'s lib/esm/puppeteer/puppeteer(-core).js> CHROME_PATH=<chrome> node tests/retro-netplay-browser.mjs
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import net from "node:net";

const LAG_MS = 50;   // each way: about three frames of a remote player's presses run on guesses

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
  await page.evaluateOnNewDocument(lag => {   // every checksum this browser sends; incoming messages held back `lag` ms
    window.sentSums = [];
    const real = WebSocket.prototype.send;
    WebSocket.prototype.send = function (d) {
      if (typeof d === "string" && d.includes('"t":"sum"')) window.sentSums.push(JSON.parse(d));
      return real.call(this, d);
    };
    const set = Object.getOwnPropertyDescriptor(WebSocket.prototype, "onmessage").set;
    Object.defineProperty(WebSocket.prototype, "onmessage", { configurable: true, set(fn) {   // in order, all late alike
      set.call(this, e => setTimeout(() => fn(e), lag));
    } });
  }, LAG_MS);
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
  await page.evaluate(() => {   // every press the core gets, with the netplay frame it was set for
    const gm = window.EJS_emulator_for_tests.gameManager, real = gm.functions.simulateInput;
    window.applied = [];
    gm.functions.simulateInput = (p, i, v) => {
      window.applied.push([p, i, v, window.RetroNetplay.state().frame]);
      return real(p, i, v);
    };
    window.RetroNetplay.tune({ checkEvery: 60, trace: true });   // compare checksums every 60 frames, not 300
  });
  return page;
}
const np = page => page.evaluate(() => window.RetroNetplay.state());
async function press(page, key, extra = "") {   // a panel button, found and clicked in one go (the list redraws itself)
  const sel = `[data-key="${key}"]${extra}`;
  await page.waitForSelector(sel, { timeout: 15000 });
  await page.evaluate(s => document.querySelector(s).click(), sel);
}
function mash(page, buttons, ms) {   // press and let go of these buttons (player 1 on this browser's own pad) for a while
  return page.evaluate((buttons, ms) => new Promise(done => {
    const gm = window.EJS_emulator_for_tests.gameManager, end = performance.now() + ms;
    let n = 0;
    (function next() {
      if (performance.now() > end) { buttons.forEach(b => gm.simulateInput(0, b, 0)); done(); return; }
      const b = buttons[Math.floor(n / 2) % buttons.length];   // each in turn: down, then up
      gm.simulateInput(0, b, n % 2 ? 0 : 1);
      n += 1;
      setTimeout(next, 37 + (n * 13) % 50);   // uneven, so presses fall on all sorts of frames
    })();
  }), buttons, ms);
}
const sums = page => page.evaluate(() => Object.fromEntries(window.sentSums.map(s => [s.e + ":" + s.f, s.h])));
let blank = null;   // a plain page to read screenshots' pixels in (the player page's policy refuses data: pictures)
async function centre(page) {   // the colour in the middle of the game's picture, as the screen shows it
  const box = await (await page.$("#rp-game canvas")).boundingBox();
  const png = await page.screenshot({ clip: { x: box.x + box.width / 2 - 2, y: box.y + box.height / 2 - 2, width: 4, height: 4 }, encoding: "base64" });
  return blank.evaluate(src => new Promise(done => {
    const im = new Image();
    im.onload = () => { const c = document.createElement("canvas"); c.width = im.width; c.height = im.height;
      const g = c.getContext("2d"); g.drawImage(im, 0, 0); done(Array.from(g.getImageData(1, 1, 1, 1).data).slice(0, 3)); };
    im.src = "data:image/png;base64," + src;
  }), png);
}
const green = rgb => rgb[1] > rgb[0] + 40;   // the test game: green while any pad holds a button, else pink

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
  await bob.waitForFunction(() => window.RetroNetplay.state().players.length === 2, { timeout: 10000 });   // (his news is late too)
  assert.deepEqual((await np(bob)).players.map(p => p.name), ["alice", "bob"]);
  assert.equal((await np(bob)).slot, 1);

  // bob joining starts them both from alice's game: she needn't press anything (2026-11-15: Start together sat in a
  // closed panel, so a guest's game just ran on alone and nothing synced).
  await bob.waitForFunction(() => window.RetroNetplay.state().started, { timeout: 15000 });
  await alice.waitForFunction(() => window.RetroNetplay.state().started, { timeout: 15000 });
  await sleep(3000);
  const [a1, b1] = [await np(alice), await np(bob)];
  assert.ok(a1.driving && b1.driving && a1.window === 8 && a1.delay === 1, "rollback, with an input delay of 1: " + JSON.stringify(a1));
  assert.ok(a1.frame > 60 && b1.frame > 60, `both games run: alice ${a1.frame}, bob ${b1.frame}`);
  assert.ok(Math.abs(a1.frame - b1.frame) <= 8, `in step: alice ${a1.frame}, bob ${b1.frame}`);

  // A press of alice's own reaches her own game the next frame (delay 1), not five frames on as lockstep had it.
  const own = await alice.evaluate(() => new Promise(done => {
    const gm = window.EJS_emulator_for_tests.gameManager, at = window.RetroNetplay.state().frame;
    window.applied.length = 0;
    gm.simulateInput(0, 3, 1);                    // Start, down
    setTimeout(() => {
      gm.simulateInput(0, 3, 0);
      const first = window.applied.find(c => c[0] === 0 && c[1] === 3 && c[2] === 1);
      done({ at, landed: first ? first[3] : -1 });
    }, 300);
  }));
  assert.ok(own.landed >= own.at && own.landed <= own.at + 2, "her own press in her own game within two frames: " + JSON.stringify(own));

  // The picture: bob holds A, and both screens show it (green); he lets go, and both are pink again. Every frame is
  // drawn while playing together, though it runs with fast-forward on (retro-player.js: fastforward_frameskip).
  blank = await browser.newPage();
  await bob.evaluate(() => window.RetroNetplay.close());   // (his 👥 panel, still open from joining, off the picture)
  await alice.evaluate(() => window.EJS_emulator_for_tests.gameManager.simulateInput(0, 8, 1));   // her own A first
  await sleep(500);
  const hers = await centre(alice);
  await alice.evaluate(() => window.EJS_emulator_for_tests.gameManager.simulateInput(0, 8, 0));
  await sleep(500);
  assert.ok(green(hers), "alice's own A shows in her picture: " + JSON.stringify(hers));
  await bob.evaluate(() => window.EJS_emulator_for_tests.gameManager.simulateInput(0, 8, 1));
  await sleep(500);
  const held = [await centre(alice), await centre(bob)];
  await bob.evaluate(() => window.EJS_emulator_for_tests.gameManager.simulateInput(0, 8, 0));
  await sleep(500);
  const free = [await centre(alice), await centre(bob)];
  assert.ok(held.every(green), "bob's A shows in both pictures: " + JSON.stringify(held));
  assert.ok(!free.some(green), "and goes from both: " + JSON.stringify(free));

  // Both press away at once, with their messages arriving ~3 frames late: guesses, wrong ones, and rollbacks.
  await Promise.all([mash(alice, [8, 0, 4, 7], 4000), mash(bob, [8, 0, 5, 6], 4000)]);
  await sleep(2500);                              // the last checks come in
  const [a2, b2] = [await np(alice), await np(bob)];
  if (process.env.NETPLAY_DEBUG) console.log("after mashing", JSON.stringify([a2, b2].map(s => ({ f: s.frame, rb: s.rollbacks, deep: s.deepest, g: s.guesses, wrong: s.wrong, waits: s.waits, stalls: s.stalls, extra: s.extra, stray: s.stray }))));
  assert.ok(a2.frame > a1.frame + 200, "still playing: " + a2.frame);
  assert.ok(a2.rollbacks > 0 && b2.rollbacks > 0, `both put wrong guesses right: alice ${a2.rollbacks}, bob ${b2.rollbacks}`);
  assert.ok(a2.deepest <= 8 && b2.deepest <= 8, "never further back than the window");
  const [sa, sb] = [await sums(alice), await sums(bob)];
  const both = Object.keys(sa).filter(k => k in sb);
  assert.ok(both.length >= 8, "checksums from both at the same frames: " + both.length);
  if (process.env.NETPLAY_DEBUG) {
    const [ta, tb] = [new Map(await alice.evaluate(() => window.RetroNetplay.trace())), new Map(await bob.evaluate(() => window.RetroNetplay.trace()))];
    let shown = 0;
    for (const [f, pads] of ta) if (tb.has(f) && tb.get(f) !== pads && shown++ < 6) console.log("frame", f, "alice ran", pads, "| bob ran", tb.get(f));
    console.log("trace frames", ta.size, tb.size, "differing", [...ta].filter(([f, p]) => tb.has(f) && tb.get(f) !== p).length);
    for (const k of both) if (sa[k] !== sb[k]) { console.log("first differing check", k); break; }
  }
  both.forEach(k => assert.equal(sa[k], sb[k], "the same game state at epoch:frame " + k));
  assert.equal(a2.starts, 1, "no resync for drifting apart (alice)");
  assert.equal(b2.starts, 1, "no resync for drifting apart (bob)");
  assert.equal(a2.mismatches, 0, "the host never saw a checksum differ");

  // bob leaves: alice plays on alone.
  await bob.evaluate(() => window.RetroNetplay.open());
  await press(bob, "quit");
  await alice.waitForFunction(() => window.RetroNetplay.state().players.length === 1, { timeout: 10000 });
  const a3 = (await np(alice)).frame;
  await sleep(1500);
  assert.ok((await np(alice)).frame > a3 + 10, "alice isn't kept waiting for bob");
  assert.ok(!(await np(bob)).driving, "bob's emulator runs its own frames again");
  const bobAlone = await bob.evaluate(() => window.EJS_emulator_for_tests.gameManager.getFrameNum());
  await sleep(1000);
  assert.ok(await bob.evaluate(() => window.EJS_emulator_for_tests.gameManager.getFrameNum()) > bobAlone + 10, "bob's game goes on alone");

  // bob and carol join a second apart (the room's started: alice restarts everyone from where she is). carol's start
  // comes inside the gap after bob's, so it waits its turn rather than being dropped (which left everyone waiting on
  // her presses forever). Then all three play on, in step.
  const carol = await player(await browser.createBrowserContext(), "carol");
  await carol.click("#rp-together-open");
  await press(bob, `join-${code}`);
  await sleep(300);
  await press(carol, `join-${code}`);
  for (const page of [bob, carol]) await page.waitForFunction(() => window.RetroNetplay.state().started, { timeout: 15000 });
  await alice.waitForFunction(() => window.RetroNetplay.state().players.length === 3 && !window.RetroNetplay.state().gate, { timeout: 15000 });
  await sleep(2500);
  await Promise.all([mash(bob, [8, 0], 1500), mash(carol, [8, 4], 1500)]);
  await sleep(1500);
  const trio = await Promise.all([alice, bob, carol].map(np));
  assert.equal(trio[2].slot, 2, "carol is player 3");
  assert.ok(trio.every(s => s.frame > 100), "all three run: " + trio.map(s => s.frame));
  assert.ok(Math.max(...trio.map(s => s.frame)) - Math.min(...trio.map(s => s.frame)) <= 10, "in step: " + trio.map(s => s.frame));
  const [s3a, s3c] = [await sums(alice), await sums(carol)];
  const epoch = Math.max(...Object.keys(s3c).map(k => Number(k.split(":")[0])));
  const shared = Object.keys(s3c).filter(k => k.startsWith(epoch + ":") && k in s3a);
  assert.ok(shared.length >= 2, "alice's and carol's checksums at the same frames: " + shared.length);
  if (process.env.NETPLAY_DEBUG) {
    const s3b = await sums(bob);
    console.log("trio", JSON.stringify(trio.map(s => ({ f: s.frame, starts: s.starts, why: s.why, rb: s.rollbacks, mm: s.mismatches, extra: s.extra, stalls: s.stalls }))));
    for (const k of Object.keys(s3c).sort()) console.log(k, s3a[k], s3b[k], s3c[k]);
  }
  shared.forEach(k => assert.equal(s3a[k], s3c[k], "alice and carol agree at " + k));
  await carol.evaluate(() => window.RetroNetplay.open());
  await press(carol, "quit");
  await alice.waitForFunction(() => window.RetroNetplay.state().players.length === 2, { timeout: 10000 });

  // alice closes the room: bob's told, and his game goes on alone.
  await alice.evaluate(() => window.RetroNetplay.open());
  await press(alice, "quit");
  await bob.waitForFunction(() => !window.RetroNetplay.state().code, { timeout: 10000 });
  assert.match(await bob.$eval("#rp-status", s => s.textContent), /closed the room|closed/);
  const before = await bob.evaluate(() => window.EJS_emulator_for_tests.gameManager.getFrameNum());
  await sleep(1500);
  assert.ok(await bob.evaluate(() => window.EJS_emulator_for_tests.gameManager.getFrameNum()) > before + 10,
    "bob's game goes on alone");

  assert.deepEqual(errors, [], "no page errors");
  console.log(`ok: room ${code}, a join starts everyone; ${LAG_MS} ms each way: alice and bob at frame ${a2.frame} with ` +
    `${a2.rollbacks} + ${b2.rollbacks} rollbacks (deepest ${Math.max(a2.deepest, b2.deepest)}), the same checksums at ` +
    `${both.length} checks; her own press in ${own.landed - own.at} frame(s); three players in step; leaving and closing fine`);
} catch (err) {
  console.error(err);
  console.error(logs.slice(-2000));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  server.kill();
}
