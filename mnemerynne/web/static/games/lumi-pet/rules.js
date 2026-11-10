/* Lumi Pet's small pure rules for the page. Her needs, pantry and coins live on the server (mnemerynne/lumipet.py);
   what stays here is what only the device knows (its local clock, for night and day) and how to read things aloud. */
(function (root, make) {
  const rules = make();
  if (typeof module === "object" && module.exports) module.exports = rules; else root.LumiPetRules = rules;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const PALETTES = ["rose", "lagoon", "starlight", "zephyr", "wisp", "umbra"]; // Each is also her element in a fight (lumibattle.GLOW_ELEMENT).
  const METERS = ["hunger", "energy", "joy"];
  const LOW = 25; // Below this a need shows in her mood; the last quarter of a meter takes a day to empty.

  function scene(state, now) { // Follow the device's local night, with the server's nap or gentle-wake override.
    const hour = new Date(now).getHours(), night = hour >= 22 || hour < 7;
    const override = state.restUntil > now;
    return { night: night, asleep: override && state.rest === "nap" || (night && !(override && state.rest === "awake")) };
  }

  function legacy(raw) { // Read the old browser-only save (version 1) so its happy moments can move to the account once.
    const value = raw && typeof raw === "object" && raw.version === 1 ? raw : {};
    return {
      moments: Number.isSafeInteger(value.moments) ? Math.max(0, Math.min(999999, value.moments)) : 0,
      glow: PALETTES.includes(value.palette) ? value.palette : null,
    };
  }

  function mood(pet) { // Her most pressing need decides the caption; at zero she droops, and nothing else is lost.
    let worst = null;
    METERS.forEach(function (meter) { if (pet[meter] < LOW && (worst === null || pet[meter] < pet[worst])) worst = meter; });
    return { need: worst, sad: METERS.some(function (meter) { return pet[meter] <= 0; }) };
  }

  function nextPalette(current) { // Glow colours cycle in a fixed order.
    return PALETTES[(PALETTES.indexOf(current) + 1) % PALETTES.length];
  }

  function countdown(ms) { // "1:05" for a timed nap or play; never negative.
    const seconds = Math.max(0, Math.ceil(ms / 1000));
    return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }

  function signed(amount, label) { // "+25 joy" or "−10 energy", for an item's description.
    return (amount < 0 ? "−" : "+") + Math.abs(amount) + " " + label;
  }

  return { PALETTES: PALETTES, METERS: METERS, LOW: LOW, scene: scene, legacy: legacy, mood: mood,
    nextPalette: nextPalette, countdown: countdown, signed: signed };
});
