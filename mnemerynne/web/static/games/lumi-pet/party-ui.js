/* Lumi Dungeons with friends (mnemerynne/lumiparty.py): the party's lists, as plain rows for game.js to draw. Pure,
   like dungeon-ui.js: it turns the server's party view into rows and never decides anything. A party trip looks like
   a solo trip to the page (party.run has the same shape), so the dungeon's own rows (dungeon-ui.js) still list the
   ways, the chest and the door; this file adds what only a party has (votes, whose turn it is, the lobby), and marks
   each way with who voted for it.

   A row is { key, icon, name, hint } plus one of: `send` with `url: "party"` (a move at the party), `url: "party-new"`
   or `url: "party-join"` (making or joining one), `local` (only changes what is showing), `turn(by)` (◀ ▶ flip it),
   or `note` (words to say when it can't be chosen). Couch menus (one per person at a TV) are rows whose `send` is
   the body of that person's press (web/games.py PartyPress). */
(function (root, make) {
  const ui = make();
  if (typeof module === "object" && module.exports) module.exports = ui; else root.LumiPetParty = ui;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const WAYS = { n: ["north", "▲"], e: ["east", "▶"], s: ["south", "▼"], w: ["west", "◀"] };
  const PUZZLES = { sokoban: "crates", lights: "lamps", runes: "runes", match: "cards", cups: "cups" };
  const RUNE = { sun: "☀", moon: "☾", star: "★", leaf: "❧", wave: "≈" };
  const CHOICE = { descend: "⤓", home: "⌂", back: "‹" }; // The icon of a vote that isn't a way.

  function member(party, user) { return party.members.find(function (m) { return m.user === user; }) || null; } // A member by account id.

  function voters(party, choice) { // The names of everyone who voted for this choice.
    return party.members.filter(function (m) { return m.vote === choice; }).map(function (m) { return m.name; });
  }

  function seconds(ms) { // "1:05", for a countdown.
    const s = Math.max(0, Math.ceil(ms / 1000));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function choiceName(party, choice) { // A vote in words: "north", "the stairs down", "Jane to go first".
    const words = party.run && party.run.party.words;
    return (words && words[choice]) || (WAYS[choice] ? WAYS[choice][0] : choice);
  }

  function limitWords(puzzle, now) { // How much of this turn is left: "2:31 left", "2 guesses left".
    if (!puzzle || puzzle.phase !== "attempt" || !puzzle.limit) return "";
    if (puzzle.limit.kind === "seconds") return seconds(puzzle.ends - now) + " left";
    const left = puzzle.limit.count - puzzle.used;
    return left + " " + (left === 1 ? puzzle.limit.kind.replace(/es$|s$/, "") : puzzle.limit.kind) + " left";
  }

  function nextUp(party, puzzle) { // Who comes after the holder in the circle.
    if (!puzzle || !puzzle.order.length) return "";
    const next = member(party, puzzle.order[(puzzle.turn + 1) % puzzle.order.length]);
    return next ? next.name : "";
  }

  function people(party) { // "Doll 30/40 · Jane (ghost) · Mina fighting", the party at a glance.
    return party.members.map(function (m) {
      return m.name + (m.fainted ? " (ghost)" : m.fighting ? " fighting" : " " + m.hp + "/" + m.hpMax) + (m.away ? " (away)" : "");
    }).join(" · ");
  }

  function status(party, now) { // The line about what the party is doing: its name and its hint.
    const run = party.run, p = run.party, puzzle = p.puzzle;
    if (p.fighting) return { name: "Everyone's fighting!", hint: people(party) };
    if (puzzle && puzzle.phase === "attempt") {
      const holder = member(party, puzzle.holder);
      return { name: (holder ? holder.name : "Someone") + "'s turn at the " + PUZZLES[puzzle.kind],
               hint: [limitWords(puzzle, now), "then " + nextUp(party, puzzle), people(party)].filter(Boolean).join(" · ") };
    }
    const left = p.ends ? " · " + seconds(p.ends - now) + " to vote" : "";
    if (puzzle && puzzle.phase === "pick") return { name: "Who goes first at the " + PUZZLES[puzzle.kind] + "?", hint: "Vote below" + left + " · " + people(party) };
    return { name: "Where to? Vote!", hint: "Two votes decide at once" + left + " · " + people(party) };
  }

  // ---- Not in a party yet: make one, join one, or play around this TV.
  function startRows(ctx) { // ctx: { open: [parties from the server], tv: on a TV as its owner, askCode() }
    const rows = [{ key: "party-new", icon: "👥", name: "Go with friends", hint: "A party of up to 3, each with their own Lumi · share its code", url: "party-new", send: { mode: "online" } }];
    if (ctx.tv) rows.push({ key: "party-couch", icon: "📺", name: "Party on this TV", hint: "Up to 3 remotes around this TV, each playing their own Lumi", url: "party-new", send: { mode: "couch" } });
    (ctx.open || []).filter(function (p) { return !p.mine && p.phase === "lobby"; }).forEach(function (p) {
      rows.push({ key: "party-join-" + p.code, icon: "✉", name: "Join " + (p.host || "a") + "'s party", hint: (p.members.join(", ") || "Nobody yet") + " · " + p.members.length + "/3 · code " + p.code,
        url: "party-join", send: { code: p.code } });
    });
    if (!ctx.tv && ctx.askCode) rows.push({ key: "party-code", icon: "⌨", name: "Join with a code", hint: "Type the code a friend shares", local: ctx.askCode });
    return rows;
  }

  function lastWords(last, you) { // How the last trip went, for this member.
    const mine = last.members.find(function (m) { return m.user === you; }), ended = mine ? mine.ended : null;
    const how = last.how === "home" ? "Home together from floor " + last.depth : last.how === "defeat" ? "Everyone fainted on floor " + last.depth : "The trip was given up on floor " + last.depth;
    if (!ended || ended.lost || ended.fainted) return how + " · your satchel was left behind";
    const parts = [ended.coins + " coins"];
    if (ended.potions) parts.push(ended.potions + (ended.potions === 1 ? " potion" : " potions"));
    if (ended.trophies.length) parts.push(ended.trophies.length === 1 ? "something for the house" : ended.trophies.length + " things for the house");
    if (ended.mods.length) parts.push(ended.mods.length === 1 ? "a new mod" : ended.mods.length + " new mods");
    return how + " · you brought " + parts.join(", ");
  }

  // ---- In the lobby: who's here and ready, where to go (the host chooses), and setting out.
  function lobbyRows(party, send) { // send(body): a move at the party, for ◀ ▶ on the place
    const me = member(party, party.you), host = party.host === party.you, ready = party.members.every(function (m) { return m.ready; });
    const open = party.envs.filter(function (e) { return e.open; }), at = open.find(function (e) { return e.id === party.env; }) || open[0];
    const rows = [{ key: "party-who", icon: "👥", name: "Party " + party.code + " · " + party.members.length + "/3",
      hint: party.members.map(function (m) { return m.name + (m.ready ? " ✓" : " …") + (m.host ? " (host)" : ""); }).join(" · "),
      note: "Friends join with the code " + party.code + " (Lumi Dungeons → Dungeon → Join with a code)." }];
    if (party.last) rows.push({ key: "party-last", icon: party.last.how === "home" ? "⌂" : "★", name: party.last.how === "home" ? "Home again!" : "Back home", hint: lastWords(party.last, party.you), note: lastWords(party.last, party.you) + "." });
    if (me) rows.push({ key: "party-ready", icon: me.ready ? "✓" : "○", name: me.ready ? "Ready!" : "I'm ready", hint: me.ready ? "Choose again if you're not" : "Everyone gets ready, then the host sets out", url: "party", send: { action: "ready" } });
    if (host && at) rows.push({ key: "party-env", icon: "✦", name: "Where: " + at.name, hint: "◀ ▶ a place every Lumi here has opened",
      note: at.about, turn: function (by) { const i = open.indexOf(at); send({ action: "env", item: open[(i + by + open.length) % open.length].id }); } });
    else if (at) rows.push({ key: "party-env", icon: "✦", name: "Where: " + at.name, hint: "The host chooses", note: at.about });
    if (host) rows.push(party.members.length < 2 ? { key: "party-start", icon: "⤓", name: "Set out!", hint: "Waiting for a friend to join", note: "A party needs at least two. Share the code " + party.code + "." }
      : ready ? { key: "party-start", icon: "⤓", name: "Set out!", hint: "Everyone's ready", url: "party", send: { action: "start" } }
        : { key: "party-start", icon: "⤓", name: "Set out!", hint: "Waiting for everyone to be ready", note: "Not everyone is ready yet." });
    rows.push({ key: "party-leave", icon: "✕", name: "Leave the party", hint: "Your Lumi goes back to exploring on her own", confirm: "party-leave", url: "party", send: { action: "leave" } });
    return rows;
  }

  // ---- Out on a trip: the dungeon's own rows (base), with the party's around them.
  const BOARD_KEYS = ["play", "undo", "reset", "try", "erase"]; // dungeon-ui's rows for working a puzzle, kept for its holder only

  function voteOf(row) { // The vote a dungeon-ui row stands for, if it is one: a way, home, the stairs, or stepping back.
    if (!row.send || row.url !== "dungeon/act") return null;
    return { move: row.send.way, leave: "home", descend: "descend", back: "back" }[row.send.action] || null;
  }

  function tripRows(party, base, now) { // base: dungeon-ui's rows for party.run
    const run = party.run, p = run.party, puzzle = p.puzzle, me = member(party, party.you), said = status(party, now);
    const rows = [{ key: "party-status", icon: "👥", name: said.name, hint: said.hint, note: said.name + ". " + said.hint + "." }];
    if (puzzle && puzzle.phase === "pick") party.members.forEach(function (m) { // Who goes first: one row per member.
      const choice = "m" + m.user, who = voters(party, choice);
      rows.push({ key: "pick-" + m.user, icon: "☞", name: "Vote " + (m.user === party.you ? "me" : m.name) + " to go first" + (who.length ? " · " + who.join(", ") : ""),
        hint: "Tried " + m.puzzles + (m.puzzles === 1 ? " puzzle" : " puzzles") + " this trip", url: "party", send: { action: "vote", target: choice } });
    });
    const mine = puzzle && puzzle.phase === "attempt" && puzzle.holder === party.you;
    if (mine) rows.push({ key: "party-pass", icon: "⇥", name: "Pass it on", hint: limitWords(puzzle, now) + " · next: " + nextUp(party, puzzle), url: "party", send: { action: "pass" } });
    else if (puzzle && puzzle.phase === "attempt") rows.push({ key: "party-watch", icon: "👀", name: "Watching " + (member(party, puzzle.holder) || { name: "someone" }).name, hint: limitWords(puzzle, now) + " · only they can move it", note: "It's their turn. Yours comes round if they don't solve it." });
    base.forEach(function (row) {
      if (puzzle && puzzle.phase === "attempt" && !mine && (BOARD_KEYS.indexOf(row.key) >= 0 || /^(rune|cup)-/.test(row.key))) return; // only the holder works it
      if (puzzle && puzzle.phase === "pick" && /^(play|cup-)/.test(row.key)) return; // nobody works it until the vote says who
      const choice = voteOf(row);
      if (choice) {
        const who = voters(party, choice);
        row = Object.assign({}, row, { name: "Vote: " + row.name.charAt(0).toLowerCase() + row.name.slice(1) + (who.length ? " · " + who.join(", ") : "") });
      }
      if (row.key === "abandon") row = Object.assign({}, row, { name: "Leave the party", hint: "Your satchel stays behind; the others carry on" });
      if (me && me.fainted && (row.key === "take" || row.key === "potion")) row = Object.assign({}, row, { send: null, url: null, note: "A little ghost finds nothing and drinks nothing. She'll mend at home." });
      rows.push(row);
    });
    return rows;
  }

  // ---- The couch: one menu per person at the TV, worked with their own remote.
  function couchMenu(party, m, ctx) { // ctx: { guess: [runes so far], now }. Rows' `send` is the press's body.
    if (party.phase === "lobby") {
      const open = party.envs.filter(function (e) { return e.open; }), at = open.find(function (e) { return e.id === party.env; }) || open[0];
      const rows = [{ key: "ready", icon: m.ready ? "✓" : "○", name: m.ready ? "Ready!" : "I'm ready", send: { action: "ready" } }];
      if (m.host && at) rows.push({ key: "env", icon: "✦", name: "Where: " + at.name, hint: "◀ ▶ to choose", turn: function (by) { const i = open.indexOf(at); return { action: "env", item: open[(i + by + open.length) % open.length].id }; } });
      if (m.host) rows.push({ key: "start", icon: "⤓", name: "Set out!", send: { action: "start" } });
      rows.push({ key: "leave", icon: "✕", name: "Leave the party", send: { action: "leave" }, confirm: true });
      return rows;
    }
    const run = party.run, p = run.party, puzzle = p.puzzle;
    if (m.fight) { // Their own fight: their moves, Defend, a potion, or run.
      const fight = m.fight, moves = fight.player.moves || [];
      return moves.map(function (move, i) {
        return { key: "move" + i, icon: "✦", name: move.name, hint: (move.mp ? move.mp + " MP" : "free") + " · vs " + fight.enemy.name + " " + fight.enemy.hp + "/" + fight.enemy.hpMax, send: { action: "fight", target: "move" + i, turn: fight.turn } };
      }).concat([{ key: "defend", icon: "⛨", name: "Defend", hint: "Gather MP", send: { action: "fight", target: "defend", turn: fight.turn } },
        { key: "potion", icon: "🧪", name: "Potion", send: { action: "fight", target: "potion", turn: fight.turn } },
        { key: "run", icon: "↩", name: "Run", send: { action: "fight", target: "run", turn: fight.turn } }]);
    }
    if (p.fighting) return [{ key: "wait", icon: "…", name: m.fainted ? "A little ghost, cheering" : "Waiting for the others", hint: people(party) }];
    if (puzzle && puzzle.phase === "attempt" && puzzle.holder === m.user) { // Their turn at the puzzle.
      const pass = { key: "pass", icon: "⇥", name: "Pass it on", hint: limitWords(puzzle, ctx.now) + " · ⏯ does it too", send: { action: "pass" } };
      if (puzzle.kind === "cups") return [0, 1, 2].map(function (cup) { return { key: "cup" + cup, icon: "🥤", name: ["Left", "Middle", "Right"][cup] + " cup", send: { action: "cup", picks: [cup] } }; });
      if (puzzle.kind === "runes") {
        const lock = run.facing.lock, guess = ctx.guess || [], full = guess.length === lock.slots;
        const rows = [full ? { key: "try", icon: "✓", name: "Try " + guess.map(function (r) { return RUNE[r]; }).join(" "), hint: limitWords(puzzle, ctx.now), send: { action: "rune", picks: guess } }
          : { key: "try", icon: "✓", name: "Code: " + (guess.map(function (r) { return RUNE[r]; }).join(" ") || "…"), hint: "Add " + lock.slots + " runes", note: true }];
        lock.runes.forEach(function (rune) { rows.push({ key: "rune-" + rune, icon: RUNE[rune], name: "Add " + rune, local: { rune: rune } }); });
        rows.push({ key: "erase", icon: "↶", name: "Take one off", local: { erase: true } }, pass);
        return rows;
      }
      return [{ key: "board", icon: "▣", name: "Arrows work the " + PUZZLES[puzzle.kind], hint: { sokoban: "OK takes a step back", lights: "OK flips a lamp", match: "OK turns a card" }[puzzle.kind] + " · " + limitWords(puzzle, ctx.now), note: true }, pass];
    }
    const rows = [];
    p.options.forEach(function (choice) { // Every vote open to the party.
      const who = voters(party, choice), mineToo = m.vote === choice;
      rows.push({ key: "vote-" + choice, icon: WAYS[choice] ? WAYS[choice][1] : choice.charAt(0) === "m" ? "☞" : CHOICE[choice] || "•",
        name: (mineToo ? "✓ " : "") + "Vote " + choiceName(party, choice), hint: who.length ? who.join(", ") : "No votes yet", send: { action: "vote", target: choice } });
    });
    const room = run.room;
    if (!run.facing && !room.done && ["treasure", "vault", "key"].indexOf(room.kind) >= 0) rows.push({ key: "take", icon: "✦", name: room.kind === "key" ? "Pick up the key" : "Open the chest", hint: "Everyone gets their own share", send: { action: "take" } });
    if (run.facing && run.facing.lock.kind === "key" && run.satchel.keys) rows.push({ key: "unlock", icon: "🗝", name: "Use the key", send: { action: "unlock" } });
    if (!m.fainted && m.hp < m.hpMax) rows.push({ key: "potion", icon: "🧪", name: "Drink a potion", hint: m.hp + "/" + m.hpMax + " HP · ⏯ does it too", send: { action: "potion" } });
    rows.push({ key: "look", icon: "◉", name: "Look around", send: { action: "look" } });
    rows.push({ key: "leave", icon: "✕", name: "Leave the party", hint: "Your satchel stays behind", send: { action: "abandon" }, confirm: true });
    return rows;
  }

  return { status: status, startRows: startRows, lobbyRows: lobbyRows, tripRows: tripRows, couchMenu: couchMenu, lastWords: lastWords,
    limitWords: limitWords, voters: voters, member: member, people: people, PUZZLES: PUZZLES };
});
