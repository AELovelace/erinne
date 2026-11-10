/* The Home and Closet tabs' lists: one row per spot in the house, or per place on Lumi, each showing one of its
   choices. ◀ ▶ flip through a row's choices, shown in the room (or on her) before anything is bought; Enter buys
   it or puts it out. Pure: it turns the server's snapshot into plain rows for game.js to draw.

   A row is { key, icon, name, hint } and may have: `options` and `shown` (the choices ◀ ▶ flip through, and the one
   showing), `flipKey` (where game.js remembers the choice), `preview` or `wear` ([spot or slot, choice] to draw),
   `label` (the choice's own name, for messages), and one of `send`, `local` or `note` as in the other lists. */
(function (root, make) {
  const ui = make();
  if (typeof module === "object" && module.exports) module.exports = ui; else root.LumiPetPicker = ui;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function turn(options, current, by) { // The choice `by` steps round from `current`, wrapping at the ends.
    const at = Math.max(0, options.indexOf(current));
    return options[(at + by + options.length) % options.length];
  }

  function capital(text) { return text[0].toUpperCase() + text.slice(1); }

  function homeRows(ctx) { // ctx: { snap, open (a spot id, or null for the list of spots), flip, go(change) }
    const snap = ctx.snap, pet = snap.pet, go = ctx.go;
    function mine(piece) { return (!piece.price && !piece.found && !piece.rarity) || pet.decor.indexOf(piece.id) >= 0; } // Free with the house, or already hers.
    function hidden(piece) { return (piece.found || piece.rarity) && !mine(piece); } // A dungeon trophy or Star Capsule prize she hasn't got: it can't be bought.
    function where(piece) { // The dungeon place a found piece comes from, by name: "the Moonlit Shrine".
      const env = ((snap.dungeon || {}).environments || []).find(function (entry) { return entry.id === piece.found; });
      return env ? "the " + env.name : "the dungeon";
    }
    const spot = snap.house.spots.find(function (entry) { return entry.id === ctx.open; });
    if (spot) return [{ key: "back", icon: "‹", name: "Back to the house", hint: spot.name, local: function () { go({ open: null, pick: snap.house.spots.indexOf(spot) }); } }]
      .concat(spot.items.map(function (piece) {
        const here = pet.house[spot.id] === piece.id, owned = mine(piece);
        const row = { key: piece.id, icon: here ? "✓" : owned ? "○" : hidden(piece) ? "🔒" : "✧", name: piece.name, label: piece.name, preview: [spot.id, piece.id],
          hint: here ? "In the room now" : owned ? "Yours · Enter puts it out" : piece.found ? "Found in " + where(piece) + (spot.id === "trophy" ? "'s vault" : " · a rare find") : piece.rarity ? capital(piece.rarity) + " · only from a capsule" : piece.price + " coins · Enter buys it" };
        if (here) row.note = piece.name + " is already in the room."; else if (!hidden(piece)) row.send = { action: "decorate", item: piece.id };
        else row.note = piece.found ? piece.name + " can't be bought. It's waiting somewhere in " + where(piece) + "." : piece.name + " only comes from a capsule.";
        return row;
      }));
    return snap.house.spots.map(function (entry) {
      const choices = entry.items.filter(function (piece) { return !hidden(piece); }); // A trophy or Capsule prize joins the row once she has it.
      const ids = choices.map(function (piece) { return piece.id; }), placed = pet.house[entry.id];
      const shown = ids.indexOf(ctx.flip[entry.id]) >= 0 ? ctx.flip[entry.id] : placed;
      const piece = choices.find(function (found) { return found.id === shown; }) || choices[0];
      const row = { key: entry.id, icon: "⌂", name: entry.name, label: piece.name, options: ids, shown: piece.id, flipKey: entry.id, preview: [entry.id, piece.id],
        hint: "◀ " + piece.name + " ▶ · " + (piece.id === placed ? "in the room" : mine(piece) ? "yours · Enter puts it out" : piece.price + " coins · Enter buys it") };
      if (piece.id === placed) row.local = function () { go({ open: entry.id }); }; else row.send = { action: "decorate", item: piece.id };
      return row;
    });
  }

  function capsuleRows(snap) { // The two capsules. Sold at the top of the Closet and of the Shop: they give clothes, toys and things for the house.
    const capsule = snap.closet.capsule, moon = snap.closet.moon, REPEAT = " · a repeat pays back half its value";
    return [{ key: "capsule", icon: "🔮", name: capsule.name, hint: capsule.price + " coins · something to wear, a toy or something for the house" + REPEAT, send: { action: "capsule" } },
      { key: "capsule-moon", icon: "🌙", name: moon.name, hint: moon.price + " coins · rare, epic or legendary only" + REPEAT, send: { action: "capsule", item: "moon" } }];
  }

  function closetRows(ctx) { // ctx as for homeRows; `open` is a slot id, or null for the list of places on Lumi.
    const snap = ctx.snap, pet = snap.pet, go = ctx.go;
    const CAPSULES = 2; // The capsule rows come first, so a slot's place in the list is this many further down.
    function status(piece, worn) {
      return worn ? "wearing it" : pet.closet.indexOf(piece.id) >= 0 ? "hers · Enter puts it on" : piece.price ? piece.price + " coins · Enter buys it" : "only from a capsule";
    }
    const slot = snap.closet.slots.find(function (entry) { return entry.id === ctx.open; });
    if (slot) return [{ key: "back", icon: "‹", name: "Back to the closet", hint: slot.name, local: function () { go({ open: null, pick: CAPSULES + snap.closet.slots.indexOf(slot) }); } }]
      .concat(slot.items.map(function (piece) {
        const owned = pet.closet.indexOf(piece.id) >= 0, worn = pet.outfit[slot.id] === piece.id;
        const row = { key: piece.id, icon: worn ? "✓" : owned ? "○" : piece.price ? "✧" : "🔒", name: piece.name, label: piece.name, wear: [slot.id, piece.id],
          hint: capital(piece.rarity) + " · " + (worn ? "wearing it · Enter takes it off" : status(piece, false)) };
        if (owned || piece.price) row.send = { action: "wear", item: piece.id }; else row.note = piece.name + " only comes from a capsule.";
        return row;
      }));
    return capsuleRows(snap)
      .concat(snap.closet.slots.map(function (entry) {
        const ids = [null].concat(entry.items.map(function (piece) { return piece.id; })), worn = pet.outfit[entry.id] || null, key = "slot:" + entry.id;
        const shown = Object.prototype.hasOwnProperty.call(ctx.flip, key) && ids.indexOf(ctx.flip[key]) >= 0 ? ctx.flip[key] : worn;
        const piece = entry.items.find(function (found) { return found.id === shown; });
        const row = { key: "slot-" + entry.id, icon: worn ? "✓" : "○", name: entry.name, label: piece ? piece.name : "nothing", options: ids, shown: shown, flipKey: key, wear: [entry.id, shown],
          hint: "◀ " + (piece ? piece.name : "Nothing") + " ▶ · " + (piece ? capital(piece.rarity) + " · " + status(piece, shown === worn) : shown === worn ? "nothing on" : "Enter takes it off") };
        if (shown === worn) row.local = function () { go({ open: entry.id }); };       // Nothing to change: open the whole list instead.
        else if (!piece) row.send = { action: "wear", item: worn };                      // "Nothing": take off what she has on.
        else if (pet.closet.indexOf(piece.id) >= 0 || piece.price) row.send = { action: "wear", item: piece.id };
        else row.note = piece.name + " only comes from a capsule.";
        return row;
      }));
  }

  return { turn: turn, homeRows: homeRows, closetRows: closetRows, capsuleRows: capsuleRows };
});
