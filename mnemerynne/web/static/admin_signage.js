/* Access → Signage (mnemerynne/media/signage.py): on the left the content (the Signage folder and the
   Media library's pictures and clips, with thumbnails, upload and new folders), on the right the
   playlists, the schedule, the screens and the settings. Playlist and screen edits are drafts until
   Save; the preview plays a playlist with the TV's own renderer (signage_render.js). All text goes
   through textContent. */
(function () {
  "use strict";

  const { el, api, form, toast, sortable, treeNode, coarse } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const errorNode = $("admin-error");
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const KINDS = { folder: "Pictures and clips from a folder", image: "A picture", video: "A video", pdf: "A PDF, a page a slide",
                  text: "Text", web: "A web page", youtube: "A YouTube video", announcements: "Announcements for everyone" };
  const ADDABLE = ["folder", "text", "web", "youtube", "announcements"];
  const TRANSITIONS = [["fade", "Fade"], ["cut", "Cut"], ["slide", "Slide"], ["zoom", "Zoom"]];
  const FITS = [["contain", "Whole picture"], ["cover", "Fill the screen"], ["blur", "Whole picture over a blur"]];
  const EFFECT_PARTS = [["scanlines", "Scanlines"], ["curvature", "Curved glass"], ["fringe", "Colour fringe"],
                        ["flicker", "Flicker"], ["glow", "Glow"], ["power_on", "Tube warm-up"]];
  const ITEMS_MIME = "application/x-erynne-signage";
  const TIMING_KEYS = ["seconds", "transition", "fit", "enabled", "days", "start", "end", "date_from", "date_to"];
  const NEW_SLIDE = {
    folder: function () { return { kind: "folder", folder_id: null, library_id: null, subfolders: false, videos: true, sound: false }; },
    image: function () { return { kind: "image", file_id: null }; },
    video: function () { return { kind: "video", file_id: null, sound: true }; },
    pdf: function () { return { kind: "pdf", file_id: null }; },
    text: function () { return { kind: "text", title: "", body: "", background: "#12031f", color: "#ffffff", image_id: null }; },
    web: function () { return { kind: "web", url: "" }; },
    youtube: function () { return { kind: "youtube", url: "", sound: false }; },
    announcements: function () { return { kind: "announcements" }; },
  };

  let data = null;
  let tab = remembered("erynne.signage.tab", "playlists");
  let editing = null;            // the playlist draft being changed
  let scheduleTarget = "all";    // "all", "g<id>" or "s<id>": whose week the grid shows
  const screenDrafts = {};
  const browser = { library: null, folder: null, files: [], selected: new Set(), expanded: new Set(), loading: false };
  let preview = null;

  function remembered(key, fallback) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
  }
  function remember(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage off */ }
  }

  function showError(message) {
    errorNode.textContent = message || "";
    errorNode.hidden = !message;
    if (message) errorNode.scrollIntoView({ block: "nearest" });
  }

  async function call(method, url, body) {
    showError("");
    try {
      const got = await api(method, url, body);
      if (got && got.shows) data = got;
      return got;
    } catch (e) { showError(e.message); return null; }
  }

  function when(iso) {
    if (!iso) return "never";
    const d = new Date(iso.replace(" ", "T") + "Z");
    return isNaN(d) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function clock(seconds) {
    if (!seconds) return "";
    const s = Math.round(seconds), m = Math.floor(s / 60);
    return (m ? m + ":" : "0:") + String(s % 60).padStart(2, "0");
  }

  function sizeLabel(bytes) {
    if (!bytes) return "";
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  function copy(x) { return JSON.parse(JSON.stringify(x)); }

  function moveIn(list, i, by) {
    const j = i + by;
    if (j < 0 || j >= list.length) return;
    const t = list[i]; list[i] = list[j]; list[j] = t;
  }

  // ------------------------------------------------------------ the browser
  function libraryOf(id) { return data.tree.libraries.find(function (l) { return l.id === id; }); }
  function foldersIn(libraryId, parentId) {
    return data.tree.folders.filter(function (f) { return f.library_id === libraryId && (f.parent_id || null) === (parentId || null); });
  }
  function folderById(id) { return data.tree.folders.find(function (f) { return f.id === id; }); }

  function folderPath(folderId) {
    const parts = [];
    let f = folderById(folderId);
    const lib = f ? libraryOf(f.library_id) : null;
    while (f) { parts.unshift(f.name); f = f.parent_id ? folderById(f.parent_id) : null; }
    return (lib ? lib.name : "?") + (parts.length ? "/" + parts.join("/") : "");
  }

  function renderTree() {
    const ul = $("signage-tree");
    ul.replaceChildren();
    data.tree.libraries.forEach(function (lib) {
      const key = "l:" + lib.id;
      const kids = foldersIn(lib.id, null);
      const expanded = browser.expanded.has(key) || browser.expanded.size === 0;
      const node = treeNode((lib.signage ? "🪧 " : "📚 ") + lib.name, {
        depth: 0, expandable: kids.length > 0, expanded: expanded,
        current: browser.library === lib.id && browser.folder === null,
        title: lib.signage ? "The Signage folder: anything a screen can show" : "The Media library's Pictures and Movies",
        onToggle: function () { if (expanded) browser.expanded.delete(key); else browser.expanded.add(key); if (browser.expanded.size === 0) browser.expanded.add("x"); renderTree(); },
        onOpen: function () { open(lib.id, null); },
      });
      if (expanded && kids.length) node.appendChild(branch(lib, null, 1));
      ul.appendChild(node);
    });
    if (!data.tree.libraries.length) ul.appendChild(el("li", "muted small ax-empty", "No Signage folder yet: turn Media on (Settings → Entertainment → Media)."));
  }

  function branch(lib, parentId, depth) {
    const group = el("ul");
    group.setAttribute("role", "group");
    foldersIn(lib.id, parentId).forEach(function (f) {
      const key = "f:" + f.id;
      const kids = foldersIn(lib.id, f.id);
      const expanded = browser.expanded.has(key);
      const node = treeNode("📁 " + f.name, {
        depth: depth, expandable: kids.length > 0, expanded: expanded, current: browser.folder === f.id,
        onToggle: function () { if (expanded) browser.expanded.delete(key); else browser.expanded.add(key); renderTree(); },
        onOpen: function () { open(lib.id, f.id); },
      });
      if (expanded && kids.length) node.appendChild(branch(lib, f.id, depth + 1));
      group.appendChild(node);
    });
    return group;
  }

  async function open(libraryId, folderId) {
    browser.library = libraryId;
    browser.folder = folderId;
    browser.selected.clear();
    browser.loading = true;
    renderTree();
    renderFiles();
    const got = await call("GET", "/api/admin/signage/files?library=" + libraryId + (folderId ? "&folder=" + folderId : ""));
    browser.loading = false;
    browser.files = got ? got.files : [];
    renderFiles();
  }

  function fileIcon(f) { return f.type === "video" ? "🎞️" : f.type === "document" ? "📕" : "🖼️"; }

  function renderFiles() {
    const box = $("signage-files");
    box.replaceChildren();
    const lib = browser.library !== null ? libraryOf(browser.library) : null;
    $("signage-upload-button").hidden = !(lib && lib.signage);
    $("signage-new-folder").hidden = !(lib && lib.signage);
    $("signage-add-selected").hidden = !browser.selected.size;
    if (browser.library === null) { box.appendChild(el("p", "muted small sg-hint", "Pick a folder above to see its pictures, clips and PDFs.")); return; }
    if (browser.loading) { box.appendChild(el("p", "muted small sg-hint", "Loading…")); return; }
    if (!browser.files.length) {
      box.appendChild(el("p", "muted small sg-hint", lib && lib.signage
        ? "Nothing here yet. Upload pictures, MP4 clips or PDFs; they show up once the worker has read them (a minute or two)."
        : "No pictures or clips here."));
      return;
    }
    browser.files.forEach(function (f) {
      const tile = el("div", "tile sg-tile" + (browser.selected.has(f.id) ? " selected" : "") + (f.plays ? "" : " sg-noplay"));
      tile.setAttribute("role", "option");
      tile.title = f.name + (f.plays ? "" : " (needs converting: MP4 H.264 plays)");
      const check = el("input", "c-check");
      check.type = "checkbox";
      check.checked = browser.selected.has(f.id);
      check.addEventListener("click", function (e) { e.stopPropagation(); toggleFile(f.id); });
      const art = el("div", "sg-thumb");
      if (f.art) { const img = el("img"); img.src = f.art; img.alt = ""; img.loading = "lazy"; art.appendChild(img); }
      else art.appendChild(el("span", "ico big", fileIcon(f)));
      const badge = f.type === "video" ? clock(f.duration) : f.type === "document" ? f.pages + " p." : "";
      if (badge) art.appendChild(el("span", "sg-badge", badge));
      tile.append(check, art, el("span", "nm", f.name), el("span", "muted small", sizeLabel(f.size)));
      tile.addEventListener("click", function () { toggleFile(f.id); });
      tile.addEventListener("dblclick", function () { addFiles([f]); });
      tile.draggable = !coarse;
      tile.addEventListener("dragstart", function (e) {
        if (!browser.selected.has(f.id)) { browser.selected.clear(); browser.selected.add(f.id); renderFiles(); }
        e.dataTransfer.setData(ITEMS_MIME, JSON.stringify(selectedFiles().map(function (x) { return x.id; })));
        e.dataTransfer.effectAllowed = "copy";
      });
      box.appendChild(tile);
    });
  }

  function toggleFile(id) {
    if (browser.selected.has(id)) browser.selected.delete(id); else browser.selected.add(id);
    renderFiles();
  }

  function selectedFiles() { return browser.files.filter(function (f) { return browser.selected.has(f.id); }); }

  /* Picked files become slides of the open playlist: pictures, clips (those that play) and PDFs. */
  function addFiles(files) {
    if (!editing) { toast("Open a playlist first (Playlists → Edit), then add pictures to it.", true); return; }
    let added = 0;
    files.forEach(function (f) {
      if (!f.plays) return;
      const kind = f.type === "video" ? "video" : f.type === "document" ? "pdf" : "image";
      const slide = NEW_SLIDE[kind]();
      slide.file_id = f.id;
      slide.file = f;
      editing.slides.push(slide);
      added += 1;
    });
    browser.selected.clear();
    renderFiles();
    renderPane();
    toast(added ? added + " slide(s) added to " + editing.name : "Those files can't play on a screen.", !added);
  }

  async function upload(fileList) {
    const lib = libraryOf(browser.library);
    if (!lib || !lib.signage) return;
    const queue = $("signage-queue");
    queue.hidden = false;
    queue.replaceChildren();
    let done = 0;
    for (const file of Array.from(fileList)) {
      const li = el("li", "q-item");
      li.append(el("span", null, "⬆"), el("span", "q-name", file.name), el("span", "muted small", "uploading…"));
      queue.appendChild(li);
      const body = new FormData();
      body.append("file", file);
      body.append("collection_id", String(lib.id));
      if (browser.folder) body.append("folder_id", String(browser.folder));
      try {
        const resp = await fetch("/api/files", { method: "POST", body: body });
        const got = await resp.json().catch(function () { return {}; });
        if (!resp.ok) throw new Error(got.detail || resp.statusText);
        li.lastChild.textContent = "uploaded";
        done += 1;
      } catch (e) {
        li.lastChild.textContent = e.message;
        li.classList.add("q-failed");
      }
    }
    if (done) toast(done + " file(s) uploaded; they appear here once the worker has read them (a minute or two).");
  }

  async function newFolder() {
    const lib = libraryOf(browser.library);
    if (!lib || !lib.signage) return;
    const name = prompt("New folder in " + folderPath(browser.folder || 0).replace("?", lib.name) + ":");
    if (!name) return;
    const got = await call("POST", "/api/admin/signage/folders", { library_id: lib.id, parent_id: browser.folder, name: name });
    if (got) { browser.expanded.add(browser.folder ? "f:" + browser.folder : "l:" + lib.id); renderTree(); toast("Folder made."); }
  }

  // --------------------------------------------------------------- effects
  function effectsForm(fx, onChange) {
    const box = el("div", "sg-effects");
    const on = form.check("CRT effects", fx.crt, function (v) { fx.crt = v; onChange(fx); render(); });
    box.appendChild(on);
    if (fx.crt) {
      const details = el("details", "sg-fine");
      details.appendChild(el("summary", "small", "Fine-tune ▾"));
      const parts = el("div", "sg-days");
      EFFECT_PARTS.forEach(function (p) {
        parts.appendChild(form.check(p[1], fx[p[0]] !== false, function (v) { fx[p[0]] = v; onChange(fx); }));
      });
      const range = form.input("range", fx.intensity === undefined ? 0.6 : fx.intensity, function (v) { fx.intensity = Number(v); onChange(fx); },
                               { min: "0", max: "1", step: "0.05" });
      details.append(parts, form.field("Strength", range));
      box.appendChild(details);
    }
    return box;
  }

  function fullEffects(fx) {
    const out = { crt: false, scanlines: true, curvature: true, fringe: true, flicker: true, glow: true, power_on: true, intensity: 0.6 };
    return Object.assign(out, fx || {});
  }

  // ------------------------------------------------------------- playlists
  function timingFields(slide) {
    const details = el("details", "sg-timing");
    const limits = slide.days || slide.start || slide.date_from || slide.date_to || slide.enabled === false;
    details.appendChild(el("summary", "small muted", "Settings ▾" + (limits ? " (limited)" : "")));
    const row = el("div", "sg-row");
    if (slide.kind !== "video") {
      row.appendChild(form.field("Seconds", form.input("number", slide.seconds, function (v) { slide.seconds = v ? Number(v) : null; },
                                                        { min: "3", max: "3600", placeholder: "playlist's" })));
    }
    row.append(
      form.field("Transition", form.select(TRANSITIONS, slide.transition, function (v) { slide.transition = v || null; }, "Playlist's")),
      form.field("Fit", form.select(FITS, slide.fit, function (v) { slide.fit = v || null; }, "Playlist's")),
      form.field("On", form.input("checkbox", slide.enabled !== false, function (v) { slide.enabled = v; })));
    const days = el("span", "sg-days");
    DAYS.forEach(function (name, d) {
      days.appendChild(form.check(name, !slide.days || slide.days.indexOf(d) >= 0, function (on) {
        const current = slide.days || [0, 1, 2, 3, 4, 5, 6];
        const next = on ? current.concat([d]).sort() : current.filter(function (x) { return x !== d; });
        slide.days = next.length === 7 ? null : next;
      }));
    });
    const row2 = el("div", "sg-row");
    row2.append(form.field("Only on", days),
      form.field("From", form.input("time", slide.start, function (v) { slide.start = v || null; })),
      form.field("To", form.input("time", slide.end, function (v) { slide.end = v || null; })),
      form.field("From date", form.input("date", slide.date_from, function (v) { slide.date_from = v || null; })),
      form.field("To date", form.input("date", slide.date_to, function (v) { slide.date_to = v || null; })));
    details.append(row, row2);
    return details;
  }

  function folderOptions() {
    const out = [];
    data.tree.libraries.forEach(function (lib) {
      if (lib.root) out.push(["l" + lib.id, lib.name + " (top)"]);
    });
    data.tree.folders.forEach(function (f) { out.push(["f" + f.id, folderPath(f.id)]); });
    return out;
  }

  function slideFields(slide) {
    const box = el("div", "sg-row");
    if (slide.kind === "folder") {
      const value = slide.folder_id ? "f" + slide.folder_id : slide.library_id ? "l" + slide.library_id : "";
      box.append(
        form.field("Folder", form.select(folderOptions(), value, function (v) {
          slide.folder_id = v && v[0] === "f" ? Number(v.slice(1)) : null;
          slide.library_id = v && v[0] === "l" ? Number(v.slice(1)) : null;
        }, "Choose a folder…")),
        form.field("Its subfolders too", form.input("checkbox", slide.subfolders, function (v) { slide.subfolders = v; })),
        form.field("Clips too", form.input("checkbox", slide.videos, function (v) { slide.videos = v; })),
        form.field("Clips with sound", form.input("checkbox", slide.sound, function (v) { slide.sound = v; })));
    } else if (slide.kind === "image" || slide.kind === "video" || slide.kind === "pdf") {
      const f = slide.file;
      box.appendChild(el("span", "small muted", f ? f.name + (f.pages ? " · " + f.pages + " pages" : "") + (f.duration ? " · " + clock(f.duration) : "")
                                                 : "(file gone)"));
      if (slide.kind === "video") box.appendChild(form.field("Sound", form.input("checkbox", slide.sound, function (v) { slide.sound = v; })));
    } else if (slide.kind === "text") {
      const body = el("textarea");
      body.rows = 3;
      body.maxLength = 1000;
      body.value = slide.body;
      body.addEventListener("input", function () { slide.body = body.value; });
      box.append(
        form.field("Title", form.input("text", slide.title, function (v) { slide.title = v; }, { maxlength: "120" })),
        form.field("Text", body),
        form.field("Background", form.input("color", slide.background, function (v) { slide.background = v; })),
        form.field("Text colour", form.input("color", slide.color, function (v) { slide.color = v; })),
        form.field("Picture behind", form.button(slide.file ? slide.file.name : (slide.image_id ? "(gone)" : "Pick from the browser"),
          "link small", function () {
            const picked = selectedFiles().filter(function (f) { return f.type === "image"; })[0];
            if (!picked) { toast("Tick one picture in the browser on the left first.", true); return; }
            slide.image_id = picked.id; slide.file = picked; render();
          })));
      if (slide.image_id) box.appendChild(form.button("No picture", "link small danger", function () { slide.image_id = null; slide.file = null; render(); }));
    } else if (slide.kind === "web") {
      box.append(form.field("Address", form.input("url", slide.url, function (v) { slide.url = v.trim(); },
                                                  { maxlength: "1000", placeholder: "https://…", size: "40" })));
      box.appendChild(el("p", "small muted", "Many sites refuse to be shown inside another page; try it on a screen."));
    } else if (slide.kind === "youtube") {
      box.append(form.field("Link", form.input("url", slide.url, function (v) { slide.url = v.trim(); },
                                               { maxlength: "1000", placeholder: "https://www.youtube.com/watch?v=…", size: "40" })),
                 form.field("Sound", form.input("checkbox", slide.sound, function (v) { slide.sound = v; })));
    } else {
      box.appendChild(el("p", "small muted", "Each current announcement for everyone (Access → Announcements) as a slide."));
    }
    return box;
  }

  /* The slide as stored: only its own settings. */
  function clean(slide) {
    const keys = Object.keys(NEW_SLIDE[slide.kind]()).concat(TIMING_KEYS);
    const out = {};
    keys.forEach(function (k) { if (slide[k] !== undefined) out[k] = slide[k]; });
    return out;
  }

  function slideThumb(slide) {
    const box = el("div", "sg-slide-thumb");
    if (slide.file && slide.file.art) { const img = el("img"); img.src = slide.file.art; img.alt = ""; box.appendChild(img); }
    else box.appendChild(el("span", "ico big", { folder: "📁", text: "🅰", web: "🌐", youtube: "▶️", announcements: "📣", pdf: "📕",
                                                 video: "🎞️", image: "🖼️" }[slide.kind]));
    return box;
  }

  function summary(slide) {
    if (slide.kind === "folder") return slide.folder_label || (slide.folder_id ? folderPath(slide.folder_id) : "(choose a folder)");
    if (slide.file) return slide.file.name;
    if (slide.kind === "text") return slide.title || "(untitled)";
    if (slide.kind === "web" || slide.kind === "youtube") return slide.url || "(no address)";
    return "";
  }

  function editor() {
    const show = editing;
    const box = el("div", "sg-editor");
    const head = el("div", "sg-row");
    head.append(
      form.field("Name", form.input("text", show.name, function (v) { show.name = v; }, { maxlength: "60" })),
      form.field("Description", form.input("text", show.description, function (v) { show.description = v; }, { maxlength: "300", size: "30" })),
      form.field("Seconds a slide", form.input("number", show.seconds, function (v) { show.seconds = Number(v) || 10; }, { min: "3", max: "3600" })),
      form.field("Transition", form.select(TRANSITIONS, show.transition, function (v) { show.transition = v; })),
      form.field("Fit", form.select(FITS, show.fit, function (v) { show.fit = v; })),
      form.field("Shuffle", form.input("checkbox", show.shuffle, function (v) { show.shuffle = v; })));
    const fxMode = show.effects === null ? "follow" : show.effects.crt ? "on" : "off";
    head.appendChild(form.field("Effects", form.select([["follow", "As the screen"], ["on", "CRT on"], ["off", "CRT off"]], fxMode,
      function (v) { show.effects = v === "follow" ? null : fullEffects(v === "on" ? { crt: true } : { crt: false }); render(); })));
    box.appendChild(head);
    if (show.effects && show.effects.crt) box.appendChild(effectsForm(show.effects, function () {}));

    const list = el("ol", "sg-slides");
    show.slides.forEach(function (slide, i) {
      const li = el("li", "sg-slide glass" + (slide.enabled === false ? " sg-off" : ""));
      const bar = el("div", "sg-slide-head");
      const grip = el("span", "sg-grip", "⠿");
      grip.title = "Drag to reorder";
      const label = el("div", "sg-slide-label");
      label.append(el("strong", null, KINDS[slide.kind]), el("span", "muted small", summary(slide)));
      const tools = el("span", "sg-tools");
      tools.append(
        form.button("↑", "link small", function () { moveIn(show.slides, i, -1); render(); }, "Earlier"),
        form.button("↓", "link small", function () { moveIn(show.slides, i, 1); render(); }, "Later"),
        form.button("Remove", "link small danger", function () { show.slides.splice(i, 1); render(); }));
      bar.append(grip, slideThumb(slide), label, tools);
      li.append(bar, slideFields(slide), timingFields(slide));
      list.appendChild(li);
    });
    if (!show.slides.length) list.appendChild(el("li", "muted sg-empty", "No slides yet: tick pictures on the left and press Add to playlist, drag them here, or add a slide below."));
    sortable(list, function (from, to) { const s = show.slides.splice(from, 1)[0]; show.slides.splice(to, 0, s); render(); });
    list.addEventListener("dragover", function (e) {
      if (Array.from(e.dataTransfer.types || []).indexOf(ITEMS_MIME) >= 0) { e.preventDefault(); list.classList.add("drop-here"); }
    });
    list.addEventListener("dragleave", function () { list.classList.remove("drop-here"); });
    list.addEventListener("drop", function (e) {
      list.classList.remove("drop-here");
      let ids = [];
      try { ids = JSON.parse(e.dataTransfer.getData(ITEMS_MIME) || "[]"); } catch (err) { return; }
      if (!ids.length) return;
      e.preventDefault();
      addFiles(browser.files.filter(function (f) { return ids.indexOf(f.id) >= 0; }));
    });
    box.appendChild(list);

    const add = el("div", "sg-row");
    let kind = "folder";
    add.append(form.select(ADDABLE.map(function (k) { return [k, KINDS[k]]; }), kind, function (v) { kind = v; }),
               form.button("Add slide", "secondary small", function () { show.slides.push(NEW_SLIDE[kind]()); render(); }),
               el("span", "muted small", "Pictures, clips and PDFs come from the browser on the left."));
    box.appendChild(add);

    const actions = el("div", "sg-row sg-actions");
    actions.append(
      form.button("Save", "", async function () {
        const got = await call("PUT", "/api/admin/signage/shows/" + show.id, {
          name: show.name, description: show.description, seconds: show.seconds, shuffle: show.shuffle,
          transition: show.transition, fit: show.fit, effects: show.effects, slides: show.slides.map(clean) });
        if (got) { editing = null; toast("Playlist saved; screens pick it up within seconds."); render(); }
      }),
      form.button("Preview", "secondary", function () { openPreview(show.id, show.effects); }),
      form.button("Cancel", "link", function () { editing = null; showError(""); render(); }));
    box.appendChild(actions);
    return box;
  }

  function playlists() {
    const sec = el("section", "sg-section");
    const head = el("div", "ax-bar");
    head.append(el("h2", "ax-split-title", "Playlists"));
    const f = el("form", "sg-row");
    const name = el("input");
    name.placeholder = "New playlist, e.g. Lobby";
    name.maxLength = 60;
    name.required = true;
    const create = el("button", "small", "Create");
    create.type = "submit";
    f.append(name, create);
    f.addEventListener("submit", async function (e) {
      e.preventDefault();
      const got = await call("POST", "/api/admin/signage/shows", { name: name.value });
      if (!got) return;
      const made = data.shows.find(function (s) { return s.id === got.created; });
      editing = made ? copy(made) : null;
      render();
    });
    head.appendChild(f);
    sec.appendChild(head);
    if (!data.enabled) sec.appendChild(el("p", "notice", "Media is off (Settings → Entertainment → Media): playlists can be made, but screens only play them with Media on."));

    const ul = el("ul", "grants sg-list");
    data.shows.forEach(function (s) {
      const li = el("li", "sg-show");
      const bar = el("div", "ax-bar");
      const text = el("div");
      text.append(el("strong", null, s.name), el("span", "muted small", " · " + s.slides.length + " slide(s) · " + s.seconds + " s · " +
        s.transition + (s.shuffle ? " · shuffled" : "") + (s.effects ? (s.effects.crt ? " · CRT" : " · no CRT") : "") +
        " · " + (s.used_by.length ? "on " + s.used_by.join(", ") : "not scheduled yet")));
      if (s.description) text.appendChild(el("div", "muted small", s.description));
      const tools = el("span", "sg-tools");
      if (!editing || editing.id !== s.id) tools.appendChild(form.button("Edit", "link small", function () { editing = copy(s); render(); }));
      tools.append(
        form.button("Preview", "link small", function () { openPreview(s.id, s.effects); }),
        form.button("Copy", "link small", async function () { if (await call("POST", "/api/admin/signage/shows/" + s.id + "/duplicate")) render(); }),
        form.button("Remove", "link small danger", async function () {
          if (!confirm("Remove the playlist “" + s.name + "”? Screens playing it stop, and its schedule entries go.")) return;
          if (await call("DELETE", "/api/admin/signage/shows/" + s.id)) { if (editing && editing.id === s.id) editing = null; render(); }
        }));
      bar.append(text, tools);
      li.appendChild(bar);
      if (editing && editing.id === s.id) li.appendChild(editor());
      ul.appendChild(li);
    });
    if (!data.shows.length) ul.appendChild(el("li", "muted", "No playlists yet."));
    sec.appendChild(ul);
    return sec;
  }

  // -------------------------------------------------------------- preview
  async function openPreview(showId, effects) {
    closePreview();
    const got = await call("GET", "/api/admin/signage/preview/" + showId);
    if (!got) return;
    const dlg = el("dialog", "dlg glass wide sg-preview");
    const head = el("div", "ax-bar");
    head.append(el("h2", null, "Preview: " + got.name), el("span", "muted small", got.items.length + " slide(s)"));
    const frame = el("div", "sg-preview-box mc-signage-preview");
    const zone = new window.SignageRender.Zone(frame, { speed: 1 });
    const fx = fullEffects(effects || data.settings.signage_effects);
    window.SignageRender.applyEffects(frame, fx);
    const bar = el("div", "sg-row");
    const caption = el("span", "muted small", "");
    let index = 0, speed = 1;
    const items = got.items;
    const step = function (by) {
      if (!items.length) { caption.textContent = "Nothing to show: add slides first."; return; }
      index = (index + by + items.length) % items.length;
      const item = items[index];
      caption.textContent = (index + 1) + "/" + items.length + " · " + (item.title || item.type) + " · " + (item.seconds || 10) + " s";
      zone.opts.speed = speed;
      zone.play(item, got, function () { step(1); });
    };
    bar.append(form.button("⏮", "tool", function () { step(-1); }, "Previous"), form.button("⏭", "tool", function () { step(1); }, "Next"),
      form.check("Fast", false, function (v) { speed = v ? 6 : 1; zone.opts.speed = speed; }),
      form.check("CRT", fx.crt, function (v) { fx.crt = v; window.SignageRender.applyEffects(frame, fx); }),
      caption, form.button("Close", "link push-right", closePreview));
    dlg.append(head, frame, bar);
    document.body.appendChild(dlg);
    dlg.addEventListener("close", closePreview);
    preview = { dlg: dlg, zone: zone };
    dlg.showModal();
    window.SignageRender.power(frame, true, fx);
    step(0);
  }

  function closePreview() {
    if (!preview) return;
    preview.zone.stop();
    if (preview.dlg.open) preview.dlg.close();
    preview.dlg.remove();
    preview = null;
  }

  // -------------------------------------------------------------- schedule
  function targetOptions() {
    const out = [["all", "All screens"]];
    data.groups.forEach(function (g) { out.push(["g" + g.id, "Group: " + g.name]); });
    data.screens.forEach(function (s) { out.push(["s" + s.id, "Screen: " + s.name]); });
    return out;
  }

  function targetOf(key) {
    if (key === "all") return { target: "all", target_id: null };
    return { target: key[0] === "g" ? "group" : "screen", target_id: Number(key.slice(1)) };
  }

  function showName(id) { const s = data.shows.find(function (x) { return x.id === id; }); return s ? s.name : "?"; }

  /* Which entries play on the chosen target: its own, then its group's, then everyone's (as the screen ranks them). */
  function entriesFor(key) {
    const t = targetOf(key);
    const screen = t.target === "screen" ? data.screens.find(function (s) { return s.id === t.target_id; }) : null;
    return data.schedule.filter(function (e) {
      if (e.target === "all") return true;
      if (e.target === t.target && e.target_id === t.target_id) return true;
      return t.target === "screen" && e.target === "group" && screen && screen.group_id === e.target_id;
    }).map(function (e) { return Object.assign({ own: e.target === t.target && e.target_id === t.target_id }, e); });
  }

  function minutes(hhmm) { return Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5)); }

  function weekGrid(key) {
    const grid = el("div", "sg-week");
    grid.appendChild(el("div", "sg-week-corner"));
    DAYS.forEach(function (d) { grid.appendChild(el("div", "sg-week-day", d)); });
    const hours = el("div", "sg-week-hours");
    for (let h = 0; h < 24; h += 3) { const l = el("span", null, String(h).padStart(2, "0")); l.style.top = (h / 24 * 100) + "%"; hours.appendChild(l); }
    grid.appendChild(hours);
    const columns = DAYS.map(function () { const c = el("div", "sg-week-col"); grid.appendChild(c); return c; });
    const palette = {};
    data.shows.forEach(function (s, i) { palette[s.id] = i % 8; });
    entriesFor(key).forEach(function (e) {
      const from = minutes(e.start), to = minutes(e.end);
      const spans = from === to ? [[0, 1440]] : from < to ? [[from, to]] : [[from, 1440], [0, to]];
      e.days.forEach(function (d) {
        spans.forEach(function (span, n) {
          const day = n === 1 ? (d + 1) % 7 : d;
          const block = el("button", "sg-block sg-c" + palette[e.show_id] + (e.own ? "" : " sg-inherited") + (e.enabled ? "" : " sg-off")
                           + (e.mode === "always" ? " sg-always" : ""), showName(e.show_id));
          block.type = "button";
          block.style.top = (span[0] / 1440 * 100) + "%";
          block.style.height = (Math.max(span[1] - span[0], 20) / 1440 * 100) + "%";
          block.title = (e.name ? e.name + ": " : "") + showName(e.show_id) + " " + e.start + "–" + e.end + " (" + e.mode + ")" +
            (e.own ? "" : " · from " + (e.target === "all" ? "all screens" : "the group")) + (e.enabled ? "" : " · off");
          block.addEventListener("click", function () { entryDialog(e); });
          columns[day].appendChild(block);
        });
      });
    });
    return grid;
  }

  function entryDialog(entry) {
    const draft = entry ? copy(entry) : { name: "", show_id: data.shows.length ? data.shows[0].id : null, days: [0, 1, 2, 3, 4, 5, 6],
                                          start: "00:00", end: "00:00", date_from: null, date_to: null, mode: "idle", priority: 0, enabled: true };
    if (!entry) Object.assign(draft, targetOf(scheduleTarget));
    if (!data.shows.length) { showError("Make a playlist first."); return; }
    const dlg = el("dialog", "dlg glass wide");
    const f = el("form", "sg-entry");
    f.method = "dialog";
    f.appendChild(el("h2", null, entry ? "Schedule entry" : "New schedule entry"));
    const days = el("span", "sg-days");
    DAYS.forEach(function (name, d) {
      days.appendChild(form.check(name, draft.days.indexOf(d) >= 0, function (on) {
        draft.days = on ? draft.days.concat([d]).sort() : draft.days.filter(function (x) { return x !== d; });
      }));
    });
    const key = draft.target === "all" ? "all" : (draft.target === "group" ? "g" : "s") + draft.target_id;
    const rows = el("div", "sg-row");
    rows.append(
      form.field("Playlist", form.select(data.shows.map(function (s) { return [s.id, s.name]; }), draft.show_id, function (v) { draft.show_id = Number(v); })),
      form.field("For", form.select(targetOptions(), key, function (v) { Object.assign(draft, targetOf(v)); })),
      form.field("Name", form.input("text", draft.name, function (v) { draft.name = v; }, { maxlength: "80", placeholder: "optional" })),
      form.field("Days", days),
      form.field("From", form.input("time", draft.start, function (v) { draft.start = v || "00:00"; })),
      form.field("To", form.input("time", draft.end, function (v) { draft.end = v || "00:00"; })),
      form.field("From date", form.input("date", draft.date_from, function (v) { draft.date_from = v || null; })),
      form.field("To date", form.input("date", draft.date_to, function (v) { draft.date_to = v || null; })),
      form.field("Plays", form.select([["idle", "When the screen is idle"], ["always", "Always (signage hours)"]], draft.mode, function (v) { draft.mode = v; })),
      form.field("Priority", form.input("number", draft.priority, function (v) { draft.priority = Number(v) || 0; }, { min: "-100", max: "100" })),
      form.field("On", form.input("checkbox", draft.enabled, function (v) { draft.enabled = v; })));
    f.appendChild(rows);
    f.appendChild(el("p", "small muted", "From and To the same: all day. To before From: past midnight. A screen's own entry beats its " +
      "group's, which beats one for all screens; among those, the higher priority, then the newer entry."));
    const actions = el("div", "dlg-actions");
    if (entry) actions.appendChild(form.button("Remove", "link danger", async function () {
      if (!confirm("Remove this schedule entry?")) return;
      if (await call("DELETE", "/api/admin/signage/schedule/" + entry.id)) { dlg.close(); render(); }
    }));
    actions.append(form.button("Cancel", "link", function () { dlg.close(); }), form.button("Save", "", async function () {
      const got = entry ? await call("PUT", "/api/admin/signage/schedule/" + entry.id, draft)
                        : await call("POST", "/api/admin/signage/schedule", draft);
      if (got) { dlg.close(); toast("Schedule saved."); render(); }
    }));
    f.appendChild(actions);
    dlg.appendChild(f);
    document.body.appendChild(dlg);
    dlg.addEventListener("close", function () { dlg.remove(); });
    dlg.showModal();
  }

  function schedule() {
    const sec = el("section", "sg-section");
    const head = el("div", "ax-bar");
    head.append(el("h2", "ax-split-title", "Schedule"));
    const tools = el("span", "sg-tools");
    const options = targetOptions();
    if (!options.some(function (o) { return o[0] === scheduleTarget; })) scheduleTarget = "all";
    tools.append(form.field("Show the week of", form.select(options, scheduleTarget, function (v) { scheduleTarget = v; render(); })),
                 form.button("Add entry", "small", function () { entryDialog(null); }));
    head.appendChild(tools);
    sec.appendChild(head);
    sec.appendChild(weekGrid(scheduleTarget));
    const legend = el("p", "small muted", "Click an entry to change it. Paler entries come from the group or from all screens. " +
      "Striped ones run always (signage hours); the others wait for the screen to be idle.");
    sec.appendChild(legend);
    const list = el("ul", "grants sg-list");
    entriesFor(scheduleTarget).forEach(function (e) {
      const li = el("li");
      const text = (e.name ? e.name + " · " : "") + showName(e.show_id) + " · " + e.days.map(function (d) { return DAYS[d]; }).join(" ") +
        " · " + e.start + "–" + e.end + (e.date_from || e.date_to ? " · " + (e.date_from || "…") + " to " + (e.date_to || "…") : "") +
        " · " + e.mode + (e.priority ? " · priority " + e.priority : "") + (e.enabled ? "" : " · off") +
        (e.own ? "" : " · for " + (e.target === "all" ? "all screens" : "the group"));
      li.append(el("span", e.enabled ? "" : "muted", text), form.button("Edit", "link small", function () { entryDialog(e); }));
      list.appendChild(li);
    });
    if (!data.schedule.length) list.appendChild(el("li", "muted", "Nothing scheduled yet: add an entry."));
    sec.appendChild(list);
    return sec;
  }

  // --------------------------------------------------------------- screens
  function screenCard(s) {
    const draft = screenDrafts[s.id] || (screenDrafts[s.id] = {
      idle_minutes: s.idle_minutes, group_id: s.group_id, location: s.location, orientation: s.orientation,
      effects: s.effects === null ? null : copy(s.effects) });
    const card = el("div", "sg-screen glass" + (s.online ? " sg-online" : ""));
    const head = el("div", "sg-screen-head");
    const dot = el("span", "presence " + (s.online ? "online" : "offline"), "●");
    const title = el("div", "sg-screen-title");
    title.append(dot, el("strong", null, "📺 " + s.name), el("span", "muted small", " " + s.owner + "'s" + (s.location ? " · " + s.location : "")));
    const state = s.state || {};
    const now = s.online ? (state.kind && state.kind !== "idle" ? "Showing " + (state.title || state.kind) + (state.sub ? " · " + state.sub : "")
                                                                  : "On, nothing playing")
                         : "Off · last seen " + when(s.last_seen);
    const status = el("div", "small", now);
    if (!s.online) status.classList.add("muted");
    title.appendChild(status);
    const meta = [];
    if (s.player_version) meta.push("player " + s.player_version);
    if (s.orientation === "portrait") meta.push("portrait");
    if (s.last_state && s.last_state.errors && s.last_state.errors.length) meta.push("⚠ couldn't show: " + s.last_state.errors.slice(-3).join(", "));
    if (meta.length) title.appendChild(el("div", "muted small", meta.join(" · ")));
    const tools = el("span", "sg-tools");
    [["Identify", "identify"], ["Reload", "reload"], ["Stop signage", "signage_stop"], ["Start signage", "signage_start"]].forEach(function (c) {
      tools.appendChild(form.button(c[0], "link small", async function () {
        showError("");
        try { await api("POST", "/api/admin/signage/screens/" + s.id + "/command", { action: c[1] }); toast(c[0] + " sent to " + s.name + "."); }
        catch (e) { showError(e.message); }
      }));
    });
    head.append(title, tools);
    card.appendChild(head);

    const row = el("div", "sg-row");
    row.append(
      form.field("Group", form.select(data.groups.map(function (g) { return [g.id, g.name]; }), draft.group_id, function (v) { draft.group_id = v ? Number(v) : null; }, "No group")),
      form.field("Location", form.input("text", draft.location, function (v) { draft.location = v; }, { maxlength: "80", placeholder: "e.g. Bar, ground floor" })),
      form.field("Orientation", form.select([["landscape", "Landscape"], ["portrait", "Portrait"]], draft.orientation, function (v) { draft.orientation = v; })),
      form.field("Idle after (minutes)", form.input("number", draft.idle_minutes, function (v) { draft.idle_minutes = Number(v) || 5; }, { min: "1", max: "1440" })));
    card.appendChild(row);
    const fxMode = draft.effects === null ? "default" : draft.effects.crt ? "on" : "off";
    const fxRow = el("div", "sg-row");
    fxRow.appendChild(form.field("CRT effects", form.select([["default", "Settings default"], ["on", "On"], ["off", "Off"]], fxMode, function (v) {
      draft.effects = v === "default" ? null : fullEffects(v === "on" ? { crt: true } : { crt: false }); render();
    })));
    if (draft.effects && draft.effects.crt) fxRow.appendChild(effectsForm(draft.effects, function () {}));
    card.appendChild(fxRow);
    const actions = el("div", "sg-row");
    actions.append(
      form.button("Save", "small", async function () {
        if (await call("PUT", "/api/admin/signage/screens/" + s.id, draft)) { delete screenDrafts[s.id]; toast("Screen saved."); render(); }
      }),
      form.button("Undo changes", "link small", function () { delete screenDrafts[s.id]; render(); }));
    card.appendChild(actions);
    return card;
  }

  function screens() {
    const sec = el("section", "sg-section");
    const head = el("div", "ax-bar");
    head.append(el("h2", "ax-split-title", "Screens"));
    const f = el("form", "sg-row");
    const name = el("input");
    name.placeholder = "New group, e.g. Bar screens";
    name.maxLength = 40;
    name.required = true;
    const create = el("button", "small", "Create group");
    create.type = "submit";
    f.append(name, create);
    f.addEventListener("submit", async function (e) {
      e.preventDefault();
      if (await call("POST", "/api/admin/signage/groups", { name: name.value })) render();
    });
    head.appendChild(f);
    sec.appendChild(head);
    sec.appendChild(el("p", "small muted", "Every paired screen, whoever owns it. Put screens in groups to schedule them together; " +
      "a screen's own schedule entries beat its group's. Identify shows the screen's name on it for a few seconds."));
    if (!data.screens.length) {
      sec.appendChild(el("p", "muted", "No screens yet. Pair a TV under Media → Screens."));
      return sec;
    }
    const groups = data.groups.concat([{ id: null, name: "No group" }]);
    groups.forEach(function (g) {
      const members = data.screens.filter(function (s) { return (s.group_id || null) === g.id; });
      if (!members.length && g.id === null) return;
      const block = el("div", "sg-group");
      const bar = el("div", "ax-bar");
      bar.append(el("h3", "ax-h", g.name + " (" + members.length + ")"));
      if (g.id !== null) {
        const tools = el("span", "sg-tools");
        tools.append(
          form.button("Rename", "link small", async function () {
            const renamed = prompt("Rename the group:", g.name);
            if (renamed && renamed !== g.name && await call("PUT", "/api/admin/signage/groups/" + g.id, { name: renamed })) render();
          }),
          form.button("Remove", "link small danger", async function () {
            if (!confirm("Remove the group “" + g.name + "”? Its schedule entries go; its screens keep what's for all screens.")) return;
            if (await call("DELETE", "/api/admin/signage/groups/" + g.id)) render();
          }));
        bar.appendChild(tools);
      }
      block.appendChild(bar);
      members.forEach(function (s) { block.appendChild(screenCard(s)); });
      if (!members.length) block.appendChild(el("p", "muted small", "No screens in this group yet."));
      sec.appendChild(block);
    });
    return sec;
  }

  // -------------------------------------------------------------- settings
  function settingsPane() {
    const sec = el("section", "sg-section");
    sec.appendChild(el("h2", "ax-split-title", "Settings"));
    const lib = data.signage_library;
    sec.appendChild(el("p", "small muted", lib
      ? "Signage assets live in the folder “" + lib.name + "” at the top of the share: anyone with rights there can drop in pictures, " +
        "MP4 clips and PDFs from Windows, or use Upload on the left. The worker reads them within a minute or two."
      : "The Signage folder is made on the share once Media is on (Settings → Entertainment → Media)."));
    const folder = el("form", "sg-row");
    const name = form.input("text", data.settings.signage_folder, null, { maxlength: "120" });
    const save = el("button", "small", "Save");
    save.type = "submit";
    folder.append(form.field("Signage folder (top of the share)", name), save);
    folder.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try { await api("PUT", "/api/admin/settings/media", { signage_folder: name.value }); toast("Signage folder set."); load(); }
      catch (err) { showError(err.message); }
    });
    sec.appendChild(folder);
    sec.appendChild(el("h3", "ax-h", "CRT effects for new screens"));
    sec.appendChild(el("p", "small muted", "What a screen wears unless it says otherwise: scanlines, curved glass, colour fringe, flicker, glow " +
      "and a tube warm-up, like an old monitor. Each screen and each playlist can override it."));
    const fx = fullEffects(data.settings.signage_effects);
    const fxBox = el("div");
    fxBox.appendChild(effectsForm(fx, function () {}));
    fxBox.appendChild(form.button("Save effects", "small", async function () {
      showError("");
      try { await api("PUT", "/api/admin/settings/media", { signage_effects: fx }); toast("Effects saved; screens pick them up within seconds."); load(); }
      catch (err) { showError(err.message); }
    }));
    sec.appendChild(fxBox);
    return sec;
  }

  // ---------------------------------------------------------------- render
  function renderPane() {
    const box = $("signage-admin");
    box.replaceChildren();
    document.querySelectorAll("#signage-tabs [data-sg-tab]").forEach(function (b) {
      b.setAttribute("aria-selected", String(b.dataset.sgTab === tab));
    });
    if (!data.library && tab !== "settings") box.appendChild(el("p", "notice", "There is no Media folder yet (Settings → Entertainment → Media): turn Media on to get a Signage folder and screens."));
    box.appendChild({ playlists: playlists, schedule: schedule, screens: screens, settings: settingsPane }[tab]());
  }

  function render() {
    renderTree();
    renderFiles();
    renderPane();
  }

  async function load() {
    try {
      data = await api("GET", "/api/admin/signage");
      if (browser.library === null && data.tree.libraries.length) {
        const sg = data.tree.libraries.find(function (l) { return l.signage; }) || data.tree.libraries[0];
        browser.library = sg.id;
        browser.expanded.add("l:" + sg.id);
        open(sg.id, null);
      }
      render();
    } catch (e) { $("signage-admin").replaceChildren(el("p", "error", e.message)); }
  }

  document.querySelectorAll("#signage-tabs [data-sg-tab]").forEach(function (b) {
    b.addEventListener("click", function () { tab = b.dataset.sgTab; remember("erynne.signage.tab", tab); renderPane(); });
  });
  $("signage-upload-button").addEventListener("click", function () { $("signage-upload").click(); });
  $("signage-upload").addEventListener("change", function () { upload(this.files); this.value = ""; });
  $("signage-new-folder").addEventListener("click", newFolder);
  $("signage-add-selected").addEventListener("click", function () { addFiles(selectedFiles()); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && preview) closePreview(); });

  window.ErynneSignage = { load: load };
  // Opened straight from the address (#tab=signage): admin.js showed the panel before this script loaded.
  const panel = document.querySelector('[data-panel="signage"]');
  if (panel && !panel.hidden) load();
})();
