/* Lumi Pet's small, local-only save and clock rules. Time changes the room, never affection or wellbeing. */
(function (root, make) {
  const rules = make();
  if (typeof module === "object" && module.exports) module.exports = rules; else root.LumiPetRules = rules;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const ACTIONS = ["cuddle", "snack", "play", "rest"];
  const PALETTES = ["rose", "lagoon", "starlight"];
  const NAP_MS = 2 * 60 * 1000, AWAKE_MS = 15 * 60 * 1000;

  function normalize(raw, now) { // Accept only known, bounded fields; old or damaged saves start safely.
    const value = raw && typeof raw === "object" && raw.version === 1 ? raw : {};
    const until = Number.isSafeInteger(value.restUntil) && value.restUntil > now && value.restUntil <= now + AWAKE_MS
      ? value.restUntil : 0; // Clock changes and edited saves cannot leave Lumi asleep forever.
    return {
      version: 1,
      moments: Number.isSafeInteger(value.moments) ? Math.max(0, Math.min(999999, value.moments)) : 0,
      palette: PALETTES.includes(value.palette) ? value.palette : "rose",
      memory: ACTIONS.includes(value.memory) ? value.memory : null,
      rest: until && ["nap", "awake"].includes(value.rest) ? value.rest : "auto",
      restUntil: until,
    };
  }

  function scene(state, now) { // Follow the device's local night, with a short nap or gentle wake override.
    const hour = new Date(now).getHours(), night = hour >= 22 || hour < 7;
    const override = state.restUntil > now;
    return { night: night, asleep: override && state.rest === "nap" || (night && !(override && state.rest === "awake")) };
  }

  function interact(raw, action, now) { // Record a happy moment; visits never create chores, decay or streaks.
    const state = normalize(raw, now);
    if (!ACTIONS.includes(action)) return state;
    const asleep = scene(state, now).asleep;
    state.moments = Math.min(999999, state.moments + 1);
    state.memory = action;
    state.rest = action === "rest" && !asleep ? "nap" : "awake";
    state.restUntil = now + (state.rest === "nap" ? NAP_MS : AWAKE_MS);
    return state;
  }

  function nextPalette(raw, now) { // Cosmetic changes do not award moments or interrupt a nap.
    const state = normalize(raw, now);
    state.palette = PALETTES[(PALETTES.indexOf(state.palette) + 1) % PALETTES.length];
    return state;
  }

  return { ACTIONS: ACTIONS, PALETTES: PALETTES, NAP_MS: NAP_MS, AWAKE_MS: AWAKE_MS,
    normalize: normalize, scene: scene, interact: interact, nextPalette: nextPalette };
});
