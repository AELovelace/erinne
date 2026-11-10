/* The Battle tab's lists: what to offer when Lumi is between fights (find a fight, skills, moves, mods, potions) and
   during one (her moves, Defend, Potion, Run). Pure: it turns the server's snapshot into plain rows for game.js to
   draw, and never decides an outcome. The fighting itself is mnemerynne/lumibattle.py.

   A row is { key, icon, name, hint } plus one of: `send` (a request body; `url` says where, default "act"),
   `local` (a function that only changes which list is showing), or `note` (words to say when it can't be chosen). */
(function (root, make) {
  const ui = make();
  if (typeof module === "object" && module.exports) module.exports = ui; else root.LumiPetBattle = ui;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const TIERS = { // In the order they are offered.
    common: ["Common", "A gentle warm-up"], uncommon: ["Uncommon", "A fair scrap"], rare: ["Rare", "Bring a plan"],
    epic: ["Epic", "Bring the right element"], legendary: ["Legendary", "The Comet Drake waits"], gamble: ["Gamble", "Anything at all · a fifth more reward"],
  };
  const ICON = { fire: "🔥", ice: "❄", wind: "🍃", spirit: "👻", holy: "☀", dark: "🌙", none: "🐾", glow: "✦" };
  const EFFECT = { pierce: "cuts through armour", crit: "often critical", first: "strikes first", poison: "poisons", burn: "burns",
    stun: "may stun", ward: "halves damage taken", drain: "drains health" };
  const BONUS = { attack: "attack", defense: "defense", magic: "magic", ward: "ward", speed: "speed", hp: "health", mp: "MP",
    crit: "critical chance", dodge: "dodge", coins: "coins from wins" };

  function describeMove(move) { // "Power 60 · spell · fire · 4 MP · burns"
    const parts = [];
    if (move.power) parts.push("Power " + move.power + (move.hits > 1 ? " ×" + move.hits : ""));
    if (move.heal) parts.push("Heals " + move.heal + "%");
    parts.push(move.magic ? "spell" : "physical");
    if (move.element !== "none") parts.push(move.element === "glow" ? "her glow's element" : move.element);
    if (move.mp) parts.push(move.mp + " MP");
    if (move.effect) parts.push(EFFECT[move.effect] + (move.effect === "ward" ? " for " + (move.turns || 2) + " turns" : ""));
    return parts.join(" · ");
  }

  function describeBonus(bonus) { // "+10% attack, +3% health"
    return Object.keys(bonus).map(function (key) { return "+" + bonus[key] + "% " + BONUS[key]; }).join(", ");
  }

  function capital(text) { return text[0].toUpperCase() + text.slice(1); }

  function fightRows(snap) { // During a fight: her ready moves, then the three things she can always do.
    const fight = snap.fight, lumi = fight.player;
    function turn(action) { return { battle: fight.id, turn: fight.turn, action: action }; }
    return lumi.moves.map(function (move, index) {
      return { key: "move" + index, icon: ICON[move.element] || ICON.none, name: move.name, url: "battle/act", send: turn("move" + index),
        hint: describeMove(move) + (move.mp > lumi.mp ? " · not enough MP" : "") };
    }).concat([
      { key: "defend", icon: "🛡", name: "Defend", hint: "Halve the next hit · gather 6 MP", url: "battle/act", send: turn("defend") },
      { key: "potion", icon: "🧪", name: "Potion (" + (snap.potions_ready === undefined ? snap.pet.potions : snap.potions_ready) + ")", hint: "Restores half her health", url: "battle/act", send: turn("potion") },
      { key: "run", icon: "💨", name: "Run", hint: "Three times in four she gets away", url: "battle/act", send: turn("run") },
    ]);
  }

  function rows(ctx) { // ctx: { snap, view, finished, now, go(view, pick, clearFinished), countdown(ms) }
    const snap = ctx.snap, pet = snap.pet, battle = snap.battle, progress = snap.progress, rules = battle.rules, go = ctx.go, view = ctx.view;
    function back(to, pick, label) { return { key: "back", icon: "‹", name: "Back", hint: label, local: function () { go(to, pick); } }; }
    if (snap.fight && snap.fight.status === "active") return fightRows(snap);
    if (ctx.finished) { // The fight just ended: one row to read the result and carry on.
      const reward = ctx.finished.reward;
      return [{ key: "continue", icon: "›", name: ctx.finished.status === "victory" ? "Hooray!" : "Carry on", local: function () { go(null, 0, true); },
        hint: reward ? "+" + reward.exp + " EXP · +" + reward.coins + " coins" + (reward.levels ? " · level " + reward.level + "!" : "")
          : ctx.finished.status === "ran" ? "Safe and sound" : "She'll be up again soon" }];
    }
    if (view === "fight") return [back(null, 0, "Who to look for")].concat(rules.tiers.concat(["gamble"]).map(function (tier) {
      return { key: "tier-" + tier, icon: tier === "gamble" ? "🎲" : "⚔", name: TIERS[tier][0], hint: TIERS[tier][1], url: "battle", send: { tier: tier } };
    }));
    if (view === "skills") return [back(null, 1, progress.points + " skill points to spend")].concat(battle.trees.map(function (tree) {
      const known = tree.nodes.filter(function (node) { return pet.skills.includes(node.id); }).length;
      return { key: "tree-" + tree.id, icon: "✦", name: tree.name, hint: tree.about + " · " + known + " of " + tree.nodes.length, local: function () { go("tree:" + tree.id, Math.min(known + 1, tree.nodes.length)); } };
    }));
    if (view && view.indexOf("tree:") === 0) {
      const tree = battle.trees.find(function (entry) { return "tree:" + entry.id === view; });
      const known = tree.nodes.filter(function (node) { return pet.skills.includes(node.id); }).length;
      return [back("skills", 1 + battle.trees.indexOf(tree), tree.name + " · " + progress.points + " points to spend")].concat(tree.nodes.map(function (node, index) {
        const learned = pet.skills.includes(node.id), next = index === known, cost = rules.costs[index];
        const row = { key: node.id, icon: learned ? "✓" : next ? "✧" : "·", name: node.name,
          hint: (node.kind === "move" ? describeMove(node) : describeBonus(node.bonus)) + " · " + (learned ? "learned" : cost + (cost === 1 ? " point" : " points")) };
        if (learned) row.note = "Lumi already knows " + node.name + ".";
        else if (!next) row.note = "Learn the skills above it first.";
        else row.send = { action: "learn", item: node.id };
        return row;
      }));
    }
    if (view === "moves") {
      const known = battle.basics.concat(battle.trees.reduce(function (all, tree) {
        return all.concat(tree.nodes.filter(function (node) { return node.kind === "move" && pet.skills.includes(node.id); }));
      }, []));
      return [back(null, 2, pet.moves.length + " of " + rules.moves + " ready")].concat(known.map(function (move) {
        const ready = pet.moves.includes(move.id);
        return { key: move.id, icon: ready ? "✓" : "○", name: move.name, hint: describeMove(move) + (ready ? " · ready" : ""), send: { action: "equip", item: move.id } };
      }));
    }
    if (view === "mods") return [back(null, 3, "Three cybertek, two magitek")].concat(progress.slots.map(function (slot, index) {
      const fitted = battle.mods.find(function (mod) { return mod.id === slot.mod; });
      const row = { key: "slot-" + index, icon: slot.tek === "magitek" ? "🔮" : "⚙", name: capital(slot.tek) + " slot",
        hint: slot.open ? (fitted ? fitted.name + " · " + describeBonus(fitted.bonus) : "Empty") : "Opens at level " + slot.opens };
      if (slot.open) row.local = function () { go("slot:" + index, 0); }; else row.note = "That slot opens at level " + slot.opens + ".";
      return row;
    }));
    if (view && view.indexOf("slot:") === 0) {
      const index = Number(view.slice(5)), slot = progress.slots[index];
      return [back("mods", 1 + index, capital(slot.tek) + " slot"), { key: "empty", icon: "○", name: "Leave empty", hint: "Take the mod out", send: { action: "mod", slot: index } }]
        .concat(battle.mods.filter(function (mod) { return mod.tek === slot.tek; }).map(function (mod) {
          const owned = pet.mods_owned.includes(mod.id), here = slot.mod === mod.id;
          const row = { key: mod.id, icon: here ? "✓" : owned ? "○" : "✧", name: mod.name,
            hint: describeBonus(mod.bonus) + " · " + (here ? "fitted here" : owned ? "yours" : mod.price + " coins") };
          if (here) row.note = mod.name + " is already fitted here."; else row.send = { action: "mod", item: mod.id, slot: index };
          return row;
        }));
    }
    // The Battle tab's front page.
    const resting = pet.faint_until > ctx.now, fitted = progress.slots.filter(function (slot) { return slot.mod; }).length;
    const front = [resting
      ? { key: "heal", icon: "☀", name: "Wake her now", hint: rules.heal + " coins · or free in " + ctx.countdown(pet.faint_until - ctx.now), send: { action: "heal" } }
      : { key: "fight", icon: "⚔", name: "Find a fight", hint: "Level " + pet.level + " · " + pet.wins + (pet.wins === 1 ? " win" : " wins"), local: function () { go("fight", 1); } },
      { key: "skills", icon: "✦", name: "Skills", hint: progress.points + (progress.points === 1 ? " point" : " points") + " to spend", local: function () { go("skills", 1); } },
      { key: "moves", icon: "🐾", name: "Moves", hint: pet.moves.length + " of " + rules.moves + " ready", local: function () { go("moves", 1); } },
      { key: "mods", icon: "⚙", name: "Mods", hint: fitted + " of " + progress.slots.length + " fitted", local: function () { go("mods", 1); } },
      { key: "potions", icon: "🧪", name: "Buy a potion", hint: rules.potion + " coins · carrying " + pet.potions + " of " + rules.potion_cap, send: { action: "potions", count: 1 } }];
    if (pet.skills.length) front.push({ key: "respec", icon: "↺", name: "Hand back skill points", hint: rules.respec + " coins · start her trees afresh", send: { action: "respec" } });
    return front;
  }

  function bars(fight) { // The numbers for the fight's health and MP bars, as percentages and labels.
    function pct(now, max) { return Math.max(0, Math.min(100, Math.round(now / Math.max(1, max) * 100))); }
    const lumi = fight.player, foe = fight.enemy;
    return { lumi: { hp: pct(lumi.hp, lumi.hpMax), mp: pct(lumi.mp, lumi.mpMax), label: "Lumi · Lv " + lumi.level + " · " + lumi.hp + "/" + lumi.hpMax + " HP · " + lumi.mp + " MP" },
      foe: { hp: pct(foe.hp, foe.hpMax), label: foe.name + " · Lv " + foe.level + " · " + capital(foe.tier) + " · " + foe.hp + "/" + foe.hpMax + " HP" } };
  }

  return { TIERS: TIERS, rows: rows, bars: bars, describeMove: describeMove, describeBonus: describeBonus };
});
