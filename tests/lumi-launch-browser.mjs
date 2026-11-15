// Lumi Launch in real Chrome (run by connected-browser.mjs; LUMI_LAUNCH_ONLY=1 runs just this). Every kind of input:
// - checkLumiLaunch: a paid solo go whose three launches are played by the keyboard, by a remote (the TV page's
//   key message, as media.js forwards it), and by a pretend gamepad plus a tap on the course; the server's results and
//   coins; the board; a phone (touch buttons, couch turns on one screen); and 1080p / 4K sizes.
// - checkLumiLaunchTV: couch turns on a paired TV with two real remotes over the Media command stream: each remote's
//   OK joins as its holder, a press out of turn is turned away, three rounds to a winner.
// Screenshots go to runs/casino-browser/lumi-launch-*.png for a person to look at.
import assert from "node:assert/strict";

const wait = ms => new Promise(r => setTimeout(r, ms));
const view = (frame) => frame.evaluate(() => window.LumiLaunchGame.view());
const shot = (frame) => frame.evaluate(() => { const s = window.LumiLaunchGame.shot(); return s && { phase: s.phase, aim: s.aim, manual: s.manual, power: s.power, puffs: s.puffs }; });
const until = (frame, fn, arg, timeout) => frame.waitForFunction(fn, { timeout: timeout || 30000 }, arg);
// The waits ask window.LumiLaunchGame only once game.js has made it: a TV's frame shows its markup a moment before its scripts run.
const phase = (frame, p) => until(frame, want => !!window.LumiLaunchGame && (window.LumiLaunchGame.shot() || {}).phase === want, p);
const at = (frame, v, timeout) => until(frame, want => !!window.LumiLaunchGame && window.LumiLaunchGame.view() === want, v, timeout || 90000);   // a long flight can take a minute

export async function checkLumiLaunch(page, base) {
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto(base + "/media/games/lumi-launch");
  await until(page, () => !document.getElementById("ll-title").hidden && /\d/.test(document.getElementById("ll-coins").textContent));
  const coins = await page.evaluate(async () => (await (await fetch("/api/media/games/lumi-launch/state")).json()).coins);
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-title.png" });

  // Launch 1, by the keyboard: Enter on the focused button starts a go; arrows nudge the needle; Enter locks; Space fires; Enter puffs.
  await page.focus('[data-go="solo"]');
  await page.keyboard.press("Enter");
  await at(page, "play");
  await page.keyboard.press("ArrowUp"); await page.keyboard.press("ArrowUp");
  await until(page, () => window.LumiLaunchGame.shot().manual);
  const aimed = (await shot(page)).aim;
  await wait(200);
  assert.equal((await shot(page)).aim, aimed, "a nudged needle stays put");
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-aim.png" });
  await page.keyboard.press("Enter");
  await phase(page, "power");
  await until(page, () => window.LumiLaunchGame.shot().power >= 70);
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-power.png" });
  await page.keyboard.press("Space");
  await phase(page, "fly");
  await wait(500);
  await page.keyboard.press("Enter");                                          // a sparkle puff
  await until(page, () => window.LumiLaunchGame.shot().puffs < 3 || window.LumiLaunchGame.shot().phase === "done");
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-flying.png" });
  await at(page, "between");
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-landed.png" });

  // Launch 2, by a remote: the TV page posts the remote's keys to the game with its id (media.js games.key).
  const remote = key => page.evaluate(k => window.postMessage({ type: "mnemerynne-game-key", key: k, remote: 7 }, location.origin), key);
  await wait(1000);                                                            // a landing waits a moment before OK moves on
  await remote("Enter");
  await until(page, () => window.LumiLaunchGame.view() === "play" && window.LumiLaunchGame.go().n === 1);
  await remote("ArrowDown");
  await until(page, () => window.LumiLaunchGame.shot().manual);
  await remote("Enter");
  await phase(page, "power");
  await until(page, () => window.LumiLaunchGame.shot().power >= 50);
  await remote("Enter");
  await phase(page, "fly");
  await at(page, "between");

  // Launch 3, by a gamepad (navigator.getGamepads, read every frame by gamepad.js) and a tap on the course to fire.
  await page.evaluate(() => {
    window.testPads = [];
    navigator.getGamepads = () => window.testPads;
    window.testPress = (button) => {                                           // a standard pad holding one button
      window.testPads = [{ index: 0, connected: true, axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, (_, n) => ({ pressed: n === button, value: n === button ? 1 : 0 })) }];
    };
  });
  const pad = async (button) => { await page.evaluate(b => window.testPress(b), button); await wait(120); await page.evaluate(() => { window.testPads = []; }); await wait(120); };
  await wait(1000);
  await pad(0);                                                                // A: the next launch
  await until(page, () => window.LumiLaunchGame.view() === "play" && window.LumiLaunchGame.go().n === 2);
  await pad(12);                                                               // D-pad up: a nudge
  await until(page, () => window.LumiLaunchGame.shot().manual);
  await pad(0);                                                                // A: lock
  await phase(page, "power");
  await until(page, () => window.LumiLaunchGame.shot().power >= 40);
  await page.mouse.click(700, 420);                                            // a click on the course fires
  await phase(page, "fly");
  await pad(9);                                                                // Start pauses mid-flight
  await at(page, "paused");
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-paused.png" });
  await pad(9);                                                                // and carries on
  await at(page, "play");

  // The server flies all three again and pays what it saw.
  await at(page, "results");
  const results = await page.evaluate(() => ({
    title: document.getElementById("ll-results-title").textContent, earned: document.getElementById("ll-results-earned").textContent,
    rows: document.querySelectorAll("#ll-results-list li").length, logs: window.LumiLaunchGame.go().logs.length }));
  assert.equal(results.rows, 3, "three launches listed: " + JSON.stringify(results));
  assert.match(results.title, /m in all$/);
  assert.match(results.earned, /^Paid \d+ coins/, "the go was counted: " + results.earned);
  const after = await page.evaluate(async () => (await (await fetch("/api/media/games/lumi-launch/state")).json()));
  const paid = Number(results.earned.match(/Paid (\d+)/)[1]);
  assert.equal(after.coins, coins - after.entry + paid, "25 coins in, the distance's coins out");
  assert.ok(after.best.go > 0 && after.board.some(r => r.go === after.best.go), "the go is on the board");
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-results.png" });
  await page.click('#ll-results [data-go="menu"]');
  await at(page, "menu");
  await page.click('[data-go="board"]');
  await until(page, () => document.querySelectorAll("#ll-board-list li").length >= 1 && /m/.test(document.getElementById("ll-board-list").textContent));
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-board.png" });
  console.log("lumi-launch: a paid go by keyboard, remote, gamepad and tap; replayed and paid by the server");

  // A phone: one column of buttons, couch turns on one screen, and big touch buttons under her thumbs.
  await page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.reload();
  await until(page, () => !document.getElementById("ll-title").hidden);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, "no sideways scrolling on a phone");
  await page.tap('[data-go="couch"]');
  await at(page, "couch");
  await page.tap('[data-go="couch-here"]'); await page.tap('[data-go="couch-here"]');
  await until(page, () => window.LumiLaunchGame.go().players.length === 2);
  await page.tap('[data-go="couch-start"]');
  await at(page, "play");
  assert.equal(await page.$eval("#ll-touch", e => !e.hidden && getComputedStyle(e).display !== "none"), true, "touch buttons on a phone");
  await page.tap(".ll-touch-ok");
  await phase(page, "power");
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-phone.png" });
  await page.tap(".ll-touch-ok");
  await phase(page, "fly");
  await page.keyboard.press("p");
  await at(page, "paused");
  await page.tap("#ll-quit");
  await at(page, "menu");

  // A TV's sizes: nothing overflows, and the canvas stays sharp.
  for (const [width, height] of [[1920, 1080], [3840, 2160]]) {
    await page.setViewport({ width, height, isMobile: false, hasTouch: false, deviceScaleFactor: 1 });
    await wait(300);
    const fit = await page.evaluate(() => {
      const c = document.getElementById("ll-view");
      return { overflow: document.documentElement.scrollWidth > innerWidth + 1 || document.documentElement.scrollHeight > innerHeight + 1,
               sharp: Math.abs(c.width - c.clientWidth * Math.min(2, devicePixelRatio)) < 3 };
    });
    assert.deepEqual(fit, { overflow: false, sharp: true }, width + "x" + height);
  }
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-4k.png" });
  console.log("lumi-launch: phone touch buttons, couch turns on one screen, 1080p and 4K");
}

export async function checkLumiLaunchTV(page, base) {
  // A TV of its own, and two of this person's remotes paired the way physical panels pair.
  await page.setViewport({ width: 1920, height: 1080, isMobile: false, hasTouch: false, deviceScaleFactor: 1 });
  await page.goto(base + "/media");
  const screen = await page.evaluate(async () => (await fetch("/api/media/screens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Lumi Launch TV" }) })).json());
  async function pair(name) {
    return page.evaluate(async remoteName => {
      const pending = await (await fetch("/media/remote/pair", { method: "POST" })).json();
      const claimed = await fetch("/api/media/screens/claim", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: pending.code, name: remoteName }) });
      if (!claimed.ok) throw new Error("Could not pair test remote");
      return (await (await fetch("/media/remote/pair/status", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ secret: pending.secret }) })).json()).key;
    }, name);
  }
  const pink = await pair("Pink remote"), blue = await pair("Blue remote");
  async function press(token, button) { // a real remote command: the server signs it and sends it down the TV's stream
    const response = await fetch(base + "/api/media/remote/screens/" + screen.id + "/command", {
      method: "POST", headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
      body: JSON.stringify(button === "play" ? { action: "toggle" } : { action: "press", button }) });
    assert.equal(response.status, 200, "the remote's press reaches the TV");
  }
  await page.goto(base + "/media/screen#games");
  await page.waitForFunction(async () => (await (await fetch("/api/media/screens")).json()).screens.some(s => s.online));
  await page.waitForFunction(() => document.querySelectorAll(".mc-card").length === 12);
  await page.evaluate(() => [...document.querySelectorAll(".mc-card")].find(b => b.querySelector(".mc-label").textContent === "Lumi Launch").click());
  const frame = await (await page.waitForSelector("#mc-game-frame")).contentFrame();
  await frame.waitForFunction(() => document.getElementById("gm")?.dataset.game === "lumi-launch" && document.getElementById("gm").dataset.tv === "1");
  await at(frame, "menu");

  // The menu by remote: down to Couch turns, OK.
  await press(pink, "down");
  await frame.waitForFunction(() => document.activeElement && document.activeElement.dataset.go === "couch");
  await press(pink, "enter");
  await at(frame, "couch");
  await frame.waitForFunction(() => window.LumiLaunchGame.go().players.length === 0);
  await press(pink, "enter");                                                  // each remote's OK joins as whoever holds it
  await frame.waitForFunction(() => window.LumiLaunchGame.go().players.length === 1 && !window.LumiLaunchGame.go().players[0].pending);
  await press(blue, "enter");
  await frame.waitForFunction(() => window.LumiLaunchGame.go().players.length === 2 && !window.LumiLaunchGame.go().players[1].pending);
  const names = await frame.evaluate(() => window.LumiLaunchGame.go().players.map(p => p.name));
  assert.equal(names.length, 2);
  assert.ok(names.every(n => !n.startsWith("Player")), "the server named both remotes' holders: " + names);
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-tv-couch.png" });
  await press(pink, "enter");                                                  // a joined remote's OK presses Start
  await at(frame, "play");

  // Three rounds of two turns. A press out of turn is turned away.
  const tokens = [pink, blue];
  for (let turn = 0; turn < 6; turn++) {
    const mine = tokens[turn % 2], theirs = tokens[(turn + 1) % 2];
    await phase(frame, "aim");
    await press(theirs, "enter");
    await frame.waitForFunction(() => /turn!/.test(document.getElementById("ll-toast").textContent) && !document.getElementById("ll-toast").hidden);
    assert.equal((await shot(frame)).phase, "aim", "not their turn: nothing happened");
    await press(mine, "enter");
    await phase(frame, "power");
    await press(mine, "enter");
    await phase(frame, "fly");
    if (turn === 0) { await wait(400); await page.screenshot({ path: "runs/casino-browser/lumi-launch-tv-flying.png" }); }
    await at(frame, "scores");
    if (turn < 5) { await press(theirs, "enter"); await at(frame, "play"); }   // anyone's OK moves on to the next turn
  }
  const final = await frame.evaluate(() => ({ title: document.getElementById("ll-scores-title").textContent,
    next: document.getElementById("ll-scores-next").textContent, rows: document.querySelectorAll("#ll-scores-list li").length,
    launches: window.LumiLaunchGame.go().players.map(p => p.metres.length) }));
  assert.match(final.title, /wins!$|tie/, JSON.stringify(final));
  assert.deepEqual(final.launches, [3, 3]);
  assert.equal(final.next, "Play again");
  await page.screenshot({ path: "runs/casino-browser/lumi-launch-tv-final.png" });
  await page.click("#mc-game-back");
  await page.waitForFunction(() => document.getElementById("mc-game").hidden);
  console.log("lumi-launch: couch turns on a paired TV with two real remotes, joined by OK, three rounds to a winner");
}
