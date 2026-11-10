/* The art for what Lumi wears: each accessory drawn as plain SVG over (or behind) her own drawing, in her 320 x 300
   box (templates/games/lumi-pet.html). The server's catalog (mnemerynne/lumipet_data/closet.json) says which
   accessories exist, their slot, rarity and price; this file only knows how each looks. Every catalog id needs an
   entry here (tests/js/lumi_closet.test.mjs checks). One accessory per slot, paperdoll style.

   `layer` is where it is drawn: "back" (behind her body: capes, packs) or "front" (over her). Accessories are for
   looks only; nothing here changes a fight. All authored here: nothing a person typed goes into this markup. */
(function (root, make) {
  const closet = make();
  if (typeof module === "object" && module.exports) module.exports = closet; else root.LumiPetCloset = closet;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const EDGE = 'stroke="#462846" stroke-opacity=".5" stroke-width="2" stroke-linejoin="round"';
  function p(d, fill, more) { return '<path d="' + d + '" fill="' + fill + '" ' + (more === undefined ? EDGE : more) + "/>"; }
  function c(x, y, r, fill, more) { return '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" fill="' + fill + '" ' + (more === undefined ? EDGE : more) + "/>"; }
  function o(x, y, rx, ry, fill, more) { return '<ellipse cx="' + x + '" cy="' + y + '" rx="' + rx + '" ry="' + ry + '" fill="' + fill + '" ' + (more === undefined ? EDGE : more) + "/>"; }
  function star(x, y, r) { // A four-pointed sparkle.
    const s = r * 0.3;
    return "M" + x + " " + (y - r) + " L" + (x + s) + " " + (y - s) + " L" + (x + r) + " " + y + " L" + (x + s) + " " + (y + s) + " L" + x + " " + (y + r) +
      " L" + (x - s) + " " + (y + s) + " L" + (x - r) + " " + y + " L" + (x - s) + " " + (y - s) + "Z";
  }
  function bow(x, y, size, fill) { // A ribbon bow: two loops and a knot.
    const w = size, h = size * .7;
    return p("M" + x + " " + y + " L" + (x - w) + " " + (y - h) + " L" + (x - w) + " " + (y + h) + "Z M" + x + " " + y + " L" + (x + w) + " " + (y - h) + " L" + (x + w) + " " + (y + h) + "Z", fill) + c(x, y, size * .28, fill);
  }
  const LINE = 'stroke-linecap="round" fill="none"';

  const ART = {
    // Head
    "acc-bow": { layer: "front", svg: bow(212, 72, 20, "#ff8ecb") },
    "acc-star-clip": { layer: "front", svg: p(star(112, 86, 13), "#ffdc83") },
    "acc-flower-crown": { layer: "front", svg: p("M112 92 Q160 70 208 92", "none", 'stroke="#7fc49a" stroke-width="4" ' + LINE) +
      [[116, 90, "#ff8ecb"], [138, 81, "#fff1cd"], [160, 78, "#c9a8ff"], [182, 81, "#fff1cd"], [204, 90, "#ff8ecb"]].map(function (f) {
        return [0, 72, 144, 216, 288].map(function (a) { const r = a * Math.PI / 180; return c(Math.round((f[0] + 5 * Math.cos(r)) * 10) / 10, Math.round((f[1] + 5 * Math.sin(r)) * 10) / 10, 4, f[2], ""); }).join("") + c(f[0], f[1], 2.6, "#ffdc83", "");
      }).join("") },
    "acc-witch-hat": { layer: "front", svg: p("M122 82 Q150 40 176 2 Q188 44 198 82 Z", "#5a3fa8") + o(160, 84, 64, 12, "#4a3190") + p("M126 74 Q160 84 195 74 L197 82 Q160 92 124 82 Z", "#ffdc83") + p(star(170, 40, 7), "#ffdc83", "") },
    "acc-crown": { layer: "front", svg: p("M126 86 L122 50 L142 66 L160 40 L178 66 L198 50 L194 86 Z", "#ffdc83") + c(160, 72, 5, "#ff8ecb") + c(138, 76, 3.5, "#70ebea") + c(182, 76, 3.5, "#70ebea") + p(star(160, 34, 7), "#fff8dc", "") },
    // Neck
    "acc-bell-collar": { layer: "front", svg: p("M118 196 Q160 216 202 196", "none", 'stroke="#e8556a" stroke-width="8" ' + LINE) + c(160, 214, 8, "#ffdc83") + p("M156 216 H164", "none", 'stroke="#8a6a2a" stroke-width="2" ' + LINE) },
    "acc-bowtie": { layer: "front", svg: bow(160, 210, 16, "#70ebea") },
    "acc-scarf": { layer: "front", svg: p("M114 192 Q160 220 206 192 L208 206 Q160 234 112 206 Z", "#ff9d6b") + p("M184 212 L200 208 L206 250 L190 252 Z", "#ff9d6b") + p("M190 238 L205 236 M191 245 L206 243", "none", 'stroke="#fff1cd" stroke-width="2.5"') },
    "acc-star-pendant": { layer: "front", svg: p("M124 196 Q160 222 196 196", "none", 'stroke="#d9b24a" stroke-width="2" ' + LINE) + p(star(160, 222, 11), "#ffdc83") },
    // Wings
    "acc-wing-ribbons": { layer: "front", svg: bow(108, 178, 11, "#c9a8ff") + bow(212, 178, 11, "#c9a8ff") },
    "acc-wing-glitter": { layer: "front", svg: [[52, 120, 7], [74, 150, 5], [40, 148, 4], [268, 120, 7], [246, 150, 5], [280, 148, 4], [66, 206, 4], [254, 206, 4]].map(function (s) { return p(star(s[0], s[1], s[2]), "#fff8dc", 'opacity=".95"'); }).join("") },
    "acc-wing-aurora": { layer: "front", svg: ["#70ebea", "#c9a8ff", "#ff8ecb"].map(function (colour, i) {
      const inset = i * 9;
      return p("M" + (108 - inset * .4) + " " + (170 - inset * .3) + " C" + (22 + inset) + " " + (180 - inset) + " " + (36 + inset) + " " + (88 + inset) + " " + (80 + inset * .2) + " " + (112 + inset * .5) +
        " M" + (212 + inset * .4) + " " + (170 - inset * .3) + " C" + (298 - inset) + " " + (180 - inset) + " " + (284 - inset) + " " + (88 + inset) + " " + (240 - inset * .2) + " " + (112 + inset * .5), "none", 'stroke="' + colour + '" stroke-width="4" opacity=".85" ' + LINE);
    }).join("") },
    // Back (drawn behind her)
    "acc-backpack": { layer: "back", svg: p("M88 186 q0 -14 14 -14 h116 q14 0 14 14 v52 q0 14 -14 14 h-116 q-14 0 -14 -14 Z", "#e8556a") + p("M88 214 h144", "none", 'stroke="#b83a4e" stroke-width="3"') },
    "acc-cape": { layer: "back", svg: p("M104 186 Q60 250 78 290 Q160 300 242 290 Q260 250 216 186 Z", "#c0392b") + p("M104 186 Q84 240 92 288 M216 186 Q236 240 228 288", "none", 'stroke="#8f2a20" stroke-width="2" opacity=".6"') },
    "acc-jetpack": { layer: "back", svg: [84, 214].map(function (x) {
      return p("M" + x + " 190 q0 -10 11 -10 q11 0 11 10 v48 h-22 Z", "#a8b4c4") + p("M" + (x + 2) + " 238 h18 l-4 10 h-10 Z", "#6a7484") + p("M" + (x + 5) + " 248 q6 24 6 28 q0 -4 6 -28 Z", "#ffb84a", 'class="lp-twinkle"') + p("M" + x + " 204 h22", "none", 'stroke="#6a7484" stroke-width="2"');
    }).join("") },
    // Paws
    "acc-socks": { layer: "front", svg: [123, 197].map(function (x) { return o(x, 259, 25, 14, "#70ebea") + p("M" + (x - 22) + " 255 Q" + x + " 249 " + (x + 22) + " 255 M" + (x - 24) + " 262 Q" + x + " 256 " + (x + 24) + " 262", "none", 'stroke="#fff" stroke-width="3" opacity=".8"'); }).join("") },
    "acc-boots": { layer: "front", svg: [123, 197].map(function (x) { return p("M" + (x - 22) + " 244 h44 v16 q0 12 -12 12 h-22 q-12 0 -12 -12 Z", "#ffdc83") + p("M" + (x - 24) + " 246 h48", "none", 'stroke="#d9b24a" stroke-width="4" ' + LINE); }).join("") },
    "acc-star-mitts": { layer: "front", svg: [123, 197].map(function (x) { return o(x, 259, 26, 15, "#c9a8ff") + p(star(x, 259, 9), "#ffdc83", ""); }).join("") },
    // Tail
    "acc-tail-bow": { layer: "front", svg: bow(270, 196, 13, "#ff8ecb") },
    "acc-tail-ring": { layer: "front", svg: o(266, 240, 13, 7, "none", 'stroke="#ffdc83" stroke-width="5" transform="rotate(-25 266 240)"') + c(276, 234, 3, "#70ebea") },
    "acc-comet-trail": { layer: "front", svg: [[282, 184, 9], [296, 168, 6], [306, 154, 4], [290, 204, 4]].map(function (s, i) { return p(star(s[0], s[1], s[2]), i % 2 ? "#70ebea" : "#ffdc83", 'class="lp-twinkle" style="animation-delay:' + (i * .4) + 's"'); }).join("") },

    // ---- Star Capsule prizes: won from the capsules, never sold (the "rarity" entries in the catalog).
    // Star Capsule back
    // Everything here is drawn behind her, so the hidden ends of sticks and strings simply vanish behind her body.
    // A giant swirl lollipop strapped behind her, poking up over one shoulder with a ribbon on its stick.
    "acc-giant-lollipop": { layer: "back", svg: (function () {
      let spiral = "";
      for (let d = 0; d <= 1260; d += 20) { // Three and a half turns, outwards from the middle.
        const a = d * Math.PI / 180, r = 2 + d / 1260 * 27;
        spiral += (d ? " L" : "M") + Math.round((262 + r * Math.cos(a)) * 10) / 10 + " " + Math.round((70 + r * Math.sin(a)) * 10) / 10;
      }
      return p("M198 210 L250 100", "none", 'stroke="#fff1cd" stroke-width="8" ' + LINE) + p("M198 210 L250 100", "none", 'stroke="#d9b98a" stroke-width="2" opacity=".7" ' + LINE) +
        c(262, 70, 34, "#fff8dc") + p(spiral, "none", 'stroke="#ff8ecb" stroke-width="5" ' + LINE) + bow(245, 110, 12, "#70ebea");
    })() },
    // A wanderer's stick over the shoulder with a spotty bundle knotted to the far end.
    "acc-bindle": { layer: "back", svg: p("M40 124 C12 132 12 184 44 184 C76 184 76 132 48 124 Z", "#4a68c8") +
      [[32, 148, 4], [52, 144, 3.5], [26, 166, 3], [44, 166, 4.5], [60, 162, 3], [38, 178, 2.5]].map(function (d) { return c(d[0], d[1], d[2], "#fff8dc", ""); }).join("") +
      p("M130 178 L16 96", "none", 'stroke="#a8743e" stroke-width="7" ' + LINE) +
      p("M44 124 L28 108 L37 125 Z M44 124 L62 112 L51 126 Z", "#2e4a96") + c(44, 124, 5.5, "#2e4a96") },
    // A shiny heart balloon on a wavy string, tied somewhere behind her.
    "acc-heart-balloon": { layer: "back", svg: p("M262 92 Q246 118 262 142 Q272 170 212 204", "none", 'stroke="#fff1cd" stroke-width="2" ' + LINE) +
      p("M262 88 C222 66 226 22 250 20 C258 20 262 26 262 32 C262 26 266 20 274 20 C298 22 302 66 262 88Z", "#ff5f8f") + p("M257 92 L262 86 L267 92 Z", "#c43b6c") +
      p("M240 34 Q234 44 238 56", "none", 'stroke="#fff" stroke-width="4" opacity=".8" ' + LINE) },
    // A little teddy bear riding piggyback, peering round her side and waving.
    "acc-teddy-rider": { layer: "back", svg: o(82, 232, 26, 24, "#c98f58") + o(54, 216, 8, 15, "#c98f58", EDGE + ' transform="rotate(32 54 216)"') +
      c(58, 168, 9, "#c98f58") + c(98, 168, 9, "#c98f58") + c(58, 168, 4.5, "#ffc6e4", "") + c(98, 168, 4.5, "#ffc6e4", "") + c(78, 186, 25, "#d8a26b") + o(78, 196, 13, 9.5, "#fff1cd", "") +
      c(69, 183, 3.5, "#462846", "") + c(87, 183, 3.5, "#462846", "") + o(78, 192, 4, 3, "#462846", "") + p("M72 199 Q78 204 84 199", "none", 'stroke="#462846" stroke-width="2" ' + LINE) +
      bow(82, 214, 11, "#e8556a") },
    // A tilted parasol: alternate pink and cream panels, curved ribs, a scalloped lace hem and a curly handle reaching down behind her.
    "acc-lace-parasol": { layer: "back", svg: (function () {
      const hem = [-75, -50, -25, 0, 25, 50, 75]; // Where the ribs meet the hem, in the parasol's own tilted coordinates.
      let panels = "";
      hem.slice(0, 6).forEach(function (x, i) {
        panels += p("M0 -60 Q" + (x * .8) + " -46 " + x + " 0 A13 8 0 0 0 " + (x + 25) + " 0 Q" + ((x + 25) * .8) + " -46 0 -60Z", i % 2 ? "#fff1cd" : "#ff9fd2");
      });
      return '<g transform="translate(72 74) rotate(-35)">' + p("M0 0 V150", "none", 'stroke="#a8743e" stroke-width="6" ' + LINE) + panels +
        hem.map(function (x) { return c(x, 0, 3, "#fff8dc"); }).join("") + hem.slice(0, 6).map(function (x) { return c(x + 12.5, 8, 2.6, "#fff8dc", 'stroke="#ff8ecb" stroke-width="1"'); }).join("") +
        c(0, -64, 6, "#ffdc83") + "</g>";
    })() },
    // A little air tank peeking from her side with a hose and nozzle, blowing a stream of see-through bubbles.
    "acc-bubble-pack": { layer: "back", svg: p("M76 202 q0 -12 14 -12 h18 v62 h-18 q-14 0 -14 -12 Z", "#70ebea") + p("M76 214 h32 M76 228 h32", "none", 'stroke="#2a8c94" stroke-width="2.5"') +
      c(88, 202, 5.5, "#fff8dc") + p("M88 202 L91 199", "none", 'stroke="#e8556a" stroke-width="1.8" ' + LINE) + p("M86 190 Q84 170 66 170", "none", 'stroke="#c9a8ff" stroke-width="5" ' + LINE) + p("M70 164 L58 168 L60 176 L70 176 Z", "#ffdc83") +
      [[46, 156, 11, 0], [28, 128, 8, .4], [58, 124, 14, .9], [34, 92, 10, 1.3], [66, 84, 7, .2], [44, 56, 13, .7], [84, 42, 6, 1.5], [26, 40, 6, 1], [270, 120, 10, .5], [292, 88, 7, 1.2], [262, 62, 12, .1], [286, 34, 6, .8]].map(function (b) {
        return '<g class="lp-twinkle" style="animation-delay:' + b[3] + 's">' + c(b[0], b[1], b[2], "#bff6f4", 'fill-opacity=".2" stroke="#d6fbf8" stroke-width="2"') +
          p("M" + (b[0] - b[2] * .55) + " " + (b[1] - b[2] * .2) + " Q" + (b[0] - b[2] * .5) + " " + (b[1] - b[2] * .6) + " " + (b[0] - b[2] * .1) + " " + (b[1] - b[2] * .65), "none", 'stroke="#fff" stroke-width="2" ' + LINE) + "</g>";
      }).join("") },
    // A great brass star-reading instrument hung behind her like a halo: graduated rings, two tilted orbit hoops, planets on the rim and a star at the very top.
    "acc-astrolabe-ring": { layer: "back", svg: (function () {
      const r = function (n) { return Math.round(n * 10) / 10; }, cx = 160, cy = 178;
      let ticks = "";
      for (let d = 0; d < 360; d += 7.5) { const a = d * Math.PI / 180, long = d % 45 === 0 ? 100 : 106; ticks += "M" + r(cx + long * Math.cos(a)) + " " + r(cy + long * Math.sin(a)) + " L" + r(cx + 112 * Math.cos(a)) + " " + r(cy + 112 * Math.sin(a)) + " "; } // A short mark every 7.5 degrees and a long one every 45.
      function hoop(angle, colour) { return o(cx, cy, 116, 40, "none", 'stroke="' + colour + '" stroke-width="2.5" transform="rotate(' + angle + " " + cx + " " + cy + ')"'); }
      const planets = [[150, "#ff9d6b", 8], [205, "#70ebea", 6], [335, "#c9a8ff", 8], [28, "#ff8ecb", 6], [62, "#a9ee9f", 5], [118, "#ffdc83", 5]].map(function (s) { // Angles picked so each sits where her body won't hide it.
        const a = s[0] * Math.PI / 180, x = r(cx + 117 * Math.cos(a)), y = r(cy + 117 * Math.sin(a));
        return c(x, y, s[2], s[1]) + c(r(x - s[2] * .3), r(y - s[2] * .3), r(s[2] * .28), "#fff", 'opacity=".8"');
      }).join("");
      return hoop(30, "#c9a8ff") + hoop(-30, "#70ebea") + c(cx, cy, 117, "none", 'stroke="#b58a2e" stroke-width="7"') + c(cx, cy, 117, "none", 'stroke="#ffdc83" stroke-width="4"') + c(cx, cy, 97, "none", 'stroke="#ffdc83" stroke-width="2" opacity=".85"') +
        p(ticks, "none", 'stroke="#ffe9ad" stroke-width="1.6" ' + LINE) + planets +
        p(star(160, 61, 15), "#fff8dc", 'stroke="#b58a2e" stroke-width="1.5" class="lp-neon"') +
        p(star(62, 100, 5), "#fff8dc", 'class="lp-twinkle"') + p(star(262, 104, 5), "#fff8dc", 'class="lp-twinkle" style="animation-delay:.6s"') + p(star(46, 234, 4), "#70ebea", 'class="lp-twinkle" style="animation-delay:1.2s"');
    })() },

    // Star Capsule head
    // A two-leaf seedling growing straight out of the top of her head.
    "acc-sprout": { layer: "front", svg: p("M160 92 Q157 78 160 64", "none", 'stroke="#4f9e63" stroke-width="5" ' + LINE) +
      p("M160 68 C148 44 124 42 112 50 C116 70 142 80 160 68Z", "#8ddc8f") + p("M160 64 C170 36 196 32 210 40 C208 62 182 76 160 64Z", "#a9ee9f") +
      p("M154 65 Q136 56 122 53 M166 60 Q184 50 200 44", "none", 'stroke="#4f9e63" stroke-width="2.5" ' + LINE) },
    // A cone tilted a little to one side, two wrapped stripes, a fluffy pom on the tip and a couple of confetti dots.
    "acc-party-hat": { layer: "front", svg: p("M172 28 L134 92 Q164 98 192 88 Z", "#70ebea") +
      p("M164.4 40.8 L179 49 L183 61 L156.8 53.6 Z", "#ff8ecb", "") + p("M149.2 66.4 L187 73 L191 85 L141.6 79.2 Z", "#ffdc83", "") +
      [[166, 28, 7], [178, 28, 7], [172, 19, 7], [172, 28, 8]].map(function (b) { return c(b[0], b[1], b[2], "#fff1cd"); }).join("") +
      c(126, 66, 3.5, "#ff8ecb", "") + c(212, 62, 3.5, "#ffdc83", "") + c(204, 40, 2.5, "#c9a8ff", "") },
    // Two knitted caps pulled over her ear tips: ribbed hem, stitch lines and a pom-pom on each.
    "acc-ear-cosies": { layer: "front", svg: [0, 1].map(function (m) {
      return "<g" + (m ? ' transform="matrix(-1 0 0 1 320 0)"' : "") + ">" +
        p("M86 92 L87 62 Q89 44 102 55 L126 78 Q106 80 86 92Z", "#c9a8ff") +
        p("M84 96 Q104 80 130 80 L124 69 Q102 72 84 84 Z", "#fff1cd") +
        p("M92 90 L91 80 M101 85 L99 75 M111 81 L109 71 M120 78 L118 69", "none", 'stroke="#d9b24a" stroke-width="2" ' + LINE) +
        p("M94 72 Q98 64 96 56 M104 70 Q106 66 104 60", "none", 'stroke="#8a6cf0" stroke-width="2" ' + LINE) +
        c(91, 48, 11, "#ff8ecb") + p("M85 44 L95 52 M95 43 L86 53 M91 40 V56", "none", 'stroke="#ffc6e4" stroke-width="2" ' + LINE) + "</g>";
    }).join("") },
    // A red spotted mushroom cap with a cream frill of gills along the brim.
    "acc-toadstool-cap": { layer: "front", svg: p("M126 90 Q160 104 194 90 L192 84 Q160 96 128 84 Z", "#fff1cd") +
      p("M118 86 C118 46 148 30 160 30 C172 30 202 46 202 86 Q160 100 118 86Z", "#e8556a") +
      p("M136 88 L134 94 M148 91 L147 97 M160 92 V98 M172 91 L173 97 M184 88 L186 94", "none", 'stroke="#d9b24a" stroke-width="2" ' + LINE) +
      [[140, 58, 9], [168, 46, 11], [188, 68, 8], [158, 74, 7], [128, 76, 4.5], [183, 44, 4]].map(function (s) { return c(s[0], s[1], s[2], "#fff8dc", 'stroke="#b83a4e" stroke-opacity=".5" stroke-width="1.5"'); }).join("") +
      p("M126 66 Q132 46 148 38", "none", 'stroke="#ffc1c9" stroke-width="3" opacity=".8" ' + LINE) },
    // A tiny ringed planet held up by a gold circlet: the ring passes behind and then in front of the globe, a moon keeps it company.
    "acc-planet-topper": { layer: "front", svg: p("M118 94 Q160 76 202 94", "none", 'stroke="#d9b24a" stroke-width="7" ' + LINE) + p("M118 94 Q160 76 202 94", "none", 'stroke="#ffdc83" stroke-width="3.5" ' + LINE) +
      c(132, 87, 3, "#70ebea") + c(188, 87, 3, "#70ebea") + p("M142 80 Q160 94 178 80 L182 88 Q160 100 138 88 Z", "#ffdc83") + p("M152 89 H168", "none", 'stroke="#b58a2e" stroke-width="2" ' + LINE) +
      p("M110 50 A50 13 0 0 1 210 50", "none", 'stroke="#ffdc83" stroke-width="5" transform="rotate(-16 160 50)" ' + LINE) +
      c(160, 50, 29, "#c9a8ff") + p("M135.8 36 Q160 42 184.2 36 L188 45 Q160 52 132 45 Z", "#8a6cf0", 'opacity=".85"') + p("M132 56 Q160 63 188 56 L184.2 65 Q160 71 135.8 65 Z", "#ff8ecb", 'opacity=".75"') +
      p("M140 34 Q148 26 160 25", "none", 'stroke="#fff" stroke-width="4" opacity=".75" ' + LINE) +
      p("M110 50 A50 13 0 0 0 210 50", "none", 'stroke="#ffdc83" stroke-width="5" transform="rotate(-16 160 50)" ' + LINE) +
      p("M116 50 A44 10 0 0 0 204 50", "none", 'stroke="#70ebea" stroke-width="1.8" transform="rotate(-16 160 50)" ' + LINE) +
      c(232, 24, 8, "#fff1cd") + c(230, 22, 2, "#e6cf9e", "") + c(235, 27, 1.4, "#e6cf9e", "") +
      p(star(100, 26, 7), "#fff8dc", 'class="lp-twinkle"') + p(star(214, 56, 5), "#fff8dc", 'class="lp-twinkle" style="animation-delay:.5s"') + p(star(118, 12, 4), "#70ebea", 'class="lp-twinkle" style="animation-delay:1s"') },

    // Star Capsule neck
    // Eleven chunky candy beads strung along her neck, each with a shiny dab.
    "acc-candy-beads": { layer: "front", svg: ["#ff8ecb", "#ffdc83", "#70ebea", "#c9a8ff", "#a9ee9f"].concat(["#ff8ecb", "#ffdc83", "#70ebea", "#c9a8ff", "#a9ee9f", "#ff8ecb"]).map(function (colour, i) {
      const t = i / 10, x = Math.round(((1 - t) * (1 - t) * 116 + 2 * (1 - t) * t * 160 + t * t * 204) * 10) / 10, y = Math.round(((1 - t) * (1 - t) * 196 + 2 * (1 - t) * t * 238 + t * t * 196) * 10) / 10; // A point along the dip of the string.
      return c(x, y, 7.5, colour) + c(x - 2, y - 2.5, 1.8, "#fff", 'opacity=".9"');
    }).join("") },
    // A triangle of spotty cloth tied at her neck.
    "acc-bandana": { layer: "front", svg: p("M116 196 Q160 214 204 196 L160 250 Z", "#4aa3e8") +
      [[140, 212, 3], [160, 216, 3.5], [180, 212, 3], [150, 229, 3], [170, 229, 3], [160, 241, 2.5]].map(function (d) { return c(d[0], d[1], d[2], "#fff8dc", ""); }).join("") +
      p("M116 196 Q160 214 204 196", "none", 'stroke="#2c74b5" stroke-width="5" ' + LINE) +
      p("M204 196 L216 188 L213 201 Z M204 196 L218 204 L206 208 Z", "#4aa3e8") + c(204, 197, 4.5, "#2c74b5") },
    // A wide navy collar with two white piping stripes and a red neckerchief knotted at the V.
    "acc-sailor-collar": { layer: "front", svg: p("M110 192 Q134 212 160 232 Q186 212 210 192 L216 214 L160 250 L104 214 Z", "#2e4a96") +
      p("M110 208 L160 240 L210 208", "none", 'stroke="#fff" stroke-width="3" ' + LINE) + p("M109 201 L160 234 L211 201", "none", 'stroke="#fff" stroke-width="2" opacity=".85" ' + LINE) +
      p("M152 232 L140 252 L153 247 Z M168 232 L180 252 L167 247 Z", "#e8556a") + p("M150 222 h20 l-3 14 h-14 Z", "#e8556a") + p("M155 226 v7 M165 226 v7", "none", 'stroke="#b83a4e" stroke-width="1.6" ' + LINE) },
    // A pleated lace ruff: a wide band with a scalloped outer edge, fold lines, a layer of pink frill and a tiny bow in the middle.
    "acc-lace-ruff": { layer: "front", svg: (function () {
      function at(t, a, b, d) { return [Math.round(((1 - t) * (1 - t) * a + 2 * (1 - t) * t * 160 + t * t * b) * 10) / 10, Math.round(((1 - t) * (1 - t) * 196 + 2 * (1 - t) * t * d + t * t * 196) * 10) / 10]; } // A point along a dipping curve.
      function frill(a, b, d, n, r, fill) { // A band from under her chin out to a scalloped edge of n bumps.
        let path = "M114 194 Q160 218 206 194 L" + at(1, a, b, d).join(" ");
        for (let i = n - 1; i >= 0; i--) path += " A" + r + " " + r + " 0 0 1 " + at(i / n, a, b, d).join(" ");
        return p(path + " Z", fill);
      }
      let folds = "";
      for (let i = 1; i < 12; i++) { folds += "M" + at(i / 12, 116, 204, 216).join(" ") + " L" + at(i / 12, 100, 220, 258).join(" ") + " "; } // One pleat line per scallop.
      return frill(100, 220, 258, 12, 11, "#fff8dc") + p(folds, "none", 'stroke="#d9b98a" stroke-width="1.6" ' + LINE) + frill(110, 210, 236, 10, 8, "#ffc6e4") + bow(160, 210, 10, "#ff8ecb");
    })() },
    // A beaten-gold torque set with five little moons running through their phases, full at the centre, with a pearl dangling beneath.
    "acc-moon-phase-torque": { layer: "front", svg: (function () {
      function at(t) { return [Math.round(((1 - t) * (1 - t) * 112 + 2 * (1 - t) * t * 160 + t * t * 208) * 10) / 10, Math.round(((1 - t) * (1 - t) * 198 + 2 * (1 - t) * t * 236 + t * t * 198) * 10) / 10]; } // A point along the collar.
      const lit = [.2, .72, 1, .72, .2], r = 8; // How much of each moon is lit: crescent, gibbous, full and back again.
      let out = p("M110 197 Q160 238 210 197", "none", 'stroke="#b58a2e" stroke-width="28" ' + LINE) + p("M110 197 Q160 238 210 197", "none", 'stroke="#ffdc83" stroke-width="24" ' + LINE) +
        p("M114 200 Q160 237 206 200", "none", 'stroke="#241a58" stroke-width="19" ' + LINE);
      lit.forEach(function (l, i) {
        const m = at(.1 + i * .2), left = i < 2, crescent = l < .5, rx = Math.round(r * Math.abs(1 - 2 * l) * 10) / 10; // rx is how far the shadow's edge bows.
        out += c(m[0], m[1], r, "#3c3580", 'stroke="#c9a8ff" stroke-width="1"');
        out += l === 1 ? c(m[0], m[1], r, "#fff1cd", "") : p("M" + m[0] + " " + (m[1] - r) + " A" + r + " " + r + " 0 0 " + (left ? 0 : 1) + " " + m[0] + " " + (m[1] + r) + " A" + rx + " " + r + " 0 0 " + (crescent === left ? 1 : 0) + " " + m[0] + " " + (m[1] - r) + "Z", "#fff1cd", "");
      });
      return out + p("M160 229 V236", "none", 'stroke="#d9b24a" stroke-width="3" ' + LINE) + c(160, 241, 7, "#fff8dc") + c(157.5, 238.5, 2, "#fff", "") +
        p(star(132, 240, 5), "#ffdc83", 'class="lp-twinkle"') + p(star(188, 240, 5), "#ffdc83", 'class="lp-twinkle" style="animation-delay:.7s"');
    })() },
    // A midnight capelet sewn with a gold-linked constellation, a scalloped gold hem and a great teardrop jewel that shines.
    "acc-constellation-mantle": { layer: "front", svg: p("M108 192 Q160 222 212 192 L224 232 Q208 248 192 238 Q176 254 160 243 Q144 254 128 238 Q112 248 96 232 Z", "#241a58") +
      p("M97 232 Q112 247 128 237 Q144 253 160 242 Q176 253 192 237 Q208 247 223 232", "none", 'stroke="#ffdc83" stroke-width="4" ' + LINE) +
      p("M108 192 Q160 222 212 192", "none", 'stroke="#ffdc83" stroke-width="5" ' + LINE) +
      p("M111 212 L126 226 L137 208 L146 234 M209 212 L194 226 L183 208 L174 234", "none", 'stroke="#c9a8ff" stroke-width="1.4" opacity=".9" ' + LINE) +
      [[111, 212, 4, 0], [126, 226, 5, .3], [137, 208, 4, .9], [146, 234, 3.5, 1.4], [209, 212, 4, .6], [194, 226, 5, 1.1], [183, 208, 4, .2], [174, 234, 3.5, 1.7]].map(function (s) {
        return p(star(s[0], s[1], s[2] + 2), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + s[3] + 's"');
      }).join("") +
      [128, 160, 192].map(function (x, i) { return c(x, [240, 245, 240][i], 3.5, "#fff1cd") + c(x, [248, 251, 248][i], 2, "#ff8ecb", ""); }).join("") +
      p(star(160, 225, 25), "#ffdc83") + p("M160 208 L172 224 L160 244 L148 224 Z", "#70ebea", 'stroke="#2a8c94" stroke-width="2" stroke-linejoin="round" class="lp-neon"') +
      p("M160 208 L166 224 L160 244 L154 224 Z", "#c6fbf4", "") + p("M148 224 L172 224", "none", 'stroke="#2a8c94" stroke-width="1" opacity=".7"') },

    // Star Capsule paws
    // Her paws sit at (123, 259) and (197, 259).
    // Lilac slippers each topped with a big fluffy pom-pom made of soft overlapping tufts.
    "acc-pom-slippers": { layer: "front", svg: [123, 197].map(function (x) {
      const tufts = [0, 45, 90, 135, 180, 225, 270, 315].map(function (d) { const a = d * Math.PI / 180; return [Math.round((x + 7.5 * Math.cos(a)) * 10) / 10, Math.round((249 + 7.5 * Math.sin(a)) * 10) / 10]; }); // Around the middle of the pom.
      return o(x, 261, 26, 14, "#c9a8ff") + o(x, 268, 21, 6, "#a98be6", 'opacity=".55"') + tufts.map(function (t) { return c(t[0], t[1], 6, "#d8b6d8", ""); }).join("") + tufts.map(function (t) { return c(t[0], t[1], 5, "#fff8ec", ""); }).join("") + c(x, 249, 7, "#fff8ec", "");
    }).join("") },
    // Sandals: a cork sole under each paw, a strap over it and a daisy on the strap.
    "acc-daisy-sandals": { layer: "front", svg: [123, 197].map(function (x) {
      return o(x, 271, 29, 7.5, "#d9a86a") + p("M" + (x - 22) + " 268 Q" + (x - 20) + " 250 " + x + " 250 Q" + (x + 20) + " 250 " + (x + 22) + " 268", "none", 'stroke="#70ebea" stroke-width="6" ' + LINE) +
        [0, 51, 103, 154, 206, 257, 309].map(function (d) { const a = d * Math.PI / 180; return c(Math.round((x + 7.5 * Math.cos(a)) * 10) / 10, Math.round((251 + 7.5 * Math.sin(a)) * 10) / 10, 3.8, "#fff"); }).join("") + c(x, 251, 4.6, "#ffdc83");
    }).join("") },
    // Green frog-faced booties with bulgy eyes on top and a wide smile.
    "acc-frog-booties": { layer: "front", svg: [123, 197].map(function (x) {
      return o(x, 262, 27, 15, "#86d67e") + [-1, 1].map(function (s) { return c(x + s * 13, 247, 9, "#86d67e") + c(x + s * 13, 246, 6, "#fff", "") + c(x + s * 12, 247, 3, "#2a3a2a", ""); }).join("") +
        p("M" + (x - 15) + " 264 Q" + x + " 275 " + (x + 15) + " 264", "none", 'stroke="#2f6e3e" stroke-width="2.5" ' + LINE) + o(x - 19, 262, 4, 2.4, "#ff8ecb", 'opacity=".75"') + o(x + 19, 262, 4, 2.4, "#ff8ecb", 'opacity=".75"');
    }).join("") },
    // Coral high-top skates flaring out at the toe, laced up the middle, on a metal plate with two chunky teal wheels each.
    "acc-roller-skates": { layer: "front", svg: [123, 197].map(function (x) {
      return p("M" + (x - 16) + " 238 h32 q3 12 11 22 q4 6 2 11 q-1 3 -6 3 h-46 q-5 0 -6 -3 q-2 -5 2 -11 q8 -10 11 -22 Z", "#ff8a8a") + o(x, 269, 20, 4, "#fff1cd", "") + p("M" + (x - 17) + " 240 h34", "none", 'stroke="#fff1cd" stroke-width="7" ' + LINE) +
        p("M" + (x - 6) + " 247 L" + (x + 6) + " 252 M" + (x + 6) + " 247 L" + (x - 6) + " 252 M" + (x - 8) + " 255 L" + (x + 8) + " 261 M" + (x + 8) + " 255 L" + (x - 8) + " 261", "none", 'stroke="#fff" stroke-width="2.4" ' + LINE) +
        p("M" + (x - 24) + " 276 h48", "none", 'stroke="#8d97a8" stroke-width="5" ' + LINE) + [-14, 14].map(function (d) { return c(x + d, 284, 8, "#70ebea") + c(x + d, 284, 3, "#fff8dc"); }).join("");
    }).join("") },
    // Satin ballet shoes with ribbons criss-crossing up the ankle and a tiny bow at the top.
    "acc-ballet-ribbons": { layer: "front", svg: [123, 197].map(function (x) {
      return o(x, 261, 27, 14.5, "#ffb3da") + p("M" + (x - 15) + " 255 Q" + x + " 262 " + (x + 15) + " 255", "none", 'stroke="#e87fb4" stroke-width="2.5" ' + LINE) + p("M" + (x - 18) + " 266 Q" + (x - 6) + " 272 " + (x + 8) + " 271", "none", 'stroke="#fff" stroke-width="2.5" opacity=".7" ' + LINE) +
        p("M" + (x - 19) + " 254 L" + (x + 15) + " 240 M" + (x + 19) + " 254 L" + (x - 15) + " 240 M" + (x - 15) + " 248 L" + (x + 12) + " 233 M" + (x + 15) + " 248 L" + (x - 12) + " 233", "none", 'stroke="#ff7fb8" stroke-width="3.5" ' + LINE) + bow(x, 233, 9, "#ff7fb8");
    }).join("") },
    // Paws lost in fluffy clouds: a small rainbow rising from each, drips of rain below and a twinkle on top.
    "acc-cloud-hoppers": { layer: "front", svg: [0, 1].map(function (m) {
      const x = 123, puffs = [[-20, 264, 11], [-7, 257, 13], [9, 256, 14], [22, 263, 11], [-10, 270, 12], [10, 271, 12]];
      return "<g" + (m ? ' transform="matrix(-1 0 0 1 320 0)"' : "") + ">" + [[20, "#ff8ecb"], [16, "#ffdc83"], [12, "#70ebea"]].map(function (a) {
        return p("M" + (x - 18 - a[0]) + " 258 A" + a[0] + " " + a[0] + " 0 0 1 " + (x - 18 + a[0]) + " 258", "none", 'stroke="' + a[1] + '" stroke-width="3.5" stroke-linecap="butt" fill="none"');
      }).join("") + puffs.map(function (b) { return c(x + b[0], b[1], b[2] + 1.5, "#b9c7e8", ""); }).join("") + puffs.map(function (b) { return c(x + b[0], b[1], b[2], "#fff", ""); }).join("") +
        p("M" + (x - 22) + " 270 Q" + x + " 282 " + (x + 26) + " 270", "none", 'stroke="#c9d6f2" stroke-width="3" ' + LINE) +
        [[-14, 287], [2, 291], [16, 286]].map(function (d) { const dx = x + d[0], dy = d[1]; return p("M" + dx + " " + (dy - 6) + " Q" + (dx + 5) + " " + (dy + 1) + " " + dx + " " + (dy + 4) + " Q" + (dx - 5) + " " + (dy + 1) + " " + dx + " " + (dy - 6) + "Z", "#70c8f0", ""); }).join("") +
        p(star(x + 14, 246, 6.5), "#ffdc83", 'class="lp-twinkle" style="animation-delay:' + (m * .5) + 's"') + p(star(x - 2, 244, 3.5), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (.9 + m * .4) + 's"') + "</g>";
    }).join("") },
    // The legendary pair: midnight boots with a galaxy swirling in the leather, gold cuffs and soles, a glowing star on each toe, feather wings at the ankle, a tilted orbit ring and stardust rising from a shining arc beneath.
    "acc-starstrider-boots": { layer: "front", svg: [0, 1].map(function (m) {
      const x = 123, tw = function (d) { return 'class="lp-twinkle" style="animation-delay:' + (d + m * .35) + 's"'; };
      const wing = function (tipX, tipY, footY, fill) { return p("M" + (x - 25) + " " + footY + " C" + (x - 40) + " " + (footY - 8) + " " + tipX + " " + (tipY + 14) + " " + tipX + " " + tipY + " C" + (tipX + 16) + " " + (tipY + 2) + " " + (x - 24) + " " + (footY - 18) + " " + (x - 22) + " " + (footY - 4) + " Z", fill); }; // One ankle feather, from the boot out to its tip.
      return "<g" + (m ? ' transform="matrix(-1 0 0 1 320 0)"' : "") + ">" + wing(x - 70, 246, 262, "#ffdc83") + wing(x - 66, 230, 256, "#ffe9ad") + wing(x - 56, 216, 250, "#fff8ec") + wing(x - 42, 208, 245, "#fff8ec") +
        p("M" + (x - 26) + " 268 q0 -26 14 -29 h24 q14 3 14 29 q0 10 -12 10 h-28 q-12 0 -12 -10 Z", "#241a78", 'stroke="#ffdc83" stroke-width="2.5" stroke-linejoin="round"') +
        o(x - 7, 256, 13, 7, "#c94fd8", 'opacity=".55" transform="rotate(-18 ' + (x - 7) + ' 256)"') + o(x + 9, 264, 12, 6, "#2fb6d8", 'opacity=".55" transform="rotate(-12 ' + (x + 9) + ' 264)"') + o(x - 4, 270, 11, 4, "#ff8ecb", 'opacity=".4"') +
        p("M" + (x - 16) + " 262 Q" + (x - 2) + " 248 " + (x + 16) + " 254 Q" + (x + 4) + " 262 " + (x - 8) + " 262", "none", 'stroke="#d9c8ff" stroke-width="1.5" opacity=".8" ' + LINE) +
        p("M" + (x - 17) + " 240 h34", "none", 'stroke="#b58a2e" stroke-width="10" ' + LINE) + p("M" + (x - 17) + " 240 h34", "none", 'stroke="#ffdc83" stroke-width="6.5" ' + LINE) + c(x, 240, 4.5, "#ff8ecb") +
        p("M" + (x - 24) + " 278 h48", "none", 'stroke="#ffdc83" stroke-width="4" ' + LINE) +
        p(star(x, 264, 14), "#fff8dc", 'stroke="#b58a2e" stroke-width="1.2" stroke-linejoin="round" class="lp-neon"') + c(x, 264, 3.5, "#70ebea", "") +
        [[-16, 250, 2.6], [15, 250, 2.2], [-20, 268, 2], [19, 271, 2.4]].map(function (s, i) { return p(star(x + s[0], s[1], s[2] + 1.2), "#fff8dc", tw(i * .4)); }).join("") +
        o(x, 266, 36, 10, "none", 'stroke="#ffdc83" stroke-width="2" opacity=".85" transform="rotate(-14 ' + x + ' 266)"') + c(x - 34, 275, 4.5, "#70ebea", tw(.5)) + c(x + 31, 257, 2.8, "#ff8ecb", tw(1)) +
        p("M" + (x - 32) + " 284 Q" + x + " 296 " + (x + 32) + " 284", "none", 'stroke="#70ebea" stroke-width="2.5" opacity=".85" class="lp-neon" ' + LINE) +
        p(star(x - 15, 294, 4), "#ffdc83", tw(.2)) + p(star(x, 295, 3), "#70ebea", tw(1.1)) + p(star(x + 16, 294, 3.5), "#fff8dc", tw(.7)) + "</g>";
    }).join("") },

    // Star Capsule tail
    // Her tail runs out from her side, around the curl at about (271, 210), to its tip at (239, 218).
    // Two shiny cherries dangling from the outside of her curl.
    "acc-cherry-charm": { layer: "front", svg: p("M274 206 Q286 212 297 228 M274 206 Q276 220 278 234", "none", 'stroke="#5f9e4f" stroke-width="3" ' + LINE) +
      p("M274 206 C282 192 298 192 304 200 C296 210 282 212 274 206Z", "#8ddc8f") + c(298, 236, 10, "#e8405f") + c(278, 243, 10, "#f0506e") + c(294.5, 232.5, 2.4, "#fff", 'opacity=".85"') + c(274.5, 239.5, 2.4, "#fff", 'opacity=".85"') },
    // A golden curtain tassel: cord looped on her tail, a round knot, a waisted cap and a skirt of threads.
    "acc-tail-tassel": { layer: "front", svg: p("M268 208 Q282 212 284 226", "none", 'stroke="#d9b24a" stroke-width="4" ' + LINE) + c(284, 228, 6.5, "#ffdc83") + p("M278 233 h12 l4 10 h-20 Z", "#d9a83a") +
      p("M274 243 L271 254 H297 L294 243 Z", "#ffdc83") + p("M277 245 L275.5 253 M282 245 L281 253 M287 245 L288 253 M292 245 L293.5 253", "none", 'stroke="#b58a2e" stroke-width="1.8" ' + LINE) },
    // A knitted sleeve pulled along her tail: two colours of stripe that follow the curve, with cream ribbed cuffs.
    "acc-tail-cosy": { layer: "front", svg: (function () {
      const LOOP = "M237 233.7 C280.1 239.2 282.4 188.6 256 193";
      return p(LOOP, "none", 'stroke="#70ebea" stroke-width="20" fill="none"') + p(LOOP, "none", 'stroke="#ff8ecb" stroke-width="20" stroke-dasharray="7 11" fill="none"') +
        p("M240 223.5 L237.5 243.5 M257.5 183 L259.5 203", "none", 'stroke="#fff1cd" stroke-width="7" fill="none"'); // Cuffs set square across each end of the sleeve.
    })() },
    // A paper lantern hung on a thread above her curl, with a warm glow inside and a little tassel underneath.
    "acc-tail-lantern": { layer: "front", svg: p("M262 190 Q270 166 292 168", "none", 'stroke="#d9b24a" stroke-width="2.5" ' + LINE) + p("M285 168 h14 l2 5 h-18 Z", "#d9a83a") +
      o(292, 192, 18, 20, "#e8556a") + o(292, 192, 10, 13, "#ffdc83", 'class="lp-twinkle" opacity=".9"') + o(292, 192, 18, 20, "none", 'stroke="#b83a4e" stroke-width="1.8"') + p("M292 172 V212 M281 176 Q276 192 281 208 M303 176 Q308 192 303 208", "none", 'stroke="#b83a4e" stroke-width="1.8" ' + LINE) +
      p("M284 211 h16 l-2 5 h-12 Z", "#d9a83a") + p("M292 216 V226", "none", 'stroke="#d9b24a" stroke-width="2.5" ' + LINE) + p("M288 226 h8 l2 15 h-12 Z", "#ffdc83") },
    // Three long wavy ribbons streaming from a knot on her curl, each cut to a swallow tail.
    "acc-tail-streamers": { layer: "front", svg: [["M270 204 C284 192 278 176 292 168 C300 163 300 156 306 152", "#ff8ecb", "M301 150 L312 150 L306 158 Z"], ["M272 208 C288 206 292 192 304 192 C308 192 310 190 313 186", "#70ebea", "M308 182 L315 188 L306 192 Z"], ["M272 214 C286 222 288 236 300 238 C306 240 308 244 310 250", "#ffdc83", "M304 250 L314 246 L312 256 Z"]].map(function (r) {
      return p(r[2], r[1], "") + p(r[0], "none", 'stroke="' + r[1] + '" stroke-width="7" ' + LINE) + p(r[0], "none", 'stroke="#fff" stroke-width="1.5" opacity=".55" ' + LINE);
    }).join("") + bow(270, 208, 9, "#c9a8ff") },
    // A dragon's tail: teal scales laid along the curve, a row of spines around the outside and a spade-shaped tip.
    "acc-dragon-tail": { layer: "front", svg: (function () {
      const r = function (n) { return Math.round(n * 10) / 10; }, A = [[237, 233.7], [280.1, 239.2], [282.4, 188.6], [256, 193]];
      function at(t) { const u = 1 - t, q = [0, 1].map(function (k) { return u * u * u * A[0][k] + 3 * u * u * t * A[1][k] + 3 * u * t * t * A[2][k] + t * t * t * A[3][k]; }), d = [0, 1].map(function (k) { return 3 * u * u * (A[1][k] - A[0][k]) + 6 * u * t * (A[2][k] - A[1][k]) + 3 * t * t * (A[3][k] - A[2][k]); }), l = Math.hypot(d[0], d[1]); return { x: q[0], y: q[1], tx: d[0] / l, ty: d[1] / l }; } // A point on the loop and which way it's heading.
      const LOOP = "M237 233.7 C280.1 239.2 282.4 188.6 256 193 C238 196 260 215 239 218";
      let spines = "", scales = "";
      [.06, .22, .38, .54, .7, .86].forEach(function (t) { // Spikes standing on the outside edge.
        const a = at(t), nx = -a.ty, ny = a.tx, bx = a.x + nx * 8, by = a.y + ny * 8;
        spines += "M" + r(bx - a.tx * 6) + " " + r(by - a.ty * 6) + " L" + r(bx + nx * 13) + " " + r(by + ny * 13) + " L" + r(bx + a.tx * 6) + " " + r(by + a.ty * 6) + "Z ";
      });
      for (let i = 0; i <= 11; i++) { // Rows of little U-shaped scales along the loop.
        const a = at(i / 11), nx = -a.ty, ny = a.tx;
        [-4.2, 3.6].forEach(function (off) {
          const px = a.x + nx * off, py = a.y + ny * off;
          scales += "M" + r(px - a.ty * -3) + " " + r(py + a.tx * -3) + " Q" + r(px + a.tx * 6) + " " + r(py + a.ty * 6) + " " + r(px - a.ty * 3) + " " + r(py + a.tx * 3) + " ";
        });
      }
      return p(spines, "#2f8f83") + p(LOOP, "none", 'stroke="#28706a" stroke-width="20" ' + LINE) + p(LOOP, "none", 'stroke="#49c2a8" stroke-width="16" ' + LINE) + p(scales, "none", 'stroke="#1f6e66" stroke-width="1.5" ' + LINE) +
        p("M239 218 C232 208 222 208 216 214 C220 218 222 222 216 226 C224 232 234 230 239 218Z", "#2f8f83") + p("M238 218 Q228 216 221 219", "none", 'stroke="#a9f0dc" stroke-width="2" ' + LINE) +
        p(star(290, 176, 5), "#a9f0dc", 'class="lp-twinkle"') + p(star(304, 216, 4), "#fff8dc", 'class="lp-twinkle" style="animation-delay:.7s"');
    })() },
    // A wind-up tail in brass plates, with meshing gears on the outside, tiny rivets and one twinkling jewel.
    "acc-clockwork-tail": { layer: "front", svg: (function () {
      const r = function (n) { return Math.round(n * 10) / 10; }, LOOP = "M237 233.7 C280.1 239.2 282.4 188.6 256 193 C238 196 260 215 239 218";
      function gear(x, y, size, teeth, turn, fill) { // A toothed wheel with a hub and spokes.
        let d = "";
        for (let i = 0; i < teeth * 4; i++) { const a = (i * Math.PI / (teeth * 2)) + turn, out = i % 4 < 2 ? size * 1.28 : size, ang = a - Math.PI / (teeth * 4); d += (i ? " L" : "M") + r(x + out * Math.cos(ang)) + " " + r(y + out * Math.sin(ang)); }
        return p(d + "Z", fill) + c(x, y, r(size * .62), "#7a5a24", "") + c(x, y, r(size * .4), fill, "") + c(x, y, r(size * .16), "#3a2a12", "");
      }
      return p(LOOP, "none", 'stroke="#7a5a24" stroke-width="20" ' + LINE) + p(LOOP, "none", 'stroke="#d9b24a" stroke-width="16" ' + LINE) + p(LOOP, "none", 'stroke="#8a6a2a" stroke-width="16" stroke-dasharray="2 11" fill="none"') +
        gear(292, 222, 16, 10, 0, "#ffdc83") + gear(298, 192, 11, 8, .35, "#d9b24a") + gear(276, 170, 8, 7, .1, "#ffe9ad") +
        [[252, 238], [266, 231], [275, 216], [276, 202]].map(function (v) { return c(v[0], v[1], 2, "#fff1cd", ""); }).join("") +
        c(250, 205, 5.5, "#70ebea", 'stroke="#2a8c94" stroke-width="1.5" class="lp-twinkle"') + p(star(306, 244, 5), "#ffdc83", 'class="lp-twinkle" style="animation-delay:.6s"');
    })() },

    // Star Capsule wings
    // Each drawing is made once for her left side and mirrored about x = 160 for the right.
    // Painted-on spots in cream and sky blue.
    "acc-wing-dots": { layer: "front", svg: [0, 1].map(function (m) {
      return "<g" + (m ? ' transform="matrix(-1 0 0 1 320 0)"' : "") + ">" + [[62, 128, 9], [84, 140, 6], [47, 140, 5.5], [73, 158, 7.5], [55, 163, 4.5], [78, 119, 4.5], [80, 198, 7.5], [64, 205, 4.5], [90, 211, 4]].map(function (d, i) {
        return c(d[0], d[1], d[2], i % 2 ? "#70ebea" : "#fff1cd", 'opacity=".95"');
      }).join("") + "</g>";
    }).join("") },
    // Little gold bells tied to the wing edges with short red ribbons.
    "acc-wing-bells": { layer: "front", svg: [0, 1].map(function (m) {
      return "<g" + (m ? ' transform="matrix(-1 0 0 1 320 0)"' : "") + ">" + [[45, 158, 10], [58, 104, 8], [66, 217, 8]].map(function (b) {
        const x = b[0], y = b[1], s = b[2];
        return p("M" + x + " " + (y - 3) + " V" + (y + 4), "none", 'stroke="#e8556a" stroke-width="3" ' + LINE) +
          p("M" + (x - s) + " " + (y + 2 * s + 2) + " Q" + (x - s) + " " + (y + 4) + " " + x + " " + (y + 4) + " Q" + (x + s) + " " + (y + 4) + " " + (x + s) + " " + (y + 2 * s + 2) + " L" + (x + s + 3) + " " + (y + 2 * s + 6) + " H" + (x - s - 3) + " Z", "#ffdc83") +
          c(x, y + 2 * s + 7, 3, "#b58a2e", "");
      }).join("") + "</g>";
    }).join("") },
    // Big felt hearts sewn on with white running stitch.
    "acc-wing-hearts": { layer: "front", svg: (function () {
      function heart(x, y, s) { const r = function (n) { return Math.round(n * 10) / 10; }; return "M" + x + " " + r(y + s) + " C" + r(x - 1.7 * s) + " " + r(y + .1 * s) + " " + r(x - .9 * s) + " " + r(y - s) + " " + x + " " + r(y - .3 * s) + " C" + r(x + .9 * s) + " " + r(y - s) + " " + r(x + 1.7 * s) + " " + r(y + .1 * s) + " " + x + " " + r(y + s) + "Z"; }
      return [0, 1].map(function (m) {
        return "<g" + (m ? ' transform="matrix(-1 0 0 1 320 0)"' : "") + ">" + [[64, 138, 22, "#e8556a"], [77, 201, 11, "#ff8ecb"]].map(function (h) {
          return p(heart(h[0], h[1], h[2]), h[3]) + p(heart(h[0], h[1], Math.round(h[2] * .78)), "none", 'stroke="#fff" stroke-width="1.6" stroke-dasharray="3 3" ' + LINE);
        }).join("") + "</g>";
      }).join("");
    })() },
    // Two big green leaves laid over her wings, with a pale midrib, side veins and a touch of autumn at the tip.
    "acc-wing-leaves": { layer: "front", svg: [0, 1].map(function (m) {
      return "<g" + (m ? ' transform="matrix(-1 0 0 1 320 0)"' : "") + ">" + p("M97 176 C58 182 26 150 30 98 C70 92 96 126 97 176Z", "#7ccb7e") + p("M30 98 C42 96 54 98 62 104 C48 110 38 122 34 132 C30 122 29 110 30 98Z", "#e8b04a", 'opacity=".85"') +
        p("M97 176 Q66 142 34 104 M80 156 Q58 158 46 150 M68 142 Q50 138 40 128 M80 156 Q84 136 80 122 M68 142 Q72 124 66 108", "none", 'stroke="#3f8a55" stroke-width="2.2" ' + LINE) +
        p("M101 190 C80 180 56 188 50 216 C76 226 98 214 101 190Z", "#9edb8b") + p("M101 190 Q76 202 50 216 M84 197 Q78 208 72 212 M84 197 Q74 192 66 192", "none", 'stroke="#4f9e63" stroke-width="2" ' + LINE) + "</g>";
    }).join("") },
    // Painted butterfly wings: amber panes, a dark border dotted with white, veins, and an eyespot on the lower wing.
    "acc-wing-butterfly": { layer: "front", svg: [0, 1].map(function (m) {
      return "<g" + (m ? ' transform="matrix(-1 0 0 1 320 0)"' : "") + ">" + p("M98 177 C10 176 28 76 79 108 C90 115 92 132 92 153 Q92 170 98 177Z", "#ffa63d", 'stroke="#3a1c3a" stroke-width="5" stroke-linejoin="round"') +
        p("M98 176 Q60 150 46 118 M98 176 Q70 128 66 108 M98 176 Q50 168 38 142 M92 150 Q84 124 78 112", "none", 'stroke="#3a1c3a" stroke-width="2.4" ' + LINE) +
        [[40, 124, 2.8], [37, 136, 3], [42, 150, 2.8], [52, 164, 2.8], [50, 112, 2.6], [64, 106, 2.4]].map(function (d) { return c(d[0], d[1], d[2], "#fff8dc", ""); }).join("") +
        p("M103 183 C38 174 48 238 93 215 L100 200 Z", "#ffc35e", 'stroke="#3a1c3a" stroke-width="4.5" stroke-linejoin="round"') +
        c(76, 202, 10.5, "#3a1c3a", "") + c(76, 202, 7, "#ffe07a", "") + c(76, 202, 3.8, "#3b6fd8", "") + c(74.5, 200.5, 1.2, "#fff", "") + "</g>";
    }).join("") },
    // A church-window wing: wedges of coloured glass fanned from the shoulder, in two rings, with dark leading and a jewel at the hinge.
    "acc-wing-stained-glass": { layer: "front", svg: (function () {
      const r = function (n) { return Math.round(n * 10) / 10; };
      function bez(a, b, e, f, t) { const u = 1 - t; return [0, 1].map(function (k) { return r(u * u * u * a[k] + 3 * u * u * t * b[k] + 3 * u * t * t * e[k] + t * t * t * f[k]); }); } // A point on a wing's outer curve.
      const colours = ["#ff8ecb", "#70ebea", "#ffdc83", "#c9a8ff", "#8ddc8f", "#ff9d6b"], LEAD = 'stroke="#2b1d50" stroke-width="2" stroke-linejoin="round" fill-opacity=".9"';
      function fan(base, rim, shift) { // Two rings of glass panes between the hinge and the points around the rim.
        let out = "";
        for (let i = 0; i < rim.length - 1; i++) {
          const mid = function (q) { return [r(base[0] + (q[0] - base[0]) * .55), r(base[1] + (q[1] - base[1]) * .55)]; }, m1 = mid(rim[i]), m2 = mid(rim[i + 1]);
          out += p("M" + base.join(" ") + " L" + m1.join(" ") + " L" + m2.join(" ") + "Z", colours[(i + shift) % 6], LEAD);
          out += p("M" + m1.join(" ") + " L" + rim[i].join(" ") + " L" + rim[i + 1].join(" ") + " L" + m2.join(" ") + "Z", colours[(i + shift + 2) % 6], LEAD);
        }
        return out;
      }
      const upperOut = [[98, 177], [10, 176], [28, 76], [79, 108]], upperIn = [[79, 108], [90, 115], [92, 132], [92, 153]], lower = [[103, 183], [38, 174], [48, 238], [93, 215]];
      const top = [0, .18, .33, .47, .6, .72, .85, 1].map(function (t) { return bez(upperOut[0], upperOut[1], upperOut[2], upperOut[3], t); }).concat([.5, 1].map(function (t) { return bez(upperIn[0], upperIn[1], upperIn[2], upperIn[3], t); }));
      const low = [0, .22, .45, .68, .85, 1].map(function (t) { return bez(lower[0], lower[1], lower[2], lower[3], t); });
      return [0, 1].map(function (m) {
        return "<g" + (m ? ' transform="matrix(-1 0 0 1 320 0)"' : "") + ">" + p("M98 177 C10 176 28 76 79 108 C90 115 92 132 92 153 Q92 170 98 177Z M103 183 C38 174 48 238 93 215 L100 200 Z", "#fff1cd", 'fill-opacity=".5" stroke="none"') +
          fan([96, 175], top, m) + fan([100, 192], low, m + 3) +
          p("M98 177 C10 176 28 76 79 108 C90 115 92 132 92 153 Q92 170 98 177Z M103 183 C38 174 48 238 93 215 L100 200 Z", "none", 'stroke="#2b1d50" stroke-width="3.5" stroke-linejoin="round"') +
          c(96, 176, 5.5, "#ffdc83") + c(100, 192, 4, "#ffdc83") +
          p(star(52, 132, 6), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (m * .6) + 's"') + p(star(66, 204, 4), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (.4 + m * .6) + 's"') + "</g>";
      }).join("");
    })() },
    // A proper swept wing of broad round feathers: long gold-dipped flight feathers fanning up and out, peach ones layered over their roots, and a blush-pink row below.
    "acc-wing-dawn-feathers": { layer: "front", svg: (function () {
      const r = function (n) { return Math.round(n * 10) / 10; };
      function feather(x, y, a, len, w, fill) { // One round-ended feather with its quill, drawn pointing up and then turned.
        return p("M0 0 C" + -w + " " + r(-len * .3) + " " + -w + " " + r(-len * .85) + " 0 " + -len + " C" + w + " " + r(-len * .85) + " " + w + " " + r(-len * .3) + " 0 0Z M0 -4 L0 " + (6 - len), fill, 'stroke="#8a6a55" stroke-opacity=".7" stroke-width="1.4" stroke-linejoin="round" transform="translate(' + x + " " + y + ") rotate(" + a + ')"');
      }
      function tip(x, y, a, len, w) { // The gold dip at a feather's end.
        const o = r(.6 * w), s = r(-.8 * len);
        return p("M" + -o + " " + s + " Q" + r(-.45 * w) + " " + r(-.97 * len) + " 0 " + -len + " Q" + r(.45 * w) + " " + r(-.97 * len) + " " + o + " " + s + " Q0 " + r(-.72 * len) + " " + -o + " " + s + "Z", "#ffdc83", 'stroke="#b58a2e" stroke-width="1" stroke-linejoin="round" transform="translate(' + x + " " + y + ") rotate(" + a + ')"');
      }
      const long = [[-86, 66], [-74, 76], [-62, 84], [-50, 86], [-38, 80], [-27, 70], [-20, 58]], low = [[-132, 40], [-116, 50], [-100, 48]], covert = [[-82, 42], [-66, 46], [-50, 46], [-35, 42], [-23, 36]];
      return [0, 1].map(function (m) {
        return "<g" + (m ? ' transform="matrix(-1 0 0 1 320 0)"' : "") + ">" + low.map(function (f) { return feather(98, 190, f[0], f[1], 13, "#ffd3e6"); }).join("") +
          long.map(function (f) { return feather(92, 172, f[0], f[1], 14, "#fff8ec") + tip(92, 172, f[0], f[1], 14); }).join("") +
          covert.map(function (f) { return feather(93, 173, f[0], f[1], 13, "#ffc48a"); }).join("") +
          p(star(22, 118, 6), "#fff8dc", 'class="lp-twinkle" style="animation-delay:' + (m * .5) + 's"') + p(star(50, 74, 4.5), "#ffdc83", 'class="lp-twinkle" style="animation-delay:' + (.8 + m * .5) + 's"') + "</g>";
      }).join("");
    })() },
  };

  function render(outfit, layer) { // The markup for everything she wears on one layer. `outfit` is {slot: accessory id}.
    return Object.keys(outfit).map(function (slot) {
      const id = outfit[slot], art = Object.prototype.hasOwnProperty.call(ART, id) ? ART[id] : null;
      return art && art.layer === layer ? '<g data-acc="' + slot + '">' + art.svg + "</g>" : ""; // One this file doesn't know simply isn't drawn.
    }).join("");
  }

  return { ART: ART, render: render };
});
