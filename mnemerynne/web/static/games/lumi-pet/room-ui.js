/* Room mode (Doll's design, 2026-11-19): Lumi Pet without menus. Lumi walks through her flat, the kitchen, her
   bedroom and the den, and each thing in it stands for something to do: the fridge is the Pantry, the bed is a nap,
   the wardrobe is the Closet, the terminal is Settings. Like Microsoft Bob, with Lumi as the guide.

   Nothing here is new game logic. A thing opens a "card", which is the same rows the tab's list shows (game.js
   rows()), sometimes narrowed to a few of them (the potion shelf shows just the potion). So the lists and the rooms can
   never disagree. This file is pure: the rooms and their things, where Lumi stands, which way an arrow takes her,
   the rows of the room and of a card, and the invisible press boxes over the drawing (room-art.js draws the things).

   A room is one 1000 x 500 picture like her bedroom (house.js). Every screen shows about x 290-710 of it, so
   everything pressable sits there. Walking is "snap" walking: ◀ ▶ take her from one thing to the next, and past
   the last thing into the next room, so a TV remote never has to steer. */
(function (root, make) {
  const ui = make();
  if (typeof module === "object" && module.exports) module.exports = ui; else root.LumiPetRoom = ui;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // The flat, left to right. Each thing: where Lumi stands to use it (x), its press box over the drawing [x, y, w, h],
  // which tab's rows it opens (tab), and optionally only some of them (keys), and what's said when that leaves none (empty).
  // Lumi's own words, always first person: `say` is her bubble over it in the lower bar (what it is), `open` what she
  // says when its menu opens. act: "door" goes straight out through her front door (no menu). In the bedroom, the boxes
  // sit over house.js's own furniture (its 400-wide block starts at x 300); in the kitchen and the den over room-art.js's things.
  const ROOMS = [
    { id: "kitchen", name: "HER KITCHEN", things: [
      { id: "fridge", icon: "🧊", name: "My fridge", hint: "Snacks and treats", say: "My fridge! Snacks and treats live in here.", open: "Something tasty? I'll share!",
        x: 380, box: [325, 160, 110, 265], tab: "pantry" },
      { id: "bowl", icon: "🥣", name: "My bowl", hint: "Free snacks, always there", say: "My bowl! It's never, ever empty.", open: "Ooh, what's in my bowl today?",
        x: 500, box: [455, 395, 90, 70], tab: "pantry" },
      { id: "potions", icon: "🧪", name: "My potion shelf", hint: "Mends me after a fight", say: "My potions, for when I'm hurt after a fight.", open: "Glug glug!",
        x: 620, box: [565, 215, 110, 160], tab: "care", keys: ["drink"], empty: "I'm fine just now! Potions are for after a fight." },
    ] },
    { id: "bedroom", name: "HER BEDROOM", things: [
      { id: "door", icon: "🚪", name: "My front door", hint: "Out into Lumi Town", say: "My front door! Shall we go out into town?", open: "Off we go!",
        x: 280, box: [234, 192, 82, 166], act: "door" },
      { id: "toychest", icon: "🧸", name: "My toy chest", hint: "Play with my toys", say: "My toy chest! All my favourite toys.", open: "Playtime? Pick a toy, any toy!",
        x: 390, box: [350, 405, 82, 75], tab: "toys" },
      { id: "trophy", icon: "🏆", name: "My trophy shelf", hint: "Everything I've found and won", say: "My treasures! Everything I've found and won.", open: "Look at all my treasures!",
        x: 430, box: [370, 95, 75, 55], tab: "collection" },
      { id: "paint", icon: "🎨", name: "My paint pots", hint: "Decorate my room", say: "My paint pots! Let's make my room pretty.", open: "What shall we change?",
        x: 545, box: [515, 410, 64, 60], tab: "home" },
      { id: "bed", icon: "🛏", name: "My bed", hint: "Cuddles and naps", say: "My bed. Cuddles and naps happen here.", open: "A cuddle? Or a little nap?",
        x: 640, box: [575, 330, 125, 140], tab: "care" },
    ] },
    { id: "den", name: "THE DEN", things: [
      { id: "wardrobe", icon: "👗", name: "My wardrobe", hint: "My clothes", say: "My wardrobe! So many outfits.", open: "What shall I wear?",
        x: 370, box: [305, 205, 130, 270], tab: "closet" },
      { id: "terminal", icon: "🖥", name: "My terminal", hint: "My name, colour, sound and reminders", say: "My terminal. Beep boop, my settings!", open: "Beep boop! What shall we change?",
        x: 500, box: [440, 290, 120, 180], tab: "settings" },
      { id: "capsules", icon: "🎁", name: "My capsule machine", hint: "Capsules, furniture and clothes", say: "My capsule machine! What's inside this time?", open: "Turn the handle... what's inside?",
        x: 630, box: [580, 250, 100, 225], tab: "shop" },
    ] },
  ];
  const START = { room: "bedroom", at: 4 }; // A first visit: by the paint pots (layout index: after the way to the kitchen), about where she always stood.
  const EDGE = { left: 300, right: 700 };    // Where she stands on a "next room" row: at the edge she'll walk through.

  function room(id) { return ROOMS.find(function (r) { return r.id === id; }) || ROOMS[1]; } // An unknown room is her bedroom.
  function index(id) { return ROOMS.indexOf(room(id)); } // A room's place in the flat, left to right.

  function way(to, side) { // A spot at a room's edge that walks through to the next room, in Lumi's words.
    const short = to.name.replace(/^HER |^THE /, "").toLowerCase(), mine = to.name.indexOf("HER ") === 0; // "kitchen"; and whose it is
    return { go: to.id, side: side, x: EDGE[side], icon: side === "left" ? "◀" : "▶", name: "To " + (mine ? "my " : "the ") + short, hint: "Walk through",
             say: "This way to " + (mine ? "my " : "the ") + short + "!" };
  }

  function layout(id) { // A room's spots in walking order: a way into the room on the left, its things, a way on the right.
    const i = index(id), out = [];
    if (i > 0) out.push(way(ROOMS[i - 1], "left"));
    room(id).things.forEach(function (thing) { out.push(thing); });
    if (i < ROOMS.length - 1) out.push(way(ROOMS[i + 1], "right"));
    return out;
  }

  function clean(at) { // A remembered place (this device's G.store) made safe: a real room and a spot in it.
    const id = at && typeof at.room === "string" ? room(at.room).id : START.room, spots = layout(id);
    const n = at && Number.isInteger(at.at) ? at.at : START.at;
    return { room: id, at: Math.max(0, Math.min(spots.length - 1, n)) };
  }

  function enter(id, side) { // Arriving in a room from one side: standing at the first thing on that side (not on the way back out).
    const spots = layout(id), things = spots.map(function (s, i) { return s.go ? -1 : i; }).filter(function (i) { return i >= 0; });
    return { room: id, at: side === "right" ? things[things.length - 1] : things[0] };
  }

  function step(at, by) { // ◀ (-1) or ▶ (+1): the next spot that way. A "next room" spot walks straight through; the flat's ends stop her.
    const here = clean(at), spots = layout(here.room), to = here.at + by;
    if (to < 0 || to >= spots.length) return here;               // the end of the flat
    const spot = spots[to];
    return spot.go ? enter(spot.go, by > 0 ? "left" : "right") : { room: here.room, at: to };
  }

  function hop(at, by) { // ⏮ ⏭: the whole next room that way (or stay, at the flat's ends).
    const here = clean(at), i = index(here.room) + by;
    if (i < 0 || i >= ROOMS.length) return here;
    return enter(ROOMS[i].id, by > 0 ? "left" : "right");
  }

  function spot(at) { const here = clean(at); return layout(here.room)[here.at]; } // What she's standing at.

  function rows(at, ctx) { // The room's own list (no card open): one row per spot, so a remote, a keyboard and a screen reader all reach everything.
    // ctx.open(thing, index): open its card (game.js); ctx.walk(state): go to another room; ctx.door(): out of the front door.
    const here = clean(at);
    return layout(here.room).map(function (s, i) {
      if (s.go) return { key: "go-" + s.go, icon: s.icon, name: s.name, hint: s.hint, say: s.say, local: function () { ctx.walk(enter(s.go, s.side === "left" ? "right" : "left")); } };
      return { key: "thing-" + s.id, icon: s.icon, name: s.name, hint: s.hint, say: s.say, local: function () { if (s.act === "door") ctx.door(); else ctx.open(s, i); } };
    });
  }

  function cardRows(list, thing, back) { // A card: the tab's rows (only `keys`, when the thing names some), then the way back to the room.
    const shown = thing.keys ? list.filter(function (row) { return thing.keys.indexOf(row.key) >= 0; }) : list.slice();
    if (!shown.length) shown.push({ key: "card-empty", icon: thing.icon, name: thing.name, hint: thing.empty || "Nothing here just now", note: thing.empty || "Nothing here just now." });
    shown.push({ key: "card-back", icon: "↩", name: "Back to the room", hint: "Escape, Back or B does it too", local: back });
    return shown;
  }

  function guide(at, thing) { // Lumi's words for a spot (her bubble in the lower bar), or for an open menu (`thing`): always first person.
    const s = thing || spot(at);
    return !s ? "" : thing ? s.open || s.say : s.say;
  }

  function hits(id, at) { // Invisible press boxes over a room's things (game.js listens for data-click="thing:id" and "go:room"),
    // and the dashed ring at Lumi's feet where she's standing (the town's own mark, game.css .lp-tw-mark). All authored here.
    const here = clean({ room: id, at: at }), spots = layout(id), mine = spots[here.at];
    let out = "";
    room(id).things.forEach(function (t) {
      out += '<rect x="' + t.box[0] + '" y="' + t.box[1] + '" width="' + t.box[2] + '" height="' + t.box[3] + '" rx="10" fill="transparent" class="lp-rm-hit" data-click="thing:' + t.id + '"/>';
    });
    if (mine) out += '<ellipse cx="' + mine.x + '" cy="478" rx="44" ry="9" fill="none" class="lp-tw-mark" stroke="var(--pet-glow)" stroke-width="2.5" stroke-dasharray="6 4"/>';
    return out;
  }

  function find(id, thingId) { // A thing's place in its room's layout (a press on the drawing), or -1.
    return layout(id).findIndex(function (s) { return s.id === thingId; });
  }

  function nearest(id, x) { // The spot nearest a press on the floor: she walks to the thing closest to it.
    let best = 0, gap = Infinity;
    layout(id).forEach(function (s, i) { const d = Math.abs(s.x - x); if (!s.go && d < gap) { gap = d; best = i; } });
    return best;
  }

  function tabs() { // Every tab some thing opens (tests: each of Lumi Pet's tabs must be reachable from a room).
    const out = [];
    ROOMS.forEach(function (r) { r.things.forEach(function (t) { if (t.tab && out.indexOf(t.tab) < 0) out.push(t.tab); }); });
    return out;
  }

  function neighbours(at) { // The rooms either side of where she is ({left, right}: a room or null), for the edge arrows.
    const i = index(clean(at).room);
    return { left: i > 0 ? ROOMS[i - 1] : null, right: i < ROOMS.length - 1 ? ROOMS[i + 1] : null };
  }

  return { ROOMS: ROOMS, START: START, room: room, layout: layout, clean: clean, enter: enter, step: step, hop: hop, spot: spot,
           rows: rows, cardRows: cardRows, guide: guide, hits: hits, find: find, nearest: nearest, tabs: tabs, neighbours: neighbours };
});
