// Two distinct bearer-authenticated remote panels drive a single real TV stream and game iframe.
import assert from "node:assert/strict";

export async function checkSharedTV(page, screen, base) {
  async function pair(name) { // Pair disposable remote identities through the same flow used by physical panels.
    return page.evaluate(async remoteName => {
      const pending = await (await fetch("/media/remote/pair", { method: "POST" })).json();
      const claimed = await fetch("/api/media/screens/claim", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: pending.code, name: remoteName }) });
      if (!claimed.ok) throw new Error("Could not pair test remote");
      const ready = await (await fetch("/media/remote/pair/status", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ secret: pending.secret }) })).json();
      return ready.key;
    }, name);
  }
  const pink = await pair("Pink remote"), blue = await pair("Blue remote"), mint = await pair("Mint remote");
  async function press(token, button) { // No signed-in cookie accompanies panel commands; each bearer key identifies its player.
    const response = await fetch(base + "/api/media/remote/screens/" + screen.id + "/command", {
      method: "POST", headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
      body: JSON.stringify(button === "play" ? { action: "toggle" } : { action: "press", button }),
    });
    assert.equal(response.status, 200, "Authenticated remote command must reach the TV");
  }
  for (const [slug, name] of [["blackjack", "Blackjack"], ["roulette", "Roulette"], ["horse-racing", "Horse Racing"], ["slots", "Slot Machines"]]) {
    await page.click("#mc-game-back");
    await page.waitForFunction(() => document.getElementById("mc-game").hidden);
    await page.evaluate(gameName => [...document.querySelectorAll(".mc-card")].find(b => b.querySelector(".mc-label").textContent === gameName).click(), name);
    let frame = await (await page.waitForSelector("#mc-game-frame")).contentFrame();
    await frame.waitForFunction(game => document.getElementById("gm")?.dataset.game === game, {}, slug);
    await frame.waitForFunction(() => !sessionStorage.getItem("casino-room:" + document.getElementById("gm").dataset.game) || !document.getElementById("casino-table").hidden);
    if (await frame.$eval("#casino-table", el => !el.hidden)) { // Earlier single-seat tests intentionally preserve their resume pointers.
      await frame.click("#casino-leave");
      await frame.waitForFunction(() => !document.getElementById("casino-lobby").hidden);
    }
    await frame.waitForSelector("#casino-create-tv:not([disabled])", { visible: true });
    await frame.click("#casino-create-tv");
    await frame.waitForFunction(() => !document.getElementById("casino-tv-guide").hidden);
    const code = await frame.evaluate(game => sessionStorage.getItem("casino-room:" + game), slug);
    const endpoint = "/api/media/games/" + slug + "/rooms/" + code + "/tv";
    async function snapshot() { return page.evaluate(async path => (await fetch(path)).json(), endpoint); }
    await press(pink, "enter");
    await frame.waitForFunction(() => document.querySelectorAll(".casino-controller").length === 1);
    await press(blue, "enter");
    await frame.waitForFunction(() => document.querySelectorAll(".casino-controller").length === 2);
    assert.deepEqual((await snapshot()).players.map(p => p.name), ["Pink remote", "Blue remote"]);
    await Promise.all([press(pink, "up"), press(blue, "right")]);
    await frame.waitForFunction(() => document.querySelector(".casino-draft").textContent.includes("60"));
    await Promise.all([press(pink, "enter"), press(blue, "enter")]);
    await frame.waitForFunction(() => [...document.querySelectorAll(".casino-controller")].every(p => p.textContent.includes("update bet")));
    let state = await snapshot();
    assert.deepEqual(state.players.map(p => p.bet), [60, 50]);
    assert.deepEqual(state.players.map(p => p.chips), slug === "slots" ? [820, 850] : [940, 950]);
    if (["roulette", "horse-racing"].includes(slug)) assert.notEqual(state.players[0].choice, state.players[1].choice);
    await press(blue, "play");
    await frame.waitForFunction(() => document.querySelector(".casino-seat-error")?.textContent.includes("host"));
    assert.equal((await snapshot()).phase, "betting");
    await Promise.all([frame.waitForNavigation({ waitUntil: "load" }), frame.evaluate(() => location.reload())]);
    await frame.waitForFunction(() => !document.getElementById("casino-tv-guide").hidden && document.querySelectorAll(".casino-controller").length === 2);
    assert.deepEqual((await snapshot()).players.map(p => p.bet), [60, 50]);
    if (slug === "roulette") { // A completely fresh tab can recover its own full display from the lobby.
      await Promise.all([frame.waitForNavigation({ waitUntil: "load" }), frame.evaluate(game => {
        sessionStorage.removeItem("casino-room:" + game); sessionStorage.removeItem("casino-view:" + game); location.reload();
      }, slug)]);
      await frame.waitForSelector('[data-room="' + code + '"]');
      await frame.click('[data-room="' + code + '"]');
      await frame.waitForFunction(() => !document.getElementById("casino-tv-guide").hidden && document.querySelectorAll(".casino-controller").length === 2);
    }
    await press(pink, "play");
    await frame.waitForFunction(() => document.getElementById("casino-phase").textContent !== "Place your bets");
    await press(mint, "enter");
    await frame.waitForFunction(() => document.querySelectorAll(".casino-controller").length === 3);
    assert.equal((await snapshot()).players[2].bet, 0);
    if (slug === "blackjack") {
      for (const [index, token] of [[0, pink], [1, blue]]) {
        state = await snapshot();
        if (state.phase === "playing" && !state.players[index].stood) {
          await press(token, "right");
          await frame.waitForFunction(i => document.querySelectorAll(".casino-controller")[i].textContent.includes("[STAND]"), {}, index);
          await press(token, "enter");
        }
      }
    }
    await frame.waitForFunction(() => document.getElementById("casino-phase").textContent === "Round complete", { timeout: 15000 });
    if (slug === "slots") assert.equal(await frame.$$eval(".casino-controller .casino-slot-symbol", cells => cells.length), 27);
    if (slug === "slots") {
      const controllers = [pink, blue, mint];
      for (let i = 4; i <= 8; i++) { const token = await pair("Remote " + i); controllers.push(token); await press(token, "enter"); }
      await frame.waitForFunction(() => document.querySelectorAll(".casino-controller").length === 8);
      assert.ok(await frame.$$eval(".casino-controller", cards => cards.every(card => card.getBoundingClientRect().bottom <= innerHeight)), "Eight slot-player cards must be visible on the TV without scrolling");
      await page.screenshot({ path: "runs/casino-browser/slots-one-tv-results.png", fullPage: true });
      await frame.waitForFunction(() => document.getElementById("casino-phase").textContent === "Place your bets", { timeout: 15000 });
      await Promise.all(controllers.map(token => press(token, "enter")));
      await frame.waitForFunction(() => [...document.querySelectorAll(".casino-controller")].every(p => p.textContent.includes("update bet")));
      assert.ok(await frame.$$eval(".casino-controller", cards => cards.every(card => card.getBoundingClientRect().bottom <= innerHeight)), "All eight players' betting controls must fit on the TV");
      await page.setViewport({ width: 3840, height: 2160 });
      await frame.waitForFunction(() => parseFloat(getComputedStyle(document.documentElement).fontSize) === 32);
      assert.ok(await frame.$$eval(".casino-controller", cards => cards.every(card => card.getBoundingClientRect().bottom <= innerHeight)), "All eight scaled player cards fit the 4K TV");
      assert.equal(await frame.$eval(".casino-player p", el => parseFloat(getComputedStyle(el).fontSize)), 27.2);
      await page.screenshot({ path: "runs/casino-browser/slots-eight-remotes-4k.png", fullPage: true });
      await page.setViewport({ width: 1440, height: 1000 });
      await frame.waitForFunction(() => parseFloat(getComputedStyle(document.documentElement).fontSize) === 16);
    }
    await page.screenshot({ path: `runs/casino-browser/${slug}-one-tv.png`, fullPage: true });
    await page.setViewport({ width: 390, height: 844 });
    assert.ok(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Shared-TV cards must fit narrow displays");
    await page.setViewport({ width: 1440, height: 1000 });
    const count = (await snapshot()).players.length;
    await frame.click(".casino-controller .casino-remove");
    await frame.waitForFunction(n => document.querySelectorAll(".casino-controller").length === n - 1, {}, count);
    state = await snapshot();
    assert.equal(state.players[0].name, "Blue remote");
    assert.ok(await frame.$eval(".casino-controller h3", e => e.textContent.includes("Host")));
    await frame.click("#casino-leave");
    await frame.waitForFunction(() => !document.getElementById("casino-lobby").hidden);
    console.log(`${slug}: one TV, three paired remotes, independent bets, host-only Play, refresh, late join, results and host transfer passed`);
  }
}
