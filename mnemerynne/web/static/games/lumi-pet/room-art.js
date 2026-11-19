/* Room mode's drawings (Doll's design, 2026-11-19): the things in Lumi's kitchen and den, and the paint pots in her
   bedroom. The rooms' walls, floors and windows are her own from house.js (render with `only`), so the whole flat is
   decorated the way she decorated her bedroom. Only these few things are new, drawn in house.js's style: flat shapes,
   soft outlines, and accents in her glow colour (var(--pet-glow)), so the flat always suits her.

   Plain SVG in the room's own 1000 x 500 coordinates, at the places room-ui.js gives each thing's press box.
   All authored here: nothing a person typed goes into it. */
(function (root, make) {
  const art = make();
  if (typeof module === "object" && module.exports) module.exports = art; else root.LumiPetRoomArt = art;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const GLOW = "var(--pet-glow)";                 // Her glow: handles, screens and lights follow it.
  const LINE = "#5a3a6e", INK = "#2a1838";        // Outlines and the darkest shade, as in house.js.
  // The layers of her bedroom that make the other rooms hers too (house.js render's `only`): no door, furniture or boxes.
  const SHELL = ["wallpaper", "floor", "view", "frame", "curtains", "ceiling"];

  // ---- Small drawing helpers (the same shapes house.js uses).
  function rect(x, y, w, h, fill, more) { return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function circle(x, y, r, fill, more) { return '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function oval(x, y, rx, ry, fill, more) { return '<ellipse cx="' + x + '" cy="' + y + '" rx="' + rx + '" ry="' + ry + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function path(d, fill, more) { return '<path d="' + d + '" fill="' + fill + '" ' + (more || "") + "/>"; }
  function group(inner, more) { return "<g " + (more || "") + ">" + inner + "</g>"; }
  function shadow(x, y, rx) { return oval(x, y, rx, 9, "#000", 'opacity=".22"'); } // The soft shadow under anything standing on the floor.
  const OUT = 'stroke="' + LINE + '" stroke-width="3"';                              // The outline every piece has.

  // ---- The kitchen.
  const FRIDGE_BACK = -55; // How far back (up the floor) the fridge stands: against the wall, with floor in front of it.
  function fridge() { // A tall rounded fridge with a freezer door, a glowing handle and a few magnets. Opens the Pantry.
    return group(shadow(380, 472, 62) +
      rect(328, 222, 104, 250, "#e9eef7", 'rx="16" ' + OUT) +                                   // the body
      path("M328 300 H432", "none", 'stroke="' + LINE + '" stroke-width="3"') +               // freezer above, fridge below
      rect(414, 244, 7, 38, GLOW, 'rx="3"') + rect(414, 318, 7, 70, GLOW, 'rx="3"') +         // handles in her glow
      circle(352, 330, 7, "#ff8ecb") + rect(366, 344, 16, 12, "#ffdc83", 'rx="2"') +         // magnets
      path("M348 360 l6 -10 l6 10 z", "#70ebea") +
      rect(344, 236, 40, 30, "#fff", 'rx="3" opacity=".9"') + path("M350 246 H378 M350 254 H372", "none", 'stroke="#c9a8ff" stroke-width="2"'), // a little shopping list
      'transform="translate(0 ' + FRIDGE_BACK + ')"');
  }

  function bowl() { // Her food bowl on a mat, always a little full: the free snacks. Opens the Pantry too.
    return rect(458, 452, 84, 14, GLOW, 'rx="7" opacity=".55"') +                            // the mat
      path("M466 432 Q500 470 534 432 Z", "#ffffff", OUT) +                                     // the bowl
      oval(500, 433, 34, 7, "#f5c26b") + circle(490, 430, 4, "#d98f3a") + circle(506, 429, 4, "#d98f3a") + circle(498, 426, 3.5, "#e8a650") + // kibble
      path("M484 446 L488 440 L492 446 L488 452 Z", GLOW);                                      // a star on the side
  }

  function potions() { // A wall shelf of potions in a small cabinet, each bottle a different colour. Opens just the potion row.
    const bottle = function (x, colour, tall) { // One bottle: a cork, a neck, and a round belly that glows faintly.
      const top = 300 - tall;
      return rect(x - 5, top - 8, 10, 8, "#c79a6b", 'rx="2"') + rect(x - 4, top, 8, 14, "#dfe9ff", 'opacity=".8"') +
        circle(x, top + 28, 15, colour, OUT + ' opacity=".9"') + circle(x - 5, top + 23, 4, "#fff", 'opacity=".7"');
    };
    return rect(568, 300, 104, 10, "#8a5a3b", 'rx="3" ' + OUT) +                               // the shelf
      path("M580 310 L590 330 M660 310 L650 330", "none", 'stroke="' + LINE + '" stroke-width="4"') + // its brackets
      bottle(592, "#ff6b8b", 40) + bottle(620, "#70ebea", 52) + bottle(648, "#8be9a8", 38) +
      rect(600, 188, 40, 40, INK, 'rx="8" opacity=".85"') +                                   // a little neon sign above it, clear of the bottles:
      group(path("M620 196 V220 M608 208 H632", "none", 'stroke="' + GLOW + '" stroke-width="6" stroke-linecap="round"'), 'class="lp-tw-glow"'); // a glowing cross, for mending
  }

  function counter() { // A low kitchen counter between the fridge and the shelf, so the kitchen reads as one at a glance.
    return rect(440, 360, 120, 18, "#b98aa9", 'rx="4" ' + OUT) + rect(446, 378, 108, 40, "#8c5f86", OUT) +
      circle(470, 352, 9, "#ff9d6b") + circle(486, 350, 7, "#8be9a8") + rect(512, 338, 26, 22, "#fff", 'rx="4" ' + OUT); // fruit and a teapot-ish mug
  }

  // ---- The bedroom.
  function paint() { // Two paint pots and a brush on the floor by the rug: decorate her room (the Home tab).
    return shadow(547, 470, 36) +
      rect(520, 430, 30, 38, "#dfe4f0", 'rx="4" ' + OUT) + oval(535, 430, 15, 5, "#ff8ecb") + path("M528 430 Q530 444 534 446 Q536 438 538 430 Z", "#ff8ecb") + // pink, dripping
      rect(552, 440, 26, 28, "#dfe4f0", 'rx="4" ' + OUT) + oval(565, 440, 13, 4.5, GLOW) +    // her glow colour
      path("M560 418 L590 392", "none", 'stroke="#c79a6b" stroke-width="6" stroke-linecap="round"') + path("M556 422 L566 412 L570 424 Z", GLOW, OUT); // the brush
  }

  // ---- The den.
  function wardrobe() { // A tall wardrobe, one door ajar with a dress on a hanger peeking out. Opens the Closet.
    return shadow(370, 473, 72) +
      rect(310, 212, 120, 260, "#a8789f", 'rx="10" ' + OUT) + rect(304, 204, 132, 16, "#8c5f86", 'rx="6" ' + OUT) + // body and its crown
      rect(320, 226, 48, 230, "#c39abc", 'rx="5" ' + OUT) +                                    // the closed door
      path("M372 226 L420 236 V448 L372 456 Z", "#c39abc", OUT) +                              // the door ajar
      path("M374 250 Q392 236 410 250", "none", 'stroke="#ddd" stroke-width="3"') +           // a hanger
      path("M378 252 L404 252 L414 330 L368 330 Z", "#ff8ecb", OUT) + circle(391, 290, 4, GLOW) + // the dress, with a sparkle
      circle(360, 340, 5, GLOW) + circle(380, 345, 5, GLOW);                                   // knobs
  }

  function terminal() { // A cyberpunk desk terminal: a glowing screen of scrolling lines, a keyboard, a mug. Opens Settings.
    return shadow(500, 472, 70) +
      rect(440, 380, 120, 12, "#3b2a4d", 'rx="3" ' + OUT) +                                    // the desk top
      rect(448, 392, 10, 80, "#3b2a4d", OUT) + rect(542, 392, 10, 80, "#3b2a4d", OUT) +       // its legs
      rect(456, 296, 88, 66, INK, 'rx="8" ' + OUT) +                                           // the monitor
      rect(464, 304, 72, 50, "#0b1030", 'rx="4"') +                                            // its screen
      group(path("M470 314 H500 M470 324 H520 M470 334 H492 M470 344 H512", "none", 'stroke="' + GLOW + '" stroke-width="3" stroke-linecap="round"') +
        rect(516, 340, 8, 6, "#70ebea"), 'class="lp-tw-glow"') +                               // lines of settings, and a blinking cursor
      rect(494, 362, 12, 18, INK) + rect(468, 370, 64, 8, "#5d4a72", 'rx="2"') +              // the stand and the keyboard
      rect(536, 362, 14, 18, "#ffdc83", 'rx="3" ' + OUT);                                      // a mug of something warm
  }

  function capsules() { // A capsule machine: a glass dome of coloured capsules on a red stand with a big handle. Opens the Shop.
    let balls = "";
    [[612, 300, "#ff8ecb"], [636, 296, "#70ebea"], [624, 318, "#ffdc83"], [648, 316, "#8be9a8"], [604, 322, "#c9a8ff"], [660, 302, "#ff9d6b"]]
      .forEach(function (b) { balls += circle(b[0], b[1], 10, b[2], 'stroke="' + LINE + '" stroke-width="2"') + path("M" + (b[0] - 10) + " " + b[1] + " H" + (b[0] + 10), "none", 'stroke="' + LINE + '" stroke-width="1.5"'); });
    return shadow(630, 473, 54) +
      circle(632, 305, 46, "#dff6ff", 'opacity=".55" ' + OUT) + balls + circle(614, 286, 9, "#fff", 'opacity=".7"') + // the dome, its capsules, a shine
      rect(590, 346, 84, 126, "#e0455f", 'rx="10" ' + OUT) +                                   // the stand
      circle(632, 384, 16, "#f6f6f6", OUT) + rect(626, 368, 12, 32, "#bbb", 'rx="4"') +        // the handle
      rect(612, 420, 40, 26, INK, 'rx="5" ' + OUT) + circle(632, 433, 6, GLOW, 'class="lp-tw-glow"'); // the chute, with a capsule waiting
  }

  // ---- Stand-ins: a Lumi who has just moved in has no bed, toy chest or trophy shelf of her own yet (house.js draws
  // nothing for an empty spot), but in room mode those are how you nap, play and see her collection. So until she owns
  // one, something humble stands in its place: a mattress on the floor, a cardboard box of toys, a plank shelf.
  function mattress() {
    return shadow(638, 470, 62) + rect(582, 438, 112, 30, "#e8dff2", 'rx="10" ' + OUT) + rect(590, 426, 34, 16, "#fff", 'rx="7" ' + OUT) + // the mattress and a pillow
      path("M626 438 Q660 428 690 440 L690 452 Q660 446 626 452 Z", GLOW, 'opacity=".6"');                                                   // a little blanket in her glow
  }
  function toybox() {
    return shadow(390, 474, 42) + rect(356, 424, 70, 48, "#c8955f", 'rx="3" ' + OUT) + path("M356 424 L346 410 L382 414 Z M426 424 L436 410 L400 414 Z", "#b07d4a", OUT) + // a box, flaps open
      circle(380, 418, 10, "#ff8ecb", OUT) + rect(394, 404, 10, 20, "#70ebea", 'rx="3" ' + OUT) + path("M372 444 H410", "none", 'stroke="#8a5a3b" stroke-width="3"'); // a ball, a block, "TOYS"
  }
  function plank() {
    return rect(372, 138, 72, 8, "#8a5a3b", 'rx="2" ' + OUT) + path("M380 146 L384 156 M436 146 L432 156", "none", 'stroke="' + LINE + '" stroke-width="3"') + // a plank on two brackets
      path("M408 108 L413 122 L427 124 L416 132 L420 138 L408 132 L396 138 L400 132 L389 124 L403 122 Z", "none", 'stroke="' + GLOW + '" stroke-width="2.5" stroke-dasharray="4 3"'); // a dotted star: room for a first trophy
  }
  const STAND_INS = { bed: mattress, toychest: toybox, trophy: plank }; // house.js spot → what stands in while it's empty

  // What each room adds over its shell (in house.js's bedroom, only the paint pots: the rest is her own furniture).
  const SCENES = { kitchen: [counter, fridge, bowl, potions], bedroom: [paint], den: [wardrobe, terminal, capsules] };

  function scene(id, house) { // A room's things as SVG markup, drawn over its walls and floor. An unknown room has none.
    // `house` ({spot: id}, the bedroom only): stand-ins for the spots room mode needs that are still empty.
    let out = (SCENES[id] || []).map(function (draw) { return draw(); }).join("");
    if (id === "bedroom") Object.keys(STAND_INS).forEach(function (spot) { if (!house || !house[spot]) out = STAND_INS[spot]() + out; }); // behind the paint pots
    return out;
  }

  return { SHELL: SHELL, SCENES: SCENES, STAND_INS: STAND_INS, scene: scene };
});
