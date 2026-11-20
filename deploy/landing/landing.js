/* Landing page backdrop (index.html): the app's slideshow of pixel-art cities whose layers drift sideways,
   nearer ones faster. The same idea as mnemerynne/web/static/parallax.js, trimmed for a page that is always
   neon. The art is copied next to this file by deploy/linux/landing-install.sh (parallax/city-N/1..n.png).
   Reduced motion: one still city, no slideshow. */
(function () {
  "use strict";

  var LAYERS = [5, 6, 5, 6, 5, 6, 5, 5];     // layers per city; keep in step with the app's parallax.js
  var SHOW_MS = 45000;                        // how long each city stays
  var FADE_MS = 3000;                         // matches .parallax-scene's transition in landing.css
  var SKY_SECONDS = 360;                      // one loop of the sky; layer n drifts n times faster

  var root = document.querySelector(".parallax");
  if (!root) return;
  var still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var timer = null;
  var current = 0;                            // the city on screen

  function urls(n) {                          // the layer images of city n, sky first
    var out = [];
    for (var i = 1; i <= LAYERS[n]; i++) out.push("parallax/city-" + (n + 1) + "/" + i + ".png");
    return out;
  }

  function preload(list) {                    // resolves once every layer is decoded, so a city never fades in half-drawn
    return Promise.all(list.map(function (src) {
      var img = new Image();
      img.src = src;
      return img.decode().catch(function () {});
    }));
  }

  function scene(n) {                         // build one city: a stack of drifting layers
    var node = document.createElement("div");
    node.className = "parallax-scene";
    urls(n).forEach(function (src, i) {
      var layer = document.createElement("div");
      layer.className = "parallax-layer";
      layer.style.backgroundImage = 'url("' + src + '")';
      layer.style.animationDuration = SKY_SECONDS / (i + 1) + "s";
      node.appendChild(layer);
    });
    return node;
  }

  function show(n) {                          // fade city n in over the last one, then queue the next
    preload(urls(n)).then(function () {
      current = n;
      var next = scene(n);
      root.appendChild(next);
      next.getBoundingClientRect();           // commit opacity 0 first, so the fade runs
      next.classList.add("on");
      root.querySelectorAll(".parallax-scene").forEach(function (old) {
        if (old !== next) setTimeout(function () { old.remove(); }, FADE_MS + 100);
      });
      if (!still) timer = setTimeout(function () { show((n + 1) % LAYERS.length); }, SHOW_MS);
    });
  }

  // While the tab is away the drift and the slideshow rest.
  document.addEventListener("visibilitychange", function () {
    root.classList.toggle("paused", document.hidden);
    clearTimeout(timer);
    if (!document.hidden && !still) timer = setTimeout(function () { show((current + 1) % LAYERS.length); }, SHOW_MS);
  });

  show(Math.floor(Math.random() * LAYERS.length));   // a random city to start
})();
