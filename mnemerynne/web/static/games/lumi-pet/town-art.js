/* Lumi Town's drawings (Doll's design, 2026-11-12): the town map, and the insides of the three places you walk into
   (the Arcade, the Casino and the Lumi-Soft field), and Market Street (2026-11-14: the Retro café, the Market and its
   inside of four stalls), with a signpost at each street's end to the other. Each is one 1000 x 500 picture, drawn in the room behind the lists
   like her apartment (house.js) and the tavern (tavern-art.js).

   Every screen shows the middle of the picture (about x 290-710: game.css crops the sides to fit), so everything you
   can pick sits there: the map's six places in two rows of three, and the stations of a place's inside (stations(): four, or the Arcade's five). The rest
   (trees, lamps, a claw machine) is what a wide screen adds at the sides. Lumi is the page's own Lumi, moved to spot()
   by game.js. Plain SVG, all authored here: nothing a person typed goes into it, and the one label it is given (the
   picked place's name, from town-ui.js) is escaped anyway. game.css makes the lights breathe (.lp-tw-glow). */
(function (root, make) {
  const art = make();
  if (typeof module === "object" && module.exports) module.exports = art; else root.LumiPetTownArt = art;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Each place on the map: its middle and the line its front door stands on. Top row on the high street, bottom row on the lane.
  const MAP = { arcade: [355, 232], tavern: [500, 232], casino: [645, 232], home: [355, 436], garden: [500, 436], field: [645, 436] };
  // Market Street (2026-11-14): the Retro café and the Market on its top row; its lane has the fountain, and room for more.
  const MARKET = { cafe: [430, 232], market: [575, 232] };
  // The signpost to the other street, keyed by where it leads: on the High Street's right-hand end to Market Street, and
  // on Market Street's left-hand end back. Both inside the middle every screen shows.
  const SIGNS = { market: [688, 282], high: [312, 282] };   // standing on the cobbles at the street's end
  const ALL = Object.assign({}, MAP, MARKET);
  const STATIONS = [365, 455, 545, 635]; // Inside a place: where its cabinets or tables stand, close enough that a squarer screen's panels don't cover the end ones (four of them).
  // The same row for any count, centred on the room: four 90 apart is STATIONS exactly. Five (the Arcade since Lumi Launch,
  // 2026-11-14) stand 72 apart and a fifth smaller (SNUG), so all five sit in the middle a 1440x1000 screen leaves clear of
  // Lumi Pet's panels (about x 302-670 there); 90 apart, the fifth went under the right panel.
  const SNUG = 0.8;
  function stations(count) {
    const n = Math.max(1, count), gap = n > 4 ? 72 : 90;
    return Array.from({ length: n }, function (_, i) { return 500 + (i - (n - 1) / 2) * gap; });
  }
  const NEON = ["#ff8ecb", "#ffdc83", "#70ebea", "#c9a8ff", "#8be9a8", "#ff9d6b"]; // Lumi's own glow colours.

  function rect(x, y, w, h, fill, more) { return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function circle(x, y, r, fill, more) { return '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function oval(x, y, rx, ry, fill, more) { return '<ellipse cx="' + x + '" cy="' + y + '" rx="' + rx + '" ry="' + ry + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function path(d, fill, more) { return '<path d="' + d + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function line(x1, y1, x2, y2, stroke, width, more) { return '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="' + stroke + '" stroke-width="' + width + '" ' + (more || "") + "/>"; }
  function text(x, y, words, size, fill, more) { return '<text x="' + x + '" y="' + y + '" font-size="' + size + '" fill="' + fill + '" text-anchor="middle" ' + (more || "") + ">" + words + "</text>"; }
  function group(inner, more) { return "<g " + (more || "") + ">" + inner + "</g>"; }
  function glow(inner) { return group(inner, 'class="lp-tw-glow"'); } // A light that breathes (game.css): the group's opacity moves, the shape keeps its own.
  function esc(words) { return String(words).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); } // Safe as SVG text.
  function dice(seed) { // A steady seeded roll, so the stones and stars are the same on every draw (and every device).
    let value = seed;
    return function (low, high) { value = (value * 16807) % 2147483647; return Math.round((low + (value / 2147483647) * (high - low)) * 10) / 10; };
  }
  function hit(x, y, w, h, click) { return rect(x, y, w, h, "transparent", 'data-click="' + click + '"'); } // A press anywhere on it counts (game.js townPress).
  function marker(x, y) { return oval(x, y, 34, 8, "none", 'class="lp-tw-mark" stroke="#ff8ecb" stroke-width="2.5" stroke-dasharray="6 4"'); } // The dashed ring where she's about to go.

  // ---- The map ---------------------------------------------------------------------------------------------
  function sky(night, top) { // The sky down to `top`, with the sun or the moon and a few clouds or stars.
    const roll = dice(5);
    let bits = "";
    if (night) for (let i = 0; i < 40; i++) bits += circle(roll(-10, 1010), roll(6, top - 10), roll(.8, 1.8), "#ffffff", 'opacity="' + roll(.4, .9) + '"');
    else [[150, 52], [420, 34], [800, 60]].forEach(function (c) { bits += oval(c[0], c[1], 34, 11, "#ffffff", 'opacity=".85"') + oval(c[0] + 22, c[1] - 6, 22, 11, "#ffffff", 'opacity=".85"'); });
    const orb = night ? circle(880, 46, 18, "#fff6d8") + circle(888, 40, 15, "#1a1a44") : glow(circle(880, 46, 34, "#fff3b0", 'opacity=".25"')) + circle(880, 46, 18, "#fff3b0");
    return rect(-20, -20, 1040, top + 20, night ? "#1a1a44" : "#9ad4ff") + rect(-20, top - 40, 1040, 40, night ? "#2a2050" : "#cdeeff", 'opacity=".7"') + bits + orb;
  }

  function hills(night) { // Soft hills on the horizon, behind the high street.
    return path("M-20 128 Q120 70 260 112 T540 104 T820 96 T1040 110 V140 H-20 Z", night ? "#24324a" : "#9bd69a") +
      path("M-20 140 Q180 104 380 132 T760 126 T1040 132 V150 H-20 Z", night ? "#2c3d52" : "#86c985");
  }

  function ground(night) { // Grass, the high street's cobbles and the lane in front of the bottom row.
    const roll = dice(9), stone = night ? "#7a6e84" : "#d8c8b4", seam = night ? "#5a5064" : "#b8a690";
    let cobbles = "";
    for (let y = 246, row = 0; y < 294; y += 12, row++) for (let x = -20 + (row % 2) * 14; x < 1020; x += roll(24, 32)) cobbles += rect(x, y, 22, 9, roll(0, 1) > .5 ? stone : seam, 'rx="3" opacity=".75"');
    let tufts = "";
    for (let i = 0; i < 46; i++) { const x = roll(-10, 1010), y = roll(160, 490); if (y > 240 && y < 300) continue; tufts += path("M" + x + " " + y + " l3 -7 l3 7 l3 -5 l2 5", "none", 'stroke="' + (night ? "#2a5038" : "#5fae5c") + '" stroke-width="1.5"'); }
    return rect(-20, 140, 1040, 380, night ? "#2f5a44" : "#7ccf7a") + tufts +
      rect(-20, 240, 1040, 58, night ? "#5a5064" : "#c9b8a6") + cobbles + rect(-20, 240, 1040, 4, night ? "#3e3646" : "#a8967e") + rect(-20, 296, 1040, 4, night ? "#3e3646" : "#a8967e") +
      path("M-20 446 Q500 432 1020 446 V488 Q500 474 -20 488 Z", night ? "#4a4256" : "#e0cfae") + // The lane: packed earth.
      path("M486 298 Q480 372 492 440 H510 Q520 372 514 298 Z", night ? "#4a4256" : "#e0cfae"); // A path up from the lane to the high street.
  }

  function tree(x, y, size, night) { // A round little tree.
    const leaf = night ? "#24503a" : "#4fae5a", light = night ? "#2f6046" : "#6cc46a";
    return rect(x - 4, y - size * .9, 8, size * .9, "#6a4a32") + circle(x, y - size * 1.2, size * .62, leaf) + circle(x - size * .3, y - size, size * .42, leaf) + circle(x + size * .32, y - size * 1.02, size * .44, leaf) + circle(x - size * .12, y - size * 1.34, size * .3, light);
  }

  function lamp(x, y, night) { // A street lamp: lit and glowing at night.
    return rect(x - 2.5, y - 62, 5, 62, "#3a3048") + rect(x - 8, y - 4, 16, 4, "#3a3048") + path("M" + (x - 9) + " " + (y - 62) + " h18 l-3 -14 h-12 Z", "#3a3048") +
      rect(x - 6, y - 74, 12, 11, night ? "#ffe6a8" : "#f3eedd") + (night ? glow(circle(x, y - 68, 26, "#ffcf7a", 'opacity=".22"')) : "");
  }

  function label(x, y, words) { // The picked place's name in a little bubble over it.
    const w = Math.max(60, String(words).length * 8.4 + 22);
    return group(rect(x - w / 2, y - 18, w, 24, "#1a0f2e", 'rx="12" opacity=".92" stroke="#ff8ecb" stroke-width="1.5"') + path("M" + (x - 6) + " " + (y + 6) + " l6 7 l6 -7 Z", "#1a0f2e") +
      text(x, y - 1, esc(words), 13, "#ffe9f5", 'font-family="serif" letter-spacing="1"'), 'class="lp-tw-label"');
  }

  function arcade(cx, base, night) { // A purple arcade with a neon sign, two game screens in its windows and a glass door.
    const screen = function (x, colours) { // A window showing a little game.
      return rect(x, base - 72, 32, 28, "#140a2a", 'rx="3"') + colours.map(function (c, i) { return rect(x + 4 + i * 8, base - 66 + (i % 2) * 6, 6, 6, c); }).join("") + rect(x + 10, base - 52, 12, 3, "#ffffff", 'opacity=".7"');
    };
    return rect(cx - 60, base - 112, 120, 112, "#3a2466") + rect(cx - 60, base - 112, 120, 6, "#2a1a4e") +
      rect(cx - 64, base - 120, 128, 10, "#ff2e97", 'rx="3"') + glow(rect(cx - 64, base - 120, 128, 10, "#ff8ecb", 'rx="3" opacity=".5"')) +
      rect(cx - 50, base - 104, 100, 24, "#14082a", 'rx="4" stroke="#70ebea" stroke-width="1.5"') + glow(text(cx, base - 87, "ARCADE", 15, "#70ebea", 'font-family="monospace" font-weight="bold" letter-spacing="3"')) +
      screen(cx - 52, ["#ff8ecb", "#ffdc83", "#70ebea"]) + screen(cx + 20, ["#c9a8ff", "#8be9a8", "#ff9d6b"]) +
      rect(cx - 15, base - 48, 30, 48, night ? "#ffb3dc" : "#ff8ecb", 'rx="2" opacity=".9"') + rect(cx - 15, base - 48, 30, 48, "none", 'stroke="#ffe9f5" stroke-width="2" rx="2"') + line(cx, base - 48, cx, base, "#ffe9f5", 1.5) +
      glow(circle(cx - 46, base - 30, 3, "#ffdc83")) + glow(circle(cx + 46, base - 22, 3, "#70ebea")) + text(cx + 44, base - 10, "★", 11, "#ffdc83");
  }

  function tavern(cx, base, night) { // The Lantern & Paw: a timber-framed inn, a smoking chimney and a hanging paw sign.
    const lit = night ? "#ffcf7a" : "#a8d8f0";
    return rect(cx + 26, base - 150, 14, 34, "#6a4a3a") + glow(oval(cx + 36, base - 160, 9, 6, "#e8e0f0", 'opacity=".5"')) + oval(cx + 44, base - 172, 7, 5, "#e8e0f0", 'opacity=".35"') + // The chimney and its smoke.
      path("M" + (cx - 70) + " " + (base - 92) + " L" + cx + " " + (base - 148) + " L" + (cx + 70) + " " + (base - 92) + " Z", "#7a3a2a") + path("M" + (cx - 70) + " " + (base - 92) + " L" + cx + " " + (base - 148) + " L" + (cx + 70) + " " + (base - 92), "none", 'stroke="#5a2a1e" stroke-width="5"') +
      rect(cx - 58, base - 94, 116, 94, "#f3e3c8") + [-58, -20, 18, 54].map(function (dx) { return rect(cx + dx, base - 94, 4, 94, "#6a4028"); }).join("") + rect(cx - 58, base - 50, 116, 4, "#6a4028") +
      [cx - 44, cx + 28].map(function (x) { return rect(x, base - 84, 18, 22, lit) + line(x + 9, base - 84, x + 9, base - 62, "#6a4028", 2) + (night ? glow(rect(x - 2, base - 86, 22, 26, "#ffcf7a", 'opacity=".25"')) : ""); }).join("") +
      path("M" + (cx - 13) + " " + base + " V" + (base - 32) + " Q" + cx + " " + (base - 46) + " " + (cx + 13) + " " + (base - 32) + " V" + base + " Z", "#5a3420") + circle(cx + 7, base - 16, 2, "#d8b24a") +
      line(cx - 50, base - 40, cx - 50, base - 30, "#8a7a5a", 1.5) + line(cx - 30, base - 40, cx - 30, base - 30, "#8a7a5a", 1.5) + rect(cx - 56, base - 30, 32, 18, "#8a5434", 'rx="3"') +
      circle(cx - 40, base - 19, 4, "#ff8ecb") + circle(cx - 45, base - 25, 1.8, "#ff8ecb") + circle(cx - 40, base - 27, 1.8, "#ff8ecb") + circle(cx - 35, base - 25, 1.8, "#ff8ecb"); // The sign: Lumi's paw.
  }

  function casino(cx, base, night) { // Lumi's Lounge: a red and gold front, a marquee of bulbs, card suits in the windows and a red carpet.
    let bulbs = "";
    for (let x = cx - 58; x <= cx + 58; x += 9.6) bulbs += circle(x, base - 128, 2.4, "#ffdc83") + circle(x, base - 100, 2.4, "#ffdc83");
    return rect(cx - 60, base - 104, 120, 104, "#5a1430") + rect(cx - 60, base - 104, 120, 104, "none", 'stroke="#d8b24a" stroke-width="3"') +
      [cx - 52, cx + 44].map(function (x) { return rect(x, base - 96, 8, 96, "#d8b24a", 'opacity=".8"'); }).join("") +
      rect(cx - 62, base - 132, 124, 34, "#2a0818", 'rx="4"') + glow(bulbs) + text(cx, base - 109, "LUMI’S LOUNGE", 11.5, "#ffdc83", 'font-family="serif" font-weight="bold" letter-spacing="1.5"') +
      [["♠", cx - 30, "#ffe9f5"], ["♥", cx + 30, "#ff8ecb"]].map(function (s) { return rect(s[1] - 13, base - 84, 26, 28, night ? "#ffcf7a" : "#3a0a20", 'rx="3" stroke="#d8b24a" stroke-width="1.5"') + text(s[1], base - 63, s[0], 18, night ? "#5a1430" : s[2]); }).join("") +
      rect(cx - 16, base - 44, 32, 44, "#d8b24a") + rect(cx - 13, base - 41, 12, 41, "#7a1a3a") + rect(cx + 1, base - 41, 12, 41, "#7a1a3a") +
      path("M" + (cx - 14) + " " + base + " L" + (cx + 14) + " " + base + " L" + (cx + 18) + " " + (base + 10) + " L" + (cx - 18) + " " + (base + 10) + " Z", "#c8304a"); // The red carpet, out to the street.
  }

  function house(cx, base, night) { // Lumi's own house: a pink cottage with a round star window, a heart on the door and a little fence.
    const lit = night ? "#ffcf7a" : "#bfe6ff";
    return rect(cx + 20, base - 124, 12, 26, "#8a5a7a") +
      path("M" + (cx - 60) + " " + (base - 76) + " L" + cx + " " + (base - 128) + " L" + (cx + 60) + " " + (base - 76) + " Z", "#8a5ac0") + path("M" + (cx - 60) + " " + (base - 76) + " L" + cx + " " + (base - 128) + " L" + (cx + 60) + " " + (base - 76), "none", 'stroke="#6a3ea0" stroke-width="5" stroke-linecap="round"') +
      circle(cx, base - 96, 10, lit, 'stroke="#ffe9f5" stroke-width="2.5"') + text(cx, base - 92, "★", 11, night ? "#ff8ecb" : "#ffdc83") +
      rect(cx - 50, base - 78, 100, 78, "#ffd0e6") + rect(cx - 50, base - 78, 100, 6, "#f0b0d0") +
      rect(cx - 40, base - 62, 22, 20, lit, 'stroke="#ffe9f5" stroke-width="3"') + rect(cx + 18, base - 62, 22, 20, lit, 'stroke="#ffe9f5" stroke-width="3"') +
      (night ? glow(rect(cx - 42, base - 64, 84, 24, "#ffcf7a", 'opacity=".15"')) : "") +
      rect(cx - 12, base - 40, 24, 40, "#c06a9a", 'rx="10"') + path("M" + cx + " " + (base - 20) + " l-5 -5 a3 3 0 0 1 5 -4 a3 3 0 0 1 5 4 Z", "#ffe9f5") +
      [-56, -48, 48, 56].map(function (dx) { return rect(cx + dx - 2, base - 14, 4, 14, "#ffe9f5"); }).join("") + line(cx - 58, base - 9, cx - 46, base - 9, "#ffe9f5", 2.5) + line(cx + 46, base - 9, cx + 58, base - 9, "#ffe9f5", 2.5); // (Her house's name shows in the picked label: "Lumi's house".)
  }

  function garden(cx, base, night) { // The Stardust Garden's gate: hedges, a flowered arch and beds either side.
    const roll = dice(41);
    let blooms = "";
    for (let i = 0; i < 18; i++) { const a = Math.PI * (i / 17), x = cx + Math.cos(a) * 30, y = base - 62 - Math.sin(a) * 34; blooms += circle(x + roll(-3, 3), y + roll(-3, 3), roll(3, 5), NEON[i % NEON.length]); }
    let beds = "";
    for (let i = 0; i < 10; i++) { const side = i < 5 ? -1 : 1, x = cx + side * (40 + (i % 5) * 4), y = base - 10 - (i % 5) * 6; beds += line(x, y + 8, x, y, "#3f8a4a", 1.5) + circle(x, y, 3.4, NEON[(i + 2) % NEON.length]); }
    return rect(cx - 62, base - 40, 46, 40, night ? "#28543a" : "#3f9a63", 'rx="14"') + rect(cx + 16, base - 40, 46, 40, night ? "#28543a" : "#3f9a63", 'rx="14"') +
      path("M" + (cx - 30) + " " + base + " V" + (base - 62) + " A30 34 0 0 1 " + (cx + 30) + " " + (base - 62) + " V" + base, "none", 'stroke="#f3e3c8" stroke-width="6"') + blooms +
      rect(cx - 20, base - 34, 18, 34, "none", 'stroke="#f3e3c8" stroke-width="2"') + rect(cx + 2, base - 34, 18, 34, "none", 'stroke="#f3e3c8" stroke-width="2"') + beds +
      glow(text(cx - 52, base - 54, "✦", 10, "#ffdc83") + text(cx + 54, base - 60, "✧", 12, "#c9a8ff"));
  }

  function field(cx, base, night) { // The Lumi-Soft field: a fenced meadow, cardboard creatures on stakes and the banner over the gate.
    const critter = function (x, y, s, c) { // A cardboard creature: round head, two ears, a stake.
      return line(x, y, x, y + s * 1.3, "#8a6a4a", 2) + circle(x, y - s * .2, s, c, 'stroke="#8a6a4a" stroke-width="1.2"') + path("M" + (x - s * .8) + " " + (y - s * .7) + " l" + s * .2 + " " + (-s * .7) + " l" + s * .4 + " " + s * .5 + " Z M" + (x + s * .8) + " " + (y - s * .7) + " l" + (-s * .2) + " " + (-s * .7) + " l" + (-s * .4) + " " + s * .5 + " Z", c) +
        circle(x - s * .3, y - s * .3, s * .12, "#3a2a2a") + circle(x + s * .3, y - s * .3, s * .12, "#3a2a2a") + circle(x, y - s * .2, s * .55, "none", 'stroke="#e8556a" stroke-width="1" opacity=".7"');
    };
    let fence = "";
    for (let x = cx - 60; x <= cx + 60; x += 15) fence += rect(x - 2, base - 22, 4, 22, "#c8a070");
    return rect(cx - 62, base - 92, 124, 92, night ? "#3a6a46" : "#a6e08a", 'rx="6"') +
      critter(cx - 34, base - 66, 9, "#d8b890") + critter(cx + 8, base - 74, 7, "#e8c8a0") + critter(cx + 40, base - 58, 10, "#d8b890") + critter(cx - 4, base - 46, 8, "#e0c098") +
      fence + rect(cx - 62, base - 18, 124, 4, "#c8a070") + rect(cx - 62, base - 10, 124, 4, "#c8a070") +
      line(cx - 44, base - 22, cx - 44, base - 104, "#8a6a4a", 3) + line(cx + 44, base - 22, cx + 44, base - 104, "#8a6a4a", 3) +
      path("M" + (cx - 46) + " " + (base - 106) + " H" + (cx + 46) + " V" + (base - 88) + " L" + cx + " " + (base - 82) + " L" + (cx - 46) + " " + (base - 88) + " Z", "#ff8ecb") +
      text(cx, base - 92, "LUMI-SOFT", 10.5, "#ffffff", 'font-family="monospace" font-weight="bold" letter-spacing="2"') + glow(text(cx + 56, base - 98, "🎯", 12, "#ffffff"));
  }

  function cafe(cx, base, night) { // The Retro café: a teal shopfront with a giant cartridge on the roof, a pixel picture in the window, and a cocoa sign.
    const lit = night ? "#ffcf7a" : "#bfe6ff";
    let pixels = "";
    [[0, 0, 0], [1, 0, 1], [2, 0, 0], [0, 1, 1], [1, 1, 1], [2, 1, 1], [0, 2, 1], [2, 2, 1]].forEach(function (p, i) { if (p[2]) pixels += rect(cx - 46 + p[0] * 7, base - 66 + p[1] * 7, 6, 6, NEON[i % NEON.length]); });
    return rect(cx - 30, base - 150, 60, 34, "#c9a8ff", 'rx="4" stroke="#7a55c0" stroke-width="2.5"') + rect(cx - 22, base - 146, 44, 18, "#efe4ff", 'rx="2"') + // The cartridge on the roof, with its label.
      text(cx, base - 133, "RETRO", 9, "#7a55c0", 'font-family="monospace" font-weight="bold" letter-spacing="2"') + [-14, -6, 2, 10].map(function (dx) { return rect(cx + dx, base - 118, 4, 4, "#d8b24a"); }).join("") +
      rect(cx - 60, base - 114, 120, 114, "#2f8a8a") + rect(cx - 60, base - 114, 120, 6, "#246c6c") +
      rect(cx - 64, base - 108, 128, 20, "#1a3a4a", 'rx="4" stroke="#70ebea" stroke-width="1.5"') + glow(text(cx, base - 93, "RETRO CAFÉ", 12, "#70ebea", 'font-family="monospace" font-weight="bold" letter-spacing="2"')) +
      rect(cx - 52, base - 74, 36, 30, "#0b1626", 'rx="3" stroke="#ffe9f5" stroke-width="2"') + pixels + // A little pixel picture in the window.
      rect(cx + 16, base - 74, 36, 30, lit, 'rx="3" stroke="#ffe9f5" stroke-width="2"') + path("M" + (cx + 26) + " " + (base - 62) + " h14 v8 a7 7 0 0 1 -14 0 Z", "#8a5a3a") + path("M" + (cx + 40) + " " + (base - 60) + " a4 4 0 0 1 0 6", "none", 'stroke="#8a5a3a" stroke-width="2"') + // A cocoa in the other.
      (night ? glow(rect(cx + 14, base - 76, 40, 34, "#ffcf7a", 'opacity=".2"')) : "") +
      rect(cx - 13, base - 44, 26, 44, "#1a3a4a", 'rx="2"') + rect(cx - 9, base - 40, 18, 18, lit, 'opacity=".8"') + circle(cx + 8, base - 20, 2, "#ffdc83") +
      glow(text(cx - 44, base - 14, "🎮", 11, "#ffffff")) + text(cx + 46, base - 12, "☕", 11, "#ffffff");
  }

  function market(cx, base, night) { // The Market: a long shopfront under a pink and cream striped awning, crates of goods out front and bunting.
    let stripes = "", crates = "", flags = "";
    for (let i = 0; i < 8; i++) stripes += path("M" + (cx - 66 + i * 16.5) + " " + (base - 92) + " h16.5 l2 18 a9 9 0 0 1 -18 0 Z", i % 2 ? "#fff3e6" : "#ff8ecb");
    [[-50, "#ff9d6b"], [-26, "#8be9a8"], [26, "#ffdc83"], [50, "#c9a8ff"]].forEach(function (c) {
      crates += rect(cx + c[0] - 11, base - 18, 22, 18, "#c8a070", 'rx="2"') + [-6, 0, 6].map(function (dx) { return circle(cx + c[0] + dx, base - 20, 4, c[1]); }).join("");
    });
    for (let i = 0; i < 7; i++) flags += path("M" + (cx - 54 + i * 18) + " " + (base - 126) + " l9 0 l-4.5 9 Z", NEON[i % NEON.length]);
    return rect(cx - 62, base - 118, 124, 118, night ? "#6a4a3a" : "#e8c8a0") + rect(cx - 62, base - 118, 124, 6, "#c8a070") +
      path("M" + (cx - 66) + " " + (base - 126) + " Q" + cx + " " + (base - 118) + " " + (cx + 66) + " " + (base - 126), "none", 'stroke="#8a6a4a" stroke-width="1.5"') + flags +
      rect(cx - 50, base - 112, 100, 18, "#5a3420", 'rx="4"') + text(cx, base - 99, "MARKET", 12, "#ffdc83", 'font-family="serif" font-weight="bold" letter-spacing="3"') + stripes +
      rect(cx - 52, base - 70, 104, 40, night ? "#ffcf7a" : "#fff3e6", 'opacity=".9"') + rect(cx - 14, base - 66, 28, 66, "#8a5a3a", 'rx="2"') + circle(cx + 9, base - 32, 2, "#ffdc83") +
      text(cx - 34, base - 44, "🍪", 12, "#ffffff") + text(cx + 34, base - 44, "🔮", 12, "#ffffff") + crates + (night ? glow(rect(cx - 52, base - 70, 104, 40, "#ffcf7a", 'opacity=".2"')) : "");
  }

  const BUILD = { arcade: arcade, tavern: tavern, casino: casino, home: house, garden: garden, field: field, cafe: cafe, market: market };

  function signpost(to, words, on) { // The way to the other street: a post with an arrow-shaped board, pressable.
    const at = SIGNS[to], x = at[0], y = at[1], right = x > 500, d = right ? 1 : -1;   // d: which way the arrow points
    return group((on ? marker(x - 26 * d, y + 14) : "") + rect(x - 3, y - 6, 6, 18, "#8a6a4a") +
      path("M" + (x - 32 * d) + " " + (y - 26) + " H" + (x + 32 * d) + " L" + (x + 42 * d) + " " + (y - 15) + " L" + (x + 32 * d) + " " + (y - 4) + " H" + (x - 32 * d) + " Z", "#c8a070", 'stroke="#8a6a4a" stroke-width="1.5"') +
      text(x + (right ? -4 : 4), y - 11, words, 8.5, "#5a3420", 'font-family="serif" font-weight="bold" letter-spacing="1"') + hit(x - 40, y - 34, 80, 50, "street:" + to), on ? 'class="lp-tw-on"' : "");
  }

  function townMap(opts) { // The whole map: sky, hills, ground, trees and lamps at the sides, the six places, and who's picked.
    const night = opts.night, picked = MAP[opts.cursor] ? opts.cursor : null;
    let places = "";
    Object.keys(MAP).forEach(function (id) { // Each place, pressable anywhere on it.
      const at = MAP[id], on = id === picked;
      places += group(BUILD[id](at[0], at[1], night) + hit(at[0] - 66, at[1] - 150, 132, 158, "place:" + id), 'data-place="' + id + '"' + (on ? ' class="lp-tw-on"' : ""));
    });
    const sides = tree(70, 232, 40, night) + tree(178, 226, 30, night) + tree(250, 236, 22, night) + tree(780, 230, 26, night) + tree(880, 234, 38, night) + tree(960, 222, 26, night) +
      lamp(282, 244, night) + lamp(718, 244, night) + tree(110, 440, 36, night) + tree(226, 446, 26, night) + tree(790, 440, 28, night) + tree(900, 444, 40, night) +
      lamp(282, 448, night) + lamp(718, 448, night);
    const sign = rect(497, 300, 6, 36, "#8a6a4a") + rect(468, 304, 64, 16, "#c8a070", 'rx="3"') + text(500, 316, "LUMI TOWN", 9, "#5a3420", 'font-family="serif" font-weight="bold" letter-spacing="1"');
    const mark = picked ? marker(MAP[picked][0], MAP[picked][1] + (MAP[picked][1] > 300 ? 38 : 36)) : "";
    const toMarket = opts.cursor === "street:market";
    const tag = picked && opts.label ? label(MAP[picked][0], MAP[picked][1] - (picked === "tavern" ? 160 : 140), opts.label)
      : toMarket && opts.label ? label(SIGNS.market[0] - 40, SIGNS.market[1] - 40, opts.label) : "";
    return group(sky(night, 140) + hills(night) + ground(night) + sides + sign + signpost("market", "MARKET ST", toMarket) + mark + places +
      (night ? rect(-20, -20, 1040, 540, "#0a0620", 'opacity=".12" pointer-events="none"') : "") + tag, 'data-town="map" data-time="' + (night ? "night" : "day") + '"');
  }

  // ---- Inside ----------------------------------------------------------------------------------------------
  function exitMat(on) { // The way back out: a mat by the front, with its own little sign.
    return group(rect(452, 470, 96, 22, "#3a2a4a", 'rx="5"') + text(500, 485, "EXIT ▸ TOWN", 10, "#ffe9f5", 'font-family="monospace" letter-spacing="1"') + hit(440, 462, 120, 36, "leave"), on ? 'class="lp-tw-on"' : "");
  }

  function cabinet(x, slug, on) { // One arcade cabinet: its colour, its marquee, and a little picture of its game on the screen.
    const colour = { breakout: "#ff8ecb", snake: "#ffdc83", "teacup-tower": "#70ebea", "prism-drop": "#c9a8ff", "lumi-launch": "#8be9a8" }[slug] || "#ff9d6b";
    const name = { breakout: "YARN POP", snake: "CONGA", "teacup-tower": "TEACUPS", "prism-drop": "PRISM", "lumi-launch": "LAUNCH" }[slug] || "GAME";
    let screen = "";
    if (slug === "breakout") { for (let r = 0; r < 3; r++) for (let c = 0; c < 6; c++) screen += rect(x - 28 + c * 9.5, 212 + r * 7, 8, 5, NEON[(r + c) % 6]); screen += circle(x + 4, 246, 3.5, "#ff8ecb") + rect(x - 10, 256, 22, 3, "#ffe9f5"); }
    else if (slug === "snake") { [[-20, 240], [-12, 240], [-4, 240], [4, 240], [4, 232], [4, 224], [12, 224]].forEach(function (p, i) { screen += circle(x + p[0], p[1], 3.6, NEON[i % 6]); }); screen += circle(x + 20, 248, 3, "#ffdc83"); }
    else if (slug === "teacup-tower") { [[-14, 252], [10, 236], [-8, 220]].forEach(function (p) { screen += path("M" + (x + p[0] - 9) + " " + p[1] + " h18 l-3 7 h-12 Z", "#ffe9f5") + rect(x + p[0] - 12, p[1] + 7, 24, 2, "#70ebea"); }); screen += circle(x + 10, 228, 3.5, "#ff8ecb"); }
    else if (slug === "lumi-launch") { screen += path("M" + (x - 26) + " 254 Q" + (x - 4) + " 196 " + (x + 22) + " 236", "none", 'stroke="#ffdc83" stroke-width="1.5" stroke-dasharray="3 3"') + // her arc out of the cannon
      path("M" + (x - 30) + " 256 l10 -12 l6 5 l-10 12 Z", "#c9a8ff") + circle(x - 24, 256, 4, "#ff8ecb") + glow(circle(x + 22, 236, 4.5, "#ff8ecb")) + rect(x - 30, 258, 60, 3, "#8be9a8", 'opacity=".7"'); }
    else { for (let r = 0; r < 3; r++) for (let c = 0; c < 5 - (r % 2); c++) screen += circle(x - 22 + c * 11 + (r % 2) * 5.5, 222 + r * 11, 2, "#c9a8ff"); screen += glow(circle(x + 2, 214, 4.5, "#ffdc83")) + rect(x - 28, 256, 56, 4, "#70ebea", 'opacity=".7"'); }
    return group((on ? marker(x, 452) : "") +
      path("M" + (x - 42) + " 390 V184 L" + (x - 36) + " 168 H" + (x + 36) + " L" + (x + 42) + " 184 V390 Z", colour, 'opacity=".9"') + path("M" + (x - 42) + " 390 V184 L" + (x - 36) + " 168 H" + (x + 36) + " L" + (x + 42) + " 184 V390", "none", 'stroke="#1a0f2e" stroke-width="3"') +
      rect(x - 36, 174, 72, 22, "#1a0f2e", 'rx="3"') + text(x, 189, name, 10, colour, 'font-family="monospace" font-weight="bold" letter-spacing="1"') +
      rect(x - 34, 204, 68, 60, "#0b0620", 'rx="4" stroke="#1a0f2e" stroke-width="3"') + group(screen, on ? "" : 'opacity=".75"') +
      path("M" + (x - 40) + " 286 L" + (x - 46) + " 312 H" + (x + 46) + " L" + (x + 40) + " 286 Z", "#2a1a4e") + circle(x - 18, 298, 5, "#ff2e97") + rect(x - 19.5, 290, 3, 8, "#1a0f2e") + circle(x + 10, 299, 4, "#70ebea") + circle(x + 22, 297, 4, "#ffdc83") +
      rect(x - 8, 334, 16, 24, "#1a0f2e", 'rx="2"') + rect(x - 2, 340, 4, 10, "#ffdc83") + // The coin slot.
      (on ? glow(rect(x - 34, 204, 68, 60, colour, 'rx="4" opacity=".18"')) : "") + hit(x - 44, 166, 88, 290, "game:" + slug), on ? 'class="lp-tw-on"' : "");
  }

  function arcadeInside(opts) { // The Arcade: a dark room of neon, a starry carpet, the cabinets in a row, a claw machine and the prize shelf at the sides.
    const roll = dice(13);
    let stars = "";
    for (let i = 0; i < 60; i++) stars += text(roll(-10, 1010), roll(362, 498), i % 3 ? "✦" : "·", roll(7, 12), NEON[i % 6], 'opacity=".45"');
    let waves = "";
    [70, 120].forEach(function (y, i) { waves += path("M-20 " + y + " Q60 " + (y - 14) + " 140 " + y + " T300 " + y + " T460 " + y + " T620 " + y + " T780 " + y + " T940 " + y + " T1100 " + y, "none", 'stroke="' + NEON[i * 2] + '" stroke-width="3" opacity=".35"'); });
    const claw = rect(132, 190, 96, 200, "#ff8ecb", 'rx="6" opacity=".85"') + rect(140, 200, 80, 110, "#1a1036", 'rx="4" opacity=".9"') + line(180, 200, 180, 238, "#d8d8e8", 2) + path("M172 238 h16 l4 10 M172 238 l-4 10", "none", 'stroke="#d8d8e8" stroke-width="2.5"') +
      [[156, 296, 0], [176, 300, 2], [198, 294, 3], [206, 302, 4]].map(function (p) { return circle(p[0], p[1], 8, NEON[p[2]]); }).join("") + text(180, 352, "CLAW", 12, "#1a0f2e", 'font-family="monospace" font-weight="bold"');
    const prizes = rect(772, 160, 150, 8, "#6a4a8a") + rect(772, 228, 150, 8, "#6a4a8a") + rect(772, 296, 150, 8, "#6a4a8a") +
      [[790, 150, 0], [826, 146, 2], [866, 152, 3], [902, 148, 1], [796, 218, 4], [840, 214, 5], [884, 216, 0], [808, 286, 3], [858, 282, 1], [900, 288, 2]].map(function (p) { return circle(p[0], p[1], 10, NEON[p[2]], 'opacity=".9"'); }).join("") +
      rect(764, 320, 166, 70, "#3a2466", 'rx="4"') + text(847, 362, "PRIZES", 14, "#ffdc83", 'font-family="monospace" font-weight="bold" letter-spacing="2"');
    let cabinets = "";
    const row = stations(opts.games.length), snug = opts.games.length > 4;
    opts.games.forEach(function (slug, i) { // five in a row stand a little smaller, scaled about the spot Lumi stands on
      const one = cabinet(row[i], slug, opts.cursor === i);
      cabinets += snug ? group(one, 'transform="translate(' + row[i] + ' 452) scale(' + SNUG + ') translate(' + (-row[i]) + ' -452)"') : one;
    });
    return group(rect(-20, -20, 1040, 372, "#1a0d33") + waves + glow(text(500, 120, "✦ A R C A D E ✦", 30, "#70ebea", 'font-family="monospace" font-weight="bold"')) +
      rect(-20, 350, 1040, 170, "#140a2e") + stars + rect(-20, 348, 1040, 5, "#ff2e97", 'opacity=".6"') + claw + prizes + cabinets + exitMat(opts.cursor === opts.games.length) +
      (opts.night ? "" : rect(-20, -20, 1040, 540, "#ffffff", 'opacity=".03" pointer-events="none"')), 'data-town="arcade"');
  }

  function station(x, slug, on) { // One of the Casino's tables: blackjack, roulette, the horse-racing screen, or a slot machine.
    let art = "";
    if (slug === "blackjack") art = path("M" + (x - 42) + " 336 A42 30 0 0 0 " + (x + 42) + " 336 Z", "#1f7a4a", 'stroke="#d8b24a" stroke-width="4"') + rect(x - 42, 330, 84, 8, "#6a2a1a") +
      rect(x - 16, 342, 12, 16, "#ffffff", 'rx="2" transform="rotate(-8 ' + (x - 10) + ' 350)"') + rect(x - 2, 342, 12, 16, "#ffffff", 'rx="2" transform="rotate(8 ' + (x + 4) + ' 350)"') + text(x - 9, 354, "A", 8, "#c8304a") + text(x + 5, 354, "K", 8, "#1a0f2e") +
      [0, 1, 2].map(function (k) { return oval(x + 30, 352 - k * 3, 7, 3, k % 2 ? "#ff8ecb" : "#ffffff"); }).join("") + line(x - 34, 368, x - 38, 392, "#4a1a10", 5) + line(x + 34, 368, x + 38, 392, "#4a1a10", 5);
    else if (slug === "roulette") {
      let wedges = "";
      for (let i = 0; i < 12; i++) { const a = i * Math.PI / 6, b = a + Math.PI / 6; wedges += path("M" + x + " 300 L" + (x + Math.cos(a) * 30).toFixed(1) + " " + (300 + Math.sin(a) * 30).toFixed(1) + " A30 30 0 0 1 " + (x + Math.cos(b) * 30).toFixed(1) + " " + (300 + Math.sin(b) * 30).toFixed(1) + " Z", i % 2 ? "#1a0f2e" : "#c8304a"); }
      art = rect(x - 44, 334, 88, 10, "#6a2a1a") + rect(x - 40, 344, 80, 46, "#1f7a4a") + circle(x, 300, 36, "#6a2a1a") + wedges + circle(x, 300, 12, "#d8b24a") + circle(x + 22, 290, 3, "#ffffff") + line(x, 290, x, 310, "#6a2a1a", 2) + line(x - 10, 300, x + 10, 300, "#6a2a1a", 2);
    }
    else if (slug === "horse-racing") art = rect(x - 46, 176, 92, 66, "#1a0f2e", 'rx="4" stroke="#d8b24a" stroke-width="3"') + rect(x - 40, 182, 80, 54, "#3a8a4a") + rect(x - 40, 196, 80, 2, "#ffffff", 'opacity=".6"') + rect(x - 40, 214, 80, 2, "#ffffff", 'opacity=".6"') +
      [[-26, 190, 0], [-8, 207, 2], [14, 225, 3], [26, 190, 1]].map(function (p) { return oval(x + p[0], p[1], 6, 3.5, NEON[p[2]]) + circle(x + p[0] + 6, p[1] - 3, 2.4, NEON[p[2]]); }).join("") + line(x + 34, 182, x + 34, 236, "#ffffff", 2, 'stroke-dasharray="3 3"') +
      rect(x - 40, 318, 80, 72, "#6a2a1a", 'rx="4"') + rect(x - 34, 326, 68, 22, "#2a0818", 'rx="3"') + text(x, 342, "BETS", 12, "#ffdc83", 'font-family="serif" font-weight="bold" letter-spacing="2"') + text(x, 374, "🏇", 16, "#ffffff");
    else art = path("M" + (x - 36) + " 390 V214 Q" + x + " 186 " + (x + 36) + " 214 V390 Z", "#c8304a", 'stroke="#d8b24a" stroke-width="3"') + rect(x - 28, 236, 56, 34, "#ffffff", 'rx="3"') +
      [["7", -18, "#c8304a"], ["★", 0, "#d8b24a"], ["♥", 18, "#ff8ecb"]].map(function (r) { return text(x + r[1], 260, r[0], 15, r[2], 'font-weight="bold"'); }).join("") + line(x - 28, 253, x + 28, 253, "#d8b24a", 1, 'opacity=".6"') +
      line(x + 40, 260, x + 40, 228, "#d8d8e8", 3) + circle(x + 40, 224, 6, "#ff2e97") + rect(x - 20, 300, 40, 10, "#2a0818", 'rx="2"') + glow(text(x, 206, "♦ ♦ ♦", 9, "#ffdc83"));
    const title = { blackjack: "BLACKJACK", roulette: "ROULETTE", "horse-racing": "RACES", slots: "SLOTS" }[slug] || "";
    return group((on ? marker(x, 452) : "") + text(x, slug === "horse-racing" ? 166 : 160, title, 10, on ? "#ffdc83" : "#d8b24a", 'font-family="serif" font-weight="bold" letter-spacing="1.5"') + art + hit(x - 44, 150, 88, 306, "game:" + slug), on ? 'class="lp-tw-on"' : "");
  }

  function casinoInside(opts) { // The Casino, Lumi's Lounge: velvet walls with gold diamonds, a red carpet, a chandelier, and the four tables.
    let diamonds = "";
    for (let y = 40; y < 340; y += 40) for (let x = -20 + (y % 80 ? 20 : 0); x < 1040; x += 40) diamonds += path("M" + x + " " + (y - 8) + " l6 8 l-6 8 l-6 -8 Z", "#d8b24a", 'opacity=".18"');
    let carpet = "";
    for (let x = -20; x < 1040; x += 60) carpet += path("M" + x + " 420 l30 -20 l30 20 l-30 20 Z", "#8a1a3a", 'opacity=".6"');
    const chandelier = line(500, 0, 500, 40, "#d8b24a", 2) + path("M440 40 Q500 70 560 40", "none", 'stroke="#d8b24a" stroke-width="3"') + glow([440, 470, 500, 530, 560].map(function (x) { return circle(x, x === 500 ? 58 : 46, 5, "#ffe6a8") + circle(x, x === 500 ? 58 : 46, 16, "#ffcf7a", 'opacity=".25"'); }).join(""));
    const sides = rect(80, 120, 120, 160, "#2a0818", 'rx="60" stroke="#d8b24a" stroke-width="4"') + text(140, 214, "♠ ♥", 30, "#d8b24a") + rect(800, 120, 120, 160, "#2a0818", 'rx="60" stroke="#d8b24a" stroke-width="4"') + text(860, 214, "♦ ♣", 30, "#d8b24a") +
      rect(60, 300, 40, 90, "#d8b24a", 'opacity=".5"') + rect(900, 300, 40, 90, "#d8b24a", 'opacity=".5"');
    let tables = "";
    const row = stations(opts.games.length);
    opts.games.forEach(function (slug, i) { tables += station(row[i], slug, opts.cursor === i); });
    return group(rect(-20, -20, 1040, 372, "#4a0f24") + diamonds + rect(-20, 340, 1040, 12, "#d8b24a", 'opacity=".7"') + glow(text(500, 112, "LUMI’S LOUNGE", 26, "#ffdc83", 'font-family="serif" font-weight="bold" letter-spacing="4"')) +
      chandelier + sides + rect(-20, 352, 1040, 168, "#5a1430") + carpet + tables + exitMat(opts.cursor === opts.games.length), 'data-town="casino"');
  }

  function fieldInside(opts) { // The Lumi-Soft field: sky, a far treeline, long grass, cardboard creatures on stakes, and the cushion with the Star Scope in front.
    const night = opts.night, on = opts.cursor === 0;
    let treeline = "";
    for (let x = -20; x < 1040; x += 46) treeline += circle(x, 236, 34, night ? "#1f4030" : "#4a9a58");
    const critter = function (x, y, s) { return line(x, y, x, y + s * 1.6, "#8a6a4a", s / 5) + circle(x, y, s, "#d8b890", 'stroke="#8a6a4a" stroke-width="' + s / 8 + '"') +
      path("M" + (x - s * .8) + " " + (y - s * .5) + " l" + s * .2 + " " + (-s * .8) + " l" + s * .45 + " " + s * .55 + " Z M" + (x + s * .8) + " " + (y - s * .5) + " l" + (-s * .2) + " " + (-s * .8) + " l" + (-s * .45) + " " + s * .55 + " Z", "#d8b890") +
      circle(x - s * .32, y - s * .1, s * .12, "#3a2a2a") + circle(x + s * .32, y - s * .1, s * .12, "#3a2a2a") + circle(x, y, s * .6, "none", 'stroke="#e8556a" stroke-width="' + s / 10 + '" opacity=".75"'); };
    let fence = "";
    for (let x = -20; x < 1040; x += 34) fence += rect(x, 288, 6, 30, "#c8a070");
    const cushion = oval(500, 440, 90, 22, "#ff8ecb") + oval(500, 434, 84, 16, "#ffb3dc") + rect(520, 412, 70, 14, "#3a3048", 'rx="7"') + circle(590, 419, 9, "#1a1a2e", 'stroke="#ffdc83" stroke-width="3"') + text(554, 423, "★", 10, "#ffdc83") + // The Star Scope, resting on the cushion.
      rect(380, 446, 40, 26, "#c8a070", 'rx="3"') + text(400, 464, "25¢", 10, "#5a3420", 'font-family="monospace"');
    return group(sky(night, 250) + treeline + rect(-20, 240, 1040, 280, night ? "#2f5a44" : "#8fd47a") + rect(-20, 240, 1040, 6, night ? "#24503a" : "#6cc46a") +
      critter(220, 262, 10) + critter(360, 270, 12) + critter(640, 266, 11) + critter(790, 258, 9) + critter(470, 300, 18) + critter(580, 318, 20) + fence + rect(-20, 296, 1040, 4, "#c8a070") +
      line(380, 330, 380, 140, "#8a6a4a", 5) + line(620, 330, 620, 140, "#8a6a4a", 5) + path("M376 140 H624 V182 L500 194 L376 182 Z", "#ff8ecb") + text(500, 172, "LUMI-SOFT", 22, "#ffffff", 'font-family="monospace" font-weight="bold" letter-spacing="4"') +
      group((on ? marker(440, 456) : "") + cushion + hit(360, 380, 260, 100, "game:" + opts.games[0]), on ? 'class="lp-tw-on"' : "") + exitMat(opts.cursor === opts.games.length) +
      (night ? glow(circle(380, 140, 6, "#ffe6a8") + circle(620, 140, 6, "#ffe6a8")) : ""), 'data-town="field"');
  }

  function marketStreet(opts) { // Market Street: the same sky and cobbles, the café and the Market, a fountain on the lane, bunting overhead.
    const night = opts.night, picked = MARKET[opts.cursor] ? opts.cursor : null, back = opts.cursor === "street:high";
    let places = "";
    Object.keys(MARKET).forEach(function (id) {
      const at = MARKET[id], on = id === picked;
      places += group(BUILD[id](at[0], at[1], night) + hit(at[0] - 66, at[1] - 156, 132, 164, "place:" + id), 'data-place="' + id + '"' + (on ? ' class="lp-tw-on"' : ""));
    });
    let bunting = "";
    for (let i = 0; i < 14; i++) bunting += path("M" + (296 + i * 30) + " " + (60 + Math.abs(7 - i) * 2) + " l12 0 l-6 11 Z", NEON[i % NEON.length]);
    const string = path("M290 58 Q500 82 710 58", "none", 'stroke="#ffe9f5" stroke-width="1.2" opacity=".7"');
    const fountain = oval(500, 456, 64, 16, night ? "#3a4a6a" : "#9ad4ff", 'stroke="#d8c8b4" stroke-width="6"') + rect(494, 414, 12, 40, "#d8c8b4") + oval(500, 414, 24, 7, "#d8c8b4") +
      glow(path("M500 414 Q486 392 474 412 M500 414 Q514 392 526 412 M500 412 V388", "none", 'stroke="' + (night ? "#c9a8ff" : "#ffffff") + '" stroke-width="2.5" opacity=".85"'));
    const cart = rect(330, 448, 60, 22, "#c8a070", 'rx="3"') + circle(342, 474, 7, "#8a6a4a") + circle(378, 474, 7, "#8a6a4a") +
      [0, 1, 2, 3, 4].map(function (i) { return circle(338 + i * 11, 444, 5, NEON[(i + 1) % NEON.length]); }).join("") + line(330, 448, 318, 432, "#8a6a4a", 3);
    const bench = rect(612, 452, 70, 8, "#8a5a3a", 'rx="2"') + rect(612, 440, 70, 6, "#8a5a3a", 'rx="2"') + rect(618, 460, 5, 14, "#5a3a2a") + rect(670, 460, 5, 14, "#5a3a2a");
    const sides = tree(70, 232, 40, night) + tree(180, 228, 30, night) + tree(250, 236, 22, night) + tree(790, 230, 28, night) + tree(890, 236, 36, night) + tree(960, 226, 24, night) +
      lamp(282, 244, night) + lamp(718, 244, night) + tree(110, 440, 36, night) + tree(226, 446, 26, night) + tree(790, 440, 30, night) + tree(900, 444, 40, night) + lamp(718, 448, night);
    const mark = picked ? marker(MARKET[picked][0], MARKET[picked][1] + 36) : "";
    const tag = picked && opts.label ? label(MARKET[picked][0], MARKET[picked][1] - 168, opts.label)
      : back && opts.label ? label(SIGNS.high[0] + 40, SIGNS.high[1] - 40, opts.label) : "";
    return group(sky(night, 140) + hills(night) + ground(night) + sides + string + bunting + fountain + cart + bench + signpost("high", "HIGH ST", back) + mark + places +
      (night ? rect(-20, -20, 1040, 540, "#0a0620", 'opacity=".12" pointer-events="none"') : "") + tag, 'data-town="market-street" data-time="' + (night ? "night" : "day") + '"');
  }

  function stall(x, id, on) { // One of the Market's stalls: a striped awning, its counter, and what it sells on show.
    const colour = { store: "#ff8ecb", capsules: "#c9a8ff", seeds: "#8be9a8", garden: "#ffdc83" }[id] || "#ff9d6b";
    const name = { store: "STORE", capsules: "CAPSULES", seeds: "SEEDS", garden: "GARDEN" }[id] || "STALL";
    let stripes = "", goods = "";
    for (let i = 0; i < 6; i++) stripes += path("M" + (x - 42 + i * 14) + " 230 h14 l1 14 a7.5 7.5 0 0 1 -15 0 Z", i % 2 ? "#fff3e6" : colour);
    if (id === "store") goods = [["🍪", -22, 300], ["🍌", 0, 300], ["🥛", 22, 300], ["🎾", -12, 332], ["🧸", 12, 332]].map(function (g) { return text(x + g[1], g[2], g[0], 16, "#ffffff"); }).join("");
    else if (id === "capsules") goods = [[-18, "#ff8ecb", "★"], [18, "#70ebea", "☾"]].map(function (m) { // Two capsule machines: a glass globe of capsules on a coin stand.
      return rect(x + m[0] - 14, 316, 28, 34, m[1], 'rx="4"') + circle(x + m[0], 300, 16, "#ffffff", 'opacity=".55" stroke="' + m[1] + '" stroke-width="2"') +
        circle(x + m[0] - 6, 302, 4, "#ffdc83") + circle(x + m[0] + 5, 296, 4, "#8be9a8") + circle(x + m[0] + 2, 306, 4, "#ff9d6b") + text(x + m[0], 338, m[2], 11, "#ffffff"); }).join("");
    else if (id === "seeds") goods = [-24, -8, 8, 24].map(function (dx, i) { return rect(x + dx - 6, 290, 12, 16, NEON[i], 'rx="1"') + circle(x + dx, 296, 3, "#ffffff", 'opacity=".8"'); }).join("") +
      [-16, 0, 16].map(function (dx, i) { return path("M" + (x + dx - 8) + " 326 h16 l-3 14 h-10 Z", "#c06a3a") + line(x + dx, 326, x + dx, 316, "#3f8a4a", 2) + circle(x + dx, 314, 4, NEON[(i + 3) % NEON.length]); }).join("");
    else goods = path("M" + (x - 30) + " 340 h22 l-4 -18 h-14 Z", "#a07a5a") + circle(x - 19, 316, 9, "#4fae5a") + // A planter, a little lantern and a birdbath.
      rect(x + 2, 300, 10, 16, "#3a3048", 'rx="2"') + glow(rect(x + 4, 304, 6, 8, "#ffdc83")) + rect(x + 6, 316, 2, 24, "#3a3048") +
      oval(x + 28, 320, 12, 4, "#9ad4ff", 'stroke="#d8c8b4" stroke-width="2"') + rect(x + 26, 322, 4, 18, "#d8c8b4");
    return group((on ? marker(x, 452) : "") + rect(x - 40, 244, 4, 120, "#8a6a4a") + rect(x + 36, 244, 4, 120, "#8a6a4a") + stripes +
      rect(x - 34, 210, 68, 18, "#5a3420", 'rx="3"') + text(x, 223, name, 9.5, colour, 'font-family="monospace" font-weight="bold" letter-spacing="1"') + goods +
      rect(x - 44, 350, 88, 30, "#c8a070", 'rx="3"') + rect(x - 44, 350, 88, 5, "#a07a5a") + (on ? glow(rect(x - 44, 244, 88, 136, colour, 'opacity=".12"')) : "") +
      hit(x - 46, 206, 92, 250, "stall:" + id), on ? 'class="lp-tw-on"' : "");
  }

  function marketInside(opts) { // The Market under its roof: wooden beams, lanterns, a tiled floor and the stalls in a row.
    const night = opts.night;
    let beams = "", tiles = "", lanterns = "";
    for (let x = -20; x < 1040; x += 120) beams += rect(x, 0, 14, 360, "#6a4a3a", 'opacity=".6"');
    for (let x = -20; x < 1040; x += 40) for (let y = 380; y < 520; y += 40) tiles += rect(x, y, 38, 38, (x + y) % 80 ? "#e8d0b0" : "#d8bc98");
    [180, 340, 500, 660, 820].forEach(function (x, i) { lanterns += line(x, 0, x, 70, "#8a6a4a", 1.5) + glow(oval(x, 84, 12, 16, NEON[i % NEON.length], 'opacity=".9"')); });
    let stalls = "";
    const row = stations(opts.games.length);
    opts.games.forEach(function (id, i) { stalls += stall(row[i], id, opts.cursor === i); });
    return group(rect(-20, -20, 1040, 400, night ? "#3a2a3a" : "#f3e3c8") + beams + lanterns + glow(text(500, 150, "✦ T H E   M A R K E T ✦", 24, "#c06a9a", 'font-family="serif" font-weight="bold"')) +
      rect(-20, 376, 1040, 150, "#e8d0b0") + tiles + rect(-20, 374, 1040, 6, "#a07a5a") + stalls + exitMat(opts.cursor === opts.games.length) +
      (night ? rect(-20, -20, 1040, 540, "#0a0620", 'opacity=".18" pointer-events="none"') : ""), 'data-town="market"');
  }

  // ---- What game.js asks for ------------------------------------------------------------------------------
  function scene(opts) { // The picture for where she is. opts: at (a place she's inside, or null for the map), cursor (the map: a
    // place id; inside: the row number, one past the games for the exit), games (the slugs inside, in town-ui.js order), label, night.
    // street: which map ("high" or "market"); the map's cursor may also be "street:<where>", its signpost to the other street.
    const o = { at: opts.at || null, cursor: opts.cursor, games: opts.games || [], label: opts.label || "", night: Boolean(opts.night), street: opts.street || "high" };
    if (o.at === "market") return marketInside(o);
    if (!o.at && o.street === "market") return marketStreet(o);
    if (o.at === "arcade") return arcadeInside(o);
    if (o.at === "casino") return casinoInside(o);
    if (o.at === "field") return fieldInside(o);
    return townMap(o);
  }

  function spot(at, cursor, count) { // Where Lumi stands (the middle of her feet) for this cursor: by a place's door on the map, or in front of a station inside.
    if (!at && typeof cursor === "string" && cursor.indexOf("street:") === 0 && SIGNS[cursor.slice(7)]) { // Beside a street's signpost.
      const s = SIGNS[cursor.slice(7)];
      return { x: s[0] + (s[0] > 500 ? -26 : 26), y: s[1] + 14 };
    }
    if (!at) { const p = ALL[cursor] || MAP.tavern; return { x: p[0], y: p[1] + (p[1] > 300 ? 38 : 36) }; }
    if (cursor >= count) return { x: 500, y: 474 }; // On the exit mat.
    if (at === "field") return { x: 440, y: 452 }; // Beside the cushion.
    const row = stations(count);
    return { x: row[Math.min(cursor, row.length - 1)], y: 452 };
  }

  return { MAP: MAP, MARKET: MARKET, SIGNS: SIGNS, STATIONS: STATIONS, SNUG: SNUG, stations: stations, scene: scene, spot: spot };
});
