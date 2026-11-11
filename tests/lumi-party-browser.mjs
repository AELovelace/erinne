// Included by connected-browser.mjs: Lumi Dungeons with friends (mnemerynne/lumiparty.py) and Who's playing?
// (mnemerynne/lumi_tv.py) in real browsers, against the disposable preview app.
//  - checkLumiPartyOnline: alice and bob, each in their own browser, make a party, join it, get ready, set out, vote
//    (one vote waits and is marked; two go at once) and walk home together.
//  - checkLumiPartyCouch: a real TV with nobody signed in. bob is alice's TV guest: his own remote's OK shows HIS Lumi
//    (Who's playing?), then both remotes play a couch party on that TV, each as their own account.
import assert from "node:assert/strict";

const row = key => `[data-lp-row="${key}"]`;
const said = (page, words) => page.waitForFunction(w => document.getElementById("lp-message").textContent.includes(w), {}, words);

async function freeLumi(page) { // Earlier checks may have left this Lumi on a trip, napping or playing: bring her home and let her finish.
  await page.evaluate(async () => {
    const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await post("/api/media/games/lumi-pet/dungeon/act", { action: "abandon" });
    await post("/api/media/games/lumi-pet/act", { action: "heal" });
    const mine = (await (await fetch("/api/media/games/lumi-pet/parties")).json()).mine;
    if (mine) await post("/api/media/games/lumi-pet/parties/" + mine, { action: "leave" });
    await post("/api/media/games/lumi-pet/act", { action: "wake" });          // a nap ends at once
  });
  for (let i = 0; i < 240; i++) { // A toy can't be put down: wait until she's done playing (a party won't set out while anyone's Lumi is busy).
    if (!(await page.evaluate(async () => (await (await fetch("/api/media/games/lumi-pet/state")).json()).pet.task))) return;
    await new Promise(done => setTimeout(done, 1000));
  }
}

async function choose(page, key) { // Move the list's cursor onto a row with the keyboard and press Enter.
  for (let i = 0; i < 40 && !(await page.$(`${row(key)}[aria-pressed="true"]`)); i++) await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
}

export async function checkLumiPartyOnline(alice, bob, base) {
  for (const page of [alice, bob]) await freeLumi(page);
  await alice.goto(base + "/media/games/lumi-dungeons");
  await alice.waitForSelector(row("party-new"));
  await alice.click(row("party-new"));
  await alice.waitForSelector(row("party-ready"));
  const code = await alice.evaluate(async () => (await (await fetch("/api/media/games/lumi-pet/parties")).json()).mine);
  assert.match(code, /^[0-9A-F]{8}$/);

  await bob.goto(base + "/media/games/lumi-dungeons");
  await bob.waitForSelector(row("party-join-" + code));
  await bob.click(row("party-join-" + code));
  await bob.waitForSelector(row("party-ready"));
  await alice.waitForFunction(() => document.querySelector('[data-lp-row="party-who"]').textContent.includes("bob")); // her stream saw him join
  await bob.click(row("party-ready"));
  await alice.click(row("party-ready"));
  await alice.waitForFunction(() => document.querySelector('[data-lp-row="party-start"] small')?.textContent === "Everyone's ready").catch(async failed => {
    const seen = await alice.evaluate(async () => ({ // What alice's page shows, and what the server says, so a failure explains itself.
      rows: [...document.querySelectorAll("[data-lp-row]")].map(b => b.dataset.lpRow + ": " + b.textContent).slice(0, 12),
      message: document.getElementById("lp-message").textContent, tab: document.querySelector('[aria-selected="true"]')?.textContent,
      server: await (await fetch("/api/media/games/lumi-pet/parties")).json(), state: (await (await fetch("/api/media/games/lumi-pet/state")).json()).party,
      fight: (await (await fetch("/api/media/games/lumi-pet/state")).json()).fight?.status ?? null,
    }));
    throw new Error("alice never saw everyone ready: " + JSON.stringify(seen, null, 1) + " | " + failed.message);
  });
  await alice.click(row("party-start"));
  for (const page of [alice, bob]) { // Both are out on the same floor, with the party's own line above the dungeon's rows.
    await page.waitForFunction(() => document.getElementById("lp-room").dataset.dungeon === "true").catch(async failed => {
      const seen = await page.evaluate(async () => ({ // The page's party requests (streams, asks) and the server's own view.
        requests: performance.getEntriesByType("resource").filter(e => e.name.includes("parties")).map(e => e.name.replace(location.origin, "") + " " + Math.round(e.startTime) + "ms+" + Math.round(e.duration)),
        server: await (await fetch("/api/media/games/lumi-pet/parties/" + (await (await fetch("/api/media/games/lumi-pet/parties")).json()).mine)).json().then(v => ({ phase: v.phase, revision: v.revision, you: v.you })),
        now: Math.round(performance.now()), message: document.getElementById("lp-message").textContent,
      }));
      throw new Error("a page never set out: " + JSON.stringify(seen) + " | " + failed.message);
    });
    await page.waitForSelector(row("party-status"));
  }

  // One vote waits, marked with who chose it; the second makes a majority of two and the party goes.
  await alice.waitForSelector(row("leave"));
  await alice.click(row("leave"));
  await bob.waitForFunction(() => /alice/.test(document.querySelector('[data-lp-row="leave"]').textContent));
  assert.match(await bob.$eval(row("party-status") + " small", el => el.textContent), /to vote/);
  await bob.click(row("leave"));
  for (const page of [alice, bob]) await page.waitForSelector(row("party-last"));      // home together, back in the lobby
  assert.match(await alice.$eval(row("party-last") + " small", el => el.textContent), /Home together from floor 1/);

  await bob.click(row("party-leave"));                                                // leaving asks twice
  await said(bob, "Choose it once more");
  await bob.click(row("party-leave"));
  await bob.waitForSelector(row("party-new"));
  await alice.waitForFunction(() => /1\/3/.test(document.querySelector('[data-lp-row="party-who"]').textContent));
  await alice.click(row("party-leave"));
  await alice.click(row("party-leave"));
  await alice.waitForSelector(row("party-new"));
  console.log("Lumi Dungeons: an online party is made, joined, readied, votes and walks home together");
}

export async function checkLumiPartyCouch(browser, alice, bob, base) {
  for (const page of [alice, bob]) await freeLumi(page);
  const screen = await alice.evaluate(async () => (await fetch("/api/media/screens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Party TV" }) })).json());
  const added = await alice.evaluate(async () => (await fetch("/api/media/screens/guests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "bob" }) })).status);
  assert.equal(added, 201, "alice makes bob a TV guest");
  const pair = page => page.evaluate(async name => (await (await fetch("/api/media/panel/pair", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) })).json()).key, "Phone " + Math.random().toString(36).slice(2, 7));
  const aliceKey = await pair(alice), bobKey = await pair(bob);
  async function press(key, button) { // A real remote command: the server signs it and sends it down the TV's stream.
    const response = await fetch(base + "/api/media/remote/screens/" + screen.id + "/command", { method: "POST",
      headers: { "Authorization": "Bearer " + key, "Content-Type": "application/json" }, body: JSON.stringify(button === "play" ? { action: "toggle" } : { action: "press", button }) });
    assert.equal(response.status, 200, "the remote reaches the TV (bob's as a TV guest)");
  }
  const screenCookie = (await alice.cookies()).find(c => c.name === "mnemerynne_screen");
  const tvContext = await browser.createBrowserContext(), tv = await tvContext.newPage();  // the TV itself: its screen key, nobody signed in
  await tv.setViewport({ width: 1920, height: 1080 });
  await tv.evaluateOnNewDocument(() => { Object.defineProperty(document, "hidden", { get: () => false }); });
  await tv.setCookie({ name: screenCookie.name, value: screenCookie.value, url: base });
  await tv.goto(base + "/media/screen#games");
  await tv.waitForFunction(async () => (await (await fetch("/api/media/screens")).json()).screens !== undefined).catch(() => {});
  await tv.waitForFunction(() => [...document.querySelectorAll(".mc-card")].some(b => b.querySelector(".mc-label")?.textContent === "Lumi Dungeons"));
  await tv.evaluate(() => [...document.querySelectorAll(".mc-card")].find(b => b.querySelector(".mc-label").textContent === "Lumi Dungeons").click());
  const frame = await (await tv.waitForSelector("#mc-game-frame")).contentFrame();
  await frame.waitForFunction(() => document.getElementById("gm")?.dataset.game === "lumi-dungeons");

  // Who's playing? bob's own remote: his Lumi, on alice's TV.
  await frame.waitForFunction(() => document.getElementById("lp-who") && !document.getElementById("lp-who").hidden);
  await press(bobKey, "enter");
  await frame.waitForFunction(() => document.getElementById("lp-player").textContent === "Playing: bob's Lumi");

  // A couch party on this TV: bob chooses it from his Lumi's list, then each remote's OK seats its own account.
  await frame.waitForSelector(row("party-couch"));
  for (let i = 0; i < 20 && !(await frame.$(`${row("party-couch")}[aria-pressed="true"]`)); i++) { // each press goes through the server: wait for it to land before looking again
    const was = await frame.$eval('[data-lp-row][aria-pressed="true"]', el => el.dataset.lpRow).catch(() => "");
    await press(bobKey, "down");
    await frame.waitForFunction(w => (document.querySelector('[data-lp-row][aria-pressed="true"]') || {}).dataset?.lpRow !== w, {}, was);
  }
  await press(bobKey, "enter");
  await frame.waitForFunction(() => !document.getElementById("lp-couch").hidden);
  await press(bobKey, "enter");
  await frame.waitForFunction(() => document.querySelectorAll(".lp-couch-col").length === 1);
  await press(aliceKey, "enter");
  await frame.waitForFunction(() => document.querySelectorAll(".lp-couch-col").length === 2);
  const names = await frame.$$eval(".lp-couch-col h3", els => els.map(el => el.textContent));
  assert.ok(names[0].startsWith("bob ★") && names[1].startsWith("alice"), "bob sat first, so he is the host: " + names);
  await press(bobKey, "enter");                               // each cursor starts on I'm ready
  await press(aliceKey, "enter");
  await frame.waitForFunction(() => [...document.querySelectorAll(".lp-couch-col h3")].every(h => h.textContent.endsWith("✓")));
  await press(bobKey, "down"); await press(bobKey, "down");   // bob: Ready, Where, Set out!
  await frame.waitForFunction(() => document.querySelector(".lp-couch-col li[aria-current]").textContent.includes("Set out"));
  await press(bobKey, "enter");
  await frame.waitForFunction(() => document.getElementById("lp-room").dataset.dungeon === "true");

  // Everyone votes home from the entrance with their own remote.
  const code = await frame.evaluate(() => sessionStorage.getItem("mnemerynne-lumi-couch"));
  const view = await tv.evaluate(async c => (await fetch("/api/media/games/lumi-pet/parties/" + c)).json(), code);
  assert.equal(view.display, true);
  const home = view.run.party.options.indexOf("home");
  for (const key of [bobKey, aliceKey]) {
    for (let i = 0; i < home; i++) await press(key, "down");
    await press(key, "enter");
  }
  const voted = Date.now();
  await frame.waitForFunction(() => document.querySelector(".lp-couch-head").textContent.startsWith("Party on this TV"));
  assert.ok(Date.now() - voted < 15000, "two votes settle it at once, without waiting for the vote's time to run out");
  assert.equal(await frame.$eval("#lp-message", el => el.textContent), "The party chooses home.");
  assert.ok(!(await frame.$$eval(".lp-couch-col li", els => els.some(li => li.textContent.includes("Press OK again")))), "nobody pressed Leave by accident");
  await tv.screenshot({ path: "runs/casino-browser/lumi-couch-party.png" });

  // The TV's own keyboard ends it (Enter twice), and it's gone for everyone.
  await frame.focus("body").catch(() => {});
  await frame.evaluate(() => window.MGame.tap("enter"));
  await frame.evaluate(() => window.MGame.tap("enter"));
  await frame.waitForFunction(() => document.getElementById("lp-couch").hidden);
  assert.equal((await tv.evaluate(async c => (await fetch("/api/media/games/lumi-pet/parties/" + c)).status, code)), 404);
  await tvContext.close();
  console.log("Who's playing? and a couch party: bob's remote plays his own Lumi on alice's TV, then both remotes vote home together");
}

export async function checkLumiPartyPuzzle(browser, page, base) { // A party's crates, on the real page: the holder's moves go out one by one; a watcher can't move them.
  // Like checkLumiPetDoors: a floor is random, so the page is handed a party trip with a crates door in front of it
  // (its /state, its party stream and its party moves are answered here), and is driven with the keyboard.
  const levels = JSON.parse(await (await import("node:fs/promises")).readFile(new URL("../mnemerynne/lumipet_data/sokoban.json", import.meta.url), "utf8")).levels;
  for (const holder of [1, 2]) {
    const tab = await browser.newPage(), errors = [];
    tab.on("pageerror", error => errors.push(String(error)));
    try {
      await tab.setCookie(...await page.cookies());
      await tab.setViewport({ width: 1440, height: 1000 });
      await tab.evaluateOnNewDocument((rows, holder) => {
        const member = (user, name) => ({ user, name, seat: user - 1, host: user === 1, ready: true, level: 1, glow: "rose", hp: 40, hpMax: 58, mp: 20, mpMax: 22,
          fainted: false, satchel: { coins: 0, potions: 0, trophies: [], mods: [] }, puzzles: 1, vote: null, fighting: false, away: false, you: user === 1, couch: false, remote: null, fight: null });
        window.__sent = [];
        window.__view = function (moves) {
          return { code: "TEST0001", mode: "online", phase: "trip", host: 1, env: "moonlit-shrine", envs: [], you: 1, display: false, last: null, notes: [], revision: 5 + window.__sent.length,
            members: [member(1, "alice"), member(2, "bob")],
            run: { id: 77, env: { id: "moonlit-shrine", name: "Moonlit Shrine", family: "mystical", palette: { wall: "#221133", floor: "#332244", accent: "#ff66aa", glow: "#66ffee" } }, depth: 1, at: "1,1", over: null, ended: null,
              rooms: { "1,1": { kind: "empty", done: true, exits: ["e", "w"] }, "2,1": { kind: "vault", done: false, exits: ["w"] } }, room: { kind: "empty", done: true, exits: ["e", "w"] },
              satchel: { coins: 0, potions: 0, keys: 0, trophies: [], mods: [] }, hp: 40, hpMax: 58, mp: 20, mpMax: 22, event: { scenario: "party_turn" }, say: "Crates!", scene: "A quiet room.", story: ["Crates!"],
              fighting: false, facing: { at: "2,1", lock: { kind: "sokoban", rows } }, game: null,
              party: { code: "TEST0001", options: ["back"], votes: {}, ends: null, fighting: false, words: { back: "a step back from the door" },
                puzzle: { kind: "sokoban", phase: "attempt", order: [holder, 3 - holder], turn: 0, holder, door: true, used: 0, moves: moves || "", presses: [], limit: { kind: "seconds", count: 180 }, ends: Date.now() + 170000 } } } };
        };
        const real = window.fetch;
        window.fetch = async function (url, options) {
          if (String(url).includes("/parties/TEST0001")) {
            const body = options && options.body ? JSON.parse(options.body) : null;
            if (body) window.__sent.push(body);
            return new Response(JSON.stringify(window.__view(body && body.moves)), { status: 200, headers: { "Content-Type": "application/json" } });
          }
          const answer = await real(url, options);
          if (!String(url).endsWith("/lumi-pet/state")) return answer;
          const data = await answer.json();
          data.fight = null; data.party = { code: "TEST0001", phase: "trip", mode: "online" }; data.dungeon.run = window.__view("").run;
          return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
        };
        window.EventSource = function () { // The party's stream: one "party" event with the view above.
          const listeners = {};
          setTimeout(() => (listeners.party || []).forEach(fn => fn({ data: JSON.stringify(window.__view(holder === 1 ? "" : "r")) })), 50);
          return { addEventListener: (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); }, close() {} };
        };
      }, levels[0].rows, holder);
      await tab.goto(base + "/media/games/lumi-dungeons");
      await tab.waitForSelector('[data-lp-row="party-status"]');
      if (holder === 1) { // alice's turn: each arrow is sent as the moves so far; Pass it on hands it on.
        assert.ok(await tab.$('[data-lp-row="party-pass"]') && await tab.$('[data-lp-row="play"]'), "the holder has the crates and Pass it on");
        for (const key of ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"]) await tab.keyboard.press(key);
        await tab.waitForFunction(() => window.__sent.some(b => b.action === "sokoban"));
        const sent = await tab.evaluate(() => window.__sent);
        assert.ok(sent.every(b => b.action === "sokoban" && /^[udlr]*$/.test(b.moves)), JSON.stringify(sent));
        assert.ok(!sent.some(b => b.action === "move"), "arrows at a party's puzzle never walk (or vote)");
        await tab.evaluate(() => { window.__sent = []; });
        await tab.click('[data-lp-row="party-pass"]');
        await tab.waitForFunction(() => window.__sent.some(b => b.action === "pass"));
      } else { // bob's turn: alice watches; her arrows say whose turn it is and send nothing.
        assert.ok(await tab.$('[data-lp-row="party-watch"]') && !(await tab.$('[data-lp-row="play"]')), "a watcher has no crates controls");
        await tab.keyboard.press("ArrowRight");
        await tab.waitForFunction(() => document.getElementById("lp-message").textContent.includes("It's bob's turn"));
        assert.deepEqual(await tab.evaluate(() => window.__sent), []);
      }
      assert.deepEqual(errors, []);
    } finally {
      await tab.close();
    }
  }
  console.log("Lumi Dungeons: at a party's crates the holder's moves go out one by one, and a watcher can't move them");
}

export async function checkLumiPartyFight(browser, page, base) { // The co-op fight on the real page: everyone's Lumi, whose turn, aiming, an item for a friend.
  // Like the puzzle check: a fight in a party can't be walked into on purpose (the floor is random), so the page is
  // handed a party view with a co-op fight going on, and its moves are caught here and answered with the next turn.
  const tab = await browser.newPage(), errors = [];
  tab.on("pageerror", error => errors.push(String(error)));
  try {
    await tab.setCookie(...await page.cookies());
    await tab.setViewport({ width: 1440, height: 1000 });
    await tab.evaluateOnNewDocument(() => {
      const member = (user, name, pet, colour) => ({ user, name, pet, colour, outfit: user === 2 ? { head: "acc-bow" } : {}, seat: user - 1, host: user === 1, ready: true, level: 3, glow: "lagoon", hp: 40, hpMax: 58, mp: 20, mpMax: 22,
        fainted: false, satchel: { coins: 0, potions: 0, trophies: [], mods: [] }, puzzles: 0, vote: null, fighting: true, away: false, you: user === 1, couch: false, remote: null, fight: null });
      const lumi = (user, name, hp) => ({ user, name, level: 3, element: "ice", hp, hpMax: 58, mp: 20, mpMax: 22, status: {}, stages: user === 3 ? { attack: 1 } : {}, guarding: false, gone: false,
        pocket: user === 1 ? { potion: 2, ether: 1, "x-attack": 0, "x-defend": 0, "x-speed": 0 } : {}, moves: [{ name: "Pounce", mp: 0, element: "none", magic: false, power: 50 }, { name: "Glow Spark", mp: 0, element: "ice", magic: true, power: 45 }] });
      const foe = (name, hp) => ({ id: "dust-bunny", name, tier: "common", body: "blob", colour: "#c9a8ff", level: 3, element: "wind", hp, hpMax: 30, status: {}, stages: {}, weak: [], resist: [] });
      window.__sent = [];
      window.__view = function () {
        const at = window.__sent.length ? 1 : 0;                 // once alice has acted, it's jane's turn
        return { code: "TEST0002", mode: "online", phase: "trip", host: 1, env: "moonlit-shrine", envs: [], you: 1, display: false, last: null, notes: [], revision: 9 + window.__sent.length,
          members: [member(1, "alice", "Lumi", null), member(2, "jane", "Twinkle", "#33ff99"), member(3, "mina", "Lumi", null)],
          run: { id: 78, env: { id: "moonlit-shrine", name: "Moonlit Shrine", family: "mystical", palette: { wall: "#221133", floor: "#332244", accent: "#ff66aa", glow: "#66ffee" } }, depth: 1, at: "2,1", over: null, ended: null,
            rooms: { "2,1": { kind: "enemy", done: false, exits: ["w"] } }, room: { kind: "enemy", done: false, exits: ["w"] },
            satchel: { coins: 0, potions: 0, keys: 0, trophies: [], mods: [] }, hp: 40, hpMax: 58, mp: 20, mpMax: 22, event: { scenario: "enemy", enemy: "Dust Bunny" }, say: "A pack appears!", scene: "Moonlight on stone.", story: ["A pack appears!"],
            fighting: true, facing: null, game: null,
            party: { code: "TEST0002", options: [], votes: {}, ends: null, fighting: true, words: {}, puzzle: null, last_fight: null,
              fight: { lumis: [lumi(1, "alice's Lumi", 40), lumi(2, "Twinkle", 12), lumi(3, "mina's Lumi", 50)], foes: [foe("Dust Bunny A", 30), foe("Dust Bunny B", 18)],
                round: 1, at, turn: 4 + window.__sent.length, log: ["A pack appears: Dust Bunny A, Dust Bunny B!"].concat(window.__sent.length ? ["alice's Lumi used Pounce: 9 damage."] : []), outcome: null, ends: Date.now() + 40000, you: 0 } } } };
      };
      const real = window.fetch;
      window.fetch = async function (url, options) {
        if (String(url).includes("/parties/TEST0002")) {
          const body = options && options.body ? JSON.parse(options.body) : null;
          if (body) window.__sent.push(body);
          return new Response(JSON.stringify(window.__view()), { status: 200, headers: { "Content-Type": "application/json" } });
        }
        const answer = await real(url, options);
        if (!String(url).endsWith("/lumi-pet/state")) return answer;
        const data = await answer.json();
        data.fight = null; data.party = { code: "TEST0002", phase: "trip", mode: "online" }; data.dungeon.run = window.__view().run;
        return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
      };
      window.EventSource = function () {
        const listeners = {};
        setTimeout(() => (listeners.party || []).forEach(fn => fn({ data: JSON.stringify(window.__view()) })), 50);
        return { addEventListener: (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); }, close() {} };
      };
    });
    await tab.goto(base + "/media/games/lumi-dungeons");
    await tab.waitForSelector(row("fight-aim"));
    // Everyone is on screen: the team's bars, both creatures, and her two friends' own Lumis beside hers.
    assert.equal(await tab.$eval("#lp-team", el => el.hidden), false);
    assert.deepEqual(await tab.$$eval("#lp-team-lumis li", lis => lis.map(li => [li.dataset.turn, li.dataset.you])), [["true", "true"], ["false", "false"], ["false", "false"]]);
    assert.equal((await tab.$$("#lp-team-foes figure")).length, 2, "two creatures, each drawn");
    assert.deepEqual(await tab.$$eval("#lp-crew figure figcaption", caps => caps.map(c => c.textContent)), ["Twinkle · jane", "mina"]);
    assert.match(await tab.$eval("#lp-crew figure", el => el.querySelector("svg g").getAttribute("style")), /--pet-glow:#33ff99/, "jane's Lumi in her own colour");
    assert.ok(await tab.$('#lp-crew figure [data-acc="head"]'), "and in her own bow");
    assert.equal(await tab.$eval(".lp-fight-bars", el => getComputedStyle(el).display), "none", "the solo fight's bars step aside");
    const sizes = await tab.evaluate(() => [document.querySelector(".lp-pet-wrap"), ...document.querySelectorAll(".lp-crew-lumi")].map(el => Math.round(el.getBoundingClientRect().width)));
    assert.ok(sizes.every(w => Math.abs(w - sizes[0]) <= 4), "every Lumi in the party stands the same size in the row: " + sizes);
    // Aim: Enter (or a click) on the Aim row aims at the next creature; a click on a creature aims at it.
    assert.match(await tab.$eval(row("fight-aim"), el => el.textContent), /Aim at: Dust Bunny A/);
    await tab.click(row("fight-aim"));
    await tab.waitForFunction(() => /Dust Bunny B/.test(document.querySelector('[data-lp-row="fight-aim"]').textContent));
    assert.deepEqual(await tab.$$eval("#lp-team-foes figure", figs => figs.map(f => f.dataset.aim)), ["false", "true"]);
    await tab.click('#lp-team-foes figure[data-click="aim:0"]');
    await tab.waitForFunction(() => /Dust Bunny A/.test(document.querySelector('[data-lp-row="fight-aim"]').textContent));
    // An item for a friend: ◀ ▶ on the potion row chooses who it's for.
    assert.match(await tab.$eval(row("potion"), el => el.textContent), /Potion \(2\) for yourself/);
    for (let i = 0; i < 12 && !(await tab.$(row("potion") + '[aria-pressed="true"]')); i++) await tab.keyboard.press("ArrowDown");
    await tab.keyboard.press("ArrowRight");
    await tab.waitForFunction(() => /for Twinkle/.test(document.querySelector('[data-lp-row="potion"]').textContent));
    await tab.keyboard.press("Enter");
    await tab.waitForFunction(() => window.__sent.length === 1);
    assert.deepEqual(await tab.evaluate(() => window.__sent[0]), { action: "fight", target: "potion", ally: 1, turn: 4 });
    // Jane's turn now: alice waits, and her arrows send nothing (no walking, no votes, mid-fight).
    await tab.waitForSelector(row("fight-wait"));
    assert.match(await tab.$eval(row("fight-wait"), el => el.textContent), /Twinkle is choosing/);
    assert.equal(await tab.$(row("move0")), null);
    await tab.keyboard.press("ArrowUp"); await tab.keyboard.press("ArrowLeft");
    assert.equal(await tab.evaluate(() => window.__sent.length), 1);
    assert.match(await tab.$eval("#lp-message", el => el.textContent), /used Pounce/);
    await tab.screenshot({ path: "runs/casino-browser/lumi-party-fight.png", fullPage: true });
    assert.deepEqual(errors, []);
  } finally { if (errors.length) console.log("Page errors:", errors.join(" | ")); await tab.close(); }
  console.log("Lumi Dungeons: a co-op fight draws every Lumi and creature, aims by key and mouse, gives a friend a potion, and waits its turn");
}

export async function checkLumiPartyBrowser(browser, page, base) { // The open-party browser: every open party drawn with its Lumis; arrows move, Enter joins.
  const tab = await browser.newPage(), errors = [];
  tab.on("pageerror", error => errors.push(String(error)));
  try {
    await tab.setCookie(...await page.cookies());
    await tab.setViewport({ width: 1440, height: 1000 });
    await tab.evaluateOnNewDocument(() => {
      const lumi = (name, pet, glow, colour, level) => ({ name, pet, glow, colour, outfit: {}, level, ready: false });
      window.__joined = [];
      const real = window.fetch;
      window.fetch = async function (url, options) {
        if (String(url).endsWith("/lumi-pet/parties") && !(options && options.body)) return new Response(JSON.stringify({ mine: null, parties: [
          { code: "AAAA1111", mode: "online", phase: "lobby", mine: false, host: "jane", members: ["jane"], lumis: [lumi("jane", "Twinkle", "lagoon", "#33ff99", 7)], env: "Moonlit Shrine", screen: "", max: 3 },
          { code: "BBBB2222", mode: "couch", phase: "lobby", mine: false, host: "mina", members: ["mina", "<b>sam</b>"], lumis: [lumi("mina", "Lumi", "rose", null, 3), lumi("<b>sam</b>", "Lumi", "umbra", null, 2)], env: "Crystal Cavern", screen: "Lounge TV", max: 3 }] }),
          { status: 200, headers: { "Content-Type": "application/json" } });
        if (/\/lumi-pet\/parties\/[A-Z0-9]+$/.test(String(url)) && options && options.body) { window.__joined.push(String(url).split("/").pop()); return new Response(JSON.stringify({ detail: "x" }), { status: 400, headers: { "Content-Type": "application/json" } }); }
        return real(url, options);
      };
    });
    await tab.goto(base + "/media/games/lumi-dungeons");
    await tab.waitForSelector(row("party-browse"));
    await tab.waitForFunction(() => /2 parties are looking/.test(document.querySelector('[data-lp-row="party-browse"]').textContent));
    await tab.click(row("party-browse"));
    await tab.waitForFunction(() => document.getElementById("lp-room").dataset.browse === "true");
    assert.equal((await tab.$$("#lp-dungeon .lp-pb-card")).length, 2, "a card for each open party");
    assert.equal((await tab.$$("#lp-dungeon .lp-pb-card svg")).length, 3, "every Lumi in them is drawn");
    assert.ok(await tab.$eval("#lp-dungeon", el => el.textContent.includes("<b>sam</b>") && !el.querySelector("b")), "people's names are shown as text, never markup");
    assert.equal(await tab.$eval(".lp-tabs", el => getComputedStyle(el).display), "none", "the browser has the panel to itself");
    assert.ok(await tab.$(row("party-join-AAAA1111") + '[aria-pressed="true"]'));
    await tab.keyboard.press("ArrowRight");                       // the arrows move over the cards, and the list with them
    assert.ok(await tab.$(row("party-join-BBBB2222") + '[aria-pressed="true"]'));
    assert.ok(await tab.$eval('#lp-dungeon [data-click="join:BBBB2222"] rect', el => el.getAttribute("stroke") === "#70ebea"));
    await tab.keyboard.press("Enter");
    await tab.waitForFunction(() => window.__joined.length === 1);
    assert.deepEqual(await tab.evaluate(() => window.__joined), ["BBBB2222"]);
    await tab.screenshot({ path: "runs/casino-browser/lumi-party-browser.png", fullPage: true });
    await tab.click(row("party-back"));
    await tab.waitForFunction(() => document.getElementById("lp-room").dataset.browse === "false");
    assert.deepEqual(errors, []);
  } finally { if (errors.length) console.log("Page errors:", errors.join(" | ")); await tab.close(); }
  console.log("Lumi Dungeons: the open-party browser draws every party and its Lumis, moves by arrows, and Enter joins");
}
