// Media → Retro in a real browser (python/retro_preview.py serves a homebrew game per console on a disposable share).
//   PUPPETEER_MODULE=<puppeteer's lib/esm/puppeteer/puppeteer.js> node tests/retro-browser.mjs
// Each game (python/make_retro_test_rom.py) paints the screen pink and, but for the PS1's, adds one to its battery
// save on every boot. So, for the GBA in full: the emulator really ran (the screenshot's colour), a save state and its
// screenshot went into a slot, leaving kept the game's own save and an automatic state, Continue started from it,
// and the next boot read the save back from the server (the byte went up by one). Then, for every console: its game
// boots pink, and a second boot in a fresh browser (nothing kept locally) counts on from the server's save.
// Nothing may be asked of any other host.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import net from "node:net";

const modulePath = process.env.PUPPETEER_MODULE;
const { default: puppeteer } = await import(modulePath ? pathToFileURL(modulePath).href : "puppeteer");
const port = await new Promise(done => { // a free loopback port for the disposable preview app
  const socket = net.createServer(); socket.listen(0, "127.0.0.1", () => { const p = socket.address().port; socket.close(() => done(p)); });
});
const base = `http://127.0.0.1:${port}`;
const python = resolve(process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const server = spawn(python, ["python/retro_preview.py", String(port)], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
let logs = "", browser;
server.stdout.on("data", b => { logs += b; }); server.stderr.on("data", b => { logs += b; });
const errors = [], elsewhere = [], consoleLines = [];
// Where each console's homebrew game keeps its boot count in its save file (make_retro_test_rom.py GAMES); the PS1's
// keeps none. Genesis Plus GX's save starts at $200000, so the odd byte at $200001 is the second.
const SAVE_BYTE = { nes: 0, snes: 0, genesis: 1, gbc: 0, gba: 0, psx: null };
const SHELVES = ["NES", "Super Nintendo", "Genesis", "Game Boy Color", "Game Boy Advance", "PlayStation"];

async function newPage(context) { // a signed-in page in a browser context, watched for errors and outside requests
  const page = await context.newPage();
  page.on("pageerror", e => errors.push(e.stack || e.message));
  page.on("console", m => consoleLines.push(m.type() + ": " + m.text()));   // shown if a step fails
  page.on("request", r => { if (!r.url().startsWith(base) && !/^(data|blob):/.test(r.url())) elsewhere.push(r.url()); });
  // The player keeps its emulator to itself; this hands the test a reference, as the page's own code sees it.
  await page.evaluateOnNewDocument(() => {
    let real;
    Object.defineProperty(window, "EmulatorJS", { configurable: true, get: () => real, set: v => {
      real = class extends v { constructor(...a) { super(...a); window.EJS_emulator_for_tests = this; } };
    } });
  });
  await page.setViewport({ width: 1280, height: 860 });
  await page.goto(base + "/login");
  await page.evaluate(async () => { // the real session sign-in
    const r = await fetch("/login", { method: "POST", body: new URLSearchParams({ username: "alice", password: "preview-password" }) });
    if (!r.ok) throw new Error("Sign-in failed");
  });
  return page;
}

async function api(page, path) { // a JSON answer, asked from inside the signed-in page
  return page.evaluate(async p => (await fetch(p, { cache: "no-store" })).json(), path);
}

async function saveByte(page, id, at = 0) { // a byte of the game's own save on the server, or null
  return page.evaluate(async (i, n) => {
    const r = await fetch(`/api/media/retro/saves/${i}/sram`, { cache: "no-store" });
    return r.ok ? new Uint8Array(await r.arrayBuffer())[n] : null;
  }, id, at);
}

async function started(page) { // the game is running: the bar's Save button comes on at the emulator's start
  await page.waitForFunction(() => !document.getElementById("rp-save").disabled, { timeout: 60000 });
  await new Promise(r => setTimeout(r, 2500)); // a few frames: the program has painted and counted the boot
}

async function screenColour(page) { // the centre pixel of RetroArch's own screenshot (the emulated frame itself)
  return page.evaluate(async () => {
    const png = await window.EJS_emulator_for_tests.gameManager.screenshot();
    const bitmap = await createImageBitmap(new Blob([png], { type: "image/png" }));
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(bitmap, 0, 0);
    return Array.from(ctx.getImageData(bitmap.width >> 1, bitmap.height >> 1, 1, 1).data.slice(0, 3));
  });
}

function pink([r, g, b]) { // every console's pink, after its own colour handling (the GBC's is softened)
  return r > 180 && b > 180 && r - g > 60;
}

async function playFresh(page, id) { // open a game, press Start, wait for it to run
  await page.goto(`${base}/media/retro/play/${id}`);
  await page.waitForSelector("#rp-fresh");
  await page.click("#rp-fresh");
  await started(page);
}

async function leave(page) { // ← Retro: the saves go to the server, then the shelf
  await Promise.all([page.waitForNavigation({ timeout: 30000 }), page.click("#rp-back")]);
  assert.equal(new URL(page.url()).pathname, "/media/retro");
}

try {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(base + "/login")).ok) break; } catch (_) { /* the app is still starting */ }
    if (server.exitCode !== null) throw new Error(logs);
    if (attempt === 149) throw new Error("The preview didn't start: " + logs);
    await new Promise(r => setTimeout(r, 200));
  }
  browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true,
    args: ["--autoplay-policy=no-user-gesture-required", "--enable-unsafe-swiftshader"] });
  const page = await newPage(browser.defaultBrowserContext());

  // Media's menu has Retro; a shelf per console, each with its game.
  await page.goto(base + "/media");
  assert.ok(await page.$('a.mc-menu-item[href="/media/retro"]'), "Media's menu links to Retro");
  await page.goto(base + "/media/retro");
  await page.waitForSelector(".rt-game");
  assert.deepEqual(await page.$$eval(".rt-shelf-title", hs => hs.map(h => h.firstChild.textContent)), SHELVES);
  const ids = Object.fromEntries((await api(page, "/api/media/retro/games")).games.map(g => [g.console, g.id]));
  const id = ids.gba;

  // ---- The GBA in full. First visit: no Continue, just Start. The game runs and paints the screen pink.
  await page.click('.rt-game[data-console="gba"]');
  await page.waitForSelector("#rp-start:not([hidden])");
  assert.equal(await page.$eval("#rp-continue", b => b.hidden), true, "nothing to continue yet");
  await page.click("#rp-fresh");
  await started(page);
  assert.ok(pink(await screenColour(page)), "the GBA's screen is pink");

  // A save state into slot 1, with its screenshot.
  await page.click("#rp-save");
  await page.waitForSelector("#rp-slots:not([hidden])");
  await page.click("#rp-slot-list li:nth-child(1) button");
  await page.waitForFunction(() => document.getElementById("rp-status").textContent === "Saved to Slot 1", { timeout: 20000 });
  let saves = (await api(page, `/api/media/retro/saves/${id}`)).saves;
  const one = saves.find(s => s.slot === "1");
  assert.ok(one && one.size > 1000 && one.has_shot, "slot 1 holds a state and its screenshot: " + JSON.stringify(saves));

  // Leaving keeps the game's own save and an automatic state, then goes back to the shelf.
  await leave(page);
  saves = (await api(page, `/api/media/retro/saves/${id}`)).saves;
  assert.deepEqual(saves.map(s => s.slot).sort(), ["1", "auto", "sram"]);
  assert.notEqual(await saveByte(page, id), null, "the game's own save reached the server");
  await page.waitForSelector('.rt-game[data-console="gba"] .rt-badge');
  assert.equal(await page.$eval('.rt-game[data-console="gba"] .rt-badge', b => b.textContent), "▶ Continue");
  assert.equal(await page.$eval(".rt-shelf-title", h => h.firstChild.textContent), "Continue playing");

  // Second visit: Continue starts from the automatic state. (mGBA's states hold the battery save too, so loading
  // one puts the save back as it was then, as in RetroArch: the boot's count is undone.)
  await page.click('.rt-game[data-console="gba"]');
  await page.waitForSelector("#rp-continue:not([hidden])");
  await page.click("#rp-continue");
  await started(page);
  await page.waitForFunction(() => /^Continued from /.test(document.getElementById("rp-status").textContent), { timeout: 10000 });
  await leave(page);

  // Third visit, started over: the boot reads the game's own save back from the server and counts on from it.
  const before = await saveByte(page, id);
  await playFresh(page, id);
  await leave(page);
  assert.equal(await saveByte(page, id), (before + 1) & 0xff, "the boot counted on from the server's save");

  // A slot loads; the automatic state is listed first.
  await playFresh(page, id);
  await page.click("#rp-load");
  await page.waitForSelector("#rp-slots:not([hidden])");
  const loadable = await page.$$eval("#rp-slot-list li", rows => rows.map(r => r.querySelector("strong").textContent));
  assert.deepEqual(loadable, ["Automatic", "Slot 1", "Slot 2", "Slot 3", "Slot 4"]);
  await page.click("#rp-slot-list li:nth-child(2) button");   // Slot 1's Load
  await page.waitForFunction(() => document.getElementById("rp-status").textContent === "Loaded Slot 1", { timeout: 20000 });
  await leave(page);

  // ---- Every console: its game boots pink; a second boot in a fresh browser (another device, as far as the
  // emulator's local copy goes) counts on from the save the first left on the server.
  const counted = [];
  for (const key of Object.keys(SAVE_BYTE)) {
    await playFresh(page, ids[key]);
    const colour = await screenColour(page);
    assert.ok(pink(colour), `the ${key} game's screen is pink: ${colour}`);
    await leave(page);
    if (SAVE_BYTE[key] === null) { counted.push(key + " (no save)"); continue; }
    const first = await saveByte(page, ids[key], SAVE_BYTE[key]);
    assert.notEqual(first, null, `the ${key} game's own save reached the server`);
    const elsewhereContext = await browser.createBrowserContext();
    const other = await newPage(elsewhereContext);
    await playFresh(other, ids[key]);
    await leave(other);
    assert.equal(await saveByte(other, ids[key], SAVE_BYTE[key]), (first + 1) & 0xff,
      `the ${key} game counted on from the server's save in a fresh browser`);
    await elsewhereContext.close();
    counted.push(`${key} ${first}→${(first + 1) & 0xff}`);
  }

  assert.deepEqual(elsewhere, [], "nothing was asked of another host");
  assert.deepEqual(errors, [], "no page errors");
  console.log("Retro browser check passed. GBA: played, saved, continued, started over and loaded. Every console " +
    "booted pink; saves counted on in a fresh browser: " + counted.join(", ") + ".");
} catch (err) {
  console.error("The page said:\n" + consoleLines.slice(-60).join("\n"));
  throw err;
} finally {
  if (browser) await browser.close();
  server.kill();
}
