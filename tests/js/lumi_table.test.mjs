// Lumi's Lounge (static/games/casino/lumi-table.js): Dealer Lumi, each player's Lumi in her seat, chips and ponies.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const closet = require("../../mnemerynne/web/static/games/lumi-pet/closet.js");
globalThis.LumiPetCloset = closet;                                       // as the page has it: closet.js loads first
const T = require("../../mnemerynne/web/static/games/casino/lumi-table.js");
const glows = readFileSync(new URL("../../mnemerynne/lumibattle.py", import.meta.url), "utf8");

test("every glow Lumi can have is a palette here, with the same colours as Lumi Pet", () => {
  const css = readFileSync(new URL("../../mnemerynne/web/static/games/lumi-pet/game.css", import.meta.url), "utf8");
  for (const [name, p] of Object.entries(T.PALETTES)) {
    assert.ok(glows.includes('"' + name + '"'), name + " is one of lumibattle's glows");
    if (name !== "rose") assert.match(css, new RegExp('data-palette="' + name + '"\\] \\{ --pet-glow: ' + p.glow + "; --pet-body: " + p.body + "; --pet-shadow: " + p.shadow));
  }
  assert.match(css, new RegExp("--pet-glow: " + T.PALETTES.rose.glow + "; --pet-body: " + T.PALETTES.rose.body + "; --pet-shadow: " + T.PALETTES.rose.shadow));
});

test("a seat's Lumi wears her own glow; plain seats take turns; nonsense falls back", () => {
  assert.equal(T.glowOf({ look: { glow: "lagoon" } }, 0), "lagoon");
  assert.deepEqual([0, 1, 2, 6].map(i => T.glowOf({}, i)), ["rose", "lagoon", "starlight", "rose"]);
  assert.equal(T.glowOf({ look: { glow: "constructor" } }, 1), "lagoon");          // not a palette, even if it's an Object key
  assert.equal(T.glowOf(null, 4), "wisp");
});

test("she is drawn in her glow and her clothes, and only known clothes are drawn", () => {
  const svg = T.lumi({ glow: "starlight", outfit: { head: "acc-crown", neck: "<script>" } }, { mood: "happy" });
  assert.match(svg, /--pet-glow:#ffdc83;--pet-body:#fff1cd;--pet-shadow:#b98b49/);
  assert.match(svg, /data-mood="happy"/);
  assert.ok(svg.includes('data-acc="head"'), "the crown is on");
  assert.ok(!svg.includes("<script") && !svg.includes('data-acc="neck"'), "an unknown id draws nothing");
  assert.match(T.lumi({ glow: '"><img', outfit: null }, { mood: "nope" }), /data-mood="idle".*--pet-glow:#ff8ecb/s);
  assert.ok(!/<text|<title/.test(svg), "no words inside: every viewer's seats read the same");
});

test("Dealer Lumi is lilac, in her visor and bow tie, and her mood follows the round", () => {
  const svg = T.dealer("deal");
  assert.match(svg, /lt-dealer/); assert.match(svg, /--pet-glow:#c9a8ff/); assert.match(svg, /lt-dealer-cards/);
  assert.deepEqual(["betting", "playing", "results"].map(phase => T.dealerMood({ phase })), ["ready", "deal", "cheer"]);
  assert.equal(T.dealerMood(null), "cheer");
});

test("a seat's mood: ready with a bet, happy on a win, a sweat drop on a loss, calm on a push", () => {
  const seat = (bet, payout) => ({ bet, payout });
  assert.equal(T.seatMood({ phase: "betting" }, seat(0, 0), "roulette"), "idle");
  assert.equal(T.seatMood({ phase: "betting" }, seat(50, 0), "roulette"), "ready");
  assert.equal(T.seatMood({ phase: "results" }, seat(50, 100), "blackjack"), "happy");
  assert.equal(T.seatMood({ phase: "results" }, seat(50, 50), "blackjack"), "idle");
  assert.equal(T.seatMood({ phase: "results" }, seat(50, 0), "horse-racing"), "aww");
  assert.equal(T.seatMood({ phase: "results", slot_lines: 3 }, seat(10, 20), "slots"), "aww");    // 30 staked over three lines
  assert.equal(T.seatMood({ phase: "results", slot_lines: 3 }, seat(10, 40), "slots"), "happy");
});

test("chips: biggest first, one stack per colour, never taller than six", () => {
  assert.deepEqual(T.chipCounts(780).map(c => [c[0], c[2]]), [[500, 1], [100, 2], [50, 1], [10, 3]]);
  assert.deepEqual(T.chipCounts(0), []);
  assert.equal(T.chips(0), "");
  const pile = T.chips(3000);                                                          // six 500s
  assert.equal((pile.match(/stroke-dasharray/g) || []).length, 6);
  assert.match(T.chips(60), /viewBox="0 0 64 /);                                        // two stacks: a 50 and a 10
});

test("six ponies, one per horse, and the dealer's lines are the same for everyone at the table", () => {
  const ponies = [0, 1, 2, 3, 4, 5].map(T.pony);
  assert.equal(new Set(ponies).size, 6);
  assert.equal(T.pony(6), T.pony(0));
  const st = { phase: "results", round: 3, result: { order: [3, 0, 1, 2, 4, 5] } };
  assert.equal(T.say("horse-racing", st, null), "Cherry Comet wins by a whisker!");
  assert.equal(T.say("horse-racing", st, { bet: 50, payout: 250 }), "Cherry Comet wins by a whisker! Yay, you won! ♡");
  assert.equal(T.say("blackjack", { phase: "results", result: { dealer_total: 23 } }, { bet: 50, payout: 0 }), "Oopsie, I bust at 23! Aww, next time!");
  assert.equal(T.say("roulette", { phase: "results", result: { number: 0, color: "green" } }, { bet: 10, payout: 10 }), "0 green! Your chips come home~");
  assert.equal(T.say("slots", { phase: "betting", round: 1 }, null), T.say("slots", { phase: "betting", round: 1 }, { bet: 10 }));
  assert.match(T.say("roulette", null, null), /cushion/);
});
