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
  };

  function render(outfit, layer) { // The markup for everything she wears on one layer. `outfit` is {slot: accessory id}.
    return Object.keys(outfit).map(function (slot) {
      const id = outfit[slot], art = Object.prototype.hasOwnProperty.call(ART, id) ? ART[id] : null;
      return art && art.layer === layer ? '<g data-acc="' + slot + '">' + art.svg + "</g>" : ""; // One this file doesn't know simply isn't drawn.
    }).join("");
  }

  return { ART: ART, render: render };
});
