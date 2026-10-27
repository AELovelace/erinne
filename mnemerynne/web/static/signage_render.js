/* Signage rendering, shared by the TV (media.js) and the admin page's preview (admin_signage.js):
   a Zone shows one slide at a time on two layers that cross over with the playlist's transition and
   fit; the renderers for each kind of slide; the CRT effects (signage_crt.css) on a frame; the
   "identify" overlay; and the clock checks a screen makes against its programme and a slide's own
   days, hours and dates. Server text goes through textContent, except an announcement's HTML, which
   the server rendered from Markdown and made safe. */
(function () {
  "use strict";

  const VERSION = "0.2.0";
  const TRANSITION_MS = { fade: 1000, cut: 0, slide: 700, zoom: 900 };
  const EFFECT_KEYS = ["scanlines", "curvature", "fringe", "flicker", "glow"];

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  // ------------------------------------------------------------- the clock
  function minutes(hhmm) { return Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5)); }

  function isoDate(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  /* Does a rule ({days, start, end, date_from, date_to}, any of them missing) hold at `now`? Days are 0 Monday
     to 6 Sunday; start = end is all day; end before start runs past midnight (then the early hours belong to
     yesterday's weekday). */
  function matches(rule, now) {
    now = now || new Date();
    const today = isoDate(now);
    if (rule.date_from && today < rule.date_from) return false;
    if (rule.date_to && today > rule.date_to) return false;
    const day = (now.getDay() + 6) % 7, yesterday = (day + 6) % 7;
    const mins = now.getHours() * 60 + now.getMinutes();
    const days = rule.days && rule.days.length ? rule.days : [0, 1, 2, 3, 4, 5, 6];
    if (!rule.start || !rule.end) return days.indexOf(day) >= 0;
    const from = minutes(rule.start), to = minutes(rule.end);
    if (from === to) return days.indexOf(day) >= 0;
    if (from < to) return days.indexOf(day) >= 0 && mins >= from && mins < to;
    return (days.indexOf(day) >= 0 && mins >= from) || (days.indexOf(yesterday) >= 0 && mins < to);
  }

  // ------------------------------------------------------------ renderers
  /* The element for one slide. hooks: reveal() once it can be seen, advance() when it is over (videos),
     skip() when it won't load, volume(media) to apply the screen's volume. */
  function render(item, show, hooks) {
    const fit = item.fit || (show && show.fit) || "contain";
    if (item.type === "image") {
      const wrap = el("div", "mc-sg-fit mc-sg-fit-" + fit);
      if (fit === "blur") {
        const back = el("img", "mc-sg-blur");
        back.alt = "";
        back.src = item.src;
        wrap.appendChild(back);
      }
      const img = el("img");
      img.alt = "";
      img.onload = hooks.reveal;
      img.onerror = hooks.skip;
      img.src = item.src;
      wrap.appendChild(img);
      return wrap;
    }
    if (item.type === "video") {
      const wrap = el("div", "mc-sg-fit mc-sg-fit-" + (fit === "blur" ? "contain" : fit));
      const v = el("video");
      if (!item.sound) v.dataset.silent = "1";
      if (hooks.volume) hooks.volume(v); else v.muted = true;
      v.autoplay = true;
      v.playsInline = true;
      v.addEventListener("playing", hooks.reveal, { once: true });
      v.addEventListener("ended", hooks.advance);
      v.addEventListener("error", hooks.skip);
      v.src = item.src;
      wrap.appendChild(v);
      return wrap;
    }
    if (item.type === "web") {
      const frame = el("iframe");
      frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
      frame.setAttribute("referrerpolicy", "no-referrer");
      frame.setAttribute("allow", "autoplay");
      frame.src = item.url;
      return frame;
    }
    if (item.type === "youtube") {
      const frame = el("iframe");
      frame.setAttribute("allow", "autoplay; encrypted-media");
      frame.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
      frame.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(item.id) + "?autoplay=1&controls=0&rel=0"
        + "&playsinline=1&loop=1&playlist=" + encodeURIComponent(item.id) + (item.sound ? "" : "&mute=1");
      return frame;
    }
    const card = el("div", "mc-sg-text");
    if (item.type === "text") {
      card.style.background = item.background;
      card.style.color = item.color;
      if (item.image) card.style.backgroundImage = "url(\"" + item.image + "\")";
      if (item.title) card.appendChild(el("h1", null, item.title));
      if (item.body) card.appendChild(el("p", null, item.body));
    } else {                                   // an announcement: Markdown made safe by the server
      card.appendChild(el("div", "mc-sg-kicker", "Announcement"));
      card.appendChild(el("h1", null, item.title));
      const body = el("div", "mc-sg-html");
      body.innerHTML = item.html;
      card.appendChild(body);
    }
    return card;
  }

  function immediate(item) {
    return item.type === "text" || item.type === "notice" || item.type === "web" || item.type === "youtube";
  }

  // ------------------------------------------------------------------ zone
  /* A box that shows slides one after another: the next slide is drawn on the hidden layer and, once it
     can be seen, brought over the current one with the transition. */
  function Zone(box, opts) {
    this.box = box;
    this.opts = opts || {};
    this.layers = [el("div", "mc-sg-layer"), el("div", "mc-sg-layer")];
    box.append(this.layers[0], this.layers[1]);
    this.layer = 0;
    this.seq = 0;
    this.timer = null;
  }

  /* Show `item` with the playlist's transition and fit; `done(skipped)` when it has had its time (or won't load). */
  Zone.prototype.play = function (item, show, done) {
    clearTimeout(this.timer);
    const self = this, seq = ++this.seq;
    const cur = this.layers[this.layer], next = this.layers[this.layer ^ 1];
    const transition = item.transition || (show && show.transition) || "fade";
    const speed = this.opts.speed || 1;
    const advance = function () { if (seq === self.seq) done(false); };
    const skip = function () { if (seq === self.seq) self.timer = setTimeout(function () { done(true); }, 1000); };
    const reveal = function () {
      if (seq !== self.seq) return;
      next.className = "mc-sg-layer mc-sg-t-" + transition + " on";
      cur.classList.remove("on");
      self.layer ^= 1;
      setTimeout(function () { if (!cur.classList.contains("on")) cur.replaceChildren(); }, TRANSITION_MS[transition] + 200);
      if (item.type !== "video") self.timer = setTimeout(advance, (item.seconds || 10) * 1000 / speed);
      if (self.opts.onShown) self.opts.onShown(item);
    };
    next.className = "mc-sg-layer mc-sg-t-" + transition;
    next.replaceChildren(render(item, show, { reveal: reveal, advance: advance, skip: skip, volume: this.opts.volume }));
    if (immediate(item)) reveal();
  };

  Zone.prototype.stop = function () {
    this.seq += 1;
    clearTimeout(this.timer);
    this.layers.forEach(function (l) { l.className = "mc-sg-layer"; l.replaceChildren(); });
  };

  // --------------------------------------------------------------- effects
  /* The CRT look on a frame (signage_crt.css): classes for each part, the intensity as --crt. The SVG filter
     that splits a picture's colours is added to the page once. */
  function applyEffects(frame, fx) {
    fx = fx || {};
    frame.classList.toggle("mc-crt", !!fx.crt);
    EFFECT_KEYS.forEach(function (k) { frame.classList.toggle("mc-crt-" + k, !!fx.crt && fx[k] !== false); });
    frame.style.setProperty("--crt", String(fx.crt ? (fx.intensity === undefined ? 0.6 : fx.intensity) : 0));
    let overlay = frame.querySelector(":scope > .mc-crt-overlay");
    if (fx.crt && !overlay) {
      overlay = el("div", "mc-crt-overlay");
      overlay.setAttribute("aria-hidden", "true");
      overlay.append(el("div", "mc-crt-scan"), el("div", "mc-crt-bar"), el("div", "mc-crt-vignette"));
      frame.appendChild(overlay);
    }
    if (fx.crt && fx.fringe !== false && !document.getElementById("mc-crt-filter")) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("id", "mc-crt-filter");
      svg.setAttribute("aria-hidden", "true");
      svg.setAttribute("width", "0");
      svg.setAttribute("height", "0");
      svg.style.position = "absolute";
      const filter = document.createElementNS("http://www.w3.org/2000/svg", "filter");
      filter.setAttribute("id", "mc-crt-fringe");
      filter.setAttribute("color-interpolation-filters", "sRGB");
      const parts = [
        ["feColorMatrix", { in: "SourceGraphic", type: "matrix", result: "r",
                            values: "1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" }],
        ["feOffset", { in: "r", dx: "-1.5", dy: "0", result: "rl" }],
        ["feColorMatrix", { in: "SourceGraphic", type: "matrix", result: "b",
                            values: "0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" }],
        ["feOffset", { in: "b", dx: "1.5", dy: "0", result: "br" }],
        ["feColorMatrix", { in: "SourceGraphic", type: "matrix", result: "g",
                            values: "0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" }],
        ["feBlend", { in: "rl", in2: "g", mode: "screen", result: "rg" }],
        ["feBlend", { in: "rg", in2: "br", mode: "screen" }],
      ];
      parts.forEach(function (p) {
        const node = document.createElementNS("http://www.w3.org/2000/svg", p[0]);
        Object.keys(p[1]).forEach(function (k) { node.setAttribute(k, p[1][k]); });
        filter.appendChild(node);
      });
      svg.appendChild(filter);
      document.body.appendChild(svg);
    }
  }

  /* The tube warming up (or going dark) when a show starts or stops; resolves when it's done. */
  function power(frame, on, fx) {
    if (!fx || !fx.crt || fx.power_on === false || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return Promise.resolve();
    }
    frame.classList.remove("mc-crt-power-on", "mc-crt-power-off");
    frame.getBoundingClientRect();
    frame.classList.add(on ? "mc-crt-power-on" : "mc-crt-power-off");
    return new Promise(function (resolve) { setTimeout(resolve, on ? 650 : 450); });
  }

  // -------------------------------------------------------------- identify
  function identify(title, sub, ms) {
    let box = document.getElementById("mc-sg-identify");
    if (!box) {
      box = el("div", "mc-sg-identify");
      box.id = "mc-sg-identify";
      document.body.appendChild(box);
    }
    box.replaceChildren(el("strong", null, title), el("span", null, sub || ""));
    box.classList.add("on");
    clearTimeout(box._timer);
    box._timer = setTimeout(function () { box.classList.remove("on"); }, ms || 5000);
  }

  window.SignageRender = { VERSION: VERSION, Zone: Zone, render: render, matches: matches, applyEffects: applyEffects,
                           power: power, identify: identify, TRANSITION_MS: TRANSITION_MS };
})();
