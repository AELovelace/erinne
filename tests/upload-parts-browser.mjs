// Files' big uploads in parts, in a real browser (python/retro_preview.py: a disposable share, alice signed in).
//   PUPPETEER_MODULE=<puppeteer's lib/esm/puppeteer/puppeteer.js> node tests/upload-parts-browser.mjs
// A 40 MB file goes up through the Files page in 16 MB parts (mnemerynne/ingest/parts.py, static/files.js):
// 1. A part's connection is cut (as a sleeping laptop or a restarting server cuts it), and so is the first finish:
//    the page tries again by itself and the file arrives whole, byte for byte.
// 2. Mid-upload the page is reloaded and the same file chosen again: the parts already on the server aren't sent again.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
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
const work = mkdtempSync(join(tmpdir(), "upload-parts-")); // the test files, removed at the end

function bigFile(name, mb) { // a file of random bytes and its sha256
  const data = randomBytes(mb * 1024 * 1024);
  const path = join(work, name);
  writeFileSync(path, data);
  return { path, sha: createHash("sha256").update(data).digest("hex"), size: data.length };
}

async function filesPage(page) { // the signed-in Files page, ready for an upload
  await page.goto(base + "/files");
  await page.waitForSelector("#pick-files", { hidden: true });
  await page.waitForFunction(() => document.querySelector(".fm-toolbar") !== null);
}

async function choose(page, path) { // the same as picking a file in Upload → Files
  const input = await page.$("#pick-files");
  await input.uploadFile(path);
}

async function uploaded(page) { // the queue's row once it's done or failed: {state, detail}
  await page.waitForFunction(() => document.querySelector(".q-item.q-done, .q-item.q-failed, .q-item.q-indexing"),
    { timeout: 120000 });
  return page.$eval(".q-item", li => ({ state: li.className, detail: li.querySelector(".q-detail").textContent }));
}

async function serverSha(page, name) { // the sha256 of the file named `name` as the server sends it back
  return page.evaluate(async n => {
    const files = (await (await fetch("/api/files", { cache: "no-store" })).json()).files;
    const f = files.find(x => x.name === n);
    if (!f) return null;
    const bytes = await (await fetch(f.download_url, { cache: "no-store" })).arrayBuffer();
    return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)))
      .map(b => b.toString(16).padStart(2, "0")).join("");
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
  const page = await browser.newPage();
  page.on("pageerror", e => errors.push(e.stack || e.message));
  await page.goto(base + "/login");
  await page.evaluate(async () => { // the real session sign-in
    const r = await fetch("/login", { method: "POST", body: new URLSearchParams({ username: "alice", password: "preview-password" }) });
    if (!r.ok) throw new Error("Sign-in failed");
  });

  // Each request the page sends for parts, and the cuts this test makes.
  const sent = [];       // "PUT n" / "finish", every attempt
  let cut = new Set(["PUT 1", "finish"]);   // cut once each: the connection drops
  let hold = null;       // a predicate: requests it matches are cut every time (the line is down)
  await page.setRequestInterception(true);
  page.on("request", r => {
    const m = r.url().match(/\/api\/files\/uploads\/[0-9a-f]+\/(?:parts\/(\d+)|(finish))$/);
    if (!m) return r.continue();
    const what = m[1] !== undefined ? "PUT " + m[1] : "finish";
    sent.push(what);
    if (hold && hold(what)) return r.abort("connectionreset");
    if (cut.has(what)) { cut.delete(what); return r.abort("connectionreset"); }
    return r.continue();
  });

  // 1. Cut connections: the upload carries on by itself and arrives whole.
  const one = bigFile("first.bin", 40);    // 3 parts: 16 + 16 + 8 MB
  await filesPage(page);
  await choose(page, one.path);
  const row = await uploaded(page);
  assert.match(row.state, /q-done/, "the upload finished: " + JSON.stringify(row));
  assert.deepEqual(sent, ["PUT 0", "PUT 1", "PUT 1", "PUT 2", "finish", "finish"], "the cut part and finish were sent again");
  assert.equal(await serverSha(page, "first.bin"), one.sha, "the server has the file byte for byte");

  // 2. A reload mid-upload: choosing the same file again sends only the parts that are missing.
  const two = bigFile("second.bin", 40);
  sent.length = 0;
  hold = what => what !== "PUT 0";        // only the first part gets through; the rest wait for the line
  await filesPage(page);
  await choose(page, two.path);
  await page.waitForFunction(() => /trying again/.test(document.querySelector(".q-item .q-detail").textContent),
    { timeout: 60000 });
  assert.ok(sent.includes("PUT 0"), "the first part went up before the line went down");
  page.on("dialog", d => d.accept());     // "uploads are still going": leave anyway
  await page.reload();
  hold = null;
  sent.length = 0;
  await page.waitForSelector("#pick-files", { hidden: true });
  await choose(page, two.path);
  assert.match((await uploaded(page)).state, /q-done/, "the second upload finished after the reload");
  assert.deepEqual(sent, ["PUT 1", "PUT 2", "finish"], "part 0 was already there, so it wasn't sent again");
  assert.equal(await serverSha(page, "second.bin"), two.sha, "the resumed file is byte for byte too");

  assert.deepEqual(errors, [], "no page errors");
  console.log("Uploads in parts: a cut part and a cut finish retried, a reload carried on, both files whole.");
} catch (e) {
  console.error(logs.slice(-4000));
  throw e;
} finally {
  if (browser) await browser.close();
  server.kill();
  rmSync(work, { recursive: true, force: true });
}
