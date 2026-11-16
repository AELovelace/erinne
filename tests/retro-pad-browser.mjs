// Retro with a controller that was already connected when the game loaded, as on a TV: the controller that picked
// the game is still there when EmulatorJS starts. EmulatorJS only seats a controller as a player when its
// GamepadHandler announces it, and that first look happens inside the handler's constructor, before EmulatorJS
// listens, so such a controller never got a player and every press was dropped (Doll's Metroid, 2026-11-15).
// retro-player.js seatPads gives it the next free player. This boots the NES test game with a pretend controller
// present from the first moment, then checks it is player 1 and that Start reaches the game. Then that a Nintendo
// console plays by place (the right button is A, the bottom one B), and that a game played before, whose saved
// controls are still EmulatorJS's letter map, is turned round when it starts again.
//   PUPPETEER_MODULE=<puppeteer(-core)'s lib/esm/puppeteer/puppeteer(-core).js> CHROME_PATH=<chrome> node tests/retro-pad-browser.mjs
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
const errors = [];

try {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(base + "/login")).ok) break; } catch (_) { /* the app is still starting */ }
    if (server.exitCode !== null) throw new Error(logs);
    if (attempt === 149) throw new Error("The preview didn't start: " + logs);
    await new Promise(r => setTimeout(r, 200));
  }
  browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true,
    args: ["--autoplay-policy=no-user-gesture-required", "--enable-unsafe-swiftshader"] });
  const page = await browser.newPage();
  page.on("pageerror", e => errors.push(e.stack || e.message));
  await page.evaluateOnNewDocument(() => {
    // The player keeps its emulator to itself; this hands the test a reference, as retro-browser.mjs does.
    let real;
    Object.defineProperty(window, "EmulatorJS", { configurable: true, get: () => real, set: v => {
      real = class extends v { constructor(...a) { super(...a); window.EJS_emulator_for_tests = this; } };
    } });
    // A standard-layout controller, connected before anything on the page runs (an xb2pc on the TV box).
    const buttons = Array.from({ length: 17 }, () => ({ pressed: false, touched: false, value: 0 }));
    window.testPad = { id: "xb2pc Xbox pad", index: 0, connected: true, mapping: "standard", timestamp: 0,
      axes: [0, 0, 0, 0], buttons };
    navigator.getGamepads = () => [window.testPad, null, null, null];
  });
  await page.goto(base + "/login");
  await page.evaluate(async () => { // the real session sign-in
    const r = await fetch("/login", { method: "POST", body: new URLSearchParams({ username: "alice", password: "preview-password" }) });
    if (!r.ok) throw new Error("Sign-in failed");
  });
  const games = await page.evaluate(async () => (await fetch("/api/media/retro/games", { cache: "no-store" })).json());
  const nes = games.games.find(g => g.console === "nes");
  assert.ok(nes, "the preview has an NES game");

  await page.goto(`${base}/media/retro/play/${nes.id}`);
  await page.waitForSelector("#rp-fresh");
  await page.click("#rp-fresh");
  await page.waitForFunction(() => !document.getElementById("rp-save").disabled, { timeout: 60000 });   // running

  // The controller that was there all along is player 1 (EmulatorJS's own "connected" never told anyone).
  await page.waitForFunction(() => (window.EJS_emulator_for_tests.gamepadSelection || [])[0] === "xb2pc Xbox pad_0",
    { timeout: 5000 }).catch(() => {});
  const seats = await page.evaluate(() => window.EJS_emulator_for_tests.gamepadSelection);
  assert.equal(seats[0], "xb2pc Xbox pad_0", "the controller is player 1: " + JSON.stringify(seats));

  // Start on the controller (standard button 9) reaches the game as player 1's START (EmulatorJS input 3).
  const pressed = await page.evaluate(async () => {
    const emu = window.EJS_emulator_for_tests, calls = [];
    const real = emu.gameManager.simulateInput.bind(emu.gameManager);
    emu.gameManager.simulateInput = (player, input, value) => { calls.push([player, input, value]); return real(player, input, value); };
    window.testPad.buttons[9] = { pressed: true, touched: true, value: 1 };
    await new Promise(r => setTimeout(r, 200));
    window.testPad.buttons[9] = { pressed: false, touched: false, value: 0 };
    await new Promise(r => setTimeout(r, 200));
    return calls;
  });
  assert.deepEqual(pressed.filter(c => c[1] === 3), [[0, 3, 1], [0, 3, 0]], "Start went in and came up: " + JSON.stringify(pressed));

  // A Nintendo console plays by place: the right button (standard 1, Xbox B) is the NES's A (EmulatorJS input 8),
  // the bottom one (0, Xbox A) its B (input 0). (Doll: Zero Mission's A and B were the wrong way round.)
  const tap = async (button) => page.evaluate(async b => {
    const emu = window.EJS_emulator_for_tests, calls = [];
    const real = emu.gameManager.simulateInput.bind(emu.gameManager);
    emu.gameManager.simulateInput = (player, input, value) => { calls.push([player, input, value]); return real(player, input, value); };
    window.testPad.buttons[b] = { pressed: true, touched: true, value: 1 };
    await new Promise(r => setTimeout(r, 200));
    window.testPad.buttons[b] = { pressed: false, touched: false, value: 0 };
    await new Promise(r => setTimeout(r, 200));
    emu.gameManager.simulateInput = real;
    return calls.filter(c => c[2] === 1).map(c => c[1]);
  }, button);
  assert.deepEqual(await tap(1), [8], "the right button is the NES's A");
  assert.deepEqual(await tap(0), [0], "the bottom button is the NES's B");

  // A Nintendo game played before keeps its controls in the browser, in EmulatorJS's letter map: next time they're
  // turned round to the map by place (a map somebody set up differently would be left alone).
  await page.evaluate(() => {
    const emu = window.EJS_emulator_for_tests;   // what EmulatorJS's saveSettings writes, from these controls
    const saved = JSON.parse(localStorage.getItem(emu.getLocalStorageKey()) || "null") ||
      { controlSettings: JSON.parse(JSON.stringify(emu.controls)), settings: {}, cheats: [] };
    for (const p of [0, 1, 2, 3]) Object.assign(saved.controlSettings[p], { 0: { ...saved.controlSettings[p][0], value2: "BUTTON_2" },
      8: { ...saved.controlSettings[p][8], value2: "BUTTON_1" } });   // as an earlier visit left it
    localStorage.setItem(emu.getLocalStorageKey(), JSON.stringify(saved));
  });
  await page.goto(`${base}/media/retro/play/${nes.id}`);
  await page.waitForSelector("#rp-fresh");
  await page.click("#rp-fresh");
  await page.waitForFunction(() => !document.getElementById("rp-save").disabled, { timeout: 60000 });
  const turned = await page.evaluate(() => {
    const c = window.EJS_emulator_for_tests.controls[0];
    return [c[0].value2, c[8].value2];
  });
  assert.deepEqual(turned, ["BUTTON_1", "BUTTON_2"], "the saved letter map was turned round");
  await page.waitForFunction(() => (window.EJS_emulator_for_tests.gamepadSelection || [])[0] === "xb2pc Xbox pad_0",
    { timeout: 5000 });
  assert.deepEqual(await tap(1), [8], "and the right button is A again after a reload");
  assert.deepEqual(errors, [], "no page errors");
  console.log("ok: a controller connected before the game loaded plays as player 1, Nintendo buttons by place");
} catch (err) {
  console.error(err);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  server.kill();
}
