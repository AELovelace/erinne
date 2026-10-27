/* Access → Signage: slideshows (a Media folder's pictures and clips, videos, text, web pages, the
   announcements for everyone) and, per screen, when each plays (mnemerynne/media/signage.py). Edits
   are drafts until Save; all text through textContent. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const errorNode = $("admin-error");
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const KINDS = { folder: "Pictures and clips from a folder", video: "A video", text: "Text",
                  web: "A web page", announcements: "Announcements for everyone" };
  const NEW_SLIDE = {
    folder: function () { return { kind: "folder", folder_id: null, subfolders: false, videos: true, sound: false, seconds: null }; },
    video: function () { return { kind: "video", file_id: null, sound: true }; },
    text: function () { return { kind: "text", title: "", body: "", background: "#12031f", color: "#ffffff", image_id: null, seconds: null }; },
    web: function () { return { kind: "web", url: "", seconds: null }; },
    announcements: function () { return { kind: "announcements", seconds: null }; },
  };

  let data = null;
  let editing = null;            // {id, name, seconds, shuffle, slides}: the slideshow being changed
  const screenDrafts = {};       // screen id -> {idle_minutes, rules}
  const fileCache = new Map();   // folder id -> its pictures and videos

  function showError(message) {
    errorNode.textContent = message || "";
    errorNode.hidden = !message;
  }

  function button(text, cls, onClick, title) {
    const b = el("button", cls || "", text);
    b.type = "button";
    if (title) b.title = title;
    b.addEventListener("click", onClick);
    return b;
  }

  function field(label, input) {
    const wrap = el("label", "sg-field");
    wrap.append(el("span", "small muted", label), input);
    return wrap;
  }

  function input(type, value, onInput, attrs) {
    const i = el("input");
    i.type = type;
    if (type === "checkbox") i.checked = !!value; else i.value = value === null || value === undefined ? "" : value;
    Object.keys(attrs || {}).forEach(function (k) { i.setAttribute(k, attrs[k]); });
    i.addEventListener(type === "checkbox" ? "change" : "input", function () {
      onInput(type === "checkbox" ? i.checked : i.value);
    });
    return i;
  }

  function select(options, value, onChange, blank) {
    const s = el("select");
    if (blank !== undefined) { const o = el("option", null, blank); o.value = ""; s.appendChild(o); }
    options.forEach(function (o) {
      const opt = el("option", null, o[1]);
      opt.value = String(o[0]);
      s.appendChild(opt);
    });
    s.value = value === null || value === undefined ? "" : String(value);
    s.addEventListener("change", function () { onChange(s.value); });
    return s;
  }

  function seconds(slide) {
    return field("Seconds", input("number", slide.seconds, function (v) { slide.seconds = v ? Number(v) : null; },
                                  { min: "3", max: "3600", placeholder: "slideshow's" }));
  }

  function moveIn(list, i, by) {
    const j = i + by;
    if (j < 0 || j >= list.length) return;
    const t = list[i]; list[i] = list[j]; list[j] = t;
  }

  function when(iso) {
    if (!iso) return "never";
    const d = new Date(iso.replace(" ", "T") + "Z");
    return isNaN(d) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  async function files(folderId) {
    if (!fileCache.has(folderId)) fileCache.set(folderId, (await api("GET", "/api/admin/signage/files?folder=" + folderId)).files);
    return fileCache.get(folderId);
  }

  /* A folder, then a file in it: a video to play, or a picture behind a text slide. */
  function filePicker(slide, key, type) {
    const wrap = el("div", "sg-row");
    const fileSel = el("select");
    const fill = async function (folderId, chosen) {
      fileSel.replaceChildren();
      const none = el("option", null, type === "image" ? "No picture" : "Choose a video");
      none.value = "";
      fileSel.appendChild(none);
      if (!folderId) return;
      try {
        (await files(folderId)).filter(function (f) { return f.type === type; }).forEach(function (f) {
          const o = el("option", null, f.name + (f.plays ? "" : " (needs converting: MP4 H.264 plays)"));
          o.value = String(f.id);
          o.disabled = !f.plays;
          fileSel.appendChild(o);
        });
      } catch (e) { showError(e.message); }
      fileSel.value = chosen ? String(chosen) : "";
    };
    const folderSel = select(data.folders.map(function (f) { return [f.id, f.path]; }), slide.file_folder,
      function (v) { slide.file_folder = v ? Number(v) : null; slide[key] = null; fill(slide.file_folder, null); }, "Folder…");
    fileSel.addEventListener("change", function () { slide[key] = fileSel.value ? Number(fileSel.value) : null; });
    fill(slide.file_folder, slide[key]);
    wrap.append(field("Folder", folderSel), field(type === "image" ? "Picture" : "Video", fileSel));
    return wrap;
  }

  function slideFields(slide) {
    const box = el("div", "sg-row");
    if (slide.kind === "folder") {
      box.append(
        field("Folder", select(data.folders.map(function (f) { return [f.id, f.path]; }), slide.folder_id,
                               function (v) { slide.folder_id = v ? Number(v) : null; }, "Choose a folder…")),
        field("Its subfolders too", input("checkbox", slide.subfolders, function (v) { slide.subfolders = v; })),
        field("Clips too", input("checkbox", slide.videos, function (v) { slide.videos = v; })),
        field("Clips with sound", input("checkbox", slide.sound, function (v) { slide.sound = v; })),
        seconds(slide));
    } else if (slide.kind === "video") {
      box.append(filePicker(slide, "file_id", "video"),
                 field("Sound", input("checkbox", slide.sound, function (v) { slide.sound = v; })));
    } else if (slide.kind === "text") {
      const body = el("textarea");
      body.rows = 3;
      body.maxLength = 1000;
      body.value = slide.body;
      body.addEventListener("input", function () { slide.body = body.value; });
      box.append(
        field("Title", input("text", slide.title, function (v) { slide.title = v; }, { maxlength: "120" })),
        field("Text", body),
        field("Background", input("color", slide.background, function (v) { slide.background = v; })),
        field("Text colour", input("color", slide.color, function (v) { slide.color = v; })),
        filePicker(slide, "image_id", "image"),
        seconds(slide));
    } else if (slide.kind === "web") {
      box.append(
        field("Address", input("url", slide.url, function (v) { slide.url = v.trim(); },
                               { maxlength: "1000", placeholder: "https://…", size: "40" })),
        seconds(slide));
      box.appendChild(el("p", "small muted", "Many sites refuse to be shown inside another page; try it on a screen."));
    } else {
      box.append(seconds(slide));
      box.appendChild(el("p", "small muted", "Each current announcement for everyone (Access → Announcements) as a slide."));
    }
    return box;
  }

  /* The slide as stored: only its own settings. */
  function clean(slide) {
    const keys = Object.keys(NEW_SLIDE[slide.kind]());
    const out = {};
    keys.forEach(function (k) { out[k] = slide[k]; });
    return out;
  }

  function editor() {
    const show = editing;
    const box = el("div", "sg-editor");
    const head = el("div", "sg-row");
    head.append(
      field("Name", input("text", show.name, function (v) { show.name = v; }, { maxlength: "60" })),
      field("Seconds a slide", input("number", show.seconds, function (v) { show.seconds = Number(v) || 10; },
                                     { min: "3", max: "3600" })),
      field("Shuffle", input("checkbox", show.shuffle, function (v) { show.shuffle = v; })));
    box.appendChild(head);

    const list = el("ol", "sg-slides");
    show.slides.forEach(function (slide, i) {
      const li = el("li", "sg-slide glass");
      const bar = el("div", "ax-bar");
      bar.append(el("strong", null, KINDS[slide.kind]),
        el("span", "sg-tools", ""));
      const tools = bar.lastChild;
      tools.append(
        button("↑", "link small", function () { moveIn(show.slides, i, -1); render(); }, "Earlier"),
        button("↓", "link small", function () { moveIn(show.slides, i, 1); render(); }, "Later"),
        button("Remove", "link small danger", function () { show.slides.splice(i, 1); render(); }));
      li.append(bar, slideFields(slide));
      list.appendChild(li);
    });
    if (!show.slides.length) list.appendChild(el("li", "muted", "No slides yet: add one below."));
    box.appendChild(list);

    const add = el("div", "sg-row");
    let kind = "folder";
    add.append(select(Object.keys(KINDS).map(function (k) { return [k, KINDS[k]]; }), kind, function (v) { kind = v; }),
               button("Add slide", "secondary small", function () { show.slides.push(NEW_SLIDE[kind]()); render(); }));
    box.appendChild(add);

    const actions = el("div", "sg-row");
    actions.append(
      button("Save", "", async function () {
        showError("");
        try {
          data = await api("PUT", "/api/admin/signage/shows/" + show.id, {
            name: show.name, seconds: show.seconds, shuffle: show.shuffle, slides: show.slides.map(clean) });
          editing = null;
          render();
        } catch (e) { showError(e.message); }
      }),
      button("Cancel", "secondary", function () { editing = null; showError(""); render(); }));
    box.appendChild(actions);
    return box;
  }

  function showsSection() {
    const sec = el("section", "sg-section");
    sec.appendChild(el("h2", null, "Slideshows"));
    const form = el("form", "sg-row");
    const name = el("input");
    name.placeholder = "New slideshow, e.g. Lobby";
    name.maxLength = 60;
    name.required = true;
    const create = el("button", "small", "Create");
    create.type = "submit";
    form.append(name, create);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try {
        data = await api("POST", "/api/admin/signage/shows", { name: name.value });
        const made = data.shows.find(function (s) { return s.id === data.created; });
        editing = made ? JSON.parse(JSON.stringify(made)) : null;
        render();
      } catch (err) { showError(err.message); }
    });
    sec.appendChild(form);

    const ul = el("ul", "grants");
    data.shows.forEach(function (s) {
      const li = el("li", "sg-show");
      const head = el("div", "ax-bar");
      head.append(el("strong", null, s.name),
        el("span", "muted small", s.slides.length + " slide(s) · " + s.seconds + " s each" + (s.shuffle ? " · shuffled" : "") +
           " · " + (s.used_by.length ? "on " + s.used_by.join(", ") : "on no screen yet")));
      const tools = el("span", "sg-tools");
      if (!editing || editing.id !== s.id) {
        tools.appendChild(button("Edit", "link small", function () { editing = JSON.parse(JSON.stringify(s)); render(); }));
      }
      tools.appendChild(button("Remove", "link small danger", async function () {
        if (!confirm("Remove the slideshow “" + s.name + "”? Screens playing it stop.")) return;
        showError("");
        try { data = await api("DELETE", "/api/admin/signage/shows/" + s.id); if (editing && editing.id === s.id) editing = null; render(); }
        catch (e) { showError(e.message); }
      }));
      head.appendChild(tools);
      li.appendChild(head);
      if (editing && editing.id === s.id) li.appendChild(editor());
      ul.appendChild(li);
    });
    if (!data.shows.length) ul.appendChild(el("li", "muted", "No slideshows yet."));
    sec.appendChild(ul);
    return sec;
  }

  function ruleRow(draft, rule, i) {
    const li = el("li", "sg-rule sg-row");
    const days = el("span", "sg-days");
    DAYS.forEach(function (name, d) {
      const box = input("checkbox", rule.days.indexOf(d) >= 0, function (on) {
        rule.days = on ? rule.days.concat([d]).sort() : rule.days.filter(function (x) { return x !== d; });
      });
      const label = el("label", "check small");
      label.append(box, document.createTextNode(name));
      days.appendChild(label);
    });
    li.append(
      field("Slideshow", select(data.shows.map(function (s) { return [s.id, s.name]; }), rule.show_id,
                                function (v) { rule.show_id = Number(v); })),
      field("Days", days),
      field("From", input("time", rule.start, function (v) { rule.start = v || "00:00"; })),
      field("To", input("time", rule.end, function (v) { rule.end = v || "00:00"; })),
      field("Plays", select([["idle", "When idle"], ["always", "Always (signage)"]], rule.mode, function (v) { rule.mode = v; })),
      el("span", "sg-tools"));
    li.lastChild.append(
      button("↑", "link small", function () { moveIn(draft.rules, i, -1); render(); }, "Checked earlier"),
      button("↓", "link small", function () { moveIn(draft.rules, i, 1); render(); }, "Checked later"),
      button("Remove", "link small danger", function () { draft.rules.splice(i, 1); render(); }));
    return li;
  }

  function screensSection() {
    const sec = el("section", "sg-section");
    sec.appendChild(el("h2", null, "Screens"));
    sec.appendChild(el("p", "small muted", "Each screen plays the first of its rules that matches its own clock. " +
      "When idle: once nothing has played and nobody pressed a key for the idle time; a key or a cast stops it. " +
      "Always: whenever nothing is cast to it. From and To the same: all day; To before From: past midnight."));
    if (!data.screens.length) {
      sec.appendChild(el("p", "muted", "No screens yet. Pair a TV under Media → Screens."));
      return sec;
    }
    data.screens.forEach(function (s) {
      const draft = screenDrafts[s.id] || (screenDrafts[s.id] = {
        idle_minutes: s.idle_minutes, rules: JSON.parse(JSON.stringify(s.rules)) });
      const card = el("div", "sg-screen glass");
      const head = el("div", "ax-bar");
      const now = s.online ? (s.state && s.state.kind !== "idle" ? "showing " + s.state.title : "on, nothing playing")
                           : "off · last seen " + when(s.last_seen);
      head.append(el("strong", null, "📺 " + s.name), el("span", "muted small", s.owner + "'s · " + now));
      card.appendChild(head);
      card.appendChild(field("Idle after (minutes)", input("number", draft.idle_minutes,
        function (v) { draft.idle_minutes = Number(v) || 5; }, { min: "1", max: "1440" })));
      const rules = el("ol", "sg-rules");
      draft.rules.forEach(function (r, i) { rules.appendChild(ruleRow(draft, r, i)); });
      if (!draft.rules.length) rules.appendChild(el("li", "muted small", "No rules: this screen shows no slideshow."));
      card.appendChild(rules);
      const actions = el("div", "sg-row");
      actions.append(
        button("Add rule", "secondary small", function () {
          if (!data.shows.length) { showError("Make a slideshow first."); return; }
          draft.rules.push({ show_id: data.shows[0].id, days: [0, 1, 2, 3, 4, 5, 6], start: "00:00", end: "00:00", mode: "idle" });
          render();
        }),
        button("Save", "small", async function () {
          showError("");
          try {
            data = await api("PUT", "/api/admin/signage/screens/" + s.id, draft);
            delete screenDrafts[s.id];
            render();
          } catch (e) { showError(e.message); }
        }),
        button("Undo changes", "link small", function () { delete screenDrafts[s.id]; render(); }));
      card.appendChild(actions);
      sec.appendChild(card);
    });
    return sec;
  }

  function render() {
    const box = $("signage-admin");
    box.replaceChildren();
    if (!data.enabled) box.appendChild(el("p", "notice", "Media is off (Settings → Media): slideshows can be made, " +
                                                            "but screens only play them with Media on."));
    else if (!data.library) box.appendChild(el("p", "notice", "There is no Media folder yet (Settings → Media)."));
    box.append(showsSection(), screensSection());
  }

  async function load() {
    try {
      data = await api("GET", "/api/admin/signage");
      fileCache.clear();
      render();
    } catch (e) { $("signage-admin").replaceChildren(el("p", "error", e.message)); }
  }

  window.ErynneSignage = { load: load };
})();
