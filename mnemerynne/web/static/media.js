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
  const screenId = main.dataset.screenId ? Number(main.dataset.screenId) : null;   // this browser is a screen (the TV)
  /* The Mnemerynne screen box (deploy/screen) opens this page as ?kiosk=1 from its own start page
     (http://127.0.0.1:9187/, which also sets up Wi-Fi); remembered, since later loads come without it. */
  const KIOSK_HOME = "http://127.0.0.1:9187/";
  const kiosk = (function () {
    try {
      if (/[?&]kiosk=1\b/.test(location.search)) {
        localStorage.setItem("mc-kiosk", "1");
        history.replaceState(null, "", location.pathname + location.hash);
      }
      return localStorage.getItem("mc-kiosk") === "1";
    } catch (e) { return false; }
  })();
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
                    if (cast.active()) cast.send({ action: "play_item", file_id: p.id });
                    else if (p.type === "video") player.open(p);
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

  function shuffled(list) {
    const out = list.slice();
    for (let i = out.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); const t = out[i]; out[i] = out[j]; out[j] = t; }
    return out;
  }

  /* Play here, or on the chosen screen (casting). */
  function playItem(item, start) {
    if (cast.active()) cast.send({ action: "play_item", file_id: item.id, start: start || 0 });
    else player.open(item, start);
  }

  function playAlbum(a, index, shuffle) {
    if (cast.active()) { cast.send({ action: "play_album", key: a.key, index: index, shuffle: shuffle }); return; }
    nowPlaying.playQueue(shuffle ? shuffled(a.tracks) : a.tracks, shuffle ? 0 : index, a.art);
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

  /* The narrow browser's dropdown of the menu's views (none on a screen). */
  const viewSelect = $("mc-view-select");
  if (viewSelect) {
    document.querySelectorAll(".mc-menu-item[data-view]").forEach(function (b) {
      const option = el("option", null, b.querySelector(".mc-icon").textContent + "  " + b.lastChild.textContent.trim());
      option.value = b.dataset.view;
      viewSelect.appendChild(option);
    });
    viewSelect.addEventListener("change", function () { go(viewSelect.value); });
  }

  const SECTION_OF = { screens: "screens", cast: "screens", pair: "screens", home: "home", movies: "movies", movie: "movies", music: "music", album: "music",
                       artist: "music", pictures: "pictures", live: "live", radio: "radio" };

  async function render() {
    const r = route();
    const token = ++renderToken;
    const section = SECTION_OF[r.view] || "home";
    document.querySelectorAll(".mc-menu-item").forEach(function (b) {
      b.setAttribute("aria-current", String(b.dataset.view === section));
    });
    if (viewSelect) viewSelect.value = section;
    showError("");
    backdrop(null);
    const views = { home: viewHome, movies: viewMovies, movie: viewMovie, music: viewMusic, album: viewAlbum,
                    artist: viewArtist, pictures: viewPictures, live: viewLive, radio: viewRadio,
                    screens: viewScreens, cast: viewScreens, pair: function (code) { return viewScreens("", code); } };
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
      const r = button("▶ Resume from " + clock(m.progress.position), "", function () { playItem(m, m.progress.position); });
      r.dataset.nav = "";
      actions.appendChild(r);
    }
    const p = button(resume ? "Play from the start" : "▶ Play", resume ? "secondary" : "", function () { playItem(m, 0); });
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
    const playAll = button("▶ Play", "", function () { playAlbum(a, 0, false); });
    const shuffle = button("⤮ Shuffle", "secondary", function () { playAlbum(a, 0, true); });
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
      row.addEventListener("click", function () { playAlbum(a, i, false); });
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
      return [empty(isAdmin ? "No live streams yet. Add RTMP, HLS or YouTube sources in Access → Settings → Media."
                            : "No live streams have been set up.")];
    }
    return [shelf(null, data.streams.map(function (s) {
      if (s.kind === "youtube") {
        const sub = { live: "YouTube · live channel", playlist: "YouTube · playlist", video: "YouTube" }[s.what];
        return card({ title: s.name, sub: sub, glyph: "▶", shape: "wide", badge: s.what === "live" ? "LIVE" : "YOUTUBE",
                      live: s.what === "live", onClick: function () { if (cast.active()) cast.send({ action: "play_source", source_id: s.id }); else player.openYouTube(s); } });
      }
      return card({ title: s.name, sub: s.kind.toUpperCase(), glyph: "📡", shape: "wide", badge: "LIVE", live: true,
                    onClick: function () { if (cast.active()) cast.send({ action: "play_source", source_id: s.id }); else player.openLive(s); } });
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
      const c = card({ title: s.name, sub: sub, glyph: "📻", shape: "square", onClick: function () { if (cast.active()) cast.send({ action: "play_station", key: s.key }); else nowPlaying.playStation(s); } });
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
    yt: { time: 0, duration: 0, state: -1, volume: 100 },   // what YouTube's player last told us

    status: function (text) { $("mc-status").textContent = text || ""; },

    show: function (title) {
      this.closeYouTube();
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

    /* YouTube plays in its own embed player, with its own controls: only ✕ and Escape are ours (and Escape
       only while focus is outside YouTube's frame). */
    openYouTube: function (source) {
      this.item = null;
      this.live = true;
      this.show(source.name);
      this.detach();
      $("mc-player").classList.add("youtube");
      video.hidden = true;
      const frame = $("mc-yt");
      frame.hidden = false;
      this.yt = { time: 0, duration: 0, state: -1, volume: 100 };
      // enablejsapi: pause, seek and volume from a remote, and position reports (ytCommand, the message listener).
      frame.src = source.embed + (source.embed.indexOf("?") < 0 ? "?" : "&") + "autoplay=1&rel=0&playsinline=1"
        + "&enablejsapi=1&origin=" + encodeURIComponent(location.origin);
      frame.onload = function () {
        player.ytCommand(null);
        volume.applyYouTube();
        setTimeout(function () { volume.applyYouTube(); }, 1500);
      };
      $("mc-player-close").focus();
    },

    /* A command to YouTube's player (its postMessage API); null: ask it to start reporting. */
    ytCommand: function (func, args) {
      const frame = $("mc-yt");
      if (frame.hidden || !frame.contentWindow) return;
      const msg = func ? { event: "command", func: func, args: args || [], id: 1, channel: "widget" }
                       : { event: "listening", id: 1, channel: "widget" };
      frame.contentWindow.postMessage(JSON.stringify(msg), new URL(frame.src).origin);
    },

    youtube: function () { return $("mc-player").classList.contains("youtube"); },
    toggleAny: function () {
      if (this.youtube()) this.ytCommand(this.yt.state === 1 ? "pauseVideo" : "playVideo"); else this.toggle();
    },
    positionAny: function () { return this.youtube() ? this.yt.time : this.position(); },
    seekAny: function (t) { if (this.youtube()) this.ytCommand("seekTo", [Math.max(0, t), true]); else this.seekTo(t); },

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
      this.closeYouTube();
      $("mc-player").hidden = true;
      this.status("");
      if (route().view === "movie" || route().view === "home") render();
    },

    closeYouTube: function () {
      const frame = $("mc-yt");
      if (frame.hidden) return;
      frame.removeAttribute("src");   // stops it: nothing keeps playing behind the page
      frame.hidden = true;
      video.hidden = false;
      $("mc-player").classList.remove("youtube");
    },

    toggle: function () { if (video.paused) video.play().catch(function () {}); else video.pause(); },

    wake: function () {
      const box = $("mc-player");
      box.classList.remove("idle");
      clearTimeout(this.idleTimer);
      // Never idle over YouTube: mouse moves inside its frame don't reach this page to wake the bar again.
      this.idleTimer = setTimeout(function () {
        if (!video.paused && !box.classList.contains("youtube")) box.classList.add("idle");
      }, 3000);
    },

    key: function (e) {
      const k = e.key;
      this.wake();
      if (k === "Escape" || k === "Backspace") { this.close(); }
      else if (k === " " || k === "k" || k === "MediaPlayPause") { this.toggle(); }
      else if (k === "ArrowLeft") { this.seekTo(this.position() - 10); }
      else if (k === "ArrowRight") { this.seekTo(this.position() + 30); }
      else if (k === "ArrowUp") { volume.step(0.1); }
      else if (k === "ArrowDown") { volume.step(-0.1); }
      else if (k === "f") { this.fullscreen(); }
      else if (k === "m") { volume.toggleMute(); }
      else if (k === "n") { night.step(); }
      else return false;
      return true;
    },

    fullscreen: function () {
      if (screenId) return;
      const box = $("mc-player");
      if (document.fullscreenElement) document.exitFullscreen().catch(function () {});
      else if (box.requestFullscreen) box.requestFullscreen().catch(function () {});
      else if (video.webkitEnterFullscreen && !box.classList.contains("youtube")) video.webkitEnterFullscreen();   // iPhone: the video alone
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
  // A tap while the controls are hidden only brings them back; the next one plays or pauses.
  let wokeByTap = false;
  $("mc-player").addEventListener("pointerdown", function () {
    wokeByTap = $("mc-player").classList.contains("idle");
    player.wake();
  });
  $("mc-player").addEventListener("pointermove", function (e) { if (e.pointerType === "mouse") player.wake(); });
  video.addEventListener("click", function () {
    if (wokeByTap) { wokeByTap = false; return; }
    player.toggle();
  });
  $("mc-seek").addEventListener("change", function () { player.seekTo(Number($("mc-seek").value)); });
  $("mc-seek").addEventListener("input", function () { $("mc-pos").textContent = clock(Number($("mc-seek").value)); });
  $("mc-toggle").addEventListener("click", function () { player.toggle(); });
  $("mc-back10").addEventListener("click", function () { player.seekTo(player.position() - 10); });
  $("mc-fwd30").addEventListener("click", function () { player.seekTo(player.position() + 30); });
  $("mc-mute").addEventListener("click", function () { volume.toggleMute(); });
  $("mc-vol-down").addEventListener("click", function () { volume.step(-0.1); });
  $("mc-vol-up").addEventListener("click", function () { volume.step(0.1); });

  /* The night filter, 0 (off) to 100: kept per device, so the TV keeps its own; a screen's owner can set it
     from the phone's remote (the "night" command). */
  const night = {
    STEPS: [0, 30, 55, 80],
    level: 0,

    init: function () {
      let saved = 0;
      try { saved = Number(localStorage.getItem("mc-night")) || 0; } catch (e) { /* private window */ }
      this.set(saved, false);
      $("mc-night").addEventListener("input", function () { night.set(Number($("mc-night").value), true); });
    },

    set: function (value, keep) {
      const v = Math.max(0, Math.min(100, Math.round(value)));
      this.level = v;
      $("mc-night").value = String(v);
      $("mc-night-warm").style.opacity = String((v / 100) * 0.85);
      $("mc-night-dim").style.opacity = String((v / 100) * 0.45);
      if (keep) { try { localStorage.setItem("mc-night", String(v)); } catch (e) { /* private window */ } }
    },

    /* N, and the remote's 🌙: off → low → medium → high → off. */
    step: function () {
      const next = this.STEPS.find(function (s) { return s > night.level; });
      this.set(next === undefined ? 0 : next, true);
    },
  };
  night.init();
  $("mc-full").addEventListener("click", function () { player.fullscreen(); });
  $("mc-player-close").addEventListener("click", function () { player.close(); });

  // --------------------------------------------------------- now playing
  const audio = $("mc-audio");
  // The bottom dock's height (now playing, the remote) as --dock, so the list's padding keeps clear of it.
  const dock = $("mc-dock");
  if (dock && "ResizeObserver" in window) {
    new ResizeObserver(function () {
      main.style.setProperty("--dock", Math.ceil(dock.getBoundingClientRect().height) + "px");
    }).observe(dock);
  }
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
  $("mc-np-vol-down").addEventListener("click", function () { volume.step(-0.1); });
  $("mc-np-vol-up").addEventListener("click", function () { volume.step(0.1); });

  // ----------------------------------------------------------------- volume
  /* One volume for everything the page plays: films, live TV, music, radio, YouTube and signage clips.
     A TV has no other volume control (the kiosk keeps its sound card at full), so this is it: the
     remote's volume keys, + and -, the 🔉 🔊 buttons and M, or the phone's remote. Kept per device. */
  const volume = {
    level: 1, muted: false, osdTimer: null, ready: false,

    init: function () {
      try {
        const saved = JSON.parse(localStorage.getItem("mc-volume") || "null");
        if (saved && typeof saved.level === "number") {
          this.level = Math.max(0, Math.min(1, saved.level));
          this.muted = !!saved.muted;
        }
      } catch (e) { /* private window */ }
      this.apply(false);
      this.ready = true;      // from now on changes are reported (screenMode is defined further down)
      // Before anything else sees the key: changing the volume isn't "someone's using the TV".
      window.addEventListener("keydown", function (e) {
        if (volume.key(e)) { e.preventDefault(); e.stopImmediatePropagation(); }
      }, true);
    },

    key: function (e) {
      if (e.ctrlKey || e.altKey || e.metaKey) return false;
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      if (e.key === "AudioVolumeUp" || (!typing && (e.key === "+" || e.key === "="))) { this.step(0.05); return true; }
      if (e.key === "AudioVolumeDown" || (!typing && e.key === "-")) { this.step(-0.05); return true; }
      if (e.key === "AudioVolumeMute") { this.toggleMute(); return true; }
      return false;
    },

    set: function (level, show) {
      this.level = Math.round(Math.max(0, Math.min(1, level)) * 20) / 20;
      this.muted = false;
      this.apply(show !== false);
    },

    step: function (by) { this.set(this.level + by); },

    toggleMute: function () {
      this.muted = !this.muted;
      this.apply(true);
    },

    /* A signage clip set to play silently stays silent (data-silent). */
    media: function (m) {
      m.volume = this.level;
      m.muted = this.muted || m.dataset.silent === "1";
    },

    applyYouTube: function () {
      player.ytCommand("setVolume", [Math.round(this.level * 100)]);
      player.ytCommand(this.muted ? "mute" : "unMute", []);
      player.yt.volume = this.level * 100;
    },

    apply: function (show) {
      [video, audio].concat(Array.from(document.querySelectorAll(".mc-signage video"))).forEach(function (m) {
        volume.media(m);
      });
      this.applyYouTube();
      const icon = this.muted || this.level === 0 ? "🔇" : "🔊";
      $("mc-mute").textContent = icon;
      try { localStorage.setItem("mc-volume", JSON.stringify({ level: this.level, muted: this.muted })); }
      catch (e) { /* private window */ }
      if (show) this.osd();
      if (screenId && this.ready) screenMode.report(true);
    },

    /* The level for a moment, top right, over whatever is showing. */
    osd: function () {
      let box = $("mc-volume");
      if (!box) {
        box = el("div", "mc-volume");
        box.id = "mc-volume";
        box.setAttribute("role", "status");
        box.append(el("span", "mc-volume-icon"), el("span", "mc-volume-bar"), el("span", "mc-volume-pct"));
        box.children[1].appendChild(el("span"));
        document.body.appendChild(box);
      }
      const pct = Math.round(this.level * 100);
      box.children[0].textContent = this.muted || pct === 0 ? "🔇" : pct < 50 ? "🔉" : "🔊";
      box.children[1].firstChild.style.width = (this.muted ? 0 : pct) + "%";
      box.children[2].textContent = this.muted ? "Muted" : pct + "%";
      box.classList.add("on");
      clearTimeout(this.osdTimer);
      this.osdTimer = setTimeout(function () { box.classList.remove("on"); }, 1600);
    },
  };
  volume.init();
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
  $("mc-viewer").addEventListener("pointermove", function (e) { if (e.pointerType === "mouse") viewer.wake(); });
  $("mc-viewer").addEventListener("pointerdown", function () { viewer.wake(); });

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

  // ------------------------------------------------------------- casting
  /* This device (the phone) sends what to play to one of its owner's screens (the TV); the choice is
     kept in this browser. With a screen chosen, every Play on the page goes there instead. */
  const cast = {
    target: null, screens: [], allowed: true, admin: false, timer: null, followTimer: null,

    init: function () {
      if (screenId || !$("mc-cast")) return;   // a screen itself, or not in the screens group
      try { this.target = Number(localStorage.getItem("mc-cast")) || null; } catch (e) { this.target = null; }
      $("mc-cast").addEventListener("change", function () { cast.choose(Number($("mc-cast").value) || null); });
      this.refresh();
    },

    active: function () { return !screenId && this.target !== null; },

    choose: function (id) {
      this.target = id;
      try { if (id) localStorage.setItem("mc-cast", String(id)); else localStorage.removeItem("mc-cast"); } catch (e) { /* private window */ }
      this.paint();
      this.poll();
    },

    screen: function () {
      const id = this.target;
      return this.screens.find(function (s) { return s.id === id; }) || null;
    },

    refresh: async function () {
      let data;
      try { data = await api("GET", "/api/media/screens"); } catch (e) { return; }
      this.screens = data.screens;
      this.allowed = data.allowed;
      this.admin = !!data.admin;
      if (this.target !== null && !this.screen()) this.choose(null);
      this.paint();
      if ((route().view === "screens" || route().view === "pair") && !document.activeElement.closest("#mc-content input, #mc-content select")) {
        paintScreens();
      }
    },

    /* The "Play on" menu and the remote bar. */
    paint: function () {
      const select = $("mc-cast");
      const others = this.screens.filter(function (s) { return !s.this; });
      $("mc-cast-wrap").hidden = !others.length || !this.allowed;
      const keep = this.target;
      select.replaceChildren(el("option", null, "This device"));
      select.firstChild.value = "";
      others.forEach(function (s) {
        const o = el("option", null, label(s) + (s.online ? "" : " (off)"));
        o.value = String(s.id);
        select.appendChild(o);
      });
      select.value = keep ? String(keep) : "";
      const s = this.screen();
      $("mc-remote").hidden = !s;
      if (!s) return;
      const st = s.state;
      $("mc-remote-title").textContent = "📺 " + label(s) + (s.online ? "" : " — not connected");
      $("mc-remote-sub").textContent = !s.online ? "Open Media on it (Screens → that screen's address)."
        : !st || st.kind === "idle" ? "Nothing playing"
        : [st.title, st.sub, st.duration ? clock(st.position) + " / " + clock(st.duration) : ""].filter(Boolean).join(" · ")
          + (st.paused ? " · paused" : "");
      const bar = $("mc-remote-buttons");
      if (bar.childElementCount) return;
      [["⏮", "Previous", { action: "prev" }], ["⟲", "Back 10 s", { action: "skip", by: -10 }],
       ["⏯", "Play / pause", { action: "toggle" }], ["⟳", "Forward 30 s", { action: "skip", by: 30 }],
       ["⏭", "Next", { action: "next" }], ["🔉", "Quieter", "down"], ["🔊", "Louder", "up"],
       ["🔇", "Mute / unmute", { action: "mute" }],
       ["🌙", "Night filter: off, low, medium, high", "night"],
       ["⏹", "Stop", { action: "stop" }], ["✕", "Play on this device again", "leave"]].forEach(function (b) {
        bar.appendChild(button(b[0], "", function () {
          if (b[2] === "leave") { cast.choose(null); return; }
          if (b[2] === "night") { cast.send({ action: "night" }); return; }
          if (b[2] === "up" || b[2] === "down") {
            const now = (cast.screen() && cast.screen().state && cast.screen().state.volume) || 0.5;
            cast.send({ action: "volume", level: Math.max(0, Math.min(1, now + (b[2] === "up" ? 0.1 : -0.1))) });
            return;
          }
          cast.send(b[2]);
        }, b[1]));
      });
    },

    /* While a screen is chosen, its state every 2 s (what the remote bar shows). */
    poll: function () {
      clearInterval(this.timer);
      this.timer = this.target ? setInterval(function () { if (!document.hidden) cast.refresh(); }, 2000) : null;
    },

    send: async function (command, screen) {
      const id = screen || this.target;
      showError("");
      try {
        const sent = await api("POST", "/api/media/screens/" + id + "/command", command);
        if (sent && sent.id) this.follow(id, sent.id);
        setTimeout(function () { cast.refresh(); }, 800);
        return true;
      } catch (err) { showError(err.message); return false; }
    },

    /* Someone using the TV (a screen box on another page) has a few seconds to keep it: say so here, with
       Override for an admin, until it's done or kept. */
    follow: function (screen, commandId) {
      clearTimeout(this.followTimer);
      const s = this.screens.find(function (x) { return x.id === screen; });
      const name = s ? s.name : "The screen";
      const url = "/api/media/screens/" + screen + "/commands/" + encodeURIComponent(commandId);
      const giveUp = Date.now() + 25000;
      let until = null;
      const check = async function () {
        let a;
        try { a = await api("GET", url); } catch (e) { cast.note(""); return; }
        if (a.state === "done") { cast.note(""); return; }
        if (a.state === "kept") { cast.note(name + ": the person using it kept it."); cast.followTimer = setTimeout(function () { cast.note(""); }, 6000); return; }
        if (a.state === "waiting") {
          if (until === null) until = Date.now() + (a.seconds || 0) * 1000;
          const secs = Math.max(0, Math.ceil((until - Date.now()) / 1000));
          cast.note("Someone is using " + name + ". It switches in " + secs + " s unless they keep it.",
                    cast.admin ? async function () {
                      try { await api("POST", url + "/override", {}); } catch (err) { showError(err.message); }
                    } : null);
        }
        if (Date.now() < giveUp) cast.followTimer = setTimeout(check, 700); else cast.note("");
      };
      this.followTimer = setTimeout(check, 400);
    },

    note: function (text, override) {
      let box = $("mc-takeover");
      if (!box) {
        box = el("p", "mc-takeover-note");
        box.id = "mc-takeover";
        box.setAttribute("role", "status");
        $("mc-dock").prepend(box);          // with the remote, where it's seen wherever the page is scrolled
      }
      box.replaceChildren();
      box.hidden = !text;
      if (!text) return;
      box.appendChild(el("span", null, text));
      if (override) box.appendChild(button("Override", "small", override, "Take over the TV now (admins)"));
    },
  };

  /* A screen in the lists: an admin's view of someone else's names its owner. */
  function label(s) { return s.name + (s.owner ? " (" + s.owner + ")" : ""); }

  let calibrating = null;   // the screen whose size is being adjusted from here (the phone)

  function paintScreens() {
    const list = $("mc-screen-list");
    if (!list) return;
    list.replaceChildren();
    if (!cast.screens.length) {
      list.appendChild(empty("No screens yet. Open /media/screen on the TV and pair it with the code it shows (below)."));
      return;
    }
    cast.screens.forEach(function (s) {
      const row = el("div", "mc-screen");
      const name = el("div", "mc-screen-name");
      const dot = el("span", "mc-dot" + (s.online ? " on" : ""));
      name.append(dot, document.createTextNode(label(s) + (s.this ? " (this browser)" : "")));
      const st = s.state;
      const what = el("div", "mc-sub", !s.online ? "Not connected" + (s.last_seen ? " · last seen " + s.last_seen + " UTC" : "")
        : !st || st.kind === "idle" ? "Ready, nothing playing" : st.title + (st.paused ? " (paused)" : ""));
      const actions = el("div", "mc-actions");
      if (!cast.allowed) {
        // Out of the screens group: nothing but removing it.
      } else if (!s.this) {
        if (s.online) {
          const size = button("Screen size", "link small", function () {
            calibrating = calibrating === s.id ? null : s.id;
            if (calibrating) cast.send({ action: "calibrate", step: "open" }, s.id);
            paintScreens();
          }, "Fit the picture to a TV that cuts off its edges");
          size.dataset.nav = "";
          actions.appendChild(size);
        }
        const pick = button(cast.target === s.id ? "✓ Playing here" : "Play on this screen",
                            cast.target === s.id ? "" : "secondary", function () { cast.choose(cast.target === s.id ? null : s.id); paintScreens(); });
        pick.dataset.nav = "";
        actions.appendChild(pick);
      } else {
        const open = button("Open screen mode", "secondary", function () { location.href = "/media/screen"; });
        open.dataset.nav = "";
        actions.appendChild(open);
      }
      const drop = button("Remove", "link small danger", async function () {
        if (!confirm("Remove the screen " + s.name + "? It stops taking casts until it's paired again.")) return;
        try { await api("DELETE", "/api/media/screens/" + s.id); } catch (err) { showError(err.message); }
        cast.refresh();
      });
      if (!s.owner) actions.appendChild(drop);      // only its owner removes a screen
      row.append(name, what, actions);
      list.appendChild(row);
      if (calibrating === s.id && s.online) {
        const pad = el("div", "mc-cal-remote");
        pad.appendChild(el("span", "mc-sub", "On the TV: " + (s.safe ? "sides " + s.safe.x + "% · top and bottom " + s.safe.y + "% saved" : "")));
        [["Narrower", "narrower"], ["Wider", "wider"], ["Shorter", "shorter"], ["Taller", "taller"], ["Reset", "reset"],
         ["Save", "save"], ["Cancel", "cancel"]].forEach(function (b) {
          const btn = button(b[0], b[1] === "save" ? "" : "secondary small", function () {
            cast.send({ action: "calibrate", step: b[1] }, s.id);
            if (b[1] === "save" || b[1] === "cancel") { calibrating = null; paintScreens(); }
          });
          btn.dataset.nav = "";
          pad.appendChild(btn);
        });
        list.appendChild(pad);
      }
    });
  }

  /* arg: a YouTube link to fill in (#cast/<link>, from the Share menu); code: a TV's pairing code
     (#pair/<code>, from its QR code). */
  async function viewScreens(arg, code) {
    setHead("Screens");
    await cast.refresh();
    const out = [];
    if (!cast.allowed) {
      out.push(empty("Screens (playing on a TV from your phone) are only for members of the group an admin chose. "
                     + "Ask an admin to add you.", "Not available."));
      if (cast.screens.length) {
        const own = el("div", "mc-screen-list");
        own.id = "mc-screen-list";
        out.push(own);
        setTimeout(paintScreens, 0);
      }
      return out;
    }

    const paste = el("form", "mc-paste");
    paste.appendChild(el("h2", null, "Play a YouTube link on a screen"));
    const link = el("input");
    link.type = "url";
    link.placeholder = "Paste a YouTube link (video, playlist, or a channel's live stream)";
    link.setAttribute("aria-label", "YouTube link");
    link.value = arg ? decodeURIComponent(arg) : "";
    const target = el("select");
    target.setAttribute("aria-label", "Screen");
    cast.screens.filter(function (s) { return !s.this; }).forEach(function (s) {
      const o = el("option", null, label(s) + (s.online ? "" : " (off)"));
      o.value = String(s.id);
      target.appendChild(o);
    });
    if (cast.target) target.value = String(cast.target);
    const playBtn = el("button", null, "▶ Play");
    playBtn.type = "submit";
    const pasteBtn = button("Paste", "secondary", async function () {
      try { link.value = (await navigator.clipboard.readText()).trim(); } catch (e) { link.focus(); }
    }, "Paste from the clipboard");
    const row = el("div", "mc-paste-row");
    row.append(link, pasteBtn, target, playBtn);
    paste.appendChild(row);
    paste.addEventListener("submit", async function (e) {
      e.preventDefault();
      if (!target.value) { showError("Pair a screen first (below)."); return; }
      if (await cast.send({ action: "play_youtube", url: link.value.trim() }, Number(target.value))) {
        link.value = "";
        cast.choose(Number(target.value));
      }
    });
    if (cast.screens.some(function (s) { return !s.this; })) out.push(paste);

    /* A TV showing a code (/media/screen while unpaired): name it here and it pairs itself. */
    const claim = el("form", "mc-paste");
    claim.appendChild(el("h2", null, "Pair a TV"));
    claim.appendChild(el("p", "mc-note", "Open /media/screen on the TV (the Erynne screen app does it by " +
      "itself). Type the code it shows, or scan its QR code, and name it."));
    const codeIn = el("input");
    codeIn.placeholder = "Code, e.g. BCDF-GHJK";
    codeIn.setAttribute("aria-label", "Code on the TV");
    codeIn.autocapitalize = "characters";
    codeIn.autocomplete = "off";
    codeIn.spellcheck = false;
    codeIn.value = code ? decodeURIComponent(code) : "";
    const tvName = el("input");
    tvName.placeholder = "Living room";
    tvName.setAttribute("aria-label", "Screen name");
    const claimBtn = el("button", null, "Pair");
    claimBtn.type = "submit";
    const crow = el("div", "mc-paste-row");
    crow.append(codeIn, tvName, claimBtn);
    claim.appendChild(crow);
    claim.addEventListener("submit", async function (e) {
      e.preventDefault();
      try {
        const r = await api("POST", "/api/media/screens/claim", { code: codeIn.value, name: tvName.value });
        codeIn.value = tvName.value = "";
        await cast.refresh();
        cast.choose(r.id);
        if (route().view === "pair") go("screens"); else paintScreens();
      } catch (err) { showError(err.message); }
    });
    out.push(claim);
    if (code && !window.Erynne.coarse) setTimeout(function () { tvName.focus(); }, 0);

    const listBox = el("section", "mc-shelf");
    listBox.appendChild(el("h2", null, "Your screens"));
    const list = el("div", "mc-screen-list");
    list.id = "mc-screen-list";
    listBox.appendChild(list);
    out.push(listBox);

    const pair = el("form", "mc-paste");
    pair.appendChild(el("h2", null, "Use this browser as a screen"));
    pair.appendChild(el("p", "mc-note", "On the TV: sign in, come here, name it and press Pair. It then opens " +
      "Media full-screen and waits for what you cast to it; it stays paired after sign-in runs out."));
    const name = el("input");
    name.placeholder = "Living room";
    name.setAttribute("aria-label", "Screen name");
    const pairBtn = el("button", null, "Pair");
    pairBtn.type = "submit";
    const prow = el("div", "mc-paste-row");
    prow.append(name, pairBtn);
    pair.appendChild(prow);
    pair.addEventListener("submit", async function (e) {
      e.preventDefault();
      try {
        const r = await api("POST", "/api/media/screens", { name: name.value });
        location.href = r.url;
      } catch (err) { showError(err.message); }
    });
    out.push(pair);
    setTimeout(paintScreens, 0);
    return out;
  }

  // -------------------------------------------------------- screen (TV)
  /* This browser is a screen: take commands from the server's stream, act on them, and say what is
     playing every few seconds. Browsers only play sound after someone has clicked on the page, unless
     the site is allowed to autoplay (see the help): the splash's Start button is that click. */
  const screenMode = {
    es: null, lastReport: "", lostTimer: null,

    start: function () {
      document.body.classList.add("mc-screen-mode");
      const splash = $("mc-splash");
      const policy = navigator.getAutoplayPolicy ? navigator.getAutoplayPolicy("mediaelement") : "unknown";
      const startBtn = $("mc-splash-start");
      if (policy === "allowed") startBtn.hidden = true;
      startBtn.addEventListener("click", function () {
        startBtn.hidden = true;
        if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(function () {});
      });
      splash.addEventListener("click", function (e) { if (e.target !== startBtn && startBtn.hidden) splash.hidden = true; });
      window.addEventListener("keydown", function (e) {
        if (splash.hidden || e.ctrlKey || e.altKey || e.metaKey || signage.running) return;   // a slideshow first
        e.preventDefault();
        e.stopImmediatePropagation();
        startBtn.hidden = true;
        splash.hidden = true;
        focusFirst();
      }, true);
      safeArea.init();
      signage.init();
      this.connect();
      setInterval(function () { screenMode.report(false); }, 3000);
      ["play", "pause", "ended", "loadedmetadata"].forEach(function (ev) {
        video.addEventListener(ev, function () { screenMode.report(true); });
        audio.addEventListener(ev, function () { screenMode.report(true); });
      });
    },

    connect: function () {
      const state = $("mc-splash-state");
      this.es = new EventSource("/media/screen/stream");
      this.es.addEventListener("hello", function () {
        state.textContent = "Ready for casting";
        screenMode.report(true);
        screenMode.handOver();
      });
      this.es.addEventListener("cmd", function (e) {
        let cmd;
        try { cmd = JSON.parse(e.data); } catch (err) { return; }
        screenMode.handle(cmd);
      });
      this.es.onerror = function () {
        if (screenMode.es.readyState === EventSource.CLOSED) {
          state.textContent = "This screen was removed. Showing a code to pair it again…";
          $("mc-splash").hidden = false;
          screenMode.reloadWhenUp(5000);                     // unpaired, it shows the pairing code
        } else {
          state.textContent = "Reconnecting…";
          if (kiosk && !screenMode.lostTimer) {
            screenMode.lostTimer = setTimeout(function () { location.replace(KIOSK_HOME); }, 120000);
          }
        }
      };
      this.es.addEventListener("open", function () { clearTimeout(screenMode.lostTimer); screenMode.lostTimer = null; });
    },

    /* Reload once the server answers: a kiosk left on the browser's error page would never come back. */
    reloadWhenUp: function (wait) {
      setTimeout(function () {
        fetch("/healthz", { cache: "no-store" }).then(function (r) {
          if (r.ok) location.replace("/media/screen"); else screenMode.reloadWhenUp(10000);
        }, function () { if (kiosk) location.replace(KIOSK_HOME); else screenMode.reloadWhenUp(10000); });
      }, wait);
    },

    /* A command the box took on another page (screen_link.js), after its countdown: carried out here, once. */
    handOver: function () {
      let cmd = null;
      try {
        cmd = JSON.parse(sessionStorage.getItem("mc-takeover") || "null");
        sessionStorage.removeItem("mc-takeover");
      } catch (e) { cmd = null; }
      if (cmd && typeof cmd.action === "string") this.handle(cmd);
    },

    handle: function (cmd) {
      const a = cmd.action;
      if (a === "override") return;      // for a countdown on another page (screen_link.js); none here
      if (cmd.id && a !== "unpaired") {  // the phone asks how it went (web/media.py command_answer)
        api("POST", "/media/screen/commands/" + encodeURIComponent(cmd.id), { state: "done" }).catch(function () {});
      }
      if (a === "unpaired") { this.es.close(); this.es.onerror(); return; }
      if (a === "signage") { signage.load(); return; }
      if (a === "identify") { signage.identify(); return; }
      if (a === "reload") { location.reload(); return; }
      if (a === "signage_stop") { signage.pause(true); return; }
      if (a === "signage_start") { signage.pause(false); return; }
      if (a === "calibrate") { safeArea.step(cmd.step); return; }
      if (a.indexOf("play_") === 0 || a === "stop") signage.activity(false);
      if (a.indexOf("play_") === 0) {
        $("mc-splash").hidden = true;
        viewer.close();
        if (!$("mc-player").hidden) player.close();
      }
      if (a === "play_item") {
        const item = cmd.item;
        if (item.type === "audio") nowPlaying.playQueue([item], 0, item.art);
        else if (item.type === "image") viewer.open([item], 0);
        else player.open(item, cmd.start || 0);
      } else if (a === "play_album") {
        let tracks = cmd.album.tracks, index = cmd.index;
        if (cmd.shuffle) { tracks = shuffled(tracks); index = 0; }
        nowPlaying.playQueue(tracks, index, cmd.album.art);
      } else if (a === "play_youtube") {
        player.openYouTube(cmd.source);
      } else if (a === "play_source") {
        if (cmd.source.kind === "youtube") player.openYouTube(cmd.source); else player.openLive(cmd.source);
      } else if (a === "play_station") {
        nowPlaying.playStation(cmd.station);
      } else if (a === "toggle") {
        if (!$("mc-player").hidden) player.toggleAny();
        else if (!$("mc-viewer").hidden) viewer.slideshow();
        else nowPlaying.toggle();
      } else if (a === "skip" || a === "seek") {
        const playerOpen = !$("mc-player").hidden;
        const base = a === "skip" ? (playerOpen ? player.positionAny() : nowPlaying.position()) : 0;
        const to = a === "skip" ? base + cmd.by : cmd.to;
        if (playerOpen) player.seekAny(to); else if (!$("mc-viewer").hidden) viewer.step(cmd.by > 0 ? 1 : -1);
        else nowPlaying.seekTo(to);
      } else if (a === "volume") {
        volume.set(cmd.level);
      } else if (a === "mute") {
        volume.toggleMute();
      } else if (a === "next" || a === "prev") {
        if (!$("mc-viewer").hidden) viewer.step(a === "next" ? 1 : -1);
        else if (a === "next") nowPlaying.next(); else nowPlaying.prev();
      } else if (a === "night") {
        if (typeof cmd.level === "number") night.set(cmd.level * 100, true); else night.step();
      } else if (a === "stop") {
        player.close();
        nowPlaying.stop();
        viewer.close();
      }
      setTimeout(function () { screenMode.report(true); }, 300);
    },

    state: function () {
      if (safeArea.isOpen()) {
        return { kind: "calibrating", title: "Adjusting the screen size", sub: safeArea.label() };
      }
      if (signage.running) return signage.state();
      if (!$("mc-player").hidden) {
        if ($("mc-player").classList.contains("youtube")) {
          return { kind: "youtube", title: $("mc-player-title").textContent, position: player.yt.time,
                   duration: player.yt.duration || null, paused: player.yt.state !== 1, volume: player.yt.volume / 100 };
        }
        return { kind: player.live ? "live" : "video", title: $("mc-player-title").textContent,
                 position: player.live ? null : player.position(), duration: player.duration || null,
                 paused: video.paused, volume: video.volume };
      }
      if (!$("mc-np").hidden) {
        return { kind: nowPlaying.station ? "radio" : "audio", title: $("mc-np-title").textContent,
                 sub: $("mc-np-sub").textContent, position: nowPlaying.station ? null : nowPlaying.position(),
                 duration: nowPlaying.duration || null, paused: audio.paused, volume: audio.volume,
                 has_queue: nowPlaying.queue.length > 1 };
      }
      if (!$("mc-viewer").hidden) return { kind: "pictures", title: $("mc-viewer-title").textContent };
      return { kind: "idle" };
    },

    report: function (force) {
      const state = this.state();
      state.night = night.level / 100;
      state.volume = volume.level;
      state.muted = volume.muted;
      state.version = window.SignageRender ? window.SignageRender.VERSION : null;
      state.errors = signage.errors.slice();
      const key = JSON.stringify(state);
      // Unchanged and idle: a heartbeat every 30 s keeps the screen "online" on Access → Signage.
      const stale = Date.now() - (this.lastSent || 0) > 30000;
      if (!force && key === this.lastReport && state.kind === "idle" && !stale) return;
      this.lastReport = key;
      this.lastSent = Date.now();
      api("PUT", "/api/media/screen/state", state).catch(function () {});
    },
  };

  // ------------------------------------------------- underscan (the TV, its safe area)
  /* For TVs that cut off the picture's edges (overscan): everything is drawn inside a frame this far in,
     in percent of the width (x, both sides) and height (y, top and bottom), set on the calibration screen
     (Screen size in the menu, or from the phone) and kept with the screen on the server. */
  const safeArea = {
    x: 0, y: 0, before: null, box: null, fromSettings: false,

    init: function () {
      this.x = Number(main.dataset.safeX) || 0;
      this.y = Number(main.dataset.safeY) || 0;
      this.apply();
      window.addEventListener("keydown", function (e) {
        if (safeArea.isOpen() && safeArea.key(e)) { e.preventDefault(); e.stopImmediatePropagation(); }
      }, true);
      // Screen size and Wi-Fi are in the box's Kiosk settings, behind its unlock: Screen size comes back here
      // as ?calibrate=1, and goes back to the settings when saved or cancelled. The phone can still open it.
      if (kiosk && /[?&]calibrate=1\b/.test(location.search)) {
        this.fromSettings = true;
        history.replaceState(null, "", location.pathname + location.hash);
        setTimeout(function () { safeArea.open(); }, 0);
      }
    },

    isOpen: function () { return !!this.box && !this.box.hidden; },
    label: function () { return "sides " + this.x.toFixed(1) + "% · top and bottom " + this.y.toFixed(1) + "%"; },

    apply: function () {
      // % for the fixed layers (of the viewport itself); vw and vh for the page's margins.
      const root = document.documentElement.style;
      root.setProperty("--safe-x", this.x + "%");
      root.setProperty("--safe-y", this.y + "%");
      root.setProperty("--safe-xv", this.x + "vw");
      root.setProperty("--safe-yv", this.y + "vh");
      if (this.box) $("mc-cal-values").textContent = this.label();
    },

    adjust: function (dx, dy) {
      const step = function (v) { return Math.round(Math.max(0, Math.min(10, v)) * 2) / 2; };
      this.x = step(this.x + dx);
      this.y = step(this.y + dy);
      this.apply();
      screenMode.report(true);
    },

    build: function () {
      const box = el("div", "mc-cal");
      box.id = "mc-cal";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-label", "Screen size");
      const frame = el("div", "mc-cal-frame");
      ["tl", "tr", "bl", "br"].forEach(function (c, i) { frame.appendChild(el("span", "mc-cal-corner " + c, "◤◥◣◢"[i])); });
      const help = el("div", "mc-cal-help");
      help.append(el("h2", null, "Screen size"),
        el("p", null, "Adjust until you can see the whole pink frame and the arrows in all four corners."),
        el("p", "mc-cal-keys", "← → narrower / wider · ↑ ↓ taller / shorter · 0 resets · Enter saves · Esc cancels"));
      const values = el("p", "mc-cal-values");
      values.id = "mc-cal-values";
      const row = el("div", "mc-cal-buttons");
      [["Narrower", "narrower"], ["Wider", "wider"], ["Shorter", "shorter"], ["Taller", "taller"], ["Reset", "reset"],
       ["Save", "save"], ["Cancel", "cancel"]].forEach(function (b) {
        row.appendChild(button(b[0], b[1] === "save" ? "" : "secondary", function () { safeArea.step(b[1]); }));
      });
      help.append(values, row);
      frame.appendChild(help);
      box.appendChild(frame);
      document.body.appendChild(box);
      return box;
    },

    open: function () {
      if (this.isOpen()) return;
      this.before = { x: this.x, y: this.y };
      this.box = this.box || this.build();
      this.box.hidden = false;
      $("mc-splash").hidden = true;
      signage.activity(false);
      this.apply();
      screenMode.report(true);
    },

    close: function () {
      if (this.box) this.box.hidden = true;
      screenMode.report(true);
      if (this.fromSettings) { location.href = KIOSK_HOME + "settings"; return; }
      focusFirst();
    },

    save: async function () {
      try {
        const r = await api("PUT", "/media/screen/safe-area", { x: this.x, y: this.y });
        this.x = r.x;
        this.y = r.y;
        this.apply();
        this.close();
      } catch (err) { $("mc-cal-values").textContent = "Couldn't save: " + err.message; }
    },

    step: function (name) {
      if (name === "open") { this.open(); return; }
      if (!this.isOpen()) this.open();
      if (name === "wider") this.adjust(-0.5, 0);
      else if (name === "narrower") this.adjust(0.5, 0);
      else if (name === "taller") this.adjust(0, -0.5);
      else if (name === "shorter") this.adjust(0, 0.5);
      else if (name === "reset") this.adjust(-this.x, -this.y);
      else if (name === "save") this.save();
      else if (name === "cancel") { this.x = this.before.x; this.y = this.before.y; this.apply(); this.close(); }
    },

    key: function (e) {
      const steps = { ArrowLeft: "narrower", ArrowRight: "wider", ArrowUp: "taller", ArrowDown: "shorter", "0": "reset",
                      Enter: "save", Escape: "cancel", Backspace: "cancel" };
      if (e.key === "Enter" && document.activeElement && this.box.contains(document.activeElement)) return false;  // a focused button
      if (!steps[e.key]) return false;
      this.step(steps[e.key]);
      return true;
    },
  };

  // ------------------------------------------------------ signage (the TV, idle)
  /* What the screen shows while nobody uses it (Access → Signage). Its programme is the ranked list of
     schedule entries for this screen (its own, its group's, everyone's); the first that holds by this
     screen's own clock plays (signage_render.js matches). An "idle" entry starts once nothing has played
     and no key was pressed for idle_minutes, and any key or cast stops it; an "always" one runs whenever
     nothing cast is playing, and keys don't stop it. Slides with their own days or hours are skipped
     while they don't hold. The drawing (two crossfading layers, transitions, fit, CRT effects) is
     SignageRender's, shared with the admin page's preview. */
  const signage = {
    config: null, running: null, items: [], index: 0, zone: null, paused: false, lastActivity: Date.now(), errors: [],

    init: function () {
      const self = this;
      ["keydown", "pointerdown", "wheel"].forEach(function (ev) {
        window.addEventListener(ev, function (e) {
          const swallow = self.running && self.running.entry.mode === "idle";
          self.activity(true);
          if (swallow) { e.preventDefault(); e.stopImmediatePropagation(); }   // the key that woke it does nothing else
        }, true);
      });
      this.load();
      setInterval(function () { self.check(); }, 15000);
      setInterval(function () { self.load(); }, 300000);   // new pictures in the folders, new announcements
    },

    load: async function () {
      try { this.config = await api("GET", "/media/screen/signage"); } catch (e) { return; }
      this.stop();          // with the new slides, if its entry still says so (check)
      this.check();
    },

    /* The programme entry that holds now: the list came ranked, so the first match wins. */
    entry: function () {
      const c = this.config;
      if (!c) return null;
      const now = new Date();
      return c.programme.find(function (e) { return c.shows[e.show_id] && window.SignageRender.matches(e, now); }) || null;
    },

    busy: function () {
      return !$("mc-player").hidden || !$("mc-viewer").hidden || !audio.paused || safeArea.isOpen();
    },

    activity: function (fromKey) {
      this.lastActivity = Date.now();
      if (this.running && (!fromKey || this.running.entry.mode === "idle")) this.stop();
    },

    check: function () {
      const e = this.entry();
      if (!e || this.paused) { this.stop(); return; }
      if (this.running) {
        if (this.running.entry.show_id !== e.show_id || this.running.entry.mode !== e.mode) { this.stop(); this.start(e); }
        return;
      }
      if (this.busy()) return;
      if (e.mode === "always" || Date.now() - this.lastActivity >= this.config.idle_minutes * 60000) this.start(e);
    },

    /* An admin's Stop/Start from Access → Signage: stopped, nothing plays until started again (or reloaded). */
    pause: function (on) {
      this.paused = on;
      if (on) this.stop(); else { this.lastActivity = 0; this.check(); }
    },

    box: function () {
      let box = $("mc-signage");
      if (!box) {
        box = el("div", "mc-signage");
        box.id = "mc-signage";
        box.hidden = true;
        box.setAttribute("aria-hidden", "true");
        document.body.appendChild(box);
        this.zone = new window.SignageRender.Zone(box, { volume: function (v) { volume.media(v); } });
      }
      return box;
    },

    /* The playlist's slides that hold now (a slide's own days, hours and dates). */
    playable: function (show) {
      const now = new Date();
      return show.items.filter(function (i) { return !i.when || window.SignageRender.matches(i.when, now); });
    },

    start: function (e) {
      const show = this.config.shows[e.show_id];
      const items = show ? this.playable(show) : [];
      if (!items.length) return;
      this.items = show.shuffle ? shuffled(items) : items;
      this.index = 0;
      this.running = { entry: e, show: show, item: null };
      const box = this.box();
      window.SignageRender.applyEffects(box, e.effects);
      box.hidden = false;
      document.documentElement.classList.add("mc-sg-running");
      window.SignageRender.power(box, true, e.effects);
      this.show();
      screenMode.report(true);
    },

    stop: function () {
      if (!this.running) return;
      this.running = null;
      const box = this.box();
      this.zone.stop();
      box.hidden = true;
      document.documentElement.classList.remove("mc-sg-running");
      screenMode.report(true);
    },

    /* The next slide that holds now, on the hidden layer; SignageRender brings it over when it can be seen. */
    show: function () {
      if (!this.running) return;
      const self = this;
      let item = null;
      for (let tries = 0; tries < this.items.length && !item; tries++) {
        const cand = this.items[this.index % this.items.length];
        if (!cand.when || window.SignageRender.matches(cand.when)) item = cand; else self.index += 1;
      }
      if (!item) { this.stop(); return; }   // nothing holds right now: check() starts it again when something does
      this.running.item = item;
      this.zone.play(item, this.running.show, function (skipped) {
        if (skipped) self.noteError(item);
        self.index = (self.index + 1) % self.items.length;
        self.show();
      });
      screenMode.report(true);
    },

    noteError: function (item) {
      const what = (item.title || item.type) + (item.src ? " (" + item.src.split("?")[0] + ")" : "");
      if (this.errors[this.errors.length - 1] !== what) this.errors.push(what);
      this.errors = this.errors.slice(-10);
    },

    state: function () {
      const item = this.running.item || {};
      return { kind: "signage", title: "Playlist: " + this.running.show.name, sub: item.title || "" };
    },

    /* The admin's Identify: this screen's name over everything for a few seconds. */
    identify: function () {
      const s = this.config && this.config.screen;
      window.SignageRender.identify(s ? s.name : (main.dataset.screenName || "This screen"),
                                    s && s.orientation === "portrait" ? "portrait" : "");
    },
  };

  // ---------------------------------------------------------------- start
  window.addEventListener("message", function (e) {
    // YouTube's player reporting (after player.ytCommand(null)): position, length, playing or not, volume.
    if (!/^https:\/\/www\.youtube(-nocookie)?\.com$/.test(e.origin) || typeof e.data !== "string") return;
    let msg;
    try { msg = JSON.parse(e.data); } catch (err) { return; }
    const info = msg && msg.info;
    if (!info || typeof info !== "object") return;
    if (typeof info.currentTime === "number") player.yt.time = info.currentTime;
    if (typeof info.duration === "number") player.yt.duration = info.duration;
    if (typeof info.playerState === "number") player.yt.state = info.playerState;
    if (typeof info.volume === "number") player.yt.volume = info.volume;
  });
  document.querySelectorAll(".mc-menu-item[data-view]").forEach(function (b) {
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
  cast.init();
  if (screenId) screenMode.start();
  render();
})();
