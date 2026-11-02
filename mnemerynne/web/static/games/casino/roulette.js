/* Full single-zero wheel: SVG artwork and motion are local; the server alone chooses the winning pocket. */
(function (root) {
  "use strict";
  const ORDER = Object.freeze([0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10,
    5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26]);
  const RED = new Set([1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]);
  const STEP = 360 / ORDER.length, TRACK = 231, POCKET = 157, SPIN_SECONDS = 6, LAND_MS = 1400;

  function spinPose(elapsed) { // Server-relative elapsed time makes reconnects resume the same decorative spin.
    const t = Math.max(0, Math.min(SPIN_SECONDS, elapsed));
    return { rotor: 120 * t - 6 * t * t, ball: -650 * t + 35 * t * t, radius: TRACK };
  }
  function landing(number) { // The final ball angle includes rotor rotation so it lands inside the actual numbered wedge.
    const index = ORDER.indexOf(number);
    if (index < 0) throw new RangeError("Roulette results must be a number from 0 to 36");
    const rotor = spinPose(SPIN_SECONDS).rotor + 50;
    return { rotor, ball: rotor + index * STEP, radius: POCKET };
  }
  function settlePose(number, progress) { // Ease the ball out of its outer track and into the server's winning pocket.
    const start = spinPose(SPIN_SECONDS), end = landing(number), t = Math.max(0, Math.min(1, progress));
    const ease = 1 - Math.pow(1 - t, 3);
    const ballEnd = end.ball + 360 * Math.floor((start.ball - end.ball - 40) / 360);
    return { rotor: start.rotor + (end.rotor - start.rotor) * ease,
      ball: start.ball + (ballEnd - start.ball) * ease, radius: TRACK + (POCKET - TRACK) * ease };
  }
  function matchesPick(number, choice) { // Light every pocket covered by the current pick; zero never belongs to an outside bet.
    if (/^\d+$/.test(choice)) return number === Number(choice);
    return number !== 0 && ({ red: RED.has(number), black: !RED.has(number), odd: number % 2 === 1,
      even: number % 2 === 0, low: number <= 18, high: number >= 19 })[choice] === true;
  }
  function svg(tag, attributes, text) { // Build vector artwork without HTML strings or external image dependencies.
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes || {}).forEach(([name, value]) => element.setAttribute(name, value));
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function point(radius, degrees) { // Angles start at twelve o'clock, matching the wheel's labels and ball orbit.
    const angle = degrees * Math.PI / 180;
    return [radius * Math.sin(angle), -radius * Math.cos(angle)];
  }
  function wedge(inner, outer, angle) { // One annular sector, centered on its number rather than on a boundary.
    const a = angle - STEP / 2, b = angle + STEP / 2;
    return "M " + point(outer, a).join(" ") + " A " + outer + " " + outer + " 0 0 1 " + point(outer, b).join(" ") +
      " L " + point(inner, b).join(" ") + " A " + inner + " " + inner + " 0 0 0 " + point(inner, a).join(" ") + " Z";
  }
  function create(reducedMotion) { // Own one persistent rotor, ball and animation loop for the current table.
    const element = document.createElement("div"); element.className = "roulette-display";
    const wheel = svg("svg", { viewBox: "-260 -260 520 520", class: "roulette-wheel", role: "img", "aria-label": "Single-zero roulette wheel with 37 numbered pockets" });
    const defs = svg("defs"), gold = svg("radialGradient", { id: "roulette-gold", cx: "36%", cy: "27%", r: "75%" });
    [["0%", "#fff3bf"], ["35%", "#d6ae57"], ["70%", "#86602b"], ["90%", "#e5bd67"], ["100%", "#48311c"]].forEach(([offset, color]) => gold.append(svg("stop", { offset, "stop-color": color })));
    const wood = svg("radialGradient", { id: "roulette-wood" });
    [["0%", "#4d2428"], ["70%", "#20151f"], ["90%", "#703a36"], ["100%", "#261923"]].forEach(([offset, color]) => wood.append(svg("stop", { offset, "stop-color": color })));
    defs.append(gold, wood); wheel.append(defs);
    wheel.append(svg("circle", { r: 254, fill: "url(#roulette-wood)", stroke: "#d9b666", "stroke-width": 3 }),
      svg("circle", { r: 242, fill: "#1a151c", stroke: "#997540", "stroke-width": 3 }),
      svg("circle", { r: TRACK, fill: "none", stroke: "#59432d", "stroke-width": 1 }),
      svg("circle", { r: 220, fill: "url(#roulette-gold)" }));
    const rotor = svg("g", { class: "roulette-rotor" }), pockets = [];
    ORDER.forEach((number, index) => {
      const angle = index * STEP, color = number === 0 ? "green" : RED.has(number) ? "red" : "black";
      const pocket = svg("g", { class: "roulette-pocket " + color, "data-number": number });
      pocket.append(svg("path", { d: wedge(139, 216, angle), class: "roulette-sector" }),
        svg("path", { d: wedge(141, 174, angle), class: "roulette-bed" }),
        svg("text", { x: 0, y: -195, transform: "rotate(" + angle + ")", "text-anchor": "middle", "dominant-baseline": "middle", class: "roulette-number" }, number));
      rotor.append(pocket); pockets.push(pocket);
    });
    rotor.append(svg("circle", { r: 137, fill: "url(#roulette-wood)", stroke: "#d9b666", "stroke-width": 4 }),
      svg("circle", { r: 113, fill: "none", stroke: "#ae8751", "stroke-width": 1 }),
      svg("circle", { r: 84, fill: "none", stroke: "#6f4f36", "stroke-width": 2 }));
    for (let i = 0; i < 4; i++) rotor.append(svg("path", { d: "M -6 -15 L -4 -96 Q 0 -106 4 -96 L 6 -15 Z", transform: "rotate(" + i * 90 + ")", fill: "url(#roulette-gold)" }));
    rotor.append(svg("circle", { r: 25, fill: "url(#roulette-gold)", stroke: "#edd08d", "stroke-width": 2 }));
    wheel.append(rotor);
    const orbit = svg("g", { class: "roulette-orbit" }), ball = svg("circle", { cx: 0, cy: -TRACK, r: 6, class: "roulette-ball" });
    orbit.append(ball); wheel.append(orbit);
    const caption = document.createElement("h2"); caption.className = "roulette-caption"; caption.setAttribute("aria-live", "polite");
    const note = document.createElement("p"); note.className = "roulette-note"; note.textContent = "Single zero · 37 pockets · Your pick lights the wheel";
    element.append(wheel, caption, note);
    let snapshot = null, clockOffset = 0, frame = null, landingAt = null, disposed = false;

    function paint(pose) { // Rotate SVG groups directly so the ball and numbered wheel remain independent.
      rotor.setAttribute("transform", "rotate(" + pose.rotor + ")");
      orbit.setAttribute("transform", "rotate(" + pose.ball + ")"); ball.setAttribute("cy", -pose.radius);
    }
    function animate() { // At most one rAF loop; hidden pages stop work and resync from server time when visible again.
      frame = null;
      if (disposed || !snapshot || document.hidden) return;
      let moving = false;
      if (snapshot.phase === "playing") {
        const elapsed = (Date.now() + clockOffset) / 1000 - (snapshot.deadline - SPIN_SECONDS);
        paint(spinPose(reducedMotion ? 0 : elapsed)); moving = !reducedMotion && elapsed < SPIN_SECONDS;
      } else if (snapshot.result) {
        const progress = landingAt === null ? 1 : Math.min(1, (performance.now() - landingAt) / LAND_MS);
        paint(settlePose(snapshot.result.number, progress)); moving = progress < 1;
        if (!moving) {
          wheel.dataset.landed = String(snapshot.result.number);
          pockets.forEach(p => p.classList.toggle("winning", Number(p.dataset.number) === snapshot.result.number));
        }
      } else paint(spinPose(0));
      if (moving) frame = requestAnimationFrame(animate);
    }
    function wake() { if (frame !== null) cancelAnimationFrame(frame); animate(); } // Replace, never stack, animation callbacks.
    function visibility() { // Return from another tab with the current phase instead of replaying an old spin.
      if (document.hidden) { if (frame !== null) cancelAnimationFrame(frame); frame = null; }
      else wake();
    }
    document.addEventListener("visibilitychange", visibility);
    return {
      element,
      update(data) { // Results start a short landing only on a live phase transition, never on every poll.
        const changed = !snapshot || snapshot.phase !== data.phase || snapshot.round !== data.round;
        if (changed) {
          landingAt = !reducedMotion && snapshot && snapshot.phase === "playing" && data.phase === "results" && snapshot.round === data.round ? performance.now() : null;
          delete wheel.dataset.landed; pockets.forEach(p => p.classList.remove("winning"));
          caption.textContent = data.result ? data.result.number + " · " + data.result.color : data.phase === "playing" ? "No more bets — wheel in motion" : "Place your bets";
          wheel.setAttribute("aria-label", data.result ? "Roulette result: " + data.result.number + " " + data.result.color : data.phase === "playing" ? "Roulette wheel spinning; waiting for the server result" : "Single-zero roulette wheel with 37 numbered pockets");
        }
        snapshot = data; clockOffset = data.server_time * 1000 - Date.now(); element.dataset.phase = data.phase; wake();
      },
      setPick(choice) { pockets.forEach(p => p.classList.toggle("picked", matchesPick(Number(p.dataset.number), choice))); }, // Purely visual coverage of the pending wager.
      destroy() { disposed = true; if (frame !== null) cancelAnimationFrame(frame); document.removeEventListener("visibilitychange", visibility); }, // Release listeners and frames on leaving.
    };
  }
  const api = { ORDER, spinPose, landing, settlePose, matchesPick, create };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CasinoRoulette = api;
})(typeof window !== "undefined" ? window : globalThis);
