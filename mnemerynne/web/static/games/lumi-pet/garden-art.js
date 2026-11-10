/* The art for Lumi's Stardust Garden: a night meadow, the powder press, a grid of plots with a flower in each at
   whatever stage it has reached, the things bought for looks, and the two minigames' panels. Plain SVG in the same
   1000 x 500 room as her house and the dungeon, kept to the middle so the side panels never cover it.

   The server's catalog (mnemerynne/lumipet_data/garden.json) names the flowers and gives each its colours and petal
   count; this file only knows how to draw them. Colours come from the server, which only accepts "#rrggbb";
   everything else is authored here. Shapes with data-click are where a mouse or finger can press (game.js listens). */
(function (root, make) {
  const art = make();
  if (typeof module === "object" && module.exports) module.exports = art; else root.LumiPetGardenArt = art;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const INK = "#0d0a1a", COLUMNS = 4, TILE_W = 70, TILE_H = 58, GAP = 6, LEFT = 351, TOP = 172;
  const CANS = [{ x: 500, y: 196, colour: "#70ebea", arrow: "▲" }, { x: 590, y: 272, colour: "#ffdc83", arrow: "▶" }, { x: 500, y: 348, colour: "#ff6b8b", arrow: "▼" }, { x: 410, y: 272, colour: "#8be9a8", arrow: "◀" }];
  const COSMETICS = ["picket-fence", "stone-path", "glow-borders", "garden-gnome", "scarecrow-lumi", "golden-press", "firefly-lamps"]; // Each is drawn below.

  function rect(x, y, w, h, fill, more) { return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function circle(x, y, r, fill, more) { return '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function oval(x, y, rx, ry, fill, more) { return '<ellipse cx="' + x + '" cy="' + y + '" rx="' + rx + '" ry="' + ry + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function path(d, fill, more) { return '<path d="' + d + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function text(x, y, words, size, fill, more) { return '<text x="' + x + '" y="' + y + '" font-size="' + size + '" fill="' + fill + '" text-anchor="middle" ' + (more || "") + ">" + words + "</text>"; }
  function safe(colour) { return /^#[0-9a-f]{6}$/.test(colour) ? colour : "#f0deff"; }
  function press(x, y, w, h, name) { return rect(x, y, w, h, "#000", 'fill-opacity="0" pointer-events="all" data-click="' + name + '"'); }
  function tile(index) { return { x: LEFT + (index % COLUMNS) * (TILE_W + GAP), y: TOP + Math.floor(index / COLUMNS) * (TILE_H + GAP) }; }

  function backdrop() { // A meadow at night: sky, a few fixed stars, soft hills, grass.
    let stars = "";
    for (let i = 0; i < 40; i++) stars += circle((i * 197) % 1000, 12 + (i * 61) % 130, i % 5 ? 1 : 1.8, "#f6f1ff", 'opacity="' + (0.35 + (i % 4) * 0.15) + '"' + (i % 6 ? "" : ' class="lp-twinkle"'));
    return rect(-100, 0, 1200, 500, "#15102e") + stars + oval(250, 190, 420, 70, "#1b2a3a") + oval(780, 200, 460, 80, "#182536") + rect(-100, 170, 1200, 330, "#1c3030") +
      oval(500, 470, 520, 60, "#17282a");
  }

  function bloom(x, y, flower, size, droop) { // The flower's head: petals round a heart.
    const colour = safe(flower.colour), heart = safe(flower.heart), n = flower.petals;
    let out = "";
    for (let i = 0; i < n; i++) out += oval(x, y - size * 0.62, size * (n > 7 ? 0.22 : 0.3), size * 0.5, colour, 'transform="rotate(' + (360 / n * i) + " " + x + " " + y + ')" opacity=".95"');
    return '<g' + (droop ? ' transform="rotate(28 ' + x + " " + (y + size) + ')" opacity=".7"' : "") + ">" + out + circle(x, y, size * 0.3, heart) + "</g>";
  }

  function plant(x, y, flower, stage, progress) { // One flower standing on its plot, by stage. (x, y) is where the stem meets the soil.
    const stem = "#5fae86";
    if (stage === "growing" && progress < 0.34) return path("M" + x + " " + y + " v-9", "none", 'stroke="' + stem + '" stroke-width="3" stroke-linecap="round"') +
      oval(x - 5, y - 9, 5, 3, stem, 'transform="rotate(-30 ' + (x - 5) + " " + (y - 9) + ')"') + oval(x + 5, y - 9, 5, 3, stem, 'transform="rotate(30 ' + (x + 5) + " " + (y - 9) + ')"');
    const tall = stage === "growing" ? 14 + 16 * progress : 30;
    let out = path("M" + x + " " + y + " v-" + tall, "none", 'stroke="' + stem + '" stroke-width="3" stroke-linecap="round"') + oval(x + 6, y - tall * 0.45, 6, 3, stem, 'transform="rotate(-25 ' + (x + 6) + " " + (y - tall * 0.45) + ')"');
    if (stage === "growing") return out + oval(x, y - tall - 4, 4, 6, safe(flower.colour), 'opacity=".9"'); // A bud, still shut.
    if (stage === "overgrown") out += oval(x - 8, y - 12, 8, 3.5, "#6f8a5a", 'transform="rotate(35 ' + (x - 8) + " " + (y - 12) + ')"') + oval(x + 10, y - 6, 8, 3.5, "#6f8a5a", 'transform="rotate(-40 ' + (x + 10) + " " + (y - 6) + ')"');
    out += bloom(x, y - tall, flower, 11, stage === "overgrown");
    if (stage === "ready") out += text(x + 22, y - tall - 6, "✦", 12, "#ffdc83", 'class="lp-twinkle"');
    return out;
  }

  function plots(garden, cursor, focused, passed, owned) { // The grid: every plot, and a patch of grass where the next could be dug.
    const flowers = {}, glow = owned["glow-borders"];
    garden.flowers.forEach(function (flower) { flowers[flower.id] = flower; });
    let out = "";
    garden.plots.forEach(function (plot, index) {
      const at = tile(index), left = plot.ready_in - passed, stage = plot.stage === "growing" && left <= 0 ? "ready" : plot.stage;
      out += rect(at.x, at.y, TILE_W, TILE_H, "#3a2a3f", 'rx="8" stroke="' + (glow ? "#70ebea" : "#241a2c") + '" stroke-width="2"' + (glow ? ' class="lp-neon"' : "") + ' data-plot="' + stage + '"') +
        rect(at.x + 6, at.y + TILE_H - 16, TILE_W - 12, 10, "#2a1d2f", 'rx="5"');
      if (plot.flower && flowers[plot.flower]) out += plant(at.x + TILE_W / 2, at.y + TILE_H - 10, flowers[plot.flower], stage, plot.grow ? Math.max(0, Math.min(1, 1 - left / plot.grow)) : 1);
    });
    if (garden.plot_price !== null) {
      const at = tile(garden.plots.length);
      out += rect(at.x, at.y, TILE_W, TILE_H, "none", 'rx="8" stroke="#8a86a8" stroke-width="2" stroke-dasharray="6 5"') + text(at.x + TILE_W / 2, at.y + 30, "+", 26, "#8a86a8") +
        text(at.x + TILE_W / 2, at.y + 48, garden.plot_price + " coins", 9, "#8a86a8");
    }
    if (focused) { const at = tile(cursor); out += rect(at.x - 3, at.y - 3, TILE_W + 6, TILE_H + 6, "none", 'rx="10" stroke="#70ebea" stroke-width="3" data-cursor="' + cursor + '"'); }
    return out;
  }

  const PRESS_X = 104; // The press stands to the right, clear of the bar of words at the top left (game.css .lp-dhud).
  function machine(garden, owned, pending) { // The powder press: a hopper of starpowder over a plate, with how near the next coin is.
    const gold = owned["golden-press"], body = gold ? "#d9a441" : "#6a648a", near = Math.max(0, Math.min(1, garden.squeezed / garden.price));
    return '<g transform="translate(' + PRESS_X + ' 0)">' + rect(452, 84, 96, 12, body, 'rx="4"') + path("M462 84 L474 52 H526 L538 84 Z", gold ? "#f3cf7a" : "#8a86a8", 'stroke="' + INK + '" stroke-opacity=".4" stroke-width="2"') +
      (garden.powder - pending * garden.rates.tap > 0.5 ? path("M476 60 H524 L530 78 H470 Z", "#f0deff", 'opacity=".85" class="lp-twinkle"') : "") +
      rect(470, 96, 60, 30, INK, 'opacity=".75" rx="4"') + rect(474, 114, 52, 8, "#2a2548", 'rx="4"') + rect(474, 114, 52 * near, 8, "#ffdc83", 'rx="4" data-near="' + Math.round(near * 100) + '"') +
      circle(500, 105, 5, "#ffdc83", pending ? 'class="lp-twinkle"' : 'opacity=".5"') + rect(444, 126, 112, 8, body, 'rx="4"') + text(500, 148, "PRESS", 9, "#c9aecf", 'letter-spacing="3"') + "</g>";
  }

  function extras(owned, count) { // Things bought for looks. They never change a number.
    const rowsDown = Math.ceil(count / COLUMNS), bottom = TOP + rowsDown * (TILE_H + GAP);
    let out = "";
    if (owned["stone-path"]) for (let i = 0; i < 7; i++) out += oval(476 + (i % 2) * 22, 150 + i * 2 + (i > 1 ? 6 : 0), 9, 4, "#a8a3c4", 'opacity=".5"');
    if (owned["picket-fence"]) for (let x = LEFT - 8; x <= LEFT + COLUMNS * (TILE_W + GAP); x += 14) if (x < 470 || x > 530) out += path("M" + x + " 168 v-14 l4 -5 l4 5 v14 Z", "#f6f1ff", 'opacity=".85"');
    if (owned["garden-gnome"]) out += path("M336 " + bottom + " l8 -26 l8 26 Z", "#ff6b8b") + circle(344, bottom + 2, 7, "#f3d9c4") + path("M338 " + (bottom + 6) + " q6 12 12 0 Z", "#f6f1ff") + rect(337, bottom + 12, 14, 10, "#5f8fd6", 'rx="3"');
    if (owned["scarecrow-lumi"]) out += path("M664 " + (bottom + 20) + " v-44 M650 " + (bottom - 8) + " h28", "none", 'stroke="#c79256" stroke-width="4" stroke-linecap="round"') +
      circle(664, bottom - 30, 9, "#e8cf8a") + path("M656 " + (bottom - 36) + " l2 -8 l5 6 Z M672 " + (bottom - 36) + " l-2 -8 l-5 6 Z", "#e8cf8a") + circle(661, bottom - 31, 1.5, INK) + circle(667, bottom - 31, 1.5, INK);
    if (owned["firefly-lamps"]) [LEFT - 14, LEFT + COLUMNS * (TILE_W + GAP) + 6].forEach(function (x) {
      out += path("M" + x + " " + (bottom + 4) + " V" + (TOP - 8), "none", 'stroke="#6a648a" stroke-width="3"') + rect(x - 6, TOP - 22, 12, 14, "#fff8dc", 'rx="4" opacity=".25"') + circle(x - 2, TOP - 16, 1.6, "#ffdc83", 'class="lp-twinkle"') + circle(x + 2, TOP - 12, 1.6, "#ffdc83", 'class="lp-twinkle"');
    });
    return out;
  }

  function presses(garden) { // Where a mouse or finger can press: each plot, the patch for the next one, and the press.
    let out = press(440 + PRESS_X, 46, 120, 106, "press");
    for (let i = 0; i < garden.plots.length + (garden.plot_price === null ? 0 : 1); i++) { const at = tile(i); out += press(at.x, at.y, TILE_W, TILE_H, "plot:" + i); }
    return out;
  }

  function panel(inner, title) { return rect(340, 96, 320, 316, INK, 'opacity=".92" rx="14" stroke="#70ebea" stroke-opacity=".5" stroke-width="2"') + text(500, 124, title, 13, "#70ebea", 'font-weight="700" letter-spacing="2"') + inner; }

  function watering(mini) { // Four cans in a cross, one per arrow. `mini.lit`: the can lit just now (showing, or just pressed), or -1.
    let out = "";
    CANS.forEach(function (can, i) {
      const lit = mini.lit === i;
      out += circle(can.x, can.y, 38, can.colour, 'opacity="' + (lit ? 1 : 0.22) + '" data-can="' + (lit ? "lit" : "dim") + '"' + (lit ? ' class="lp-neon"' : "")) +
        path("M" + (can.x - 14) + " " + (can.y - 8) + " h22 v20 h-22 Z M" + (can.x + 8) + " " + (can.y - 2) + " l14 -8 l2 4 l-12 10 Z", lit ? INK : can.colour, 'opacity="' + (lit ? 0.8 : 0.9) + '"') +
        text(can.x, can.y + 30, can.arrow, 10, lit ? INK : "#c9aecf") + (mini.phase === "answer" ? rect(can.x - 40, can.y - 40, 80, 80, "#000", 'fill-opacity="0" pointer-events="all" data-click="can:' + i + '"') : "");
    });
    const dots = mini.show.map(function (_, i) { return circle(500 - (mini.show.length - 1) * 7 + i * 14, 392, 4, i < mini.answer.length ? "#8be9a8" : "#3a3550"); }).join("");
    return panel(out + dots, mini.phase === "show" ? "WATCH THE CANS" : "YOUR TURN · " + mini.answer.length + " OF " + mini.show.length);
  }

  function feeding(mini) { // One big button, a count, and the time running out.
    const share = Math.max(0, Math.min(1, mini.left / mini.whole));
    return panel(circle(500, 262, 74, "#8be9a8", 'opacity=".25"') + circle(500, 262, 58, "#8be9a8", 'class="lp-neon"') + text(500, 274, String(mini.count), 38, INK, 'font-weight="700"') +
      rect(380, 366, 240, 10, "#2a2548", 'rx="5"') + rect(380, 366, 240 * share, 10, "#ffdc83", 'rx="5"') + rect(340, 140, 320, 272, "#000", 'fill-opacity="0" pointer-events="all" data-click="feed"'), "PRESS! PRESS! PRESS!");
  }

  function scene(garden, look) { // Everything, back to front. look: { cursor, focused, passed, pending, mini }.
    const owned = {};
    garden.cosmetics.forEach(function (thing) { if (thing.owned) owned[thing.id] = true; });
    const base = backdrop() + extras(owned, garden.plots.length + 1) + machine(garden, owned, look.pending || 0) + plots(garden, look.cursor, look.focused && !look.mini, look.passed || 0, owned);
    if (look.mini && look.mini.kind === "water") return base + watering(look.mini);
    if (look.mini && look.mini.kind === "feed") return base + feeding(look.mini);
    return base + presses(garden);
  }

  return { scene: scene, COSMETICS: COSMETICS, CANS: CANS };
});
