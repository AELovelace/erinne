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

  const LAYERS = ["wallpaper", "floor", "door", "wallart", "trophy", "shelf", "view", "frame", "curtains", "ceiling", "lamp", "sidetable", "plant", "rug", "toychest", "bed"];
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
    "frame-lattice": "M200 96 L260 161 L200 226 L140 161 Z",
    "frame-heart": "M200 226 C160 196 140 168 140 134 C140 108 158 96 176 96 C188 96 197 103 200 114 C203 103 212 96 224 96 C242 96 260 108 260 134 C260 168 240 196 200 226 Z",
    "frame-cloud": "M156 214 C142 214 140 198 148 190 C138 180 140 162 154 158 C150 138 170 126 184 134 C188 112 222 108 230 132 C246 126 262 142 254 158 C264 168 260 190 248 192 C256 204 248 216 236 214 Z",
    "frame-cottage": "M148 106 H252 V226 H148 Z",
    "frame-star": "M200 96 L218 138 L260 146 L228 174 L238 220 L200 196 L162 220 L172 174 L140 146 L182 138 Z",
    "frame-keyhole": "M180 226 L186 170 A36 36 0 1 1 214 170 L220 226 Z",
    "frame-lancet": "M144 226 V144 Q144 120 160 108 Q176 120 176 144 V226 H184 V132 Q184 110 200 96 Q216 110 216 132 V226 H224 V144 Q224 120 240 108 Q256 120 256 144 V226 Z",
    "frame-rose": "M140 161 A60 65 0 1 0 260 161 A60 65 0 1 0 140 161 Z",
    "frame-geode": "M198 96 L226 104 L240 98 L256 124 L250 146 L260 172 L246 196 L250 216 L222 222 L204 226 L180 220 L156 224 L146 200 L150 178 L140 156 L150 134 L144 112 L170 108 Z",
    "frame-orrery": "M140 161 A60 60 0 1 0 260 161 A60 60 0 1 0 140 161 Z",
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

    // Doors: on the wall to the left, between the side window and the wall art. They stand on the floor line (y = 352).
    "door-plum": function () { return rect(-62, 196, 74, 158, "#3d2450", 'rx="3"') + rect(-56, 202, 62, 152, "#5a3570") + rect(-48, 212, 46, 56, "#6d4585", 'rx="2"') + rect(-48, 280, 46, 62, "#6d4585", 'rx="2"') + circle(-2, 278, 4, "#ffdc83"); },
    "door-round": function () { return path("M-62 354 V232 A37 37 0 0 1 12 232 V354 Z", "#3a5a52") + path("M-56 354 V232 A31 31 0 0 1 6 232 V354 Z", "#4f7a70") + circle(-25, 236, 15, "#bfe8ff", 'stroke="#3a5a52" stroke-width="3"') + path("M-25 221 V251 M-40 236 H-10", "none", 'stroke="#3a5a52" stroke-width="2"') + circle(-4, 290, 4, "#ffdc83"); },
    "door-catflap": function () { return rect(-62, 196, 74, 158, "#7a4a2a", 'rx="3"') + rect(-56, 202, 62, 152, "#a8743f") + rect(-48, 212, 46, 70, "#b98550", 'rx="2"') + circle(-2, 284, 4, "#ffdc83") + rect(-42, 306, 34, 40, "#54391f", 'rx="5"') + rect(-39, 309, 28, 34, "#2a1a3e", 'rx="4"') + path("M-34 322 l5 -8 l4 6 l4 -6 l5 8 q0 10 -9 10 q-9 0 -9 -10Z", "#fce1f2", 'opacity=".9"'); },
    "door-beads": function () {
      let out = rect(-62, 196, 74, 158, "#3d2450", 'rx="3"') + rect(-56, 202, 62, 152, "#120f2a");
      for (let x = -52, i = 0; x <= 2; x += 9, i++) for (let y = 208; y < 344 - (i % 3) * 14; y += 9) out += circle(x, y, 2.6, ["#ff8ecb", "#70ebea", "#ffdc83", "#c9a8ff"][(i + Math.round(y / 9)) % 4], 'opacity=".9"');
      return out;
    },
    "door-portal": function () { return oval(-25, 276, 44, 84, GLOW, 'opacity=".12"') + oval(-25, 276, 36, 76, "#120f2a", 'stroke="' + GLOW + '" stroke-width="4" class="lp-neon"') + oval(-25, 276, 24, 56, "none", 'stroke="' + GLOW + '" stroke-width="2" opacity=".5"') + oval(-25, 276, 12, 32, "none", 'stroke="' + GLOW + '" stroke-width="1.5" opacity=".35"') +
      [[-36, 236, 5], [-12, 262, 4], [-32, 296, 6], [-16, 322, 4]].map(function (s, i) { return path(star(s[0], s[1], s[2]), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .5) + 's"'); }).join(""); },

    // Ceiling hangings: from the top of the wall, above the shelf.
    "hang-stars": function () { return path("M306 0 V46 M330 0 V70 M354 0 V52 M306 46 H354", "none", 'stroke="#8a7aa8" stroke-width="1.2"') + path(star(306, 56, 9), "#ffdc83") + path(star(330, 82, 12), "#fff1cd") + path(star(354, 62, 8), "#ff8ecb"); },
    "hang-cranes": function () { return [[304, 50, "#ff8ecb"], [330, 78, "#70ebea"], [356, 44, "#ffdc83"]].map(function (c) {
      return line(c[0], 0, c[0], c[1] - 8, "#8a7aa8", 1) + path("M" + (c[0] - 14) + " " + c[1] + " L" + c[0] + " " + (c[1] - 9) + " L" + (c[0] + 16) + " " + (c[1] - 4) + " L" + (c[0] + 2) + " " + (c[1] + 2) + " L" + (c[0] + 6) + " " + (c[1] + 10) + " L" + (c[0] - 4) + " " + (c[1] + 3) + " Z", c[2]) + path("M" + c[0] + " " + (c[1] - 9) + " L" + (c[0] + 2) + " " + (c[1] + 2), "none", 'stroke="#00000033" stroke-width="1"');
    }).join(""); },
    "hang-planets": function () { return line(304, 0, 304, 40, "#8a7aa8", 1) + line(332, 0, 332, 66, "#8a7aa8", 1) + line(358, 0, 358, 36, "#8a7aa8", 1) + circle(304, 50, 10, "#ff9d6b") + oval(304, 50, 17, 4, "none", 'stroke="#ffe0b0" stroke-width="2" transform="rotate(-18 304 50)"') + circle(332, 80, 14, "#70b8eb") + path("M322 76 q8 -6 14 0 q-4 6 -10 4 Z", "#7fc49a") + circle(358, 44, 8, "#c9a8ff") + circle(355, 41, 2, "#fff", 'opacity=".6"'); },
    "hang-basket": function () { return path("M316 0 L310 58 M344 0 L350 58 M330 0 V56", "none", 'stroke="#c79256" stroke-width="1.5"') + path("M306 58 H354 L346 84 H314 Z", "#a8743f") + oval(330, 58, 24, 6, "#5fae86") + path("M312 58 Q300 84 306 104 M322 60 Q318 90 326 110 M340 60 Q346 86 338 102 M350 58 Q362 78 356 96", "none", 'stroke="#7fc49a" stroke-width="3" stroke-linecap="round"') + [[306, 104], [326, 110], [338, 102], [356, 96]].map(function (l) { return oval(l[0], l[1], 4, 6, "#9bd6ae"); }).join(""); },
    "hang-disco": function () { return line(330, 0, 330, 44, "#8a7aa8", 1.5) + circle(330, 66, 34, "#fff", 'opacity=".07"') + circle(330, 66, 22, "#c8d2e8") + path("M308 66 H352 M311 56 H349 M311 76 H349 M318 48 H342 M318 84 H342 M330 44 V88 M320 46 V86 M340 46 V86", "none", 'stroke="#7a86a8" stroke-width="1"') + [[320, 58, "#ff8ecb"], [338, 62, "#70ebea"], [326, 74, "#ffdc83"], [340, 78, "#c9a8ff"]].map(function (t, i) { return rect(t[0] - 3, t[1] - 3, 6, 6, t[2], 'class="lp-twinkle" style="animation-delay:' + (i * .45) + 's"'); }).join(""); },

    // Side tables: against the wall under the shelf.
    "table-tea": function () { return ART.table("#7a5a8c") + path("M322 312 h18 q0 12 -9 12 q-9 0 -9 -12 Z", "#fff1e0") + path("M340 314 q7 0 6 5 q-1 4 -6 3", "none", 'stroke="#fff1e0" stroke-width="2"') + path("M327 308 q-3 -5 0 -9 M335 308 q3 -5 0 -9", "none", 'stroke="#fff" stroke-width="1.2" opacity=".5" stroke-linecap="round"') + oval(331, 324, 13, 3, "#e8dff0"); },
    "table-books": function () { return [["#c85a8e", 350, 40], ["#70ebea", 342, 36], ["#ffdc83", 334, 42], ["#9c73b8", 326, 34], ["#7fc49a", 318, 38]].map(function (b, i) { return rect(335 - b[2] / 2 + (i % 2 ? 3 : -2), b[1], b[2], 8, b[0], 'rx="1.5"'); }).join("") + rect(316, 358, 38, 4, "#54391f") + path(star(335, 308, 6), "#ffe9a8"); },
    "table-candle": function () { return ART.table("#5a4a70") + rect(328, 300, 8, 24, "#fff1e0", 'rx="2"') + oval(332, 286, 16, 18, "#ffb84a", 'opacity=".16"') + path("M332 282 q6 8 0 16 q-6 -8 0 -16 Z", "#ffb84a", 'class="lp-twinkle"') + oval(332, 324, 10, 3, "#d9b24a"); },
    "table-radio": function () { return ART.table("#7a5a8c") + rect(314, 300, 42, 24, "#e8556a", 'rx="5"') + circle(326, 312, 7, "#3a2147") + circle(326, 312, 3, "#8a7aa8") + rect(338, 306, 13, 3, "#fff1e0") + rect(338, 312, 13, 3, "#fff1e0") + rect(338, 318, 8, 3, "#fff1e0") + line(350, 300, 360, 280, "#8a7aa8", 1.5) + circle(360, 280, 2, "#ffdc83"); },
    "table-fishbowl": function () { return ART.table("#5a4a70") + circle(335, 304, 20, "#70b8eb", 'opacity=".35" stroke="#cfe8ff" stroke-width="2"') + path("M317 300 Q335 294 353 300 A20 20 0 0 1 317 300 Z", "#70b8eb", 'opacity=".55"') + path("M330 306 q8 -6 12 0 q-4 6 -12 0 Z M330 306 l-6 -4 v8 Z", "#ff9d6b") + circle(340, 305, 1, "#2a1a3e") + circle(345, 296, 1.6, "#fff", 'opacity=".7"') + circle(348, 290, 1, "#fff", 'opacity=".7"') + rect(323, 284, 24, 3, "#cfe8ff", 'rx="1.5"'); },
    table: function (colour) { return rect(308, 324, 56, 6, colour, 'rx="2"') + rect(314, 330, 4, 32, colour) + rect(354, 330, 4, 32, colour) + rect(316, 344, 40, 3, colour, 'opacity=".7"'); },

    // Plants: on the floor at the left. render() moves them right (NUDGE), to stand between the lamp and Lumi.
    "plant-fern": function () { return [-58, -36, -16, 0, 16, 36, 58].map(function (a) { return oval(18, 348, 5, 26, a % 32 ? "#5fae86" : "#7fc49a", 'transform="rotate(' + a + ' 18 372)"'); }).join("") + ART.pot("#c0764f"); },
    "plant-cactus": function () { return rect(11, 322, 14, 52, "#5fae86", 'rx="7"') + path("M11 350 h-8 q-5 0 -5 -6 v-8 M25 342 h8 q5 0 5 -6 v-6", "none", 'stroke="#5fae86" stroke-width="8" stroke-linecap="round"') + path("M18 328 v40 M14 334 v4 M22 344 v4 M14 356 v4", "none", 'stroke="#3f8a66" stroke-width="1.2"') + circle(18, 320, 4, "#ff8ecb") + ART.pot("#e8dff0"); },
    "plant-sunflower": function () { return path("M18 374 V316", "none", 'stroke="#5fae86" stroke-width="4"') + oval(8, 350, 9, 4, "#7fc49a", 'transform="rotate(-25 8 350)"') + oval(28, 340, 9, 4, "#7fc49a", 'transform="rotate(25 28 340)"') + [0, 45, 90, 135, 180, 225, 270, 315].map(function (a) { return oval(18, 290, 5, 11, "#ffdc83", 'transform="rotate(' + a + ' 18 304)"'); }).join("") + circle(18, 304, 9, "#8a5a2a") + ART.pot("#70b8eb"); },
    "plant-bonsai": function () { return path("M18 376 Q10 356 22 346 Q32 338 26 326", "none", 'stroke="#8a5a2a" stroke-width="6" stroke-linecap="round"') + path("M22 346 Q4 344 -2 334", "none", 'stroke="#8a5a2a" stroke-width="4" stroke-linecap="round"') + oval(28, 320, 20, 10, "#3f8a66") + oval(-2, 330, 14, 8, "#5fae86") + oval(20, 312, 12, 7, "#7fc49a") + rect(0, 376, 36, 10, "#3a5a52", 'rx="2"') + rect(4, 386, 5, 4, "#3a5a52") + rect(27, 386, 5, 4, "#3a5a52"); },
    "plant-glowshroom": function () { return oval(18, 372, 34, 22, GLOW, 'opacity=".12"') + [[6, 366, 9, 20], [20, 352, 13, 34], [32, 370, 7, 14]].map(function (m, i) {
      return rect(m[0] - 2.5, m[1], 5, m[3], "#fff1e0", 'rx="2"') + path("M" + (m[0] - m[2]) + " " + m[1] + " Q" + m[0] + " " + (m[1] - m[2] * 1.5) + " " + (m[0] + m[2]) + " " + m[1] + " Z", GLOW, 'class="lp-neon lp-twinkle" style="animation-delay:' + (i * .6) + 's"');
    }).join("") + oval(18, 388, 22, 5, "#3a5a52"); },
    pot: function (colour) { return path("M4 372 H32 L28 392 H8 Z", colour) + rect(2, 368, 32, 6, colour, 'rx="2"'); },

    // Toy chests: at the front of the floor on the left.
    "chest-wood": function () { return rect(58, 440, 68, 34, "#a8743f", 'rx="3"') + path("M58 440 Q92 418 126 440 Z", "#c79256") + rect(58, 438, 68, 5, "#7a4a2a") + rect(87, 440, 10, 12, "#ffdc83", 'rx="2"') + path("M72 446 V472 M112 446 V472", "none", 'stroke="#7a4a2a" stroke-width="3"'); },
    "chest-pink": function () { return rect(58, 438, 68, 36, "#ff8ecb", 'rx="5"') + rect(58, 438, 68, 8, "#ffb3d1", 'rx="4"') + path(star(92, 460, 9), "#fff1cd") + circle(72, 432, 9, "#70ebea") + path("M100 438 l6 -16 l8 4 l-4 12 Z", "#ffdc83") + rect(82, 426, 10, 12, "#c9a8ff", 'rx="2"'); },
    "chest-yarn": function () { return path("M56 442 Q92 456 128 442 L120 474 H64 Z", "#c79256") + oval(92, 442, 36, 9, "#a8743f") + path("M62 452 Q92 464 122 452 M64 462 Q92 472 120 462", "none", 'stroke="#8a5a2a" stroke-width="1.5"') + [[76, 434, 11, "#ff8ecb"], [98, 430, 13, "#70ebea"], [114, 438, 9, "#ffdc83"]].map(function (y) { return circle(y[0], y[1], y[2], y[3]) + path("M" + (y[0] - y[2] * .7) + " " + (y[1] - 2) + " Q" + y[0] + " " + (y[1] - y[2] * .8) + " " + (y[0] + y[2] * .7) + " " + (y[1] + 2) + " M" + (y[0] - y[2] * .5) + " " + (y[1] + 4) + " Q" + y[0] + " " + (y[1] - 2) + " " + (y[0] + y[2] * .6) + " " + (y[1] + 5), "none", 'stroke="#00000030" stroke-width="1.2"'); }).join(""); },
    "chest-rocket": function () { return rect(58, 444, 68, 30, "#3d4675", 'rx="4"') + scatter(12, 7, function (roll) { return path(star(Math.round(roll(64, 120)), Math.round(roll(450, 468)), 3), "#ffe9a8"); }) + path("M92 400 Q104 414 102 444 H82 Q80 414 92 400 Z", "#e8dff0") + path("M92 400 Q98 407 100 416 H84 Q86 407 92 400 Z", "#e8556a") + circle(92, 426, 5, "#70b8eb") + path("M82 434 L72 446 H82 Z M102 434 L112 446 H102 Z", "#e8556a"); },
    "chest-treasure": function () { return rect(58, 444, 68, 30, "#7a4a2a", 'rx="3"') + path("M58 444 Q60 414 92 414 Q124 414 126 444 Z", "#8a5a2a") + path("M60 444 Q92 428 124 444 Z", "#ffdc83") + [[72, 440], [84, 436], [98, 437], [110, 440], [91, 442]].map(function (c) { return oval(c[0], c[1], 6, 3, "#ffe9a8", 'stroke="#d9b24a" stroke-width="1"'); }).join("") + path("M58 444 H126 M72 444 V474 M112 444 V474", "none", 'stroke="#ffdc83" stroke-width="3"') + rect(87, 448, 10, 12, "#ffdc83", 'rx="2"') + path(star(104, 428, 5), "#fff", 'class="lp-twinkle"'); },

    // Trophies: found in the dungeon (mnemerynne/lumidungeon.py), shown on a little bracket above the wall art.
    "trophy-none": function () { return ART.bracket(); },
    "trophy-moon-bell": function () { return ART.bracket() + path("M88 132 Q88 108 100 106 Q112 108 112 132 Z", "#f0deff") + circle(100, 133, 3, "#d9b24a") + path("M96 116 a6 6 0 1 0 8 6 a5 5 0 1 1 -8 -6", "#b9a2ff"); },
    "trophy-lantern-charm": function () { return ART.bracket() + line(100, 102, 100, 110, "#8a7aa8", 1.5) + oval(100, 121, 9, 11, "#e8556a") + path("M93 117 H107 M92 122 H108 M93 127 H107", "none", 'stroke="#b83a4e" stroke-width="1"') + rect(96, 108, 8, 3, "#5a3a2a") + rect(96, 131, 8, 3, "#5a3a2a"); },
    "trophy-singing-crystal": function () { return ART.bracket() + path("M92 134 L95 114 L100 104 L106 116 L108 134 Z", "#9be8ff", 'opacity=".9" class="lp-neon"') + path("M100 104 L101 134", "none", 'stroke="#fff" stroke-opacity=".6" stroke-width="1"'); },
    "trophy-geode-egg": function () { return ART.bracket() + oval(100, 121, 11, 13, "#6a648a") + path("M100 108 L104 116 L99 122 L104 128 L100 134 Q112 132 111 121 Q112 110 100 108 Z", "#c9a8ff") + path(star(106, 121, 3), "#fff"); },
    "trophy-golden-toadstool": function () { return ART.bracket() + rect(97, 120, 6, 14, "#fff1cd") + path("M86 121 Q100 100 114 121 Q100 126 86 121 Z", "#ffdc83") + circle(95, 115, 2, "#fff8dc") + circle(104, 112, 1.6, "#fff8dc"); },
    "trophy-spore-jar": function () { return ART.bracket() + rect(92, 112, 16, 22, "#bfe8ff", 'rx="4" opacity=".45" stroke="#cfe8ff" stroke-width="1.5"') + rect(94, 108, 12, 5, "#a8743f", 'rx="1"') + circle(97, 124, 2, "#8be9a8", 'class="lp-twinkle"') + circle(103, 119, 1.6, "#8be9a8") + circle(101, 129, 1.4, "#8be9a8"); },
    "trophy-whispering-book": function () { return ART.bracket() + path("M86 130 L100 126 L114 130 V112 L100 108 L86 112 Z", "#9c73b8") + path("M100 108 V126", "none", 'stroke="#3d2450" stroke-width="1.5"') + path("M90 116 L97 114 M90 121 L97 119 M103 114 L110 116 M103 119 L110 121", "none", 'stroke="#f0deff" stroke-width="1"') + rect(86, 130, 28, 4, "#6d4585"); },
    "trophy-bottled-cloud": function () { return ART.bracket() + path("M94 134 V116 Q94 112 97 110 V104 H103 V110 Q106 112 106 116 V134 Z", "#bfe8ff", 'opacity=".4" stroke="#cfe8ff" stroke-width="1.5"') + oval(100, 124, 5, 4, "#fff") + circle(97, 122, 3, "#fff") + circle(103, 122, 3.4, "#fff") + rect(97, 101, 6, 4, "#a8743f"); },
    "trophy-pearl-idol": function () { return ART.bracket() + path("M92 134 Q90 118 96 112 H104 Q110 118 108 134 Z", "#7fd6c8") + circle(100, 108, 6, "#7fd6c8") + circle(100, 122, 4, "#fff8f0", 'class="lp-neon"'); },
    "trophy-coral-crown": function () { return ART.bracket() + path("M88 134 V124 L92 112 L95 124 L100 108 L105 124 L108 112 L112 124 V134 Z", "#ff8e8e") + circle(92, 111, 2, "#ffb3d1") + circle(100, 107, 2, "#ffb3d1") + circle(108, 111, 2, "#ffb3d1"); },
    "trophy-brass-cog": function () { return ART.bracket() + circle(100, 120, 11, "none", 'stroke="#d9b24a" stroke-width="5" stroke-dasharray="5 3.6"') + circle(100, 120, 8, "#d9b24a") + circle(100, 120, 3, "#4a3524"); },
    "trophy-cuckoo-clock": function () { return ART.bracket() + path("M88 134 V116 L100 104 L112 116 V134 Z", "#a8743f") + circle(100, 121, 6, "#fff1cd") + path("M100 121 V117 M100 121 H103", "none", 'stroke="#4a3524" stroke-width="1.2"') + path("M86 117 L100 103 L114 117", "none", 'stroke="#7a4a2a" stroke-width="3" stroke-linejoin="round"'); },
    "trophy-lollipop": function () { return ART.bracket() + line(100, 120, 100, 134, "#fff1e0", 2.5) + circle(100, 114, 10, "#ffb3d1") + path("M100 114 m0 -7 a7 7 0 1 1 -7 7 a5 5 0 1 0 5 -5 a3 3 0 1 1 3 3", "none", 'stroke="#e8556a" stroke-width="2"'); },
    "trophy-gummy-skull": function () { return ART.bracket() + path("M89 120 Q89 106 100 106 Q111 106 111 120 Q111 127 106 128 V134 H94 V128 Q89 127 89 120 Z", "#9bd6ae", 'opacity=".9"') + circle(95, 119, 3, "#23402f") + circle(105, 119, 3, "#23402f") + path("M97 130 V134 M100 130 V134 M103 130 V134", "none", 'stroke="#23402f" stroke-width="1"'); },
    "trophy-windup-mouse": function () { return ART.bracket() + oval(100, 127, 12, 7, "#a8b4c4") + circle(91, 121, 4, "#a8b4c4") + circle(91, 121, 2, "#ffb3d1") + circle(89, 128, 1.2, "#2a1a3a") + path("M112 128 q8 0 6 -8", "none", 'stroke="#a8b4c4" stroke-width="1.5"') + path("M102 120 V112 M98 110 a4 3 0 1 0 8 0 a4 3 0 1 0 -8 0", "none", 'stroke="#d9b24a" stroke-width="1.8"'); },
    "trophy-ghost-teddy": function () { return ART.bracket() + circle(93, 109, 4, "#f0deff", 'opacity=".7"') + circle(107, 109, 4, "#f0deff", 'opacity=".7"') + circle(100, 114, 8, "#f0deff", 'opacity=".8"') + oval(100, 127, 9, 8, "#f0deff", 'opacity=".7"') + circle(97, 113, 1.2, "#3d2450") + circle(103, 113, 1.2, "#3d2450"); },
    "trophy-star-map": function () { return ART.bracket() + path("M88 134 L92 108 L110 112 L113 134 Z", "#0d1230", 'stroke="#6f7fc0" stroke-width="1.5"') + path("M95 116 L101 121 L107 117 L104 128", "none", 'stroke="#8fa2d8" stroke-width=".8"') + [[95, 116], [101, 121], [107, 117], [104, 128]].map(function (s) { return circle(s[0], s[1], 1.4, "#fff"); }).join(""); },
    "trophy-robot-head": function () { return ART.bracket() + rect(90, 114, 20, 20, "#a8b4c4", 'rx="4"') + circle(96, 123, 3, "#70ebea", 'class="lp-twinkle"') + circle(104, 123, 3, "#70ebea") + line(100, 114, 100, 106, "#a8b4c4", 2) + circle(100, 105, 2.4, "#ff7a8a") + rect(95, 129, 10, 2, "#6a7484"); },
    "trophy-score-token": function () { return ART.bracket() + circle(100, 120, 12, "#ffdc83", 'stroke="#d9b24a" stroke-width="2"') + path(star(100, 120, 7), "#fff8dc"); },
    "trophy-neon-scrap": function () { return ART.bracket() + path("M88 130 L94 112 L100 126 L106 108 L112 124", "none", 'stroke="#ff8ecb" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" class="lp-neon"'); },
    "trophy-data-cube": function () { return ART.bracket() + path("M100 106 L112 113 V127 L100 134 L88 127 V113 Z", "#70ebea", 'opacity=".35" class="lp-neon"') + path("M100 106 L112 113 L100 120 L88 113 Z M100 120 V134", "none", 'stroke="#70ebea" stroke-width="1.5"') + circle(100, 120, 2, "#fff", 'class="lp-twinkle"'); },
    "trophy-floppy": function () { return ART.bracket() + path("M89 110 H108 L112 114 V134 H89 Z", "#3d4675") + rect(93, 110, 11, 8, "#a8b4c4") + rect(100, 111, 3, 5, "#3d4675") + rect(93, 123, 15, 9, "#f0deff"); },
    "trophy-honey-jar": function () { return ART.bracket() + path("M91 134 Q88 122 92 114 H108 Q112 122 109 134 Z", "#ffb84a") + rect(91, 110, 18, 5, "#fff1cd", 'rx="1.5"') + path("M96 119 q4 5 8 0", "none", 'stroke="#fff1cd" stroke-width="1.5" opacity=".7"') + oval(100, 106, 4, 2.5, "#ffdc83"); },
    "trophy-snow-globe": function () { return ART.bracket() + circle(100, 117, 12, "#bfe8ff", 'opacity=".4" stroke="#cfe8ff" stroke-width="1.5"') + path("M92 124 V118 H95 V114 H99 V120 H103 V111 H107 V124 Z", "#3d4675") + circle(95, 110, 1, "#fff") + circle(104, 107, 1, "#fff") + circle(108, 116, 1, "#fff") + rect(90, 128, 20, 6, "#8a5a2a", 'rx="2"'); },
    bracket: function () { return rect(82, 134, 36, 4, "#7a5a8c", 'rx="2"') + path("M88 138 l4 7 M112 138 l-4 7", "none", 'stroke="#7a5a8c" stroke-width="2.5" stroke-linecap="round"'); },

    // ---- Star Capsule prizes: won from the capsules, never sold (the "rarity" entries in the catalog).
    // Star Capsule bed
    "bed-teacup": function () { // A giant teacup on its saucer, with a cushion where the tea would be.
      return oval(340, 456, 54, 8, "#e8dff0") + oval(340, 454, 44, 5, "#fff8f0") +
        path("M376 416 q22 -4 20 14 q-2 14 -26 14", "none", 'stroke="#fff8f0" stroke-width="6" stroke-linecap="round"') +
        path("M296 410 Q298 452 340 454 Q382 452 384 410 Z", "#fff8f0") + path("M297 422 Q340 436 383 422 L382 428 Q340 442 298 428 Z", "#ff8ecb") +
        [316, 340, 364].map(function (x, i) { return path(star(x, 445 - (i === 1 ? -2 : 0), 3.5), "#d9b24a"); }).join("") +
        oval(340, 410, 44, 10, "#d9b24a") + oval(340, 410, 40, 8, "#ffb3d1") + oval(340, 409, 26, 4.5, "#ffd9e6");
    },
    "bed-slipper": function () { // One huge fluffy slipper, toe to the left, with a pompom on top. Lumi naps in the heel.
      const fluff = [[352, 412], [358, 420], [361, 429], [362, 438], [346, 407], [339, 405]];
      return oval(340, 458, 54, 5, "#00000044") + path("M290 450 Q288 458 300 458 H378 Q394 458 392 444 Q390 434 376 436 H304 Q292 438 290 450 Z", "#7a4a2a") +
        path("M340 444 Q352 426 376 428 Q392 432 390 448 Z", "#f6dcc0") + oval(372, 440, 13, 7, "#ffb3d1") +
        path("M290 450 Q286 410 318 406 Q350 404 362 446 Z", "#b9a2ff") + path("M296 446 Q294 420 318 416", "none", 'stroke="#d8c9ff" stroke-width="4" stroke-linecap="round"') +
        fluff.map(function (p) { return circle(p[0], p[1], 7, "#e8dff0"); }).join("") +
        circle(312, 402, 11, "#fff") + circle(304, 408, 7, "#f0e6ff") + circle(321, 407, 7, "#f0e6ff") + circle(312, 400, 5, "#ffd9e6");
    },
    "bed-nest": function () { // A twiggy bird's nest with a soft lining and one speckled egg for a pillow.
      return oval(340, 448, 54, 14, "#4a3218") + oval(340, 436, 50, 18, "#6b4a2f") + oval(340, 430, 38, 10, "#f6dcc0") + oval(340, 432, 28, 6, "#ffe9c9") +
        scatter(17, 26, function (roll) {
          const a = roll(0, Math.PI * 2), x = round(340 + Math.cos(a) * roll(40, 52)), y = round(440 + Math.sin(a) * roll(6, 18)), t = roll(-16, 16);
          return line(x, y, round(x + t), round(y + t * .25 + roll(-3, 3)), ["#8a5a2a", "#a8743f", "#54391f"][Math.floor(roll(0, 3))], 2, 'stroke-linecap="round"');
        }) +
        oval(362, 424, 9, 12, "#bfe8ff", 'transform="rotate(14 362 424)"') + circle(359, 421, 1.3, "#6f7fc0") + circle(365, 427, 1, "#6f7fc0") + circle(363, 418, 1, "#6f7fc0") +
        path("M300 424 q10 -14 22 -12 q-4 12 -22 12 Z", "#ffb3d1") + path("M302 423 L320 413", "none", 'stroke="#e8556a" stroke-width="1"');
    },
    "bed-donut": function () { // A big iced donut: the hole is the dip Lumi curls up in.
      return oval(340, 450, 54, 8, "#00000044") + oval(340, 438, 54, 20, "#d9a25f") +
        path("M288 432 Q288 414 340 412 Q392 414 392 432 Q394 442 378 442 Q374 450 362 444 Q350 454 338 446 Q326 452 316 444 Q306 450 298 442 Q286 442 288 432 Z", "#ff8ecb") +
        oval(340, 430, 20, 7, "#a8743f") + oval(340, 431, 16, 5, "#ffd9e6") +
        scatter(23, 30, function (roll) {
          const a = roll(0, Math.PI * 2), x = round(340 + Math.cos(a) * roll(26, 46)), y = round(430 + Math.sin(a) * roll(9, 13)), t = roll(-4, 4);
          return line(x, y, round(x + t), round(y + 3 - Math.abs(t) * .5), ["#fff", "#ffdc83", "#70ebea", "#c9a8ff"][Math.floor(roll(0, 4))], 2, 'stroke-linecap="round"');
        });
    },
    "bed-hammock": function () { // A striped hammock between two star-topped posts.
      return oval(340, 460, 58, 6, "#00000044") + rect(292, 392, 6, 66, "#8a5a2a", 'rx="2"') + rect(382, 392, 6, 66, "#8a5a2a", 'rx="2"') +
        path("M284 460 L295 440 L306 460 M374 460 L385 440 L396 460", "none", 'stroke="#6b4a2f" stroke-width="4" stroke-linecap="round"') +
        path(star(295, 386, 9), "#ffdc83") + path(star(385, 386, 9), "#ffdc83") +
        path("M295 400 L306 412 M385 400 L374 412", "none", 'stroke="#fff1e0" stroke-width="2"') +
        path("M304 410 Q340 478 376 410 Q340 432 304 410 Z", "#70b8eb") + path("M304 410 Q340 462 376 410 Q340 446 304 410 Z", "#fff1e0") + path("M304 410 Q340 478 376 410 Q340 470 304 410 Z", "#ff8ecb") + // Three bands that all meet at the ropes.
        path("M304 410 Q340 432 376 410", "none", 'stroke="#3d4675" stroke-width="2"') +
        oval(340, 424, 18, 6, "#ffe9c9") + [[322, 433], [358, 433]].map(function (s) { return path(star(s[0], s[1], 3), "#3d4675"); }).join("");
    },
    "bed-shell": function () { // An open scallop shell with a pearl pillow.
      function at(r, deg) { return [round(340 + Math.sin(deg * Math.PI / 180) * r), round(452 - Math.cos(deg * Math.PI / 180) * r)]; } // A point at radius r and an angle from the hinge.
      let edge = "M340 452 L" + at(58, -66).join(" "), ribs = "";
      for (let a = -66; a < 66; a += 16.5) edge += " Q" + at(70, a + 8.25).join(" ") + " " + at(58, a + 16.5).join(" "); // Each scallop bulges past the rim.
      for (let a = -49.5; a < 60; a += 16.5) ribs += line(340, 452, at(60, a)[0], at(60, a)[1], "#d9775e", 1.6, 'opacity=".75"');
      const fan = path(edge + " Z", "#ffb99c") + path("M340 452 L" + at(40, -60).join(" ") + " Q" + at(50, 0).join(" ") + " " + at(40, 60).join(" ") + " Z", "#ffd0ba", 'opacity=".6"') + ribs;
      return oval(340, 462, 54, 5, "#00000044") + fan +
        path("M288 444 Q296 466 340 466 Q384 466 392 444 Q340 424 288 444 Z", "#ffdccb") +
        path("M300 450 Q340 436 380 450 M312 458 Q340 448 368 458", "none", 'stroke="#ffb09a" stroke-width="1.5"') +
        oval(340, 440, 34, 7, "#fff1e0") + circle(340, 430, 12, "#fff8f0", 'stroke="#e8dff0" stroke-width="1"') + circle(336, 426, 3.5, "#fff") + circle(344, 436, 4, "#ffd9e6", 'opacity=".5"');
    },
    "bed-quilt": function () { // A proper little bed under a patchwork quilt.
      const colours = ["#ff8ecb", "#70b8eb", "#ffdc83", "#7fc49a", "#c9a8ff", "#ff9d6b"];
      let patches = "";
      for (let row = 0; row < 2; row++) for (let col = 0; col < 7; col++) patches += rect(306 + col * 10, 422 + row * 11, 10, 11, colours[(row * 2 + col) % 6]);
      return oval(340, 460, 58, 5, "#00000044") + rect(376, 394, 12, 62, "#8a5a2a", 'rx="4"') + circle(382, 392, 6, "#d9b24a") + rect(292, 418, 10, 38, "#8a5a2a", 'rx="3"') + circle(297, 416, 5, "#d9b24a") +
        rect(300, 442, 78, 10, "#a8743f") + rect(306, 452, 5, 8, "#6b4a2f") + rect(366, 452, 5, 8, "#6b4a2f") +
        oval(362, 418, 15, 8, "#fff8f0") + patches +
        path("M306 422 H376 M306 433 H376 M316 422 V444 M326 422 V444 M336 422 V444 M346 422 V444 M356 422 V444 M366 422 V444", "none", 'stroke="#fff" stroke-width=".8" stroke-dasharray="2 2" opacity=".7"') +
        path("M306 444 H376", "none", 'stroke="#e8dff0" stroke-width="2"');
    },
    "bed-canopy": function () { // A four-poster with velvet curtains, tassels and a heap of pillows.
      return oval(340, 462, 60, 5, "#00000055") +
        [298, 382].map(function (x) { return rect(x - 3, 382, 6, 76, "#d9b24a", 'rx="2"') + circle(x, 378, 5, "#ffdc83"); }).join("") +
        path("M294 380 Q340 354 386 380 V392 Q374 404 362 392 Q351 404 340 392 Q329 404 318 392 Q306 404 294 392 Z", "#7a2f6e") + path("M294 381 Q340 356 386 381", "none", 'stroke="#ffdc83" stroke-width="2.5"') + path(star(340, 367, 6), "#fff1cd") +
        path("M295 392 Q306 412 314 408 Q306 432 298 440 Z", "#a13f8f") + path("M385 392 Q374 412 366 408 Q374 432 382 440 Z", "#a13f8f") + circle(314, 409, 2.6, "#ffdc83") + circle(366, 409, 2.6, "#ffdc83") + line(314, 411, 314, 420, "#ffdc83", 1.5) + line(366, 411, 366, 420, "#ffdc83", 1.5) +
        rect(298, 442, 84, 12, "#6b3f8a", 'rx="3"') + rect(302, 454, 76, 6, "#3d2450") +
        oval(324, 430, 14, 7, "#fff8f0") + oval(356, 430, 14, 7, "#ffd9e6") + oval(340, 427, 12, 6, "#c9a8ff") +
        path("M300 442 Q320 432 340 438 Q360 432 380 442 V450 H300 Z", "#d46fb8") + path("M304 448 H376", "none", 'stroke="#ffdc83" stroke-width="1.5" stroke-dasharray="3 3"') +
        [314, 332, 350, 368].map(function (x, i) { return path(star(x, 444, 2.6), "#fff1cd", 'class="lp-twinkle" style="animation-delay:' + (i * .5) + 's"'); }).join("");
    },
    "bed-lotus": function () { // A giant water lily on its pad, petals in three rings around a glowing heart.
      function petal(angle, length, width, fill) { return path("M0 0 Q" + (-width) + " " + (-length * .55) + " 0 " + (-length) + " Q" + width + " " + (-length * .55) + " 0 0 Z", fill, 'transform="rotate(' + angle + ')"'); }
      let back = "", mid = "", front = "";
      [-84, -60, -36, -12, 12, 36, 60, 84].forEach(function (a) { back += petal(a, 56, 18, "#d46fb8"); });
      [-70, -42, -14, 14, 42, 70].forEach(function (a) { mid += petal(a, 44, 15, "#ff8ecb"); });
      [-100, -75, -48, 48, 75, 100].forEach(function (a) { front += petal(a, 34, 13, "#ffc4e0"); });
      return oval(340, 460, 62, 7, "#70b8eb", 'opacity=".25"') + oval(340, 460, 48, 4, "none", 'stroke="#bfe8ff" stroke-width="1" opacity=".5"') +
        path("M286 452 Q286 438 340 438 Q394 438 394 452 Q394 462 340 462 Q330 462 340 452 L324 462 Q286 462 286 452 Z", "#3f8a66") + path("M300 452 H380 M340 442 V452", "none", 'stroke="#5fae86" stroke-width="1"') +
        group(back + mid, 'transform="translate(340 448) scale(1.05 .9)"') +
        oval(340, 428, 20, 9, "#ffdc83", 'class="lp-neon"') + oval(340, 427, 13, 5, "#fff1cd") +
        group(front, 'transform="translate(340 448) scale(1.05 .9)"') +
        [[316, 408, 3], [366, 400, 3.5], [388, 428, 2.5], [296, 428, 2.5]].map(function (s, i) { return circle(s[0], s[1], s[2], "#ffe9a8", 'class="lp-twinkle" style="animation-delay:' + (i * .6) + 's"'); }).join("");
    },
    "bed-planet": function () { // The prize of prizes: a hovering half-planet to sleep in, ringed in gold, with its own moons and starlit canopy.
      const cx = 340, cy = 410, r = 42;
      function width(dy) { return round(Math.sqrt(Math.max(0, r * r - dy * dy))); }
      function band(d1, d2, fill) { // A curved stripe across the bowl of the planet.
        const a = width(d1), b = width(d2);
        return path("M" + (cx - a) + " " + (cy + d1) + " Q" + cx + " " + (cy + d1 + 7) + " " + (cx + a) + " " + (cy + d1) + " L" + (cx + b) + " " + (cy + d2) + " Q" + cx + " " + (cy + d2 + 7) + " " + (cx - b) + " " + (cy + d2) + " Z", fill);
      }
      return oval(cx, 464, 40, 6, GLOW, 'opacity=".28"') + oval(cx, 464, 20, 3, GLOW, 'opacity=".5"') +
        path("M" + (cx - 28) + " 450 L" + (cx - 34) + " 462 M" + cx + " 454 V464 M" + (cx + 28) + " 450 L" + (cx + 34) + " 462", "none", 'stroke="' + GLOW + '" stroke-width="2" opacity=".5" stroke-dasharray="2 3"') +
        path("M" + (cx - 40) + " 396 Q" + cx + " 354 " + (cx + 40) + " 396", "none", 'stroke="#ffdc83" stroke-width="1.5" opacity=".7"') +
        [[-32, 383, 3], [-16, 372, 4], [0, 366, 6], [16, 372, 4], [32, 383, 3]].map(function (s, i) { return line(cx + s[0], s[1], cx + s[0], s[1] + 8, "#ffdc83", 1, 'opacity=".5"') + path(star(cx + s[0], s[1] + 12, s[2]), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .35) + 's"'); }).join("") +
        path("M" + (cx - 56) + " 412 A56 15 0 0 1 " + (cx + 56) + " 412", "none", 'stroke="#d9b24a" stroke-width="5"') +
        path("M" + (cx - r) + " " + cy + " A" + r + " " + r + " 0 0 0 " + (cx + r) + " " + cy + " Z", "#2a3a8a") +
        band(4, 12, "#4f6ad0") + band(16, 22, "#c85a8e") + band(26, 31, "#70b8eb") + band(34, 38, "#ffdc83") +
        circle(cx - 16, cy + 18, 3, "#fff", 'opacity=".5"') + circle(cx + 20, cy + 26, 2, "#fff", 'opacity=".5"') +
        oval(cx, cy, r, 11, "#120f2a") + oval(cx, cy - 1, r - 4, 9, "#c9a8ff") + oval(cx, cy - 3, r - 16, 6, "#f0deff") + oval(cx + 18, cy - 5, 11, 5, "#fff8f0") +
        path("M" + (cx - 56) + " 412 A56 15 0 0 0 " + (cx + 56) + " 412", "none", 'stroke="#ffdc83" stroke-width="5" class="lp-neon"') +
        circle(cx - 54, 396, 6, "#c8d2e8") + circle(cx - 56, 394, 2, "#fff", 'opacity=".7"') + circle(cx + 54, 432, 4.5, "#ff9d6b") +
        [[288, 430, 4], [392, 396, 5], [340, 470, 3.5]].map(function (s, i) { return path(star(s[0], s[1], s[2]), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .7 + .2) + 's"'); }).join("");
    },

    // Star Capsule ceiling hanging
    // Hung from the top of the wall above the shelf, roughly x 288..380 and y 0..125.

    "hang-bunting": function () { // Two swags of party flags. Each flag sits on the curve and tilts to follow it.
      const colours = ["#ff8ecb", "#70ebea", "#ffdc83", "#c9a8ff", "#ff9d6b", "#7fc49a"];
      const swag = function (x1, x2, sag, y0, count, shift) {
        let out = path("M" + x1 + " " + y0 + " Q" + (x1 + x2) / 2 + " " + (y0 + sag * 2) + " " + x2 + " " + y0, "none", 'stroke="#8a7aa8" stroke-width="1.2"');
        for (let i = 0; i < count; i++) {
          const t = (i + .5) / count, x = x1 + (x2 - x1) * t, y = y0 + 2 * sag * 2 * t * (1 - t), slope = Math.atan2(2 * sag * 2 * (1 - 2 * t), x2 - x1) * 57.3;  // A point on the curve, and its tilt.
          out += path("M-7 0 H7 L0 17 Z", colours[(i + shift) % 6], 'transform="translate(' + round(x) + " " + round(y) + ") rotate(" + round(slope) + ')"');
        }
        return out;
      };
      return swag(286, 378, 30, 6, 9, 0) + swag(300, 364, 22, 40, 6, 3);
    },

    "hang-raincloud": function () { // A felt rain cloud with a sleepy smile, raining beads of blue on strings.
      const drops = [[306, 94, "#70b8eb"], [320, 112, "#9bd6f5"], [334, 100, "#70b8eb"], [348, 116, "#9bd6f5"], [360, 92, "#70b8eb"]];
      return line(333, 0, 333, 30, "#8a7aa8", 1.2) + drops.map(function (d) { // Each drop on its own thread.
        return line(d[0], 50, d[0], d[1] - 8, "#8a7aa8", 1) + path("M" + d[0] + " " + (d[1] - 9) + " C" + (d[0] - 8) + " " + (d[1] + 2) + " " + (d[0] - 5) + " " + (d[1] + 9) + " " + d[0] + " " + (d[1] + 9) + " C" + (d[0] + 5) + " " + (d[1] + 9) + " " + (d[0] + 8) + " " + (d[1] + 2) + " " + d[0] + " " + (d[1] - 9) + " Z", d[2]);
      }).join("") + oval(333, 46, 44, 15, "#dfe6f7") + circle(310, 40, 15, "#eef2ff") + circle(332, 30, 20, "#ffffff") + circle(356, 38, 16, "#eef2ff") +
        path("M320 42 q4 4 8 0 M339 42 q4 4 8 0", "none", 'stroke="#46507a" stroke-width="2" stroke-linecap="round"') + path("M329 49 q4 4 8 0", "none", 'stroke="#46507a" stroke-width="1.8" stroke-linecap="round"') +
        circle(316, 49, 3, "#ffb3c8", 'opacity=".7"') + circle(350, 49, 3, "#ffb3c8", 'opacity=".7"');
    },

    "hang-windsock": function () { // A fish-shaped streamer swimming sideways on two threads: five colour bands from nose to tail, big eye, scale marks and a forked tail.
      const stripes = ["#ff9d6b", "#fff1e0", "#e8556a", "#fff1e0", "#ffdc83"];
      const half = function (x) { return round(19 * Math.sin((x - 298) / 80 * Math.PI * .86 + .25)); };                                    // How tall the body is at a point.
      let body = "";
      for (let i = 0; i < 5; i++) { // Each band follows the same fish outline.
        let top = "", under = "";
        for (let x = 298 + i * 13; x <= 311 + i * 13; x += 3.25) { top += (top ? " L" : "M") + x + " " + round(54 - half(x)); under = " L" + x + " " + round(54 + half(x)) + under; }
        body += path(top + under + " Z", stripes[i]);
      }
      return path("M312 0 V38 M350 0 V40", "none", 'stroke="#8a7aa8" stroke-width="1.2"') +
        path("M361 54 L386 40 Q378 54 386 68 Z", "#e8556a") + body +                                                                         // The forked tail, then the body.
        path("M323 44 a6 6 0 0 1 0 12 M336 42 a6 6 0 0 1 0 14 M349 45 a5 5 0 0 1 0 11", "none", 'stroke="#ffffff" stroke-width="1.4" opacity=".55"') +          // Scale marks.
        path("M321 68 Q324 76 332 74 Q327 70 330 66 Z", "#ffb86a") + oval(298, 54, 3, 8, "#3a2147") +                                                            // A belly fin and the round open mouth.
        circle(308, 48, 5, "#fff") + circle(307, 48, 2.6, "#1c1430") +
        path("M318 76 q-8 12 0 24 q8 12 0 22 M346 74 q8 12 0 22 q-8 10 -2 20", "none", 'stroke="#70b8eb" stroke-width="2.6" stroke-linecap="round"');                 // Two trailing ribbons.
    },

    "hang-pompoms": function () { // Woolly pom-pom strings: a dashed outer stroke gives each ball its fuzz.
      const strands = [[302, [34, 72]], [318, [20, 58, 96]], [334, [40, 80, 112]], [350, [24, 64]], [366, [38, 84]]], colours = ["#ff8ecb", "#ffdc83", "#70ebea", "#c9a8ff", "#ff9d6b", "#7fc49a"];
      let n = 0;
      return strands.map(function (s) {
        const bottom = s[1][s[1].length - 1];
        return line(s[0], 0, s[0], bottom, "#8a7aa8", 1) + s[1].map(function (y) { const c = colours[n++ % 6]; return circle(s[0], y, 8.5, c) + circle(s[0], y, 9.5, "none", 'stroke="' + c + '" stroke-width="3.2" stroke-dasharray="1.6 .9" opacity=".8"') + circle(s[0] - 3, y - 3, 2, "#ffffff", 'opacity=".5"'); }).join("");
      }).join("");
    },

    "hang-chimes": function () { // Bamboo wind chimes: a wooden top, five tubes on threads, and a paddle that catches the breeze.
      const tubes = [[304, 76], [318, 90], [333, 96], [348, 86], [362, 72]];
      return line(333, 0, 333, 24, "#8a7aa8", 1.2) + oval(333, 30, 38, 7, "#6b4426") + oval(333, 28, 38, 7, "#a8743f") +
        tubes.map(function (t) { // A thread, a tube and the bamboo's joints.
          return line(t[0], 32, t[0], 48, "#8a7aa8", 1) + rect(t[0] - 4, 48, 8, t[1] - 48, "#cdb06a", 'rx="3"') + rect(t[0] - 4, 62, 8, 2, "#8a6a2a") + rect(t[0] - 4, Math.round((62 + t[1]) / 2), 8, 2, "#8a6a2a") + rect(t[0] - 2.5, 50, 1.5, t[1] - 54, "#fff1cd", 'opacity=".5"');
        }).join("") + line(333, 32, 333, 108, "#8a7aa8", 1) + oval(333, 68, 7, 3, "#6b4426") + path("M326 108 H340 L337 122 H329 Z", "#a8743f");
    },

    "hang-moonswing": function () { // A crescent moon on two ropes, with a little cushion to sit on and stars dangling from its tip.
      return path("M300 0 L299 42 M378 0 L381 50", "none", 'stroke="#c79256" stroke-width="1.6"') +                                                          // The two ropes, running to the moon's tips.
        group(path("M298 58 Q306 108 350 104 Q376 100 382 66 Q368 92 344 90 Q316 88 298 58 Z", "#ffdc83", 'stroke="#d9b24a" stroke-width="1.4" stroke-linejoin="round"') + circle(318, 88, 1.8, "#d9b24a") + circle(338, 96, 2.2, "#d9b24a") + circle(358, 94, 1.6, "#d9b24a") + // The moon and its craters.
          path("M322 84 Q338 78 352 86 Q344 94 330 92 Z", "#c85a8e") + path("M326 85 Q338 81 348 87", "none", 'stroke="#ffb3d1" stroke-width="1.4"') +                             // The cushion.
          path("M352 102 V116 M326 100 V120 M340 104 V128", "none", 'stroke="#8a7aa8" stroke-width="1"') +
          path(star(352, 121, 6), "#fff8dc", 'class="lp-twinkle"') + path(star(326, 126, 7), "#fff1cd", 'class="lp-twinkle" style="animation-delay:.6s"') + path(star(340, 133, 5), "#ffdc83", 'class="lp-twinkle" style="animation-delay:1.1s"'),
          'transform="translate(0 -16)"');
    },

    "hang-jellyfish": function () { // A big see-through jellyfish and its baby, drifting on threads, with trailing wavy tendrils and glowing spots.
      const jelly = function (x, y, s, delay) {
        const tendril = function (dx, turn) { return path("M" + dx + " 0 q" + turn + " 12 0 24 q" + (-turn) + " 12 0 24 q" + turn + " 10 0 20", "none", 'stroke="#ffd0ea" stroke-width="2" stroke-linecap="round" opacity=".7"'); };
        return group(oval(0, -6, 30, 22, GLOW, 'opacity=".09"') + tendril(-14, 6) + tendril(-5, -6) + tendril(5, 6) + tendril(14, -6) +
          path("M-24 0 C-24 -30 24 -30 24 0 Q18 -5 12 0 Q6 -5 0 0 Q-6 -5 -12 0 Q-18 -5 -24 0Z", "#ff9fd8", 'opacity=".78"') + path("M-14 -8 Q-6 -20 12 -14", "none", 'stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" opacity=".55"') +
          circle(-8, -8, 2.6, "#fff0f8", 'class="lp-twinkle" style="animation-delay:' + delay + 's"') + circle(5, -14, 2, "#fff0f8", 'class="lp-twinkle" style="animation-delay:' + (delay + .5) + 's"') + circle(10, -6, 2.4, "#fff0f8"),
          'transform="translate(' + x + " " + y + ") scale(" + s + ')"');
      };
      return line(320, 0, 320, 14, "#8a7aa8", 1) + line(364, 0, 364, 56, "#8a7aa8", 1) + jelly(320, 38, 1.05, 0) + jelly(364, 76, .62, .8);
    },

    "hang-balloon": function () { // A hot-air balloon: narrower and narrower teardrops stacked up make the stripes.
      const bulb = function (w) { return "M333 14 C" + (333 - w * 1.45) + " 14 " + (333 - w * 1.4) + " 76 " + (333 - w * .42) + " 94 L" + (333 + w * .42) + " 94 C" + (333 + w * 1.4) + " 76 " + (333 + w * 1.45) + " 14 333 14Z"; };
      return line(333, 0, 333, 14, "#8a7aa8", 1.2) + path(bulb(42), "#e8556a") + path(bulb(32), "#ffdc83") + path(bulb(21), "#70b8eb") + path(bulb(10), "#fff1e0") +
        path("M309 80 H357 L352 90 H314 Z", "#7a4a2a") + [316, 324, 333, 342, 350].map(function (x) { return circle(x, 85, 1.8, "#ffdc83"); }).join("") +                                       // The band of little gold studs.
        path("M319 94 L325 112 M347 94 L341 112 M333 94 V112", "none", 'stroke="#c79256" stroke-width="1.2"') + rect(324, 112, 18, 13, "#a8743f", 'rx="2"') + path("M324 116 H342 M324 120 H342 M329 112 V125 M337 112 V125", "none", 'stroke="#7a4a2a" stroke-width="1"') + // Ropes and wicker basket.
        oval(321, 119, 3.4, 4.5, "#c9b58a") + oval(345, 119, 3.4, 4.5, "#c9b58a") +                                                                                                     // Sandbags.
        path("M312 40 q5 -8 12 -6 M316 30 q4 -5 9 -4", "none", 'stroke="#ffffff" stroke-width="2.2" opacity=".35" stroke-linecap="round"') +
        path("M333 14 V6 L342 9 L333 12", "#ff8ecb") + path(star(296, 52, 4), "#fff8dc", 'class="lp-twinkle"') + path(star(374, 70, 3.4), "#fff8dc", 'class="lp-twinkle" style="animation-delay:.8s"');
    },

    "hang-chandelier": function () { // A crystal chandelier: curling gold arms, flickering candles and rows of hanging drops.
      const crystal = function (x, y, s) { return path("M" + x + " " + y + " l" + 3.6 * s + " " + 6 * s + " l-" + 3.6 * s + " " + 8 * s + " l-" + 3.6 * s + " -" + 8 * s + " Z", "#bfe8ff", 'stroke="#ffffff" stroke-width=".7"'); };
      const arms = "M333 44 Q303 42 301 66 M333 44 Q363 42 365 66 M333 44 Q317 44 315 58 M333 44 Q349 44 351 58 M333 62 Q303 70 301 86 M333 62 Q363 70 365 86";                             // The six curling arms, drawn as one line.
      const candles = [[301, 60], [365, 60], [315, 52], [351, 52], [333, 50]];
      let drops = "";
      [[301, 88], [309, 96], [317, 100], [325, 104], [333, 106], [341, 104], [349, 100], [357, 96], [365, 88]].forEach(function (d, i) { drops += line(d[0], d[1] - 12, d[0], d[1], "#e8c877", .8) + crystal(d[0], d[1], i % 2 ? 1 : 1.3); }); // The lowest fringe of crystals.
      return line(333, 0, 333, 36, "#d9b24a", 2) + path("M319 36 H347 L342 44 H324 Z", "#d9b24a") + oval(333, 62, 8, 4, "#d9b24a") + circle(333, 16, 4, "#d9b24a") + rect(331, 44, 4, 60, "#d9b24a", 'rx="2"') + path(arms, "none", 'stroke="#e8c877" stroke-width="3" stroke-linecap="round"') +
        candles.map(function (c, i) { // A candle, a drip plate and a flickering flame.
          return rect(c[0] - 2.5, c[1] - 11, 5, 11, "#fff1e0", 'rx="1"') + oval(c[0], c[1], 6, 2, "#c9a15a") + oval(c[0], c[1] - 18, 8, 8, "#ffb84a", 'opacity=".2"') + path("M" + c[0] + " " + (c[1] - 22) + " q4 6 0 10 q-4 -4 0 -10 Z", "#ffc86a", 'class="lp-twinkle" style="animation-delay:' + (i * .4) + 's"');
        }).join("") + [[305, 76], [361, 76], [319, 70], [347, 70]].map(function (c) { return crystal(c[0], c[1], 1.1); }).join("") + drops + crystal(333, 108, 1.1);
    },

    "hang-orrery": function () { // A grand brass orrery: three tilted orbits around a blazing sun, with planets riding in front and behind. Each orbit is drawn in two halves so the sun can pass between them.
      const cx = 333, cy = 66, tilt = "rotate(-14 333 66)";
      const back = function (rx, ry, colour, w) { return path("M" + (cx - rx) + " " + cy + " A" + rx + " " + ry + " 0 0 1 " + (cx + rx) + " " + cy, "none", 'stroke="' + colour + '" stroke-width="' + w + '"'); };
      const front = function (rx, ry, colour, w) { return path("M" + (cx - rx) + " " + cy + " A" + rx + " " + ry + " 0 0 0 " + (cx + rx) + " " + cy, "none", 'stroke="' + colour + '" stroke-width="' + w + '"'); };
      const place = function (rx, ry, a) { return [round(cx + rx * Math.cos(a)), round(cy + ry * Math.sin(a))]; }; // A point on an orbit; sin(a) > 0 is the near side.
      const dot = function (spot, r, colour, extra) { return circle(spot[0], spot[1], r, colour, extra || "") + circle(round(spot[0] - r * .35), round(spot[1] - r * .35), round(r * .3), "#ffffff", 'opacity=".55"'); };
      const orbits = [[46, 20], [33, 14], [20, 8]];
      const p1 = place(46, 20, -2.6), p2 = place(33, 14, -.5), p3 = place(20, 8, 2.5), p4 = place(46, 20, .85), p5 = place(33, 14, 2.3);
      const behind = group(back(46, 20, "#c9a15a", 2.5) + back(33, 14, "#e8c877", 2.2) + back(20, 8, "#c9a15a", 2) + dot(p1, 7, "#70b8eb") + dot(p2, 5.5, "#ff9d6b"), 'transform="' + tilt + '"');
      const before = group(front(46, 20, "#e8c877", 2.5) + front(33, 14, "#ffdc83", 2.2) + front(20, 8, "#e8c877", 2) +
        dot(p3, 4, "#c9a8ff") + dot(p4, 8, "#7fc49a") + oval(p4[0], p4[1], 13, 3.4, "none", 'stroke="#ffe0b0" stroke-width="1.6"') + dot(p5, 5, "#ff8ecb") +
        orbits.map(function (o, i) { const n = place(o[0], o[1], 3.14 * i + .4); return circle(n[0], n[1], 2.2, "#fff4c0"); }).join(""), 'transform="' + tilt + '"');          // Brass rivets riding the rings.
      return line(cx, 0, cx, 18, "#d9b24a", 2.4) + circle(cx, 17, 10, "#d9b24a") + circle(cx, 17, 12, "none", 'stroke="#8a6a2a" stroke-width="3.5" stroke-dasharray="2.6 2.6"') + circle(cx, 17, 4, "#8a6a2a") + rect(cx - 1.8, 26, 3.6, 26, "#c9a15a") + // The brass gearbox it all hangs from.
        behind + circle(cx, cy, 10, "#ffb84a", 'class="lp-neon"') + circle(cx, cy, 7.5, "#ffdc83") + path(star(cx, cy, 15), "#fff8dc", 'class="lp-twinkle"') + circle(cx, cy, 3, "#ffffff") + before +
        path("M294 116 Q308 112 322 112", "none", 'stroke="#ffffff" stroke-width="2" stroke-linecap="round" opacity=".45"') + path(star(322, 112, 5), "#fff8dc", 'class="lp-twinkle" style="animation-delay:.5s"') +                                     // A tiny comet below.
        [[291, 40, 3.4], [376, 30, 4], [378, 106, 3], [290, 88, 3]].map(function (s, i) { return path(star(s[0], s[1], s[2]), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .4) + 's"'); }).join("");
    },

    // Star Capsule curtains
    "curtain-gingham": function () { // Country check panels, tied back to each side with a bow.
      function panel(x, flip) { // One tied-back panel: only the checks that fit the panel's width at that height are drawn, so none land on the glass.
        const edge = "M0 88 H44 Q32 150 8 170 Q24 200 32 236 H0 Z";
        function width(y) { return y < 170 ? 44 - (y - 88) / 82 * 38 : 8 + (y - 170) / 66 * 26; }
        let checks = "";
        for (let y = 92, row = 0; y < 232; y += 7, row++) for (let col = 0; col * 7 + 6 <= width(y + 6); col++) if ((col + row) % 2) checks += "M" + col * 7 + " " + y + " h6 v6 h-6Z ";
        return group(path(edge, "#fbe3ee") + path(checks, "#f58fb6", 'opacity=".55"') + path("M0 164 Q10 168 14 172 Q10 176 0 178", "#e8689a") + circle(13, 171, 3.4, "#fff"), 'transform="translate(' + x + ' 0) scale(' + flip + ' 1)"');
      }
      return ART.rod() + panel(126, 1) + panel(274, -1);
    },
    "curtain-bunting": function () { // A string of party pennants along the top and two more dropping down the sides; the view stays clear.
      const colours = ["#ff8ecb", "#ffdc83", "#70ebea", "#c9a8ff", "#7fc49a"];
      function flags(points) { // A flag hanging from each point on a string.
        return points.map(function (p, i) { return path("M" + (p[0] - 7) + " " + p[1] + " h14 l-7 15 Z", colours[i % 5]); }).join("");
      }
      const top = [130, 146, 162, 178, 194, 210, 226, 242, 258, 272].map(function (x) { return [x, 92 + Math.round(Math.sin((x - 130) / 142 * Math.PI) * 12)]; });
      return ART.rod() + path("M126 92 Q200 118 274 92", "none", 'stroke="#f2e6cf" stroke-width="1.5"') + flags(top) +
        path("M130 92 Q124 162 132 236 M270 92 Q276 162 268 236", "none", 'stroke="#f2e6cf" stroke-width="1.5"') + flags([[127, 128], [128, 158], [128, 188], [131, 218], [273, 124], [272, 156], [272, 186], [269, 216]]);
    },
    "curtain-cloud": function () { // A fluffy valance of cloud hanging off the rod, with little cloudlets drifting down each side.
      function cloudlet(x, y, s) { // Three overlapping puffs and a soft shadow underneath, as one small cloud.
        return group(oval(0, 3, 12, 5, "#c9d6f2") + circle(-6, 0, 6.5, "#fff") + circle(6, 0, 6, "#fff") + circle(0, -3, 8, "#fff"), 'transform="translate(' + x + " " + y + ") scale(" + s + ')"');
      }
      const top = [[140, 100, 14], [162, 98, 17], [188, 100, 17], [212, 98, 17], [238, 100, 16], [260, 101, 13]];
      return ART.rod() + top.map(function (c) { return oval(c[0], c[1] + 8, c[2], 5, "#c9d6f2"); }).join("") + rect(126, 86, 148, 13, "#eef2ff", 'rx="6"') + top.map(function (c) { return circle(c[0], c[1], c[2], "#eef2ff"); }).join("") +
        cloudlet(132, 136, 1) + cloudlet(138, 168, .85) + cloudlet(130, 200, .7) + cloudlet(268, 140, 1) + cloudlet(262, 176, .85) + cloudlet(271, 208, .7);
    },
    "curtain-roller": function () { // A striped roller blind pulled a third of the way down, with a pull-cord and a wooden bead.
      let stripes = "";
      for (let x = 136; x < 264; x += 16) stripes += "M" + x + " 94 h8 v34 h-8Z ";
      return rect(126, 86, 148, 8, "#a8743f", 'rx="4"') + rect(130, 94, 140, 36, "#f6dcc0") + path(stripes, "#f0a86e", 'opacity=".75"') + rect(130, 128, 140, 5, "#7a4a2a", 'rx="2"') +
        path("M200 133 V162", "none", 'stroke="#7a4a2a" stroke-width="1.5"') + circle(200, 166, 4.5, "#a8743f") + circle(198.5, 164.5, 1.3, "#fff", 'opacity=".5"');
    },
    "curtain-ivy": function () { // Ivy trailing from the rod in three strands each side, with leaves growing off each stem.
      function vine(x, length, lean, seed) { // One strand: a wavy stem through evenly spaced points, with a leaf at each point.
        let stem = "M" + x + " 88", leaves = "";
        leaves += scatter(seed, length, function (r, i) {
          const y = 96 + i * 14, px = round(x + lean * i), side = i % 2 ? 1 : -1, k = round(r(.8, 1.2));
          stem += " L" + px + " " + y;
          return "M" + px + " " + y + " q" + round(side * 6 * k) + " -8 " + round(side * 11 * k) + " -2 q" + round(-side * 1 * k) + " 8 " + round(-side * 11 * k) + " 2 Z ";
        });
        return path(stem, "none", 'stroke="#3f7a5a" stroke-width="1.6" stroke-linejoin="round"') + path(leaves, "#5fae86") + circle(round(x + lean * (length - 1)), 96 + (length - 1) * 14, 2, "#9bd6ae");
      }
      return ART.rod() + vine(130, 10, .6, 171) + vine(143, 7, -.4, 172) + vine(156, 4, .5, 173) + vine(270, 10, -.6, 174) + vine(257, 7, .4, 175) + vine(244, 4, -.5, 176);
    },
    "curtain-noren": function () { // Short split door-cloth panels across the top, each with a cat's paw on it.
      const panels = [[130, "#3a4a7a"], [166, "#46588a"], [202, "#3a4a7a"], [238, "#46588a"]];
      return ART.rod() + panels.map(function (p) {
        const x = p[0] + 16;
        return rect(p[0], 88, 32, 52, p[1]) + rect(p[0], 88, 32, 6, "#2a3660") + path("M" + (p[0] + 4) + " 140 V148 M" + (p[0] + 28) + " 140 V148", "none", 'stroke="' + p[1] + '" stroke-width="3" stroke-linecap="round"') +
          oval(x, 120, 7, 6, "#f2e6cf") + circle(x - 7, 112, 2.6, "#f2e6cf") + circle(x - 2.5, 108.5, 2.6, "#f2e6cf") + circle(x + 2.5, 108.5, 2.6, "#f2e6cf") + circle(x + 7, 112, 2.6, "#f2e6cf");
      }).join("");
    },
    "curtain-macrame": function () { // Knotted cream cord in tapering fringes down the sides, each strand finishing in a tassel.
      function fringe(x, dir) { // Rows of diamond knots, three strands wide at the top; the shorter strands stop first.
        let knots = "", cords = "", tassels = "";
        for (let col = 0; col < 3; col++) {
          const last = [5, 3, 1][col]; // Which row each strand ends on: outermost longest.
          for (let row = 0; row <= last; row++) {
            const y = 104 + row * 21, kx = x + dir * (col * 12 + (row % 2) * 6);
            knots += "M" + kx + " " + (y - 8) + " l5 8 l-5 8 l-5 -8Z ";
            cords += "M" + kx + " " + (y - 13) + " V" + (y + 13) + " ";
            if (row === last) tassels += path("M" + kx + " " + (y + 10) + " l-3.5 24 h7 Z", "#e8d8b8") + circle(kx, y + 10, 3, "#c9a15a");
          }
        }
        return path(cords, "none", 'stroke="#d8c8a8" stroke-width="1.4"') + path(knots, "#f2e6cf") + tassels;
      }
      return ART.rod() + fringe(130, 1) + fringe(270, -1);
    },
    "curtain-velvet": function () { // A little theatre: deep red drapes with gold fringe, a swagged pelmet and big tasselled tie-backs.
      function drape(flip) { // One side, drawn on the left and mirrored for the right.
        return group(path("M126 88 H164 Q150 130 152 160 Q140 170 136 236 H126 Z", "#a02c44") + path("M134 88 Q130 140 130 236 M144 88 Q138 130 137 162 M154 88 Q144 120 145 158", "none", 'stroke="#6e1c30" stroke-width="2.2"') +
          path("M126 232 H136 V238 H126 Z", "#ffdc83") + path("M126 150 Q142 166 156 158", "none", 'stroke="#ffdc83" stroke-width="4" stroke-linecap="round"') + circle(156, 158, 5, "#ffdc83") + path("M152 162 L150 182 M156 163 V184 M160 162 L162 182", "none", 'stroke="#ffc65a" stroke-width="2"') + rect(151, 184, 10, 3, "#b8862e", 'rx="1"'),
          flip ? 'transform="translate(400 0) scale(-1 1)"' : "");
      }
      return drape(false) + drape(true) + path("M122 84 H278 V100 Q258 122 239 102 Q220 124 200 104 Q180 124 161 102 Q142 122 122 100 Z", "#b83450") +
        path("M122 100 Q142 122 161 102 Q180 124 200 104 Q220 124 239 102 Q258 122 278 100", "none", 'stroke="#ffdc83" stroke-width="3.5"') +
        path("M161 90 Q164 100 161 102 M200 90 Q203 102 200 104 M239 90 Q236 100 239 102", "none", 'stroke="#7a1f34" stroke-width="2"') + [161, 200, 239].map(function (x) { return path(star(x, 92, 5), "#ffdc83"); }).join("") + rect(120, 82, 160, 5, "#ffdc83", 'rx="2.5"') + circle(120, 84.5, 5, "#ffdc83") + circle(280, 84.5, 5, "#ffdc83");
    },
    "curtain-butterflies": function () { // Gauzy side panels with a flutter of butterflies resting on them, a few twinkling.
      function fly(x, y, size, colour, tilt, cls) { // Two rounded wings and a thin body.
        return group(path("M0 0 C" + (-size * 1.2) + " " + (-size * 1.1) + " " + (-size * 1.4) + " " + size * .4 + " 0 " + size * .4 + " C" + (size * 1.4) + " " + size * .4 + " " + (size * 1.2) + " " + (-size * 1.1) + " 0 0 Z", colour) + line(0, -size * .5, 0, size * .7, "#3a2452", 1.2), 'transform="translate(' + x + " " + y + ") rotate(" + tilt + ')"' + (cls ? ' class="lp-twinkle" style="animation-delay:' + cls + 's"' : ""));
      }
      const sheer = "#d9c8ff";
      return ART.rod() + path("M126 88 H160 Q146 150 154 238 Q140 242 126 236 Z M274 88 H240 Q254 150 246 238 Q260 242 274 236 Z", sheer, 'opacity=".28"') +
        path("M134 88 Q130 160 134 238 M146 88 Q140 150 144 240 M266 88 Q270 160 266 238 M254 88 Q260 150 256 240", "none", 'stroke="#f0e6ff" stroke-width="1" opacity=".5"') +
        fly(140, 112, 7, "#ff8ecb", -18) + fly(146, 152, 5.5, "#70ebea", 14, ".4") + fly(136, 192, 6.5, "#ffdc83", 10) + fly(150, 220, 5, "#c9a8ff", -22, ".9") +
        fly(258, 118, 6, "#ffdc83", 16, "1.2") + fly(250, 156, 6.5, "#ff8ecb", -10) + fly(264, 190, 5.5, "#c9a8ff", 20) + fly(252, 226, 5, "#70ebea", -16, ".6") + fly(186, 112, 4.5, "#ff8ecb", 24, "1.5") + fly(214, 106, 4, "#70ebea", -26);
    },
    "curtain-starfall": function () { // A night-sky mantle: indigo drapes full of stars, gold constellations, comet tie-backs and glowing moon finials.
      function drape(flip) {
        return group(path("M126 86 H172 Q152 150 160 238 H126 Z", "#1c1f55") + path("M136 86 Q132 160 134 238 M148 86 Q140 150 146 238", "none", 'stroke="#0e1038" stroke-width="2.4"') +
          path("M126 86 H172 Q152 150 160 238", "none", 'stroke="' + GLOW + '" stroke-width="2.4" class="lp-neon"') +
          scatter(flip ? 181 : 182, 18, function (roll) { const x = round(roll(130, 156)), y = round(roll(96, 232)); return path(star(x, y, round(roll(1.6, 3.4))), "#fff4cf", Math.round(x) % 2 ? 'class="lp-twinkle" style="animation-delay:' + round(roll(0, 1.8)) + 's"' : ""); }) +
          path("M134 108 L146 124 L138 142 L152 160", "none", 'stroke="#ffdc83" stroke-width="1" opacity=".85"') + path(star(134, 108, 3.5), "#ffdc83") + path(star(152, 160, 3.5), "#ffdc83") +
          path("M126 196 Q144 206 162 190", "none", 'stroke="#ffdc83" stroke-width="3" stroke-linecap="round"') + path("M162 190 L176 180 L170 192 Z", "#fff4cf", 'class="lp-neon"') + circle(162, 190, 3.6, "#ffdc83"),
          flip ? 'transform="translate(400 0) scale(-1 1)"' : "");
      }
      return drape(false) + drape(true) + rect(120, 83, 160, 5, "#0e1038", 'rx="2.5"') + line(120, 85.5, 280, 85.5, GLOW, 1.2, 'opacity=".7"') +
        path("M124 78 A8 8 0 1 0 124 92 A9.5 9.5 0 0 1 124 78 Z M276 78 A8 8 0 1 1 276 92 A9.5 9.5 0 0 0 276 78 Z", "#fff4cf", 'class="lp-neon"') + // A crescent finial at each end of the rod.
        
        path(star(200, 100, 6), "#fff8dc", 'class="lp-twinkle" style="animation-delay:.3s"') + path(star(176, 96, 3.5), "#fff4cf", 'class="lp-twinkle" style="animation-delay:1s"') + path(star(224, 96, 3.5), "#fff4cf", 'class="lp-twinkle" style="animation-delay:1.6s"');
    },

    // Star Capsule door
    "door-barn": function () { // A red barn door hanging from a sliding rail, with the classic white X brace.
      let planks = "";
      for (let x = -46; x < 6; x += 10) planks += line(x, 204, x, 354, "#7f2f22", 1.5);
      return rect(-70, 190, 90, 6, "#3a3a4a", 'rx="2"') + circle(-44, 198, 6, "#6a6a80") + circle(-6, 198, 6, "#6a6a80") + circle(-44, 198, 2, "#2a2a3a") + circle(-6, 198, 2, "#2a2a3a") +
        rect(-60, 202, 70, 152, "#a8432e", 'rx="2"') + planks +
        path("M-56 206 H6 V350 H-56 Z M-56 206 L6 278 M6 206 L-56 278 M-56 278 H6 M-56 278 L6 350 M6 278 L-56 350", "none", 'stroke="#fff1e0" stroke-width="4" stroke-linejoin="round"') +
        rect(-1, 266, 5, 26, "#3a3a4a", 'rx="2"');
    },
    "door-stable": function () { // A Dutch door: the top half stands open on the night, a flower box sits on the ledge.
      return rect(-62, 196, 74, 158, "#54391f", 'rx="3"') + rect(-56, 202, 62, 70, "#101630") + circle(-18, 228, 11, "#fff1cd") + circle(-13, 225, 10, "#101630") +
        scatter(41, 9, function (roll) { return circle(round(roll(-52, 2)), round(roll(208, 254)), round(roll(.7, 1.5)), "#fff", 'opacity=".85"'); }) +
        path("M-56 272 V258 Q-42 248 -26 258 Q-12 266 6 254 V272 Z", "#27584a") +
        rect(-56, 276, 62, 78, "#3f8a66") + rect(-49, 286, 48, 60, "#4f9c78", 'rx="2"') + circle(-1, 312, 4, "#ffdc83") +
        rect(-62, 270, 74, 7, "#7a5a3a", 'rx="2"') + path("M-48 270 l3 -8 h26 l3 8 Z", "#c0764f") +
        [[-44, "#ff8ecb"], [-36, "#ffdc83"], [-28, "#70ebea"], [-20, "#ff8ecb"]].map(function (f) { return line(f[0], 263, f[0], 256, "#5fae86", 1.5) + circle(f[0], 254, 3.6, f[1]) + circle(f[0], 254, 1.3, "#fff8dc"); }).join("");
    },
    "door-candy": function () { // Peppermint stripes, a gumdrop trim, a pinwheel window and a lollipop for a handle.
      let stripes = "";
      for (let i = 0; i < 7; i++) if (i % 2 === 0) stripes += rect(-56 + i * 62 / 7, 214, 62 / 7, 140, "#e8556a");
      const swirl = [0, 60, 120, 180, 240, 300].map(function (a) { return path("M-25 256 L-9 256 A16 16 0 0 1 -11.1 264 Z", "#e8556a",'transform="rotate(' + a + ' -25 256)"'); }).join("");
      return rect(-62, 196, 74, 158, "#fff1e0", 'rx="3"') + rect(-56, 214, 62, 140, "#fff8f0") + stripes +
        [-54, -39, -24, -9, 6].map(function (x, i) { return path("M" + (x - 8) + " 214 a8 8 0 0 1 16 0 Z", ["#ff8ecb", "#70ebea", "#ffdc83", "#c9a8ff", "#7fc49a"][i]); }).join("") +
        circle(-25, 256, 18, "#fff8f0", 'stroke="#d9c7b0" stroke-width="2.5"') + swirl +
        circle(-1, 300, 7, "#ffdc83") + path("M-1 300 m0 -4.5 a4.5 4.5 0 1 1 -4.5 4.5 a3 3 0 1 0 3 -3", "none", 'stroke="#e8556a" stroke-width="1.4"') + rect(-2.2, 307, 2.4, 12, "#fff1e0");
    },
    "door-letters": function () { // A cheerful blue door so loved it is overflowing with mail.
      function env(x, y, deg, fill) { return group(rect(-11, -7, 22, 14, fill, 'rx="1"') + path("M-11 -7 L0 2 L11 -7", "none", 'stroke="#c0a890" stroke-width="1.2"') + path("M0 -3 c-3 -3 -6 1 0 5 c6 -4 3 -8 0 -5 Z", "#e8556a"), 'transform="translate(' + x + " " + y + ") rotate(" + deg + ')"'); }
      return rect(-62, 196, 74, 158, "#2f4f80", 'rx="3"') + rect(-56, 202, 62, 152, "#5a8fcc") + rect(-49, 210, 48, 58, "#4a7ab0", 'rx="3"') + rect(-49, 318, 48, 30, "#4a7ab0", 'rx="3"') +
        path("M-25 256 C-46 240 -42 220 -32 220 Q-25 220 -25 228 Q-25 220 -18 220 C-8 220 -4 240 -25 256 Z", "#ff8ecb", 'stroke="#fff1e0" stroke-width="2.5"') + path(star(-25, 236, 6), "#fff8dc") +
        env(-36, 280, -12, "#fff8f0") + env(-22, 278, 8, "#ffe9c9") + env(-10, 283, -6, "#fff1e0") +
        rect(-48, 286, 46, 10, "#d9b24a", 'rx="2"') + rect(-44, 289, 38, 3, "#54391f") + circle(-2, 306, 4, "#ffdc83") +
        env(-40, 351, 24, "#ffe9c9");
    },
    "door-bookcase": function () { // Looks like a bookcase, but it has swung open a crack and light spills from behind.
      const spines = ["#c85a8e", "#70ebea", "#ffdc83", "#9c73b8", "#7fc49a", "#e98a5a", "#8fa2d8"];
      let books = "";
      [212, 244, 276, 308].forEach(function (y, row) {
        books += rect(-56, y + 26, 58, 4, "#54391f");
        books += scatter(61 + row * 7, 6, function (roll, i) {
          const h = round(roll(17, 25)), lean = roll(0, 1) > .8;
          return rect(-54 + i * 9.4, y + 26 - h, 8.2, h, spines[Math.floor(roll(0, spines.length))], 'rx="1"' + (lean ? ' opacity=".75"' : ""));
        });
      });
      return oval(10, 280, 20, 90, "#ffdc83", 'opacity=".22"') + path("M4 200 L14 196 V356 L4 354 Z", "#ffe9a8", 'opacity=".85"') +
        rect(-62, 196, 66, 158, "#6b4a2f", 'rx="3"') + rect(-57, 203, 58, 149, "#2d1d3d") + rect(-62, 196, 66, 7, "#8a5a2a", 'rx="2"') + books +
        path("M-7 278 l9 -19 l-5 -3 l-9 19 Z", "#e8556a") + circle(-1, 262, 1.6, "#ffdc83");
    },
    "door-hollow": function () { // A doorway grown from an old tree trunk, with a lantern and a ring of mushrooms.
      return path("M-74 354 Q-62 340 -64 300 L-62 214 Q-40 196 -24 204 Q-6 196 14 212 L14 300 Q14 342 24 354 Z", "#6b4a2f") +
        path("M-58 300 Q-54 260 -60 226 M-50 330 Q-48 310 -52 290 M8 290 Q4 256 10 226 M4 330 Q2 316 6 300 M-40 212 Q-34 206 -28 210 M-16 208 Q-8 206 -2 212", "none", 'stroke="#4a3218" stroke-width="2" stroke-linecap="round"') +
        oval(-60, 208, 16, 9, "#5fae86") + oval(-36, 197, 18, 9, "#7fc49a") + oval(-8, 199, 17, 8, "#5fae86") + oval(12, 208, 13, 8, "#7fc49a") +
        path("M-50 354 V268 A25 26 0 0 1 0 268 V354 Z", "#3a2147", 'stroke="#a8743f" stroke-width="4"') + path("M-34 248 V354 M-17 248 V354", "none", 'stroke="#2a1a3e" stroke-width="1.5"') +
        circle(-25, 272, 9, "#ffdc83", 'stroke="#a8743f" stroke-width="2.5"') + path("M-25 263 V281 M-34 272 H-16", "none", 'stroke="#a8743f" stroke-width="1.5"') + circle(-6, 314, 3.2, "#ffdc83") +
        line(-62, 248, -55, 248, "#54391f", 2) + oval(-62, 262, 14, 16, "#ffb84a", 'opacity=".22"') + line(-62, 248, -62, 254, "#54391f", 1.5) + rect(-67, 254, 10, 13, "#ffb84a", 'rx="3" class="lp-twinkle"') + rect(-68, 252, 12, 3, "#54391f") +
        [[-64, 348, 7, "#e8556a"], [-52, 351, 4, "#ff9d6b"], [8, 350, 5, "#e8556a"], [18, 349, 7, "#ff9d6b"]].map(function (m) { return rect(m[0] - 1.5, m[1] - 1, 3, 6, "#fff1e0") + path("M" + (m[0] - m[2]) + " " + m[1] + " Q" + m[0] + " " + (m[1] - m[2] * 1.5) + " " + (m[0] + m[2]) + " " + m[1] + " Z", m[3]); }).join("");
    },
    "door-hatch": function () { // A riveted ship's hatch with a porthole full of fish and a big wheel to turn.
      const rivets = [[-54, 208], [-40, 204], [-25, 203], [-10, 204], [4, 208], [-58, 230], [-58, 256], [-58, 282], [-58, 308], [-58, 334], [8, 230], [8, 256], [8, 282], [8, 308], [8, 334]];
      return rect(-62, 196, 74, 158, "#4a5470", 'rx="14"') + rect(-54, 206, 58, 142, "#6a7a98", 'rx="10"') + rivets.map(function (r) { return circle(r[0], r[1], 2, "#a8b4c4"); }).join("") +
        circle(-25, 246, 24, "#2a3a60") + circle(-25, 246, 20, "#1a4f8a") + path("M-44 252 Q-34 246 -25 252 T-6 252 V256 Q-20 266 -44 256 Z", "#2a6db0") +
        path("M-34 240 q8 -7 14 0 q-6 7 -14 0 Z M-34 240 l-6 -5 v10 Z", "#ff9d6b") + circle(-23, 239, 1.1, "#2a1a3e") + path("M-14 254 q5 -4 9 0 q-4 4 -9 0 Z M-14 254 l-4 -3 v6 Z", "#ffdc83") +
        circle(-11, 236, 1.8, "#cfe8ff", 'opacity=".8"') + circle(-9, 230, 1.2, "#cfe8ff", 'opacity=".8"') + path("M-39 232 A19 19 0 0 1 -28 227", "none", 'stroke="#fff" stroke-width="2" opacity=".5" stroke-linecap="round"') +
        circle(-25, 310, 18, "none", 'stroke="#e8556a" stroke-width="5"') + path("M-25 289 V331 M-46 310 H-4 M-40 295 L-10 325 M-10 295 L-40 325", "none", 'stroke="#e8556a" stroke-width="3.5" stroke-linecap="round"') + circle(-25, 310, 6, "#a8b4c4");
    },
    "door-stained": function () { // A pointed cathedral arch of stained glass, its panes fanning out around a moon.
      const hub = [-25, 276], rim = [[-54, 276], [-54, 256], [-48, 238], [-38, 220], [-25, 204], [-12, 220], [-2, 238], [4, 256], [4, 276]];
      const hues = ["#c85a8e", "#6d4bb0", "#70b8eb", "#ffb84a", "#ffb84a", "#70b8eb", "#6d4bb0", "#c85a8e"];
      let panes = "";
      hues.forEach(function (fill, i) { panes += path("M" + hub.join(" ") + " L" + rim[i].join(" ") + " L" + rim[i + 1].join(" ") + " Z", fill, 'stroke="#2a1a3e" stroke-width="2" stroke-linejoin="round"'); });
      const low = ["#70ebea", "#ff8ecb", "#ffdc83", "#c9a8ff", "#ffb84a", "#7fc49a", "#ffdc83", "#70b8eb", "#ff8ecb"];
      for (let row = 0; row < 3; row++) for (let col = 0; col < 3; col++) panes += rect(-54 + col * 58 / 3, 278 + row * 23, 58 / 3, 23, low[row * 3 + col], 'stroke="#2a1a3e" stroke-width="2"');
      return oval(-25, 270, 44, 90, "#ffdc83", 'opacity=".1"') + path("M-64 354 V262 Q-62 222 -25 188 Q12 222 14 262 V354 Z", "#8a7aa8") + path("M-60 354 V262 Q-58 226 -25 194 Q8 226 10 262 V354 Z", "#3a3350") + panes +
        path("M-41 276 A16 16 0 0 1 -9 276 Z", "#fff8dc", 'stroke="#2a1a3e" stroke-width="2"') + path("M-25 276 m-9 0 a11 11 0 0 1 14 -9 a9 9 0 0 0 -7 9 Z", "#d9c79a") +
        path(star(-25, 242, 7), "#fff8dc", 'class="lp-twinkle"') + path(star(-40, 262, 3.5), "#fff8dc") + path(star(-10, 262, 3.5), "#fff8dc") + path(star(-25, 312, 7), "#fff8dc") +
        circle(1, 318, 3.6, "#ffdc83", 'stroke="#2a1a3e" stroke-width="1"') + rect(-64, 350, 78, 5, "#6a5c80", 'rx="1"');
    },
    "door-clockwork": function () { // A brass vault of meshing gears, with a dial and a little puff of steam.
      function gear(x, y, r, fill) { return circle(x, y, r, "none", 'stroke="' + fill + '" stroke-width="' + round(r * .32) + '" stroke-dasharray="' + round(r * .36) + " " + round(r * .3) + '"') + circle(x, y, r * .8, fill) + circle(x, y, r * .3, "#3a2c1c"); }
      return rect(-62, 196, 74, 158, "#7a5a2a", 'rx="4"') + rect(-56, 202, 62, 152, "#3a2c1c") +
        path("M-50 208 V226 H-34 M2 346 V322 H-14 M-50 346 V332 H-40", "none", 'stroke="#a8743f" stroke-width="4" stroke-linejoin="round"') +
        gear(-25, 282, 26, "#d9b24a") + gear(-46, 316, 13, "#c0764f") + gear(-3, 322, 15, "#e0c070") + gear(-44, 246, 11, "#e0c070") + gear(-6, 240, 17, "#c0764f") +
        path(star(-25, 282, 8), "#fff8dc") +
        circle(-25, 218, 12, "#fff1cd", 'stroke="#d9b24a" stroke-width="3"') + path("M-25 218 V210 M-25 218 L-19 221", "none", 'stroke="#3a2c1c" stroke-width="1.8" stroke-linecap="round"') +
        [[10, 203, 4], [17, 198, 5.5], [9, 192, 4.5]].map(function (p, i) { return circle(p[0], p[1], p[2], "#e8dff0", 'opacity="' + (.6 - i * .15) + '" class="lp-twinkle" style="animation-delay:' + (i * .4) + 's"'); }).join("") +
        [[-58, 200], [8, 200], [-58, 350], [8, 350]].map(function (r) { return circle(r[0], r[1], 2.4, "#ffdc83"); }).join("");
    },
    "door-skystair": function () { // The grand prize: golden doors thrown open on the night sky, and a stair of light climbing up to the moon.
      function stair(t) { // The stair at t (0 floor, 1 moon): its height, centre and half-width. It winds, narrows and bunches up with distance.
        const eased = 1 - Math.pow(1 - t, 1.7);
        return { y: round(354 - 112 * eased), c: round(-25 + Math.sin(t * Math.PI * 2.2) * 11 * (1 - t * .7)), w: round(22 * (1 - eased) + 2) };
      }
      let left = "", right = "", rungs = "";
      for (let i = 0; i <= 14; i++) {
        const s = stair(i / 14);
        left += (i ? " L" : "M") + round(s.c - s.w) + " " + s.y; // Up the left edge...
        right = " L" + round(s.c + s.w) + " " + s.y + right;     // ...and back down the right.
        if (i < 14) rungs += line(round(s.c - s.w), s.y, round(s.c + s.w), s.y, "#e8a23c", round(2.4 - i * .12));
      }
      const steps = path(left + right + " Z", "#fff8dc") + rungs;
      return oval(-25, 356, 54, 9, GLOW, 'opacity=".22"') + oval(-25, 276, 48, 90, GLOW, 'opacity=".09"') +
        path("M-66 354 V236 A41 41 0 0 1 16 236 V354 Z", "#d9b24a") + path("M-61 354 V236 A36 36 0 0 1 11 236 V354 Z", "#ffdc83") + path("M-58 354 V236 A33 33 0 0 1 8 236 V354 Z", "#0d1230") +
        oval(-34, 262, 24, 34, "#c85a8e", 'opacity=".22"') + oval(-10, 300, 24, 40, "#70b8eb", 'opacity=".18"') +
        scatter(77, 16, function (roll, i) { return circle(round(roll(-54, 4)), round(roll(214, 330)), round(roll(.6, 1.5)), "#fff", i % 3 ? 'opacity=".8"' : 'class="lp-twinkle" style="animation-delay:' + round(i * .23) + 's"'); }) +
        circle(-25, 228, 22, "#fff1cd", 'opacity=".18"') + circle(-25, 228, 14, "#fff1cd", 'class="lp-neon"') + circle(-30, 224, 3, "#e6d5a8") + circle(-20, 232, 2.2, "#e6d5a8") + steps +
        path("M-58 354 V236 Q-58 222 -50 212 L-44 224 V344 Z", "#3f7a9c") + path("M8 354 V236 Q8 222 0 212 L-6 224 V344 Z", "#3f7a9c") + path("M-55 348 V238 Q-55 228 -50 220 L-47 226 V342 Z M5 348 V238 Q5 228 0 220 L-3 226 V342 Z", "none", 'stroke="#ffdc83" stroke-width="1.4"') +
        path(star(-51, 284, 3.5), "#ffdc83") + path(star(1, 284, 3.5), "#ffdc83") +
        path(star(-25, 195, 9), "#fff8dc", 'class="lp-twinkle"') + path(star(-46, 204, 5), "#ffdc83", 'class="lp-twinkle" style="animation-delay:.5s"') + path(star(-4, 204, 5), "#ffdc83", 'class="lp-twinkle" style="animation-delay:1s"');
    },

    // Star Capsule floor
    // Floors run from y = 350 down to 500 and repeat side by side, so patterns repeat evenly across 400 or stay inside the tile.

    "floor-chevron": function () { // Warm parquet laid in stacked zigzag bands.
      const woods = ["#8a5a3f", "#9a6a48", "#7a4f36", "#a8744f"];
      let out = rect(0, 350, 400, 150, woods[3]);
      for (let band = 0; band < 11; band++) {
        const top = 370 + band * 15;
        const low = function (y) { return Math.min(y, 500); };                                                   // Nothing draws below the floor's edge.
        let d = "M0 " + low(top);
        for (let x = 0; x < 400; x += 50) d += " L" + (x + 25) + " " + low(top - 20) + " L" + (x + 50) + " " + low(top); // The zigzag's top edge.
        const edge = d;                                                                                          // The line between planks, drawn on its own so the band's closed ends don't draw a dark seam where tiles meet.
        d += " V" + low(top + 15);
        for (let x = 400; x > 0; x -= 50) d += " L" + (x - 25) + " " + low(top - 5) + " L" + (x - 50) + " " + low(top + 15); // And back along its bottom edge.
        out += path(d + " Z", woods[band % 4]) + path(edge, "none", 'stroke="#4f3020" stroke-width="1"');
      }
      return out + rect(0, 350, 400, 3, "#e5c090", 'opacity=".5"');
    },

    "floor-terrazzo": function () { // Polished stone flecked with candy-coloured chips.
      const chips = ["#ff8ecb", "#70ebea", "#ffdc83", "#c9a8ff", "#fff1e0", "#7fc49a"];
      return rect(0, 350, 400, 150, "#3f3650") + scatter(41, 90, function (roll, i) {
        const x = roll(8, 392), y = roll(358, 494), s = roll(3, 8), a = roll(0, 6.28);
        const corner = function (turn) { return round(x + Math.cos(a + turn) * s) + " " + round(y + Math.sin(a + turn) * s * .7); };     // Squashed a little, so the chips lie flat.
        return path("M" + corner(0) + " L" + corner(2.1) + " L" + corner(4.2) + " Z", chips[i % 6], 'opacity=".85" stroke="' + chips[i % 6] + '" stroke-width="2.5" stroke-linejoin="round"'); // Rounded off, like tumbled stone.
      }) + rect(0, 350, 400, 3, "#c9a8ff", 'opacity=".45"');
    },

    "floor-pebbles": function () { // Rounded river stones set in dark mortar; each stone has a soft top-light.
      const stones = ["#6a6e88", "#7c7f98", "#585c78", "#8a84a0", "#6f7a8c"], roll = dice(52);
      let out = rect(0, 350, 400, 150, "#232738");
      for (let row = 0; row < 5; row++) for (let col = 0; col < 8; col++) {
        const x = 25 + col * 50 + roll(-3, 3) + (row % 2 ? 0 : 6) - 3, y = 366 + row * 30 + roll(-3, 3), rx = roll(17, 22), ry = roll(11, 14);
        out += oval(round(x), round(y), round(rx), round(ry), stones[(row * 3 + col) % 5]) + oval(round(x - 3), round(y - 4), round(rx * .55), round(ry * .4), "#ffffff", 'opacity=".12"');
      }
      return out + rect(0, 350, 400, 3, "#b6b0c8", 'opacity=".4"');
    },

    "floor-tatami": function () { // Woven straw mats bound with dark cloth, half a mat offset on the second row.
      let weave = "";
      for (let x = 3; x < 400; x += 6) weave += "M" + x + " 353 V500 ";                                                  // The fine straw lines, all one shape.
      return rect(0, 350, 400, 150, "#9a9457") + path(weave, "none", 'stroke="#7f7a45" stroke-width="1.2"') +
        rect(0, 350, 400, 5, "#27402f") + rect(0, 423, 400, 7, "#27402f") + rect(0, 495, 400, 5, "#27402f") +             // Cloth bindings along the rows.
        rect(197, 350, 6, 75, "#27402f") + rect(0, 350, 3, 75, "#27402f") + rect(397, 350, 3, 75, "#27402f") +            // Upright bindings between the mats of the first row (the edge ones meet at the join).
        rect(97, 427, 6, 70, "#27402f") + rect(297, 427, 6, 70, "#27402f");                                               // And the second row's, half a mat along.
    },

    "floor-cloudwalk": function () { // Soft banks of cloud to pad about on. Each bank's bumps add up to exactly 400, so banks meet at the join.
      const bank = function (y, a, b, colour) { // a and b are the widths of two alternating bumps.
        let d = "M0 " + y;
        for (let i = 0; i < 4; i++) d += " a" + a / 2 + " " + a * .46 + " 0 0 1 " + a + " 0 a" + b / 2 + " " + b * .46 + " 0 0 1 " + b + " 0";
        return path(d + " V500 H0 Z", colour);
      };
      return rect(0, 350, 400, 150, "#5a6fc0") +
        scatter(44, 14, function (roll) { return path(star(round(roll(10, 390)), round(roll(354, 376)), round(roll(2, 4))), "#fff1cd", 'opacity=".8"'); }) +
        bank(388, 62, 38, "#8c9fe0") + bank(420, 44, 56, "#b5c2ee") + bank(450, 66, 34, "#d6dff8") + bank(482, 40, 60, "#f0f4ff") +
        rect(0, 350, 400, 3, "#dfe6ff", 'opacity=".55"');
    },

    "floor-piano": function () { // One octave of giant keys: pad across and play. Seven white keys fit the tile exactly.
      const keyW = 400 / 7;
      let out = rect(0, 350, 400, 150, "#ddd3ec");
      for (let i = 1; i < 7; i++) out += rect(round(i * keyW - 1), 350, 2, 150, "#8d82a8");                                   // The gaps between white keys.
      out += rect(0, 350, 1, 150, "#8d82a8") + rect(399, 350, 1, 150, "#8d82a8");                                               // The gap on the join, half on each tile.
      out += rect(0, 488, 400, 12, "#beb1d4");                                                                                   // The keys' front lip.
      [1, 2, 4, 5, 6].forEach(function (i) { out += rect(round(i * keyW - 14), 350, 28, 92, "#241a36", 'rx="3"') + rect(round(i * keyW - 9), 354, 6, 72, "#4d4066", 'rx="3"'); }); // Black keys, with a shine.
      const note = "m0 0 v-20 q8 2 10 8 q-4 -3 -7 -3 v18 a6 4 0 1 1 -3 -3Z";
      [[28, 470, "#c85a8e"], [142, 456, "#6d4bb0"], [258, 474, "#2f8a9a"], [370, 460, "#c85a8e"]].forEach(function (n) { out += path("M" + n[0] + " " + n[1] + note, n[2], 'opacity=".75"'); }); // A few music notes drifting over the keys.
      return out + rect(0, 350, 400, 3, "#f6f0ff", 'opacity=".6"');
    },

    "floor-patchwork": function () { // A quilt-stitched carpet: 24 little patches, each with its own motif and dashed stitching.
      const cloths = ["#8a3f6a", "#3f6a8a", "#b9843f", "#5a4a95", "#3f8a70", "#a84f4f"], ink = "#fff1e0";
      let out = "";
      for (let row = 0; row < 3; row++) for (let col = 0; col < 8; col++) {
        const x = col * 50, y = 350 + row * 50, mid = x + 25, midY = y + 25, kind = (row * 3 + col) % 5;
        out += rect(x, y, 50, 50, cloths[(row * 2 + col) % 6]) + rect(x + 3, y + 3, 44, 44, "none", 'stroke="#ffffff" stroke-opacity=".6" stroke-width="1.2" stroke-dasharray="3 3"');
        if (kind === 0) out += circle(mid - 9, midY - 8, 3.5, ink) + circle(mid + 9, midY - 6, 3.5, ink) + circle(mid, midY + 8, 3.5, ink);        // Dots.
        if (kind === 1) out += path("M" + mid + " " + (midY + 12) + " C" + (mid - 18) + " " + (midY - 2) + " " + (mid - 9) + " " + (midY - 13) + " " + mid + " " + (midY - 5) + " C" + (mid + 9) + " " + (midY - 13) + " " + (mid + 18) + " " + (midY - 2) + " " + mid + " " + (midY + 12) + "Z", ink); // A heart.
        if (kind === 2) out += path("M" + (x + 9) + " " + (y + 13) + " H" + (x + 41) + " M" + (x + 9) + " " + midY + " H" + (x + 41) + " M" + (x + 9) + " " + (y + 37) + " H" + (x + 41), "none", 'stroke="' + ink + '" stroke-width="3" stroke-linecap="round"'); // Stripes.
        if (kind === 3) out += path(star(mid, midY, 14), ink);                                                                                        // A star.
        if (kind === 4) out += circle(mid, midY, 4, "#ffdc83") + [0, 1, 2, 3, 4].map(function (p) { const a = p / 5 * 6.283; return circle(round(mid + Math.cos(a) * 9), round(midY + Math.sin(a) * 9), 4.5, ink); }).join(""); // A flower.
      }
      return out + rect(0, 350, 400, 3, "#fff1e0", 'opacity=".4"');
    },

    "floor-star-mosaic": function () { // A jewel-toned mosaic: twelve eight-pointed stars, each laid out in a 100-wide box then squashed a little to lie flat.
      const eight = function (outer, inner) { // An eight-pointed star as one polygon.
        let d = "";
        for (let i = 0; i < 16; i++) { const a = i / 16 * 6.283 - 1.5708, r = i % 2 ? inner : outer; d += (i ? " L" : "M") + round(Math.cos(a) * r) + " " + round(Math.sin(a) * r); }
        return d + "Z";
      };
      const corners = "M-50 -50 L-22 -50 L-50 -22Z M50 -50 L22 -50 L50 -22Z M-50 50 L-22 50 L-50 22Z M50 50 L22 50 L50 22Z";
      const cw = 400 / 6, ch = 75;
      let out = rect(0, 350, 400, 150, "#14304a");
      for (let row = 0; row < 2; row++) for (let col = 0; col < 6; col++) {
        const even = (row + col) % 2;
        out += group(rect(-50, -50, 100, 100, even ? "#173f5e" : "#3a1f52") + path(corners, even ? "#a88a3e" : "#4f8ab0") + path(eight(47, 24), even ? "#d9b860" : "#c06a9c") +
          path(eight(27, 13), even ? "#6a2a50" : "#14304a") + path(star(0, 0, 11), "#fff0d0", (row + col) % 3 ? "" : 'class="lp-twinkle" style="animation-delay:' + (col * .3) + 's"'),
          'transform="translate(' + round(cw / 2 + col * cw) + " " + (350 + ch / 2 + row * ch) + ") scale(" + round(cw / 100) + " " + (ch / 100) + ')"');
      }
      return out + rect(0, 350, 400, 3, "#ffdc83", 'opacity=".7"');
    },

    "floor-frozen-lake": function () { // Clear blue ice with hairline cracks, snowflakes caught beneath it and the loops a skater left behind.
      const flake = function (x, y, s, o) { // A six-armed snowflake with little V tips.
        let d = "";
        for (let i = 0; i < 6; i++) { const a = i / 6 * 6.283, c = Math.cos(a), n = Math.sin(a); d += "M0 0 L" + round(c * 12) + " " + round(n * 12) + " M" + round(c * 7 - n * 3.5) + " " + round(n * 7 + c * 3.5) + " L" + round(c * 11) + " " + round(n * 11) + " M" + round(c * 7 + n * 3.5) + " " + round(n * 7 - c * 3.5) + " L" + round(c * 11) + " " + round(n * 11) + " "; }
        return path(d, "none", 'stroke="#e4f4ff" stroke-width="1.4" stroke-linecap="round" opacity="' + o + '" transform="translate(' + x + " " + y + ") scale(" + s + ' .8)"');
      };
      return rect(0, 350, 400, 150, "#2f5e8c") +
        path("M0 392 Q100 374 200 392 T400 392 V350 H0 Z", "#4a82b0", 'opacity=".55"') + path("M0 448 Q100 430 200 448 T400 448 V500 H0 Z", "#25496f", 'opacity=".6"') +      // Paler and deeper patches in the ice.
        [[26, 44], [152, 34], [290, 46]].map(function (s) { return path("M" + s[0] + " 352 h" + s[1] + " l-30 148 h-" + s[1] + " Z", "#d6f0ff", 'opacity=".1"'); }).join("") +      // Long reflections of the window light.
        path("M40 372 L70 392 L58 412 L96 430 M70 392 L104 384 L132 398 M58 412 L34 440 M256 362 L240 388 L272 408 L256 438 L290 470 M272 408 L312 402 M240 388 L212 400 M340 410 L366 436 L352 462 M366 436 L388 430 M120 462 L150 478 L188 470 M150 478 L146 496", "none", 'stroke="#d6f0ff" stroke-width="1.1" stroke-linejoin="round" opacity=".7"') + // The cracks.
        path("M60 476 C80 454 118 458 120 476 C122 494 80 496 60 476 C40 456 8 458 10 476 C12 494 48 494 60 476 Z", "none", 'stroke="#ffffff" stroke-width="1.2" stroke-dasharray="6 4" opacity=".55"') +  // A skater's figure of eight.
        flake(112, 372, 1, .8) + flake(214, 444, 1.2, .9) + flake(330, 374, .9, .7) + flake(372, 482, 1.1, .85) + flake(36, 410, .7, .6) + flake(294, 448, .7, .6) + flake(186, 374, .6, .55) +
        scatter(46, 22, function (roll) { return circle(round(roll(8, 392)), round(roll(356, 496)), round(roll(.8, 2.2)), "#e4f4ff", 'opacity="' + round(roll(.25, .7)) + '"'); }) +
        rect(0, 350, 400, 3, "#cfeaff", 'opacity=".75"');
    },

    "floor-lantern-pool": function () { // A still, dark pool: the moon's reflection breaks into ripples under Lumi while paper lanterns float and flicker around her.
      const lantern = function (l, i) { // Glow, ripple ring, lantern and its wobbling reflection.
        const x = l[0], y = l[1], s = l[2];
        return oval(x, y + 2, 36 * s, 11 * s, "#ffb84a", 'opacity=".16"') +
          oval(x, y + 4, 26 * s, 7 * s, "none", 'stroke="#ffdc83" stroke-width="1" opacity=".45"') +
          path("M" + round(x - 11 * s) + " " + (y + 2) + " L" + round(x + 11 * s) + " " + (y + 2) + " L" + round(x + 8 * s) + " " + round(y - 14 * s) + " L" + round(x - 8 * s) + " " + round(y - 14 * s) + " Z", "#ffdc83", 'class="lp-twinkle" style="animation-delay:' + (i * .41 % 2).toFixed(2) + 's"') +
          path("M" + round(x - 11 * s) + " " + (y + 2) + " L" + round(x + 11 * s) + " " + (y + 2), "none", 'stroke="#d9722a" stroke-width="2"') + rect(round(x - 3 * s), round(y - 11 * s), round(6 * s), round(4 * s), "#ff8e4a") +
          oval(x, round(y + 12 * s), round(9 * s), round(2 * s), "#ffdc83", 'opacity=".4"');
      };
      const ripples = [[366, 26, 5], [379, 42, 4.5], [393, 60, 5], [408, 80, 5], [424, 102, 5.5], [442, 124, 6], [462, 148, 6.5], [484, 172, 7]];
      return rect(0, 350, 400, 150, "#0a1030") + path("M0 410 Q100 396 200 410 T400 410 V500 H0 Z", "#101a48", 'opacity=".7"') +
        scatter(48, 34, function (roll) { return circle(round(roll(6, 394)), round(roll(358, 496)), round(roll(.5, 1.5)), "#e6d5ff", 'opacity="' + round(roll(.3, .9)) + '"'); }) +
        ripples.map(function (r, i) { return oval(200, r[0], r[1], r[2], "#f4e8c8", 'opacity="' + round(.9 - i * .08) + '"'); }).join("") +                                                         // The moon's reflection, breaking into wider ripples nearer us.
        path("M0 440 Q50 432 100 440 T200 440 T300 440 T400 440 M0 470 Q66 462 133 470 T266 470 T400 470", "none", 'stroke="#2b3f8a" stroke-width="1.4" opacity=".7"') +
        [[30, 392, 1.5], [98, 374, 1.05], [150, 386, .85], [250, 374, .9], [312, 388, 1.2], [344, 408, 1.5], [158, 490, 1.1], [246, 492, .9], [344, 486, 1.15], [40, 484, 1.3]].map(lantern).join("") +
        [[150, 412, 3], [252, 408, 2.4], [376, 460, 2.6], [32, 440, 2.2]].map(function (s, i) { return path(star(s[0], s[1], s[2] * 2), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .6) + 's"'); }).join("") +
        rect(0, 350, 400, 3, GLOW, 'opacity=".6"');
    },

    // Star Capsule window frame
    "frame-lattice": function () { // A diamond window with leaded crossing bars and a little sill ledge under it.
      return path(CLIPS["frame-lattice"], "none", 'stroke="#d9b24a" stroke-width="6" stroke-linejoin="round"') +
        path("M170 128.5 L230 193.5 M185 112.5 L245 177.5 M155 144.5 L215 209.5 M230 128.5 L170 193.5 M215 112.5 L155 177.5 M245 144.5 L185 209.5", "none", 'stroke="#d9b24a" stroke-width="1.8" opacity=".8"') +
        circle(200, 96, 4, "#fff1cd") + circle(260, 161, 4, "#fff1cd") + circle(140, 161, 4, "#fff1cd") + ART.sill("#d9b24a");
    },
    "frame-heart": function () { // A heart-shaped opening in a pink frame with a small bow on top.
      return path(CLIPS["frame-heart"], "none", 'stroke="#ff8ecb" stroke-width="7" stroke-linejoin="round"') +
        path(CLIPS["frame-heart"], "none", 'stroke="#ffd2e8" stroke-width="2" stroke-linejoin="round"') +
        path("M200 110 L184 100 Q180 108 184 118 Z M200 110 L216 100 Q220 108 216 118 Z", "#ffdc83") + circle(200, 110, 3.6, "#d9b24a") + rect(158, 224, 84, 8, "#ff8ecb", 'rx="3"');
    },
    "frame-cloud": function () { // A puffy cloud-shaped hole, white and soft, with a few tiny raindrops on the sill.
      return path(CLIPS["frame-cloud"], "none", 'stroke="#f4f8ff" stroke-width="9" stroke-linejoin="round"') +
        path(CLIPS["frame-cloud"], "none", 'stroke="#c9d6f2" stroke-width="1.5" stroke-linejoin="round" transform="translate(200 165) scale(1.07) translate(-200 -165)"') +
        path("M166 224 q3 6 0 8 q-3 -2 0 -8 Z M200 226 q3 6 0 8 q-3 -2 0 -8 Z M234 224 q3 6 0 8 q-3 -2 0 -8 Z", "#8fc6f5");
    },
    "frame-cottage": function () { // A plain square window with both wooden shutters folded open against the wall, and a flower box.
      function shutter(x) { // One shutter: a board with slats.
        let slats = "";
        for (let y = 114; y < 220; y += 9) slats += "M" + (x + 3) + " " + y + " h22 ";
        return rect(x, 106, 28, 120, "#5f8a6e", 'rx="2"') + path(slats, "none", 'stroke="#3f6a50" stroke-width="2"');
      }
      return shutter(117) + shutter(255) + path(CLIPS["frame-cottage"], "none", 'stroke="#f2e6cf" stroke-width="5" stroke-linejoin="round"') + line(200, 106, 200, 226, "#f2e6cf", 3) + line(148, 164, 252, 164, "#f2e6cf", 3) +
        rect(136, 224, 128, 12, "#a8743f", 'rx="2"') + path("M144 224 q3 -9 6 0 M160 224 q3 -11 6 0 M176 224 q3 -9 6 0 M222 224 q3 -9 6 0 M238 224 q3 -11 6 0 M250 224 q3 -9 6 0", "none", 'stroke="#5fae86" stroke-width="2"') +
        circle(147, 216, 3.4, "#ff8ecb") + circle(163, 213, 3.4, "#ffdc83") + circle(179, 216, 3.4, "#ff8e8e") + circle(225, 216, 3.4, "#ffdc83") + circle(241, 213, 3.4, "#ff8ecb") + circle(253, 216, 3.4, "#c9a8ff");
    },
    "frame-star": function () { // A five-pointed window in gold, with a little sparkle at each tip.
      const tips = [[200, 96], [260, 146], [238, 220], [162, 220], [140, 146]];
      return path(CLIPS["frame-star"], "none", 'stroke="#ffdc83" stroke-width="7" stroke-linejoin="round"') + path(CLIPS["frame-star"], "none", 'stroke="#b8862e" stroke-width="1.5" stroke-linejoin="round"') +
        tips.map(function (t, i) { return path(star(t[0], t[1], 7), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + i * .3 + 's"'); }).join("") + rect(160, 224, 80, 8, "#ffdc83", 'rx="3"');
    },
    "frame-keyhole": function () { // The opening of an old lock, set in a big brass plate with screws.
      return path("M132 92 H268 V232 H132 Z " + CLIPS["frame-keyhole"], "#c99b3f", 'fill-rule="evenodd" stroke="#8a6220" stroke-width="2"') +
        path(CLIPS["frame-keyhole"], "none", 'stroke="#ffe9a8" stroke-width="2.5"') +
        [[140, 100], [260, 100], [140, 224], [260, 224]].map(function (s) { return circle(s[0], s[1], 4, "#8a6220") + line(s[0] - 3, s[1] - 2, s[0] + 3, s[1] + 2, "#ffe9a8", 1.2); }).join("") +
        path("M150 128 Q144 150 150 172 M250 128 Q256 150 250 172", "none", 'stroke="#ffe9a8" stroke-width="1.5" opacity=".5"');
    },
    "frame-lancet": function () { // Three tall pointed church-window arches side by side.
      const stone = "#d8cfe4";
      return path("M136 232 V144 Q136 116 160 98 Q176 110 180 124 Q184 106 200 90 Q216 106 220 124 Q224 110 240 98 Q264 116 264 144 V232 Z M144 226 V144 Q144 120 160 108 Q176 120 176 144 V226 Z M184 226 V132 Q184 110 200 96 Q216 110 216 132 V226 Z M224 226 V144 Q224 120 240 108 Q256 120 256 144 V226 Z", stone, 'fill-rule="evenodd" stroke="#9a8cb0" stroke-width="1.5"') +
        path("M144 170 H176 M184 160 H216 M224 170 H256", "none", 'stroke="#9a8cb0" stroke-width="2"') + circle(180, 122, 3, "#9a8cb0") + circle(220, 122, 3, "#9a8cb0") + rect(130, 228, 140, 7, stone, 'rx="2"');
    },
    "frame-rose": function () { // A cathedral rose window: coloured glass petals round the rim, a clear middle, stone tracery over the lot.
      const glass = ["#e8556a", "#ffb84a", "#70b8eb", "#9c73b8"];
      let petals = "", ribs = "", rivets = "";
      for (let i = 0; i < 16; i++) { // Sixteen wedges of rim, each its own colour of glass.
        const a = (i / 16) * Math.PI * 2, b = ((i + 1) / 16) * Math.PI * 2, rx = 60, ry = 65, ix = 42, iy = 46;
        petals += path("M" + round(200 + ix * Math.cos(a)) + " " + round(161 + iy * Math.sin(a)) + " L" + round(200 + rx * Math.cos(a)) + " " + round(161 + ry * Math.sin(a)) + " L" + round(200 + rx * Math.cos(b)) + " " + round(161 + ry * Math.sin(b)) + " L" + round(200 + ix * Math.cos(b)) + " " + round(161 + iy * Math.sin(b)) + " Z", glass[i % 4], 'opacity=".78"');
        rivets += star(round(200 + 51 * Math.cos(a + Math.PI / 16)), round(161 + 55.5 * Math.sin(a + Math.PI / 16)), 2.6) + " "; // A small gold sparkle in the middle of each pane.
        ribs += "M" + round(200 + ix * Math.cos(a)) + " " + round(161 + iy * Math.sin(a)) + " L" + round(200 + rx * Math.cos(a)) + " " + round(161 + ry * Math.sin(a)) + " ";
      }
      return petals + path(rivets, "#fff1cd", 'opacity=".9"') + path(ribs, "none", 'stroke="#5a4a70" stroke-width="2"') + oval(200, 161, 42, 46, "none", 'stroke="#5a4a70" stroke-width="3"') +
        path(CLIPS["frame-rose"], "none", 'stroke="#d8cfe4" stroke-width="8"') + path(CLIPS["frame-rose"], "none", 'stroke="#7a6a90" stroke-width="1.5"') +
        path("M200 115 V207 M158 161 H242", "none", 'stroke="#5a4a70" stroke-width="2"') + circle(200, 161, 5, "#ffdc83", 'stroke="#5a4a70" stroke-width="2"') + rect(150, 224, 100, 8, "#d8cfe4", 'rx="3"');
    },
    "frame-geode": function () { // A cracked-open amethyst: rough rock outside, a ring of crystal facets, and a jagged glowing hole.
      const hole = [[198, 96], [226, 104], [240, 98], [256, 124], [250, 146], [260, 172], [246, 196], [250, 216], [222, 222], [204, 226], [180, 220], [156, 224], [146, 200], [150, 178], [140, 156], [150, 134], [144, 112], [170, 108]];
      function out(p, kx, ky) { return round(200 + (p[0] - 200) * kx) + " " + round(161 + (p[1] - 161) * ky); } // A point pushed out from the middle of the hole.
      const shades = ["#7a4bb8", "#a06fff", "#5a3a9c"];
      let rock = "M", facets = "";
      hole.forEach(function (p, i) {
        const next = hole[(i + 1) % hole.length], k = i % 2 ? 1.26 : 1.14, kNext = i % 2 ? 1.14 : 1.26;
        rock += (i ? " L" : "") + out(p, i % 2 ? 1.34 : 1.27, i % 2 ? 1.26 : 1.2);
        facets += path("M" + p[0] + " " + p[1] + " L" + next[0] + " " + next[1] + " L" + out(next, kNext, kNext) + " L" + out(p, k, k) + " Z", shades[i % 3], i % 5 === 0 ? 'class="lp-twinkle" style="animation-delay:' + round(i * .2) + 's"' : "");
        if (i % 2) facets += line(p[0], p[1], out(p, k, k).split(" ")[0], out(p, k, k).split(" ")[1], "#e6d4ff", 1, 'opacity=".55"'); // A pale ridge down every other facet.
      });
      return path(rock + " Z " + CLIPS["frame-geode"], "#33204a", 'fill-rule="evenodd" stroke="#1f1430" stroke-width="2" stroke-linejoin="round"') + facets +
        path(CLIPS["frame-geode"], "none", 'stroke="#e6d4ff" stroke-width="2.5" stroke-linejoin="round" class="lp-neon"') +
        [[146, 100], [262, 118], [264, 214], [150, 226]].map(function (s, i) { return path(star(s[0], s[1], 5), "#fff", 'class="lp-twinkle" style="animation-delay:' + i * .5 + 's"'); }).join("");
    },
    "frame-orrery": function () { // A round brass orrery: the view fills the great disc, two tilted orbits ring it (clear of the glass) and carry little planets.
      function onRing(tilt, deg) { // A spot on one of the two tilted orbits, in room coordinates.
        const t = deg * Math.PI / 180, a = tilt * Math.PI / 180, x = 82 * Math.cos(t), y = 66 * Math.sin(t);
        return [round(200 + x * Math.cos(a) - y * Math.sin(a)), round(161 + x * Math.sin(a) + y * Math.cos(a))];
      }
      const planets = [[onRing(-28, 200), 5, "#ff9d6b"], [onRing(28, 20), 6, "#70ebea"], [onRing(-28, 70), 4, "#ffdc83"], [onRing(28, 250), 4.5, "#ff8ecb"]];
      return oval(200, 161, 82, 66, "none", 'stroke="#c9a15a" stroke-width="2.5" transform="rotate(-28 200 161)"') + oval(200, 161, 82, 66, "none", 'stroke="#c9a15a" stroke-width="2.5" transform="rotate(28 200 161)"') +
        path(CLIPS["frame-orrery"], "none", 'stroke="#ffdc83" stroke-width="8"') + path(CLIPS["frame-orrery"], "none", 'stroke="#fff4cf" stroke-width="2.5" class="lp-neon"') +
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map(function (i) { const a = i / 12 * Math.PI * 2, x = round(200 + 56 * Math.cos(a)), y = round(161 + 56 * Math.sin(a)); return circle(x, y, 1.6, i % 3 ? "#b8862e" : "#fff8dc"); }).join("") + // Hour marks just inside the disc.
        planets.map(function (p, i) { return circle(p[0][0], p[0][1], p[1] + 5, p[2], 'opacity=".22"') + circle(p[0][0], p[0][1], p[1], p[2], 'class="lp-twinkle" style="animation-delay:' + i * .5 + 's"') + circle(round(p[0][0] - p[1] * .3), round(p[0][1] - p[1] * .3), round(p[1] * .3), "#fff", 'opacity=".7"'); }).join("") +
        path("M151.5 93.5 A11 11 0 1 0 151.5 112.5 A13 13 0 0 1 151.5 93.5 Z", "#fff1cd", 'class="lp-neon"') + path(star(252, 92, 5), "#fff8dc", 'class="lp-twinkle"') + path(star(172, 86, 3.5), "#fff8dc", 'class="lp-twinkle" style="animation-delay:.8s"') + // The moon charm and two sparkles.
        rect(160, 225, 80, 7, "#ffdc83", 'rx="3"') + path("M176 232 l8 10 h32 l8 -10", "none", 'stroke="#c9a15a" stroke-width="2.5"') + path(star(200, 238, 4), "#fff8dc");
    },

    // Star Capsule lamp
    "lamp-arc": function () { // A slim arched floor lamp with a coral dome, leaning out over the room.
      return oval(92, 268, 44, 40, "#ffdc83", 'opacity=".12"') + oval(26, 404, 20, 6, "#3a3048") + path("M26 402 Q22 290 56 248 Q72 230 92 238", "none", 'stroke="#c9a15a" stroke-width="4" stroke-linecap="round"') +
        path("M72 260 Q74 234 92 234 Q110 234 112 260 Z", "#e8556a") + oval(92, 260, 20, 4, "#fff1cd") + circle(92, 236, 2.6, "#c9a15a");
    },
    "lamp-firefly": function () { // A shepherd's crook with a jam jar of fireflies swinging from the hook.
      const bugs = [[46, 288], [58, 278], [66, 296], [52, 304], [62, 310]];
      return oval(50, 300, 48, 46, "#d8f27a", 'opacity=".1"') + oval(26, 404, 16, 5, "#3a3048") + path("M26 402 V252 q0 -16 16 -16 q16 0 16 14 v12", "none", 'stroke="#8a7aa8" stroke-width="3.5" stroke-linecap="round"') + rect(40, 262, 36, 58, "#bfe8ff", 'rx="7" opacity=".22" stroke="#d6f0ff" stroke-width="1.8"') + rect(44, 256, 28, 7, "#a8743f", 'rx="1.5"') +
        bugs.map(function (b, i) { return circle(b[0], b[1], 7, "#d8f27a", 'opacity=".2"') + circle(b[0], b[1], 2.4, "#efffa8", 'class="lp-twinkle" style="animation-delay:' + i * .4 + 's"'); }).join("") + line(48, 270, 48, 314, "#fff", 1.4, 'opacity=".5"');
    },
    "lamp-tulip": function () { // A big pink tulip growing straight from the floor, its bud lit from inside.
      return oval(56, 268, 42, 42, "#ff8ecb", 'opacity=".12"') + path("M56 404 Q50 340 56 268", "none", 'stroke="#5fae86" stroke-width="5" stroke-linecap="round"') +
        path("M54 404 Q24 372 26 322 Q44 356 56 384 Z", "#7fc49a") + path("M58 404 Q82 378 78 332 Q66 360 56 388 Z", "#5fae86") + path("M34 242 Q34 286 56 288 Q78 286 78 242 L66 254 L56 238 L46 254 Z", "#ff8ecb") + path("M46 254 L56 238 L66 254 Q60 280 56 282 Q52 280 46 254 Z", "#ffc2dd") + oval(56, 266, 7, 12, "#fff1cd", 'opacity=".55"') + oval(56, 405, 16, 4, "#3a2147");
    },
    "lamp-bunny": function () { // A round nightlight shaped like a sitting bunny, with long ears and a bow.
      return group(oval(56, 354, 54, 52, "#ffdcb0", 'opacity=".14"') + oval(44, 288, 7, 22, "#fff1e0", 'transform="rotate(-12 44 306)"') + oval(68, 288, 7, 22, "#fff1e0", 'transform="rotate(12 68 306)"') + oval(44, 288, 3.2, 15, "#ffc2c2", 'transform="rotate(-12 44 306)"') + oval(68, 288, 3.2, 15, "#ffc2c2", 'transform="rotate(12 68 306)"') +
        oval(56, 372, 30, 32, "#fff1e0") + oval(56, 382, 20, 20, "#ffe2b8", 'opacity=".8"') + circle(56, 332, 23, "#fff1e0") + circle(47, 330, 2.4, "#3a2a2a") + circle(65, 330, 2.4, "#3a2a2a") + path("M56 336 l-3 3 h6 Z", "#ff9aae") + oval(39, 339, 4.5, 2.4, "#ffc2c2", 'opacity=".8"') + oval(73, 339, 4.5, 2.4, "#ffc2c2", 'opacity=".8"') +
        path("M56 356 L46 350 v12 Z M56 356 L66 350 v12 Z", "#c9a8ff") + circle(56, 356, 3, "#a888e8") + oval(56, 405, 26, 4, "#3a3048"), 'transform="translate(-10 0)"');
    },
    "lamp-moon": function () { // A full-moon globe on a small turned stand, with craters and a ring of light.
      const craters = [[34, 342, 5.4], [54, 356, 3.8], [46, 332, 2.8], [60, 340, 3], [38, 362, 3]];
      return oval(46, 352, 46, 46, "#ffefc0", 'opacity=".12"') + circle(46, 352, 30, "#fff4d6", 'class="lp-neon"') + path(craters.map(function (c) { return "M" + (c[0] - c[2]) + " " + c[1] + " a" + c[2] + " " + c[2] + " 0 1 0 " + c[2] * 2 + " 0 a" + c[2] + " " + c[2] + " 0 1 0 -" + c[2] * 2 + " 0Z"; }).join(" "), "#e6d3a8") + path("M24 336 A30 30 0 0 1 46 322", "none", 'stroke="#fff" stroke-width="2.5" opacity=".6" stroke-linecap="round"') +
        rect(40, 382, 12, 12, "#8a5a2a") + path("M32 394 H60 L66 404 H26 Z", "#a8743f") + oval(46, 404, 22, 4, "#5a3a20");
    },
    "lamp-mosaic": function () { // A dome of coloured glass panes held by dark lead, on a bronze stem.
      const colours = ["#e8556a", "#ffb84a", "#70b8eb", "#7fc49a", "#c9a8ff"];
      function widthAt(y) { const t = Math.min(1, Math.max(0, (306 - y) / 44)); return 42 * Math.sqrt(1 - t * t); } // Half-width of the dome at height y.
      const panes = colours.map(function () { return ""; });
      let lead = "";
      for (let row = 0; row < 3; row++) {
        const y1 = 306 - row * 14.6, y2 = 306 - (row + 1) * 14.6, w1 = widthAt(y1), w2 = widthAt(y2), cols = 7 - row;
        for (let c = 0; c < cols; c++) {
          const a1 = 56 - w1 + (2 * w1 * c) / cols, b1 = 56 - w1 + (2 * w1 * (c + 1)) / cols, a2 = 56 - w2 + (2 * w2 * c) / cols, b2 = 56 - w2 + (2 * w2 * (c + 1)) / cols;
          const d = "M" + round(a1) + " " + round(y1) + " L" + round(b1) + " " + round(y1) + " L" + round(b2) + " " + round(y2) + " L" + round(a2) + " " + round(y2) + " Z ";
          panes[(c + row * 2) % 5] += d;
          lead += d;
        }
      }
      return group(oval(56, 318, 62, 44, "#ffcf8a", 'opacity=".13"') + panes.map(function (d, i) { return path(d, colours[i]); }).join("") + path(lead, "none", 'stroke="#3a2452" stroke-width="1.4" stroke-linejoin="round"') + rect(12, 305, 88, 4, "#8a6220", 'rx="2"') + path("M12 309 q6 7 12 0 q6 7 12 0 q6 7 12 0 q6 7 12 0 q6 7 12 0 q6 7 12 0 q6 7 16 0 Z", "#b8862e") +
        circle(56, 262, 4, "#8a6220") + rect(53, 314, 6, 72, "#8a6220") + oval(56, 350, 8, 12, "#b8862e") + path("M38 392 Q56 374 74 392 L78 404 H34 Z", "#8a6220") + oval(56, 404, 22, 4, "#5a4010"), 'transform="translate(-8 0)"');
    },
    "lamp-street": function () { // An old-town street lamp with a snowy cap, an iron arm and a robin who has found a warm spot.
      return oval(54, 272, 48, 40, "#ffcf7a", 'opacity=".14"') + rect(38, 398, 32, 6, "#2e2a3e", 'rx="1"') + rect(44, 386, 20, 12, "#3a3650") + rect(50, 300, 8, 88, "#3a3650") + rect(46, 312, 16, 3, "#6a6680") + rect(46, 372, 16, 3, "#6a6680") +
        path("M54 324 H84 M84 324 q6 -2 6 -8 M78 318 v-4", "none", 'stroke="#3a3650" stroke-width="2.8" stroke-linecap="round"') + oval(84, 310, 5, 4.4, "#b05838") + circle(80, 306, 3.4, "#7a4030") + path("M77 306 l-3 1 l3 1 Z", "#ffb84a") + circle(79.4, 305.2, .6, "#fff") +
        path("M34 256 L42 298 H66 L74 256 Z", "#ffe0a0", 'opacity=".9"') + oval(54, 278, 10, 16, "#fff4cf", 'class="lp-twinkle"') + path("M46 256 L48 298 M62 256 L60 298 M38 277 H70", "none", 'stroke="#3a3650" stroke-width="2"') + rect(40, 298, 28, 4, "#3a3650", 'rx="1"') +
        path("M30 256 L54 234 L78 256 Z", "#3a3650") + path("M36 256 Q40 244 54 238 Q68 244 72 256 Q60 250 54 254 Q48 250 36 256 Z", "#f4f8ff") + circle(54, 232, 3, "#ffdc83");
    },
    "lamp-jelly": function () { // A jellyfish floating on a cord: its bell breathes with light and it trails beaded tentacles.
      const tentacles = [[36, 8], [47, -7], [58, 8], [68, -7]];
      const beads = [];
      function sway(t, y) { // How far a tentacle has swung sideways at height y: two half-waves of 50, then straight on.
        const d = y - 286; if (d >= 100) return 0;
        const seg = d < 50 ? d / 50 : (d - 50) / 50;
        return (d < 50 ? t[1] : -t[1]) * 2 * seg * (1 - seg);
      }
      tentacles.forEach(function (t, i) { for (let y = 306 + i * 7; y < 382; y += 19) beads.push([t[0] + sway(t, y), y]); }); // Beads sit right on the tentacles.
      const dots = beads.map(function (p) { const r = 1.6; return "M" + round(p[0] - r) + " " + round(p[1]) + " a" + r + " " + r + " 0 1 0 " + r * 2 + " 0 a" + r + " " + r + " 0 1 0 -" + r * 2 + " 0Z"; }).join(" ");
      return line(52, 40, 52, 238, "#8a7aa8", 1.2) + oval(52, 268, 54, 44, "#c9a8ff", 'opacity=".14"') +
        path(tentacles.map(function (t, i) { return "M" + t[0] + " 286 q" + t[1] + " 25 0 50 q" + -t[1] + " 25 0 50 q" + t[1] * .5 + " 8 0 " + [22, 10, 18, 6][i]; }).join(" "), "none", 'stroke="#e6d4ff" stroke-width="2.4" opacity=".7" stroke-linecap="round"') +
        path(dots, "#fff8dc", 'class="lp-twinkle"') +
        path("M20 278 Q20 236 52 236 Q84 236 84 278 Q74 270 68 280 Q60 272 52 282 Q44 272 36 280 Q30 270 20 278 Z", "#c9a8ff", 'opacity=".92" class="lp-twinkle" style="animation-delay:.5s"') + path("M30 266 Q32 248 52 246 Q72 248 74 266", "none", 'stroke="#fff" stroke-width="3" opacity=".45" stroke-linecap="round"') + circle(44, 260, 3, "#fff8dc", 'opacity=".85"') + circle(60, 256, 2.4, "#fff8dc", 'opacity=".85"') + circle(52, 268, 2, "#fff8dc", 'opacity=".85"');
    },
    "lamp-tree": function () { // A little bare tree with light-bulb blossoms in three colours, twinkling in turns.
      const orbs = [[28, 262, 0], [50, 244, 1], [72, 254, 2], [84, 276, 0], [20, 288, 1], [40, 276, 2], [60, 272, 0], [76, 298, 1], [32, 304, 2], [52, 296, 1], [16, 312, 0], [68, 318, 2]];
      const glow = ["#ff8ecb", "#70ebea", "#ffdc83"];
      return oval(50, 294, 60, 56, "#ffdc83", 'opacity=".08"') + path("M50 402 V340 M50 340 Q34 320 30 298 Q28 282 28 266 M50 340 Q68 322 76 296 Q80 280 72 258 M50 330 Q50 300 50 248 M40 320 Q24 318 20 292 M62 324 Q78 318 84 280 M50 300 Q58 288 60 276 M50 306 Q40 294 40 280", "none", 'stroke="#7a5a44" stroke-width="3.2" stroke-linecap="round"') +
        orbs.map(function (o, i) { return circle(o[0], o[1], 7, glow[o[2]], 'opacity=".22"') + circle(o[0], o[1], 3.4, glow[o[2]], 'class="lp-twinkle" style="animation-delay:' + round(i * .23) + 's"') + circle(o[0] - 1, o[1] - 1, 1, "#fff", 'opacity=".9"'); }).join("") +
        path("M30 404 Q40 392 50 396 Q60 392 70 404 Z", "#5a4436") + oval(50, 405, 26, 4, "#3a2a22");
    },
    "lamp-starcradle": function () { // A brass crescent cupping a real captured star, orbited by a ring and little charms; the glow follows Lumi's.
      const sparks = [[14, 250, 3.5, 0], [96, 246, 4.5, .4], [20, 300, 2.8, .9], [92, 300, 3.2, 1.3], [108, 274, 2.6, 1.7], [8, 278, 2.4, .6]];
      return oval(54, 268, 70, 70, GLOW, 'opacity=".1"') + oval(54, 268, 44, 44, GLOW, 'opacity=".12"') +
        path("M42 404 L54 366 L66 404", "none", 'stroke="#c9a15a" stroke-width="3" stroke-linejoin="round"') + oval(54, 404, 22, 4.5, "#8a6220") + rect(51, 308, 6, 62, "#c9a15a") + oval(54, 368, 7, 4, "#ffdc83") + oval(54, 340, 5, 3, "#ffdc83") + oval(54, 322, 5, 3, "#ffdc83") + // Tripod and pole, with brass collars.
        path("M26 254 A34 34 0 1 0 82 254 A30 30 0 0 1 26 254 Z", "#ffdc83", 'stroke="#b8862e" stroke-width="1.5"') + path("M34 270 Q54 290 74 270", "none", 'stroke="#fff4cf" stroke-width="2.5" stroke-linecap="round" opacity=".8"') + // The crescent, horns up.
        path("M54 300 l5 -6 l-5 -6 l-5 6 Z", "#ff8ecb") + // A little jewel at the crescent's heart.
        oval(54, 254, 42, 11, "none", 'stroke="#fff4cf" stroke-width="1.6" opacity=".75" transform="rotate(-18 54 254)"') + // The orbit ring, behind the star.
        path(star(54, 254, 20), "#fff8dc", 'class="lp-neon lp-twinkle"') + path(star(54, 254, 11), "#ffe08a") + circle(54, 254, 4, "#fff") +
        path("M14.06 266.98 A42 11 -18 0 0 93.94 241.02", "none", 'stroke="#fff4cf" stroke-width="1.8" stroke-linecap="round" opacity=".9"') + // The ring's near half, passing in front of the star.
        circle(90, 232, 3.2, "#70ebea", 'class="lp-twinkle" style="animation-delay:.3s"') + circle(16, 268, 2.4, "#ff8ecb", 'class="lp-twinkle" style="animation-delay:1s"') +
        sparks.map(function (s) { return path(star(s[0], s[1], s[2]), "#fff4cf", 'class="lp-twinkle" style="animation-delay:' + s[3] + 's"'); }).join("") +
        line(26, 254, 22, 280, "#c9a15a", 1) + path(star(22, 284, 4.5), "#ffe9a8") + line(82, 254, 86, 274, "#c9a15a", 1) + path(star(86, 278, 4), "#ffe9a8");
    },

    // Star Capsule plant
    "plant-tulips": function () { // Three tulips and their long leaves in a striped pot.
      const heads = [[4, 322, "#ff8ecb", "#e8669f"], [18, 304, "#ffdc83", "#f0b84a"], [32, 326, "#e8556a", "#c23a50"]];
      return path("M12 372 Q2 350 -6 332 Q8 346 16 372 Z M24 372 Q34 352 44 338 Q30 348 20 372 Z", "#5fae86") +
        path("M14 372 Q6 346 4 330 M18 372 V314 M22 372 Q30 350 32 334", "none", 'stroke="#3f8a66" stroke-width="3" stroke-linecap="round"') +
        heads.map(function (h) {
          const x = h[0], y = h[1];
          return path("M" + (x - 10) + " " + (y - 8) + " L" + (x - 5) + " " + (y - 3) + " L" + x + " " + (y - 10) + " L" + (x + 5) + " " + (y - 3) + " L" + (x + 10) + " " + (y - 8) + " Q" + (x + 12) + " " + (y + 10) + " " + x + " " + (y + 10) + " Q" + (x - 12) + " " + (y + 10) + " " + (x - 10) + " " + (y - 8) + " Z", h[2]) +
            path("M" + x + " " + (y - 10) + " Q" + (x + 7) + " " + y + " " + x + " " + (y + 10) + " Q" + (x - 7) + " " + y + " " + x + " " + (y - 10) + " Z", h[3]);
        }).join("") + ART.pot("#e8dff0") + path("M8 374 L11 392 M16 374 V392 M24 374 V392 M31 374 L28 392", "none", 'stroke="#9c73b8" stroke-width="3"');
    },
    "plant-heartvine": function () { // Heart-shaped leaves climbing a mossy post.
      function heart(x, y, s, deg, fill) { return path("M0 8 C-11 0 -8 -8 -3.5 -8 Q0 -8 0 -4 Q0 -8 3.5 -8 C8 -8 11 0 0 8 Z", fill, 'transform="translate(' + x + " " + y + ") rotate(" + deg + ") scale(" + s + ')"'); }
      const leaves = [[6, 360, 1, -60], [31, 352, 1, 60], [4, 340, .95, -55], [32, 332, .95, 58], [6, 320, .9, -50], [30, 312, .85, 55], [8, 301, .8, -40], [27, 294, .7, 40], [18, 284, .65, 0]];
      return rect(14, 288, 8, 86, "#6b4a2f", 'rx="4"') + scatter(19, 12, function (roll) { return circle(round(roll(14, 22)), round(roll(292, 370)), round(roll(1, 2.2)), "#5fae86"); }) +
        path("M18 372 Q32 356 18 346 Q4 336 18 326 Q30 316 18 306 Q10 298 18 290", "none", 'stroke="#3f8a66" stroke-width="2" stroke-linecap="round"') +
        leaves.map(function (l, i) { return heart(l[0], l[1], l[2], l[3], i % 2 ? "#7fc49a" : "#5fae86"); }).join("") + ART.pot("#9c73b8");
    },
    "plant-kittypot": function () { // Plump succulents growing out of a pot with a sleepy cat's face.
      let leaves = "";
      [-70, -46, -24, 0, 24, 46, 70].forEach(function (a, i) { leaves += oval(18, 340, 7.5, 20, i % 2 ? "#7fc49a" : "#9bd6ae", 'transform="rotate(' + a + ' 18 364)"'); });
      [-30, 0, 30].forEach(function (a) { leaves += oval(18, 348, 6, 14, "#c8ecd2", 'transform="rotate(' + a + ' 18 364)"'); });
      return leaves + circle(-2, 338, 3, "#ffb3d1") + circle(40, 340, 3, "#ffb3d1") +
        path("M0 368 L-2 354 L9 362 Z M36 368 L38 354 L27 362 Z", "#fff1e0") + path("M2 364 L1 358 L6 362 Z M34 364 L35 358 L30 362 Z", "#ffb3d1") +
        rect(-2, 362, 40, 30, "#fff1e0", 'rx="13"') +
        path("M6 376 q4 4 8 0 M22 376 q4 4 8 0", "none", 'stroke="#462846" stroke-width="1.8" stroke-linecap="round"') + path("M16 381 h4 l-2 2.4 Z", "#ff8ecb") + path("M18 383.4 q-2.5 3 -5 1 M18 383.4 q2.5 3 5 1", "none", 'stroke="#462846" stroke-width="1.2" stroke-linecap="round"') +
        path("M2 381 H-6 M2 385 L-5 388 M34 381 H42 M34 385 L41 388", "none", 'stroke="#c0a890" stroke-width="1"');
    },
    "plant-dandelion": function () { // A dandelion clock, a few of its seeds already drifting off.
      const cx = 18, cy = 310;
      let puff = circle(cx, cy, 26, "#fff", 'opacity=".1"');
      for (let i = 0; i < 22; i++) {
        const a = i / 22 * Math.PI * 2, r = i % 2 ? 22 : 25, x = round(cx + Math.cos(a) * r), y = round(cy + Math.sin(a) * r);
        puff += line(cx, cy, x, y, "#f4f0ff", .8, 'opacity=".8"') + circle(x, y, 1.7, "#fff");
      }
      return path("M18 372 Q15 345 18 316", "none", 'stroke="#7fc49a" stroke-width="3" stroke-linecap="round"') + path("M16 366 Q2 356 -4 344 Q10 348 17 360 Z M19 356 Q32 346 40 342 Q30 354 19 362 Z", "#5fae86") +
        puff + circle(cx, cy, 5, "#c0a038") +
        [[50, 296, 0], [58, 284, .6], [-14, 292, 1.2]].map(function (s) { return group(line(0, 0, 0, 9, "#f4f0ff", .8) + path("M-4 -1 Q0 -5 4 -1 M-3 -2 L0 0 L3 -2", "none", 'stroke="#fff" stroke-width=".9"') + circle(0, 9, 1.1, "#c0a038"), 'transform="translate(' + s[0] + " " + s[1] + ') rotate(18)" class="lp-twinkle" style="animation-delay:' + s[2] + 's"'); }).join("") + ART.pot("#70b8eb");
    },
    "plant-lavender": function () { // Lavender sprigs filling an old tin watering can.
      let sprigs = "";
      [[-8, 300], [2, 288], [12, 296], [22, 284], [32, 294], [42, 304]].forEach(function (s, i) {
        const base = 2 + i * 6.4;
        sprigs += path("M" + base + " 364 Q" + ((base + s[0]) / 2) + " 340 " + s[0] + " " + (s[1] + 22), "none", 'stroke="#5fae86" stroke-width="1.8" stroke-linecap="round"');
        for (let b = 0; b < 7; b++) sprigs += oval(s[0] + (b % 2 ? 2.4 : -2.4), s[1] + b * 3.6, 3.2, 2.4, b % 3 ? "#9c73b8" : "#c9a8ff");
      });
      return sprigs + path("M-2 372 L-20 348", "none", 'stroke="#8fa2d8" stroke-width="7" stroke-linecap="round"') + path("M-26 343 L-15 339 L-18 354 Z", "#a8b4d8") +
        path("M38 366 Q54 368 50 384 Q46 390 38 386", "none", 'stroke="#6f7fc0" stroke-width="4" stroke-linecap="round"') +
        rect(-2, 362, 40, 30, "#8fa2d8", 'rx="4"') + rect(-3, 360, 42, 5, "#a8b4d8", 'rx="2"') + rect(-2, 384, 40, 3, "#6f7fc0") + path("M10 372 q8 5 16 0", "none", 'stroke="#c8d2e8" stroke-width="2" stroke-linecap="round"') + path(star(18, 378, 3.5), "#fff1cd");
    },
    "plant-kittytopiary": function () { // A hedge clipped into a sitting cat, with a pink ribbon on.
      const bush = "#3f8a66";
      return path("M34 356 Q56 352 54 332 Q52 320 44 324", "none", 'stroke="' + bush + '" stroke-width="9" stroke-linecap="round"') + path("M4 306 L4 286 L18 298 Z M32 306 L32 286 L18 298 Z", bush) +
        oval(18, 346, 20, 24, bush) + circle(18, 310, 17, bush) +
        scatter(29, 34, function (roll, i) {
          const onHead = i < 11, a = roll(0, Math.PI * 2), r = Math.sqrt(roll(0, 1));
          const x = onHead ? 18 + Math.cos(a) * r * 12 : 18 + Math.cos(a) * r * 18, y = onHead ? (i % 2 ? roll(296, 303) : roll(318, 323)) : 346 + Math.sin(a) * r * 22; // Head leaves stay above and below the eyes, so her face stays clear.
          return circle(round(x), round(y), round(roll(1.4, 3)), i % 3 ? "#5fae86" : "#7fc49a");
        }) +
        path("M8 310 q4 4 8 0 M20 310 q4 4 8 0", "none", 'stroke="#1f4a38" stroke-width="2" stroke-linecap="round"') + path("M16 316 h4 l-2 2.4 Z", "#ffb3d1") +
        path("M18 328 L6 322 V334 Z M18 328 L30 322 V334 Z", "#ff8ecb") + circle(18, 328, 3, "#ffb3d1") + ART.pot("#c0764f");
    },
    "plant-bamboo": function () { // Lucky bamboo in a glass vase of water and pebbles, tied with a red ribbon.
      const stalks = [[8, 310, 334], [18, 288, 306], [28, 318, 340]];
      return rect(4, 348, 28, 44, "#bfe8ff", 'rx="5" opacity=".25"') + rect(5, 364, 26, 27, "#70b8eb", 'rx="4" opacity=".4"') +
        stalks.map(function (s) {
          const x = s[0]; let joints = "";
          for (let y = s[1] + 14; y < 384; y += 16) joints += rect(x - 3.6, y, 7.2, 2.4, "#3f8a66", 'rx="1"');
          return rect(x - 3, s[1], 6, 386 - s[1], "#7fc49a", 'rx="2"') + rect(x - 1.8, s[1], 1.4, 386 - s[1], "#a9dcb8") + joints + path("M" + x + " " + s[1] + " Q" + (x - 14) + " " + (s[1] - 10) + " " + (x - 20) + " " + (s[1] + 2) + " Q" + (x - 8) + " " + (s[1] + 4) + " " + x + " " + s[1] + " Z M" + x + " " + s[1] + " Q" + (x + 14) + " " + (s[1] - 10) + " " + (x + 20) + " " + (s[1] + 2) + " Q" + (x + 8) + " " + (s[1] + 4) + " " + x + " " + s[1] + " Z", "#5fae86");
        }).join("") +
        [[9, 388, 4], [18, 389, 3.2], [26, 388, 4]].map(function (p, i) { return oval(p[0], p[1], p[2], 2.6, ["#e8dff0", "#c9a8ff", "#ffdc83"][i]); }).join("") +
        rect(4, 348, 28, 44, "none", 'rx="5" stroke="#cfe8ff" stroke-width="1.6" opacity=".9"') + rect(1, 343, 34, 3.5, "#cfe8ff", 'rx="1.5" opacity=".75"') + path("M9 354 V382", "none", 'stroke="#fff" stroke-width="1.5" opacity=".5" stroke-linecap="round"') +
        rect(3, 346, 30, 4, "#e8556a", 'rx="1"') + path("M18 348 L7 342 V354 Z M18 348 L29 342 V354 Z", "#e8556a") + circle(18, 348, 3, "#ffdc83"); // The ribbon and its bow go on last, around the neck of the vase.
    },
    "plant-blossom": function () { // A little cherry tree in full bloom, petals falling around its glazed pot.
      return path("M18 372 Q10 354 18 342 Q24 332 16 318", "none", 'stroke="#54391f" stroke-width="7" stroke-linecap="round"') + path("M18 342 Q4 334 -6 322 M20 336 Q34 326 44 314", "none", 'stroke="#54391f" stroke-width="4" stroke-linecap="round"') +
        oval(-3, 312, 20, 15, "#e88ab5") + oval(43, 308, 20, 15, "#e88ab5") + oval(20, 298, 28, 20, "#f3a3c8") + oval(3, 298, 15, 12, "#ffc4e0") + oval(37, 296, 15, 12, "#ffc4e0") +
        scatter(37, 30, function (roll) { const a = roll(0, Math.PI * 2), r = Math.sqrt(roll(0, 1)); return circle(round(20 + Math.cos(a) * r * 42),round(304 + Math.sin(a) * r * 22), round(roll(1.2, 2.6)), roll(0, 1) > .5 ? "#fff0f7" : "#ffd9e6"); }) +
        [[-22, 340], [-10, 362], [52, 346], [60, 370], [38, 362]].map(function (p, i) { return oval(p[0], p[1], 2.8, 1.6, "#ffc4e0", 'transform="rotate(' + (i * 50) + " " + p[0] + " " + p[1] + ')" class="lp-twinkle" style="animation-delay:' + (i * .4) + 's"'); }).join("") +
        ART.pot("#3f6fae") + path("M6 380 q6 -4 12 0 t12 0", "none", 'stroke="#fff" stroke-width="2" stroke-linecap="round" opacity=".8"') + rect(2, 368, 32, 2, "#fff", 'opacity=".4" rx="1"');
    },
    "plant-lanternvine": function () { // Arching vines hung with glowing seed-pod lanterns.
      function pod(x, y, s, delay) {
        return line(x, y - 10 * s, x, y - 18 * s, "#3f8a66", 1.5) + circle(x, y, 13 * s, "#ff9d6b", 'opacity=".2"') +
          path("M" + x + " " + (y - 11 * s) + " Q" + (x + 11 * s) + " " + (y - 5 * s) + " " + (x + 8 * s) + " " + (y + 5 * s) + " Q" + (x + 4 * s) + " " + (y + 11 * s) + " " + x + " " + (y + 12 * s) + " Q" + (x - 4 * s) + " " + (y + 11 * s) + " " + (x - 8 * s) + " " + (y + 5 * s) + " Q" + (x - 11 * s) + " " + (y - 5 * s) + " " + x + " " + (y - 11 * s) + " Z", "#ffa55c", 'class="lp-twinkle" style="animation-delay:' + delay + 's"') +
          path("M" + x + " " + (y - 11 * s) + " V" + (y + 12 * s) + " M" + x + " " + (y - 11 * s) + " Q" + (x + 6 * s) + " " + y + " " + x + " " + (y + 12 * s) + " M" + x + " " + (y - 11 * s) + " Q" + (x - 6 * s) + " " + y + " " + x + " " + (y + 12 * s), "none", 'stroke="#d9652e" stroke-width="1"') + circle(x, y + 2 * s, 2.4 * s, "#fff1cd", 'opacity=".9"');
      }
      return path("M14 372 Q6 318 -18 318 M22 372 Q26 300 54 308 M18 372 Q18 306 22 286", "none", 'stroke="#3f8a66" stroke-width="3" stroke-linecap="round"') +
        [[-12, 330, -30], [12, 330, -70], [36, 318, 30], [50, 322, 60], [24, 296, 20], [10, 304, -60], [30, 344, 30], [2, 350, -40]].map(function (l) { return oval(l[0], l[1], 4, 9, "#5fae86", 'transform="rotate(' + l[2] + " " + l[0] + " " + l[1] + ')"'); }).join("") +
        pod(-18, 340, 1, 0) + pod(54, 330, 1, .6) + pod(4, 322, .75, 1.1) + pod(36, 346, .85, .3) + pod(22, 306, .7, 1.5) + ART.pot("#2a3a70") + rect(2, 368, 32, 2, "#ffdc83", 'opacity=".7"');
    },
    "plant-wishtree": function () { // The showpiece: a midnight tree whose leaves hold a sky of stars, hung with moon charm and wish ribbons.
      const ribbons = [[-16, 324, "#ff8ecb", 20], [-1, 330, "#70ebea", 24], [34, 330, "#ffdc83", 22]];
      return oval(18, 308, 52, 36, GLOW, 'opacity=".12"') +
        path("M18 374 Q2 356 14 340 Q24 328 12 316", "none", 'stroke="#3a2a60" stroke-width="9" stroke-linecap="round"') + path("M18 374 Q2 356 14 340 Q24 328 12 316", "none", 'stroke="#ffdc83" stroke-width="1.5" stroke-dasharray="5 4" stroke-linecap="round"') +
        path("M14 340 Q0 332 -12 322 M16 336 Q32 330 44 318", "none", 'stroke="#3a2a60" stroke-width="5" stroke-linecap="round"') +
        oval(-5, 317, 21, 14, "#1f2a6a") + oval(41, 315, 21, 14, "#1f2a6a") + oval(18, 322, 16, 10, "#1f2a6a") + oval(-8, 303, 15, 12, "#26337c") + oval(44, 301, 15, 12, "#26337c") + oval(18, 298, 30, 18, "#2a3a8a") + oval(5, 306, 16, 11, "#3a2a80") + oval(32, 304, 15, 10, "#3a2a80") + // Overlapping lobes, so it reads as a leafy crown.
        ribbons.map(function (r) { return path("M" + r[0] + " " + r[1] + " q-3 " + r[3] * .5 + " 1 " + r[3] + " l3 -3 l3 3 q-4 " + -r[3] * .5 + " -1 " + -r[3] + " Z", r[2], 'opacity=".95"'); }).join("") +
        scatter(53, 17, function (roll, i) {
          const a = roll(0, Math.PI * 2), rad = Math.sqrt(roll(0, 1)), x = round(18 + Math.cos(a) * rad * 42), y = round(308 + Math.sin(a) * rad * 22), big = i % 3 === 0;
          return path(star(x, y, big ? 5 : 3), big ? "#fff8dc" : "#ffe9a8", 'class="lp-twinkle" style="animation-delay:' + round(i * .21) + 's"');
        }) +
        line(52, 324, 52, 340, "#ffdc83", 1) + path("M52 340 A7.5 7.5 0 1 0 59 351 A6.5 6.5 0 1 1 52 340 Z", "#fff1cd", 'class="lp-neon"') + // The crescent charm hangs clear of the trunk, from the right bough.
        path("M2 372 H34 L30 392 H6 Z", "#d9b24a") + rect(0, 367, 36, 7, "#ffdc83", 'rx="2"') + path(star(18, 382, 6), "#fff8dc") + path(star(8, 382, 2.5), "#7a5a2a") + path(star(28, 382, 2.5), "#7a5a2a") + rect(5, 390, 26, 2.5, "#8a6a2a");
    },

    // Star Capsule rug
    // Rugs lie on the floor under Lumi, around 200,440. Round ones are drawn from above in a group squashed to lie flat.

    "rug-braid": function () { // A braided rag rug: seven coiled rings of cloth, each with a dashed line to look plaited.
      const cloth = ["#c85a8e", "#ffdc83", "#70b8eb", "#9c73b8", "#7fc49a", "#e98a5a", "#fff1e0"];
      let rings = circle(0, 0, 148, "#5a3a5a");
      cloth.forEach(function (colour, i) {
        const r = 138 - i * 19;
        rings += circle(0, 0, r, "none", 'stroke="' + colour + '" stroke-width="19"') + circle(0, 0, r, "none", 'stroke="#000000" stroke-opacity=".28" stroke-width="5" stroke-dasharray="11 9"'); // A ring, then its braid marks.
      });
      return group(rings + circle(0, 0, 11, "#ffdc83"), 'transform="translate(200 440) scale(1 .27)"');
    },

    "rug-daisy": function () { // A big soft daisy: twelve petals around a speckled sunny middle.
      let flower = "";
      for (let i = 0; i < 12; i++) flower += group(oval(0, -92, 25, 52, "#fff4d0") + path("M0 -48 V-128", "none", 'stroke="#e8cf96" stroke-width="3" opacity=".7"'), 'transform="rotate(' + i * 30 + ')"'); // A petal and its crease.
      flower += circle(0, 0, 56, "#d9962e") + circle(0, 0, 50, "#ffc64a");
      flower += scatter(61, 18, function (roll) { const a = roll(0, 6.28), r = roll(4, 44); return circle(round(Math.cos(a) * r), round(Math.sin(a) * r), round(roll(2.5, 5)), "#a8691f", 'opacity=".75"'); }); // The freckles.
      return group(flower, 'transform="translate(200 440) scale(1 .3)"');
    },

    "rug-kilim": function () { // A flat-woven runner: a row of stepped diamonds between pale bands, with fringe at both ends.
      let fringe = "";
      for (let y = 414; y <= 468; y += 4) fringe += "M72 " + y + " H62 M328 " + y + " H338 ";                       // The tassels, as one shape.
      let out = path(fringe, "none", 'stroke="#e9d8b0" stroke-width="1.6"') + rect(72, 412, 256, 58, "#9c4a3c", 'rx="2"') + rect(72, 412, 256, 7, "#e9d8b0") + rect(72, 463, 256, 7, "#e9d8b0") +
        path("M72 423 H328 M72 459 H328", "none", 'stroke="#3d7a7a" stroke-width="3"');
      for (let x = 92, i = 0; x <= 308; x += 36, i++) {
        out += path("M" + x + " 425 L" + (x + 16) + " 441 L" + x + " 457 L" + (x - 16) + " 441 Z", i % 2 ? "#ffcf6e" : "#3d7a7a") +
          path("M" + x + " 432 L" + (x + 9) + " 441 L" + x + " 450 L" + (x - 9) + " 441 Z", i % 2 ? "#9c4a3c" : "#e9d8b0") + circle(x, 441, 2.6, i % 2 ? "#e9d8b0" : "#9c4a3c");
      }
      return out;
    },

    "rug-cookie": function () { // One enormous cookie with a bite out of the side. The outline is a wobbly circle with a scoop cut from it.
      const roll = dice(66);
      let edge = "";
      for (let i = 0; i < 44; i++) {
        const a = i / 44 * 6.283, nibble = Math.abs(Math.atan2(Math.sin(a - 4.3), Math.cos(a - 4.3))); // How close this point is to the bitten corner, top left where furniture won't hide it.
        const r = nibble < .36 ? 104 + 40 * Math.pow(nibble / .36, 2) : 144 + roll(-4, 4);
        edge += (i ? " L" : "M") + round(Math.cos(a) * r) + " " + round(Math.sin(a) * r);
      }
      const chips = [[-92, -40, 15], [-44, -92, 13], [34, -82, 12], [96, -30, 14], [-108, 36, 12], [-58, 64, 16], [18, 34, 13], [64, 92, 12], [-14, 106, 11], [-30, -24, 12], [58, -14, 9]];
      return group(path(edge + "Z", "#c78a4f", 'stroke="#99602e" stroke-width="10" stroke-linejoin="round"') +
        path(edge + "Z", "none", 'stroke="#e6ac72" stroke-width="3" opacity=".6" transform="scale(.9)"') +                                                              // The paler baked rim.
        chips.map(function (c) { return circle(c[0], c[1], c[2], "#46271a") + circle(c[0] - 3, c[1] - 3, round(c[2] * .35), "#7d4f33"); }).join(""),                        // Chocolate chips, each with a shine.
        'transform="translate(200 440) scale(1 .3)"');
    },

    "rug-lilypad": function () { // A great lily pad with a notch, fanned veins and one pink water lily at the rim.
      let edge = "M0 0";
      for (let a = 0.2; a < 6.1; a += .2) edge += " L" + round(Math.cos(a) * 144) + " " + round(Math.sin(a) * 144);
      let veins = "";
      for (let a = 0.35; a < 6; a += .42) veins += "M0 0 L" + round(Math.cos(a) * 130) + " " + round(Math.sin(a) * 130) + " ";
      const lily = group(oval(0, 3, 26, 9, "#0e2b1e", 'opacity=".35"') + [-60, -36, -12, 12, 36, 60].map(function (a) { return group(path("M0 3 Q-10 -9 0 -24 Q10 -9 0 3Z", "#ff9fce"), 'transform="rotate(' + a + ')"'); }).join("") +
        [-24, 0, 24].map(function (a) { return group(path("M0 3 Q-7 -6 0 -16 Q7 -6 0 3Z", "#ffd6e8"), 'transform="rotate(' + a + ')"'); }).join("") + circle(0, -2, 3.6, "#ffdc83"), 'transform="translate(146 420) scale(1.2)"');
      return group(path(edge + " Z", "#3f8a5a") + path(veins, "none", 'stroke="#6fbd7f" stroke-width="3.5" opacity=".75"') + path(edge + " Z", "none", 'stroke="#2c6b45" stroke-width="7" stroke-linejoin="round"') + circle(60, -76, 8, "#cfe8ff", 'opacity=".7"') + circle(-30, 92, 6, "#cfe8ff", 'opacity=".7"') + circle(92, 54, 5, "#cfe8ff", 'opacity=".6"'),
        'transform="translate(200 440) scale(1 .3)"') + lily;
    },

    "rug-sheep": function () { // A sheep-shaped rug fast asleep: woolly lobes all round, a pale face at one end, little hooves tucked under.
      let lobes = "";
      for (let i = 0; i < 16; i++) { const a = i / 16 * 6.283; lobes += oval(round(200 + Math.cos(a) * 128), round(442 + Math.sin(a) * 30), 26, 13, i % 2 ? "#f4efe6" : "#fffaf0"); }
      let curls = "";
      for (let i = 0; i < 9; i++) curls += "M" + (120 + i * 22) + " " + (436 + (i % 2) * 12) + " a7 5 0 1 1 7 5 ";                      // The stitched curls of wool.
      return oval(200, 470, 136, 7, "#000000", 'opacity=".2"') +
        oval(92, 468, 8, 5, "#4a3a4a") + oval(142, 472, 8, 5, "#4a3a4a") + oval(256, 472, 8, 5, "#4a3a4a") + oval(306, 468, 8, 5, "#4a3a4a") + lobes + oval(200, 442, 128, 30, "#fffaf0") +
        path(curls, "none", 'stroke="#e0d2c0" stroke-width="2" stroke-linecap="round"') +
        // The face rests at the right, between Lumi and the bed, where neither hides it.
        oval(288, 414, 12, 5, "#e8d6bf", 'transform="rotate(24 288 414)"') + oval(252, 414, 12, 5, "#e8d6bf", 'transform="rotate(-24 252 414)"') +                                                                     // Floppy ears.
        oval(270, 424, 22, 17, "#f5e4cd") + circle(270, 409, 10, "#fffaf0") + path("M258 422 q5 4 9 0 M273 422 q5 4 9 0", "none", 'stroke="#4a3a4a" stroke-width="2" stroke-linecap="round"') + // Face, topknot and sleeping eyes.
        path("M267 432 q3 3 6 0", "none", 'stroke="#c88a8a" stroke-width="2" stroke-linecap="round"') + circle(256, 428, 3, "#ffb3c8", 'opacity=".7"') + circle(284, 428, 3, "#ffb3c8", 'opacity=".7"');
    },

    "rug-vinyl": function () { // A vinyl record to curl up on: fine grooves, a shine across it and a bright centre label.
      const grooves = [132, 122, 112, 102, 92, 82, 72, 62].map(function (r) { return circle(0, 0, r, "none", 'stroke="#3a3350" stroke-width="2"'); }).join("");
      return group(circle(0, 0, 146, "#1a1624") + grooves +
        path("M0 0 L-62 -129 A143 143 0 0 1 62 -129 Z", "#ffffff", 'opacity=".12"') + path("M0 0 L62 129 A143 143 0 0 1 -62 129 Z", "#ffffff", 'opacity=".12"') + // The two wedges of shine.
        circle(0, 0, 52, "#ff8ecb") + circle(0, 0, 46, "none", 'stroke="#fff1cd" stroke-width="2" stroke-dasharray="4 5"') + path(star(0, 0, 30), "#fff1cd") + circle(0, 0, 5, "#1a1624"), 'transform="translate(200 440) scale(1 .3)"') +
        circle(330, 440, 2, "#fff", 'opacity=".5"');
    },

    "rug-medallion": function () { // A proper woven carpet: border of tiny diamonds, deep starry field, and a lobed medallion at its heart.
      const lobed = function (base, wiggle) { // A closed shape with eight soft lobes, in the medallion's own coordinates.
        let d = "";
        for (let i = 0; i < 80; i++) { const a = i / 80 * 6.283, r = base + wiggle * Math.cos(8 * a); d += (i ? " L" : "M") + round(Math.cos(a) * r) + " " + round(Math.sin(a) * r); }
        return d + "Z";
      };
      let fringe = "";
      for (let y = 410; y <= 474; y += 3.2) fringe += "M60 " + round(y) + " H52 M340 " + round(y) + " H348 ";
      let border = "";
      for (let x = 74; x <= 326; x += 18.2) border += path("M" + round(x) + " 411 l4.5 4.5 l-4.5 4.5 l-4.5 -4.5 Z M" + round(x) + " 466 l4.5 4.5 l-4.5 4.5 l-4.5 -4.5 Z", "#e8c877");       // Diamonds running along top and bottom.
      return path(fringe, "none", 'stroke="#efe0c0" stroke-width="1.4"') + rect(60, 408, 280, 70, "#6e2342", 'rx="2"') + rect(66, 413, 268, 60, "none", 'stroke="#e8c877" stroke-width="1.6"') + rect(74, 421, 252, 44, "#1a2a55", 'stroke="#c0a0d0" stroke-width="1"') + border +
        path("M66 424 l5 5 l-5 5 l-5 -5 Z M66 452 l5 5 l-5 5 l-5 -5 Z M334 424 l5 5 l-5 5 l-5 -5 Z M334 452 l5 5 l-5 5 l-5 -5 Z", "#e8c877") +
        [[86, 428], [86, 458], [314, 428], [314, 458]].map(function (p) { return path(star(p[0], p[1], 7), "#ffdc83"); }).join("") +                                                                 // A star in each corner of the field.
        path("M86 443 Q128 424 160 443 M86 443 Q128 462 160 443 M314 443 Q272 424 240 443 M314 443 Q272 462 240 443", "none", 'stroke="#70b8eb" stroke-width="1.6" opacity=".8"') +                  // Vines reaching for the medallion.
        scatter(62, 12, function (roll) { return circle(round(roll(92, 308)), round(roll(424, 462)), round(roll(.7, 1.4)), "#fff8dc", 'opacity=".8"'); }) +
        group(path(lobed(80, 12), "#c85a8e") + path(lobed(64, 9), "#1a2a55") + path(lobed(50, 6), "#e8c877") + path(lobed(36, 4), "#6e2342") + path(star(0, 0, 30), "#fff1cd") + [0, 1, 2, 3, 4, 5, 6, 7].map(function (i) { const a = i / 8 * 6.283; return circle(round(Math.cos(a) * 72), round(Math.sin(a) * 72), 3.4, "#ffdc83"); }).join(""), 'transform="translate(200 443) scale(1.25 .24)"');
    },

    "rug-koi": function () { // A round pond rug with two koi circling each other, ringed with pebbles and small lily leaves.
      const koi = function (x, y, angle, skin, patch) { // A fish pointing along its own x axis.
        return group(path("M-52 0 L-86 -20 Q-74 0 -86 20 Z", skin) + oval(14, -20, 20, 7, skin, 'transform="rotate(-24 14 -20)"') + oval(14, 20, 20, 7, skin, 'transform="rotate(24 14 20)"') +
          path("M56 0 Q22 -25 -30 -11 Q-52 -4 -58 0 Q-52 4 -30 11 Q22 25 56 0Z", skin) +
          circle(22, -6, 8, patch) + circle(-8, 4, 10, patch) + circle(-32, -2, 6, patch) + circle(40, -8, 3, "#1a2a3a") + circle(40, 8, 3, "#1a2a3a"),
          'transform="translate(' + x + " " + y + ") rotate(" + angle + ')"');
      };
      const pad = function (x, y, r) { return path("M" + x + " " + y + " L" + (x + r) + " " + (y - r * .2) + " A" + r + " " + r + " 0 1 1 " + (x + r * .8) + " " + (y + r * .6) + " Z", "#4f9a62", 'stroke="#2c6b45" stroke-width="2"'); };
      return group(circle(0, 0, 148, "#9c8a64") + circle(0, 0, 140, "none", 'stroke="#c9b58a" stroke-width="14" stroke-dasharray="14 7"') + circle(0, 0, 130, "#1d6a7c") + circle(0, 0, 112, "#2a8d96") +
        circle(0, 0, 90, "none", 'stroke="#7fd6c8" stroke-width="2.5" opacity=".6"') + circle(0, 0, 54, "none", 'stroke="#7fd6c8" stroke-width="2.5" opacity=".5"') +                                 // Water, deepening at the rim, with soft ripples.
        koi(0, -72, 0, "#ff9d4a", "#fff4e0") + koi(0, 72, 180, "#fff4e0", "#ff5a4a") +
        pad(-108, -48, 18) + pad(106, 46, 16) + pad(-84, 76, 12) +
        circle(-96, 14, 3, "#cfe8ff", 'opacity=".75"') + circle(92, -42, 3, "#cfe8ff", 'opacity=".75"') + circle(20, 92, 2.5, "#cfe8ff", 'opacity=".7"'),
        'transform="translate(200 440) scale(1 .3)"');
    },

    "rug-wishing-circle": function () { // A circle of light woven into the floor: rings that follow Lumi's glow, a big eight-pointed star, and twinkling points around the rim.
      let ticks = "", marks = "", lamps = "";
      for (let i = 0; i < 48; i++) { const a = i / 48 * 6.283, c = Math.cos(a), s = Math.sin(a), inner = i % 4 ? 124 : 116; ticks += "M" + round(c * inner) + " " + round(s * inner) + " L" + round(c * 134) + " " + round(s * 134) + " "; } // The measuring marks around the rim, as one shape.
      for (let i = 0; i < 12; i++) { const a = i / 12 * 6.283; marks += path(star(round(Math.cos(a) * 140), round(Math.sin(a) * 140), 13), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .17).toFixed(2) + 's"'); } // Twelve points of light on the rim.
      for (let i = 0; i < 8; i++) { const a = i / 8 * 6.283 + .39; lamps += circle(round(Math.cos(a) * 88), round(Math.sin(a) * 88), 5, GLOW, 'class="lp-twinkle" style="animation-delay:' + (i * .25) + 's"'); } // Eight lamps inside it.
      return oval(200, 442, 158, 42, GLOW, 'opacity=".14"') + group(circle(0, 0, 146, "#0d1238") + circle(0, 0, 146, "none", 'stroke="' + GLOW + '" stroke-width="5" class="lp-neon"') + path(ticks, "none", 'stroke="' + GLOW + '" stroke-width="2.5" opacity=".75"') +
        circle(0, 0, 112, "none", 'stroke="#ffdc83" stroke-width="2.5"') + circle(0, 0, 62, "none", 'stroke="' + GLOW + '" stroke-width="2.5" opacity=".8"') +
        path("M0 -108 L76 -76 L108 0 L76 76 L0 108 L-76 76 L-108 0 L-76 -76Z", "none", 'stroke="#ffdc83" stroke-width="2.5"') +
        path("M0 -108 L0 108 M108 0 L-108 0 M76 -76 L-76 76 M76 76 L-76 -76", "none", 'stroke="#ffdc83" stroke-width="1.6" opacity=".7"') +
        path(star(0, 0, 56), "#ffe9a8", 'opacity=".9" class="lp-neon"') + path(star(0, 0, 26), "#fff") + marks + lamps, 'transform="translate(200 442) scale(1 .3)"') +
        scatter(63, 7, function (roll, i) { return path(star(round(roll(80, 320)), round(roll(404, 412)), round(roll(2.4, 4.4))), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .29 % 2).toFixed(2) + 's"'); }); // A few sparks rising off it.
    },

    // Star Capsule shelf
    "shelf-teacups": function () { // A round teapot and a little stack of teacups.
      const cup = function (x, y, colour) { return path("M" + (x - 9) + " " + y + " h18 q-1 9 -9 9 q-8 0 -9 -9 Z", colour) + path("M" + (x + 9) + " " + (y + 2) + " q6 1 3 6 q-2 2 -5 1", "none", 'stroke="' + colour + '" stroke-width="1.8"') + rect(x - 8, y + 9, 16, 2, "#fff1e0", 'rx="1"'); };
      return oval(322, 186, 16, 14, "#c9d6ff") + oval(322, 192, 15, 8, "#a9b8e8") + path("M306 182 q-10 -2 -10 -9 q0 -4 4 -4 M338 186 q8 1 9 -7 l4 -3 l-2 7 q-2 6 -10 8", "none", 'stroke="#c9d6ff" stroke-width="3" stroke-linecap="round"') + oval(322, 172, 8, 3, "#a9b8e8") + circle(322, 168, 2.5, "#ffdc83") + path(star(322, 187, 4), "#fff") +
        cup(352, 189, "#ff8ecb") + cup(352, 178, "#ffdc83") + cup(352, 167, "#70ebea") + ART.board();
    },
    "shelf-jars": function () { // Three screw-top sweet jars with different sweets inside.
      function jar(x, top, lid, sweets) { // Glass body drawn over its sweets, with a lid.
        return sweets + rect(x - 11, top, 22, 200 - top, "#bfe8ff", 'rx="4" opacity=".28" stroke="#d6f0ff" stroke-width="1.5"') + rect(x - 9, top - 5, 18, 6, lid, 'rx="1.5"') + line(x - 7, top + 5, x - 7, 192, "#fff", 1.2, 'opacity=".55"');
      }
      const round1 = [[-6, 194], [0, 194], [6, 194], [-3, 188], [4, 188], [0, 182]];
      return jar(312, 170, "#ff8ecb", round1.map(function (p, i) { return circle(312 + p[0], p[1], 3.2, ["#ff8ecb", "#ffdc83", "#70ebea"][i % 3]); }).join("")) +
        jar(338, 160, "#70ebea", [0, 1, 2, 3].map(function (i) { return rect(330, 192 - i * 9, 16, 7, ["#c9a8ff", "#ffb3d1", "#fff1cd", "#7fc49a"][i], 'rx="3.5"'); }).join("")) +
        jar(364, 174, "#ffdc83", [[-7, 196], [-1, 196], [5, 196], [-4, 190], [3, 190]].map(function (p) { return path(star(364 + p[0], p[1], 3.8), "#ffe9a8"); }).join("")) + ART.board();
    },
    "shelf-shells": function () { // A beachcomber's row: a scallop, a spiral whelk, a starfish and a pebble stack.
      const ribs = [-38, -19, 0, 19, 38].map(function (a) { return "M308 196 L" + round(308 + 13 * Math.sin(a * Math.PI / 180)) + " " + round(196 - 18 * Math.cos(a * Math.PI / 180)); }).join(" ");
      return path("M296 192 Q294 172 308 170 Q322 172 320 192 Q314 200 308 200 Q302 200 296 192 Z", "#ffc2b0") + path(ribs, "none", 'stroke="#d98a7a" stroke-width="1.2"') + rect(302, 196, 12, 4, "#f0a080", 'rx="1.5"') +
        path("M334 200 Q324 190 332 176 L342 164 L346 178 Q352 190 344 200 Z", "#ffe0b0") + path("M330 184 Q338 190 346 182 M331 192 Q338 196 346 190 M336 174 Q340 178 345 174", "none", 'stroke="#c99a60" stroke-width="1.4"') +
        path(star(358, 192, 8), "#ff8e8e") + circle(358, 192, 1.4, "#ffdc83") + oval(372, 197, 7, 3.5, "#a8b4c4") + oval(372, 192, 5.5, 3, "#c9d2de") + oval(372, 187.5, 3.5, 2.2, "#e6ecf2") + ART.board();
    },
    "shelf-ducks": function () { // A parade of rubber ducklings: mum in front, then three small ones with little hats.
      function duck(x, s, body, beak, extra) { // Facing left, in a local box that sits on the shelf.
        return group(oval(0, -7, 11, 7, body) + circle(-7, -14, 6, body) + path("M-13 -14 l-5 1 l5 2 Z", beak) + circle(-8.5, -15.5, 1, "#2a1a3a") + path("M9 -9 q5 -3 3 2 q-1 3 -5 3 Z", body) + (extra || ""), 'transform="translate(' + x + " 200) scale(" + s + ')"');
      }
      return duck(309, 1.15, "#ffdc83", "#ff9d6b") + duck(330, .78, "#ffe9a8", "#ff9d6b", path("M-13 -21 h12 l-6 -8 Z", "#70ebea")) + duck(347, .72, "#ffd0e8", "#ff9d6b", rect(-13, -20, 11, 2.4, "#c9a8ff", 'rx="1"') + rect(-11, -26, 7, 6, "#c9a8ff", 'rx="1"')) + duck(364, .68, "#c9f0ff", "#ffb84a", path(star(-7, -23, 5), "#ffdc83")) + ART.board();
    },
    "shelf-records": function () { // A little record player with a turning disc and a stack of sleeves leaning next to it.
      return rect(297, 182, 44, 18, "#7a4a2a", 'rx="2"') + rect(297, 182, 44, 3.5, "#a8743f", 'rx="1.5"') + oval(318, 181, 17, 4.5, "#1a1228") + oval(318, 181, 12, 3.2, "none", 'stroke="#3a3048" stroke-width="1"') + oval(318, 181, 4.5, 1.6, "#ff8ecb", 'class="lp-twinkle"') + path("M338 180 L330 176 L324 180", "none", 'stroke="#d8cfe4" stroke-width="1.6" stroke-linejoin="round"') + circle(338, 180, 2, "#a8b4c4") + circle(304, 191, 2.4, "#ffdc83") + circle(311, 191, 2.4, "#ffdc83") + rect(322, 188, 15, 2, "#3a2452") + rect(322, 193, 15, 2, "#3a2452") +
        rect(346, 165, 8, 35, "#70b8eb", 'rx="1"') + rect(355, 168, 8, 32, "#ff8ecb", 'rx="1"') + path("M365 200 L370 168 L377 170 L372 200 Z", "#ffdc83") + path("M358 178 v10 M350 174 v14 M371 178 l-1 8", "none", 'stroke="#fff" stroke-width="1" opacity=".55"') + ART.board();
    },
    "shelf-windups": function () { // Tin wind-up toys: a robot with a key in its back, a spinning top and a hopping chick.
      return rect(303, 176, 22, 18, "#a8b4c4", 'rx="3"') + rect(305, 194, 7, 6, "#6a7484") + rect(316, 194, 7, 6, "#6a7484") + rect(308, 165, 12, 11, "#c9d2de", 'rx="2"') + circle(311, 170, 1.8, "#70ebea", 'class="lp-twinkle"') + circle(317, 170, 1.8, "#70ebea") + rect(310, 182, 8, 3, "#ff8ecb") + rect(310, 187, 8, 3, "#ffdc83") + path("M326 182 h7 M333 178 v8", "none", 'stroke="#d9b24a" stroke-width="2"') + line(314, 165, 314, 160, "#6a7484", 1.5) + circle(314, 159, 1.8, "#ff7a8a") +
        path("M345 200 L335 186 Q345 176 355 186 Z", "#e8556a") + path("M337.5 189 Q345 183 352.5 189 M340 193 Q345 189 350 193", "none", 'stroke="#fff1cd" stroke-width="2"') + rect(343.5, 170, 3, 9, "#d9b24a", 'rx="1"') + circle(345, 169, 2.4, "#ff8ecb") +
        group(oval(364, 192, 10, 7, "#ffdc83") + circle(358, 184, 6, "#ffe9a8") + path("M352 184 l-5 1 l5 2 Z", "#ff9d6b") + circle(356.5, 183, 1, "#2a1a3a") + path("M369 190 q7 -4 5 2 M369 193 q6 -1 4 3", "none", 'stroke="#d9b24a" stroke-width="2"') + rect(358, 198, 3, 2, "#ff9d6b") + rect(366, 198, 3, 2, "#ff9d6b"), 'transform="translate(3 0)"') + ART.board();
    },
    "shelf-terrarium": function () { // A glass cloche over moss, ferns and a tiny red mushroom.
      return oval(337, 197, 30, 4.5, "#3a5a52") + path("M314 197 Q314 186 337 185 Q360 186 360 197 Z", "#5fae86") + path("M324 189 q-2 -10 -8 -12 M324 189 q0 -11 4 -14 M324 189 q3 -9 8 -10 M352 191 q-1 -9 -5 -11 M352 191 q2 -9 6 -10", "none", 'stroke="#3f8a66" stroke-width="2" stroke-linecap="round"') +
        rect(342, 184, 3, 8, "#fff1e0") + path("M336 186 Q343.5 174 351 186 Z", "#e8556a") + circle(341, 182, 1.2, "#fff") + circle(346, 183, 1, "#fff") + path("M308 200 V172 Q308 152 337 152 Q366 152 366 172 V200 Z", "#bfe8ff", 'opacity=".2" stroke="#d6f0ff" stroke-width="1.8"') + path("M316 172 Q316 160 330 158", "none", 'stroke="#fff" stroke-width="1.8" opacity=".6" stroke-linecap="round"') + circle(337, 150, 2.6, "#d9b24a") + circle(324, 170, 1.2, "#8be9a8", 'class="lp-twinkle"') + circle(352, 168, 1, "#8be9a8", 'class="lp-twinkle" style="animation-delay:.9s"') + ART.board();
    },
    "shelf-potions": function () { // An alchemist's corner: three corked flasks of glowing liquid, bubbles rising from the pink and green ones.
      function flask(x, h, r, colour, delay) { // A round-bellied bottle with a cork: the coloured liquid fills the bottom two-thirds, with a glint.
        const y = 200 - r, neck = 200 - r * 2 - h;
        return rect(x - 3, neck, 6, h + 4, "#d6f0ff", 'opacity=".55"') + rect(x - 3.5, neck - 5, 7, 5, "#a8743f", 'rx="1"') + circle(x, y, r, "#d6f0ff", 'opacity=".32" stroke="#e6f6ff" stroke-width="1.2"') + path("M" + (x - r + 1) + " " + (y + 1) + " A" + (r - 1) + " " + (r - 1) + " 0 0 0 " + (x + r - 1) + " " + (y + 1) + " Q" + x + " " + (y - 3) + " " + (x - r + 1) + " " + (y + 1) + " Z", colour, 'class="lp-twinkle" style="animation-delay:' + delay + 's"') + oval(x, 200, r * .7, 1.5, colour, 'opacity=".3"') + circle(x - r * .4, y - r * .4, r * .2, "#fff", 'opacity=".8"') + path(star(x, y + r * .35, r * .3), "#fff", 'opacity=".75"');
      }
      return flask(310, 10, 12, "#ff6fb0", 0) + flask(336, 18, 9, "#70ebea", .5) + flask(362, 8, 13, "#b9f05a", 1) +
        scatter(161, 5, function (roll, i) { const x = round(310 + roll(-6, 6)), y = round(174 - i * 5); return circle(x, y, round(roll(.8, 1.6)), "#ffb3d9", 'opacity=".8" class="lp-twinkle" style="animation-delay:' + i * .4 + 's"'); }) +
        scatter(162, 5, function (roll, i) { const x = round(362 + roll(-6, 6)), y = round(170 - i * 4); return circle(x, y, round(roll(.8, 1.4)), "#d2f58a", 'opacity=".8" class="lp-twinkle" style="animation-delay:' + i * .5 + 's"'); }) + ART.board();
    },
    "shelf-explorer": function () { // A traveller's keepsakes: a tilted globe on a brass stand, a ship in a bottle, and a spyglass.
      return path("M300 200 h24 l-3 -5 h-18 Z", "#8a5a2a") + rect(310, 188, 4, 8, "#d9b24a") + path("M312 196 A20 20 0 0 1 300 164", "none", 'stroke="#d9b24a" stroke-width="2"') + circle(312, 176, 14, "#5aa0d8") + path("M302 170 q4 -5 8 -2 q2 3 -2 5 q-3 2 -6 -3 Z M314 166 q5 -1 8 3 q1 5 -5 4 q-4 -2 -3 -7 Z M308 183 q4 -3 7 0 q1 3 -3 4 q-3 0 -4 -4 Z", "#7fc49a") + path("M298 176 Q312 184 326 176", "none", 'stroke="#fff" stroke-width=".8" opacity=".4"') +
        rect(330, 183, 42, 17, "#bfe8ff", 'rx="7" opacity=".32" stroke="#d6f0ff" stroke-width="1.5"') + rect(372, 188, 5, 7, "#a8743f", 'rx="1"') + path("M333 197 Q351 193 369 197 V198 H333 Z", "#1a5c8c") + path("M340 195 h20 l-4 3 h-12 Z", "#8a5a2a") + path("M349 195 V184 M350 186 l9 7 h-9 Z M348 186 l-7 7 h7 Z", "#fff") + line(349, 195, 349, 184, "#6a4a30", 1) +
        rect(334, 176, 19, 7, "#8a6220", 'rx="2"') + rect(353, 177.5, 14, 4, "#d9b24a", 'rx="1.5"') + rect(367, 176.5, 3, 6, "#8a6220", 'rx="1"') + oval(334, 179.5, 1.6, 3.5, "#bfe8ff") + path("M340 176 v7 M346 176 v7", "none", 'stroke="#d9b24a" stroke-width="1"') + ART.board();
    },
    "shelf-dragon": function () { // A very small dragon asleep on a heap of gold, one wing folded over and a curl of sleepy smoke.
      const coins = scatter(171, 22, function (roll) { const x = round(roll(302, 372)), y = round(roll(186, 197)); return "M" + round(x - 4) + " " + y + " a4 2 0 1 0 8 0 a4 2 0 1 0 -8 0Z "; });
      return path("M298 200 Q306 182 322 178 Q340 172 356 180 Q372 184 378 200 Z", "#d9a02e") + path(coins, "#ffdc83", 'stroke="#b8862e" stroke-width=".6"') +
        path("M322 196 Q320 172 340 170 Q362 170 362 188 Q362 196 354 196 H330 Q328 196 326 200 Z", "#7fd6a8") + path("M358 188 q14 6 12 -8 q8 12 -4 16 q-6 2 -12 -2 Z", "#7fd6a8") + // Body and curled tail.
        path("M344 172 q8 -12 18 -10 q-4 8 -10 12 Z M338 174 q-2 -14 6 -18 q6 8 2 16 Z", "#4fae88") + path("M341 171 L352 178 M347 169 L357 176", "none", 'stroke="#3a8a6a" stroke-width="1"') + // Folded wing.
        circle(326, 176, 11, "#8fe0b4") + path("M318 169 l-3 -8 l6 4 Z M330 167 l3 -8 l3 8 Z", "#4fae88") + path("M319 178 q3 3 7 1", "none", 'stroke="#2a4a3a" stroke-width="1.4" stroke-linecap="round"') + path("M332 174 q3 3 6 1", "none", 'stroke="#2a4a3a" stroke-width="1.4" stroke-linecap="round"') + oval(323, 183, 3, 2, "#ffb3d1") + circle(315, 176, 1, "#2a4a3a") + // Head, horns, closed eyes, a rosy cheek.
        path("M312 172 q-5 -4 -3 -8 q3 -3 1 -8", "none", 'stroke="#fff" stroke-width="1.6" stroke-linecap="round" opacity=".55" class="lp-twinkle"') + // Smoke from the nostril.
        path(star(366, 174, 4), "#fff8dc", 'class="lp-twinkle" style="animation-delay:.5s"') + path(star(304, 184, 3), "#fff8dc", 'class="lp-twinkle" style="animation-delay:1.1s"') + path("M319 166 l1.5 -7 l3 4.5 l3 -6 l3 6 l3 -4.5 l1.5 7 Z", "#ffdc83", 'class="lp-neon"') + circle(326.5, 163, 1, "#ff6fb0", 'class="lp-twinkle"') + ART.board(); // ...and a very small crown, because of course.
    },

    // Star Capsule sidetable
    "table-cake": function () { // A strawberry layer cake on a glass stand.
      return ART.table("#c0764f") + oval(335, 323, 14, 2.5, "#e8dff0") + rect(332, 313, 6, 10, "#e8dff0") + oval(335, 313, 26, 4, "#fff8f0") +
        rect(315, 291, 40, 21, "#ffd9e6", 'rx="3"') + rect(315, 300, 40, 3.5, "#e8556a") +
        path("M315 294 Q315 287 322 287 H348 Q355 287 355 294 V297 q-4 5 -7 0 q-4 7 -8 0 q-4 5 -8 0 q-4 7 -8 0 q-3 4 -9 0 Z", "#fff8f0") +
        [[325, 283], [335, 281], [345, 283]].map(function (b) { return path("M" + (b[0] - 5) + " " + (b[1] - 1) + " Q" + b[0] + " " + (b[1] - 5) + " " + (b[0] + 5) + " " + (b[1] - 1) + " Q" + (b[0] + 4) + " " + (b[1] + 6) + " " + b[0] + " " + (b[1] + 7) + " Q" + (b[0] - 4) + " " + (b[1] + 6) + " " + (b[0] - 5) + " " + (b[1] - 1) + " Z", "#e8556a") + path("M" + (b[0] - 3) + " " + (b[1] - 4) + " L" + b[0] + " " + (b[1] - 7) + " L" + (b[0] + 3) + " " + (b[1] - 4) + " Z", "#5fae86"); }).join("");
    },
    "table-toadstool": function () { // The table IS a big flat toadstool, with a snail asleep on top.
      return path("M326 362 Q330 346 330 332 H342 Q342 346 346 362 Z", "#fff1e0") + path("M300 330 Q300 312 320 308 H352 Q372 312 372 330 Q336 342 300 330 Z", "#e8556a") + path("M304 332 Q336 342 368 332 Q336 348 304 332 Z", "#f6dcc0") +
        circle(314, 322, 4.5, "#fff") + circle(334, 326, 3.5, "#fff") + circle(356, 322, 5, "#fff") + oval(345, 314, 3.5, 2, "#fff") + oval(322, 312, 3, 1.8, "#fff") +
        path("M322 309 Q324 302 334 302 H348 Q352 302 354 296", "none", 'stroke="#d9c79a" stroke-width="5" stroke-linecap="round"') + circle(354, 293, 3, "#d9c79a") + path("M355 289 L358 284 M352 289 L352 283", "none", 'stroke="#d9c79a" stroke-width="1.3" stroke-linecap="round"') +
        circle(336, 294, 11, "#c0764f") + path("M336 294 m0 -7 a7 7 0 1 1 -7 7 a5 5 0 1 0 5 -5 a3 3 0 1 1 3 3", "none", 'stroke="#7a4a2a" stroke-width="1.6"') + circle(355, 293, .9, "#2a1a3e");
    },
    "table-typewriter": function () { // A teal typewriter halfway through a love poem.
      let keys = "";
      for (let row = 0; row < 3; row++) for (let col = 0; col < 8 - row; col++) keys += circle(320 + row * 2 + col * 4.4, 310 + row * 4.4, 1.5, "#fff8f0");
      return ART.table("#7a5a8c") + rect(322, 280, 26, 20, "#fff8f0") + path("M326 286 H344 M326 291 H340", "none", 'stroke="#8a7aa8" stroke-width="1.2"') + path("M336 296 c-2 -2 -4 1 0 3 c4 -2 2 -5 0 -3 Z", "#e8556a") +
        rect(316, 297, 38, 6, "#2a4a48", 'rx="3"') + circle(314, 300, 3.5, "#d9b24a") + circle(356, 300, 3.5, "#d9b24a") +
        path("M314 302 H356 L364 324 H306 Z", "#3a8a84") + keys + rect(322, 322, 26, 2, "#2a4a48");
    },
    "table-clock": function () { // A twin-bell alarm clock that has clearly been dozing too.
      return ART.table("#5a4a70") + path("M318 322 l-4 3 M352 322 l4 3", "none", 'stroke="#d9b24a" stroke-width="3" stroke-linecap="round"') +
        circle(320, 284, 8.5, "#ffdc83") + circle(350, 284, 8.5, "#ffdc83") + rect(333, 274, 4, 10, "#d9b24a") + path("M328 274 H342", "none", 'stroke="#d9b24a" stroke-width="3" stroke-linecap="round"') +
        circle(335, 305, 20, "#e8556a") + circle(335, 305, 15, "#fff8f0") +
        [0, 90, 180, 270].map(function (a) { return rect(334, 292, 2, 3.5, "#8a7aa8", 'transform="rotate(' + a + ' 335 305)"'); }).join("") +
        path("M335 305 V295 M335 305 L342 309", "none", 'stroke="#2a1a3e" stroke-width="1.8" stroke-linecap="round"') + circle(335, 305, 1.8, "#e8556a") +
        path("M358 278 h5 l-5 6 h5 M365 268 h4 l-4 5 h4", "none", 'stroke="#c9a8ff" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"');
    },
    "table-gramophone": function () { // A wind-up gramophone with a great golden horn, playing a tune.
      function note(x, y, fill, delay) { return circle(x, y, 2.4, fill, 'class="lp-twinkle" style="animation-delay:' + delay + 's"') + path("M" + (x + 2.3) + " " + y + " V" + (y - 10) + " l5 2", "none", 'stroke="' + fill + '" stroke-width="1.4"'); }
      return ART.table("#6b4a2f") + rect(318, 308, 40, 16, "#8a5a2a", 'rx="2"') + rect(318, 319, 40, 5, "#6b4a2f") + oval(338, 308, 19, 3.5, "#2a1a3e") + oval(338, 307.5, 6, 1.2, "#e8556a") +
        rect(362, 312, 7, 3, "#d9b24a") + circle(370, 313, 3, "#d9b24a") +
        path("M344 306 C336 302 326 298 312 296 L300 288 L322 268 L328 282 C332 292 340 298 346 302 Z", "#d9b24a") + oval(310, 279, 17, 7, "#8a6a2a", 'transform="rotate(-42 310 279)"') + oval(310, 279, 12, 4, "#4a3524", 'transform="rotate(-42 310 279)"') +
        path("M344 306 C336 302 326 298 312 296 M346 302 C340 298 332 292 328 282", "none", 'stroke="#ffe9a8" stroke-width="1.2"') +
        note(340, 282, "#ff8ecb", 0) + note(354, 292, "#70ebea", .5) + note(362, 278, "#ffdc83", 1);
    },
    "table-terrarium": function () { // A little world under a glass dome: moss, mushrooms and one firefly.
      return ART.table("#5a4a70") + rect(312, 318, 46, 6, "#8a5a2a", 'rx="2"') + path("M316 318 Q316 288 335 284 Q354 288 354 318 Z", "#23402f") + path("M316 318 Q326 300 338 308 Q348 302 354 318 Z", "#5fae86") +
        path("M322 318 Q330 310 340 316 Q348 312 352 318 Z", "#7fc49a") + rect(328, 300, 2.5, 10, "#fff1e0") + path("M323 302 Q329 293 336 302 Z", "#e8556a") + rect(344, 306, 2, 7, "#fff1e0") + path("M340 307 Q345 300 350 307 Z", "#ffb84a") +
        path("M320 316 q-1 -8 -4 -12 M320 316 q2 -7 1 -14", "none", 'stroke="#3f8a66" stroke-width="1.5" stroke-linecap="round"') +
        path("M314 318 V292 A21 21 0 0 1 356 292 V318 Z", "#bfe8ff", 'opacity=".22" stroke="#cfe8ff" stroke-width="2"') + path("M320 294 A15 15 0 0 1 332 278", "none", 'stroke="#fff" stroke-width="2.5" opacity=".55" stroke-linecap="round"') + circle(335, 269, 3.5, "#d9b24a") +
        circle(344, 292, 5, "#ffdc83", 'opacity=".25"') + circle(344, 292, 1.8, "#ffe9a8", 'class="lp-twinkle"');
    },
    "table-arcade": function () { // A tiny arcade cabinet with a pixel heart on its glowing screen.
      const pixels = ["01100110", "11111111", "11111111", "01111110", "00111100", "00011000"];
      let heart = "";
      pixels.forEach(function (row, y) { row.split("").forEach(function (bit, x) { if (bit === "1") heart += rect(325 + x * 2.5, 283 + y * 2.5, 2.6, 2.6, "#ff8ecb"); }); });
      return ART.table("#3a3350") + path("M316 324 V300 L320 272 Q322 266 330 266 H346 Q352 266 352 272 V324 Z", "#4b2a8a") + path("M316 324 V300 L320 272 Q320 268 322 267 L322 324 Z", "#3a2070") +
        rect(323, 266, 28, 9, "#70ebea", 'rx="2" class="lp-neon"') + path(star(337, 270.5, 3.5), "#fff8dc") +
        rect(323, 279, 26, 22, "#120f2a", 'rx="2"') + heart + rect(320, 303, 32, 10, "#6d4bb0") + line(329, 309, 329, 303, "#a8b4c4", 1.8) + circle(329, 303, 2.6, "#e8556a") + circle(340, 308, 2, "#ffdc83") + circle(347, 308, 2, "#7fc49a") + rect(330, 316, 12, 5, "#2a1a3e", 'rx="1"') + rect(333, 318, 6, 1.5, "#ffdc83");
    },
    "table-potions": function () { // An alchemy set: flask over a small flame, glass pipe dripping into a bubbling jar, a rack of tubes.
      return ART.table("#7a5a3a") +
        oval(322, 316, 9, 5, "#ffb84a", 'opacity=".25"') + path("M322 312 q5 6 0 11 q-5 -5 0 -11 Z", "#ffb84a", 'class="lp-twinkle"') + path("M313 310 L310 324 M331 310 L334 324", "none", 'stroke="#8a7aa8" stroke-width="2"') + rect(311, 308, 22, 2.5, "#8a7aa8") +
        rect(318, 279, 8, 11, "#bfe8ff", 'opacity=".4" stroke="#cfe8ff" stroke-width="1.5"') + circle(322, 297, 13, "#bfe8ff", 'opacity=".32" stroke="#cfe8ff" stroke-width="1.5"') + path("M310 300 Q322 294 334 300 A13 13 0 0 1 310 300 Z", "#ff5fa8", 'class="lp-neon"') +
        path("M322 279 Q322 269 332 269 H342 Q350 269 350 282", "none", 'stroke="#cfe8ff" stroke-width="2.5" opacity=".7"') + rect(319, 276, 6, 4, "#a8743f") + // The glass pipe from the flask's cork down into the jar.
        path("M350 280 V290 L363 318 Q364 324 358 324 H342 Q336 324 337 318 L350 290", "#bfe8ff", 'opacity=".3" stroke="#cfe8ff" stroke-width="1.5"') + path("M343 310 H357 L362 320 Q362 323 358 323 H342 Q338 323 338 320 Z", "#70ebea") + circle(350, 298, 1.6, "#ff8ecb", 'class="lp-twinkle" style="animation-delay:.5s"') +
        [[318, 294, 1.6], [326, 291, 1.2], [322, 288, 1], [346, 314, 1.5], [353, 306, 1.2], [349, 300, 1]].map(function (b, i) { return circle(b[0], b[1], b[2], "#fff", 'opacity=".85" class="lp-twinkle" style="animation-delay:' + (i * .3) + 's"'); }).join("");
    },
    "table-musicbox": function () { // An open music box lined in velvet, with a tiny dancer twirling before a mirror.
      return rect(314, 328, 4, 34, "#d9b24a") + rect(354, 328, 4, 34, "#d9b24a") + rect(316, 346, 40, 3, "#d9b24a", 'opacity=".7"') + rect(304, 324, 64, 6, "#d9b24a", 'rx="2"') + path("M304 328 q8 8 16 0 q8 8 16 0 q8 8 16 0 q8 8 16 0 V324 H304 Z", "#fff8f0") + // Legs first, so the lace cloth hangs over them.
        path("M312 300 V274 Q312 270 316 270 H354 Q358 270 358 274 V300 Z", "#6d2f6a", 'stroke="#d9b24a" stroke-width="2"') + oval(335, 285, 17, 12, "#cfe8ff", 'opacity=".55" stroke="#ffdc83" stroke-width="1.5"') + path("M324 280 Q330 276 336 277", "none", 'stroke="#fff" stroke-width="1.5" opacity=".7" stroke-linecap="round"') +
        rect(310, 302, 50, 22, "#8a3f88", 'rx="2"') + rect(310, 300, 50, 4, "#d9b24a", 'rx="1"') + rect(310, 320, 50, 4, "#d9b24a", 'rx="1"') + path(star(335, 313, 5), "#ffe9a8") + path(star(320, 313, 3), "#ffdc83") + path(star(350, 313, 3), "#ffdc83") +
        rect(361, 310, 5, 2.5, "#d9b24a") + path("M366 306 q5 2 0 5 q-5 -3 0 -5 M366 317 q5 -2 0 -5 q-5 3 0 5", "#d9b24a") +
        line(335, 300, 335, 292, "#d9b24a", 1.5) + path("M335 278 L344 294 Q335 298 326 294 Z", "#ff8ecb") + path("M327 293 Q335 289 343 293 Q335 297 327 293 Z", "#fff") + circle(335, 276, 3.6, "#fce1f2") + path("M335 278 Q326 276 325 270 M335 278 Q344 276 345 270", "none", 'stroke="#fce1f2" stroke-width="1.5" stroke-linecap="round"') + path("M332 274 L335 271 L338 274", "none", 'stroke="#ffdc83" stroke-width="1.4"') +
        path(star(319, 280, 3), "#fff8dc", 'class="lp-twinkle"') + path(star(351, 279, 3.5), "#fff8dc", 'class="lp-twinkle" style="animation-delay:.6s"');
    },
    "table-orrery": function () { // The showpiece: a brass clockwork solar system on a claw-footed pedestal, planets twinkling in their orbits.
      const planets = [[-26, 7, 4.5, "#70b8eb", 0], [23, -7, 3.5, "#ff9d6b", .5], [-12, -12, 3, "#c9a8ff", 1], [36, 6, 5, "#7fc49a", 1.5]];
      const cx = 336, cy = 290;
      return oval(cx, cy, 46, 26, "#ffdc83", 'opacity=".08"') +
        path("M326 362 Q330 358 330 348 V336 H342 V348 Q342 358 346 362 Z", "#8a6a2a") + path("M318 362 Q322 350 332 350 M354 362 Q350 350 340 350", "none", 'stroke="#d9b24a" stroke-width="4" stroke-linecap="round"') +
        circle(318, 362, 3, "#ffdc83") + circle(354, 362, 3, "#ffdc83") + rect(304, 328, 64, 7, "#6d4585", 'rx="3"') + path("M304 331.5 H368", "none", 'stroke="#ffdc83" stroke-width="1.5"') +
        path("M336 328 V296 M326 328 Q336 316 346 328", "none", 'stroke="#d9b24a" stroke-width="3" stroke-linecap="round"') + circle(336, 322, 7, "none", 'stroke="#d9b24a" stroke-width="2.5" stroke-dasharray="2.5 2"') +
        oval(cx, cy, 16, 6, "none", 'stroke="#d9b24a" stroke-width="1.5"') + oval(cx, cy, 27, 10, "none", 'stroke="#d9b24a" stroke-width="1.5" opacity=".85"') + oval(cx, cy, 37, 14, "none", 'stroke="#d9b24a" stroke-width="1.5" opacity=".7"') +
        circle(cx, cy, 15, "#ffdc83", 'opacity=".2"') + circle(cx, cy, 9, "#ffdc83", 'class="lp-neon"') + circle(cx, cy, 5, "#fff8dc") +
        planets.map(function (p) { return circle(cx + p[0], cy + p[1], p[2], p[3], 'class="lp-twinkle" style="animation-delay:' + p[4] + 's"') + circle(cx + p[0] - p[2] * .3, cy + p[1] - p[2] * .3, p[2] * .3, "#fff", 'opacity=".7"'); }).join("") +
        oval(cx + 36, cy + 6, 8.5, 2.2, "none", 'stroke="#ffe9a8" stroke-width="1.2" transform="rotate(-16 ' + (cx + 36) + " " + (cy + 6) + ')"') +
        [[302, 274, 3], [370, 276, 3.5], [316, 269, 2.5], [356, 269, 2.5]].map(function (s, i) { return path(star(s[0], s[1], s[2]), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .4) + 's"'); }).join("");
    },

    // Star Capsule toychest
    "chest-crate": function () { // A slatted crate of alphabet-style blocks (with pictures, not letters) and a ball.
      function block(x, y, s, fill, mark) { return rect(x, y, s, s, fill, 'rx="2"') + rect(x + 2, y + 2, s - 4, s - 4, "none", 'stroke="#fff" stroke-width="1" opacity=".6" rx="1"') + mark; }
      return circle(112, 428, 12, "#70b8eb") + path("M101 424 Q112 420 123 428 M104 436 Q114 430 122 436", "none", 'stroke="#fff" stroke-width="2.5"') +
        block(64, 418, 22, "#ff8ecb", path(star(75, 429, 7), "#fff8dc")) + block(88, 424, 20, "#ffdc83", path("M98 442 c-9 -6 -7 -13 -3 -13 q3 0 3 3 q0 -3 3 -3 c4 0 6 7 -3 13 Z", "#e8556a")) +
        block(70, 402, 18, "#7fc49a", circle(79, 411, 4.5, "#fff8dc")) +
        rect(56, 436, 72, 38, "#54391f", 'rx="2"') + [438, 451, 464].map(function (y) { return rect(54, y, 76, 9, "#c79256", 'rx="1"'); }).join("") + rect(58, 434, 8, 42, "#a8743f", 'rx="1"') + rect(118, 434, 8, 42, "#a8743f", 'rx="1"') +
        [[62, 442], [62, 455], [62, 468], [122, 442], [122, 455], [122, 468]].map(function (n) { return circle(n[0], n[1], 1.3, "#54391f"); }).join("");
    },
    "chest-kitty": function () { // A cat-shaped toy box whose lid has a little pair of ears.
      return oval(92, 476, 42, 4, "#00000044") + path("M60 442 L58 412 L82 430 Z M124 442 L126 412 L102 430 Z", "#c9a8ff") + path("M63 436 L62 420 L76 431 Z M121 436 L122 420 L108 431 Z", "#ffb3d1") +
        rect(56, 428, 72, 46, "#c9a8ff", 'rx="11"') + rect(56, 428, 72, 14, "#b08ce8", 'rx="9"') + path("M56 442 H128", "none", 'stroke="#8a64c0" stroke-width="2"') +
        circle(77, 456, 6, "#2a1a3e") + circle(107, 456, 6, "#2a1a3e") + circle(79, 454, 2, "#fff") + circle(109, 454, 2, "#fff") + path("M88 461 h8 l-4 4.5 Z", "#ff8ecb") +
        path("M92 465.5 q-5 6 -10 2 M92 465.5 q5 6 10 2", "none", 'stroke="#462846" stroke-width="1.8" stroke-linecap="round"') +
        path("M67 462 L52 459 M67 466 L52 468 M117 462 L132 459 M117 466 L132 468", "none", 'stroke="#fff8f0" stroke-width="1.4" stroke-linecap="round"') +
        oval(70, 475, 9, 4, "#b08ce8") + oval(114, 475, 9, 4, "#b08ce8");
    },
    "chest-pail": function () { // A seaside bucket with its spade, a starfish, a beach ball, and a little spilled sand.
      return oval(92, 472, 44, 7, "#e6c98e") + circle(112, 460, 3, "#d9b676") + circle(64, 468, 2.4, "#d9b676") +
        circle(116, 450, 14, "#fff8f0") + path("M116 436 Q124 450 116 464 Q130 458 130 450 Q130 440 116 436 Z", "#e8556a") + path("M116 436 Q108 450 116 464 Q102 458 102 450 Q102 440 116 436 Z", "#70b8eb") + circle(116, 450, 2.6, "#ffdc83") +
        path("M64 404 L72 442", "none", 'stroke="#ffdc83" stroke-width="4" stroke-linecap="round"') + path("M56 398 Q64 386 72 398 L68 412 H60 Z", "#ffb84a", 'transform="rotate(-11 64 404)"') +
        path("M58 430 Q80 408 106 430", "none", 'stroke="#c8d2e8" stroke-width="3"') + path("M60 430 H104 L98 470 H66 Z", "#ff8ecb") + oval(82, 430, 22, 5, "#d46fb8") + oval(82, 430, 18, 3, "#e6c98e") +
        path("M64 450 q6 -5 12 0 t12 0 t12 0", "none", 'stroke="#fff" stroke-width="3" stroke-linecap="round"') +
        group(path(star(0, 0, 11), "#ff9d6b") + circle(0, 0, 3, "#ffb98a") + circle(-2, -2, 1, "#d9652e") + circle(2, 1, 1, "#d9652e"), 'transform="translate(82 462) rotate(20)"');
    },
    "chest-present": function () { // A wrapped gift with a floppy bow, one ear of a plush peeking under the lid.
      return oval(92, 476, 44, 4, "#00000044") + scatter(83, 11, function (roll) { return rect(round(roll(52, 132)), round(roll(404, 430)), 3, 3, ["#ff8ecb", "#70ebea", "#ffdc83"][Math.floor(roll(0, 3))], 'transform="rotate(' + Math.round(roll(0, 90)) + " " + round(roll(52, 132)) + ' 410)"'); }) +
        rect(60, 442, 64, 32, "#70b8eb", 'rx="2"') + [[68, 450], [78, 460], [68, 468], [107, 450], [117, 460], [107, 468], [80, 448], [116, 470]].map(function (d) { return circle(d[0], d[1], 2.4, "#fff", 'opacity=".75"'); }).join("") + rect(86, 442, 12, 32, "#ff8ecb") +
        path("M72 434 Q68 422 76 420 Q82 422 78 434 Z", "#fce1f2") + rect(54, 430, 76, 14, "#5a9bda", 'rx="2"') + rect(86, 430, 12, 14, "#ff8ecb") +
        path("M92 430 Q70 406 62 418 Q60 430 92 430 Z M92 430 Q114 406 122 418 Q124 430 92 430 Z", "#ff8ecb") + path("M92 430 Q76 416 68 420 M92 430 Q108 416 116 420", "none", 'stroke="#e8669f" stroke-width="1.5"') + circle(92, 428, 5.5, "#e8669f");
    },
    "chest-wagon": function () { // A little red wagon loaded with a ball, a plush bunny and a flag.
      function wheel(x) { return circle(x, 467, 8, "#6a5a80") + circle(x, 467, 4, "#c8d2e8") + circle(x, 467, 1.3, "#2a1a3e"); }
      return oval(92, 475, 44, 3, "#00000044") + line(66, 448, 50, 432, "#c8d2e8", 3) + path("M42 434 q8 -7 16 -2", "none", 'stroke="#ffdc83" stroke-width="5" stroke-linecap="round"') +
        path("M112 420 V404 L126 410 L112 416", "#ffdc83", 'stroke="#8a7aa8" stroke-width="1.5"') + circle(108, 436, 14, "#7fc49a") + path("M96 432 Q108 428 120 436 M99 444 Q108 438 118 444", "none", 'stroke="#fff" stroke-width="2.5" opacity=".85"') +
        oval(73, 414, 3.5, 11, "#fff1e0", 'transform="rotate(-10 73 414)"') + oval(83, 413, 3.5, 11, "#fff1e0", 'transform="rotate(10 83 413)"') + circle(78, 430, 12, "#fff1e0") + circle(74, 428, 1.5, "#462846") + circle(82, 428, 1.5, "#462846") + path("M76.5 433 h3 l-1.5 2 Z", "#ff8ecb") +
        path("M60 442 H126 L122 464 H64 Z", "#e8556a") + rect(58, 440, 70, 5, "#fff1e0", 'rx="2"') + path("M72 445 V463 M92 445 V463 M112 445 V463", "none", 'stroke="#c23a50" stroke-width="2"') + wheel(74) + wheel(112);
    },
    "chest-trunk": function () { // A well-travelled steamer trunk, its sides pasted with stickers.
      return oval(92, 476, 44, 4, "#00000044") + rect(56, 432, 72, 42, "#2f7a78", 'rx="3"') + rect(56, 416, 72, 18, "#3a908c", 'rx="4"') + rect(54, 432, 76, 4, "#d9b24a", 'rx="1"') +
        [62, 112].map(function (x) { return rect(x, 416, 10, 58, "#7a4a2a") + rect(x + 2, 428, 6, 8, "#ffdc83", 'rx="1"'); }).join("") +
        circle(87, 452, 10, "#fff1e0") + path(star(87, 452, 7), "#e8556a") + rect(99, 442, 11, 15, "#ffdc83", 'rx="1" transform="rotate(10 104 450)"') + path("M100 445 l9 1 M99 450 l9 1 M99 454 l6 1", "none", 'stroke="#7a4a2a" stroke-width="1"') + oval(78, 467, 8, 5, "#ff8ecb") + path("M74 467 q4 -4 8 0", "none", 'stroke="#fff" stroke-width="1.4"') + rect(95, 462, 12, 9, "#c9a8ff", 'transform="rotate(-8 101 466)"') + path("M97 466 l8 -1", "none", 'stroke="#fff" stroke-width="1.4"') +
        rect(86, 422, 12, 15, "#ffdc83", 'rx="2"') + circle(92, 430, 2, "#2a1a3e") + path("M80 416 Q92 406 104 416", "none", 'stroke="#54391f" stroke-width="3.5" stroke-linecap="round"') +
        [[56, 416], [128, 416], [56, 474], [128, 474]].map(function (c) { return circle(c[0], c[1], 3.2, "#d9b24a"); }).join("");
    },
    "chest-dollhouse": function () { // A toy box built as a dollhouse with its front off: four tiny furnished rooms.
      return oval(92, 476, 44, 4, "#00000044") + path("M50 438 L92 402 L134 438 Z", "#c0495e") + path("M92 402 L122 428 M92 402 L62 428", "none", 'stroke="#8f2f44" stroke-width="1.4"') + circle(92, 424, 6, "#ffe9a8", 'stroke="#fff1e0" stroke-width="2"') + rect(112, 408, 8, 16, "#a8743f") +
        rect(58, 436, 68, 38, "#fff1e0") + rect(61, 439, 30, 15, "#70b8eb") + rect(93, 439, 30, 15, "#ffdc83") + rect(61, 456, 30, 15, "#ff8ecb") + rect(93, 456, 30, 15, "#c9a8ff") + // Four rooms, one wallpaper colour each.
        rect(64, 449, 14, 5, "#fff8f0", 'rx="1"') + rect(64, 446, 5, 3, "#ff8ecb", 'rx="1"') + circle(84, 444, 3.2, "#fff8dc") + // Bedroom: a bed and a moon lamp.
        rect(97, 448, 10, 6, "#a8743f") + rect(100, 444, 4, 4, "#e8556a", 'rx="1"') + rect(112, 443, 7, 11, "#3a5a52", 'rx="1"') + // Study: desk and bookcase.
        rect(65, 465, 12, 6, "#7a4a2a", 'rx="1"') + oval(84, 469, 4, 2, "#5fae86") + line(84, 462, 84, 468, "#3f8a66", 1.5) + // Parlour: sofa and pot plant.
        circle(102, 466, 5, "#e8556a") + rect(111, 460, 8, 11, "#fff8f0", 'rx="2"') + circle(115, 464, 1.3, "#2a1a3e"); // Nursery: a ball and a tiny cot.
    },
    "chest-dragon": function () { // A small sleepy dragon curled protectively around her toy chest, snoring.
      const body = "#5fc0a0";
      return oval(92, 477, 48, 4, "#00000044") + path("M108 432 L136 412 L132 438 L122 434 Z", "#3f8a78") + path("M116 430 L130 420 M118 434 L130 428", "none", 'stroke="#2f6a5c" stroke-width="1.3"') +
        path("M60 470 Q44 448 64 434 Q90 420 118 430 Q138 442 130 466", "none", 'stroke="' + body + '" stroke-width="15" stroke-linecap="round"') +
        [[78, 423], [92, 420], [106, 422], [120, 428]].map(function (t) { return path("M" + (t[0] - 5) + " " + (t[1] + 3) + " L" + t[0] + " " + (t[1] - 7) + " L" + (t[0] + 5) + " " + (t[1] + 3) + " Z", "#ffb84a"); }).join("") +
        oval(93, 450, 34, 22, "#469a82") + // Her tummy fills the space behind the chest.
        rect(70, 440, 46, 32, "#a8743f", 'rx="3"') + path("M70 448 H116", "none", 'stroke="#7a4a2a" stroke-width="3"') + path("M82 440 V472 M104 440 V472", "none", 'stroke="#ffdc83" stroke-width="2.5"') + rect(88, 445, 10, 12, "#ffdc83", 'rx="2"') + circle(93, 451, 1.8, "#7a4a2a") +
        path("M130 466 Q116 480 88 475 Q76 473 70 477", "none", 'stroke="' + body + '" stroke-width="7" stroke-linecap="round"') + path("M72 476 L60 470 L66 482 Z", "#ffb84a") +
        path("M56 428 L50 412 L62 422 Z M70 428 L74 412 L64 422 Z", "#ffdc83") + oval(62, 437, 15, 12, body) + oval(50, 443, 10, 7, "#7fd6bc") + path("M57 435 q5 4 10 0", "none", 'stroke="#23402f" stroke-width="2" stroke-linecap="round"') + circle(46, 441, 1.1, "#23402f") + circle(52, 442, 1.1, "#23402f") +
        circle(47, 431, 2.6, "#e8dff0", 'opacity=".55" class="lp-twinkle"') + circle(45, 422, 3.8, "#e8dff0", 'opacity=".4" class="lp-twinkle" style="animation-delay:.5s"') +
        [[118, 462], [100, 474]].map(function (c) { return oval(c[0], c[1], 5, 2.6, "#ffdc83", 'stroke="#d9b24a" stroke-width="1"'); }).join("");
    },
    "chest-carousel": function () { // A little merry-go-round toy box, its bulbs winking around a striped top.
      function horse(x, y, fill) { return line(x, 442, x, 468, "#ffdc83", 1.6) + oval(x, y, 7.5, 4, fill) + circle(x + 6, y - 5, 3, fill) + path("M" + (x - 6) + " " + (y + 2) + " l-2 7 M" + (x + 5) + " " + (y + 2) + " l2 7", "none", 'stroke="' + fill + '" stroke-width="2" stroke-linecap="round"') + path("M" + (x - 7) + " " + y + " q-5 1 -4 6", "none", 'stroke="#ff8ecb" stroke-width="1.6" stroke-linecap="round"') + rect(x - 3, y - 4, 5, 3, "#70ebea", 'rx="1"'); }
      const stripes = [["#e8556a", 56, 70], ["#fff1e0", 70, 84], ["#e8556a", 84, 100], ["#fff1e0", 100, 114], ["#e8556a", 114, 128]];
      return oval(92, 476, 46, 4, "#00000044") + oval(92, 442, 36, 5, "#2a1a3e") + rect(62, 442, 60, 28, "#3a2460") + rect(88, 438, 8, 32, "#d9b24a") + horse(72, 458, "#fff8f0") + horse(112, 456, "#ffdc83") +
        oval(92, 470, 44, 7, "#6d4bb0") + rect(48, 468, 88, 5, "#d9b24a", 'rx="2"') + oval(92, 466, 40, 4.5, "#8a64c0") +
        stripes.map(function (s) { return path("M92 405 L" + s[1] + " 437 Q" + ((s[1] + s[2]) / 2) + " 443 " + s[2] + " 437 Z", s[0]); }).join("") + rect(52, 435, 80, 5, "#ffdc83", 'rx="2"') + line(92, 405, 92, 396, "#ffdc83", 1.6) + path("M92 396 L104 400 L92 404 Z", "#70ebea") +
        [58, 72, 87, 102, 116, 128].map(function (x, i) { return circle(x, 437.5, 2, "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .3) + 's"'); }).join("");
    },
    "chest-stardust": function () { // The showpiece: a midnight chest flung open, starlight and toys spiralling up out of it.
      const toys = [[58, 410, "star", "#ffdc83"], [78, 400, "moon", "#fff1cd"], [108, 402, "ball", "#70ebea"], [126, 416, "star", "#ff8ecb"], [46, 428, "ball", "#ffb3d1"], [136, 436, "moon", "#c9a8ff"]];
      return oval(92, 478, 50, 5, GLOW, 'opacity=".3"') + oval(92, 430, 54, 40, GLOW, 'opacity=".1"') +
        path("M62 442 Q58 412 92 410 Q126 412 122 442 Z", "#20265a", 'stroke="#ffdc83" stroke-width="2"') + path("M68 438 Q68 418 92 417 Q116 418 116 438", "none", 'stroke="#ffdc83" stroke-width="1" opacity=".6"') + path(star(92, 426, 6), "#ffdc83") +
        path("M78 448 Q60 422 76 408 M92 448 Q92 420 96 398 M106 448 Q124 424 108 408", "none", 'stroke="' + GLOW + '" stroke-width="3" stroke-linecap="round" class="lp-neon" opacity=".85"') +
        oval(92, 448, 32, 6, "#fff1cd", 'opacity=".95"') + oval(92, 448, 22, 3.5, GLOW) +
        rect(58, 448, 68, 26, "#20265a", 'rx="3"') + rect(58, 448, 68, 4, "#ffdc83", 'rx="1"') + rect(58, 470, 68, 4, "#ffdc83", 'rx="1"') + path("M72 452 V470 M112 452 V470", "none", 'stroke="#ffdc83" stroke-width="2"') +
        path("M92 454 C84 454 80 462 92 468 C104 462 100 454 92 454 Z", "#ffdc83") + path(star(92, 461, 4), "#fff8dc") + oval(66, 476, 6, 3, "#d9b24a") + oval(118, 476, 6, 3, "#d9b24a") +
        scatter(91, 7, function (roll, i) { return circle(round(roll(62, 122)), round(roll(453, 468)), round(roll(.8, 1.4)), "#cfe0ff", 'opacity=".8" class="lp-twinkle" style="animation-delay:' + round(i * .3) + 's"'); }) +
        toys.map(function (t, i) {
          const glow = 'class="lp-twinkle" style="animation-delay:' + round(i * .35) + 's"';
          return t[2] === "star" ? path(star(t[0], t[1], 8), t[3], glow) : t[2] === "moon" ? path("M" + t[0] + " " + (t[1] - 7) + " A7 7 0 1 0 " + (t[0] + 6) + " " + (t[1] + 3) + " A6 6 0 1 1 " + t[0] + " " + (t[1] - 7) + " Z", t[3], glow) : circle(t[0], t[1], 4.5, t[3], glow);
        }).join("") +
        scatter(55, 12, function (roll, i) { return path(star(round(roll(60, 126)), round(roll(400, 442)), round(roll(1.5, 3.4))), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + round(i * .17) + 's"'); });
    },

    // Star Capsule view
    "view-clover": function () { // Green hills, a red barn, a fence along the near grass and clover flowers dotted through it.
      function dots(seed, count, top, bottom, r) { // Many small circles as one path, so they cost one shape.
        return scatter(seed, count, function (roll) { const x = round(roll(6, 234)), y = round(roll(top, bottom)); return "M" + round(x - r) + " " + y + " a" + r + " " + r + " 0 1 0 " + r * 2 + " 0 a" + r + " " + r + " 0 1 0 -" + r * 2 + " 0Z "; });
      }
      const clouds = [[40, 26, 1], [128, 18, .8], [196, 32, 1.1]].map(function (c) { return group(oval(0, 0, 18, 7, "#fff", 'opacity=".9"') + oval(-8, -5, 9, 7, "#fff", 'opacity=".9"') + oval(7, -6, 10, 8, "#fff", 'opacity=".9"'), 'transform="translate(' + c[0] + " " + c[1] + ") scale(" + c[2] + ')"'); }).join("");
      const barn = rect(146, 84, 26, 22, "#c8483e") + path("M142 86 L159 72 L176 86 Z", "#8a3030") + rect(155, 94, 8, 12, "#f5e2c8") + path("M155 94 L163 106 M163 94 L155 106", "none", 'stroke="#c8483e" stroke-width="1"');
      let posts = "M0 112 H240 M0 117 H240";
      for (let x = 10; x < 240; x += 20) posts += " M" + x + " 108 V124";
      return view(["#7fcbe8", "#eaf6d0"], ["#101d3a", "#2b4a5e"], 26,
        hills(61, 84, 26, "#9ad6a4") + clouds,
        hills(62, 102, 22, "#66b87a") + barn,
        hills(63, 120, 12, "#3f9a5a") + path(posts, "none", 'stroke="#f2e6cf" stroke-width="2"') + path(dots(64, 14, 118, 128, 1.6), "#ffb3d1") + path(dots(65, 10, 120, 129, 1.2), "#fff8dc"));
    },
    "view-dunes": function () { // Flat-topped red mesas, golden dunes and saguaro cacti under an apricot sky.
      const cacti = [30, 96, 170, 214].map(function (x) { return "M" + x + " 124 v-26 M" + x + " 110 h-8 q-2 0 -2 -2 v-8 M" + x + " 106 h7 q2 0 2 -2 v-6"; }).join(" ");
      return view(["#f0a070", "#fde3b8"], ["#1a1236", "#5a3050"], 22,
        path("M0 92 H18 L26 62 H62 L72 92 H128 L138 70 H176 L186 92 H240 V140 H0 Z", "#b56a6a") + path("M26 72 H63 M139 78 H178", "none", 'stroke="#8f4f58" stroke-width="2"'),
        hills(71, 98, 26, "#eab472"),
        hills(72, 118, 18, "#c9834a") + path(cacti, "none", 'stroke="#3f7a5a" stroke-width="4.5" stroke-linecap="round"') + oval(62, 124, 7, 3, "#8f5a34") + oval(140, 122, 9, 3.5, "#8f5a34") + oval(232, 126, 5, 2.5, "#8f5a34"));
    },
    "view-maple": function () { // An avenue of autumn trees in three colours, with two big maples on the lane in front.
      function crowns(list, r) { // Round tree-tops as one path.
        return list.map(function (c) { return "M" + (c[0] - r) + " " + c[1] + " a" + r + " " + r + " 0 1 0 " + r * 2 + " 0 a" + r + " " + r + " 0 1 0 -" + r * 2 + " 0Z"; }).join(" ");
      }
      const row = [14, 40, 66, 92, 118, 144, 170, 196, 222].map(function (x, i) { return [x, 82 + (i * 5) % 8]; });
      const leaves = scatter(83, 16, function (roll) { const x = round(roll(6, 234)), y = round(roll(120, 129)); return "M" + x + " " + y + " q3 -3 6 0 q-3 3 -6 0Z "; });
      const big = [[20, 78], [140, 74]];
      return view(["#e8b45f", "#f9e8c4"], ["#161230", "#4a2c48"], 28,
        hills(81, 88, 26, "#d39563"),
        hills(82, 106, 10, "#a8763f") + path(row.map(function (c) { return "M" + c[0] + " " + c[1] + " V108"; }).join(" "), "none", 'stroke="#5a3a20" stroke-width="3"') + path(crowns(row.filter(function (c, i) { return i % 3 === 0; }), 13), "#d9482e") + path(crowns(row.filter(function (c, i) { return i % 3 === 1; }), 13), "#f08a2e") + path(crowns(row.filter(function (c, i) { return i % 3 === 2; }), 13), "#f6c23e"),
        hills(83, 122, 7, "#6a4020") + path(big.map(function (c) { return "M" + c[0] + " " + c[1] + " V124"; }).join(" "), "none", 'stroke="#3f2614" stroke-width="6"') + path(crowns([[8, 76], [32, 76], [20, 64], [128, 72], [152, 72], [140, 60]], 16), "#c9402a") + path(crowns([[20, 80], [140, 76]], 13), "#e8682e") + path(leaves, "#f0a030"));
    },
    "view-gumdrop": function () { // A sweet-shop countryside: gumdrop hills, lollipops and candy canes on a chocolate bank.
      function gum(list) { // Dome-shaped sweets as one path, sitting on y = 108.
        return list.map(function (g) { return "M" + (g[0] - g[1]) + " 108 C" + (g[0] - g[1]) + " " + (108 - g[2]) + " " + (g[0] + g[1]) + " " + (108 - g[2]) + " " + (g[0] + g[1]) + " 108 Z"; }).join(" ");
      }
      const sugar = scatter(95, 40, function (roll) { return "M" + round(roll(8, 232)) + " " + round(roll(82, 106)) + " h1.5 v1.5 h-1.5Z "; });
      const pops = [[36, 84, "#ff7aa8"], [120, 78, "#7ad6c0"], [205, 86, "#b58cf0"]];
      const canes = [[78, 96], [166, 92]];
      return view(["#ffb0d8", "#ffeccf"], ["#1c1238", "#5a3a70"], 24,
        hills(91, 90, 34, "#e6b3e0") + group(oval(52, 36, 20, 9, "#fff0f7") + oval(40, 30, 12, 9, "#fff0f7") + oval(64, 29, 13, 10, "#fff0f7") + oval(178, 22, 18, 8, "#ffe0ee") + oval(168, 17, 11, 8, "#ffe0ee") + oval(190, 16, 11, 8, "#ffe0ee")),
        rect(0, 106, 240, 40, "#ffd9ef") + path(gum([[24, 20, 40], [150, 22, 44]]), "#ff7aa8") + path(gum([[66, 18, 34], [196, 20, 38]]), "#7ad6c0") + path(gum([[108, 22, 46]]), "#ffd66b") + path(gum([[230, 9, 22]]), "#b58cf0") + path(sugar, "#fff", 'opacity=".75"'),
        waves(120, "#8a5236", 3) + pops.map(function (p) { return rect(p[0] - 1.5, p[1], 3, 124 - p[1], "#fff4e0") + circle(p[0], p[1], 12, p[2]) + path("M" + (p[0] - 7) + " " + p[1] + " a7 7 0 1 1 14 0 a4.5 4.5 0 1 1 -9 0", "none", 'stroke="#fff" stroke-width="2" opacity=".85" stroke-linecap="round"'); }).join("") +
        canes.map(function (c) { const d = "M" + c[0] + " 124 V" + c[1] + " a7 7 0 1 1 14 0"; return path(d, "none", 'stroke="#fff" stroke-width="5" stroke-linecap="round"') + path(d, "none", 'stroke="#e8455a" stroke-width="5" stroke-dasharray="4 5"'); }).join(""));
    },
    "view-balloons": function () { // Hot-air balloons at three distances over green hills; the nearest one sails past the fastest.
      function balloon(x, y, r, colour, stripe) { // Envelope, a narrower stripe panel down the middle, ropes and a basket.
        const a = round(r * .35), b = round(r * .12), c = round(r * .3), low = round(y + r * 1.3), mid = round(y + r * .85);
        return path("M" + round(x - r) + " " + y + " A" + r + " " + r + " 0 1 1 " + round(x + r) + " " + y + " Q" + round(x + r) + " " + mid + " " + round(x + c) + " " + low + " H" + round(x - c) + " Q" + round(x - r) + " " + mid + " " + round(x - r) + " " + y + " Z", colour) +
          path("M" + round(x - a) + " " + y + " A" + a + " " + r + " 0 1 1 " + round(x + a) + " " + y + " Q" + round(x + a) + " " + mid + " " + round(x + b) + " " + low + " H" + round(x - b) + " Q" + round(x - a) + " " + mid + " " + round(x - a) + " " + y + " Z", stripe) +
          path("M" + round(x - c) + " " + low + " L" + round(x - r * .2) + " " + round(low + r * .3) + " M" + round(x + c) + " " + low + " L" + round(x + r * .2) + " " + round(low + r * .3), "none", 'stroke="#6a4a2a" stroke-width=".8"') +
          rect(round(x - r * .22), round(low + r * .3), round(r * .44), round(r * .32), "#8a5a2a", 'rx="1"');
      }
      return view(["#9fd8f2", "#fff2d9"], ["#0f1a3a", "#34406a"], 30,
        hills(101, 104, 20, "#a4d3b0") + balloon(30, 40, 7, "#f2a65a", "#fff1cd") + balloon(116, 56, 6, "#6fb7e8", "#fff") + balloon(204, 32, 8, "#e87aa8", "#ffe0ee"),
        hills(102, 116, 14, "#66ad80") + balloon(60, 50, 13, "#e8556a", "#ffdc83") + balloon(176, 66, 12, "#7a6bd8", "#c9f0ff"),
        hills(103, 128, 8, "#3d8a5c") + balloon(128, 38, 19, "#3fb9a8", "#ffe9a8") + path("M18 124 a6 6 0 0 1 12 0 a5 5 0 0 1 10 0Z M84 126 a6 6 0 0 1 12 0 a5 5 0 0 1 10 0Z M196 125 a6 6 0 0 1 12 0 a5 5 0 0 1 10 0Z", "#2c6d48"));
    },
    "view-blossom": function () { // Blue mountains, cherry trees on the bank, a red bridge over the river, and petals drifting down.
      function puffs(list, r) { return list.map(function (c) { return "M" + (c[0] - r) + " " + c[1] + " a" + r + " " + r + " 0 1 0 " + r * 2 + " 0 a" + r + " " + r + " 0 1 0 -" + r * 2 + " 0Z"; }).join(" "); }
      const trees = [[32, 70], [92, 76], [160, 68], [216, 74]];
      const crown = []; const light = [];
      trees.forEach(function (t) { crown.push([t[0] - 12, t[1] + 2], [t[0] + 12, t[1] + 2], [t[0], t[1] - 8], [t[0], t[1] + 6]); light.push([t[0] - 6, t[1] - 3], [t[0] + 7, t[1] - 1]); });
      const glints = scatter(115, 7, function (roll) { const x = round(roll(8, 210)), y = round(roll(118, 128)); return "M" + x + " " + y + " h" + round(roll(6, 16)); }).replace(/M/g, " M");
      return view(["#cde4f2", "#fdeaef"], ["#10182e", "#3a3a5e"], 26,
        path("M0 92 L40 62 L70 88 L120 50 L170 90 L208 68 L240 92 V140 H0 Z", "#a9bccf") + path("M107 60 L120 50 L133 60 L126 58 L120 63 L114 58 Z M30 70 L40 62 L50 70 L44 69 L40 72 L36 69 Z", "#fff"),
        hills(112, 110, 12, "#8fb59a") + path(trees.map(function (t) { return "M" + t[0] + " " + (t[1] + 8) + " V112"; }).join(" "), "none", 'stroke="#6a4a50" stroke-width="3"') + path(puffs(crown, 12), "#f3a8c4") + path(puffs(light, 7), "#fbd4e2"),
        rect(0, 114, 240, 30, "#6fa8d8") + path(glints, "none", 'stroke="#d6ecff" stroke-width="1.2" stroke-linecap="round"') +
          path("M90 124 Q130 92 170 124 L164 128 Q130 102 96 128 Z", "#d9483e") + path("M92 116 Q130 86 168 116", "none", 'stroke="#f0d9a0" stroke-width="2"') + path("M106 104 V116 M118 97 V110 M130 94 V106 M142 97 V110 M154 104 V116", "none", 'stroke="#a8342e" stroke-width="2"'),
        falling("snow", scatter(113, 14, function (roll) { const x = round(roll(0, 120)), y = round(roll(0, 130)); return oval(x, y, 2.2, 1.3, "#ffc2d8", 'opacity=".95" transform="rotate(' + Math.round(roll(0, 180)) + " " + x + " " + y + ')"'); })));
    },
    "view-reef": function () { // Under the sea: slanting light, rock, coral fans, swaying kelp, fish and a clam with a pearl.
      function fish(x, y, size, body, fin) { // Facing left, because the strips slide left, so they seem to swim.
        return oval(x, y, size * 1.4, size * .85, body) + path("M" + (x + size * 1.2) + " " + y + " l" + size + " -" + size * .8 + " v" + size * 1.6 + " Z", fin) + circle(x - size * .7, y - size * .15, size * .18 + .4, "#1a2a4a");
      }
      const coral = [[34, 112, 12], [150, 110, 14]].map(function (c) { return "M" + c[0] + " " + c[1] + " V" + (c[1] - c[2] * 2) + " M" + c[0] + " " + (c[1] - c[2]) + " L" + (c[0] - c[2]) + " " + (c[1] - c[2] * 1.9) + " M" + c[0] + " " + (c[1] - c[2]) + " L" + (c[0] + c[2]) + " " + (c[1] - c[2] * 1.9) + " M" + c[0] + " " + (c[1] - c[2] * 1.5) + " L" + (c[0] - c[2] * .55) + " " + (c[1] - c[2] * 2.3) + " M" + c[0] + " " + (c[1] - c[2] * 1.5) + " L" + (c[0] + c[2] * .55) + " " + (c[1] - c[2] * 2.3); }).join(" ");
      const kelp = [78, 94, 204, 222].map(function (x, i) { const top = 58 + (i * 11) % 22; return "M" + x + " 116 Q" + (x - 8) + " " + round((116 + top) * .62) + " " + x + " " + round((116 + top) * .5) + " T" + x + " " + top; }).join(" ");
      const bubbles = scatter(127, 12, function (roll) { const x = round(roll(8, 232)), y = round(roll(14, 104)), r = round(roll(1.2, 3)); return "M" + round(x - r) + " " + y + " a" + r + " " + r + " 0 1 0 " + r * 2 + " 0 a" + r + " " + r + " 0 1 0 -" + r * 2 + " 0Z "; });
      return view(["#2f9cc0", "#0d4f73"], ["#06162e", "#0a2a4a"], -30,
        hills(121, 98, 34, "#1f7a94") + path("M24 0 H58 L30 130 H0 Z M104 0 H128 L104 130 H78 Z M170 0 H206 L176 130 H146 Z", "#c9f0ff", 'opacity=".13"'),
        hills(122, 112, 12, "#d9c48a") + path(coral, "none", 'stroke="#ff7a8e" stroke-width="3.4" stroke-linecap="round"') + path(kelp, "none", 'stroke="#3fb88a" stroke-width="3.2" stroke-linecap="round"') + path("M112 112 V96 M120 112 V90 M128 112 V98", "none", 'stroke="#ffb84a" stroke-width="5" stroke-linecap="round"') + fish(58, 62, 6, "#ffdc83", "#f0a030") + fish(180, 48, 5, "#ff9ac0", "#e8689a"),
        waves(124, "#e6d29a", 3) + fish(112, 84, 9, "#ff8e4a", "#fff1cd") + fish(32, 72, 7, "#7ad6e8", "#3f8fc0") + path(bubbles, "none", 'stroke="#d6f2ff" stroke-width=".9" opacity=".8"') +
          path("M200 124 Q216 106 232 124 Z", "#c9a8ff") + path("M204 124 L214 112 M216 124 V110 M228 124 L218 112", "none", 'stroke="#8f6fc8" stroke-width="1"') + circle(216, 120, 3, "#fff8f0") + path(star(70, 126, 7), "#ff6f7f"));
    },
    "view-aurora": function () { // Green and pink ribbons of light over snowy peaks, dark pines and an igloo with a warm doorway.
      const streaks = scatter(133, 16, function (roll, i) { const x = 8 + i * 14.5, top = round(roll(8, 26)); return "M" + x + " " + top + " V" + round(top + roll(26, 46)) + " "; });
      return view(["#2a5f78", "#93cfc5"], ["#070d22", "#103048"], 18,
        path("M0 22 C40 -8 80 48 120 22 S200 -6 240 22 V62 C200 34 160 76 120 58 S40 40 0 62 Z", "#6fffc0", 'opacity=".42"') +
          path("M0 46 C50 22 90 70 130 46 S210 22 240 46 V76 C200 54 170 92 130 74 S50 62 0 76 Z", "#ff7ad0", 'opacity=".3"') +
          path(streaks, "none", 'stroke="#e0fff0" stroke-width="1.6" opacity=".28"') +
          path("M0 94 L34 62 L58 86 L104 52 L150 90 L190 66 L240 94 V140 H0 Z", "#7a9ab4") + path("M26 70 L34 62 L42 70 L37 69 L34 73 L30 69 Z M94 60 L104 52 L114 60 L108 59 L104 64 L100 59 Z M182 72 L190 66 L198 72 L193 71 L190 75 L186 71 Z", "#fff"),
        hills(132, 106, 10, "#c9dcea") + pines(131, 110, 30, "#1c3f4e", 26),
        hills(134, 124, 8, "#f0f6fc") + path("M60 124 a26 22 0 0 1 52 0 Z", "#dfeaf5") + path("M64 112 Q86 104 108 112 M60 120 H112 M76 104 L74 120 M96 104 L98 120", "none", 'stroke="#b9cbe0" stroke-width="1"') + oval(86, 122, 15, 10, "#ffb84a", 'opacity=".35"') + path("M78 124 a8 9 0 0 1 16 0 Z", "#ffcf7a") + rect(170, 100, 2.5, 26, "#6a4a30") + oval(171, 98, 7, 8, "#ffb84a", 'opacity=".55"') + circle(171, 97, 3, "#ffe9a8", 'class="lp-twinkle"'),
        falling("snow", scatter(136, 12, function (roll) { return circle(round(roll(0, 120)), round(roll(0, 130)), round(roll(.7, 1.4)), "#fff", 'opacity=".8"'); })));
    },
    "view-isles": function () { // Islands floating over a sea of cloud, with waterfalls pouring off their edges.
      function isle(x, y, w, grass) { // A rock underside and a flat grassy top; returns shapes in draw order.
        const h = round(w * .9);
        return path("M" + (x - w) + " " + y + " Q" + round(x - w * .6) + " " + round(y + h * .7) + " " + round(x - w * .15) + " " + round(y + h) + " Q" + (x + w * .1) + " " + round(y + h * 1.25) + " " + round(x + w * .3) + " " + round(y + h * .75) + " Q" + round(x + w * .8) + " " + round(y + h * .5) + " " + (x + w) + " " + y + " Z", "#8f6f8c") + oval(x, y, w + 2, round(w * .22 + 2), grass);
      }
      function fall(x, y, h) { return path("M" + (x - 1.5) + " " + y + " h3 v" + h + " h-3Z", "#d6f0ff", 'opacity=".9"') + oval(x, y + h, 6, 3, "#fff", 'opacity=".9"'); }
      const fluff = [14, 48, 88, 124, 168, 204, 226].map(function (x, i) { const r = 8 + (i * 5) % 7; return "M" + (x - r) + " 118 a" + r + " " + r + " 0 1 1 " + r * 2 + " 0Z"; }).join(" ");
      const birds = "M40 26 q5 -5 9 0 q5 -5 9 0 M170 16 q4 -4 8 0 q4 -4 8 0 M112 38 q3 -3 6 0 q3 -3 6 0";
      return view(["#8fc6f5", "#ffe7f1"], ["#0f1840", "#3a3a70"], 30,
        isle(30, 86, 14, "#a4d3a0") + isle(110, 74, 10, "#a4d3a0") + isle(196, 88, 16, "#a4d3a0") + oval(150, 50, 18, 6, "#fff", 'opacity=".75"') + oval(64, 42, 14, 5, "#fff", 'opacity=".7"'),
        isle(66, 62, 28, "#6fc27f") + fall(52, 66, 44) + path("M74 62 V46", "none", 'stroke="#6a4a30" stroke-width="3"') + circle(74, 40, 10, "#3f9a5a") + rect(50, 50, 12, 10, "#fff1e0") + path("M47 51 L56 42 L65 51 Z", "#e8556a") +
          isle(180, 46, 24, "#7fd08a") + fall(196, 50, 58) + oval(170, 36, 7, 9, "#f3a8c4") + path("M170 45 V38", "none", 'stroke="#6a4a30" stroke-width="2"'),
        waves(112, "#fff", 6) + path(fluff, "#f4f8ff") + path(birds, "none", 'stroke="#54608a" stroke-width="1.4" stroke-linecap="round"'));
    },
    "view-nebula": function () { // Open space: drifting nebula clouds, a ringed planet, a glass observatory dome and a comet going by.
      function stars(seed, count, top, bottom, delay) { // A patch of twinkling sparkles as one path, so they twinkle together.
        const d = scatter(seed, count, function (roll) { return star(round(roll(6, 234)), round(roll(top, bottom)), round(roll(1.5, 3.4))) + " "; });
        return path(d, "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + delay + 's"');
      }
      return view(["#27114f", "#43205f"], ["#0a0620", "#1f0f40"], 22,
        oval(46, 54, 60, 28, "#ff6fb0", 'opacity=".26"') + oval(70, 40, 40, 18, "#ffb3d9", 'opacity=".22"') + oval(176, 70, 62, 32, "#5a8cff", 'opacity=".28"') + oval(160, 54, 34, 16, "#70ebea", 'opacity=".2"') + oval(120, 96, 66, 22, "#a06fff", 'opacity=".22"') + oval(150, 30, 16, 5, "#d9c8ff", 'opacity=".35" transform="rotate(-20 150 30)"') + path("M150 30 a3 2 -20 1 1 5 -2 a6 4 -20 1 1 -12 4 a10 6 -20 1 1 20 -6", "none", 'stroke="#efe6ff" stroke-width="1" opacity=".75"') + stars(141, 7, 8, 104, 0) + stars(142, 7, 8, 104, .7) + stars(143, 6, 8, 104, 1.4),
        group(oval(74, 62, 34, 9, "none", 'stroke="#ffe3a9" stroke-width="2.5" opacity=".7" transform="rotate(-16 74 62)"') + circle(74, 62, 18, "#e88a6a") + path("M58 56 Q74 48 90 56 M56 66 Q74 74 92 66", "none", 'stroke="#c86a58" stroke-width="3" opacity=".6"') + path("M45.3 69.5 A34 9 -16 0 0 102.7 54.5", "none", 'stroke="#fff1cd" stroke-width="2.5" transform="rotate(-1 74 62)"')) +
          circle(128, 32, 6, "#c9d6ff") + circle(126, 30, 1.6, "#9aa8e0") +
          path("M212 13 L166 48 L218 23 Z", "#9fd8ff", 'opacity=".35"') + path("M213 15 L186 37 L217 21 Z", "#fff", 'opacity=".7"') + circle(215, 18, 4, "#fff", 'class="lp-neon"'),
        path("M0 122 Q30 108 60 122 T120 122 T180 122 T240 122 V140 H0 Z", "#2c1f4a") + path("M0 128 Q40 120 80 128 T160 128 T240 128 V140 H0 Z", "#1c1436") +
          oval(36, 124, 8, 3, "#1c1436") + oval(150, 125, 10, 3.5, "#1c1436") +
          path("M90 118 a22 22 0 0 1 44 0 Z", "#bfe8ff", 'opacity=".4" stroke="#e0f6ff" stroke-width="1.5"') + path("M98 118 V112 l3 -5 l3 4 l5 -4 l3 5 V118 Z", "#fce1f2") + circle(102, 110, 1, "#462846") + circle(108, 110, 1, "#462846") + rect(84, 118, 56, 4, "#a8b4c4", 'rx="2"') +
          path("M198 120 L204 100 L210 112 L216 96 L222 120 Z", "#70ebea", 'opacity=".9" class="lp-neon"') + path("M12 122 L16 110 L21 122 Z M28 122 L30 114 L34 122 Z", "#c9a8ff"),
        falling("snow", scatter(144, 9, function (roll) { return path(star(round(roll(4, 116)), round(roll(0, 130)), round(roll(1.2, 2.4))), "#ffe9a8", 'opacity=".85"'); })));
    },

    // Star Capsule wall art
    // On the left wall between the door and the window, roughly x 26..112 and y 146..234, below the trophy bracket.

    "art-goldfish": function () { // A little framed goldfish print on a cream mat.
      return rect(38, 150, 60, 76, "#caa272", 'rx="3"') + rect(43, 155, 50, 66, "#fdf6e3") + rect(47, 159, 42, 58, "#a9dcf0") +
        path("M54 217 q-4 -8 2 -14 q-3 -5 2 -10 M84 217 q4 -10 -2 -16", "none", 'stroke="#5fae86" stroke-width="2.4" stroke-linecap="round"') +               // Waterweed.
        path("M74 187 L87 179 Q83 187 87 196 Z", "#ffb86a") + oval(62, 187, 14, 10, "#ff9d4a") + path("M60 178 Q65 170 70 178 Z", "#ffb86a") + path("M62 196 Q65 203 70 195 Z", "#ffb86a") + // Tail, body and fins, all inside the water.
        circle(54, 185, 2.2, "#2a1a3a") + path("M64 180 q4 7 0 14 M70 181 q4 6 0 12", "none", 'stroke="#ffe0b0" stroke-width="1.4" opacity=".8"') +                  // Eye and scale lines.
        circle(50, 170, 2.6, "none", 'stroke="#fff" stroke-width="1"') + circle(56, 163, 1.8, "none", 'stroke="#fff" stroke-width="1"');
    },

    "art-pennant": function () { // A felt pennant on a little rod, pointing down, with a stitched edge and a hanging tassel.
      return path("M69 146 L38 156 M69 146 L100 156", "none", 'stroke="#8a7aa8" stroke-width="1"') + circle(69, 146, 2, "#d9b24a") +
        rect(34, 154, 70, 4, "#a8743f", 'rx="2"') + path("M38 158 H100 L69 222 Z", "#e8556a") +
        path("M43 161 H95 L69 215 Z", "none", 'stroke="#fff1cd" stroke-width="1.4" stroke-dasharray="3 3"') + path(star(69, 178, 15), "#fff1cd") +
        path("M69 222 V232 M66 222 L63 231 M72 222 L75 231", "none", 'stroke="#ffdc83" stroke-width="1.6" stroke-linecap="round"');                               // The tassel.
    },

    "art-toadstool-note": function () { // A doodled note held up with washi tape, a little crooked, as if it was stuck there by a small paw.
      return group(rect(42, 152, 56, 70, "#fff6df", 'rx="1"') +
        path("M46 200 H94 M46 208 H88 M46 216 H92", "none", 'stroke="#c4b8d8" stroke-width="1"') +                                                                  // Ruled lines.
        group(path("M60 192 Q62 180 62 170 H76 Q76 180 78 192 Z", "#fff1e0", 'stroke="#6a4a6a" stroke-width="1.6"') +
          path("M52 170 Q69 142 86 170 Q69 176 52 170Z", "#e8556a", 'stroke="#6a4a6a" stroke-width="1.6"') + circle(62, 160, 3, "#fff") + circle(75, 158, 3.6, "#fff") + circle(70, 166, 2, "#fff"), 'transform="translate(0 6)"') + // A hand-drawn toadstool, below the tape.
        path("M86 188 c-3 -4 -8 0 0 6 c8 -6 3 -10 0 -6Z", "#ff8ecb") +
        rect(58, 148, 24, 8, "#70ebea", 'opacity=".75" transform="rotate(5 70 152)"'),                                                                                 // The tape.
        'transform="rotate(-4 70 186)"');
    },

    "art-postcard": function () { // A sunset postcard with a striped sun and scalloped stamp edges.
      return rect(26, 160, 88, 58, "#fff1e0", 'rx="2" stroke="#d8c8b0" stroke-width="1" stroke-dasharray="3 2"') + rect(31, 165, 78, 28, "#f98a6a") + rect(31, 165, 78, 12, "#c85a8e") + circle(66, 192, 13, "#ffdc83") +
        rect(46, 186, 40, 2.2, "#f98a6a") + rect(46, 190, 40, 2.2, "#f98a6a") + rect(31, 193, 78, 20, "#2f5e8c") +
        path("M36 200 h20 M62 205 h26 M42 209 h14 M76 199 h22", "none", 'stroke="#ffcfa0" stroke-width="1.6" stroke-linecap="round"') +                             // The sun's path on the sea.
        path("M86 197 h14 l-3 5 h-8 Z M93 197 V186 l7 7 Z", "#1a2348") + rect(99, 167, 9, 11, "#ffffff", 'stroke="#c85a8e" stroke-width="1"') + path(star(103.5, 172.5, 3.4), "#c85a8e");  // A little sailing boat and a stamp.
    },

    "art-triptych": function () { // Three hanging panels sharing one moonlit mountain view: the ridge heights match across the gaps.
      const panel = function (x, ridge, snow) {
        return rect(x, 156, 26, 66, "#1c2452") + path(ridge, "#34447f") + path(snow, "#e6f0ff") + rect(x, 156, 26, 66, "none", 'stroke="#d9b24a" stroke-width="2"');
      };
      return path("M39 150 V156 M69 150 V156 M99 150 V156", "none", 'stroke="#8a7aa8" stroke-width="1"') +
        circle(30, 168, .9, "#fff") + circle(46, 162, 1, "#fff") + circle(92, 164, 1, "#fff") + circle(107, 174, .9, "#fff") + circle(38, 180, .8, "#fff") +
        panel(26, "M26 222 V198 L34 186 L44 200 L52 192 V222 Z", "M34 186 L30 192 L34 191 L38 195 L40 194 Z") +
        panel(56, "M56 222 V192 L64 200 L72 174 L82 196 V222 Z", "M72 174 L68 186 L72 184 L76 187 Z") +
        panel(86, "M86 222 V196 L94 182 L104 202 L112 192 V222 Z", "M94 182 L90 189 L94 188 L98 191 Z") +
        circle(72, 163, 6, "#fff8dc") + circle(75, 161, 5, "#1c2452") + path(star(100, 168, 4), "#ffe9a8", 'class="lp-twinkle"');                                   // The moon, and one bright star.
    },

    "art-sunmoon-clock": function () { // A clock with a sunny morning half and a starry night half.
      let ticks = "";
      for (let i = 0; i < 12; i++) { const a = i / 12 * 6.283; ticks += "M" + round(69 + Math.cos(a) * 28) + " " + round(189 + Math.sin(a) * 28) + " L" + round(69 + Math.cos(a) * 32) + " " + round(189 + Math.sin(a) * 32) + " "; }
      return circle(69, 189, 39, "#c9a15a") + circle(69, 189, 35, "#8a6a2a") + path("M69 155 A34 34 0 0 0 69 223 Z", "#ffcf6e") + path("M69 155 A34 34 0 0 1 69 223 Z", "#27346a") +
        circle(55, 189, 7, "#ffb84a") + path("M55 176 V180 M55 198 V202 M42 189 H46 M64 189 H68 M46 180 L49 183 M64 198 L61 195 M46 198 L49 195 M64 180 L61 183", "none", 'stroke="#ffb84a" stroke-width="2" stroke-linecap="round"') + // The sun and its rays.
        circle(84, 182, 8, "#f4e8c8") + circle(87, 180, 7, "#27346a") + path(star(92, 200, 3.4), "#fff8dc", 'class="lp-twinkle"') + path(star(80, 206, 2.6), "#fff8dc", 'class="lp-twinkle" style="animation-delay:.7s"') + // The moon and stars.
        path(ticks, "none", 'stroke="#fff1e0" stroke-width="1.8"') + line(69, 189, 69, 169, "#3a2147", 2.6, 'stroke-linecap="round"') + line(69, 189, 82, 197, "#3a2147", 2.2, 'stroke-linecap="round"') + circle(69, 189, 3, "#3a2147") + path(star(69, 148, 5), "#ffdc83");
    },

    "art-hoop": function () { // Embroidery in a wooden hoop: dashed stitches for the petals, stem and leaves, French knots in the middle.
      const petal = function (a) { return group(path("M0 -6 C-8 -16 -8 -26 0 -30 C8 -26 8 -16 0 -6Z", "#ff8ecb", 'stroke="#c85a8e" stroke-width="1"'), 'transform="translate(69 184) rotate(' + a + ')"'); };
      return path("M69 146 L60 153 M69 146 L78 153", "none", 'stroke="#c9a15a" stroke-width="1.2"') + circle(69, 146, 2, "#d9b24a") +
        circle(69, 191, 37, "#a8743f") + circle(69, 191, 32, "#fff6df") + rect(61, 152, 16, 5, "#8a5a2a", 'rx="1.5"') +
        path("M69 192 Q68 208 66 220", "none", 'stroke="#5fae86" stroke-width="2.4" stroke-dasharray="3 1.5"') +                                                       // The stem.
        path("M67 207 C57 203 53 209 53 213 C61 215 66 213 67 207 Z", "#7fc49a", 'stroke="#3f8a66" stroke-width="1"') + path("M67 210 C77 205 81 211 81 215 C74 217 68 216 67 210 Z", "#7fc49a", 'stroke="#3f8a66" stroke-width="1"') + // Two leaves.
        [0, 72, 144, 216, 288].map(petal).join("") + circle(69, 184, 6.5, "#ffdc83") + circle(66, 182, 1.4, "#c9901f") + circle(72, 182, 1.4, "#c9901f") + circle(69, 187, 1.4, "#c9901f") +
        circle(50, 207, 1.6, "#ffb84a") + circle(89, 209, 1.6, "#ffb84a");                                                                                              // French-knot flecks.
    },

    "art-stained-cat": function () { // An arched stained-glass window of a winged fairy cat. Every pane has dark leading around it.
      const lead = 'stroke="#1a1430" stroke-width="1.8" stroke-linejoin="round"';
      const cx = 69, cy = 184, rim = 36;
      let wedges = "", panes = "";
      for (let i = 0; i < 6; i++) { // A sunburst of glass fanned around the top of the arch.
        const a1 = Math.PI + i * Math.PI / 6, a2 = a1 + Math.PI / 6;
        wedges += path("M" + cx + " " + cy + " L" + round(cx + Math.cos(a1) * rim) + " " + round(cy + Math.sin(a1) * rim) + " A" + rim + " " + rim + " 0 0 1 " + round(cx + Math.cos(a2) * rim) + " " + round(cy + Math.sin(a2) * rim) + " Z", i % 2 ? "#e8b84a" : "#d96aa3", lead);
      }
      [[33, 184, 22, 20, "#3f7ad0"], [33, 204, 22, 26, "#2f5aa8"], [83, 184, 22, 20, "#3f7ad0"], [83, 204, 22, 26, "#2f5aa8"], [55, 204, 28, 26, "#6d4bb0"]].forEach(function (p) { panes += rect(p[0], p[1], p[2], p[3], p[4], lead); });
      return path("M31 232 V184 A38 38 0 0 1 107 184 V232 Z", "#8a6a2a") + wedges + panes +
        path("M56 188 Q36 178 37 197 Q41 208 56 202 Z", "#a6f0e6", lead) + path("M82 188 Q102 178 101 197 Q97 208 82 202 Z", "#a6f0e6", lead) +                          // The wings.
        path("M53 206 Q69 226 85 206 Q85 216 69 222 Q53 216 53 206Z", "#fce1f2", lead) +                                                                                     // The cat's chest.
        path("M51 176 L55 162 L63 171 Q69 168 75 171 L83 162 L87 176 Q88 202 69 204 Q50 202 51 176 Z", "#fce1f2", lead) +                                                     // Head and ears.
        path("M55 166 L59 172 L54 174 Z M83 166 L79 172 L84 174 Z", "#ff9fc8") + circle(61, 188, 2.6, "#462846") + circle(77, 188, 2.6, "#462846") +
        path("M64 194 Q69 198 74 194", "none", 'stroke="#462846" stroke-width="1.5" stroke-linecap="round"') + path(star(69, 178, 6), "#ffe9a8", 'class="lp-twinkle"');
    },

    "art-butterflies": function () { // A shadow box of six pinned butterflies, every pair of wings a different pattern.
      const wings = function (top, low, kind) { // One side; the group mirrors it for the other.
        return path("M0 -2 C-4 -14 -16 -16 -16 -6 C-16 0 -6 1 0 1Z", top) + path("M0 1 C-6 2 -14 3 -12 9 C-9 14 -3 10 0 4Z", low) +
          (kind ? circle(-9, -7, 2, "#fff8dc") + circle(-7, 7, 1.4, "#fff8dc") : path("M-3 -10 L-13 -7 M-2 5 L-11 8", "none", 'stroke="#fff8dc" stroke-width="1"'));
      };
      const butterfly = function (b, i) {
        const x = 44 + (i % 3) * 25, y = 171 + Math.floor(i / 3) * 33;
        return group(wings(b[0], b[1], b[2]) + group(wings(b[0], b[1], b[2]), 'transform="scale(-1 1)"') + rect(-1.2, -7, 2.4, 16, "#3d2450", 'rx="1"') + circle(0, -8, 1.6, "#d9b24a"), 'transform="translate(' + x + " " + y + ') scale(.72)"') +
          rect(x - 6, y + 13, 12, 2.4, "#fff6df", 'opacity=".8"');                                                                                                     // Its tiny paper label.
      };
      return rect(25, 150, 90, 78, "#c9a15a", 'rx="3"') + rect(29, 154, 82, 70, "#3a1f3a") + rect(32, 157, 76, 64, "#1c2a44") +
        [["#ff8ecb", "#c85a8e", 1], ["#70ebea", "#2f8a9a", 0], ["#ffdc83", "#e98a2a", 1], ["#c9a8ff", "#7a4bb0", 0], ["#ff9d6b", "#d9502a", 1], ["#7fe0a8", "#2f9a66", 0]].map(butterfly).join("");
    },

    "art-moon-mirror": function () { // A gilded magic mirror: in its glass a night sky with a crescent moon, and the outline of a cat that follows Lumi's glow.
      let beads = "", stars = "";
      for (let i = 0; i < 20; i++) { const a = i / 20 * 6.283; beads += circle(round(69 + Math.cos(a) * 36), round(192 + Math.sin(a) * 42), 2.5, i % 2 ? "#ffdc83" : "#d9b24a", i % 5 ? "" : 'class="lp-twinkle" style="animation-delay:' + (i * .1) + 's"'); } // Beads around the frame.
      [[56, 170, 2.6], [84, 168, 2], [52, 196, 2], [90, 196, 2.8], [62, 216, 2], [78, 212, 2.2], [69, 182, 1.6]].forEach(function (s, i) { stars += path(star(s[0], s[1], s[2] * 1.7), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .31 % 2).toFixed(2) + 's"'); });
      return path("M58 150 Q69 136 80 150 L76 156 H62 Z", "#d9b24a") + path(star(69, 146, 5), "#fff8dc", 'class="lp-twinkle"') +                                                              // Its crest.
        oval(69, 192, 40, 46, "#a8782f") + oval(69, 192, 36, 42, "#e8c877") + oval(69, 192, 32, 38, "#6e4e1f") + oval(69, 192, 30, 36, "#0b1038") +
        oval(58, 180, 26, 18, "#3a2a7a", 'opacity=".55"') + oval(84, 208, 22, 16, "#1f4a7a", 'opacity=".6"') + oval(69, 192, 30, 36, "none", 'stroke="' + GLOW + '" stroke-width="1.6" class="lp-neon"') + // The misty glass and its glowing edge.
        circle(80, 176, 11, "#f4e8c8") + circle(84, 174, 10, "#0b1038") + stars +
        path("M57 214 Q57 225 69 225 Q81 225 81 214 V207 L77 199 L71 204 Q69 203 67 204 L61 199 L57 207 Z", GLOW, 'opacity=".9"') + circle(64, 212, 1.8, "#0b1038") + circle(74, 212, 1.8, "#0b1038") +                 // The cat looking out of the mirror.
        path("M50 170 Q54 158 66 156", "none", 'stroke="#ffffff" stroke-width="1.6" opacity=".35" stroke-linecap="round"') + beads;
    },

    // Star Capsule wallpaper
    // Each tile repeats side by side, so everything either stays inside 0..400 or is cut exactly at the tile's edges.

    "wall-bubble-dots": function () { // Staggered gumdrop dots: the rows sit a half-step apart and never touch the tile's edge.
      const colours = ["#ff8ecb", "#70ebea", "#ffdc83", "#c9a8ff"];
      let out = rect(0, 0, 400, 352, "#412150");
      for (let row = 0; row < 7; row++) for (let col = 0; col < 8; col++) {
        const x = (row % 2 ? 37.5 : 12.5) + col * 50, y = 26 + row * 50;
        out += circle(x, y, 11, colours[(row + col) % 4], 'opacity=".78"') + circle(x - 3.5, y - 3.5, 2.6, "#fff", 'opacity=".7"'); // A dot and its shine.
      }
      return out;
    },

    "wall-sprouts": function () { // Rows of two-leaf seedlings poking out of little mounds of soil.
      let out = rect(0, 0, 400, 352, "#173b3f");
      for (let row = 0; row < 5; row++) for (let col = 0; col < 5; col++) {
        const x = (row % 2 ? 60 : 20) + col * 80, y = 40 + row * 68, leaf = (row + col) % 2 ? "#8fe0a8" : "#5fc08e";
        out += group(
          oval(0, 15, 9, 2.6, "#0e282b") +                                                                       // The soil.
          path("M0 15 Q-1 7 0 -2", "none", 'stroke="#c9f0c0" stroke-width="2" stroke-linecap="round"') +         // The stem.
          path("M0 -2 Q-12 -15 -17 -5 Q-10 4 0 -2Z", leaf) + path("M0 -2 Q10 -18 17 -9 Q12 2 0 -2Z", leaf),      // The two leaves.
          'transform="translate(' + x + " " + y + ')"');
      }
      return out;
    },

    "wall-gingham": function () { // Picnic-cloth check: see-through bands that cross, with a berry pair in every fourth square.
      let out = rect(0, 0, 400, 352, "#7a2f58");
      for (let x = 0; x < 400; x += 50) out += rect(x, 0, 25, 352, "#f3a6c9", 'opacity=".28"');
      for (let y = 0; y < 340; y += 50) out += rect(0, y, 400, 25, "#f3a6c9", 'opacity=".28"');
      for (let y = 87.5; y < 340; y += 100) for (let x = 87.5; x < 400; x += 100) {
        out += circle(x - 3, y + 2, 4, "#e8325a") + circle(x + 4, y + 3, 4, "#ff4f73") + path("M" + x + " " + (y - 2) + " q2 -6 8 -6 q-1 6 -8 6Z", "#7fc49a");
      }
      return out;
    },

    "wall-scallops": function () { // Overlapping shells. Every row is cut exactly at the tile's edges, so no row spills over a neighbour.
      const shades = ["#27516a", "#2e6075", "#356e7f", "#2e6075"];
      let out = rect(0, 0, 400, 352, shades[3]);
      for (let row = 0; row < 13; row++) {
        const y = 26 + row * 26, offset = row % 2;
        let edge = offset ? "M0 " + (y - 25) + " A25 25 0 0 1 25 " + y : "M0 " + y, ribs = "";               // Offset rows begin on a half-shell.
        for (let i = 0; i < (offset ? 7 : 8); i++) edge += " a25 25 0 0 1 50 0";                              // One shell's rim.
        if (offset) edge += " A25 25 0 0 1 400 " + (y - 25);                                                  // And end on the other half.
        for (let mid = offset ? 0 : 25; mid <= 400; mid += 50) {                                              // The ribs fanning up from each hinge.
          ribs += "M" + mid + " " + y + " V" + (y - 21) + " ";
          if (mid > 0) ribs += "M" + mid + " " + y + " L" + (mid - 14) + " " + (y - 17) + " ";
          if (mid < 400) ribs += "M" + mid + " " + y + " L" + (mid + 14) + " " + (y - 17) + " ";
        }
        out += path(edge + " V352 H0 Z", shades[row % 4]) + path(ribs, "none", 'stroke="#4f97a0" stroke-width="1"') + path(edge, "none", 'stroke="#7fd6c8" stroke-width="1.6"');
      }
      return out;
    },

    "wall-paper-planes": function () { // A dusk sky of folded planes, each trailing a dashed loop-the-loop. All kept well inside the tile.
      const plane = function (x, y, a, s, paper) {
        return group(
          path("M-22 2 Q-60 16 -80 0 Q-96 -18 -78 -22 Q-60 -22 -66 -4 Q-74 18 -118 12", "none", 'stroke="#cfd8f7" stroke-width="1.6" stroke-dasharray="4 6" stroke-linecap="round" opacity=".55"') + // The flight path behind it.
          path("M28 0 L-20 -15 L-7 0 Z", paper) + path("M28 0 L-7 0 L-16 14 Z", "#b6c1e6") + path("M28 0 L-7 0", "none", 'stroke="#8390bf" stroke-width="1"'), // Top wing, under wing, centre fold.
          'transform="translate(' + x + " " + y + ") rotate(" + a + ") scale(" + s + ')"');
      };
      const cloud = function (x, y, s) { return group(oval(0, 0, 26, 9, "#34407a") + oval(-11, -6, 12, 9, "#34407a") + oval(9, -7, 14, 10, "#34407a"), 'transform="translate(' + x + " " + y + ") scale(" + s + ')"'); };
      return rect(0, 0, 400, 352, "#25315e") +
        scatter(21, 26, function (roll) { return circle(round(roll(6, 394)), round(roll(6, 340)), round(roll(.6, 1.5)), "#e6d5ff", 'opacity="' + round(roll(.3, .8)) + '"'); }) +
        cloud(70, 120, 1.1) + cloud(330, 110, .9) + cloud(52, 268, .9) + cloud(250, 336, 1.1) + cloud(180, 26, .8) +
        plane(150, 62, -10, 1, "#fff") + plane(330, 48, 12, .85, "#ffe8f3") + plane(250, 158, -18, 1, "#fff5d9") + plane(128, 200, 8, .8, "#e6f6ff") +
        plane(366, 226, -14, 1.05, "#fff") + plane(206, 290, 10, .9, "#ffe8f3") + plane(340, 318, -8, .75, "#e6f6ff");
    },

    "wall-wisteria": function () { // Wisteria falling from a vine: each spray is a stack of wide blossoms narrowing to a tip.
      const purples = ["#d9bcff", "#b58cff", "#ef9cff", "#9468e0"];
      const sprays = [[33, 158], [100, 244], [167, 136], [233, 222], [300, 150], [367, 256]];
      let out = rect(0, 0, 400, 352, "#241c44") +
        path("M0 12 Q100 30 200 12 T400 12", "none", 'stroke="#5a7a4f" stroke-width="7"') + path("M0 12 Q100 30 200 12 T400 12", "none", 'stroke="#7ea366" stroke-width="2"');
      sprays.forEach(function (spray, s) {
        const x = spray[0], length = spray[1], steps = Math.floor(length / 12);
        out += oval(x - 10, 24, 9, 3.6, "#7ea366", 'transform="rotate(25 ' + (x - 10) + ' 24)"') + oval(x + 10, 24, 9, 3.6, "#7ea366", 'transform="rotate(-25 ' + (x + 10) + ' 24)"'); // Two leaves.
        for (let i = 0; i < steps; i++) {
          const t = i / steps, y = 30 + i * 12, rx = round(14 - t * 11);                                      // Each clump is narrower than the last, and overlaps it.
          out += oval(x + (i % 2 ? 4 : -4), y, rx, round(9 - t * 5), purples[(i + s) % 4], 'opacity="' + round(.96 - t * .3) + '"');
          if (i % 6 === 1) out += circle(round(x + (i % 2 ? -rx * .5 : rx * .5)), y - 2, 2, "#fff2ff", 'opacity=".8"');  // A pale highlight blossom now and then.
        }
      });
      return out;
    },

    "wall-toadstool": function () { // A dusk meadow: rolling hills whose ends match, tall shadow toadstools behind, glowing little ones in front.
      const toadstool = function (t, i) {
        const cap = t[3];
        return group(
          oval(0, -16, 22, 20, cap, 'opacity=".09"') +                                 // A faint glow.
          path("M-5 0 Q-3 -9 -3 -16 H3 Q3 -9 5 0 Z", "#fff1e0") +                       // The stem.
          path("M-16 -14 Q0 -38 16 -14 Q0 -8 -16 -14Z", cap, i % 2 ? "" : 'class="lp-twinkle" style="animation-delay:' + i * .3 + 's"') + // The cap.
          circle(-6, -20, 2.4, "#fff") + circle(5, -24, 3, "#fff") + circle(8, -15, 1.8, "#fff"),
          'transform="translate(' + t[0] + " " + t[1] + ") scale(" + t[2] + ')"');
      };
      const giant = function (x, top, s) { // A towering toadstool in shadow, on the far hill.
        return group(path("M-14 280 Q-10 200 -9 150 H9 Q10 200 14 280 Z", "#1a3050") + path("M-60 158 Q0 60 60 158 Q0 178 -60 158Z", "#273f6a") +
          circle(-22, 128, 8, "#3a5688") + circle(14, 112, 10, "#3a5688") + circle(32, 142, 6, "#3a5688"),
          'transform="translate(' + x + " " + top + ") scale(" + s + ')"');
      };
      return rect(0, 0, 400, 352, "#1c2048") + rect(0, 200, 400, 152, "#2a2a5e", 'opacity=".6"') +
        scatter(13, 30, function (roll) { return circle(round(roll(6, 394)), round(roll(6, 200)), round(roll(.5, 1.3)), "#e6d5ff", 'opacity="' + round(roll(.3, .9)) + '"'); }) +
        giant(76, -6, 1) + giant(320, 40, .75) + giant(214, 92, .55) +
        path("M0 262 Q100 214 200 262 T400 262 V352 H0 Z", "#23405a") +
        path("M0 294 Q50 268 100 294 T200 294 T300 294 T400 294 V352 H0 Z", "#2f5a4f") +
        path("M0 324 Q66 306 133 324 T266 324 T400 324 V352 H0 Z", "#2c6a4f") +
        [[44, 318, 1.5, "#e8556a"], [106, 298, 0.95, "#ffb84a"], [164, 332, 1.8, "#ff8ecb"], [232, 302, 1.1, "#70ebea"], [288, 334, 1.6, "#e8556a"], [346, 300, 1.2, "#c9a8ff"], [376, 336, 0.8, "#ffb84a"]].map(toadstool).join("") +
        scatter(14, 16, function (roll, i) { return circle(round(roll(16, 384)), round(roll(170, 300)), 2, "#fff4a8", 'class="lp-twinkle" style="animation-delay:' + (i * .31 % 2).toFixed(2) + 's"'); });
    },

    "wall-bookcase": function () { // Floor-to-ceiling bookshelves in muted colours, with a little curio tucked in here and there.
      const spines = ["#7a3f5c", "#3f6a7a", "#9a7a3f", "#5a4a85", "#4a7a5a", "#8a4a3f", "#6a5a78", "#a25a72"], roll = dice(77);
      let out = rect(0, 0, 400, 352, "#201410") + rect(0, 0, 5, 352, "#6b4426") + rect(395, 0, 5, 352, "#6b4426");  // Backing, and the upright that sits on the tile's join.
      for (let shelf = 0; shelf < 4; shelf++) {
        const base = 80 + shelf * 88;
        let x = 10;
        while (x < 372) {
          if (roll(0, 1) > .9 && x < 340) { // Instead of a book, a curio: a jar, a globe or a star on a block.
            const kind = Math.floor(roll(0, 3)), mid = x + 15;
            if (kind === 0) out += rect(mid - 9, base - 22, 18, 22, "#bfe8ff", 'rx="4" opacity=".45"') + rect(mid - 7, base - 26, 14, 5, "#a8743f", 'rx="1"') + circle(mid, base - 11, 4, "#ffdc83", 'class="lp-twinkle"');
            else if (kind === 1) out += circle(mid, base - 18, 13, "#4a7aa8") + path("M" + (mid - 7) + " " + (base - 26) + " q6 -3 10 2 q-2 5 -9 3 Z", "#7fc49a") + rect(mid - 7, base - 5, 14, 5, "#6b4426", 'rx="1"');
            else out += rect(mid - 10, base - 8, 20, 8, "#6b4426", 'rx="1"') + path(star(mid, base - 24, 13), "#ffdc83");
            x += 34;
            continue;
          }
          const w = Math.round(roll(13, 24)), h = Math.round(roll(46, 68)), colour = spines[Math.floor(roll(0, spines.length))];
          if (x + w > 391) break;
          out += rect(x, base - h, w, h, colour, 'rx="1.5"');                                                                 // The spine.
          if (roll(0, 1) > .62) out += rect(x + 2, base - h + 9, w - 4, 3, "#e8c877", 'opacity=".75"');                       // A gold title band on some of them.
          x += w + 1;
        }
        out += rect(0, base, 400, 8, "#6b4426");                                                                              // The shelf board, running edge to edge.
      }
      return out;
    },

    "wall-reef": function () { // Looking through the side of a coral reef: light shafts, swaying weed, fish and rising bubbles.
      const fish = function (f) {
        return group(oval(0, 0, 13, 7, f[4]) + path("M11 0 L22 -7 L20 0 L22 7 Z", f[4]) + path("M-3 -7 Q0 0 -3 7 M3 -7 Q6 0 3 7", "none", 'stroke="#ffffff" stroke-width="2" opacity=".55"') + circle(-8, -2, 1.7, "#1a2a3a"),
          'transform="translate(' + f[0] + " " + f[1] + ") scale(" + (f[3] ? -f[2] : f[2]) + " " + f[2] + ')"'); // f[3] flips it to swim right.
      };
      const coral = function (x, s, colour) { // A branching coral: one stroked path of forks.
        return path("M0 0 V-40 M0 -14 L-15 -34 M0 -24 L13 -46 M-15 -34 L-22 -50 M-15 -34 L-6 -52 M13 -46 L6 -60 M13 -46 L22 -58", "none", 'stroke="' + colour + '" stroke-width="5" stroke-linecap="round" transform="translate(' + x + " 328) scale(" + s + ')"');
      };
      const weed = function (x, h, colour) { return path("M" + x + " 330 q-14 -" + h / 4 + " 0 -" + h / 2 + " q14 -" + h / 4 + " 0 -" + h / 2, "none", 'stroke="' + colour + '" stroke-width="5" stroke-linecap="round"'); };
      return rect(0, 0, 400, 352, "#0f3a5c") + path("M0 26 Q100 10 200 26 T400 26 V0 H0 Z", "#3fa0b8", 'opacity=".55"') + path("M0 92 Q100 72 200 92 T400 92 V0 H0 Z", "#1c6a8a", 'opacity=".45"') +
        [[40, 58], [130, 42], [232, 62], [318, 46]].map(function (s) { return path("M" + s[0] + " 0 H" + (s[0] + s[1]) + " L" + (s[0] + s[1] + 18) + " 318 H" + (s[0] - 18) + " Z", "#d2fcf6", 'opacity=".07"'); }).join("") + // The light shafts.
        path("M0 322 Q100 306 200 322 T400 322 V352 H0 Z", "#b9a37a") + path("M0 336 Q66 326 133 336 T266 336 T400 336 V352 H0 Z", "#9a8660") +
        weed(30, 120, "#3f9a6e") + weed(46, 88, "#5fbf86") + weed(214, 110, "#3f9a6e") + weed(372, 136, "#5fbf86") + weed(356, 96, "#3f9a6e") +
        coral(86, 1.2, "#ff8e8e") + coral(150, .9, "#ffb84a") + coral(276, 1.3, "#ff8ecb") + coral(326, .8, "#c98cff") +
        path("M186 326 a26 22 0 0 1 52 0 Z", "#e8556a") + path("M192 322 q6 -14 16 -14 M208 304 q12 0 20 18 M200 320 q8 -8 16 -6", "none", 'stroke="#ff9db0" stroke-width="2.4" stroke-linecap="round"') + // A dome of brain coral.
        path(star(120, 330, 10), "#ffb84a") + path(star(250, 340, 8), "#ff8e8e") +
        [[70, 118, 1.2, 1, "#ffb84a"], [168, 76, .9, 0, "#70ebea"], [296, 130, 1.1, 0, "#ff8ecb"], [352, 58, .8, 1, "#ffe9a8"], [210, 196, 1.3, 1, "#c98cff"], [84, 236, .8, 0, "#70ebea"], [332, 232, 1, 1, "#ffb84a"]].map(fish).join("") +
        scatter(24, 18, function (roll, i) { return circle(round(roll(10, 390)), round(roll(10, 300)), round(roll(1.5, 4.5)), "none", 'stroke="#cfe8ff" stroke-width="1" opacity=".7"' + (i % 4 ? "" : ' class="lp-twinkle"')); });
    },

    "wall-aurora": function () { // Curtains of northern light over snowy peaks. Each curtain is one path of many thin upright rays.
      const wave = function (x, base, amp, lobes, flip) { // A curve that is level at both tile edges, so the curtains meet at the join.
        const t = x / 400 * Math.PI * 2 * lobes;
        return round(base + flip * (amp * Math.cos(t) + amp * .4 * Math.cos(t * 2)));
      };
      const curtain = function (base, amp, lobes, flip, length, colour) {
        let top = "", under = "", rays = "", hem = "";
        for (let x = 0; x <= 400; x += 10) {                                                                                 // The curtain's top and bottom edges.
          top += (x ? " L" : "M") + x + " " + wave(x, base, amp, lobes, flip);
          under = " L" + x + " " + wave(x, base + length, amp, lobes, flip) + under;
          hem += (x ? " L" : "M") + x + " " + wave(x, base + length, amp, lobes, flip);
        }
        for (let x = 3; x < 400; x += 5) {                                                                                   // Rays of uneven height, every 5 units.
          const y = wave(x, base, amp, lobes, flip), drop = round(length * (.72 + .28 * Math.sin(x / 400 * Math.PI * 2 * lobes * 3 + base)));
          rays += "M" + x + " " + y + " V" + round(y + drop) + " ";
        }
        return path(top + under + " Z", colour, 'opacity=".13"') +                                                           // The soft body of the curtain.
          path(rays, "none", 'stroke="' + colour + '" stroke-width="2.8" opacity=".36"') +                                   // The rays.
          path(hem, "none", 'stroke="#ffffff" stroke-width="1.4" opacity=".4"');                                             // The bright hem along the bottom.
      };
      const peaks = "M0 300 L40 238 L84 274 L140 204 L190 266 L240 222 L300 276 L352 232 L400 300 V352 H0 Z";
      return rect(0, 0, 400, 352, "#060a26") + rect(0, 170, 400, 182, "#0e1a44", 'opacity=".75"') +
        scatter(31, 44, function (roll) { return circle(round(roll(4, 396)), round(roll(4, 250)), round(roll(.5, 1.4)), "#fff", 'opacity="' + round(roll(.35, 1)) + '"'); }) +
        curtain(26, 22, 2, 1, 96, "#7fffd4") + curtain(80, 28, 1, -1, 86, "#70ebea") + curtain(124, 20, 3, 1, 70, "#c98cff") + curtain(166, 16, 2, -1, 52, "#ff8ecb") +
        [[58, 44, 7], [176, 28, 5], [312, 60, 8], [96, 150, 5], [352, 134, 6], [234, 72, 6], [24, 200, 4], [272, 188, 5]].map(function (s, i) { return path(star(s[0], s[1], s[2]), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (i * .37 % 2).toFixed(2) + 's"'); }).join("") +
        path(peaks, "#232d66") +
        path("M40 238 L27 258 L37 254 L45 262 L54 250 Z M140 204 L121 230 L134 225 L146 236 L160 228 Z M240 222 L227 240 L238 236 L248 244 L258 236 Z M352 232 L338 248 L350 245 L359 252 L367 248 Z", "#e6f0ff") +
        path("M0 318 Q100 292 200 318 T400 318 V352 H0 Z", "#101a45") +
        scatter(32, 18, function (roll) { const x = round(roll(14, 386)), h = round(roll(12, 26)); return path("M" + round(x - h * .36) + " 350 L" + x + " " + round(350 - h) + " L" + round(x + h * .36) + " 350 Z", "#091233"); }) +
        rect(292, 330, 16, 12, "#1a2452") + path("M288 332 L300 322 L312 332 Z", "#27346a") + rect(298, 334, 4, 5, "#ffdc83", 'class="lp-twinkle"'); // A tiny cabin with one warm window.
    },
  };

  // ---- The whole room. It is drawn 1000 wide and 500 tall and fills whatever it is given (the whole screen on a TV
  // or computer, a card on a phone), cropping its sides rather than squashing. The furniture above is drawn in a
  // 400-wide block in the middle; the wall and floor repeat across the full width; and there are three windows, the
  // middle one and one out to each side.
  const WIDE = 1000, MIDDLE = 300, TILES = [-100, 300, 700], WINDOWS = [-50, MIDDLE, 650]; // Where the 400-wide pieces sit.
  const NUDGE = { plant: 82 }; // Plants are drawn beside the door but stand between the lamp and Lumi, clear of both.
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
      const places = REPEATED[spot] || [MIDDLE + (NUDGE[spot] || 0)];
      return group(art ? places.map(function (x) { return group(art, 'transform="translate(' + x + ' 0)"'); }).join("") : "",
        'data-spot="' + spot + '"' + (spot === "wallpaper" ? ' mask="url(#lp-wall-mask)"' : "") + (spot === "view" && open ? ' data-open="true"' : ""));
    }).join("");
  }

  return { LAYERS: LAYERS, ART: ART, CLIPS: CLIPS, WINDOWS: WINDOWS, WIDE: WIDE, render: render };
});
