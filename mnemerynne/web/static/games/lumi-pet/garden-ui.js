/* The Stardust Garden's lists, numbers and status lines (mnemerynne/lumigarden.py does the gardening). Pure: it turns
   the server's view of the garden into plain rows for game.js to draw, and never works out a price or a result.

   A row is { key, icon, name, hint } and one of: `send` with url "garden" (a choice for the server), `local` (a
   function: open a drawer, press the press) or `note` (why it can't be chosen). `confirm` asks for a second Enter. */
(function (root, make) {
  const ui = make();
  if (typeof module === "object" && module.exports) module.exports = ui; else root.LumiPetGarden = ui;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const COLUMNS = 4; // Plots are drawn four to a row (garden-art.js draws the same grid).
  const UNITS = ["", "K", "M", "B", "T"];

  function format(n) { // 950 · 1.2K · 3.4M · 5.6B · 7.8T · then 1.2aa, 3.4ab …: short enough for a TV.
    n = Math.max(0, Number(n) || 0);
    if (n < 1000) return n < 10 && n % 1 ? n.toFixed(1) : String(Math.floor(n));
    let step = Math.min(Math.floor(Math.log10(n) / 3), 5 + 26 * 26 - 1);
    let shown = n / Math.pow(1000, step);
    if (shown >= 999.95) { step += 1; shown = n / Math.pow(1000, step); } // 999,960 is 1.0M, not 1000.0K.
    const unit = step < UNITS.length ? UNITS[step] : String.fromCharCode(97 + Math.floor((step - 5) / 26)) + String.fromCharCode(97 + (step - 5) % 26);
    return (shown < 100 ? shown.toFixed(1) : String(Math.floor(shown))) + unit;
  }

  function clock(ms) { // "1:05" · "2:03:00": how long until something is ready; never negative.
    const s = Math.max(0, Math.ceil(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), pad = function (v) { return String(v).padStart(2, "0"); };
    return h ? h + ":" + pad(m) + ":" + pad(s % 60) : m + ":" + pad(s % 60);
  }

  function span(minutes) { // "2 min" · "1 h" · "1 h 30": a flower's growing time.
    minutes = Math.max(1, Math.round(minutes));
    return minutes < 60 ? minutes + " min" : Math.floor(minutes / 60) + " h" + (minutes % 60 ? " " + minutes % 60 : "");
  }

  function left(garden, ms, passed) { return ms - passed; } // A server time, counted down by how long ago it was told.

  function stage(plot, passed) { // The plot's stage right now: a growing flower turns ready on this page without asking.
    return plot.stage === "growing" && plot.ready_in - passed <= 0 ? "ready" : plot.stage;
  }

  function flowerOf(garden, id) { return garden.flowers.find(function (flower) { return flower.id === id; }); }

  function seedFor(garden, plot, chosen) { // What Enter plants in an empty plot: the chosen seed, else what grew there last, else the best unlocked.
    const open = garden.flowers.filter(function (flower) { return flower.unlocked; }).map(function (flower) { return flower.id; });
    if (open.indexOf(chosen) >= 0) return chosen;
    return plot && open.indexOf(plot.last) >= 0 ? plot.last : open[open.length - 1];
  }

  function cells(garden) { return garden.plots.length + (garden.plot_price === null ? 0 : 1); } // The plots, and one more to dig if there is room.

  function moveCursor(cursor, action, count) { // One step over the grid; stays put at an edge.
    const x = cursor % COLUMNS, y = Math.floor(cursor / COLUMNS);
    const to = action === "left" ? (x ? cursor - 1 : cursor) : action === "right" ? (x < COLUMNS - 1 ? cursor + 1 : cursor) : action === "up" ? cursor - COLUMNS : cursor + COLUMNS;
    return to < 0 || to >= count ? cursor : to;
  }

  function plotChoice(garden, cursor, chosen, passed) { // What Enter (or a press of the mouse) does on a plot: a request for the server, or words.
    if (cursor === garden.plots.length && garden.plot_price !== null) return { send: { action: "plot" }, confirm: "Dig a new plot for " + garden.plot_price + " coins?" };
    const plot = garden.plots[cursor];
    if (!plot) return { say: "There's no plot there." };
    const now = stage(plot, passed);
    if (now === "empty") return { send: { action: "plant", plot: cursor, item: seedFor(garden, plot, chosen) } };
    if (now === "growing") return { say: flowerOf(garden, plot.flower).name + ": ready in " + clock(plot.ready_in - passed) + "." };
    return { send: { action: "trim", plot: cursor } };
  }

  function plotLine(garden, cursor, chosen, passed) { // One line about the plot under the cursor.
    if (cursor === garden.plots.length && garden.plot_price !== null) return "A patch of grass · Enter digs a new plot for " + garden.plot_price + " coins";
    const plot = garden.plots[cursor];
    if (!plot) return "";
    const now = stage(plot, passed), flower = plot.flower ? flowerOf(garden, plot.flower) : null;
    if (now === "empty") return "Plot " + (cursor + 1) + ": empty · Enter plants " + flowerOf(garden, seedFor(garden, plot, chosen)).name;
    if (now === "growing") return "Plot " + (cursor + 1) + ": " + flower.name + " · ready in " + clock(plot.ready_in - passed);
    return "Plot " + (cursor + 1) + ": " + flower.name + (now === "overgrown" ? " · overgrown, half its powder · Enter trims" : " · ready! Enter trims");
  }

  function status(garden, cursor, chosen, passed, pending) { // The lines over the list: the garden at a glance.
    const rates = garden.rates, ready = garden.plots.filter(function (plot) { const now = stage(plot, passed); return now === "ready" || now === "overgrown"; }).length;
    const lines = ["Garden " + (garden.at + 1) + (garden.gardens > 1 ? " of " + garden.gardens : "") + " · " + garden.plots.length + " plots" + (ready ? " · " + ready + " ready to trim" : ""),
      plotLine(garden, cursor, chosen, passed),
      "Starpowder waiting: " + format(Math.max(0, garden.powder - pending * rates.tap)) + " · this garden has given " + format(garden.given),
      "Next coin: " + format(garden.squeezed) + " of " + format(garden.price) + " squeezed · " + garden.coins_today + " coins today (each " + garden.band + " costs double)",
      "A press squeezes " + format(rates.tap) + " · " + Math.round(rates.lucky * 100) + "% lucky (×" + rates.lucky_by + ")" + (rates.idle ? " · the press does " + format(rates.idle * rates.double) + " a second alone" : "")];
    const effects = garden.effects || {};
    const running = [["frenzy", "Frenzy"], ["lucky", "Lucky streak"], ["double", "Blessing"]].filter(function (pair) { return effects[pair[0]] - passed > 0; })
      .map(function (pair) { return pair[1] + " " + clock(effects[pair[0]] - passed); });
    if (running.length) lines.push(running.join(" · "));
    return lines.filter(Boolean);
  }

  function gift(found, name) { // What a harvest turned up, in words. `name(id)`: a snack's name.
    if (!found) return "";
    if (found.kind === "token") return " And a Star Capsule token in the petals: " + found.coins + " coins, enough for a capsule!";
    if (found.kind === "potion") return " And a potion, hidden under a leaf!";
    if (found.kind === "snack") return " And a snack for the pantry: " + name(found.item) + "!";
    return " And a puff of extra starpowder: +" + format(found.powder) + "!";
  }

  function said(send, note, garden, name) { // What just happened, for the message line. Empty: say nothing new.
    note = note || {};
    const flower = send.item ? flowerOf(garden, send.item) : null;
    if (send.action === "plant") return "Planted: " + flower.name + ". Ready in " + span(flower.minutes_now) + ".";
    if (send.action === "trim" || send.action === "trim_all") return "Snip! " + (note.trimmed === 1 ? "One flower" : note.trimmed + " flowers") + " trimmed: +" + format(note.powder) + " starpowder." + gift(note.gift, name);
    if (send.action === "plot") return "A new plot, dug and raked, for " + note.paid + " coins.";
    if (send.action === "unlock") return "Unlocked for this garden: " + flower.name + "! " + (flower.sort === "perennial" ? "It grows back after every trim." : "Plant it again after each harvest.");
    if (send.action === "cosmetic") return "The garden has something new. Lumi walks round it twice, approving.";
    if (send.action === "helper") return garden.helpers.find(function (helper) { return helper.id === note.helper; }).name + " is now level " + note.level + ".";
    if (send.action === "ability") {
      if (note.ability === "monk") return "Lumi breathes out. " + format(note.powder) + " starpowder squeezed at once" + (note.coins ? ": +" + note.coins + " coins." : ".");
      if (note.ability === "mage") return "A burst of sunshine! " + note.moved + (note.moved === 1 ? " flower jumps" : " flowers jump") + " ahead.";
      if (note.ability === "cleric") return "Morning dew settles. " + note.moved + (note.moved === 1 ? " overgrown flower is" : " overgrown flowers are") + " fresh again.";
      return { fighter: "Frenzy! Every press counts for much more.", thief: "Lucky streak! Every press is a lucky one.", paladin: "A blessing settles on the garden. Everything is doubled." }[note.ability];
    }
    if (send.action === "water_start") return "Watch the watering cans, then repeat them.";
    if (send.action === "water_answer") {
      if (note.water === "more") return "Right! Round " + note.round + ": watch again.";
      return (note.right ? "Every round, perfectly! " : "Oops, a slip. ") + (note.rounds ? note.rounds + (note.rounds === 1 ? " round" : " rounds") + " of water: " +
        (note.moved ? "every growing flower jumps ahead." : "but nothing was growing.") : "No water this time.");
    }
    if (send.action === "feed_start") return "Fertilizer! Press as fast as you can!";
    if (send.action === "feed_end") return note.presses + " presses: " + (note.minutes ? (note.moved ? "every growing flower is " + note.minutes + (note.minutes === 1 ? " minute" : " minutes") + " further on." : "but nothing was growing.") : "not quite enough to help.");
    if (send.action === "moonrise") return send.item === "garden" ? "A new moon rises over a second patch of earth. Another garden is yours!" : "A new moon rises. This garden starts afresh, and something is better for good.";
    if (send.action === "garden") return "Lumi trots over to garden " + (garden.at + 1) + ".";
    if (send.action === "press") return note.coins ? (note.lucky ? "Lucky squeeze! " : "Squeeze! ") + "+" + note.coins + (note.coins === 1 ? " coin." : " coins.") : "";
    return "";
  }

  function away(garden) { // What happened while nobody was looking, to say once on coming back.
    const gone = garden.away;
    if (!gone || !gone.powder) return "";
    return "While you were away the press squeezed " + format(gone.powder) + " starpowder" + (gone.coins ? " into " + gone.coins + (gone.coins === 1 ? " coin." : " coins.") : ".");
  }

  function cool(ms, passed) { return ms - passed > 0 ? "ready in " + clock(ms - passed) : ""; }

  // ---- The lists. ctx: { garden, drawer (null, "seeds", "helpers", "shop", "moon"), seed, cursor, mini, passed, pending, level, go(change) }
  function rows(ctx) {
    const garden = ctx.garden, go = ctx.go, passed = ctx.passed || 0, mini = ctx.mini;
    function ask(key, icon, name, hint, send, more) { return Object.assign({ key: key, icon: icon, name: name, hint: hint, url: "garden", send: send }, more || {}); }
    function back(name) { return { key: "back", icon: "‹", name: name || "Back to the garden", hint: "The plots and the press", local: function () { go({ drawer: null }); } }; }
    if (mini && mini.kind === "water") return [{ key: "watering", icon: "💧", name: mini.phase === "show" ? "Watch the cans…" : "Your turn: repeat them",
      hint: "Round " + mini.show.length + " of " + garden.water.rounds + " · ▲ ▶ ▼ ◀ or press a can", note: "Repeat the cans with the arrows, or press them." }];
    if (mini && mini.kind === "feed") return [{ key: "feeding", icon: "🌱", name: "Fertilize! " + mini.count + " presses", hint: "Enter, ⏯ or the big button, as fast as you can",
      local: function () { go({ feed: 1 }); } }];

    if (ctx.drawer === "seeds") return [back()].concat(garden.flowers.map(function (flower) {
      const what = span(flower.minutes_now) + " · " + format(flower.powder_now) + " powder · " + (flower.sort === "perennial" ? "grows back" : "plant again each time");
      if (flower.unlocked) return { key: "seed-" + flower.id, icon: flower.id === ctx.seed ? "✓" : "❀", name: flower.name, hint: what, local: function () { go({ seed: flower.id, drawer: null }); } };
      const cost = flower.stardust ? format(flower.stardust) + " starpowder" : flower.coins + " coins";
      if (!flower.next) return { key: "seed-" + flower.id, icon: "🔒", name: flower.name, hint: "Further up the ladder · " + what, note: "Unlock the flowers before " + flower.name + " first." };
      if (garden.given < flower.needs) return { key: "seed-" + flower.id, icon: "🔒", name: flower.name, hint: "When this garden has given " + format(flower.needs) + " powder (" + format(garden.given) + " so far) · then " + cost,
        note: flower.name + " opens when this garden has given " + format(flower.needs) + " starpowder." };
      if (ctx.level < flower.level) return { key: "seed-" + flower.id, icon: "🔒", name: flower.name, hint: "Needs Lumi at level " + flower.level + " · then " + cost, note: flower.name + " needs Lumi to be level " + flower.level + "." };
      return ask("seed-" + flower.id, "✧", "Unlock " + flower.name, cost + " · " + what, { action: "unlock", item: flower.id });
    }));
    if (ctx.drawer === "helpers") return [back()].concat(garden.helpers.map(function (helper) {
      return ask("helper-" + helper.id, "✦", helper.name + " · level " + helper.level, format(helper.cost) + " starpowder · " + helper.about, { action: "helper", item: helper.id });
    }));
    if (ctx.drawer === "shop") return [back()].concat(garden.cosmetics.map(function (thing) {
      return thing.owned ? { key: "look-" + thing.id, icon: "✓", name: thing.name, hint: "In the garden · " + thing.about, note: thing.name + " is already in the garden." }
        : ask("look-" + thing.id, "✧", thing.name, format(thing.stardust) + " starpowder · " + thing.about, { action: "cosmetic", item: thing.id });
    }));
    if (ctx.drawer === "moon") return [back()].concat(
      [ask("moon-garden", "☾", "Another garden", "A new starter garden beside this one, to fill from the first flower", { action: "moonrise", item: "garden" }, { confirm: true })],
      garden.upgrades.map(function (thing) {
        return thing.count >= thing.max ? { key: "moon-" + thing.id, icon: "✓", name: thing.name, hint: "As high as it goes (" + thing.count + ")", note: thing.name + " is as high as it goes." }
          : ask("moon-" + thing.id, "✦", thing.name, thing.about + " · for good · have " + thing.count, { action: "moonrise", item: thing.id }, { confirm: true });
      }));

    const ready = garden.plots.filter(function (plot) { const now = stage(plot, passed); return now === "ready" || now === "overgrown"; }).length;
    const growing = garden.plots.some(function (plot) { return stage(plot, passed) === "growing"; });
    const list = [{ key: "press", icon: "◎", name: "Press the powder", hint: "⏯ presses too · " + format(Math.max(0, garden.powder - (ctx.pending || 0) * garden.rates.tap)) + " waiting", local: function () { go({ press: 1 }); } }];
    list.push(ready ? ask("trim-all", "✂", "Harvest everything ready (" + ready + ")", "Trims them all, and replants the cut flowers", { action: "trim_all" })
      : { key: "trim-all", icon: "✂", name: "Harvest everything ready", hint: growing ? "Nothing is ready yet" : "Nothing is planted", note: "Nothing is ready to trim yet." });
    const seed = flowerOf(garden, seedFor(garden, null, ctx.seed));
    list.push({ key: "seeds", icon: "❀", name: "Seed: " + seed.name, hint: "What Enter plants in an empty plot · open the seed drawer", local: function () { go({ drawer: "seeds" }); } });
    [["water", "💧", "Water the flowers", "Repeat the cans: up to " + garden.water.rounds * garden.water.share + "% of every flower's time", "water_start"],
      ["feed", "🌱", "Fertilize", garden.feed.seconds + " seconds of fast presses: up to " + garden.feed.max + " minutes for every flower", "feed_start"]].forEach(function (game) {
      const wait = cool(garden[game[0]].ready_in, passed);
      list.push(wait ? { key: game[0], icon: game[1], name: game[2], hint: wait, note: "Not yet: " + wait + "." } : ask(game[0], game[1], game[2], game[3], { action: game[4] }));
    });
    garden.moves.forEach(function (move) {
      const wait = cool(move.ready_in, passed), name = move.name + " (" + move.move + ")";
      list.push(wait ? { key: "move-" + move.id, icon: "✦", name: name, hint: wait, note: move.name + ": " + wait + "." }
        : ask("move-" + move.id, "✦", name, move.about, { action: "ability", item: move.id }));
    });
    list.push({ key: "helpers", icon: "♟", name: "Helpers", hint: "Six class helpers, levelled with starpowder", local: function () { go({ drawer: "helpers" }); } });
    list.push({ key: "shop", icon: "✧", name: "Garden shop", hint: "Fences, gnomes and other fine things, for starpowder", local: function () { go({ drawer: "shop" }); } });
    list.push(garden.moonrise.ready ? { key: "moon", icon: "☾", name: "Moonrise", hint: "Replant this garden for something that lasts", local: function () { go({ drawer: "moon" }); } }
      : { key: "moon", icon: "☾", name: "Moonrise", hint: "When this garden has all its plots and has given " + format(garden.moonrise.needs) + " powder",
        note: "A moonrise needs every plot dug and " + format(garden.moonrise.needs) + " starpowder given by this garden (" + format(garden.given) + " so far)." });
    if (garden.gardens > 1) list.push(ask("other", "⇄", "Go to garden " + ((garden.at + 1) % garden.gardens + 1), "You have " + garden.gardens + " gardens", { action: "garden", count: (garden.at + 1) % garden.gardens }));
    list.push({ key: "leave", icon: "♡", name: "Back to Lumi", hint: "The garden keeps growing without you", local: function () { go({ leave: true }); } });
    return list;
  }

  return { COLUMNS: COLUMNS, format: format, clock: clock, span: span, stage: stage, seedFor: seedFor, cells: cells, moveCursor: moveCursor, plotChoice: plotChoice,
    status: status, said: said, away: away, rows: rows, left: left };
});
