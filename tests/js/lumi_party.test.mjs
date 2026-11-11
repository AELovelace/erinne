// Lumi Dungeons with friends: the party's lists (party-ui.js) against party views shaped like the server's
// (mnemerynne/lumiparty.py view), on top of the dungeon's own rows (dungeon-ui.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const Pu = require("../../mnemerynne/web/static/games/lumi-pet/party-ui.js");
const Dg = require("../../mnemerynne/web/static/games/lumi-pet/dungeon-ui.js");

const bag = { coins: 20, potions: 0, keys: 0, trophies: [], mods: [] };
function member(user, name, extra = {}) {
  return { user, name, seat: user - 1, host: user === 1, ready: false, level: 3, glow: "rose", hp: 30, hpMax: 40, mp: 10, mpMax: 20,
    fainted: false, satchel: bag, puzzles: 0, vote: null, fighting: false, away: false, you: false, couch: false, remote: null, fight: null, ...extra };
}
function party(extra = {}, run = null) {
  return { code: "ABCD1234", mode: "online", phase: run ? "trip" : "lobby", host: 1, env: "moonlit-shrine", you: 1, display: false,
    envs: [{ id: "moonlit-shrine", name: "Moonlit Shrine", open: true, about: "Quiet." }, { id: "far", name: "Far Place", open: false, about: "" },
      { id: "glow-garden", name: "Glow Garden", open: true, about: "Bright." }],
    members: [member(1, "doll"), member(2, "jane"), member(3, "mina")], run, last: null, notes: [], revision: 4, ...extra };
}
function trip(partyExtra = {}, runExtra = {}) { // In a hall with ways east and west, the party deciding.
  const run = { id: 9, env: { id: "moonlit-shrine", name: "Moonlit Shrine", family: "mystical", palette: {} }, depth: 1, at: "1,0", over: null, ended: null,
    facing: null, game: null, rooms: { "1,0": { kind: "empty", done: true, exits: ["e", "w"] } }, room: { kind: "empty", done: true, exits: ["e", "w"] },
    satchel: { ...bag, keys: 0 }, hp: 30, hpMax: 40, mp: 10, mpMax: 20, say: "", event: { scenario: "empty" }, story: [],
    party: { code: "ABCD1234", options: ["e", "w"], puzzle: null, votes: {}, ends: null, fighting: false, words: { e: "east", w: "west" } }, ...runExtra };
  return party(partyExtra, run);
}
function base(p) { return Dg.rows({ snap: { pet: { potions: 0 }, potions_ready: 0, dungeon: { run: p.run, environments: [] }, catalog: { snacks: [], treats: [] } }, play: {}, go() {} }); }

test("before a party: make one, play on this TV, join one that's waiting, or type its code", () => {
  const rows = Pu.startRows({ open: [{ code: "C0FFEE00", host: "jane", members: ["jane"], phase: "lobby", mine: false },
    { code: "BEEF0000", host: "x", members: ["x"], phase: "trip", mine: false }], tv: false, askCode() {} });
  assert.deepEqual(rows.map(r => r.key), ["party-new", "party-join-C0FFEE00", "party-code"]);
  assert.deepEqual(rows[1].send, { code: "C0FFEE00" });
  const tv = Pu.startRows({ open: [], tv: true, askCode() {} }).map(r => r.key);
  assert.deepEqual(tv, ["party-new", "party-couch"]);                              // a TV can't type a code
});

test("the lobby: ready, the host's place (◀ ▶ over open places only), and setting out once everyone's ready", () => {
  const sent = [];
  let rows = Pu.lobbyRows(party(), body => sent.push(body));
  assert.deepEqual(rows.map(r => r.key), ["party-who", "party-ready", "party-env", "party-start", "party-leave"]);
  assert.equal(rows.find(r => r.key === "party-start").send, undefined);           // not everyone is ready
  rows.find(r => r.key === "party-env").turn(1);
  assert.deepEqual(sent, [{ action: "env", item: "glow-garden" }]);                // the closed place is skipped
  const ready = party({ members: [member(1, "doll", { ready: true }), member(2, "jane", { ready: true })] });
  assert.deepEqual(Pu.lobbyRows(ready, () => {}).find(r => r.key === "party-start").send, { action: "start" });
  const guest = Pu.lobbyRows(party({ you: 2 }), () => {});
  assert.ok(!guest.some(r => r.key === "party-start") && !guest.find(r => r.key === "party-env").turn);   // only the host
});

test("on a trip each way is a vote, marked with who chose it", () => {
  const p = trip({ members: [member(1, "doll", { vote: "e" }), member(2, "jane", { vote: "e" }), member(3, "mina")] });
  const rows = Pu.tripRows(p, base(p), 0);
  assert.equal(rows[0].key, "party-status");
  const east = rows.find(r => r.key === "go-e");
  assert.match(east.name, /^Vote: go east · doll, jane$/);
  assert.deepEqual(east.send, { action: "move", way: "e" });
  assert.equal(rows.find(r => r.key === "abandon").name, "Leave the party");
});

test("who goes first is a vote for a member, showing how many puzzles each has tried", () => {
  const puzzle = { kind: "runes", phase: "pick", order: [], turn: 0, holder: null, door: true, used: 0, moves: "", presses: [], limit: { kind: "guesses", count: 3 }, ends: null };
  const lock = { kind: "runes", runes: ["sun", "moon"], slots: 4, tries: [], left: 8 };
  const p = trip({ members: [member(1, "doll", { puzzles: 2 }), member(2, "jane", { vote: "m3" }), member(3, "mina")] },
    { facing: { at: "2,0", lock }, party: { ...trip().run.party, options: ["back", "m1", "m2", "m3"], puzzle } });
  const rows = Pu.tripRows(p, base(p), 0);
  const picks = rows.filter(r => r.key.startsWith("pick-"));
  assert.deepEqual(picks.map(r => r.send.target), ["m1", "m2", "m3"]);
  assert.equal(picks[0].name, "Vote me to go first");
  assert.equal(picks[0].hint, "Tried 2 puzzles this trip");
  assert.equal(picks[2].name, "Vote mina to go first · jane");
  assert.equal(Pu.status(p, 0).name, "Who goes first at the runes?");
});

test("at a puzzle only the holder gets its controls and Pass it on; the others watch", () => {
  const puzzle = { kind: "sokoban", phase: "attempt", order: [2, 1, 3], turn: 0, holder: 2, door: true, used: 0, moves: "", presses: [],
    limit: { kind: "seconds", count: 180 }, ends: 125000 };
  const run = { facing: { at: "2,0", lock: { kind: "sokoban", rows: ["#####", "#@$.#", "#####"] } }, party: { ...trip().run.party, options: ["back"], puzzle } };
  const watcher = trip({}, run);
  const rows = Pu.tripRows(watcher, base(watcher), 0);
  assert.ok(rows.some(r => r.key === "party-watch") && !rows.some(r => ["play", "undo", "reset"].includes(r.key)));
  assert.match(rows.find(r => r.key === "party-watch").hint, /^2:05 left/);
  assert.equal(Pu.status(watcher, 0).name, "jane's turn at the crates");
  assert.match(Pu.status(watcher, 0).hint, /then doll/);
  const holder = trip({ you: 2 }, run);
  const mine = Pu.tripRows(holder, base(holder), 0);
  assert.ok(mine.some(r => r.key === "party-pass") && mine.some(r => r.key === "play"));
  assert.deepEqual(mine.find(r => r.key === "party-pass").send, { action: "pass" });
});

test("a ghost's chest and potion rows explain instead of sending", () => {
  const p = trip({ members: [member(1, "doll", { fainted: true, hp: 0 }), member(2, "jane")] },
    { room: { kind: "treasure", done: false, exits: ["w"] }, hp: 0 });
  const take = Pu.tripRows(p, base(p), 0).find(r => r.key === "take");
  assert.equal(take.send, null);
  assert.match(take.note, /ghost/);
});

test("the couch: each person's own menu, their own fight, and their puzzle turn", () => {
  const p = trip({ display: true, you: null });
  const menu = Pu.couchMenu(p, p.members[0], { now: 0 });
  assert.deepEqual(menu.map(r => r.key), ["vote-e", "vote-w", "potion", "look", "leave"]);
  assert.deepEqual(menu[0].send, { action: "vote", target: "e" });
  const fight = { id: 4, turn: 2, enemy: { name: "Moss Imp", hp: 9, hpMax: 20 }, player: { moves: [{ name: "Pounce", mp: 0 }] }, log: ["Hi"] };
  const fighting = Pu.couchMenu(p, member(1, "doll", { fight }), { now: 0 });
  assert.deepEqual(fighting.map(r => r.send.target), ["move0", "defend", "potion", "run"]);
  assert.equal(fighting[0].send.turn, 2);
  const runes = { kind: "runes", phase: "attempt", order: [1], turn: 0, holder: 1, door: true, used: 1, moves: "", presses: [], limit: { kind: "guesses", count: 3 }, ends: null };
  const atDoor = trip({ display: true }, { facing: { at: "2,0", lock: { kind: "runes", runes: ["sun", "moon"], slots: 2, tries: [], left: 8 } }, party: { ...trip().run.party, puzzle: runes } });
  const turn = Pu.couchMenu(atDoor, atDoor.members[0], { guess: ["sun", "moon"], now: 0 });
  assert.deepEqual(turn[0].send, { action: "rune", picks: ["sun", "moon"] });
  assert.match(turn[0].hint, /2 guesses left/);
  assert.deepEqual(turn.at(-1).send, { action: "pass" });
});

test("how the last trip went, for each member", () => {
  const last = { how: "home", depth: 2, members: [{ user: 1, ended: { coins: 40, potions: 1, trophies: ["x"], mods: [] } }, { user: 2, ended: { coins: 0, lost: true } }] };
  assert.equal(Pu.lastWords(last, 1), "Home together from floor 2 · you brought 40 coins, 1 potion, something for the house");
  assert.equal(Pu.lastWords(last, 2), "Home together from floor 2 · your satchel was left behind");
});
