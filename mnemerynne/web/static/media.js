/* Media: a Kodi-style media centre over the share's Media library (mnemerynne/media/).
   Views come from the hash (#movies, #movie/12, #album/<key>, #pictures/34, #live, #radio...).
   Films and clips play in a full-screen player, music and radio in the now-playing bar; what the
   browser can't play itself is asked of the server (/api/media/play), which answers with the file or
   an HLS session (played with hls.js). Arrow keys move between tiles like a remote; Enter opens,
   Escape or Backspace goes back. All server text goes through textContent, never innerHTML. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const main = $("mc");
  const content = $("mc-content");
  const isAdmin = main.dataset.admin === "1";
  const folder = main.dataset.folder || "Media";   // the Media folder on the share (Settings → Media)
  let renderToken = 0;

  // ------------------------------------------------------------- helpers
  function clock(seconds) {
    if (seconds === null || seconds === undefined || !isFinite(seconds)) return "–";
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(r).padStart(2, "0");
  }

  function runtime(seconds) {
    if (!seconds) return "";
    const m = Math.round(seconds / 60);
    return m >= 60 ? Math.floor(m / 60) + " h " + (m % 60) + " min" : m + " min";
  }

  function button(text, cls, onClick, title) {
    const b = el("button", cls || "", text);
    b.type = "button";
    if (title) b.title = title;
    b.addEventListener("click", onClick);
    return b;
  }

  function showError(message) {
    const node = $("mc-error");
    node.textContent = message || "";
    node.hidden = !message;
  }

  function setHead(title, crumbs, tools) {
    $("mc-title").textContent = title;
    $("mc-crumbs").replaceChildren.apply($("mc-crumbs"), crumbs || []);
    $("mc-tools").replaceChildren.apply($("mc-tools"), tools || []);
  }

  function backdrop(url) {
    const node = $("mc-backdrop");
    if (url) node.style.backgroundImage = "url(\"" + url.replace(/"/g, "") + "\")";
    node.classList.toggle("on", !!url);
  }

  /* A tile: {title, sub, art, glyph, shape: "poster" | "square" | "wide", badge, live, progress, onClick}. */
  function card(o) {
    const b = el("button", "mc-card");
    b.type = "button";
    b.dataset.nav = "";
    const art = el("div", "mc-art" + (o.shape === "square" ? " square" : o.shape === "wide" ? " wide" : ""));
    const fallback = el("div", "mc-fallback");
    fallback.append(el("span", "mc-glyph", o.glyph || "🎞"));
    function useFallback() { if (!fallback.isConnected) art.prepend(fallback); }
    if (o.art) {
      const img = el("img");
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.src = o.art;
      img.addEventListener("error", function () { img.remove(); useFallback(); });
      art.appendChild(img);
    } else {
      useFallback();
    }
    if (o.badge) art.appendChild(el("span", "mc-badge" + (o.live ? " live" : ""), o.badge));
    if (o.progress && o.progress.duration) {
      const bar = el("div", "mc-progress");
      const fill = el("span");
      fill.style.width = Math.min(100, 100 * o.progress.position / o.progress.duration).toFixed(1) + "%";
      bar.appendChild(fill);
      art.appendChild(bar);
    }
    b.append(art, el("div", "mc-label", o.title || "Untitled"));
    if (o.sub) b.appendChild(el("div", "mc-sub", o.sub));
    b.addEventListener("click", o.onClick);
    return b;
  }

  function shelf(title, cards, shape, layout) {
    const box = el("section", "mc-shelf");
    if (title) box.appendChild(el("h2", null, title));
    const row = el("div", (layout === "grid" ? "mc-grid" : "mc-row") + (shape && shape !== "poster" ? " " + shape : ""));
    cards.forEach(function (c) { row.appendChild(c); });
    box.appendChild(row);
    return box;
  }

  function empty(text, strong) {
    const p = el("p", "mc-empty");
    if (strong) p.appendChild(el("strong", null, strong + " "));
    p.appendChild(document.createTextNode(text));
    return p;
  }

  function movieCard(m, wide) {
    return card({ title: m.title, sub: [m.year, runtime(m.duration)].filter(Boolean).join(" · "),
                  art: wide ? (m.fanart || m.art) : m.art, shape: wide ? "wide" : "poster", glyph: "🎬",
                  progress: m.progress, onClick: function () { go("movie/" + m.id); } });
  }

  function albumCard(a) {
    return card({ title: a.album, sub: a.artist + (a.year ? " · " + a.year : ""), art: a.art, shape: "square",
                  glyph: "♫", onClick: function () { go("album/" + encodeURIComponent(a.key)); } });
  }

  function pictureCard(p, list, index) {
    return card({ title: p.title, sub: p.taken_at ? p.taken_at.slice(0, 10) : "", art: p.art, shape: "wide",
                  glyph: p.type === "video" ? "🎞" : "🖼", badge: p.type === "video" ? clock(p.duration) : null,
                  onClick: function () {
                    if (p.type === "video") player.open(p);
                    else viewer.open(list.filter(function (x) { return x.type === "image"; }),
                                     list.filter(function (x) { return x.type === "image"; }).indexOf(p));
                  } });
  }

  /* Which codecs this browser decodes beyond the baseline, for the server's direct/remux/transcode choice. */
  function caps() {
    const ms = window.ManagedMediaSource || window.MediaSource;
    function ok(type) {
      try { return !!(ms && ms.isTypeSupported && ms.isTypeSupported(type)); } catch (e) { return false; }
    }
    return { hevc: ok('video/mp4; codecs="hvc1.1.6.L120.90"'), ac3: ok('audio/mp4; codecs="ac-3"'),
             eac3: ok('audio/mp4; codecs="ec-3"'), alac: ok('audio/mp4; codecs="alac"') };
  }

  function attachHls(media, url, onFatal) {
    if (window.Hls && window.Hls.isSupported()) {
      const hls = new window.Hls({ maxBufferLength: 60, backBufferLength: 90 });
      hls.on(window.Hls.Events.ERROR, function (event, data) {
        if (!data.fatal) return;
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
        onFatal(data.details || "stream error");
      });
      hls.loadSource(url);
      hls.attachMedia(media);
      return hls;
    }
    if (media.canPlayType("application/vnd.apple.mpegurl")) { media.src = url; return null; }
    onFatal("this browser can't play HLS");
    return null;
  }

  // ------------------------------------------------------------- routing
  function go(hash) {
    if (location.hash === "#" + hash) render(); else location.hash = hash;
  }

  function route() {
    const raw = location.hash.replace(/^#/, "");
    const slash = raw.indexOf("/");
    return slash < 0 ? { view: raw || "home", arg: "" } : { view: raw.slice(0, slash), arg: raw.slice(slash + 1) };
  }

  const SECTION_OF = { home: "home", movies: "movies", movie: "movies", music: "music", album: "music",
                       artist: "music", pictures: "pictures", live: "live", radio: "radio" };

  async function render() {
    const r = route();
    const token = ++renderToken;
    const section = SECTION_OF[r.view] || "home";
    document.querySelectorAll(".mc-menu-item").forEach(function (b) {
      b.setAttribute("aria-current", String(b.dataset.view === section));
    });
    showError("");
    backdrop(null);
    const views = { home: viewHome, movies: viewMovies, movie: viewMovie, music: viewMusic, album: viewAlbum,
                    artist: viewArtist, pictures: viewPictures, live: viewLive, radio: viewRadio };
    try {
      const nodes = await (views[r.view] || viewHome)(r.arg);
      if (token !== renderToken) return;
      content.replaceChildren.apply(content, nodes);
      content.scrollTop = 0;
      focusFirst();
    } catch (err) {
      if (token !== renderToken) return;
      content.replaceChildren();
      showError(err.status === 404 ? "That isn't here any more, or you can't open it." : err.message);
    }
  }

  // --------------------------------------------------------------- views
  async function viewHome() {
    setHead("Home");
    const data = await api("GET", "/api/media/home");
    const out = [];
    if (data.continue.length) out.push(shelf("Continue watching", data.continue.map(function (m) { return movieCard(m, true); }), "wide"));
    if (data.movies.length) out.push(shelf("Recently added films", data.movies.map(function (m) { return movieCard(m); })));
    if (data.albums.length) out.push(shelf("Recently added music", data.albums.map(albumCard), "square"));
    if (data.pictures.length) out.push(shelf("Recent pictures", data.pictures.map(function (p, i, all) { return pictureCard(p, all, i); }), "wide"));
    if (!out.length) {
      out.push(data.library
        ? empty("Put films in " + folder + "\\Movies, music in " + folder + "\\Music and photos in " + folder + "\\Pictures on the share. " +
                "They show up here a minute or two later, with their artwork.", "Nothing here yet.")
        : empty(isAdmin ? "There's no " + folder + " folder you can open on the share yet. Turn Media on in Access → Settings, " +
                          "and check who may read the " + folder + " folder in Windows."
                        : "There's no " + folder + " folder you can open on the share. Ask an admin for access.", "No library."));
    }
    out.push(shelf("Live", [
      card({ title: "Live TV", sub: "Streams", glyph: "📡", shape: "wide", onClick: function () { go("live"); } }),
      card({ title: "Radio", sub: "Internet radio", glyph: "📻", shape: "wide", onClick: function () { go("radio"); } }),
    ], "wide"));
    return out;
  }

  async function viewMovies() {
    // Searching re-renders the grid only: rebuilding the box would lose the cursor mid-word.
    if (!$("mc-movie-search")) moviesHead();
    const data = await api("GET", "/api/media/movies?sort=" + state.sort + "&q=" + encodeURIComponent(state.q));
    if (!data.movies.length) return [empty(state.q ? "No film matches that." : "No films yet. Put them in " + folder + "\\Movies on the share — one file per film, named like “Blade Runner (1982).mkv”.")];
    return [shelf(null, data.movies.map(function (m) { return movieCard(m); }), "poster", "grid")];
  }

  function moviesHead() {
    const search = el("input");
    search.id = "mc-movie-search";
    search.type = "search";
    search.placeholder = "Search films";
    search.value = state.q;
    search.setAttribute("aria-label", "Search films");
    const sort = el("select");
    sort.setAttribute("aria-label", "Sort");
    [["title", "Title"], ["year", "Year"], ["added", "Recently added"]].forEach(function (o) {
      const opt = el("option", null, o[1]);
      opt.value = o[0];
      sort.appendChild(opt);
    });
    sort.value = state.sort;
    let timer = null;
    search.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () { state.q = search.value; render(); }, 300);
    });
    sort.addEventListener("change", function () { state.sort = sort.value; render(); });
    setHead("Movies", [], [search, sort]);
  }

  async function viewMovie(id) {
    const m = await api("GET", "/api/media/items/" + encodeURIComponent(id));
    setHead("Movies", [button("‹ All films", "", function () { history.length > 1 ? history.back() : go("movies"); })]);
    backdrop(m.fanart);
    const box = el("div", "mc-detail");
    const art = el("div", "mc-art");
    if (m.art) { const img = el("img"); img.src = m.art; img.alt = ""; art.appendChild(img); }
    else { const f = el("div", "mc-fallback"); f.append(el("span", "mc-glyph", "🎬")); art.appendChild(f); }
    const text = el("div");
    text.appendChild(el("h2", null, m.title));
    const facts = el("div", "mc-facts");
    [m.year, runtime(m.duration), m.genre, m.width && m.height ? m.width + "×" + m.height : ""]
      .filter(Boolean).forEach(function (f) { facts.appendChild(el("span", null, String(f))); });
    text.appendChild(facts);
    if (m.overview) text.appendChild(el("p", "mc-overview", m.overview));
    const actions = el("div", "mc-actions");
    const resume = m.progress && m.progress.position > 30 && (!m.progress.duration || m.progress.position < m.progress.duration * 0.95);
    if (resume) {
      const r = button("▶ Resume from " + clock(m.progress.position), "", function () { player.open(m, m.progress.position); });
      r.dataset.nav = "";
      actions.appendChild(r);
    }
    const p = button(resume ? "Play from the start" : "▶ Play", resume ? "secondary" : "", function () { player.open(m, 0); });
    p.dataset.nav = "";
    actions.appendChild(p);
    text.appendChild(actions);
    const how = { direct: "plays as it is", remux: "repackaged for the browser", audio: "sound converted for the browser",
                  transcode: "converted for the browser" }[m.playback] || "";
    text.appendChild(el("p", "mc-tech", [m.container, m.video_codec, m.audio_codec].filter(Boolean).join(" · ").toUpperCase()
      + (how ? " — " + how : "")));
    box.append(art, text);
    return [box];
  }

  async function viewMusic(arg) {
    const tabs = [button("Albums", arg === "artists" ? "secondary" : "", function () { go("music"); }),
                  button("Artists", arg === "artists" ? "" : "secondary", function () { go("music/artists"); })];
    setHead("Music", [], tabs);
    if (arg === "artists") {
      const data = await api("GET", "/api/media/music/artists");
      if (!data.artists.length) return [empty("No music yet. Put albums in " + folder + "\\Music on the share, one folder per album.")];
      return [shelf(null, data.artists.map(function (a) {
        return card({ title: a.name, sub: a.albums + (a.albums === 1 ? " album" : " albums"), art: a.art, shape: "square",
                      glyph: "🎤", onClick: function () { go("artist/" + encodeURIComponent(a.name)); } });
      }), "square", "grid")];
    }
    const data = await api("GET", "/api/media/music/albums");
    if (!data.albums.length) return [empty("No music yet. Put albums in " + folder + "\\Music on the share, one folder per album.")];
    return [shelf(null, data.albums.map(albumCard), "square", "grid")];
  }

  async function viewArtist(name) {
    const artist = decodeURIComponent(name);
    setHead(artist, [button("‹ Artists", "", function () { go("music/artists"); })]);
    const data = await api("GET", "/api/media/music/albums?artist=" + encodeURIComponent(artist));
    return [shelf(null, data.albums.map(albumCard), "square", "grid")];
  }

  async function viewAlbum(key) {
    const a = await api("GET", "/api/media/music/album?key=" + encodeURIComponent(decodeURIComponent(key)));
    setHead("Music", [button("‹ Albums", "", function () { history.length > 1 ? history.back() : go("music"); }),
                      button(a.artist, "", function () { go("artist/" + encodeURIComponent(a.artist)); })]);
    backdrop(a.art);
    const box = el("div", "mc-detail");
    const art = el("div", "mc-art square");
    if (a.art) { const img = el("img"); img.src = a.art; img.alt = ""; art.appendChild(img); }
    else { const f = el("div", "mc-fallback"); f.append(el("span", "mc-glyph", "♫")); art.appendChild(f); }
    const text = el("div");
    text.appendChild(el("h2", null, a.album));
    const facts = el("div", "mc-facts");
    const total = a.tracks.reduce(function (s, t) { return s + (t.duration || 0); }, 0);
    [a.artist, a.year, a.tracks.length + (a.tracks.length === 1 ? " track" : " tracks"), runtime(total)]
      .filter(Boolean).forEach(function (f) { facts.appendChild(el("span", null, String(f))); });
    text.appendChild(facts);
    const actions = el("div", "mc-actions");
    const playAll = button("▶ Play", "", function () { nowPlaying.playQueue(a.tracks, 0, a.art); });
    const shuffle = button("⤮ Shuffle", "secondary", function () {
      const list = a.tracks.slice();
      for (let i = list.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); const t = list[i]; list[i] = list[j]; list[j] = t; }
      nowPlaying.playQueue(list, 0, a.art);
    });
    playAll.dataset.nav = ""; shuffle.dataset.nav = "";
    actions.append(playAll, shuffle);
    text.appendChild(actions);
    const list = el("ol", "mc-tracks");
    a.tracks.forEach(function (t, i) {
      const li = el("li");
      const row = el("button", "mc-track");
      row.type = "button";
      row.dataset.nav = "";
      row.dataset.id = t.id;
      row.append(el("span", "n", t.track ? String(t.track) : String(i + 1)), el("span", null, t.title),
                 el("span", "t", clock(t.duration)));
      if (t.artist && t.artist !== a.artist) row.children[1].appendChild(el("span", "mc-sub", " — " + t.artist));
      row.addEventListener("click", function () { nowPlaying.playQueue(a.tracks, i, a.art); });
      li.appendChild(row);
      list.appendChild(li);
    });
    text.appendChild(list);
    box.append(art, text);
    nowPlaying.markTracks(list);
    return [box];
  }

  async function viewPictures(folder) {
    const data = await api("GET", "/api/media/pictures" + (folder ? "?folder=" + encodeURIComponent(folder) : ""));
    const crumbs = [];
    if (data.folder && data.folder !== data.root) {
      crumbs.push(button("Pictures", "", function () { go("pictures"); }));
      data.path.slice(1).forEach(function (p, i, all) {
        crumbs.push(el("span", null, "›"));
        crumbs.push(i === all.length - 1 ? el("span", null, p.name)
                                         : button(p.name, "", function () { go("pictures/" + p.id); }));
      });
    }
    setHead("Pictures", crumbs);
    const out = [];
    if (data.folders.length) {
      out.push(shelf(null, data.folders.map(function (f) {
        return card({ title: f.name, art: f.art, shape: "wide", glyph: "📁", onClick: function () { go("pictures/" + f.id); } });
      }), "wide", "grid"));
    }
    if (data.items.length) {
      out.push(shelf(data.folders.length ? "In this folder" : null,
                     data.items.map(function (p, i, all) { return pictureCard(p, all, i); }), "wide", "grid"));
    }
    if (!out.length) out.push(empty("No pictures here yet. Put photos and clips in " + folder + "\\Pictures on the share; folders show as albums."));
    return out;
  }

  async function viewLive() {
    setHead("Live TV");
    const data = await api("GET", "/api/media/live");
    if (!data.streams.length) {
      return [empty(isAdmin ? "No live streams yet. Add RTMP or HLS streams in Access → Settings → Media."
                            : "No live streams have been set up.")];
    }
    return [shelf(null, data.streams.map(function (s) {
      return card({ title: s.name, sub: s.kind.toUpperCase(), glyph: "📡", shape: "wide", badge: "LIVE", live: true,
                    onClick: function () { player.openLive(s); } });
    }), "wide", "grid")];
  }

  async function viewRadio() {
    const refresh = button("↻ Refresh", "secondary", function () { render(); });
    setHead("Radio", [], [refresh]);
    const data = await api("GET", "/api/media/live");
    const out = [];
    if (data.problem) out.push(el("p", "mc-note", data.problem));
    if (!data.stations.length) {
      out.push(empty(isAdmin ? "No stations. Set the SGCRelay station list or add stations in Access → Settings → Media."
                             : "No radio stations have been set up."));
      return out;
    }
    out.push(shelf(null, data.stations.map(function (s) {
      const sub = s.running === null ? "Station"
        : (s.running ? "On air" : "Starts when you tune in") + " · " + s.listeners + (s.listeners === 1 ? " listener" : " listeners");
      const c = card({ title: s.name, sub: sub, glyph: "📻", shape: "square", onClick: function () { nowPlaying.playStation(s); } });
      if (s.running) c.querySelector(".mc-art").appendChild(el("span", "mc-badge live", "ON AIR"));
      return c;
    }), "square", "grid"));
    return out;
  }

  const state = { q: "", sort: "title" };

  // -------------------------------------------------------------- player
  const video = $("mc-video");
  const player = {
    item: null, hls: null, session: null, offset: 0, duration: 0, live: false, lastSave: 0, idleTimer: null,

    status: function (text) { $("mc-status").textContent = text || ""; },

    show: function (title) {
      nowPlaying.pause();
      $("mc-player").hidden = false;
      $("mc-player-title").textContent = title;
      this.wake();
      $("mc-toggle").focus();
    },

    open: async function (item, start) {
      this.item = item;
      this.live = false;
      this.duration = item.duration || 0;
      this.show(item.title);
      $("mc-seek").disabled = false;
      await this.load(start || 0);
    },

    openLive: async function (source) {
      this.item = null;
      this.live = true;
      this.duration = 0;
      this.show(source.name);
      this.detach();
      this.setTag("Live");
      this.status("Connecting…");
      $("mc-seek").disabled = true;
      try {
        const r = await api("POST", "/api/media/live/" + source.id + "/watch");
        this.session = null;   // shared with other viewers: the server stops it when everyone has left
        this.offset = 0;
        this.attach(r.url, 0);
      } catch (err) { this.status(err.message); }
    },

    setTag: function (text) { const t = $("mc-player-tag"); t.textContent = text || ""; t.hidden = !text; },

    load: async function (start) {
      this.detach();
      this.status("Preparing…");
      let r;
      try {
        r = await api("POST", "/api/media/play/" + this.item.id, { start: start, slot: "video", caps: caps() });
      } catch (err) { this.status(err.message); return; }
      if (this.item === null || $("mc-player").hidden) { if (r.session) api("DELETE", "/api/media/sessions/" + r.session).catch(function () {}); return; }
      this.duration = r.duration || this.duration;
      this.setTag({ transcode: "Transcoding" + (r.backend && r.backend !== "software" ? " · GPU (" + r.backend + ")" : ""),
                    remux: "Repackaged", audio: "Sound converted" }[r.mode] || "");
      if (r.mode === "direct") {
        this.session = null;
        this.offset = 0;
        video.src = r.url;
        if (start) video.addEventListener("loadedmetadata", function once() { video.currentTime = start; video.removeEventListener("loadedmetadata", once); });
        video.play().catch(function () {});
      } else {
        this.session = r.session;
        this.attach(r.url, r.start);
      }
    },

    attach: function (url, offset) {
      const self = this;
      this.offset = offset;
      this.hls = attachHls(video, url, function (detail) { self.status("Playback stopped: " + detail); });
      video.play().catch(function () {});
    },

    detach: function () {
      if (this.hls) { this.hls.destroy(); this.hls = null; }
      video.pause();
      video.removeAttribute("src");
      video.load();
    },

    position: function () { return this.offset + (video.currentTime || 0); },

    seekTo: function (t) {
      if (this.live || !this.item) return;
      t = Math.max(0, Math.min(t, this.duration ? this.duration - 1 : t));
      if (!this.session) { video.currentTime = t; return; }
      const rel = t - this.offset;
      const end = video.seekable.length ? video.seekable.end(video.seekable.length - 1) : 0;
      if (rel >= 0 && rel <= end - 1) video.currentTime = rel;
      else this.load(t);   // outside what's been prepared: start again from there
    },

    saveProgress: function (force) {
      if (this.live || !this.item || !this.item.id) return;
      const now = Date.now();
      if (!force && now - this.lastSave < 15000) return;
      this.lastSave = now;
      api("PUT", "/api/media/progress/" + this.item.id, { position: this.position(), duration: this.duration || null })
        .catch(function () {});
    },

    close: function () {
      if ($("mc-player").hidden) return;
      this.saveProgress(true);
      const session = this.session;
      this.detach();
      this.session = null;
      this.item = null;
      if (session) api("DELETE", "/api/media/sessions/" + session).catch(function () {});
      if (document.fullscreenElement) document.exitFullscreen().catch(function () {});
      $("mc-player").hidden = true;
      this.status("");
      if (route().view === "movie" || route().view === "home") render();
    },

    toggle: function () { if (video.paused) video.play().catch(function () {}); else video.pause(); },

    wake: function () {
      const box = $("mc-player");
      box.classList.remove("idle");
      clearTimeout(this.idleTimer);
      this.idleTimer = setTimeout(function () { if (!video.paused) box.classList.add("idle"); }, 3000);
    },

    key: function (e) {
      const k = e.key;
      this.wake();
      if (k === "Escape" || k === "Backspace") { this.close(); }
      else if (k === " " || k === "k" || k === "MediaPlayPause") { this.toggle(); }
      else if (k === "ArrowLeft") { this.seekTo(this.position() - 10); }
      else if (k === "ArrowRight") { this.seekTo(this.position() + 30); }
      else if (k === "ArrowUp") { video.volume = Math.min(1, video.volume + 0.1); }
      else if (k === "ArrowDown") { video.volume = Math.max(0, video.volume - 0.1); }
      else if (k === "f") { this.fullscreen(); }
      else if (k === "m") { video.muted = !video.muted; }
      else return false;
      return true;
    },

    fullscreen: function () {
      if (document.fullscreenElement) document.exitFullscreen().catch(function () {});
      else $("mc-player").requestFullscreen().catch(function () {});
    },
  };

  video.addEventListener("timeupdate", function () {
    if (player.live) { $("mc-pos").textContent = "LIVE"; $("mc-len").textContent = ""; return; }
    const pos = player.position();
    $("mc-pos").textContent = clock(pos);
    $("mc-len").textContent = clock(player.duration);
    const seek = $("mc-seek");
    seek.max = String(Math.floor(player.duration || 0));
    if (document.activeElement !== seek) seek.value = String(Math.floor(pos));
    player.saveProgress(false);
  });
  video.addEventListener("playing", function () { player.status(""); player.wake(); });
  video.addEventListener("waiting", function () { player.status("Buffering…"); });
  video.addEventListener("ended", function () {
    if (player.item && player.item.id) {
      api("PUT", "/api/media/progress/" + player.item.id, { position: player.duration || player.position(), duration: player.duration || null })
        .catch(function () {});
    }
    player.close();
  });
  video.addEventListener("click", function () { player.toggle(); });
  $("mc-seek").addEventListener("change", function () { player.seekTo(Number($("mc-seek").value)); });
  $("mc-seek").addEventListener("input", function () { $("mc-pos").textContent = clock(Number($("mc-seek").value)); });
  $("mc-toggle").addEventListener("click", function () { player.toggle(); });
  $("mc-back10").addEventListener("click", function () { player.seekTo(player.position() - 10); });
  $("mc-fwd30").addEventListener("click", function () { player.seekTo(player.position() + 30); });
  $("mc-mute").addEventListener("click", function () { video.muted = !video.muted; $("mc-mute").textContent = video.muted ? "🔇" : "🔊"; });
  $("mc-full").addEventListener("click", function () { player.fullscreen(); });
  $("mc-player-close").addEventListener("click", function () { player.close(); });
  $("mc-player").addEventListener("mousemove", function () { player.wake(); });

  // --------------------------------------------------------- now playing
  const audio = $("mc-audio");
  const nowPlaying = {
    queue: [], i: -1, art: null, hls: null, session: null, offset: 0, station: null,

    show: function (title, sub, art, seekable) {
      $("mc-np").hidden = false;
      main.classList.add("mc-has-np");
      $("mc-np-title").textContent = title;
      $("mc-np-sub").textContent = sub || "";
      const box = $("mc-np-art");
      box.replaceChildren();
      if (art) { const img = el("img", "mc-np-art"); img.src = art; img.alt = ""; box.appendChild(img); }
      else box.textContent = seekable ? "♫" : "📻";
      $("mc-np-seekrow").hidden = !seekable;
      $("mc-np-prev").hidden = $("mc-np-next").hidden = !seekable;
    },

    playQueue: function (tracks, index, art) {
      this.station = null;
      this.queue = tracks.slice();
      this.art = art || null;
      this.i = index;
      this.load(0);
    },

    load: async function (start) {
      const t = this.queue[this.i];
      if (!t) { this.stop(); return; }
      this.detach();
      this.show(t.title, [t.artist, t.album].filter(Boolean).join(" — "), t.art || this.art, true);
      this.markTracks(document);
      let r;
      try {
        r = await api("POST", "/api/media/play/" + t.id, { start: start || 0, slot: "audio", caps: caps() });
      } catch (err) { $("mc-np-sub").textContent = err.message; return; }
      if (this.queue[this.i] !== t) return;
      this.duration = r.duration || t.duration || 0;
      if (r.mode === "direct") {
        this.session = null; this.offset = 0;
        audio.src = r.url;
        if (start) audio.addEventListener("loadedmetadata", function once() { audio.currentTime = start; audio.removeEventListener("loadedmetadata", once); });
      } else {
        this.session = r.session; this.offset = r.start;
        this.hls = attachHls(audio, r.url, function (d) { $("mc-np-sub").textContent = "Playback stopped: " + d; });
      }
      audio.play().catch(function () {});
    },

    playStation: function (s) {
      this.detach();
      this.queue = []; this.i = -1; this.station = s;
      this.show(s.name, "Radio · connecting…", null, false);
      audio.src = s.play;
      audio.play().catch(function (err) { $("mc-np-sub").textContent = "Radio · " + (err.message || "can't play"); });
    },

    detach: function () {
      if (this.hls) { this.hls.destroy(); this.hls = null; }
      if (this.session) { api("DELETE", "/api/media/sessions/" + this.session).catch(function () {}); this.session = null; }
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    },

    position: function () { return this.offset + (audio.currentTime || 0); },

    seekTo: function (t) {
      if (this.station) return;
      if (!this.session) { audio.currentTime = t; return; }
      const rel = t - this.offset;
      const end = audio.seekable.length ? audio.seekable.end(audio.seekable.length - 1) : 0;
      if (rel >= 0 && rel <= end - 1) audio.currentTime = rel; else this.load(t);
    },

    next: function () { if (this.i < this.queue.length - 1) { this.i++; this.load(0); } else this.stop(); },
    prev: function () {
      if (this.position() > 5) { this.seekTo(0); return; }
      if (this.i > 0) { this.i--; this.load(0); }
    },
    pause: function () { audio.pause(); },
    toggle: function () {
      if ($("mc-np").hidden) return;
      if (audio.paused) audio.play().catch(function () {}); else audio.pause();
    },
    stop: function () {
      this.detach();
      this.queue = []; this.i = -1; this.station = null;
      $("mc-np").hidden = true;
      main.classList.remove("mc-has-np");
      this.markTracks(document);
    },

    /* Highlight the playing track in an album's list. */
    markTracks: function (scope) {
      const t = this.queue[this.i];
      scope.querySelectorAll(".mc-track").forEach(function (b) {
        b.classList.toggle("playing", !!t && Number(b.dataset.id) === t.id);
      });
    },
  };

  audio.addEventListener("timeupdate", function () {
    if (nowPlaying.station) return;
    const pos = nowPlaying.position();
    $("mc-np-pos").textContent = clock(pos);
    $("mc-np-len").textContent = clock(nowPlaying.duration);
    const seek = $("mc-np-seek");
    seek.max = String(Math.floor(nowPlaying.duration || 0));
    if (document.activeElement !== seek) seek.value = String(Math.floor(pos));
  });
  audio.addEventListener("playing", function () {
    if (nowPlaying.station) $("mc-np-sub").textContent = "Radio · on air";
  });
  audio.addEventListener("ended", function () { nowPlaying.next(); });
  audio.addEventListener("error", function () {
    if (nowPlaying.station) $("mc-np-sub").textContent = "Radio · the station isn't answering";
  });
  $("mc-np-seek").addEventListener("change", function () { nowPlaying.seekTo(Number($("mc-np-seek").value)); });
  $("mc-np-toggle").addEventListener("click", function () { nowPlaying.toggle(); });
  $("mc-np-next").addEventListener("click", function () { nowPlaying.next(); });
  $("mc-np-prev").addEventListener("click", function () { nowPlaying.prev(); });
  $("mc-np-stop").addEventListener("click", function () { nowPlaying.stop(); });
  if ("mediaSession" in navigator) {
    navigator.mediaSession.setActionHandler("nexttrack", function () { nowPlaying.next(); });
    navigator.mediaSession.setActionHandler("previoustrack", function () { nowPlaying.prev(); });
  }

  // -------------------------------------------------------------- viewer
  const viewer = {
    items: [], i: 0, timer: null, idleTimer: null,

    open: function (items, index) {
      if (!items.length) return;
      this.items = items;
      this.i = Math.max(0, index);
      $("mc-viewer").hidden = false;
      this.show();
      this.wake();
      $("mc-viewer-next").focus();
    },

    show: function () {
      const p = this.items[this.i];
      const img = $("mc-viewer-img");
      img.onerror = function () { if (p.art && img.src.indexOf(p.art) < 0) img.src = p.art; };
      img.src = p.stream;
      $("mc-viewer-title").textContent = p.title + (p.taken_at ? " · " + p.taken_at.replace("T", " ").slice(0, 16) : "")
        + (p.camera ? " · " + p.camera : "");
      $("mc-viewer-count").textContent = (this.i + 1) + " / " + this.items.length;
      const next = this.items[(this.i + 1) % this.items.length];
      if (next && next !== p) { const pre = new Image(); pre.src = next.stream; }
    },

    step: function (d) {
      this.i = (this.i + d + this.items.length) % this.items.length;
      this.show();
    },

    slideshow: function () {
      if (this.timer) { clearInterval(this.timer); this.timer = null; $("mc-viewer-show").textContent = "▶"; return; }
      const self = this;
      this.timer = setInterval(function () { self.step(1); }, 5000);
      $("mc-viewer-show").textContent = "⏸";
    },

    close: function () {
      if (this.timer) this.slideshow();
      $("mc-viewer").hidden = true;
      $("mc-viewer-img").removeAttribute("src");
    },

    wake: function () {
      const box = $("mc-viewer");
      box.classList.remove("idle");
      clearTimeout(this.idleTimer);
      this.idleTimer = setTimeout(function () { box.classList.add("idle"); }, 2500);
    },

    key: function (e) {
      this.wake();
      if (e.key === "Escape" || e.key === "Backspace") this.close();
      else if (e.key === "ArrowLeft") this.step(-1);
      else if (e.key === "ArrowRight" || e.key === " ") this.step(1);
      else if (e.key === "s") this.slideshow();
      else return false;
      return true;
    },
  };
  $("mc-viewer-prev").addEventListener("click", function () { viewer.step(-1); });
  $("mc-viewer-next").addEventListener("click", function () { viewer.step(1); });
  $("mc-viewer-show").addEventListener("click", function () { viewer.slideshow(); });
  $("mc-viewer-close").addEventListener("click", function () { viewer.close(); });
  $("mc-viewer").addEventListener("mousemove", function () { viewer.wake(); });

  // ---------------------------------------------------------- remote nav
  function targets() {
    return Array.prototype.filter.call(
      document.querySelectorAll("#mc-menu button, #mc-crumbs button, #mc-tools button, #mc-tools input, #mc-tools select, " +
                                "#mc-content [data-nav], #mc-np:not([hidden]) button"),
      function (n) { return n.offsetParent !== null && !n.hidden; });
  }

  function focusFirst() {
    const active = document.activeElement;
    if (active && active !== document.body && main.contains(active) && active.isConnected && !content.contains(active)) return;
    const first = content.querySelector("[data-nav]");
    if (first) first.focus({ preventScroll: true });
  }

  /* Move focus to the nearest target in the arrow's direction, like a TV remote. */
  function move(dir) {
    const all = targets();
    const cur = document.activeElement;
    if (all.indexOf(cur) < 0) { (content.querySelector("[data-nav]") || all[0]).focus(); return; }
    const r = cur.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    let best = null, bestScore = Infinity;
    all.forEach(function (t) {
      if (t === cur) return;
      const b = t.getBoundingClientRect();
      const dx = b.left + b.width / 2 - cx, dy = b.top + b.height / 2 - cy;
      let primary, secondary;
      if (dir === "right") { if (b.left < r.right - 4) return; primary = dx; secondary = dy; }
      else if (dir === "left") { if (b.right > r.left + 4) return; primary = -dx; secondary = dy; }
      else if (dir === "down") { if (b.top < r.bottom - 4) return; primary = dy; secondary = dx; }
      else { if (b.bottom > r.top + 4) return; primary = -dy; secondary = dx; }
      const score = primary + Math.abs(secondary) * 2.5;
      if (score < bestScore) { bestScore = score; best = t; }
    });
    if (best) { best.focus({ preventScroll: true }); best.scrollIntoView({ block: "nearest", inline: "nearest" }); }
  }

  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (!$("mc-player").hidden) { if (player.key(e)) e.preventDefault(); return; }
    if (!$("mc-viewer").hidden) { if (viewer.key(e)) e.preventDefault(); return; }
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    const typing = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
    const dirs = { ArrowRight: "right", ArrowLeft: "left", ArrowDown: "down", ArrowUp: "up" };
    if (dirs[e.key] && !typing) { e.preventDefault(); move(dirs[e.key]); return; }
    if ((e.key === "Escape" || (e.key === "Backspace" && !typing)) && ["movie", "album", "artist"].indexOf(route().view) >= 0) {
      e.preventDefault(); history.back(); return;
    }
    if (e.key === "MediaPlayPause" || (e.key === " " && !typing && document.activeElement === document.body)) {
      e.preventDefault(); nowPlaying.toggle();
    }
  });

  // ---------------------------------------------------------------- start
  document.querySelectorAll(".mc-menu-item").forEach(function (b) {
    b.addEventListener("click", function () { go(b.dataset.view); });
  });

  function tick() {
    const now = new Date();
    $("mc-clock").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    $("mc-date").textContent = now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
  }
  tick();
  setInterval(tick, 15000);
  window.addEventListener("hashchange", render);
  window.addEventListener("pagehide", function () { player.saveProgress(true); });
  render();
})();
