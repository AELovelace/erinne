// "Which ROM?" in a real browser (Doll, 2026-11-15): an archive holding two ROMs (the GBA test game and a "hack" of
// it, python/retro_preview.py with RETRO_PREVIEW_PACK) asks which one before anything starts; the one picked is the
// one the emulator plays, and the other never reaches it (EmulatorJS's unpacking is filtered); its saves go to that
// ROM's own key (?member=...); "↩ Another ROM" goes back to the list.
//   PUPPETEER_MODULE=<puppeteer(-core)'s lib/esm/puppeteer/puppeteer(-core).js> CHROME_PATH=<chrome> node tests/retro-which-rom-browser.mjs
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
const server = spawn(python, ["python/retro_preview.py", String(port)], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
  env: { ...process.env, RETRO_PREVIEW_PACK: "1" } });
let logs = "", browser;
server.stdout.on("data", b => { logs += b; }); server.stderr.on("data", b => { logs += b; });
const errors = [], saveUrls = [];
const HACK = "hacks/Lumi Test (Hack).gba", ORIGINAL = "Lumi Test.gba";

try {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(base + "/login")).ok) break; } catch (_) { /* starting */ }
    if (server.exitCode !== null) throw new Error(logs);
    if (attempt === 149) throw new Error("The preview didn't start: " + logs);
    await new Promise(r => setTimeout(r, 200));
  }
  browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true,
    args: ["--autoplay-policy=no-user-gesture-required", "--enable-unsafe-swiftshader"] });
  const page = await browser.newPage();
  page.on("pageerror", e => errors.push(e.stack || e.message));
  page.on("request", r => { if (r.url().includes("/api/media/retro/saves/")) saveUrls.push(r.url()); });
  await page.evaluateOnNewDocument(() => {
    let real;
    Object.defineProperty(window, "EmulatorJS", { configurable: true, get: () => real, set: v => {
      real = class extends v { constructor(...a) { super(...a); window.EJS_emulator_for_tests = this; } };
    } });
  });
  await page.goto(base + "/login");
  await page.evaluate(async () => {
    const r = await fetch("/login", { method: "POST", body: new URLSearchParams({ username: "alice", password: "preview-password" }) });
    if (!r.ok) throw new Error("Sign-in failed");
  });
  const games = await page.evaluate(async () => (await fetch("/api/media/retro/games", { cache: "no-store" })).json());
  const pack = games.games.find(g => g.title === "Lumi Pack");
  assert.ok(pack, "the pack is on the shelf: " + JSON.stringify(games.games.map(g => g.title)));

  // Which ROM? first: two buttons, and no Start until one is picked.
  await page.goto(`${base}/media/retro/play/${pack.id}`);
  await page.waitForSelector("#rp-which:not([hidden]) .rp-which-rom");
  assert.deepEqual(await page.$$eval(".rp-which-rom", bs => bs.map(b => b.dataset.member)), [ORIGINAL, HACK]);
  assert.equal(await page.$eval("#rp-fresh", b => b.hidden), true, "nothing starts before a ROM is picked");

  // Another ROM goes back to the list; then the hack, and it's the one playing.
  await page.click(`.rp-which-rom[data-member="${ORIGINAL}"]`);
  assert.equal(await page.$eval("#rp-other", b => b.hidden), false);
  await page.click("#rp-other");
  assert.equal(await page.$eval("#rp-which", w => w.hidden), false);
  await page.click(`.rp-which-rom[data-member="${HACK}"]`);
  await page.click("#rp-fresh");
  await page.waitForFunction(() => !document.getElementById("rp-save").disabled, { timeout: 60000 });
  const loaded = await page.evaluate(() => {
    const e = window.EJS_emulator_for_tests;
    return { file: e.fileName, other: e.gameManager.FS.analyzePath("/Lumi Test.gba").exists,
             title: new TextDecoder().decode(e.gameManager.FS.readFile("/" + e.fileName).slice(0xA0, 0xA9)) };
  });
  assert.equal(loaded.file, HACK, "the emulator plays the ROM picked");
  assert.equal(loaded.title, "LUMI HACK", "its bytes are the hack's");
  assert.equal(loaded.other, false, "the other ROM never reached the emulator");
  assert.ok(saveUrls.length && saveUrls.every(u => u.includes("member=hacks%2FLumi%20Test%20(Hack).gba")),
    "its saves are the hack's own: " + saveUrls.join(" "));
  assert.deepEqual(errors, [], "no page errors");
  console.log("ok: Which ROM? first, the hack picked is the one played, the original never unpacked, saves kept apart");
} catch (err) {
  console.error(err);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  server.kill();
}
