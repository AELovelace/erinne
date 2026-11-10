/* The Collection tab: everything Lumi can collect, in one place, with what she has and where the rest comes from.
   Pure: it reads the catalogs the server already sends (house, closet, toys, mods, garden flowers and decorations)
   and what the pet owns, and makes plain rows for game.js. Nothing here can be bought or changed: it is an album.

   A row is { key, icon, name, hint } with `local` (open a shelf) or `note` (words when chosen), and may carry
   something for game.js to show while it is highlighted: `preview` ([house spot, object]), `wear` ([slot,
   accessory]) or `card` ({ kind: "flower" | "decor", ... }, drawn by garden-art.js). */
(function (root, make) {
  const ui = make();
  if (typeof module === "object" && module.exports) module.exports = ui; else root.LumiPetCollection = ui;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function grand(word) { return word ? word[0].toUpperCase() + word.slice(1) : ""; }

  function shelves(snap) { // Every shelf of the album: [{ id, icon, name, about, items: [{ id, name, owned, from, count?, preview?, wear?, card? }] }]
    const pet = snap.pet, places = ((snap.dungeon || {}).environments || []), capsule = ((snap.garden_decor || {}).capsule || {}).name || "Seedling Capsule";
    function place(id) { const env = places.find(function (entry) { return entry.id === id; }); return env ? env.name : "the dungeon"; }
    const home = [], finds = [];
    ((snap.house || {}).spots || []).forEach(function (spot) {
      spot.items.forEach(function (piece) {
        if (!piece.price && !piece.found && !piece.rarity && spot.items.indexOf(piece) === 0 && /-none$/.test(piece.id)) return; // "Empty bracket": a way to show nothing, not a thing to collect.
        const owned = (!piece.price && !piece.found && !piece.rarity) || pet.decor.indexOf(piece.id) >= 0;
        const item = { id: piece.id, name: piece.name, owned: owned, preview: [spot.id, piece.id],
          from: spot.name + " · " + (piece.found ? "found in " + place(piece.found) : piece.rarity ? grand(piece.rarity) + " · from a capsule" : piece.price ? "the Home tab, " + piece.price + " coins" : "came with the house") };
        (piece.found ? finds : home).push(item);
      });
    });
    const clothes = [];
    ((snap.closet || {}).slots || []).forEach(function (slot) {
      slot.items.forEach(function (piece) {
        clothes.push({ id: piece.id, name: piece.name, owned: pet.closet.indexOf(piece.id) >= 0, wear: [slot.id, piece.id],
          from: slot.name + " · " + grand(piece.rarity) + " · " + (piece.price ? "the Closet tab, " + piece.price + " coins" : "from a capsule") });
      });
    });
    const toys = ((snap.catalog || {}).toys || []).map(function (toy) {
      return { id: toy.id, name: toy.icon + " " + toy.name, owned: (!toy.price && !toy.rarity) || pet.toys.indexOf(toy.id) >= 0,
        from: toy.rarity ? grand(toy.rarity) + " · from a capsule" : toy.price ? "the Shop, " + toy.price + " coins" : "came with Lumi" };
    });
    const mods = ((snap.battle || {}).mods || []).map(function (mod) {
      return { id: mod.id, name: mod.name, owned: pet.mods_owned.indexOf(mod.id) >= 0, from: grand(mod.tek) + " mod · " + (mod.price ? "Battle → Mods, " + mod.price + " coins" : "found in dungeon vaults") };
    });
    const garden = snap.garden || { flowers: [], decor: { owned: {} } };
    const flowers = garden.flowers.map(function (flower) {
      return { id: flower.id, name: flower.name, owned: flower.unlocked, card: { kind: "flower", id: flower.id },
        from: (flower.sort === "perennial" ? "Perennial" : "Cut flower") + " · " + (flower.stardust ? "boutique, for starpowder" : flower.coins ? flower.coins + " coins" : "every garden starts with it") + " · on the ladder of the garden you are in" };
    });
    const dressing = [];
    ((snap.garden_decor || {}).slots || []).forEach(function (slot) {
      slot.items.forEach(function (piece) {
        const count = (garden.decor.owned || {})[piece.id] || 0;
        dressing.push({ id: piece.id, name: piece.name, owned: count > 0, count: count, card: { kind: "decor", slot: slot.id, id: piece.id },
          from: slot.name + " · " + (piece.rarity === "shop" ? "Garden → Decorations, " + piece.stardust + " starpowder" : grand(piece.rarity) + " · from the " + capsule) });
      });
    });
    return [
      { id: "home", icon: "⌂", name: "For the house", about: "Bought, or won from capsules", items: home },
      { id: "finds", icon: "🏆", name: "Dungeon finds", about: "Trophies and rare pieces from each place", items: finds },
      { id: "closet", icon: "🎀", name: "Things to wear", about: "Bought, or won from capsules", items: clothes },
      { id: "toys", icon: "🧸", name: "Toys", about: "From the Shop and from capsules", items: toys },
      { id: "mods", icon: "⚙", name: "Mods", about: "Bought, or found in dungeon vaults", items: mods },
      { id: "flowers", icon: "❀", name: "Flowers", about: "The garden's ladder, for the garden you are in", items: flowers },
      { id: "garden", icon: "✧", name: "Garden decorations", about: "Bought with starpowder, or won from the " + capsule, items: dressing },
    ].filter(function (shelf) { return shelf.items.length; });
  }

  function tally(items) { return items.filter(function (item) { return item.owned; }).length; }

  function rows(ctx) { // ctx: { snap, open (a shelf id, or null for the list of shelves), go(change) }
    const all = shelves(ctx.snap), shelf = all.find(function (entry) { return entry.id === ctx.open; });
    if (shelf) return [{ key: "back", icon: "‹", name: "Back to the collection", hint: shelf.name + " · " + tally(shelf.items) + " of " + shelf.items.length, local: function () { ctx.go({ open: null, pick: 1 + all.indexOf(shelf) }); } }]
      .concat(shelf.items.map(function (item) {
        const row = { key: "have-" + item.id, icon: item.owned ? "✓" : "·", name: item.name + (item.count > 1 ? " ×" + item.count : ""), hint: item.from,
          note: item.owned ? item.name + " is in Lumi's collection." : item.name + " isn't hers yet: " + item.from + "." };
        ["preview", "wear", "card"].forEach(function (show) { if (item[show]) row[show] = item[show]; });
        return row;
      }));
    const have = all.reduce(function (sum, entry) { return sum + tally(entry.items); }, 0), total = all.reduce(function (sum, entry) { return sum + entry.items.length; }, 0);
    return [{ key: "all", icon: "★", name: "Collected: " + have + " of " + total, hint: Math.floor(100 * have / Math.max(1, total)) + "% of everything there is to find",
      note: "Lumi has " + have + " of the " + total + " things there are to collect." }]
      .concat(all.map(function (entry) {
        const got = tally(entry.items);
        return { key: "shelf-" + entry.id, icon: got === entry.items.length ? "✓" : entry.icon, name: entry.name, hint: got + " of " + entry.items.length + " · " + entry.about, local: function () { ctx.go({ open: entry.id }); } };
      }));
  }

  return { shelves: shelves, rows: rows };
});
