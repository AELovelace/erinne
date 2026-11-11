/* Lumi's Lounge: the art the four table games share. Dealer Lumi behind the felt, each player's own Lumi sitting in
   their seat (her glow and outfit come from the server: casino.py puts lumipet.look() on every coin seat), poker
   chips for bets, and pastel ponies for the race. Plain SVG strings, drawn in Lumi Pet's 320 x 300 box
   (templates/games/lumi-pet.html) so her clothes from lumi-pet/closet.js fit her here too.

   Safety: nothing a person typed goes into this markup. The glow is looked up in PALETTES (unknown ones fall back),
   outfit ids are only drawn when closet.js knows them, and numbers are formatted here. Names and speech go in as text.
   Works in a browser (window.LumiTable) and in Node for tests/js/lumi_table.test.mjs. */
(function (root, make) {
  const table = make(root);                                                            // build the module once
  if (typeof module === "object" && module.exports) module.exports = table; else root.LumiTable = table;
})(typeof globalThis !== "undefined" ? globalThis : self, function (root) {         // root: where closet.js left LumiPetCloset
  "use strict";

  // Lumi's six glows (lumipet.GLOWS), the same colours as lumi-pet/game.css's .lp-room palettes.
  const PALETTES = Object.freeze({
    rose: { glow: "#ff8ecb", body: "#fce1f2", shadow: "#b8569f" },
    lagoon: { glow: "#70ebea", body: "#d2fcf6", shadow: "#328b9d" },
    starlight: { glow: "#ffdc83", body: "#fff1cd", shadow: "#b98b49" },
    zephyr: { glow: "#8be9a8", body: "#dcfbe6", shadow: "#3f9a63" },
    wisp: { glow: "#c9a8ff", body: "#efe4ff", shadow: "#7a55c0" },
    umbra: { glow: "#8f9bff", body: "#d9dcf7", shadow: "#3d3f8f" },
  });
  const SEAT_GLOWS = ["rose", "lagoon", "starlight", "zephyr", "wisp", "umbra"];     // plain Lumis (credit seats) take turns
  const DEALER_GLOW = "wisp";                                                          // Dealer Lumi is always lilac
  // Six ponies, in casino.HORSES order: [coat, mane].
  const PONIES = [["#ece6ff", "#c9a8ff"], ["#b99ae0", "#5a3fa8"], ["#fff1cd", "#ffc94d"], ["#ffd3e8", "#ff6fa8"], ["#dcfbe6", "#4fc483"], ["#d2f2fc", "#3fa9d9"]];
  // Chips, biggest first: [value, colour]. Bets are always a multiple of 10 (casino.py).
  const CHIPS = [[500, "#c9a8ff"], [100, "#ffdc83"], [50, "#70ebea"], [10, "#ff8ecb"]];
  const MOST_PER_STACK = 6;                                                            // a taller pile is drawn as six

  function closet() { return root && root.LumiPetCloset ? root.LumiPetCloset : null; } // lumi-pet/closet.js, when the page loaded it

  function glowOf(seat, index) { // Which glow a seat's Lumi wears: her own, or a plain one by seat number.
    const own = seat && seat.look && seat.look.glow;
    return Object.prototype.hasOwnProperty.call(PALETTES, own) ? own : SEAT_GLOWS[Math.abs(index || 0) % SEAT_GLOWS.length];
  }

  function paletteStyle(glow) { // The three colours as CSS variables on her <svg>, read by game.css's .lt-* rules.
    const p = PALETTES[glow] || PALETTES.rose;
    return "--pet-glow:" + p.glow + ";--pet-body:" + p.body + ";--pet-shadow:" + p.shadow;
  }

  function heart(x, y, s) { // A little heart path centred on (x, y).
    return "M" + x + " " + (y + s) + " C" + (x - s * 2) + " " + (y - s * .2) + " " + (x - s * .9) + " " + (y - s * 1.6) + " " + x + " " + (y - s * .5) +
      " C" + (x + s * .9) + " " + (y - s * 1.6) + " " + (x + s * 2) + " " + (y - s * .2) + " " + x + " " + (y + s) + "Z";
  }

  // Dealer Lumi's uniform, worn over her: a pink visor, a vest with gold buttons, a bow tie and a fan of cards.
  const DEALER_KIT =
    '<path d="M100 96 Q160 70 220 96 L218 108 Q160 84 102 108Z" fill="#ff8ecb" stroke="#462846" stroke-opacity=".5" stroke-width="2"/>' +   // visor band
    '<path d="M108 104 Q160 86 212 104 Q196 124 160 122 Q124 124 108 104Z" fill="#ff8ecb" fill-opacity=".55" stroke="#b8569f" stroke-width="2"/>' + // see-through brim
    '<path d="M110 206 Q96 240 116 270 L150 270 L146 214Z M210 206 Q224 240 204 270 L170 270 L174 214Z" fill="#3d2450" stroke="#462846" stroke-opacity=".5" stroke-width="2"/>' + // vest
    '<circle cx="160" cy="236" r="4" fill="#ffdc83"/><circle cx="160" cy="252" r="4" fill="#ffdc83"/>' +                                     // buttons
    '<path d="M160 206 L138 194 L138 218Z M160 206 L182 194 L182 218Z" fill="#ff6fa8" stroke="#462846" stroke-opacity=".5" stroke-width="2"/><circle cx="160" cy="206" r="5" fill="#ffdc83"/>' + // bow tie
    '<g class="lt-dealer-cards"><rect x="206" y="226" width="22" height="30" rx="3" fill="#fff5e8" stroke="#b8569f" stroke-width="2" transform="rotate(-14 217 241)"/>' +
    '<rect x="214" y="224" width="22" height="30" rx="3" fill="#fff5e8" stroke="#b8569f" stroke-width="2" transform="rotate(8 225 239)"/>' +
    '<path d="M225 232 L227 237 L232 239 L227 241 L225 246 L223 241 L218 239 L223 237Z" fill="#ff8ecb" transform="rotate(8 225 239)"/></g>';

  /* One Lumi, as SVG markup. look: {glow, outfit} (outfit may be missing). opts.mood: "idle", "ready", "happy",
     "aww", "cheer" or "deal" (game.css shows the matching eyes and extras); opts.dealer adds the uniform. */
  function lumi(look, opts) {
    const o = opts || {}, glow = look && Object.prototype.hasOwnProperty.call(PALETTES, look.glow) ? look.glow : "rose";
    const outfit = look && look.outfit && typeof look.outfit === "object" ? look.outfit : {};
    const wear = closet(), back = wear ? wear.render(outfit, "back") : "", front = wear ? wear.render(outfit, "front") : ""; // unknown ids draw nothing
    const mood = ["idle", "ready", "happy", "aww", "cheer", "deal"].indexOf(o.mood) >= 0 ? o.mood : "idle";
    return '<svg class="lt-lumi' + (o.dealer ? " lt-dealer" : "") + '" viewBox="0 0 320 300" data-mood="' + mood + '" style="' + paletteStyle(glow) + '" aria-hidden="true" focusable="false">' +
      '<g class="lt-wings"><path d="M113 176 C8 187 25 74 79 108 C104 122 113 153 113 176Z"/><path d="M207 176 C312 187 295 74 241 108 C216 122 207 153 207 176Z"/>' +
      '<path d="M109 182 C36 174 48 238 93 215Z M211 182 C284 174 272 238 227 215Z"/></g>' +
      "<g>" + back + "</g>" +
      '<path class="lt-tail" d="M217 228 C279 254 286 188 256 193 C238 196 260 215 239 218"/>' +
      '<ellipse class="lt-body" cx="160" cy="214" rx="62" ry="57"/><ellipse class="lt-belly" cx="160" cy="230" rx="36" ry="31"/>' +
      '<path class="lt-body" d="M92 153 L88 62 Q89 49 100 58 L137 93 Q160 83 183 93 L220 58 Q231 49 232 62 L228 153 Q230 204 160 208 Q90 204 92 153Z"/>' +
      '<path class="lt-ear" d="M101 77 L106 118 L126 102Z M219 77 L214 118 L194 102Z"/>' +
      '<path class="lt-star" d="M160 103 L165 116 L179 120 L165 125 L160 139 L155 125 L141 120 L155 116Z"/>' +
      '<g class="lt-eyes-open"><ellipse cx="129" cy="153" rx="5" ry="9"/><ellipse class="lt-wink-eye" cx="191" cy="153" rx="5" ry="9"/></g>' +
      '<g class="lt-eyes-happy"><path d="M120 157 Q129 145 138 157 M182 157 Q191 145 200 157"/></g>' +                       // ^ ^
      '<g class="lt-eyes-wink"><path d="M182 154 Q191 163 200 154"/></g>' +                                                    // one eye shut
      '<ellipse class="lt-cheek" cx="114" cy="169" rx="13" ry="6"/><ellipse class="lt-cheek" cx="206" cy="169" rx="13" ry="6"/>' +
      '<path class="lt-mouth" d="M155 165 L160 169 L165 165 M160 169 Q151 184 145 173 M160 169 Q169 184 175 173"/>' +
      '<path class="lt-mouth-open" d="M150 170 Q160 188 170 170Z"/>' +                                                         // a happy "o"
      '<ellipse class="lt-paw" cx="123" cy="259" rx="24" ry="13"/><ellipse class="lt-paw" cx="197" cy="259" rx="24" ry="13"/>' +
      "<g>" + front + "</g>" + (o.dealer ? DEALER_KIT : "") +
      '<g class="lt-hearts"><path d="' + heart(72, 70, 10) + '"/><path d="' + heart(250, 52, 13) + '"/><path d="' + heart(270, 112, 8) + '"/></g>' +
      '<path class="lt-sweat" d="M236 96 Q246 112 240 120 Q230 124 228 114 Q230 106 236 96Z"/>' +                            // aww: a sweat drop
      "</svg>";
  }

  function dealer(mood) { return lumi({ glow: DEALER_GLOW, outfit: {} }, { mood: mood, dealer: true }); } // Dealer Lumi herself

  function chipCounts(amount) { // [[value, colour, how many]] for a bet, biggest chips first.
    let left = Math.max(0, Math.floor(Number(amount) || 0));
    return CHIPS.map(function (c) { const n = Math.floor(left / c[0]); left -= n * c[0]; return [c[0], c[1], n]; }).filter(function (c) { return c[2] > 0; });
  }

  function chips(amount) { // A little pile of chips for a bet, one stack per colour; "" for no bet.
    const stacks = chipCounts(amount);
    if (!stacks.length) return "";
    const tall = Math.min(MOST_PER_STACK, Math.max.apply(null, stacks.map(function (s) { return s[2]; })));
    const width = stacks.length * 30 + 4, height = 16 + tall * 5;
    let out = "";
    stacks.forEach(function (s, col) {
      const x = 17 + col * 30;
      for (let i = 0; i < Math.min(MOST_PER_STACK, s[2]); i++) {
        const y = height - 9 - i * 5;
        out += '<ellipse cx="' + x + '" cy="' + (y + 2.5) + '" rx="13" ry="5.5" fill="#462846" fill-opacity=".55"/>' +   // the chip's edge
          '<ellipse cx="' + x + '" cy="' + y + '" rx="13" ry="5.5" fill="' + s[1] + '" stroke="#fff" stroke-width="2" stroke-dasharray="3.4 3"/>' +
          '<ellipse cx="' + x + '" cy="' + y + '" rx="6" ry="2.6" fill="#fff" fill-opacity=".55"/>';                    // the middle
      }
    });
    return '<svg class="lt-chips" viewBox="0 0 ' + width + " " + height + '" width="' + width + '" height="' + height + '" aria-hidden="true" focusable="false">' + out + "</svg>";
  }

  function pony(index) { // A pastel pony facing right (the finish line), in her lane's colours.
    const c = PONIES[Math.abs(index || 0) % PONIES.length], edge = 'stroke="#462846" stroke-opacity=".55" stroke-width="1.6" stroke-linejoin="round"';
    return '<svg class="lt-pony" viewBox="0 0 70 48" aria-hidden="true" focusable="false">' +
      '<path d="M14 22 Q2 22 4 36 Q8 28 15 28Z" fill="' + c[1] + '" ' + edge + "/>" +                                       // tail
      '<g class="lt-legs" stroke="#462846" stroke-opacity=".7" stroke-width="3.2" stroke-linecap="round">' +
      '<path class="lt-leg-a" d="M20 30 L17 43"/><path class="lt-leg-b" d="M27 31 L29 43"/><path class="lt-leg-a" d="M40 31 L43 43"/><path class="lt-leg-b" d="M46 30 L44 43"/></g>' +
      '<ellipse cx="32" cy="26" rx="18" ry="9.5" fill="' + c[0] + '" ' + edge + "/>" +                                        // body
      '<path d="M44 22 L50 8 L58 10 L52 26Z" fill="' + c[0] + '" ' + edge + "/>" +                                            // neck
      '<ellipse cx="57" cy="11" rx="9" ry="6" fill="' + c[0] + '" ' + edge + ' transform="rotate(18 57 11)"/>' +           // head
      '<path d="M51 5 L52 -1 L56 4Z" fill="' + c[0] + '" ' + edge + "/>" +                                                    // ear
      '<path d="M53 4 Q44 6 44 20 Q48 12 51 11 Q47 18 50 22 Q52 12 56 7Z" fill="' + c[1] + '" ' + edge + "/>" +               // mane
      '<circle cx="59" cy="10" r="1.6" fill="#462846"/><ellipse cx="61" cy="14" rx="2.4" ry="1.3" fill="#ff8ecb" fill-opacity=".7"/>' + // eye, blush
      '<path d="M30 21 L31.5 24.5 L35 25 L32 27 L33 31 L30 29 L27 31 L28 27 L25 25 L28.5 24.5Z" fill="#fff" fill-opacity=".85"/>' + // a star on her flank
      "</svg>";
  }

  // What Dealer Lumi says. Picked by round number, so everyone at the table hears the same line.
  const LINES = {
    betting: ["Place your bets, cuties~", "Pull up a cushion and pick your chips!", "Fresh round, fresh luck ✨", "Bets are open! Don’t be shy~"],
    playing: {
      blackjack: ["Cards coming your way~", "Hit or stand? I’m watching ♡"],
      roulette: ["No more bets! Round and round~", "Wheee, look at it go!"],
      "horse-racing": ["And they’re off! Go, go, go!", "Run, ponies, run!"],
      slots: ["Spin spin spin~", "Reels are rolling!"],
    },
  };
  const HORSES = ["Moonbeam", "Velvet Thunder", "Starlight", "Cherry Comet", "Lucky Clover", "Blue Ribbon"];  // casino.HORSES

  function say(game, state, me) { // Dealer Lumi's line for this moment; me: your seat, or null on a shared TV.
    if (!state) return "Pull up a cushion! Pick a table, or start your own~";
    const round = Math.abs(Number(state.round) || 0), pick = function (list) { return list[round % list.length]; };
    if (state.phase === "betting") return pick(LINES.betting);
    if (state.phase === "playing") return pick(LINES.playing[game] || LINES.playing.slots);
    const r = state.result || {};
    let line = "That’s the round!";
    if (game === "blackjack" && typeof r.dealer_total === "number") line = r.dealer_total > 21 ? "Oopsie, I bust at " + r.dealer_total + "!" : "Dealer has " + r.dealer_total + "!";
    else if (game === "roulette" && typeof r.number === "number") line = r.number + " " + (r.color || "") + "!";
    else if (game === "horse-racing" && Array.isArray(r.order) && HORSES[r.order[0]]) line = HORSES[r.order[0]] + " wins by a whisker!";
    else if (game === "slots") line = "Let’s see those lines!";
    if (me && me.bet) {
      const staked = me.bet * (game === "slots" ? (state.slot_lines || 1) : 1);
      line += me.payout > staked ? " Yay, you won! ♡" : me.payout === staked ? " Your chips come home~" : " Aww, next time!";
    }
    return line;
  }

  function seatMood(state, seat, game) { // How a seat's Lumi feels right now.
    if (!state || !seat || !seat.bet) return "idle";
    if (state.phase === "betting") return "ready";
    if (state.phase === "playing") return "ready";
    const staked = seat.bet * (game === "slots" ? (state.slot_lines || 1) : 1);
    return seat.payout > staked ? "happy" : seat.payout === staked ? "idle" : "aww";
  }

  function dealerMood(state) { // Dealer Lumi: a wink while bets go down, dealing while it plays, cheering at the results.
    if (!state) return "cheer";
    return state.phase === "playing" ? "deal" : state.phase === "results" ? "cheer" : "ready";
  }

  return { PALETTES: PALETTES, SEAT_GLOWS: SEAT_GLOWS, glowOf: glowOf, lumi: lumi, dealer: dealer, chipCounts: chipCounts,
    chips: chips, pony: pony, say: say, seatMood: seatMood, dealerMood: dealerMood };
});
