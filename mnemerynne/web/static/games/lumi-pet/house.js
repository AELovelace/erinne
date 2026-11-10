/* The art for Lumi's house: every object that can stand in a spot, drawn as plain SVG in a 400 x 500 block that
   render() sets into a 1000 x 500 room (see the end of this file).
   The server's catalog (mnemerynne/lumipet_data/house.json) says which objects exist, what they cost and which spot
   they belong to; this file only knows how each one looks. Every catalog id needs an entry in ART here
   (tests/js/lumi_house.test.mjs checks). It is paperdoll furniture: nothing is placed freely, each spot shows one
   object, and LAYERS is the fixed back-to-front order, the way Littlepottchi layered an outfit.

   All of it is authored here and never contains anything a person typed, so building markup from it is safe. */
(function (root, make) {
  const house = make();
  if (typeof module === "object" && module.exports) module.exports = house; else root.LumiPetHouse = house;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const LAYERS = ["wallpaper", "floor", "wallart", "shelf", "view", "frame", "curtains", "lamp", "rug", "bed"];
  const GLOW = "var(--pet-glow)"; // Accents follow Lumi's own glow colour, so the room always suits her.

  // ---- Small drawing helpers: each returns a piece of SVG markup.
  function rect(x, y, w, h, fill, more) { return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function circle(x, y, r, fill, more) { return '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function oval(x, y, rx, ry, fill, more) { return '<ellipse cx="' + x + '" cy="' + y + '" rx="' + rx + '" ry="' + ry + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function path(d, fill, more) { return '<path d="' + d + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function line(x1, y1, x2, y2, stroke, width, more) { return '<path d="M' + x1 + " " + y1 + " L" + x2 + " " + y2 + '" fill="none" stroke="' + stroke + '" stroke-width="' + (width || 1) + '" ' + (more || "") + "/>"; }
  function group(inner, more) { return "<g " + (more || "") + ">" + inner + "</g>"; }
  function star(x, y, r) { // A four-pointed sparkle, like the one on Lumi's forehead.
    const s = r * 0.28;
    return "M" + x + " " + (y - r) + " L" + (x + s) + " " + (y - s) + " L" + (x + r) + " " + y + " L" + (x + s) + " " + (y + s) + " L" + x + " " + (y + r) +
      " L" + (x - s) + " " + (y + s) + " L" + (x - r) + " " + y + " L" + (x - s) + " " + (y - s) + "Z";
  }
  function dice(seed) { // The same "random" scatter every time, so a wallpaper doesn't reshuffle on each redraw.
    let value = seed;
    return function (low, high) { value = (value * 16807) % 2147483647; return low + (value / 2147483647) * (high - low); };
  }
  function scatter(seed, count, draw) { // Call draw(roll) `count` times with one shared seeded dice.
    const roll = dice(seed);
    let out = "";
    for (let i = 0; i < count; i++) out += draw(roll, i);
    return out;
  }
  function round(n) { return Math.round(n * 10) / 10; }

  // ---- The window. The view is clipped to the frame's opening; a view is a sky and three strips of scenery that
  // slide sideways at different speeds (the far one slowest), which is what makes it look deep.
  const WIN = { x: 140, y: 96, w: 120, h: 130 }, TILE = 240; // The opening's box, and the width a strip repeats over.
  const CLIPS = { // The opening of each window frame.
    "frame-arch": "M140 226 V150 A60 54 0 0 1 260 150 V226 Z",
    "frame-round": "M140 161 A60 60 0 1 0 260 161 A60 60 0 1 0 140 161 Z",
    "frame-panes": "M140 100 H260 V226 H140 Z",
    "frame-hex": "M200 96 L258 128 V194 L200 226 L142 194 V128 Z",
    "frame-ears": "M140 226 V140 L150 100 L178 124 Q200 116 222 124 L250 100 L260 140 V226 Z",
  };

  function strip(depth, tile) { // One sliding strip: its tile drawn twice side by side, so the loop has no seam.
    return group(tile + group(tile, 'transform="translate(' + TILE + ' 0)"'), 'class="lp-px lp-px-' + depth + '"');
  }
  function falling(kind, tile) { // Rain or snow: a tile drawn twice, one above the other, sliding down.
    return group(group(tile, 'transform="translate(0 -' + WIN.h + ')"') + tile, 'class="lp-fall lp-fall-' + kind + '"');
  }
  function view(day, night, sunY, far, mid, near, extra) { // A whole view, in the window's own coordinates.
    const id = "lp-sky-" + day[0].slice(1);
    return group(
      "<defs>" + [["d", day], ["n", night]].map(function (sky) {
        return '<linearGradient id="' + id + sky[0] + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + sky[1][0] + '"/><stop offset="1" stop-color="' + sky[1][1] + '"/></linearGradient>';
      }).join("") + "</defs>" +
      rect(0, 0, WIN.w, WIN.h, "url(#" + id + "n)") + rect(0, 0, WIN.w, WIN.h, "url(#" + id + "d)", 'class="lp-sky-day"') +
      scatter(7, 14, function (roll) { return circle(round(roll(4, 116)), round(roll(4, 70)), round(roll(.4, 1)), "#fff", 'class="lp-sky-star"'); }) +
      circle(88, sunY, 9, "#ffe3a9", 'class="lp-sun"') +
      strip(1, far) + strip(2, mid) + strip(3, near) + (extra || "") +
      rect(0, 0, WIN.w, WIN.h, "#0a0a2a", 'class="lp-night-veil"'),
      'transform="translate(' + WIN.x + " " + WIN.y + ')"');
  }
  function skyline(seed, base, low, high, fill, lit) { // A row of buildings across one tile, some with lit windows.
    let x = 0, out = "";
    const roll = dice(seed);
    while (x < TILE) {
      const w = Math.round(roll(12, 26)), h = Math.round(roll(low, high));
      out += rect(x, base - h, w + 1, h + 40, fill);
      if (lit) for (let wy = base - h + 5; wy < base - 4; wy += 9) for (let wx = x + 3; wx < x + w - 4; wx += 7) if (roll(0, 1) > .55) out += rect(wx, wy, 3, 4, lit);
      x += w;
    }
    return out;
  }
  function hills(seed, base, height, fill) { // Soft rolling hills across one tile, ending where they began.
    const roll = dice(seed), step = TILE / 4;
    let d = "M0 " + base;
    for (let i = 0; i < 4; i++) d += " Q" + (i * step + step / 2) + " " + round(base - roll(height * .5, height)) * 1 + " " + ((i + 1) * step) + " " + base;
    return path(d + " V140 H0 Z", fill);
  }
  function pines(seed, base, size, fill, gap) { // A line of pine trees.
    return scatter(seed, Math.floor(TILE / gap), function (roll, i) {
      const x = i * gap + roll(0, gap * .4), h = roll(size * .7, size), w = h * .45;
      return path("M" + round(x) + " " + base + " L" + round(x + w / 2) + " " + round(base - h) + " L" + round(x + w) + " " + base + " Z", fill);
    }) + rect(0, base - 1, TILE, 60, fill);
  }
  function waves(base, fill, swell) { // A band of sea with a scalloped top that tiles exactly.
    let d = "M0 " + base;
    for (let x = 0; x < TILE; x += 20) d += " q5 -" + swell + " 10 0 t10 0";
    return path(d + " V140 H0 Z", fill);
  }

  // ---- Every object, by its catalog id.
  const ART = {
    // Wallpaper: the wall fills the room down to the floor line at y = 352.
    "wall-dusk": function () { return rect(0, 0, 400, 352, "#2a1a3e") + oval(200, 300, 270, 170, "#562452", 'opacity=".55"'); },
    "wall-stars": function () {
      return rect(0, 0, 400, 352, "#161a36") + scatter(11, 46, function (roll) {
        const x = round(roll(8, 392)), y = round(roll(8, 340)), r = roll(0, 1);
        return r > .8 ? path(star(x, y, 5), "#ffe9a8", 'opacity=".8"') : circle(x, y, round(.6 + r), "#e6d5ff", 'opacity="' + round(.3 + r * .6) + '"');
      });
    },
    "wall-stripes": function () {
      let out = rect(0, 0, 400, 352, "#3a2147");
      for (let x = 10; x < 400; x += 40) out += rect(x, 0, 20, 352, "#4b2a59");
      return out + rect(0, 300, 400, 52, "#2d1a38") + rect(0, 298, 400, 3, "#6d4580");
    },
    "wall-clouds": function () {
      return rect(0, 0, 400, 352, "#2b3157") + scatter(5, 7, function (roll) {
        const x = roll(20, 380), y = roll(30, 320), s = roll(.7, 1.3);
        return group(oval(0, 0, 26, 11, "#3d4675") + oval(-14, -7, 14, 10, "#3d4675") + oval(10, -9, 16, 12, "#3d4675"), 'transform="translate(' + round(x) + " " + round(y) + ") scale(" + round(s) + ')"');
      });
    },
    "wall-grid": function () {
      let out = rect(0, 0, 400, 352, "#120f2a");
      for (let x = 0; x <= 400; x += 40) out += line(x, 0, x, 352, GLOW, 1, 'opacity=".22"');
      for (let y = 32; y <= 352; y += 40) out += line(0, y, 400, y, GLOW, 1, 'opacity=".22"');
      return out + rect(0, 0, 400, 352, "#120f2a", 'opacity=".25"');
    },

    // Floor: from y = 350 down, with a skirting line where it meets the wall.
    "floor-boards": function () {
      let out = rect(0, 350, 400, 150, "#1c1326") + rect(0, 350, 400, 3, "#dc9bca", 'opacity=".25"');
      for (let y = 378; y < 500; y += 28) out += line(0, y, 400, y, "#2c1f3a", 1.5);
      return out;
    },
    "floor-checker": function () {
      let out = rect(0, 350, 400, 150, "#2a2238");
      for (let row = 0; row < 3; row++) for (let col = 0; col < 8; col++) if ((row + col) % 2) out += rect(col * 50, 350 + row * 50, 50, 50, "#3b3152");
      return out + rect(0, 350, 400, 3, "#8a7aa8", 'opacity=".5"');
    },
    "floor-honey": function () {
      let out = rect(0, 350, 400, 150, "#6b4a2f") + rect(0, 350, 400, 3, "#d9b27c", 'opacity=".6"');
      for (let y = 380, i = 0; y < 500; y += 30, i++) { out += line(0, y, 400, y, "#54391f", 1.5); for (let x = (i % 2) * 60 + 40; x < 400; x += 120) out += line(x, y - 30, x, y, "#54391f", 1.5); }
      return out;
    },
    "floor-moss": function () {
      return rect(0, 350, 400, 150, "#23402f") + scatter(3, 90, function (roll) { return circle(round(roll(0, 400)), round(roll(356, 498)), round(roll(1, 3)), "#2f5a3f"); }) + rect(0, 350, 400, 3, "#7fc49a", 'opacity=".4"');
    },
    "floor-starglass": function () {
      return rect(0, 350, 400, 150, "#141a3a") + oval(200, 430, 230, 60, GLOW, 'opacity=".12"') + scatter(9, 26, function (roll) {
        return path(star(round(roll(10, 390)), round(roll(360, 494)), round(roll(2, 5))), "#cfe0ff", 'opacity="' + round(roll(.3, .8)) + '"');
      }) + rect(0, 350, 400, 3, GLOW, 'opacity=".5"');
    },

    // Wall art: on the left wall.
    "art-star": function () { return rect(38, 150, 60, 76, "#1a1430", 'stroke="#9c73b8" stroke-width="3" rx="3"') + path(star(68, 188, 22), "#ffe9a8"); },
    "art-moons": function () {
      let out = rect(26, 164, 86, 34, "#101630", 'stroke="#8fa2d8" stroke-width="2.5" rx="3"');
      [0, 1, 2, 3, 4].forEach(function (i) { out += circle(41 + i * 14, 181, 5, "#f0deff") + circle(41 + i * 14 + (i - 2) * 3.4, 181, i === 2 ? 0 : 5, "#101630"); });
      return out;
    },
    "art-portrait": function () {
      return rect(36, 146, 64, 82, "#d9b24a", 'rx="5"') + rect(42, 152, 52, 70, "#3a2147") + path("M52 190 L52 166 L62 176 Q68 173 74 176 L84 166 L84 190 Q84 208 68 208 Q52 208 52 190Z", "#fce1f2") +
        circle(61, 190, 2, "#462846") + circle(75, 190, 2, "#462846");
    },
    "art-heart": function () {
      return path("M68 212 C40 190 44 162 60 162 Q68 162 68 172 Q68 162 76 162 C92 162 96 190 68 212Z", "none", 'stroke="' + GLOW + '" stroke-width="4" stroke-linejoin="round" class="lp-neon"');
    },
    "art-constellation": function () {
      const dots = [[48, 162], [60, 176], [78, 170], [90, 186], [72, 198], [52, 210], [84, 214]];
      return rect(36, 148, 66, 80, "#0d1230", 'stroke="#6f7fc0" stroke-width="2.5" rx="3"') +
        path("M" + dots.map(function (p) { return p.join(" "); }).join(" L"), "none", 'stroke="#8fa2d8" stroke-width="1" opacity=".7"') +
        dots.map(function (p, i) { return i % 3 ? circle(p[0], p[1], 2, "#fff") : path(star(p[0], p[1], 5), "#ffe9a8"); }).join("");
    },

    // Shelf: a board on the right wall, with things on it.
    "shelf-books": function () {
      const spines = ["#c85a8e", "#70ebea", "#ffdc83", "#9c73b8", "#7fc49a", "#e98a5a"];
      let x = 302, out = "";
      spines.forEach(function (colour, i) { const w = 8 + (i * 3) % 5, h = 26 + (i * 7) % 12; out += rect(x, 200 - h, w, h, colour, 'rx="1"'); x += w + 1; });
      return out + path("M" + (x + 2) + " 200 l10 -24 l8 3 l-10 21 Z", "#8fa2d8") + ART.board();
    },
    "shelf-plants": function () {
      return [312, 337, 362].map(function (x, i) {
        const leaf = ["#7fc49a", "#5fae86", "#9bd6ae"][i];
        return oval(x - 5, 180 - i * 2, 4, 10, leaf, 'transform="rotate(-25 ' + (x - 5) + " 180)\"") + oval(x + 5, 180 - i * 2, 4, 10, leaf, 'transform="rotate(25 ' + (x + 5) + " 180)\"") +
          oval(x, 174 - i * 3, 4, 12, leaf) + path("M" + (x - 8) + " 186 h16 l-2 14 h-12 Z", "#c0764f");
      }).join("") + ART.board();
    },
    "shelf-trophies": function () {
      return [314, 338, 362].map(function (x, i) {
        const tall = [0, 8, 3][i];
        return path("M" + (x - 8) + " " + (172 - tall) + " h16 q0 14 -8 16 q-8 -2 -8 -16 Z", "#ffdc83") + rect(x - 1.5, 187 - tall, 3, 8 + tall, "#d9b24a") + rect(x - 6, 196, 12, 4, "#8a6a2a") +
          path(star(x, 178 - tall, 4), "#fff8dc");
      }).join("") + ART.board();
    },
    "shelf-crystals": function () {
      return [[310, "#70ebea", 26], [326, "#ff8ecb", 18], [344, "#b9a2ff", 32], [362, "#ffdc83", 20]].map(function (c) {
        return path("M" + (c[0] - 7) + " 200 L" + (c[0] - 4) + " " + (200 - c[2] * .7) + " L" + c[0] + " " + (200 - c[2]) + " L" + (c[0] + 5) + " " + (200 - c[2] * .6) + " L" + (c[0] + 7) + " 200 Z", c[1], 'opacity=".85" class="lp-neon"');
      }).join("") + ART.board();
    },
    "shelf-plushies": function () {
      return [[314, "#ffb3d1"], [338, "#b9e4ff"], [362, "#ffe9a8"]].map(function (p) {
        const x = p[0];
        return circle(x - 6, 177, 4, p[1]) + circle(x + 6, 177, 4, p[1]) + oval(x, 190, 10, 10, p[1]) + circle(x, 182, 8, p[1]) + circle(x - 3, 181, 1.2, "#462846") + circle(x + 3, 181, 1.2, "#462846");
      }).join("") + ART.board();
    },
    board: function () { return rect(296, 200, 82, 6, "#7a5a8c", 'rx="2"') + path("M304 206 l6 10 M370 206 l-6 10", "none", 'stroke="#7a5a8c" stroke-width="3" stroke-linecap="round"'); },

    // Window views.
    "view-city": function () {
      return view(["#7a4b9c", "#e88aa5"], ["#12132d", "#3b2a57"], 30, skyline(21, 96, 34, 62, "#4a3580"), skyline(33, 110, 30, 58, "#2e2160", "#ffd97a"), skyline(45, 128, 12, 30, "#17102f", "#70ebea"));
    },
    "view-sea": function () {
      return view(["#4a6cc0", "#f2b5a0"], ["#0b1030", "#1c2f66"], 26, hills(8, 84, 18, "#3d5a9c"), waves(92, "#2a5c94", 3),
        waves(108, "#1a3d6e", 4) + scatter(4, 9, function (roll) { return path(star(round(roll(6, 234)), round(roll(114, 128)), 2.4), "#cfe8ff", 'opacity=".8"'); }));
    },
    "view-forest": function () {
      return view(["#5a9ec0", "#f6dca0"], ["#0f1c2e", "#23405a"], 28, hills(14, 86, 30, "#3f7a66"), pines(15, 104, 34, "#27584a", 16), pines(16, 126, 46, "#143229", 22));
    },
    "view-snow": function () {
      return view(["#8fb4e0", "#f4e4ef"], ["#141a3a", "#3a4a7a"], 26, hills(31, 80, 46, "#b8cdea"), hills(32, 100, 24, "#dbe8f7"), hills(33, 120, 14, "#f4f9ff") + pines(34, 122, 16, "#6f8fb0", 60),
        falling("snow", scatter(6, 22, function (roll) { return circle(round(roll(0, 120)), round(roll(0, 130)), round(roll(.8, 1.8)), "#fff", 'opacity=".9"'); })));
    },
    "view-rain": function () {
      return view(["#6d7a96", "#a9a4b8"], ["#12151f", "#2b3040"], 24, skyline(51, 92, 26, 50, "#59627a"), skyline(52, 108, 18, 34, "#3d4458", "#ffd97a"),
        skyline(53, 126, 8, 18, "#232733") + scatter(54, 5, function (roll, i) { return rect(20 + i * 48, 100, 7, 16, "#232733"); }),
        falling("rain", scatter(2, 26, function (roll) { const x = round(roll(0, 124)), y = round(roll(0, 122)); return line(x, y, x - 2, y + 8, "#cfe0ff", .8, 'opacity=".6"'); })));
    },

    // Window frames: drawn over the view. Each has a sill.
    "frame-arch": function () { return path(CLIPS["frame-arch"], "none", 'stroke="#9c73b8" stroke-width="5" stroke-linejoin="round"') + line(200, 96, 200, 226, "#9c73b8", 3) + ART.sill("#9c73b8"); },
    "frame-round": function () { return path(CLIPS["frame-round"], "none", 'stroke="#c9a15a" stroke-width="7"') + line(200, 101, 200, 221, "#c9a15a", 3) + line(140, 161, 260, 161, "#c9a15a", 3) + [0, 1, 2, 3].map(function (i) { const a = Math.PI / 4 + i * Math.PI / 2; return circle(round(200 + 60 * Math.cos(a)), round(161 + 60 * Math.sin(a)), 3, "#8a6a2a"); }).join(""); },
    "frame-panes": function () { return path(CLIPS["frame-panes"], "none", 'stroke="#e8dff0" stroke-width="5" stroke-linejoin="round"') + line(200, 100, 200, 226, "#e8dff0", 3) + line(140, 163, 260, 163, "#e8dff0", 3) + ART.sill("#e8dff0"); },
    "frame-hex": function () { return path(CLIPS["frame-hex"], "none", 'stroke="#ffdc83" stroke-width="5" stroke-linejoin="round"') + path("M200 96 V226 M142 128 L258 194 M258 128 L142 194", "none", 'stroke="#ffdc83" stroke-width="2" opacity=".7"'); },
    "frame-ears": function () { return path(CLIPS["frame-ears"], "none", 'stroke="' + GLOW + '" stroke-width="5" stroke-linejoin="round"') + path("M153 112 L158 128 L168 122 Z M247 112 L242 128 L232 122 Z", GLOW, 'opacity=".7"') + ART.sill(GLOW); },
    sill: function (colour) { return rect(131, 224, 138, 8, colour, 'rx="3"'); },

    // Curtains: in front of the frame.
    "curtain-none": function () { return ""; },
    "curtain-rose": function () { return ART.rod() + path("M126 88 H168 Q150 150 162 236 H126 Z", "#c85a8e", 'opacity=".92"') + path("M274 88 H232 Q250 150 238 236 H274 Z", "#c85a8e", 'opacity=".92"') + path("M140 88 Q142 160 138 236 M260 88 Q258 160 262 236", "none", 'stroke="#8f3a66" stroke-width="2"'); },
    "curtain-sheer": function () { return ART.rod() + path("M126 88 H274 V236 Q250 244 226 236 Q200 228 174 236 Q150 244 126 236 Z", "#70ebea", 'opacity=".22"') + path("M150 88 V238 M175 88 V234 M200 88 V232 M225 88 V234 M250 88 V238", "none", 'stroke="#d2fcf6" stroke-width="1" opacity=".35"'); },
    "curtain-valance": function () { return ART.rod() + path("M124 86 H276 V104 q-9.5 14 -19 0 q-9.5 14 -19 0 q-9.5 14 -19 0 q-9.5 14 -19 0 q-9.5 14 -19 0 q-9.5 14 -19 0 q-9.5 14 -19 0 q-9.5 14 -19 0 Z", "#6d4bb0") + [143, 181, 219, 257].map(function (x) { return path(star(x, 97, 5), "#ffe9a8"); }).join(""); },
    "curtain-beads": function () { // Strings long at the sides and short in the middle, so the view stays clear.
      let out = ART.rod();
      for (let x = 132, i = 0; x <= 268; x += 12, i++) { const length = 16 + Math.pow(Math.abs(5.5 - i), 2) * 4.4; for (let y = 96; y < 88 + length; y += 9) out += circle(x, y, 2.4, ["#ff8ecb", "#70ebea", "#ffdc83"][(i + Math.round(y / 9)) % 3], 'opacity=".9"'); }
      return out;
    },
    rod: function () { return rect(120, 84, 160, 5, "#d9b24a", 'rx="2.5"') + circle(120, 86.5, 5, "#d9b24a") + circle(280, 86.5, 5, "#d9b24a"); },

    // Lamps: on the left.
    "lamp-floor": function () { return oval(52, 300, 60, 70, "#ffdc83", 'opacity=".1"') + rect(50, 268, 4, 132, "#8a7aa8") + oval(52, 400, 16, 5, "#8a7aa8") + path("M34 270 L42 240 H62 L70 270 Z", "#ffdc83", 'opacity=".9"'); },
    "lamp-lantern": function () { return line(56, 60, 56, 232, "#8a7aa8", 1.5) + oval(56, 256, 46, 46, "#ff9d6b", 'opacity=".12"') + oval(56, 256, 20, 26, "#ff9d6b", 'opacity=".92"') + path("M40 246 H72 M38 256 H74 M40 266 H72", "none", 'stroke="#c96a3e" stroke-width="1.2"') + rect(50, 229, 12, 4, "#5a3a2a") + rect(50, 280, 12, 4, "#5a3a2a"); },
    "lamp-mushroom": function () { return oval(56, 392, 44, 30, "#ff8e8e", 'opacity=".14"') + path("M48 404 Q46 382 52 378 H60 Q66 382 64 404 Z", "#fff1e0") + path("M30 380 Q56 338 82 380 Q56 388 30 380 Z", "#e8556a") + circle(46, 368, 4, "#fff") + circle(60, 360, 3, "#fff") + circle(68, 372, 3.5, "#fff") + oval(56, 405, 14, 3, "#00000055"); },
    "lamp-lava": function () { return oval(54, 372, 34, 50, GLOW, 'opacity=".12"') + path("M46 404 L50 340 H58 L62 404 Z", GLOW, 'opacity=".35"') + circle(54, 390, 5, GLOW) + circle(53, 372, 3.5, GLOW) + circle(55, 356, 2.5, GLOW) + path("M44 404 H64 L60 414 H48 Z", "#8a7aa8") + path("M49 340 H59 L57 330 H51 Z", "#8a7aa8"); },
    "lamp-fairy": function () {
      const lights = [28, 62, 96, 130, 164, 200, 236, 270, 304, 338, 372];
      return path("M10 62 Q105 92 200 70 Q295 92 390 62", "none", 'stroke="#5a4a70" stroke-width="1.2"') + lights.map(function (x, i) {
        const y = round(66 + 12 * Math.sin((x - 10) / 380 * Math.PI * 2 - Math.PI / 2) * -1 + 8), colour = ["#ffdc83", "#ff8ecb", "#70ebea"][i % 3];
        return circle(x, y, 7, colour, 'opacity=".18"') + circle(x, y, 2.8, colour, 'class="lp-twinkle" style="animation-delay:' + (i * .37 % 2).toFixed(2) + 's"');
      }).join("");
    },

    // Rugs: in the middle of the floor, under Lumi.
    "rug-oval": function () { return oval(200, 440, 152, 36, "#c18ab8", 'opacity=".08"') + oval(200, 440, 140, 30, "#2d1d3d", 'stroke="#e5b5d4" stroke-opacity=".3" stroke-width="2"'); },
    "rug-moon": function () { return oval(200, 440, 120, 30, "#f0e6c8") + oval(228, 434, 96, 24, "#d8cba0") + oval(160, 446, 8, 3, "#d8cba0") + oval(140, 434, 5, 2, "#d8cba0"); },
    "rug-star": function () { return group(path(star(0, 0, 150), "#ffdc83") + path(star(0, 0, 96), "#fff1cd"), 'transform="translate(200 440) scale(1 .27)"'); },
    "rug-rainbow": function () { return ["#e8556a", "#ff9d6b", "#ffdc83", "#7fc49a", "#70b8eb", "#9c73b8"].map(function (colour, i) { return rect(70, 414 + i * 9, 260, 9.5, colour, i === 0 || i === 5 ? 'rx="3"' : ""); }).join(""); },
    "rug-paw": function () { return oval(200, 446, 86, 22, "#ffb3d1") + [[-70, -8], [-26, -20], [26, -20], [70, -8]].map(function (p) { return oval(200 + p[0], 432 + p[1] * .6, 22, 8, "#ffb3d1"); }).join("") + oval(200, 448, 44, 11, "#ff8ecb", 'opacity=".5"'); },

    // Beds: on the right of the floor.
    "bed-cushion": function () { return oval(340, 438, 50, 15, "#6d3b7a") + oval(340, 434, 42, 11, "#8d4f9c") + oval(340, 433, 24, 5, "#a468b3"); },
    "bed-basket": function () { return path("M294 424 Q340 446 386 424 L378 446 Q340 462 302 446 Z", "#a8743f") + oval(340, 424, 46, 11, "#c79256") + oval(340, 425, 38, 8, "#f6dcc0") + path("M304 436 Q340 452 376 436 M300 430 Q340 448 380 430", "none", 'stroke="#8a5a2a" stroke-width="1.5"'); },
    "bed-cloud": function () { return oval(340, 440, 50, 12, "#cfd8f7") + circle(312, 432, 14, "#eef2ff") + circle(334, 424, 18, "#fff") + circle(358, 428, 16, "#eef2ff") + circle(374, 436, 10, "#fff") + oval(340, 440, 42, 9, "#fff"); },
    "bed-moon": function () { return oval(340, 448, 30, 5, "#00000055") + path("M300 400 Q300 448 344 448 Q380 448 384 414 Q372 436 346 434 Q314 432 300 400 Z", "#ffdc83") + path(star(372, 398, 7), "#fff1cd") + path("M322 448 L340 458 L358 448", "none", 'stroke="#d9b24a" stroke-width="3" stroke-linecap="round"'); },
    "bed-castle": function () { return path("M298 448 V398 h12 v8 h12 v-8 h12 v8 h12 v-8 h12 v8 h12 v-8 h12 V448 Z", "#c79256") + path("M328 448 V428 a12 12 0 0 1 24 0 V448 Z", "#3a2147") + rect(306, 414, 9, 10, "#3a2147") + rect(365, 414, 9, 10, "#3a2147") + path("M298 420 H382", "none", 'stroke="#a8743f" stroke-width="1.2" stroke-dasharray="6 5"') + path("M340 398 V380 l14 5 l-14 5", "#ff8ecb", 'stroke="#8a5a2a" stroke-width="1.5"'); },
  };

  // ---- The whole room. It is drawn 1000 wide and 500 tall and fills whatever it is given (the whole screen on a TV
  // or computer, a card on a phone), cropping its sides rather than squashing. The furniture above is drawn in a
  // 400-wide block in the middle; the wall and floor repeat across the full width; and there are three windows, the
  // middle one and one out to each side.
  const WIDE = 1000, MIDDLE = 300, TILES = [-100, 300, 700], WINDOWS = [-50, MIDDLE, 650]; // Where the 400-wide pieces sit.
  const REPEATED = { wallpaper: TILES, floor: TILES, view: WINDOWS, frame: WINDOWS, curtains: WINDOWS };

  function render(house, backdrop) { // The room as SVG markup, back to front. `house` is {spot: object id} from the server.
    // `backdrop`: the page has its own scrolling scenery behind it (the neon theme's drifting pixel cities). The
    // windows are real holes in the wall, so with the first, free view that scenery is what shows through them.
    // Any other view, or a theme with nothing behind the page, paints a view of ours into the holes instead.
    const frame = Object.prototype.hasOwnProperty.call(CLIPS, house.frame) ? house.frame : "frame-arch", opening = CLIPS[frame];
    const open = Boolean(backdrop) && house.view === "view-city";
    const defs = '<defs><clipPath id="lp-view-clip"><path d="' + opening + '"/></clipPath>' +
      '<mask id="lp-wall-mask" maskUnits="userSpaceOnUse" x="-100" y="0" width="' + (WIDE + 200) + '" height="500"><rect x="-100" width="' + (WIDE + 200) + '" height="500" fill="#fff"/>' +
      WINDOWS.map(function (x) { return '<path d="' + opening + '" transform="translate(' + x + ' 0)" fill="#000"/>'; }).join("") + "</mask></defs>";
    return defs + LAYERS.map(function (spot) {
      const draw = Object.prototype.hasOwnProperty.call(ART, house[spot]) ? ART[house[spot]] : null;
      let art = draw && !(spot === "view" && open) ? draw() : ""; // An object this file doesn't know simply isn't drawn.
      if (art && spot === "view") art = group(art, 'clip-path="url(#lp-view-clip)"');
      const places = REPEATED[spot] || [MIDDLE];
      return group(art ? places.map(function (x) { return group(art, 'transform="translate(' + x + ' 0)"'); }).join("") : "",
        'data-spot="' + spot + '"' + (spot === "wallpaper" ? ' mask="url(#lp-wall-mask)"' : "") + (spot === "view" && open ? ' data-open="true"' : ""));
    }).join("");
  }

  return { LAYERS: LAYERS, ART: ART, CLIPS: CLIPS, WINDOWS: WINDOWS, WIDE: WIDE, render: render };
});
