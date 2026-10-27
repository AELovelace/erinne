/* Neon theme backdrop: a slideshow of pixel-art cities whose layers drift
   sideways at different speeds (parallax). Layer 1 is the sky, the highest
   number is nearest. Only runs while html[data-theme="neon"]; the Settings
   page can switch themes live, so the attribute is watched. Reduced motion:
   one still city per page load, no drift and no slideshow. */
(function () {
  "use strict";

  const LAYERS = [5, 6, 5, 6, 5, 6, 5, 5];   // layers per city, static/parallax/city-N/1..n.png
  const SHOW_MS = 45000;                      // how long each city stays
  const FADE_MS = 3000;                       // keep in step with .parallax-scene's transition
  const SKY_SECONDS = 360;                    // one full loop of the sky; nearer layers are faster

  const root = document.querySelector(".parallax");
  if (!root) return;
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  let seq = 0;                                // bumped by each show() and stop(); stale loads give up
  let timer = null;
  let running = false;
  let current = 0;                            // the city on screen

  function urls(n) {
    const out = [];
    for (let i = 1; i <= LAYERS[n]; i++) out.push("/static/parallax/city-" + (n + 1) + "/" + i + ".png");
    return out;
  }

  // Resolves once every layer is decoded, so a city never fades in half-drawn.
  function preload(list) {
    return Promise.all(list.map(function (src) {
      const img = new Image();
      img.src = src;
      return img.decode().catch(function () {});
    }));
  }

  function scene(n) {
    const node = document.createElement("div");
    node.className = "parallax-scene";
    urls(n).forEach(function (src, i) {
      const layer = document.createElement("div");
      layer.className = "parallax-layer";
      layer.style.backgroundImage = 'url("' + src + '")';
      layer.style.animationDuration = SKY_SECONDS / (i + 1) + "s";
      node.appendChild(layer);
    });
    return node;
  }

  async function show(n) {
    const mine = ++seq;
    await preload(urls(n));
    if (!running || mine !== seq) return;
    current = n;
    const next = scene(n);
    root.appendChild(next);
    next.getBoundingClientRect();               // commit opacity 0 first, so the fade runs
    next.classList.add("on");
    // The outgoing city stays underneath until the new one has faded in.
    root.querySelectorAll(".parallax-scene").forEach(function (old) {
      if (old !== next) setTimeout(function () { old.remove(); }, FADE_MS + 100);
    });
    if (!still.matches) timer = setTimeout(function () { show((n + 1) % LAYERS.length); }, SHOW_MS);
  }

  function start() {
    if (running) return;
    running = true;
    show(Math.floor(Math.random() * LAYERS.length));
  }

  function stop() {
    running = false;
    seq++;
    clearTimeout(timer);
    root.replaceChildren();
  }

  function sync() {
    if (document.documentElement.dataset.theme === "neon") start(); else stop();
  }

  // While the tab is away (a phone's screen off, another app) the drift and the slideshow stop.
  document.addEventListener("visibilitychange", function () {
    root.classList.toggle("paused", document.hidden);
    clearTimeout(timer);
    if (!document.hidden && running && !still.matches) {
      timer = setTimeout(function () { show((current + 1) % LAYERS.length); }, SHOW_MS);
    }
  });

  new MutationObserver(sync).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  still.addEventListener("change", function () { stop(); sync(); });
  sync();
})();
