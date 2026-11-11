// The service worker shows fixed words for known reminders and nothing a push tries to inject.
// Ported from MommyBot's scripts/check-little-log-pet-worker.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

function worker(windows = []) { // Run sw.js in a stand-in service worker scope and collect what it does.
  const handlers = {}, shown = [], opened = [], focused = [];
  const self = {
    registration: { showNotification: async (...args) => { shown.push(args); } },
    addEventListener: (name, fn) => { handlers[name] = fn; }, skipWaiting() {},
    clients: { claim: async () => {}, openWindow: async url => { opened.push(url); },
      matchAll: async () => windows.map(url => ({ url, focus: async () => { focused.push(url); } })) },
  };
  vm.runInNewContext(readFileSync(new URL("../../mnemerynne/web/static/sw.js", import.meta.url), "utf8"), { self, URL });
  const push = async payload => { let work; handlers.push({ data: { json: () => payload }, waitUntil: p => { work = p; } }); await work; };
  const click = async data => { let work; handlers.notificationclick({ notification: { data, close() {} }, waitUntil: p => { work = p; } }); await work; };
  return { handlers, shown, opened, focused, push, click };
}

test("each known need shows Lumi's own words and nothing else", async () => {
  const sw = worker();
  for (const need of ["hunger", "energy", "joy", "nap"]) await sw.push({ kind: "lumi-pet", need });
  assert.equal(sw.shown.length, 4);
  assert.ok(sw.shown.every(([title, options]) => title === "Lumi" && options.tag === "lumi-pet" && options.body.includes("Lumi")));
  assert.match(sw.shown[0][1].body, /tummy/);
});

test("injected text, links and unknown kinds are ignored", async () => {
  const sw = worker();
  await sw.push({ kind: "lumi-pet", need: "hunger", title: "Injected", body: "Private record", url: "https://evil.example", page: "https://evil.example" });
  assert.equal(sw.shown[0][0], "Lumi");
  assert.equal(JSON.stringify(sw.shown[0]).includes("evil"), false);
  assert.equal(JSON.stringify(sw.shown[0]).includes("Private"), false);
  for (const payload of [{ kind: "lumi-pet", need: "__proto__" }, { kind: "lumi-pet", need: "constructor" }, { kind: "other", need: "hunger" }, {}, null, "text"]) await sw.push(payload);
  sw.handlers.push({ data: { json() { throw new Error("not json"); } }, waitUntil() {} });
  assert.equal(sw.shown.length, 1);
});

test("a tap opens Lumi, focusing her page if it's already open, and never a pushed address", async () => {
  const fresh = worker();
  await fresh.click({ page: "/media/games/lumi-pet" });
  assert.deepEqual(fresh.opened, ["/media/games/lumi-pet"]);
  await fresh.click({ page: "https://evil.example" });
  await fresh.click(null);
  assert.deepEqual(fresh.opened.slice(1), ["/", "/"]);
  const open = worker(["https://nas.example/files", "https://nas.example/media/games/lumi-pet"]);
  await open.click({ page: "/media/games/lumi-pet" });
  assert.deepEqual(open.focused, ["https://nas.example/media/games/lumi-pet"]);
  assert.deepEqual(open.opened, []);
});

test("there is no fetch handler, so pages always load as they would without it", () => {
  assert.deepEqual(Object.keys(worker().handlers).sort(), ["activate", "install", "notificationclick", "push"]);
});

test("the garden reminder has its own words and opens Lumi's Garden, an app of its own", async () => {
  const sw = worker();
  await sw.push({ kind: "lumi-pet", need: "garden", page: "https://evil.example" });
  assert.match(sw.shown[0][1].body, /flowers are ready to trim/);
  assert.equal(sw.shown[0][1].data.page, "/media/games/lumi-garden");
  await sw.click(sw.shown[0][1].data);
  assert.deepEqual(sw.opened, ["/media/games/lumi-garden"]);
  const already = worker(["https://nas.example/media/games/lumi-pet", "https://nas.example/media/games/lumi-garden"]);
  await already.click({ page: "/media/games/lumi-garden" });
  assert.deepEqual([already.focused, already.opened.length], [["https://nas.example/media/games/lumi-garden"], 0], "An open garden is brought forward, not opened twice, and never Lumi Pet instead");
  const old = worker(); // A reminder shown before the garden moved out of Lumi Pet still opens: her page sends it on (game.js).
  await old.click({ page: "/media/games/lumi-pet#garden" });
  assert.deepEqual(old.opened, ["/media/games/lumi-pet#garden"]);
  const odd = worker();
  await odd.click({ page: "/media/games/lumi-pet#elsewhere" });
  assert.deepEqual(odd.opened, ["/"], "Only an address that shipped with the worker is ever opened");
});
