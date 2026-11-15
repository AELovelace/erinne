// A TV box's 🔗 Controllers and Retro on the TV, in real Chrome (python/tv_controllers_preview.py: the app, and the
// box's own helper on 127.0.0.1:9187 with a pretend bluetoothctl).
//   PUPPETEER_MODULE=<puppeteer's lib/esm/puppeteer/puppeteer(-core).js> CHROME_PATH=<chrome> node tests/tv-controllers-browser.mjs
// 1. A real remote's OK opens Controllers: the server checks the press and gives a pass, the helper redeems it with
//    the server; the paired Xbox controller shows connected, the keyboard and phone nearby never show.
// 2. A pretend controller (navigator.getGamepads) drives the TV like its keyboard: finds and pairs the DualSense,
//    forgets it (pressed twice), moves focus, and opens Kiosk settings, which the remote can't.
// 3. Retro opens in the game layer: the remote walks the shelf and starts the game; the controller's Xbox/PS button
//    opens the game's menu; the TV's Back saves (an automatic state on the server) before the layer closes; and a
//    second controller is mapped for player 2.
// Screenshots go to runs/tv-controllers/.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import net from "node:net";

const { default: puppeteer } = await import(process.env.PUPPETEER_MODULE ? pathToFileURL(process.env.PUPPETEER_MODULE).href : "puppeteer");
const port = await new Promise(done => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => done(p)); }); });
const base = `http://127.0.0.1:${port}`;
const python = resolve(process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const server = spawn(python, ["python/tv_controllers_preview.py", String(port)], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
let logs = "", browser;
server.stdout.on("data", b => { logs += b; }); server.stderr.on("data", b => { logs += b; });
const errors = [];
const wait = ms => new Promise(r => setTimeout(r, ms));

try {
  for (let i = 0; i < 150; i++) {
    try { if ((await fetch(base + "/login")).ok) break; } catch (_) { /* starting */ }
    if (server.exitCode !== null) throw new Error(logs);
    if (i === 149) throw new Error("Preview did not start: " + logs);
    await wait(200);
  }
  await mkdir("runs/tv-controllers", { recursive: true });
  browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true,
    args: ["--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding"] });
  const page = await browser.newPage();
  page.on("pageerror", e => errors.push(e.stack || e.message));
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(document, "hidden", { get: () => false });
    window.testPads = [];                                                    // a pretend controller, read by gamepad.js
    navigator.getGamepads = () => window.testPads;
    window.testPress = (buttons) => {
      window.testPads = [{ index: 0, connected: true, mapping: "standard", axes: [0, 0, 0, 0],
        buttons: Array.from({ length: 17 }, (_, n) => ({ pressed: buttons.includes(n), value: buttons.includes(n) ? 1 : 0 })) }];
    };
    let real;                                                                // the Retro player's emulator, for the test
    Object.defineProperty(window, "EmulatorJS", { configurable: true, get: () => real, set: v => {
      real = class extends v { constructor(...a) { super(...a); window.EJS_emulator_for_tests = this; } };
    } });
  });
  await page.setViewport({ width: 1920, height: 1080 });
  await page.goto(base + "/login");
  await page.evaluate(async () => {
    const r = await fetch("/login", { method: "POST", body: new URLSearchParams({ username: "alice", password: "preview-password" }) });
    if (!r.ok) throw new Error("Sign-in failed");
  });

  // This browser becomes a TV box, and alice pairs a remote to it.
  const screen = await page.evaluate(async () => (await fetch("/api/media/screens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Box TV" }) })).json());
  const remote = await page.evaluate(async () => {
    const pending = await (await fetch("/media/remote/pair", { method: "POST" })).json();
    const claimed = await fetch("/api/media/screens/claim", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: pending.code, name: "Couch remote" }) });
    if (!claimed.ok) throw new Error("Could not pair the remote");
    return (await (await fetch("/media/remote/pair/status", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ secret: pending.secret }) })).json()).key;
  });
  await page.evaluate(async () => { await fetch("/logout", { method: "POST" }); });   // the TV is only its screen key from here
  async function press(button) { // a real remote command, through the server and the TV's stream
    const r = await fetch(base + "/api/media/remote/screens/" + screen.id + "/command", { method: "POST",
      headers: { "Authorization": "Bearer " + remote, "Content-Type": "application/json" },
      body: JSON.stringify(button === "play" ? { action: "toggle" } : { action: "press", button }) });
    assert.equal(r.status, 200, "the remote's press reaches the TV");
  }
  const everywhere = fn => Promise.all(page.frames().map(f => fn(f).catch(() => {})));   // a real controller is seen by every frame
  const pad = async (...buttons) => {
    await everywhere(f => f.evaluate(b => window.testPress && window.testPress(b), buttons)); await wait(150);
    await everywhere(f => f.evaluate(() => { window.testPads = []; })); await wait(150);
  };
  await page.goto(base + "/media/screen?kiosk=1");
  await page.waitForFunction(() => document.getElementById("mc-splash") && document.getElementById("mc-splash").hidden !== undefined);
  await page.waitForSelector("#mc-controllers:not([hidden])");
  await page.waitForFunction(() => document.querySelector("#mc-splash-start") !== null);
  await wait(1500);                                                          // the TV connects to its stream
  if (await page.evaluate(() => !document.getElementById("mc-splash").hidden)) {   // the splash takes the first key, whatever it is
    await page.keyboard.press("Shift");
    await page.waitForFunction(() => document.getElementById("mc-splash").hidden);
  }

  // 1. The remote's OK opens Controllers.
  await page.evaluate(() => document.getElementById("mc-controllers").focus());
  await press("enter");
  await page.waitForFunction(() => document.querySelectorAll("#mc-pads .mc-pad").length === 1, { timeout: 15000 });
  const first = await page.$eval("#mc-pads", e => e.textContent);
  assert.match(first, /Xbox Wireless Controller/);
  assert.match(first, /Connected · battery 90%/);
  assert.doesNotMatch(first, /Logitech|Phone/, "only game controllers");
  await page.screenshot({ path: "runs/tv-controllers/controllers.png" });

  // 2. The controller: Find, Pair, Forget, and moving focus.
  await page.evaluate(() => document.querySelector("[data-key=find]").focus());
  await pad(0);
  await page.waitForFunction(() => /DualSense/.test(document.getElementById("mc-pads").textContent), { timeout: 15000 });
  await page.evaluate(() => document.querySelector('[data-key="AA:BB:CC:00:00:02"]').focus());
  await pad(0);
  await page.waitForFunction(() => /DualSense[\s\S]*Connected/.test(document.getElementById("mc-pads").textContent), { timeout: 15000 });
  await page.screenshot({ path: "runs/tv-controllers/paired.png" });
  await page.evaluate(() => document.querySelector('[data-key="AA:BB:CC:00:00:02"]').focus());
  await pad(0);
  await page.waitForFunction(() => /Forget it\?/.test(document.getElementById("mc-pads").textContent));
  await page.evaluate(() => document.querySelector('[data-key="AA:BB:CC:00:00:02"]').focus());
  await pad(0);
  await page.waitForFunction(() => !/DualSense/.test(document.getElementById("mc-pads").textContent), { timeout: 15000 });
  const before = await page.evaluate(() => document.activeElement && document.activeElement.textContent);
  await pad(13);                                                             // D-pad down
  assert.notEqual(await page.evaluate(() => document.activeElement && document.activeElement.textContent), before, "the D-pad moves focus");
  console.log("controllers: a remote's OK opens them; a controller finds, pairs and forgets; only game controllers show");

  // Kiosk settings: the box's controller may open them, a remote may not.
  await page.evaluate(() => document.getElementById("mc-kiosk").focus());
  await press("enter");
  await wait(800);
  assert.equal(await page.evaluate(() => Boolean(document.querySelector(".mc-kiosk-unlock:not([hidden])"))), false, "a remote can't open Kiosk settings");
  await page.evaluate(() => document.getElementById("mc-kiosk").focus());
  await pad(0);
  await page.waitForSelector(".mc-kiosk-unlock:not([hidden])");
  await pad(1);                                                              // B closes it
  await page.waitForFunction(() => !document.querySelector(".mc-kiosk-unlock:not([hidden])"));
  console.log("controllers: the box's controller opens Kiosk settings; a remote can't");

  // 3. Retro on the TV.
  await page.evaluate(() => document.getElementById("mc-retro").focus());
  await press("enter");
  const frame = await (await page.waitForSelector("#mc-game-frame")).contentFrame();
  await frame.waitForSelector("a.rt-game", { timeout: 20000 });
  assert.equal(await frame.$("header.topbar"), null, "no site menu in the game layer");
  for (let i = 0; i < 6 && !(await frame.evaluate(() => document.activeElement && document.activeElement.matches("a.rt-game"))); i++) await press("down");
  assert.ok(await frame.evaluate(() => document.activeElement.matches("a.rt-game")), "the remote reaches a game");
  await press("enter");
  await frame.waitForSelector("#rp-start:not([hidden])", { timeout: 20000 });
  await press("enter");                                                      // ▶ Start
  await frame.waitForFunction(() => !document.getElementById("rp-save").disabled, { timeout: 60000 });
  await wait(2000);
  const controls = await frame.evaluate(() => window.EJS_emulator_for_tests.controls);
  assert.equal(controls[1][8].value2, "BUTTON_1", "a second controller is player 2");
  await page.screenshot({ path: "runs/tv-controllers/retro.png" });
  await pad(16);                                                             // the Xbox / PS button: the menu
  await frame.waitForSelector(".rp-tvmenu:not([hidden])");
  await page.screenshot({ path: "runs/tv-controllers/retro-menu.png" });
  await pad(1);                                                              // B: back to the game
  await frame.waitForFunction(() => document.querySelector(".rp-tvmenu").hidden);
  const id = await frame.evaluate(() => JSON.parse(document.getElementById("rp-data").textContent).id);
  await page.click("#mc-game-back");                                         // the TV's Back: save, then close
  await page.waitForFunction(() => document.getElementById("mc-game").hidden, { timeout: 20000 });
  const saves = await page.evaluate(async i => (await (await fetch("/api/media/retro/saves/" + i, { cache: "no-store" })).json()), id);
  assert.ok(JSON.stringify(saves).includes("auto"), "leaving on the TV kept an automatic state: " + JSON.stringify(saves));
  console.log("retro on the TV: the remote walks and starts it, the controller's menu, player 2 mapped, Back saves first");
  assert.deepEqual(errors, []);
} catch (failure) {
  if (errors.length) console.error("Page errors:", errors.join(" || "));
  if (browser) for (const p of await browser.pages()) console.error("TV says:", await p.evaluate(() => [location.href, (document.getElementById("mc-error") || {}).textContent, (document.getElementById("mc-content") || {}).textContent].join(" | ").slice(0, 600)).catch(() => "?"));
  if (process.env.SHOW_SERVER_LOG === "1") console.error(logs.slice(-6000));
  if (browser) for (const p of await browser.pages()) await p.screenshot({ path: "runs/tv-controllers/failure.png" }).catch(() => {});
  throw failure;
} finally {
  if (browser) await browser.close();
  server.kill();
}
