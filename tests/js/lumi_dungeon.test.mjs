// The Dungeon tab's lists and puzzles (dungeon-ui.js) and the dungeon's art (dungeon-art.js), against the server's
// catalogs (dungeon.json, sokoban.json).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const UI = require("../../mnemerynne/web/static/games/lumi-pet/dungeon-ui.js");
const Art = require("../../mnemerynne/web/static/games/lumi-pet/dungeon-art.js");
const data = name => JSON.parse(readFileSync(new URL("../../mnemerynne/lumipet_data/" + name, import.meta.url), "utf8"));
const catalog = data("dungeon.json"), levels = data("sokoban.json").levels;
const bag = { coins: 24, potions: 1, keys: 0, trophies: [], mods: [] };

function run(extra = {}) { // A trip as the server would describe it: Lumi in a room with ways north and east.
  return { id: 9, env: { ...catalog.environments[1], id: "crystal-cavern" }, depth: 2, at: "1,1", over: null, ended: null, facing: null, game: null,
    rooms: { "1,1": { kind: "empty", done: true, exits: ["e", "n"] }, "1,0": { kind: "unknown" }, "2,1": { kind: "vault", done: false, exits: ["w"] } },
    room: { kind: "empty", done: true, exits: ["e", "n"] }, satchel: bag, hp: 30, hpMax: 58, mp: 10, mpMax: 22, say: "", event: { scenario: "empty" }, ...extra };
}
function open(dungeonRun, play = {}, dismissed = null, pet = { potions: 2 }) {
  const went = [];
  const snap = { pet, dungeon: { run: dungeonRun, environments: catalog.environments.map((env, i) => ({ ...env, open: i < 2 })) } };
  const rows = UI.rows({ snap, play, dismissed, go: change => went.push(change) });
  return { rows, went, row: key => rows.find(r => r.key === key) };
}

test("every environment has a backdrop with its own motif, in its own colours", () => {
  assert.deepEqual(Object.keys(Art.MOTIF).sort(), catalog.environments.map(env => env.id).sort());
  for (const env of catalog.environments) {
    const drawn = Art.backdrop(env);
    assert.ok(drawn.includes(env.palette.wall) && drawn.includes(env.palette.floor) && drawn.length > 1500, env.id);
    assert.equal(/NaN|undefined|Infinity/.test(drawn), false, env.id + " drew a broken number");
  }
});

test("an unknown environment or a colour that isn't one still draws a plain room", () => {
  const odd = Art.backdrop({ id: "constructor", palette: { wall: '"/><script>', floor: "#123456", accent: "red", glow: "#abcdef" } });
  assert.ok(!odd.includes("script") && odd.includes("#888888") && odd.includes("#123456"));
});

test("with no trip the list is the places to go, and locked ones say when they open", () => {
  const list = open(null);
  assert.equal(list.rows.length, 12);
  assert.deepEqual([list.rows[0].url, list.rows[0].send], ["dungeon", { env: "moonlit-shrine" }]);
  assert.equal(list.rows[2].send, undefined);
  assert.match(list.rows[2].note, /opens at level 3/);
});

test("a finished trip shows how it ended once, then the places again", () => {
  const home = run({ over: "home", ended: { coins: 80, potions: 1, trophies: ["trophy-geode-egg"], mods: [], depth: 3 } });
  const first = open(home);
  assert.deepEqual(first.rows.map(r => [r.key, r.name, r.hint]), [["continue", "Home again!", "Home from floor 3 with 80 coins, 1 potion, a trophy for the house"]]);
  first.rows[0].local();
  assert.deepEqual(first.went, [{ dismiss: 9 }]);
  assert.equal(open(home, {}, 9).rows.length, 12);
  const lost = run({ over: "defeat", ended: { lost: bag, depth: 2 } });
  assert.equal(open(lost).rows[0].hint, "Carried home from floor 2 · left behind: 24 coins · 1 potion");
});

test("exploring offers each way out, saying what is known there, and only choices are sent", () => {
  const list = open(run());
  assert.deepEqual(list.rows.map(r => r.key), ["go-e", "go-n", "potion", "abandon"]);
  assert.deepEqual(list.row("go-n").send, { action: "move", way: "n" });
  assert.equal(list.row("go-n").hint, "somewhere new");
  assert.equal(list.row("go-e").hint, "a locked door");
  assert.equal(list.row("potion").name, "Drink a potion (2)");
  assert.equal(list.row("abandon").confirm, "abandon");
  assert.match(list.row("abandon").hint, /Leaves the satchel behind: 24 coins · 1 potion/);
  assert.ok(list.rows.every(r => !r.send || Object.keys(r.send).every(k => ["action", "way", "picks", "moves"].includes(k))));
  assert.match(open(run(), {}, null, { potions: 0 }).row("potion").note, /no potions/);
  assert.equal(open(run({ hp: 58 })).row("potion"), undefined);
  const found = UI.rows({ snap: { pet: { potions: 0 }, potions_ready: 1, dungeon: { run: run(), environments: [] } }, play: {}, dismissed: null, go() {} }).find(r => r.key === "potion");
  assert.deepEqual([found.name, found.send], ["Drink a potion (1)", { action: "potion" }], "A potion found on this trip counts and can be drunk");
});

test("a chest or a key is offered until it is taken, and never after", () => {
  const chest = open(run({ room: { kind: "treasure", done: false, exits: ["n"] } }));
  assert.deepEqual([chest.rows[0].key, chest.rows[0].name, chest.rows[0].send], ["take", "Open the chest", { action: "take" }]);
  assert.equal(open(run({ room: { kind: "vault", done: false, exits: ["w"] } })).row("take").hint, "The vault's treasure, at last");
  assert.equal(open(run({ room: { kind: "key", done: false, exits: ["n"] } })).row("take").name, "Pick up the key");
  for (const kind of ["treasure", "vault", "key"]) assert.equal(open(run({ room: { kind, done: true, exits: ["n"] } })).row("take"), undefined, kind);
  assert.equal(open(run()).row("take"), undefined);
  const lying = Art.scene(run({ room: { kind: "key", done: false, exits: ["n"] } }), {}, "list"), gone = Art.scene(run({ room: { kind: "key", done: true, exits: ["n"] } }), {}, "list");
  assert.ok(lying.length > gone.length, "The key is drawn while it lies there, and not once it is taken");
});

test("the entrance offers home and the stairs offer down", () => {
  const start = open(run({ room: { kind: "start", done: true, exits: ["n"] } }));
  assert.deepEqual(start.row("leave").send, { action: "leave" });
  assert.match(start.row("leave").hint, /Keep everything in the satchel: 24 coins/);
  const stairs = open(run({ room: { kind: "stairs", done: true, exits: ["n"] } }));
  assert.deepEqual(stairs.row("descend").send, { action: "descend" });
  assert.match(stairs.row("descend").hint, /Floor 3/);
});

test("a key door needs a key; a rune door builds a code four runes long", () => {
  const facing = lock => run({ facing: { at: "2,1", lock } });
  assert.match(open(facing({ kind: "key" })).row("unlock").note, /needs a key/);
  assert.deepEqual(open(run({ facing: { at: "2,1", lock: { kind: "key" } }, satchel: { ...bag, keys: 1 } })).row("unlock").send, { action: "unlock" });
  const lock = { kind: "runes", runes: ["sun", "moon", "star", "leaf", "wave"], slots: 4, tries: [], left: 8 };
  const empty = open(facing(lock), { guess: [] });
  assert.deepEqual(empty.rows.map(r => r.key), ["try", "rune-sun", "rune-moon", "rune-star", "rune-leaf", "rune-wave", "erase", "back"]);
  assert.match(empty.row("try").note, /Choose 4 runes first/);
  empty.row("rune-moon").local();
  assert.deepEqual(empty.went, [{ rune: "moon" }]);
  const full = open(facing(lock), { guess: ["sun", "moon", "sun", "wave"] });
  assert.deepEqual(full.row("try").send, { action: "rune", picks: ["sun", "moon", "sun", "wave"] });
  assert.match(full.row("rune-sun").note, /Try the code/);
  assert.deepEqual(full.row("back").send, { action: "back" });
});

test("the cup game is three rows, and the cards hand the remote to the board", () => {
  const cups = open(run({ room: { kind: "game", done: false, exits: ["n"] }, game: { kind: "cups", cups: 3 } }));
  assert.deepEqual(cups.rows.slice(0, 3).map(r => r.send), [0, 1, 2].map(cup => ({ action: "cup", picks: [cup] })));
  const cards = open(run({ room: { kind: "game", done: false, exits: ["n"] }, game: { kind: "match", cards: Array(8).fill(null), found: [], open: null, last: null, left: 14 } }));
  cards.row("play").local();
  assert.deepEqual(cards.went, [{ focus: "board" }]);
});

test("Sokoban on the page plays by the server's rules: walls stop, a crate moves if there is room, undo replays", () => {
  let board = UI.sokobanStart(["######", "#@$ .#", "######"]);
  assert.equal(UI.sokobanSolved(board), false);
  assert.equal(UI.sokobanMove(board, "l"), board, "a wall: nothing happens, and no move is recorded");
  assert.equal(UI.sokobanMove(board, "u"), board);
  board = UI.sokobanMove(board, "r");
  assert.deepEqual([board.at, Object.keys(board.crates), board.moves], [[2, 1], ["3,1"], "r"]);
  board = UI.sokobanMove(board, "r");
  assert.ok(UI.sokobanSolved(board) && board.moves === "rr");
  assert.equal(UI.sokobanMove(board, "r"), board, "a crate against the wall can't be pushed");
  const back = UI.sokobanUndo(board);
  assert.deepEqual([back.at, back.moves, UI.sokobanSolved(back)], [[2, 1], "r", false]);
  const two = UI.sokobanStart(["#######", "#@$$..#", "#######"]);
  assert.equal(UI.sokobanMove(two, "r"), two, "one crate can't push another");
});

test("every shipped Sokoban level loads on the page with as many crates as goals", () => {
  for (const level of levels) {
    const board = UI.sokobanStart(level.rows);
    assert.ok(board.at && Object.keys(board.crates).length === Object.keys(board.goals).length && Object.keys(board.crates).length > 0, level.id);
    assert.equal(UI.sokobanSolved(board), false, level.id);
  }
});

test("Lights Out flips a lamp and its neighbours, and the cursor leaves the board only off its edges", () => {
  let board = UI.lightsStart({ size: 3, board: [false, true, false, true, true, true, false, true, false] });
  board = UI.lightsPress(board, 4);
  assert.ok(UI.lightsSolved(board));
  assert.deepEqual(board.presses, [4]);
  assert.deepEqual(UI.lightsPress(UI.lightsStart({ size: 3, board: Array(9).fill(false) }), 0).lamps.map(Number), [1, 1, 0, 1, 0, 0, 0, 0, 0]);
  assert.deepEqual([UI.cursorMove(0, "e", 3, 9), UI.cursorMove(0, "s", 3, 9), UI.cursorMove(0, "w", 3, 9), UI.cursorMove(0, "n", 3, 9), UI.cursorMove(8, "s", 3, 9), UI.cursorMove(2, "e", 3, 9)], [1, 3, null, null, null, null]);
  assert.equal(UI.cursorMove(3, "s", 4, 8), 7);
});

test("the scene draws the room's doors, what is in it, a map, and the puzzle on the page", () => {
  const plain = Art.scene(run(), {}, "list");
  assert.ok(plain.includes(">N<") && plain.includes(">E<") && !plain.includes(">W<"), "a door for each way out, and no others");
  assert.equal(/NaN|undefined/.test(plain), false);
  const level = levels[0], board = UI.sokobanStart(level.rows);
  const crates = Art.scene(run({ facing: { at: "2,1", lock: { kind: "sokoban", rows: level.rows } } }), { board }, "board");
  assert.ok(crates.includes("PUSH THE CRATES") && !/NaN|undefined/.test(crates));
  assert.ok(Art.scene(run({ facing: { at: "2,1", lock: { kind: "sokoban", rows: level.rows } } }), { board }, "list").includes("PAUSED"));
  const lamps = Art.scene(run({ facing: { at: "2,1", lock: { kind: "lights", size: 3, board: Array(9).fill(true) } } }), { board: UI.lightsStart({ size: 3, board: Array(9).fill(true) }) }, "board");
  assert.ok(lamps.includes("PUT EVERY LAMP OUT"));
  const runes = Art.scene(run({ facing: { at: "2,1", lock: { kind: "runes", runes: [], slots: 4, tries: [{ guess: ["sun", "moon", "star", "leaf"], exact: 1, near: 2 }], left: 7 } } }), { guess: ["wave"] }, "list");
  assert.ok(runes.includes("FIND THE RUNE CODE") && runes.includes("☀") && runes.includes("≈"));
  assert.ok(Art.scene(run({ facing: { at: "2,1", lock: { kind: "key" } } }), {}, "list").includes("IT NEEDS A KEY"));
  const away = Art.scene(run({ facing: { at: "2,1", lock: { kind: "sokoban", rows: level.rows } } }), { board }, "board", false);
  assert.ok(!away.includes("PUSH THE CRATES") && !away.includes("NEEDS A KEY") && away.includes(">N<"), "On another tab the room shows without the puzzle over it");
  const cards = Art.scene(run({ room: { kind: "game", done: false, exits: ["n"] }, game: { kind: "match", cards: ["sun", null, null, null, null, null, null, null], found: [], open: 0, last: null, left: 13 } }), { cursor: 2 }, "board");
  assert.ok(cards.includes("FIND THE PAIRS · 13 TURNS LEFT") && cards.includes("☀"));
});

test("the top line reads the place, the floor, her health and her satchel", () => {
  assert.deepEqual(UI.hud(run()), { title: "Crystal Cavern · floor 2", hp: 52, mp: 45, label: "30/58 HP · 10 MP · satchel: 24 coins · 1 potion" });
  assert.equal(UI.carrying({ coins: 5, potions: 2, keys: 1, trophies: ["a", "b"], mods: ["m"] }), "5 coins · 2 potions · a key · 2 trophies · 1 mod");
});
