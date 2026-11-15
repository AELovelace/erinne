/* Lumi Town (Doll's design, 2026-11-12): the town map behind her room's door. Six places: her house (Lumi Pet), the
   Lantern & Paw tavern (Lumi Dungeons), the Stardust Garden (Lumi's Garden), and three you walk into, each with its own
   list of games: the Arcade, the Casino (Lumi's Lounge tables) and the Lumi-Soft field.

   This file is the town's words and lists: which places there are, what is inside each, and the rows the page shows
   (game.js reads them like every other tab's rows). town-art.js draws it all. A game opened from the town comes back
   to it when it closes (media.js games.back), so "leaving" a game is walking back out into the town. */
(function (root, make) {
  const town = make();
  if (typeof module === "object" && module.exports) module.exports = town; else root.LumiPetTown = town;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // The places, in the map's reading order (top row left to right, then the bottom row): the list's order and the
  // remote's. An `app` is one of Lumi's own apps, opened straight away (`going` is what she says on the way, and `home`
  // means leaving it doesn't come back here: it's her house); `games` are what the place's inside offers.
  const PLACES = [
    { id: "arcade", icon: "🕹", name: "Arcade", hint: "Yarn Pop, Lumi Conga, Teacup Tower, Prism Drop and Lumi Launch", room: "THE ARCADE",
      hello: "Bleeps, bloops and a carpet full of stars. Which cabinet?",
      games: [
        { slug: "breakout", icon: "🧶", name: "Yarn Pop", hint: "Bounce Lumi off a cushion and pop every block" },
        { slug: "snake", icon: "💃", name: "Lumi Conga", hint: "Lead a conga line of Lumis round the room" },
        { slug: "teacup-tower", icon: "🫖", name: "Teacup Tower", hint: "Bounce up an endless tower of teacups" },
        { slug: "prism-drop", icon: "🔮", name: "Prism Drop", hint: "Drop her glowing ball through the blocks" },
        { slug: "lumi-launch", icon: "🎇", name: "Lumi Launch", hint: "Fire her from the star cannon and see how far she flies" },
      ] },
    { id: "tavern", icon: "🗝", name: "Tavern", hint: "The Lantern & Paw: dungeon trips, the arena and skills (Lumi Dungeons)", app: "lumi-dungeons", going: "Into the Lantern & Paw! 🍺" },
    { id: "casino", icon: "🎰", name: "Casino", hint: "Lumi's Lounge: Blackjack, Roulette, Horse Racing and Slots", room: "LUMI’S LOUNGE",
      hello: "Lumi's Lounge. The dealer winks. Pick a table.",
      games: [
        { slug: "blackjack", icon: "🂡", name: "Blackjack", hint: "Hit or stand, and beat Dealer Lumi" },
        { slug: "roulette", icon: "🎡", name: "Roulette", hint: "Pick a number and watch the wheel" },
        { slug: "horse-racing", icon: "🏇", name: "Horse Racing", hint: "Back a pony and cheer her home" },
        { slug: "slots", icon: "🎰", name: "Slot Machines", hint: "Three reels, three paylines" },
      ] },
    { id: "home", icon: "🏠", name: "Lumi's house", hint: "Back to her room (Lumi Pet)", app: "lumi-pet", home: true, going: "Home again! 🏠" },
    { id: "garden", icon: "🌸", name: "Garden", hint: "The Stardust Garden: flowers, starpowder and the press (Lumi's Garden)", app: "lumi-garden", going: "Through the gate into the Stardust Garden! 🌸" },
    { id: "field", icon: "🎯", name: "Lumi-Soft field", hint: "Pop cardboard creatures through the Star Scope", room: "THE LUMI-SOFT FIELD",
      hello: "Soft grass, a comfy cushion, and cardboard creatures waiting in the long grass.",
      games: [
        { slug: "lumi-soft", icon: "🎯", name: "Lumi-Soft", hint: "Five stages, bosses and a Daily Range", desk: true },
      ] },
  ];

  function place(id) { return PLACES.find(function (p) { return p.id === id; }) || null; } // A place by its id, or null.

  function inside(id) { const p = place(id); return p && p.games ? p : null; } // A place you walk into (it has games), or null.

  function index(id) { return Math.max(0, PLACES.findIndex(function (p) { return p.id === id; })); } // Where a place sits in the map's list.

  function arrive(at) { // Where a page opened at ?at=<place> starts: inside that place, or on the map in front of it.
    const p = place(at);
    return { inside: p && p.games ? p.id : null, cursor: index(p ? p.id : "tavern") }; // No place given: in front of the tavern, the way out on an adventure.
  }

  function rows(ctx) { // The town's list. ctx: at (the place she's inside, or null for the map), desk (a mouse and keyboard
    // are here: Lumi-Soft needs both), away (she's out on a dungeon trip), go({enter}|{leave}) and open(slug, place, words).
    const here = inside(ctx.at);
    if (!here) return PLACES.map(function (p) { // The map: one row per place.
      const hint = p.id === "tavern" && ctx.away ? "Lumi is out exploring: find her in the Lantern & Paw" : p.hint;
      return { key: "place-" + p.id, icon: p.icon, name: p.name, hint: hint, local: function () { ctx.go({ enter: p.id }); } };
    });
    return here.games.map(function (g) { // Inside: a row per game, then the way back out.
      const row = { key: "game-" + g.slug, icon: g.icon, name: g.name, hint: g.hint };
      if (g.desk && !ctx.desk) row.note = g.name + " needs a mouse and keyboard: play it on a computer."; // A TV remote or a phone can't aim the Star Scope.
      else row.local = function () { ctx.open(g.slug, here.id, "Let's play " + g.name + "! ✨"); };
      return row;
    }).concat([{ key: "leave", icon: "🗺", name: "Back to the town map", hint: "Out the door and into Lumi Town", local: function () { ctx.go({ leave: true }); } }]);
  }

  return { PLACES: PLACES, place: place, inside: inside, index: index, arrive: arrive, rows: rows };
});
