// Gamepad mode in real browsers (Doll, 2026-11-16: the phone's panel and the Tab5 as a game controller):
// 1. The phone's panel (/media/panel): its 🎮 Gamepad page sends each button down and up ({action: "pad"}), the
//    D-pad as one zone a thumb rolls across (a diagonal holds two), several fingers at once, and leaving the page
//    lets go of everything. The panel's own calls are answered here (a paired remote, one TV online).
// 2. Retro on a TV: the player in a frame, as the TV's game layer has it, gets the TV page's messages
//    ({type: "mnemerynne-game-pad"}); a remote's held A and the D-pad reach the game as a player of its own
//    (player 1 here, as no controller of the box has it), pressed and let go.
//   PUPPETEER_MODULE=<puppeteer(-core)'s lib/esm/puppeteer/puppeteer(-core).js> CHROME_PATH=<chrome> node tests/panel-gamepad-browser.mjs
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

try {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(base + "/login")).ok) break; } catch (_) { /* starting */ }
    if (server.exitCode !== null) throw new Error(logs);
    if (attempt === 149) throw new Error("The preview didn't start: " + logs);
    await sleep(200);
  }
  browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true,
    args: ["--autoplay-policy=no-user-gesture-required", "--enable-unsafe-swiftshader"] });

  // ---- 1. The phone's panel
  const phone = await browser.newPage();
  phone.on("pageerror", e => errors.push("panel: " + (e.stack || e.message)));
  await phone.setViewport({ width: 844, height: 390, hasTouch: true, isMobile: true });   // a phone held sideways
  await phone.evaluateOnNewDocument(() => {
    localStorage.setItem("mc-panel-key", "a-paired-remote");
    localStorage.setItem("mc-panel-screen", "7");
  });
  const sent = [];
  await phone.setRequestInterception(true);
  phone.on("request", r => {
    const path = new URL(r.url()).pathname;
    if (path === "/api/media/remote") {
      r.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ name: "Doll's phone", owner: "alice", speaker: null,
        screens: [{ id: 7, name: "Living room", online: true, speaker: false, state: { kind: "game", title: "Retro" }, guest: false, host: null }] }) });
    } else if (path === "/api/media/remote/screens/7/command") {
      sent.push(JSON.parse(r.postData()));
      r.respond({ status: 200, contentType: "application/json", body: '{"status":"sent","id":1}' });
    } else r.continue();
  });
  await phone.goto(base + "/media/panel");
  await phone.waitForSelector("#mp-open-gamepad");
  await phone.waitForFunction(() => !document.getElementById("mp-remote").hidden);
  await sleep(300);                                                    // the first poll: the TV is online
  await phone.click("#mp-open-gamepad");
  await phone.waitForSelector("#mp-gamepad:not([hidden])");
  assert.match(await phone.$eval("#mp-gp-title", t => t.textContent), /Living room/);

  const finger = (type, id, x, y, sel) => phone.evaluate((type, id, x, y, sel) => {
    const el = document.querySelector(sel), box = el.getBoundingClientRect();
    el.dispatchEvent(new PointerEvent(type, { pointerId: id, bubbles: true, cancelable: true, pointerType: "touch",
      clientX: box.left + box.width * x, clientY: box.top + box.height * y }));
  }, type, id, x, y, sel);
  const settle = () => sleep(150);
  // A thumb on the D-pad's right, rolled up into up-right, then off; a second finger holds A meanwhile.
  await finger("pointerdown", 1, 0.95, 0.5, "#mp-gp-dpad"); await settle();
  await finger("pointerdown", 2, 0.5, 0.5, '[data-gp="a"]'); await settle();
  await finger("pointermove", 1, 0.9, 0.1, "#mp-gp-dpad"); await settle();
  assert.deepEqual((await phone.evaluate(() => window.MPGamepad.held())).sort(), ["a", "right", "up"]);
  await finger("pointerup", 1, 0.9, 0.1, "#mp-gp-dpad"); await settle();
  await finger("pointerup", 2, 0.5, 0.5, '[data-gp="a"]'); await settle();
  const pads = sent.filter(c => c.action === "pad").map(c => c.button + (c.down ? "↓" : "↑"));
  assert.deepEqual(pads, ["right↓", "a↓", "up↓", "up↑", "right↑", "a↑"], "each down and up, in order: " + pads.join(" "));

  // Leaving with Start held lets go of it on the TV.
  await finger("pointerdown", 3, 0.5, 0.5, '[data-gp="start"]'); await settle();
  await phone.click("#mp-gp-exit"); await settle();
  assert.deepEqual(sent.slice(-2).map(c => c.button + (c.down ? "↓" : "↑")), ["start↓", "start↑"], "leaving lets go");
  assert.equal(await phone.$eval("#mp-gamepad", g => g.hidden), true);

  // ---- 2. Retro: the player framed as on a TV, a remote's pad messages from the page around it
  const tv = await browser.newPage();
  tv.on("pageerror", e => errors.push("tv: " + (e.stack || e.message)));
  await tv.goto(base + "/login");
  await tv.evaluate(async () => {
    const r = await fetch("/login", { method: "POST", body: new URLSearchParams({ username: "alice", password: "preview-password" }) });
    if (!r.ok) throw new Error("Sign-in failed");
  });
  const games = await tv.evaluate(async () => (await fetch("/api/media/retro/games", { cache: "no-store" })).json());
  const nes = games.games.find(g => g.console === "nes");
  await tv.evaluate(id => {   // the TV's game layer, as far as Retro sees it: its page in a same-origin frame
    document.body.innerHTML = "";
    const f = document.createElement("iframe");
    f.id = "game"; f.style.cssText = "width:960px;height:640px;border:0";
    f.src = "/media/retro/play/" + id;
    document.body.append(f);
  }, nes.id);
  const frameHandle = await tv.waitForSelector("#game");
  const game = await frameHandle.contentFrame();
  await game.waitForSelector("#rp-fresh");
  await game.click("#rp-fresh");
  await game.waitForFunction(() => window.RetroPlayer && window.RetroPlayer.started(), { timeout: 60000 });
  await game.evaluate(() => {   // every input the remote's pad gives the game
    const gm = window.RetroPlayer.emu().gameManager, real = gm.simulateInput.bind(gm);
    window.padInputs = [];
    gm.simulateInput = (p, i, v) => { window.padInputs.push([p, i, v]); return real(p, i, v); };
  });
  const pad = (button, down) => tv.evaluate((button, down) => {
    document.getElementById("game").contentWindow.postMessage({ type: "mnemerynne-game-pad", button, down, remote: 3 }, location.origin);
  }, button, down);
  await pad("a", true); await sleep(300);
  await pad("right", true); await sleep(200);
  await pad("right", false); await sleep(100);
  await pad("a", false); await sleep(200);
  const inputs = await game.evaluate(() => window.padInputs);
  assert.deepEqual(inputs, [[0, 8, 1], [0, 7, 1], [0, 7, 0], [0, 8, 0]],
    "A (EmulatorJS input 8) held across a press of right (7), as player 1: " + JSON.stringify(inputs));
  assert.match(await game.$eval("#rp-status", s => s.textContent), /player 1/);

  assert.deepEqual(errors, [], "no page errors");
  console.log("ok: the panel's gamepad sends each button down and up in order (a rolled diagonal, two fingers, letting go on leaving); " +
    "Retro plays a remote's pad as player 1, held");
} catch (err) {
  console.error(err);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  server.kill();
}
