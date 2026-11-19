// Files' Share button in a real browser (python/retro_preview.py: a disposable share; alice, bob and carol).
//   PUPPETEER_MODULE=<puppeteer's lib/esm/puppeteer/puppeteer.js> CHROME_PATH=<chrome> node tests/share-browser.mjs
// 1. Alice opens Share on a file, shares it with Bob ("Can view"), then makes an "anyone with the link" link.
// 2. Bob finds it under 🤝 Shared with me, where it opens like any file.
// 3. Someone not signed in opens the link: the file's page and its download; a made-up link is a bare 404.
// Screenshots: runs/share/.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
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
const shots = resolve("runs/share");
mkdirSync(shots, { recursive: true });

async function signedIn(name) { // a fresh browser context signed in as `name`
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  page.on("pageerror", e => errors.push(name + ": " + (e.stack || e.message)));
  await page.setViewport({ width: 1280, height: 860 });
  await page.goto(base + "/login");
  await page.evaluate(async n => { // the real session sign-in
    const r = await fetch("/login", { method: "POST", body: new URLSearchParams({ username: n, password: "preview-password" }) });
    if (!r.ok) throw new Error("Sign-in failed");
  }, name);
  return page;
}

async function rowNamed(page, name) { // click the list row for `name`, so it's the one selected
  await page.waitForFunction(n => [...document.querySelectorAll("#list .row .c-name")].some(x => x.textContent.includes(n)),
    { timeout: 15000 }, name);
  await page.evaluate(n => {
    const row = [...document.querySelectorAll("#list .row")].find(r => r.querySelector(".c-name").textContent.includes(n));
    row.querySelector(".c-name").click();
  }, name);
}

try {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(base + "/login")).ok) break; } catch (_) { /* the app is still starting */ }
    if (server.exitCode !== null) throw new Error(logs);
    if (attempt === 149) throw new Error("The preview didn't start: " + logs);
    await new Promise(r => setTimeout(r, 200));
  }
  browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true });

  // 1. Alice: a file in her File box, shared with Bob, then a link.
  const alice = await signedIn("alice");
  await alice.goto(base + "/files");
  await alice.evaluate(async () => {
    const form = new FormData();
    form.append("file", new File(["# Night rota\n\nBob: Tuesday.\n"], "night-rota.md"));
    const r = await fetch("/api/files", { method: "POST", body: form, headers: { "X-Requested-With": "fetch" } });
    if (!r.ok) throw new Error("Upload failed: " + r.status + " " + await r.text());
  });
  await alice.goto(base + "/files");
  await rowNamed(alice, "night-rota.md");
  await alice.waitForFunction(() => !document.querySelector('[data-act="share"]').disabled);
  await alice.click('[data-act="share"]');
  await alice.waitForFunction(() => document.querySelector("#dlg-share").open
    && !document.querySelector("#share-list").textContent.includes("Loading"));
  await alice.select("#share-kind", "user");
  const bob = await alice.$eval("#share-target", s => [...s.options].find(o => o.textContent === "bob").value);
  await alice.select("#share-target", bob);
  await alice.click("#share-add");
  await alice.waitForFunction(() => document.querySelector("#share-list").textContent.includes("bob"));
  await alice.select("#share-kind", "link");
  assert.equal(await alice.$eval("#share-days", s => s.value), "7", "a link lasts 7 days unless chosen");
  assert.equal(await alice.$eval('#share-right option[value="edit"]', o => o.disabled), true, "links are view-only");
  await alice.click("#share-add");
  await alice.waitForSelector("#share-list .link-url");
  const url = await alice.$eval("#share-list .link-url", s => s.textContent);
  assert.match(url, /\/s\/[A-Za-z0-9_-]{43}$/, "a link names nothing but its token");
  assert.ok(!url.includes("night"), "the file's name isn't in the link");
  await alice.screenshot({ path: resolve(shots, "1-alice-share-dialog.png") });

  // 2. Bob: Shared with me.
  const bobPage = await signedIn("bob");
  await bobPage.goto(base + "/files#shared=1");
  await rowNamed(bobPage, "night-rota.md");
  assert.ok(await bobPage.$eval("#crumbs", c => c.textContent.includes("Shared with me")));
  assert.ok(await bobPage.$eval("#tree", t => t.textContent.includes("Shared with me")));
  assert.equal(await bobPage.$eval('[data-act="share"]', b => b.hidden || b.disabled), true, "a share isn't passed on");
  await bobPage.screenshot({ path: resolve(shots, "2-bob-shared-with-me.png") });

  // 3. Nobody signed in: the link's page and download, and a made-up link.
  const stranger = await (await browser.createBrowserContext()).newPage();
  stranger.on("pageerror", e => errors.push("stranger: " + (e.stack || e.message)));
  const opened = await stranger.goto(url.replace(/^https?:\/\/[^/]+/, base));
  assert.equal(opened.status(), 200);
  assert.equal(opened.headers()["referrer-policy"], "no-referrer");
  assert.ok((await stranger.content()).includes("night-rota.md"));
  await stranger.screenshot({ path: resolve(shots, "3-link-page.png") });
  const token = url.split("/s/")[1];
  // From outside the page: its CSP (default-src 'none') lets the page itself fetch nothing at all.
  const r = await fetch(base + "/s/" + token + "/download");
  const got = { status: r.status, type: r.headers.get("content-type"), body: await r.text() };
  assert.deepEqual([got.status, got.type], [200, "application/octet-stream"]);
  assert.ok(got.body.includes("Bob: Tuesday"));
  const fake = await stranger.goto(base + "/s/" + "A".repeat(43));
  assert.equal(fake.status(), 404);
  assert.equal(await stranger.evaluate(() => document.body.innerText.trim()), "Not found");

  assert.deepEqual(errors, [], "no script errors on any page");
  console.log("share-browser: all good. Screenshots in " + shots);
} finally {
  if (browser) await browser.close();
  server.kill();
}
