// Media → Retro's library pages in a real browser (Doll's redesign, 2026-11-15). The games list is the page's own
// API answer, replaced with one that has RetroAchievements player counts (media/popularity.py), so the order can be
// checked: the Top page (Continue playing, then each console's row by popularity, ranked, with its "All" card),
// a console's page A–Z, the tabs, [ and ], a controller's RB / LB (gamepad.js through retro-tv.js), a search over
// every console, and the page kept in the address across a reload.
//   PUPPETEER_MODULE=<puppeteer(-core)'s lib/esm/puppeteer/puppeteer(-core).js> CHROME_PATH=<chrome> node tests/retro-library-browser.mjs
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

// The answer the page gets: NES games with counts (and one without), two SNES, one GBA played lately.
const CONSOLES = [{ key: "nes", name: "NES", folder: "NES" }, { key: "snes", name: "Super Nintendo", folder: "SNES" },
  { key: "genesis", name: "Genesis", folder: "Genesis" }, { key: "gba", name: "Game Boy Advance", folder: "GBA" }];
const game = (id, console, title, players, extra = {}) => ({ id, console, title, name: title + ".rom", size: 1, cover: null,
  played_at: null, has_save: false, can_continue: false, needs_bios: false, players, ...extra });
const GAMES = [
  game(1, "nes", "Zelda", 5000), game(2, "nes", "Mario", 90000), game(3, "nes", "Metroid", 12000),
  game(4, "nes", "Aardvark Homebrew", null), game(5, "nes", "Kirby", 7000), game(6, "nes", "Tetris", 30000),
  game(7, "nes", "Contra", 8000), game(8, "nes", "Castlevania", 9000),
  game(10, "snes", "Super Metroid", 60000), game(11, "snes", "Chrono Trigger", null),
  game(20, "gba", "Zero Mission", 40000, { played_at: "2026-11-15T10:00:00Z", can_continue: true }),
];

try {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(base + "/login")).ok) break; } catch (_) { /* starting */ }
    if (server.exitCode !== null) throw new Error(logs);
    if (attempt === 149) throw new Error("The preview didn't start: " + logs);
    await new Promise(r => setTimeout(r, 200));
  }
  browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true });
  const page = await browser.newPage();
  page.on("pageerror", e => errors.push(e.stack || e.message));
  await page.evaluateOnNewDocument(() => {   // a controller whose RB / LB the test presses
    window.testPad = { id: "pad", index: 0, connected: true, mapping: "standard", timestamp: 0, axes: [0, 0, 0, 0],
      buttons: Array.from({ length: 17 }, () => ({ pressed: false, touched: false, value: 0 })) };
    navigator.getGamepads = () => [window.testPad];
  });
  await page.setRequestInterception(true);
  page.on("request", r => {
    if (new URL(r.url()).pathname === "/api/media/retro/games") {
      r.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ library: true, consoles: CONSOLES, games: GAMES }) });
    } else r.continue();
  });
  await page.goto(base + "/login");
  await page.evaluate(async () => {
    const r = await fetch("/login", { method: "POST", body: new URLSearchParams({ username: "alice", password: "preview-password" }) });
    if (!r.ok) throw new Error("Sign-in failed");
  });
  await page.goto(base + "/media/retro");
  await page.waitForSelector(".rt-game");

  // Top: Continue playing, then a row per console that has games, in console order.
  const titles = () => page.$$eval(".rt-shelf-title", hs => hs.map(h => h.firstChild.textContent));
  assert.deepEqual(await titles(), ["Continue playing", "NES", "Super Nintendo", "Game Boy Advance"]);
  const rowOf = n => page.$$eval(".rt-shelf", (ss, n) => [...ss[n].querySelectorAll("a.rt-game .rt-name")].map(e => e.textContent), n);
  assert.deepEqual(await rowOf(1), ["Mario", "Tetris", "Metroid", "Castlevania", "Contra", "Kirby", "Zelda", "Aardvark Homebrew"],
    "most played first, the unranked one last");
  assert.deepEqual(await page.$$eval(".rt-shelf:nth-of-type(2) .rt-rank", rs => rs.slice(0, 3).map(r => r.textContent)), ["#1", "#2", "#3"]);
  assert.match(await page.$eval(".rt-shelf:nth-of-type(2) a.rt-game .rt-words .small", e => e.textContent), /👥 90k/);
  assert.deepEqual(await rowOf(2), ["Super Metroid", "Chrono Trigger"]);
  assert.equal(await page.$$eval(".rt-shelf:nth-of-type(2) .rt-more", b => b.length), 1, "a long row ends with its console's page");
  assert.deepEqual(await page.$$eval(".rt-tab", ts => ts.map(t => t.textContent)), ["⭐ Top", "NES", "Super Nintendo", "Game Boy Advance"]);

  // ] and [ flip pages; a console's page is A–Z.
  await page.keyboard.press("]");
  assert.equal(await page.$eval('.rt-tab[aria-current="page"]', t => t.textContent), "NES");
  assert.deepEqual(await rowOfGrid(page), ["Aardvark Homebrew", "Castlevania", "Contra", "Kirby", "Mario", "Metroid", "Tetris", "Zelda"]);
  assert.equal(new URL(page.url()).hash, "#nes");
  await page.keyboard.press("[");
  assert.equal(await page.$eval('.rt-tab[aria-current="page"]', t => t.textContent), "⭐ Top");
  await page.keyboard.press("[");                                   // round the end
  assert.equal(await page.$eval('.rt-tab[aria-current="page"]', t => t.textContent), "Game Boy Advance");

  // A controller: RB (standard button 5) and LB (4).
  const tapPad = async n => {
    await page.evaluate(n => { window.testPad.buttons[n] = { pressed: true, touched: true, value: 1 }; }, n);
    await new Promise(r => setTimeout(r, 400));   // headless Chrome draws ~20 frames a second
    await page.evaluate(n => { window.testPad.buttons[n] = { pressed: false, touched: false, value: 0 }; }, n);
    await new Promise(r => setTimeout(r, 400));   // headless Chrome draws ~20 frames a second
  };
  await tapPad(5);
  assert.equal(await page.$eval('.rt-tab[aria-current="page"]', t => t.textContent), "⭐ Top", "RB: the next page");
  await tapPad(5);
  assert.equal(await page.$eval('.rt-tab[aria-current="page"]', t => t.textContent), "NES");
  assert.equal(await page.evaluate(() => document.activeElement.matches("a.rt-game")), true, "a controller lands on a game");
  await tapPad(4);
  assert.equal(await page.$eval('.rt-tab[aria-current="page"]', t => t.textContent), "⭐ Top", "LB: the page before");

  // The row's "All" card and a tab open a console's page.
  await page.click(".rt-shelf:nth-of-type(2) .rt-more");
  assert.equal(await page.$eval('.rt-tab[aria-current="page"]', t => t.textContent), "NES");
  await page.click('.rt-tab[data-page="snes"]');
  assert.deepEqual(await rowOfGrid(page), ["Chrono Trigger", "Super Metroid"]);

  // A search covers every console; the page is kept across a reload.
  await page.type("#rt-q", "metroid");
  assert.deepEqual(await rowOfGrid(page), ["Metroid", "Super Metroid"]);
  await page.reload();
  await page.waitForSelector(".rt-game");
  assert.equal(await page.$eval('.rt-tab[aria-current="page"]', t => t.textContent), "Super Nintendo");
  assert.deepEqual(errors, [], "no page errors");
  console.log("ok: Top by popularity, console pages A–Z, tabs, [ ], LB / RB, search, kept across a reload");
} catch (err) {
  console.error(err);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  server.kill();
}

async function rowOfGrid(page) {   // the names in the page's grid, in order
  return page.$$eval(".rt-grid a.rt-game .rt-name", es => es.map(e => e.textContent));
}
