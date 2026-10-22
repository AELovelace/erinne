/* File manager: location tree, folders, list/grid, multi-select, drag and
   drop, context menu, upload queue, recycle bin and a details/preview pane.
   State lives in the URL hash (#c=3&f=12, #c=3&trash=1, #q=text) so Back works.
   All user-controlled text goes through textContent, never innerHTML. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const ITEMS_MIME = "application/x-erynne-items";
  const TRASH_DAYS = Number($("fm").dataset.trashDays || 30);
  const UPLOAD_CONCURRENCY = 3;

  const STATUS = { queued: "Queued", processing: "Indexing", ready: "Ready", failed: "Failed",
                   stored: "Not searchable" };

  const state = {
    locations: [],
    expanded: new Set(),          // "c:3" and "f:12" tree nodes
    mode: "browse",               // browse | trash | search
    cid: null, fid: null, q: "",
    canWrite: false,
    items: [],                    // current view: [{type, id, name, ...}]
    selected: new Set(),          // "file:3", "folder:7"
    anchor: null,                 // index for shift-click ranges
    view: load("fm.view", "list"),
    sort: load("fm.sort", "name"),
    info: load("fm.info", "0") === "1",
  };

  function load(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
  }
  function save(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  const key = function (item) { return item.type + ":" + item.id; };
  const selectedItems = function () { return state.items.filter(function (i) { return state.selected.has(key(i)); }); };
  const locationOf = function (cid) { return state.locations.find(function (l) { return l.id === cid; }); };

  // ------------------------------------------------------------- formatting
  function sizeLabel(bytes) {
    if (bytes === null || bytes === undefined) return "";
    if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(1) + " GB";
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  function dateLabel(sqlDate) {
    if (!sqlDate) return "";
    const d = new Date(sqlDate.replace(" ", "T") + "Z");
    if (isNaN(d)) return sqlDate;
    const today = new Date();
    if (d.toDateString() === today.toDateString()) {
      return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }
    return d.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
  }

  function icon(item) {
    if (item.type === "folder") return item.role === "agents" ? "🤖" : "📁";
    const mime = item.mime || "";
    if (item.kind === "pdf") return "📕";
    if (item.kind === "md") return "📝";
    if (item.kind === "code") return "🧾";
    if (/sheet|excel|csv/.test(mime)) return "📊";
    if (/presentation|powerpoint/.test(mime)) return "📽️";
    if (/word|opendocument\.text|rtf/.test(mime)) return "📘";
    if (item.kind === "txt" || mime.startsWith("text/")) return "📄";
    if (mime.startsWith("image/")) return "🖼️";
    if (mime.startsWith("audio/")) return "🎵";
    if (mime.startsWith("video/")) return "🎞️";
    if (/zip|compressed|tar|rar|7z/.test(mime)) return "🗜️";
    if (/sheet|excel|csv/.test(mime)) return "📊";
    if (/presentation|powerpoint/.test(mime)) return "📽️";
    if (/word|document|rtf/.test(mime)) return "📘";
    return "📄";
  }

  function kindLabel(item) {
    if (item.type === "folder") return "Folder";
    if (item.kind === "pdf") return "PDF";
    if (item.kind === "md") return "Markdown";
    if (item.kind === "txt") return "Text";
    const ext = (item.name.match(/\.([a-z0-9]{1,8})$/i) || [])[1];
    if (item.kind === "code") return ext ? "Code (" + ext.toLowerCase() + ")" : "Code";
    return ext ? ext.toUpperCase() + " file" : "File";
  }

  function statusText(f) {
    if (f.status === "processing" && f.progress) return f.progress;
    if (f.status === "queued" && f.progress) return f.progress;
    return STATUS[f.status] || f.status;
  }

  // ------------------------------------------------------------------ toast
  let toastTimer = null;
  function toast(message, isError) {
    const t = $("toast");
    t.textContent = message;
    t.classList.toggle("error", !!isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, isError ? 7000 : 3500);
  }

  function reportBatch(result, verb) {
    const failed = result.results.filter(function (r) { return !r.ok; });
    if (!failed.length) return;
    const first = failed[0].error || "failed";
    toast(failed.length === result.results.length ? "Couldn't " + verb + ": " + first
      : failed.length + " item(s) couldn't be " + verb + "d: " + first, true);
  }

  // ------------------------------------------------------------ navigation
  function readHash() {
    const params = new URLSearchParams(window.location.hash.slice(1));
    if (params.has("q")) {
      return { mode: "search", q: params.get("q"), cid: null, fid: null };
    }
    const cid = params.has("c") ? Number(params.get("c")) : null;
    if (params.get("trash") === "1") return { mode: "trash", cid: cid, fid: null, q: "" };
    return { mode: "browse", cid: cid, fid: params.has("f") ? Number(params.get("f")) : null, q: "" };
  }

  function go(target) {
    const params = new URLSearchParams();
    if (target.mode === "search") params.set("q", target.q);
    else {
      params.set("c", target.cid);
      if (target.mode === "trash") params.set("trash", "1");
      else if (target.fid) params.set("f", target.fid);
    }
    const hash = "#" + params.toString();
    if (window.location.hash === hash) refresh(); else window.location.hash = hash;
  }

  async function refresh(keepSelection) {
    const target = readHash();
    if (target.mode !== "search" && (target.cid === null || !locationOf(target.cid))) {
      const home = state.locations[0];
      if (!home) return;
      go({ mode: "browse", cid: home.id, fid: null });
      return;
    }
    const sameView = state.mode === target.mode && state.cid === target.cid && state.fid === target.fid
      && state.q === target.q;
    Object.assign(state, target);
    if (!keepSelection || !sameView) { state.selected.clear(); state.anchor = null; }
    $("fm-search").value = state.mode === "search" ? state.q : "";
    try {
      if (state.mode === "browse") await loadBrowse();
      else if (state.mode === "trash") await loadTrash();
      else await loadSearch();
    } catch (e) {
      if (e.status === 404 && state.mode === "browse" && state.fid) {
        toast("That folder no longer exists.", true);
        go({ mode: "browse", cid: state.cid, fid: null });
        return;
      }
      toast(e.message, true);
    }
    renderTree();
    renderView();
    schedulePoll();
  }

  async function loadBrowse() {
    const data = await api("GET", "/api/browse?collection_id=" + state.cid + (state.fid ? "&folder_id=" + state.fid : ""));
    state.items = data.folders.concat(data.files);
    state.path = data.path;
    state.canWrite = data.can_write;
    state.claude = !!data.claude;   // Claude connected: show the Claude column
    state.canReembed = !!data.can_reembed;   // own inbox, or an admin
    state.ai = !!data.ai;   // Claude connected or Erynne's tools: show the AI edit column
    // Expand the tree down to where we are.
    state.expanded.add("c:" + state.cid);
    data.path.forEach(function (p) { state.expanded.add("f:" + p.id); });
  }

  async function loadTrash() {
    const data = await api("GET", "/api/trash?collection_id=" + state.cid);
    state.items = data.folders.map(function (f) { return Object.assign({ type: "folder" }, f); })
      .concat(data.files.map(function (f) { return Object.assign({ type: "file" }, f); }));
    state.path = [];
    state.canWrite = true;
    state.expanded.add("c:" + state.cid);
  }

  async function loadSearch() {
    const data = await api("GET", "/api/search-names?q=" + encodeURIComponent(state.q));
    state.items = data.folders.concat(data.files);
    state.path = [];
    state.canWrite = false;  // per item: can_delete / location writable
  }

  // ------------------------------------------------------------------- tree
  async function loadLocations() {
    const data = await api("GET", "/api/locations");
    state.locations = data.locations;
  }

  function treeNode(label, opts) {
    const li = el("li");
    li.setAttribute("role", "treeitem");
    const row = el("div", "tree-row" + (opts.current ? " current" : ""));
    const toggle = el("button", "tree-toggle", opts.expandable ? (opts.expanded ? "▾" : "▸") : "");
    toggle.type = "button";
    toggle.tabIndex = -1;
    toggle.disabled = !opts.expandable;
    toggle.setAttribute("aria-label", opts.expanded ? "Collapse" : "Expand");
    if (opts.expandable) toggle.addEventListener("click", opts.onToggle);
    const btn = el("button", "tree-label", label);
    btn.type = "button";
    btn.style.paddingLeft = (0.3 + opts.depth * 0.85) + "rem";
    btn.addEventListener("click", opts.onOpen);
    if (opts.current) btn.setAttribute("aria-current", "page");
    row.append(toggle, btn);
    if (opts.drop) makeDropTarget(row, opts.drop);
    li.appendChild(row);
    if (opts.expandable) li.setAttribute("aria-expanded", String(!!opts.expanded));
    return li;
  }

  async function renderTree() {
    const tree = $("tree");
    const frag = document.createDocumentFragment();
    for (const loc of state.locations) {
      const ck = "c:" + loc.id;
      const expanded = state.expanded.has(ck);
      const node = treeNode((loc.kind === "private" ? "🏠 " : "📚 ") + loc.name, {
        depth: 0, expandable: true, expanded: expanded,
        current: state.mode === "browse" && state.cid === loc.id && !state.fid,
        onToggle: function () { toggleNode(ck); },
        onOpen: function () { go({ mode: "browse", cid: loc.id, fid: null }); },
        drop: loc.writable ? { cid: loc.id, fid: null } : null,
      });
      if (expanded) {
        let folders = [];
        try { folders = await window.Erynne.folders(loc.id); } catch (e) { /* ignore */ }
        const kids = folderBranch(loc, folders, null, 1);
        if (kids) node.appendChild(kids);
        if (loc.editable) {
          const bin = treeNode("🗑️ Recycle bin" + (loc.trash ? " (" + loc.trash + ")" : ""), {
            depth: 1, expandable: false, current: state.mode === "trash" && state.cid === loc.id,
            onOpen: function () { go({ mode: "trash", cid: loc.id }); },
          });
          bin.classList.add("tree-bin");
          let group = node.querySelector(":scope > ul");
          if (!group) { group = el("ul"); group.setAttribute("role", "group"); node.appendChild(group); }
          group.appendChild(bin);
        }
      }
      frag.appendChild(node);
    }
    tree.replaceChildren(frag);
  }

  function folderBranch(loc, folders, parentId, depth) {
    const children = folders.filter(function (f) { return (f.parent_id || null) === parentId; });
    if (!children.length) return null;
    const ul = el("ul");
    ul.setAttribute("role", "group");
    children.forEach(function (f) {
      const fk = "f:" + f.id;
      const hasKids = folders.some(function (x) { return x.parent_id === f.id; });
      const expanded = state.expanded.has(fk);
      const node = treeNode((f.role === "agents" ? "🤖 " : "📁 ") + f.name, {
        depth: depth, expandable: hasKids, expanded: expanded,
        current: state.mode === "browse" && state.fid === f.id,
        onToggle: function () { toggleNode(fk); },
        onOpen: function () { go({ mode: "browse", cid: loc.id, fid: f.id }); },
        drop: f.can_write ? { cid: loc.id, fid: f.id } : null,
      });
      if (expanded && hasKids) node.appendChild(folderBranch(loc, folders, f.id, depth + 1));
      ul.appendChild(node);
    });
    return ul;
  }

  function toggleNode(k) {
    if (state.expanded.has(k)) state.expanded.delete(k); else state.expanded.add(k);
    renderTree();
  }

  async function treeChanged(collectionIds) {
    (collectionIds || [state.cid]).forEach(function (c) { window.Erynne.forgetFolders(c); });
    await loadLocations();  // bin counts
  }

  // ------------------------------------------------------------------- view
  function sorted(items) {
    const by = state.sort;
    const val = function (i) {
      if (by === "date") return i.updated_at || i.deleted_at || i.created_at || "";
      if (by === "size") return i.size || 0;
      if (by === "kind") return kindLabel(i);
      return i.name.toLowerCase();
    };
    return items.slice().sort(function (a, b) {
      if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
      const x = val(a), y = val(b);
      if (x < y) return by === "date" || by === "size" ? 1 : -1;
      if (x > y) return by === "date" || by === "size" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }

  function renderCrumbs() {
    const nav = $("crumbs");
    nav.replaceChildren();
    if (state.mode === "search") {
      nav.appendChild(el("span", "crumb current", "Search results for “" + state.q + "”"));
      return;
    }
    const loc = locationOf(state.cid);
    const root = el("button", "crumb", loc ? loc.name : "");
    root.type = "button";
    root.addEventListener("click", function () { go({ mode: "browse", cid: state.cid, fid: null }); });
    makeDropTarget(root, loc && loc.writable ? { cid: state.cid, fid: null } : null);
    nav.appendChild(root);
    if (state.mode === "trash") {
      nav.appendChild(el("span", "crumb-sep", "›"));
      nav.appendChild(el("span", "crumb current", "Recycle bin"));
      return;
    }
    (state.path || []).forEach(function (p, i) {
      nav.appendChild(el("span", "crumb-sep", "›"));
      const last = i === state.path.length - 1;
      const c = el(last ? "span" : "button", "crumb" + (last ? " current" : ""), p.name);
      if (!last) {
        c.type = "button";
        c.addEventListener("click", function () { go({ mode: "browse", cid: state.cid, fid: p.id }); });
        makeDropTarget(c, folderWritable(state.cid, p.id) ? { cid: state.cid, fid: p.id } : null);
      }
      nav.appendChild(c);
    });
  }

  function renderView() {
    renderCrumbs();
    const list = $("list");
    const claudeCol = state.mode === "browse" && state.claude;
    const aiCol = state.mode === "browse" && state.ai;
    list.className = "fm-list " + state.view + (state.mode === "trash" ? " trash" : "")
      + (state.mode === "search" ? " search" : "") + (claudeCol ? " with-claude" : "") + (aiCol ? " with-ai" : "");
    const items = sorted(state.items);
    state.items = items;
    const frag = document.createDocumentFragment();

    if (state.view === "list") {
      const head = el("div", "row head");
      head.append(el("span", "c-check"), el("span", "c-name", "Name"));
      if (state.mode === "trash") head.append(el("span", "c-where", "Original location"),
        el("span", "c-who", "Deleted by"), el("span", "c-date", "Deleted"));
      else {
        head.append(el("span", "c-status", state.mode === "search" ? "Location" : "Status"));
        if (claudeCol) {
          const h = el("span", "c-claude", "Claude");
          h.title = "Ticked: Claude can see it. Untick to keep it from Claude (Erynne still sees it).";
          head.appendChild(h);
        }
        if (aiCol) {
          const h = el("span", "c-claude", "AI edit");
          h.title = "Ticked: Claude and Erynne may change it. In your own files you can switch it on; " +
            "elsewhere an admin decides and you can only make things read-only.";
          head.appendChild(h);
        }
        head.append(el("span", "c-size", "Size"), el("span", "c-date", "Modified"));
      }
      frag.appendChild(head);
    }
    items.forEach(function (item, index) { frag.appendChild(renderItem(item, index)); });
    list.replaceChildren(frag);

    const note = $("fm-note");
    note.hidden = state.mode !== "trash";
    note.textContent = state.mode === "trash"
      ? "Deleted items stay here for " + TRASH_DAYS + " days, then they're removed for good. They don't show up in answers."
      : "";
    const empty = $("fm-empty");
    empty.hidden = items.length > 0;
    empty.textContent = state.mode === "trash" ? "The recycle bin is empty."
      : state.mode === "search" ? "Nothing matches that name."
      : state.canWrite ? "This folder is empty. Drop files here, or use Upload." : "This folder is empty.";
    updateToolbar();
    renderInfo();
  }

  // The Claude column: ticked when Claude can see the file or folder. Unticking keeps it (and, for a
  // folder, everything in it) from Claude; it can't share what an admin hasn't shared with Claude.
  function claudeCell(item) {
    const cell = el("span", "c-claude");
    const c = item.claude;
    if (!c) return cell;
    const box = el("input");
    box.type = "checkbox";
    box.checked = c.on;
    box.disabled = !c.can_change;
    const describe = function () {
      box.setAttribute("aria-label", (c.on ? "Claude can see " : "Claude can't see ") + item.name);
      cell.title = !c.shared ? "Not shared with Claude here (an admin setting, or a folder above is hidden from Claude)"
        : !c.can_change ? (c.on ? "Claude can see this. " : "Hidden from Claude. ") + "Only people who can change this folder can change that."
        : c.on ? "Claude can see this. Untick to keep it from Claude." : "Hidden from Claude. Tick to let Claude see it.";
    };
    describe();
    box.addEventListener("click", function (e) { e.stopPropagation(); });
    box.addEventListener("dblclick", function (e) { e.stopPropagation(); });
    box.addEventListener("change", async function () {
      const want = box.checked;
      box.disabled = true;
      try {
        await api("PUT", "/api/" + (item.type === "file" ? "files/" : "folders/") + item.id + "/claude", { visible: want });
        c.on = want;
      } catch (err) {
        box.checked = c.on;
        toast(err.message, true);
      } finally {
        box.disabled = !c.can_change;
        describe();
      }
    });
    cell.appendChild(box);
    return cell;
  }

  // The AI edit column: ticked when Claude (and Erynne's proposals) may change the file, or files in the
  // folder. In your own files ticking allows it; elsewhere it can only make things read-only.
  function aiEditCell(item) {
    const cell = el("span", "c-claude");
    const s = item.ai_edit;
    if (!s) return cell;
    const box = el("input");
    box.type = "checkbox";
    box.checked = s.on;
    box.disabled = !s.can_change;
    const describe = function () {
      box.setAttribute("aria-label", (s.on ? "The AI may change " : "The AI can't change ") + item.name);
      cell.title = !s.shared ? (s.why || "The AI can't edit here.")
        : !s.can_change ? "Only people who can change this folder can change that."
        : s.on ? "The AI may change this. Untick to make it read-only for the AI."
        : "Read-only for the AI. Tick to let Claude and Erynne change it.";
    };
    describe();
    box.addEventListener("click", function (e) { e.stopPropagation(); });
    box.addEventListener("dblclick", function (e) { e.stopPropagation(); });
    box.addEventListener("change", async function () {
      const want = box.checked;
      box.disabled = true;
      try {
        await api("PUT", "/api/" + (item.type === "file" ? "files/" : "folders/") + item.id + "/ai-edit", { allowed: want });
        s.on = want;
        if (item.type === "folder") await refresh(true);   // what's inside follows
      } catch (err) {
        box.checked = s.on;
        toast(err.message, true);
      } finally {
        box.disabled = !s.can_change;
        describe();
      }
    });
    cell.appendChild(box);
    return cell;
  }

  function renderItem(item, index) {
    const sel = state.selected.has(key(item));
    const row = el("div", (state.view === "list" ? "row" : "tile") + (sel ? " selected" : "")
      + (item.type === "file" ? " status-" + (item.status || "") : " is-folder"));
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(sel));
    row.dataset.index = index;
    row.title = item.name;

    const check = el("input", "c-check");
    check.type = "checkbox";
    check.checked = sel;
    check.tabIndex = -1;
    check.setAttribute("aria-label", "Select " + item.name);
    check.addEventListener("click", function (e) { e.stopPropagation(); toggleSelect(item, index); });

    const name = el("span", "c-name");
    name.append(el("span", "ico", icon(item)), el("span", "nm", item.name));

    if (state.view === "grid") {
      row.append(check, el("span", "ico big", icon(item)), el("span", "nm", item.name));
      if (item.type === "file" && item.status && item.status !== "ready" && state.mode !== "trash") {
        row.appendChild(el("span", "badge " + item.status, STATUS[item.status] || item.status));
      }
    } else if (state.mode === "trash") {
      row.append(check, name, el("span", "c-where muted", item.original_path || "/"),
        el("span", "c-who muted", item.deleted_by || ""), el("span", "c-date muted", dateLabel(item.deleted_at)));
    } else {
      const status = el("span", "c-status");
      if (state.mode === "search") {
        const loc = locationOf(item.collection_id);
        status.textContent = [loc ? loc.name : ""].concat((item.path || []).map(function (p) { return p.name; })).join(" / ");
        status.classList.add("muted");
      } else if (item.type === "file") {
        status.appendChild(el("span", "badge " + item.status, statusText(item)));
        if (item.status === "failed" && item.error) status.title = item.error;
      } else {
        status.appendChild(el("span", "muted small", item.items + (item.items === 1 ? " item" : " items")));
      }
      row.append(check, name, status);
      if (state.mode === "browse" && state.claude) row.appendChild(claudeCell(item));
      if (state.mode === "browse" && state.ai) row.appendChild(aiEditCell(item));
      row.append(el("span", "c-size muted", item.type === "file" ? sizeLabel(item.size) : ""),
        el("span", "c-date muted", dateLabel(item.updated_at || item.created_at)));
    }

    row.addEventListener("click", function (e) { clickSelect(item, index, e); });
    row.addEventListener("dblclick", function () { openItem(item); });
    row.addEventListener("contextmenu", function (e) {
      e.preventDefault();
      if (!state.selected.has(key(item))) { state.selected = new Set([key(item)]); state.anchor = index; paintSelection(); }
      showContextMenu(e.clientX, e.clientY);
    });
    if (state.mode !== "trash") {
      row.draggable = true;
      row.addEventListener("dragstart", function (e) {
        if (!state.selected.has(key(item))) { state.selected = new Set([key(item)]); paintSelection(); }
        const payload = selectedItems().map(function (i) {
          return { type: i.type, id: i.id, collection_id: i.collection_id || state.cid };
        });
        e.dataTransfer.setData(ITEMS_MIME, JSON.stringify(payload));
        e.dataTransfer.effectAllowed = "copyMove";
      });
      if (item.type === "folder" && state.mode === "browse" && state.canWrite) {
        makeDropTarget(row, { cid: state.cid, fid: item.id });
      }
    }
    return row;
  }

  // -------------------------------------------------------------- selection
  function clickSelect(item, index, e) {
    const k = key(item);
    if (e.shiftKey && state.anchor !== null) {
      const [a, b] = [Math.min(state.anchor, index), Math.max(state.anchor, index)];
      if (!(e.ctrlKey || e.metaKey)) state.selected.clear();
      for (let i = a; i <= b; i++) state.selected.add(key(state.items[i]));
    } else if (e.ctrlKey || e.metaKey) {
      if (state.selected.has(k)) state.selected.delete(k); else state.selected.add(k);
      state.anchor = index;
    } else {
      state.selected = new Set([k]);
      state.anchor = index;
    }
    paintSelection();
  }

  function toggleSelect(item, index) {
    const k = key(item);
    if (state.selected.has(k)) state.selected.delete(k); else state.selected.add(k);
    state.anchor = index;
    paintSelection();
  }

  function paintSelection() {
    $("list").querySelectorAll("[data-index]").forEach(function (node) {
      const item = state.items[Number(node.dataset.index)];
      const sel = state.selected.has(key(item));
      node.classList.toggle("selected", sel);
      node.setAttribute("aria-selected", String(sel));
      const cb = node.querySelector("input.c-check");
      if (cb) cb.checked = sel;
    });
    updateToolbar();
    renderInfo();
  }

  /* Edit access to a folder, from the tree's folder list when it's loaded
     (access can differ from folder to folder; unknown counts as no). */
  function folderWritable(cid, fid) {
    const list = window.Erynne.cachedFolders(cid) || [];
    const f = list.find(function (x) { return x.id === fid; });
    return !!(f && f.can_write);
  }

  function canWriteItem(item) {
    if (state.mode === "browse" || state.mode === "trash") return state.canWrite;
    if (item.type === "file") return !!item.can_delete;
    return folderWritable(item.collection_id, item.id);
  }

  function updateToolbar() {
    const items = selectedItems();
    const anyWrite = items.length > 0 && items.every(canWriteItem);
    document.querySelectorAll(".fm-toolbar [data-mode]").forEach(function (node) {
      node.hidden = !node.dataset.mode.split(" ").includes(state.mode);
    });
    document.querySelectorAll(".fm-toolbar [data-needs]").forEach(function (node) {
      const needs = node.dataset.needs.split(" ");
      let ok = true;
      if (needs.includes("write") && !(items.length ? anyWrite : state.canWrite)) ok = false;
      if (needs.includes("one") && items.length !== 1) ok = false;
      if (needs.includes("some") && items.length === 0) ok = false;
      if (needs.includes("files") && (!items.length || items.some(function (i) { return i.type !== "file"; }))) ok = false;
      if (needs.includes("reembed") && !state.canReembed) ok = false;
      node.disabled = !ok;
    });
    const empty = document.querySelector('[data-act="empty-bin"]');
    if (empty) empty.disabled = state.items.length === 0;
    document.querySelectorAll("[data-view]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.view === state.view));
    });
    $("btn-info").setAttribute("aria-pressed", String(state.info));
  }

  // ---------------------------------------------------------------- actions
  function openItem(item) {
    if (state.mode === "trash") return;
    if (item.type === "folder") {
      go({ mode: "browse", cid: item.collection_id || state.cid, fid: item.id });
      return;
    }
    if (item.view_url) window.open(item.view_url, "_blank", "noopener");
    else window.location.href = item.download_url;
  }

  function refs(items) { return items.map(function (i) { return { type: i.type, id: i.id }; }); }

  async function act(name) {
    const items = selectedItems();
    try {
      switch (name) {
        case "upload-files": $("pick-files").click(); break;
        case "upload-folder": $("pick-folder").click(); break;
        case "new-folder": await newFolder(); break;
        case "open": if (items[0]) openItem(items[0]); break;
        case "download": items.forEach(function (i, n) {
          setTimeout(function () {
            const a = el("a"); a.href = i.download_url; a.download = ""; document.body.appendChild(a); a.click(); a.remove();
          }, n * 300);
        }); break;
        case "rename": await renameItem(items[0]); break;
        case "move": await moveOrCopy(items, "move"); break;
        case "copy": await moveOrCopy(items, "copy"); break;
        case "trash": await trashItems(items); break;
        case "restore": await batch("restore", items, "restore"); break;
        case "purge": await purgeItems(items); break;
        case "empty-bin": await purgeItems(state.items, true); break;
        case "retry": await api("POST", "/api/files/" + items[0].id + "/retry"); await refresh(true); break;
        case "reembed": await reembed(items); break;
        case "properties": if (items[0]) await showProperties(items[0]); break;
        case "properties-here": await showProperties({ type: "folder", id: state.fid, name: "" }); break;
      }
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function batch(op, items, verb, target) {
    const result = await api("POST", "/api/items/" + op, { items: refs(items), target: target });
    reportBatch(result, verb);
    const touched = new Set([state.cid]);
    if (target) touched.add(target.collection_id);
    items.forEach(function (i) { if (i.collection_id) touched.add(i.collection_id); });
    await treeChanged(Array.from(touched));
    state.selected.clear();
    await refresh();
    return result;
  }

  function askName(title, value) {
    const dlg = $("dlg-name");
    $("dlg-name-title").textContent = title;
    const input = $("dlg-name-input");
    input.value = value || "";
    $("dlg-name-error").textContent = "";
    return new Promise(function (resolve) {
      let confirmed = false;
      const form = dlg.querySelector("form");
      function onSubmit() { confirmed = true; }
      form.addEventListener("submit", onSubmit, { once: true });
      dlg.addEventListener("close", function () {
        form.removeEventListener("submit", onSubmit);
        resolve(confirmed ? input.value.trim() : null);
      }, { once: true });
      dlg.showModal();
      // Select the name without its extension, like a desktop file manager.
      const dot = input.value.lastIndexOf(".");
      input.setSelectionRange(0, dot > 0 ? dot : input.value.length);
    });
  }

  function confirmDialog(title, text, okLabel) {
    const dlg = $("dlg-confirm");
    $("dlg-confirm-title").textContent = title;
    $("dlg-confirm-text").textContent = text;
    $("dlg-confirm-ok").textContent = okLabel || "Delete";
    return new Promise(function (resolve) {
      let confirmed = false;
      const form = dlg.querySelector("form");
      function onSubmit() { confirmed = true; }
      form.addEventListener("submit", onSubmit, { once: true });
      dlg.addEventListener("close", function () {
        form.removeEventListener("submit", onSubmit);
        resolve(confirmed);
      }, { once: true });
      dlg.showModal();
    });
  }

  async function newFolder() {
    const name = await askName("New folder", "New folder");
    if (!name) return;
    const made = await api("POST", "/api/folders", { collection_id: state.cid, parent_id: state.fid, name: name });
    await treeChanged();
    if (state.fid) state.expanded.add("f:" + state.fid);
    state.expanded.add("c:" + state.cid);
    await refresh();
    state.selected = new Set(["folder:" + made.id]);
    paintSelection();
  }

  async function renameItem(item) {
    if (!item) return;
    const name = await askName("Rename", item.name);
    if (!name || name === item.name) return;
    await api("PATCH", "/api/" + (item.type === "file" ? "files/" : "folders/") + item.id, { name: name });
    if (item.type === "folder") await treeChanged([item.collection_id || state.cid]);
    await refresh(true);
  }

  async function moveOrCopy(items, op, target) {
    if (!items.length) return;
    if (!target) {
      target = await window.Erynne.pickLocation({
        title: (op === "move" ? "Move " : "Copy ") + (items.length === 1 ? "“" + items[0].name + "”" : items.length + " items") + " to…",
        ok: op === "move" ? "Move here" : "Copy here", writableOnly: true,
        current: { collection_id: state.cid, folder_id: state.fid },
      });
      if (!target) return;
    }
    const result = await batch(op, items, op, { collection_id: target.collection_id, folder_id: target.folder_id });
    const done = result.results.filter(function (r) { return r.ok; }).length;
    if (done) toast((op === "move" ? "Moved " : "Copied ") + done + (done === 1 ? " item" : " items")
      + (target.label ? " to " + target.label : "") + ".");
  }

  async function trashItems(items) {
    if (!items.length) return;
    const result = await batch("trash", items, "delete");
    const done = result.results.filter(function (r) { return r.ok; }).length;
    if (done) toast("Moved " + done + (done === 1 ? " item" : " items") + " to the recycle bin.");
  }

  async function purgeItems(items, all) {
    if (!items.length) return;
    const ok = await confirmDialog(all ? "Empty the recycle bin?" : "Delete forever?",
      (all ? "Everything in the bin" : items.length === 1 ? "“" + items[0].name + "”" : items.length + " items")
      + " will be deleted for good. This can't be undone.", "Delete forever");
    if (ok) await batch("purge", items, "delete");
  }

  // ------------------------------------------------------ re-embedding
  /* Read and embed again: the selected files and folders, or everything here (the folder, or the whole
     library at its top). Extracted text and OCR are kept; only chunks and embeddings are redone. */
  async function reembed(items) {
    const here = state.fid ? "this folder" : (locationOf(state.cid) || {}).name || "this library";
    const what = items.length === 1 ? "“" + items[0].name + "”" : items.length ? items.length + " items" : "everything in " + here;
    const ok = await confirmDialog("Re-embed " + what + "?", "Files are read and embedded again in the background. " +
      "They can't be found in answers until they're Ready again.", "Re-embed");
    if (!ok) return;
    let targets = items;
    if (!items.length && state.fid) targets = [{ type: "folder", id: state.fid }];
    const out = await api("POST", "/api/reembed", { collection_id: state.cid,
      items: targets.map(function (i) { return { type: i.type, id: i.id }; }) });
    toast(out.queued ? out.queued + (out.queued === 1 ? " file is" : " files are") + " being embedded again."
      : "Nothing to re-embed: those files are already queued, or can't be searched.");
    await refresh(true);
  }

  // ---------------------------------------------------------- properties
  function propRow(table, label, value) {
    if (value === null || value === undefined || value === "") return;
    const tr = el("tr");
    tr.append(el("th", null, label));
    const td = el("td");
    if (value instanceof Node) td.appendChild(value); else td.textContent = value;
    tr.appendChild(td);
    table.appendChild(tr);
  }

  /* A Claude switch in the Properties dialog: "see" or "edit". */
  function claudeSwitch(item, kind, state_, label) {
    const wrap = el("div", "props-switch");
    const box = el("input");
    box.type = "checkbox";
    box.id = "props-" + kind;
    box.checked = state_.on;
    box.disabled = !state_.can_change;
    const l = el("label", null, label);
    l.htmlFor = box.id;
    const note = el("p", "small muted");
    const describe = function () {
      note.textContent = !state_.shared ? (state_.why || "Not shared with Claude here (an admin setting, or a folder above).")
        : !state_.can_change ? "Only people who can change this folder can change this."
        : kind === "see" ? (state_.on ? "Untick to keep it" + (item.type === "folder" ? ", and everything in it," : "") + " from Claude. Erynne still sees it."
                                      : "Hidden from Claude. Tick to let Claude see it again.")
        : (state_.on ? "Untick to make it read-only for Claude and Erynne" + (item.type === "folder" ? " (everything in it)." : ".")
                     : "Read-only for the AI. Tick to let Claude and Erynne change it" + (item.type === "folder" ? " (everything in it)." : "."));
    };
    describe();
    box.addEventListener("change", async function () {
      const want = box.checked;
      box.disabled = true;
      $("props-error").textContent = "";
      const path = "/api/" + (item.type === "file" ? "files/" : "folders/") + item.id + (kind === "see" ? "/claude" : "/ai-edit");
      try {
        await api("PUT", path, kind === "see" ? { visible: want } : { allowed: want });
        state_.on = want;
        await refresh(true);   // the Claude and AI edit columns follow
      } catch (e) {
        box.checked = state_.on;
        $("props-error").textContent = e.message;
      } finally {
        box.disabled = !state_.can_change;
        describe();
      }
    });
    wrap.append(box, l);
    const block = el("div");
    block.append(wrap, note);
    return block;
  }

  async function showProperties(item) {
    const dlg = $("dlg-props");
    const body = $("props-body");
    $("props-error").textContent = "";
    body.replaceChildren(el("p", "muted", "Loading…"));
    $("props-title").textContent = item.name || "Properties";
    if (!dlg.open) dlg.showModal();
    let p;
    try { p = await api("GET", "/api/properties?type=" + item.type + "&id=" + item.id); }
    catch (e) { body.replaceChildren(el("p", "error", e.message)); return; }
    $("props-title").textContent = p.name;
    body.replaceChildren();

    const general = el("table", "props-table");
    propRow(general, "Type", p.type === "folder" ? "Folder" : (p.kind || "file").toUpperCase() + (p.mime ? " · " + p.mime : ""));
    propRow(general, "Where", p.where || "/");
    if (p.type === "file") {
      propRow(general, "Size", sizeLabel(p.size) + (p.pages ? " · " + p.pages + " page" + (p.pages === 1 ? "" : "s") : ""));
      propRow(general, "Uploaded by", p.uploaded_by);
      propRow(general, "Added", dateLabel(p.created_at));
      propRow(general, "Changed", dateLabel(p.updated_at));
      if (p.edited_via) propRow(general, "Last change via", p.edited_via.replace(":", " for "));
    } else {
      propRow(general, "Contains", p.files + (p.files === 1 ? " file" : " files") + (p.folders ? ", " + p.folders + (p.folders === 1 ? " folder" : " folders") : ""));
      propRow(general, "Created", dateLabel(p.created_at));
    }
    body.append(el("h3", "props-h", "General"), general);

    const search = el("table", "props-table");
    if (p.type === "file") {
      const s = p.search;
      const badge = el("span", "badge " + s.status, statusText(Object.assign({}, p, { status: s.status, progress: s.progress })));
      propRow(search, "Status", badge);
      propRow(search, "Passages", s.chunks ? s.chunks + " (" + s.embedded + " embedded)" : s.status === "stored" ? "Stored only: this kind of file can't be searched" : "None yet");
      if (s.error) propRow(search, "Error", s.error);
    } else {
      const parts = Object.keys(p.search.by_status).sort().map(function (k) {
        return p.search.by_status[k] + " " + (STATUS[k] || k).toLowerCase();
      });
      propRow(search, "Files", parts.length ? parts.join(", ") : "None");
      propRow(search, "Passages", String(p.search.chunks));
    }
    body.append(el("h3", "props-h", "Search"), search);
    if (p.can_reembed) {
      const b = el("button", "small", "Re-embed" + (p.type === "folder" ? " everything in it" : ""));
      b.type = "button";
      b.addEventListener("click", async function () {
        b.disabled = true;
        try {
          const out = await api("POST", "/api/reembed", { collection_id: p.collection_id, items: [{ type: p.type, id: p.id }] });
          toast(out.queued ? out.queued + (out.queued === 1 ? " file is" : " files are") + " being embedded again."
            : "Nothing to re-embed right now.");
          await refresh(true);
          await showProperties(p);
        } catch (e) { $("props-error").textContent = e.message; b.disabled = false; }
      });
      const actions = el("div", "props-actions");
      actions.appendChild(b);
      body.appendChild(actions);
    }

    body.appendChild(el("h3", "props-h", "Claude and AI"));
    if (!p.claude_connected) {
      body.appendChild(el("p", "small muted", "Connect Claude on the Claude page to use Claude. These apply to everyone's Claude."));
    }
    body.append(claudeSwitch(p, "see", p.claude_see, "Claude can see " + (p.type === "folder" ? "this folder" : "this file")),
                claudeSwitch(p, "edit", p.ai_edit, "AI can edit " + (p.type === "folder" ? "files in it" : "this file")));
  }

  // ----------------------------------------------------------- context menu
  function showContextMenu(x, y) {
    const menu = $("ctx");
    menu.replaceChildren();
    const items = selectedItems();
    const add = function (label, action, enabled, danger) {
      const b = el("button", danger ? "danger" : null, label);
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.disabled = enabled === false;
      b.addEventListener("click", function () { hideMenus(); act(action); });
      menu.appendChild(b);
    };
    const write = items.length ? items.every(canWriteItem) : state.canWrite;
    if (state.mode === "trash") {
      add("Restore", "restore", items.length > 0);
      add("Delete forever", "purge", items.length > 0, true);
    } else if (items.length) {
      add("Open", "open", items.length === 1);
      add("Download", "download", items.every(function (i) { return i.type === "file"; }));
      if (items.length === 1 && items[0].type === "file" && items[0].status === "failed" && write) add("Retry indexing", "retry");
      if (state.mode === "browse" && state.canReembed) add("Re-embed", "reembed");
      menu.appendChild(el("hr"));
      add("Rename", "rename", items.length === 1 && write);
      add("Move to…", "move", write);
      add("Copy to…", "copy");
      menu.appendChild(el("hr"));
      add("Delete", "trash", write, true);
      menu.appendChild(el("hr"));
      add("Properties…", "properties", items.length === 1);
    } else {
      add("New folder", "new-folder", state.mode === "browse" && state.canWrite);
      add("Upload files…", "upload-files", state.mode === "browse" && state.canWrite);
      add("Upload a folder…", "upload-folder", state.mode === "browse" && state.canWrite);
      if (state.mode === "browse" && state.canReembed) {
        menu.appendChild(el("hr"));
        add("Re-embed everything here", "reembed");
      }
      if (state.mode === "browse" && state.fid) add("Properties of this folder…", "properties-here");
    }
    menu.hidden = false;
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.min(x, window.innerWidth - r.width - 8) + "px";
    menu.style.top = Math.min(y, window.innerHeight - r.height - 8) + "px";
    const first = menu.querySelector("button:not([disabled])");
    if (first) first.focus();
  }

  function hideMenus() {
    $("ctx").hidden = true;
    $("upload-menu").hidden = true;
    $("btn-upload").setAttribute("aria-expanded", "false");
  }

  // ------------------------------------------------------------ info pane
  let infoToken = 0;
  async function renderInfo() {
    const pane = $("info");
    pane.hidden = !state.info;
    if (!state.info) return;
    const items = selectedItems();
    const token = ++infoToken;
    pane.replaceChildren();
    if (items.length !== 1) {
      pane.appendChild(el("p", "muted", items.length ? items.length + " items selected." : "Select a file to see its details and a preview."));
      return;
    }
    const item = items[0];
    const head = el("div", "info-head");
    head.append(el("span", "ico big", icon(item)), el("h3", null, item.name));
    pane.appendChild(head);

    if (item.type === "folder") {
      const dl = el("dl");
      addRow(dl, "Type", item.role === "agents" ? "Agents folder" : "Folder");
      if (item.role === "agents") {
        pane.appendChild(el("p", "note small", "Claude can create and change files in here for you (with a token " +
          "that's allowed to edit). Every change keeps the previous version under Versions."));
      }
      if (item.items !== undefined) addRow(dl, "Contains", item.items + (item.items === 1 ? " item" : " items"));
      if (item.deleted_at) addRow(dl, "Deleted", dateLabel(item.deleted_at) + (item.deleted_by ? " by " + item.deleted_by : ""));
      pane.appendChild(dl);
      return;
    }
    if (state.mode === "trash") {
      const dl = el("dl");
      addRow(dl, "Type", kindLabel(item));
      addRow(dl, "Size", sizeLabel(item.size));
      addRow(dl, "Was in", item.original_path || "/");
      addRow(dl, "Deleted", dateLabel(item.deleted_at) + (item.deleted_by ? " by " + item.deleted_by : ""));
      pane.appendChild(dl);
      return;
    }

    let full = item;
    try { full = await api("GET", "/api/files/" + item.id); } catch (e) { /* show what we have */ }
    if (token !== infoToken) return;

    const preview = el("div", "preview");
    if (full.preview === "image") {
      const img = el("img");
      img.src = full.view_url;
      img.alt = full.name;
      preview.appendChild(img);
    } else if (full.preview) {
      const frame = el("iframe");
      frame.src = full.view_url + (full.preview === "pdf" ? "#view=FitH" : "");
      frame.title = "Preview of " + full.name;
      frame.setAttribute("loading", "lazy");
      if (full.preview === "text") frame.setAttribute("sandbox", "");
      preview.appendChild(frame);
    } else {
      preview.appendChild(el("p", "muted", "No preview for this kind of file."));
    }
    pane.appendChild(preview);

    const actions = el("div", "info-actions");
    if (full.view_url) {
      const open = el("a", "button-like", "Open");
      open.href = full.view_url; open.target = "_blank"; open.rel = "noopener";
      actions.appendChild(open);
    }
    const dl0 = el("a", "button-like soft", "Download");
    dl0.href = full.download_url;
    actions.appendChild(dl0);
    pane.appendChild(actions);

    const dl = el("dl");
    addRow(dl, "Type", kindLabel(full));
    addRow(dl, "Size", sizeLabel(full.size));
    if (full.pages) addRow(dl, "Pages", String(full.pages));
    const status = el("span", "badge " + full.status, statusText(full));
    addRow(dl, "Search", status);
    if (full.status === "ready" && full.chunks !== undefined) addRow(dl, "Indexed", full.chunks + (full.chunks === 1 ? " passage" : " passages"));
    const loc = locationOf(full.collection_id);
    addRow(dl, "Location", [loc ? loc.name : ""].concat((full.path || []).map(function (p) { return p.name; })).join(" / "));
    if (full.uploaded_by) addRow(dl, "Uploaded by", full.uploaded_by);
    addRow(dl, "Added", dateLabel(full.created_at));
    if (full.updated_at && full.updated_at !== full.created_at) addRow(dl, "Modified", dateLabel(full.updated_at));
    const via = /^(claude|erynne):(.*)$/.exec(full.edited_via || "");
    if (via) {
      addRow(dl, "Last change", (via[1] === "claude" ? "🤖 via Claude for " : "🌸 by Erynne, applied by ") + via[2]);
    }
    pane.appendChild(dl);
    if (full.error) pane.appendChild(el("p", "error small", full.error));
    if (full.notes) pane.appendChild(el("p", "note small", full.notes));
    renderVersions(pane, full, token);
  }

  /* Earlier versions kept when Claude changed the file; Restore puts one back
     (and keeps what's there now as a version, so it can be undone too). */
  async function renderVersions(pane, file, token) {
    let data;
    try { data = await api("GET", "/api/files/" + file.id + "/versions"); } catch (e) { return; }
    if (token !== infoToken || !data.versions.length) return;
    const box = el("section", "versions");
    box.appendChild(el("h4", null, "Versions"));
    const list = el("ul", "version-list");
    data.versions.forEach(function (v) {
      const li = el("li");
      li.appendChild(el("div", null, "Before " + dateLabel(v.replaced_at) + " — changed by " + v.replaced_by));
      if (v.change) li.appendChild(el("div", "muted small", v.change));
      const actions = el("div", "version-actions");
      const get = el("a", "small", "Download");
      get.href = v.download_url;
      actions.appendChild(get);
      if (data.can_restore) {
        const restore = el("button", "link small", "Restore");
        restore.type = "button";
        restore.addEventListener("click", async function () {
          if (!confirm("Put this version of “" + file.name + "” back? What's there now is kept as a version too.")) return;
          try {
            await api("POST", "/api/files/" + file.id + "/versions/" + v.id + "/restore");
            toast("Restored an earlier version of “" + file.name + "”.");
          } catch (e) { toast(e.message, true); }
          await refresh(true);
        });
        actions.appendChild(restore);
      }
      li.appendChild(actions);
      list.appendChild(li);
    });
    box.appendChild(list);
    pane.appendChild(box);
  }

  function addRow(dl, label, value) {
    dl.appendChild(el("dt", null, label));
    const dd = el("dd");
    if (typeof value === "string") dd.textContent = value; else dd.appendChild(value);
    dl.appendChild(dd);
  }

  // ---------------------------------------------------------- drag and drop
  function hasItems(e) { return Array.from(e.dataTransfer.types || []).includes(ITEMS_MIME); }
  function hasFiles(e) { return Array.from(e.dataTransfer.types || []).includes("Files"); }

  function makeDropTarget(node, target) {
    if (!target) return;
    node.addEventListener("dragover", function (e) {
      if (!hasItems(e) && !hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = hasFiles(e) || e.ctrlKey || e.metaKey ? "copy" : "move";
      node.classList.add("drop-here");
    });
    node.addEventListener("dragleave", function () { node.classList.remove("drop-here"); });
    node.addEventListener("drop", async function (e) {
      e.preventDefault();
      e.stopPropagation();
      node.classList.remove("drop-here");
      $("drop-overlay").hidden = true;
      if (hasItems(e)) {
        const dragged = JSON.parse(e.dataTransfer.getData(ITEMS_MIME) || "[]")
          .filter(function (i) { return !(i.type === "folder" && i.id === target.fid); });
        if (!dragged.length) return;
        try {
          await moveOrCopy(dragged.map(function (i) { return Object.assign({ name: "" }, i); }),
            e.ctrlKey || e.metaKey ? "copy" : "move", { collection_id: target.cid, folder_id: target.fid });
        } catch (err) { toast(err.message, true); }
      } else if (hasFiles(e)) {
        enqueueEntries(await filesFromDrop(e.dataTransfer), target.cid, target.fid);
      }
    });
  }

  // The whole list area takes external files (upload into the current folder).
  (function () {
    const main = document.querySelector(".fm-main");
    let depth = 0;
    main.addEventListener("dragenter", function (e) {
      if (!hasFiles(e) || state.mode !== "browse" || !state.canWrite) return;
      depth++;
      $("drop-overlay").hidden = false;
    });
    main.addEventListener("dragleave", function () {
      if (depth > 0 && --depth === 0) $("drop-overlay").hidden = true;
    });
    main.addEventListener("dragover", function (e) {
      if (hasFiles(e) && state.mode === "browse" && state.canWrite) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }
    });
    main.addEventListener("drop", async function (e) {
      depth = 0;
      $("drop-overlay").hidden = true;
      if (!hasFiles(e) || state.mode !== "browse" || !state.canWrite) return;
      e.preventDefault();
      enqueueEntries(await filesFromDrop(e.dataTransfer), state.cid, state.fid);
    });
  })();

  /* Files from a drop, walking dropped folders: [{file, path}] where path is
     "Folder/sub/name.pdf" for files inside a dropped folder. */
  async function filesFromDrop(dt) {
    const entries = Array.from(dt.items || [])
      .map(function (i) { return i.webkitGetAsEntry ? i.webkitGetAsEntry() : null; })
      .filter(Boolean);
    if (!entries.length) return Array.from(dt.files || []).map(function (f) { return { file: f, path: f.name }; });
    const out = [];
    async function walk(entry) {
      if (entry.isFile) {
        const file = await new Promise(function (res, rej) { entry.file(res, rej); });
        out.push({ file: file, path: entry.fullPath.replace(/^\//, "") });
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        // readEntries returns at most ~100 entries per call: keep reading.
        for (;;) {
          const batch = await new Promise(function (res, rej) { reader.readEntries(res, rej); });
          if (!batch.length) break;
          for (const child of batch) await walk(child);
        }
      }
    }
    for (const entry of entries) await walk(entry);
    return out;
  }

  // ----------------------------------------------------------- upload queue
  const queue = [];
  let active = 0;

  function enqueueEntries(entries, cid, fid) {
    if (!entries.length) return;
    entries.forEach(function (e) {
      const job = { file: e.file, path: e.path, cid: cid, fid: fid, state: "waiting", pct: 0, fileId: null,
                    detail: "", node: null };
      job.node = queueRow(job);
      $("queue-list").appendChild(job.node);
      queue.push(job);
    });
    $("queue").hidden = false;
    $("queue").classList.remove("min");
    pump();
    updateQueueTitle();
  }

  function queueRow(job) {
    const li = el("li", "q-item");
    const name = el("span", "q-name", job.path);
    const bar = el("span", "q-bar");
    bar.appendChild(el("span", "q-fill"));
    const detail = el("span", "q-detail muted small", "Waiting");
    const retry = el("button", "link small", "Retry");
    retry.type = "button";
    retry.hidden = true;
    retry.addEventListener("click", function () {
      job.state = "waiting"; job.pct = 0; job.detail = "Waiting"; paintJob(job); pump(); updateQueueTitle();
    });
    li.append(el("span", "ico", icon({ type: "file", name: job.file.name, mime: job.file.type,
      kind: /\.pdf$/i.test(job.file.name) ? "pdf" : /\.(md|markdown)$/i.test(job.file.name) ? "md" : "" })),
      name, retry, bar, detail);
    return li;
  }

  function paintJob(job) {
    const li = job.node;
    li.className = "q-item q-" + job.state;
    li.querySelector(".q-fill").style.width = Math.round(job.pct * 100) + "%";
    li.querySelector(".q-detail").textContent = job.detail;
    li.querySelector("button").hidden = job.state !== "failed" || !!job.fileId;
  }

  function updateQueueTitle() {
    const left = queue.filter(function (j) { return j.state === "waiting" || j.state === "uploading"; }).length;
    const indexing = queue.filter(function (j) { return j.state === "indexing"; }).length;
    const failed = queue.filter(function (j) { return j.state === "failed"; }).length;
    let title = left ? "Uploading " + (queue.length - left) + " of " + queue.length
      : indexing ? "Indexing " + indexing + (indexing === 1 ? " file" : " files")
      : "Uploads done";
    if (failed) title += " · " + failed + " failed";
    $("queue-title").textContent = title;
  }

  function pump() {
    while (active < UPLOAD_CONCURRENCY) {
      const job = queue.find(function (j) { return j.state === "waiting"; });
      if (!job) break;
      upload(job);
    }
  }

  function upload(job) {
    active++;
    job.state = "uploading";
    job.detail = "Uploading";
    paintJob(job);
    const form = new FormData();
    form.append("file", job.file, job.file.name);
    form.append("collection_id", job.cid);
    if (job.fid) form.append("folder_id", job.fid);
    if (job.path.indexOf("/") >= 0) form.append("relative_path", job.path);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/files");
    xhr.upload.addEventListener("progress", function (e) {
      if (e.lengthComputable) { job.pct = e.loaded / e.total; job.detail = Math.round(job.pct * 100) + "%"; paintJob(job); }
    });
    xhr.addEventListener("load", function () {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
      if (xhr.status === 200 || xhr.status === 201) {
        job.fileId = body.id;
        job.pct = 1;
        settle(job, body, xhr.status === 200);
      } else {
        job.state = "failed";
        job.detail = (body && body.detail) || "Upload failed (" + xhr.status + ")";
      }
      finish(job);
    });
    xhr.addEventListener("error", function () { job.state = "failed"; job.detail = "Network error"; finish(job); });
    xhr.send(form);
  }

  function settle(job, f, duplicate) {
    if (f.status === "ready") { job.state = "done"; job.detail = duplicate ? "Already here" : "Ready"; }
    else if (f.status === "stored") { job.state = "done"; job.detail = "Stored (not searchable)"; }
    else if (f.status === "failed") { job.state = "failed"; job.detail = f.error || "Indexing failed"; }
    else { job.state = "indexing"; job.detail = f.progress || "Waiting to be indexed"; }
  }

  let refreshSoon = null;
  function finish(job) {
    active--;
    paintJob(job);
    updateQueueTitle();
    pump();
    // Show new files (and new folders from folder uploads) without hammering the server.
    clearTimeout(refreshSoon);
    refreshSoon = setTimeout(async function () {
      await treeChanged([job.cid]);
      refresh(true);
    }, 600);
    schedulePoll();
  }

  $("queue-clear").addEventListener("click", function () {
    for (let i = queue.length - 1; i >= 0; i--) {
      if (queue[i].state === "done") { queue[i].node.remove(); queue.splice(i, 1); }
    }
    if (!queue.length) $("queue").hidden = true;
    updateQueueTitle();
  });
  $("queue-min").addEventListener("click", function () {
    const q = $("queue");
    q.classList.toggle("min");
    this.setAttribute("aria-expanded", String(!q.classList.contains("min")));
    this.textContent = q.classList.contains("min") ? "▴" : "▾";
  });

  // --------------------------------------------------------------- polling
  let pollTimer = null;
  function schedulePoll() {
    clearTimeout(pollTimer);
    const busyView = state.mode === "browse" && state.items.some(function (i) {
      return i.type === "file" && (i.status === "queued" || i.status === "processing");
    });
    const busyQueue = queue.some(function (j) { return j.state === "indexing"; });
    if (!busyView && !busyQueue) return;
    pollTimer = setTimeout(async function () {
      for (const job of queue.filter(function (j) { return j.state === "indexing"; })) {
        try { settle(job, await api("GET", "/api/files/" + job.fileId)); } catch (e) { job.state = "failed"; job.detail = e.message; }
        paintJob(job);
      }
      updateQueueTitle();
      if (busyView) await refresh(true); else schedulePoll();
    }, 2500);
  }

  // ---------------------------------------------------------------- wiring
  document.querySelector(".fm-toolbar").addEventListener("click", function (e) {
    const b = e.target.closest("[data-act]");
    if (b && !b.disabled) { hideMenus(); act(b.dataset.act); }
  });
  $("btn-upload").addEventListener("click", function (e) {
    e.stopPropagation();
    const menu = $("upload-menu");
    menu.hidden = !menu.hidden;
    this.setAttribute("aria-expanded", String(!menu.hidden));
  });
  $("pick-files").addEventListener("change", function () {
    enqueueEntries(Array.from(this.files).map(function (f) { return { file: f, path: f.name }; }), state.cid, state.fid);
    this.value = "";
  });
  $("pick-folder").addEventListener("change", function () {
    enqueueEntries(Array.from(this.files).map(function (f) {
      return { file: f, path: f.webkitRelativePath || f.name };
    }), state.cid, state.fid);
    this.value = "";
  });
  document.querySelectorAll("[data-view]").forEach(function (b) {
    b.addEventListener("click", function () { state.view = b.dataset.view; save("fm.view", state.view); renderView(); });
  });
  const sortSelect = $("fm-sort");
  sortSelect.value = state.sort;
  sortSelect.addEventListener("change", function () { state.sort = this.value; save("fm.sort", state.sort); renderView(); });
  $("btn-info").addEventListener("click", function () {
    state.info = !state.info; save("fm.info", state.info ? "1" : "0"); updateToolbar(); renderInfo();
  });

  let searchTimer = null;
  $("fm-search").addEventListener("input", function () {
    const q = this.value.trim();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      if (q) go({ mode: "search", q: q });
      else if (state.mode === "search") go({ mode: "browse", cid: state.locations[0].id, fid: null });
    }, 300);
  });

  $("list").addEventListener("click", function (e) {
    if (e.target === this) { state.selected.clear(); paintSelection(); }
  });
  $("list").addEventListener("contextmenu", function (e) {
    if (e.target !== this) return;
    e.preventDefault();
    state.selected.clear();
    paintSelection();
    showContextMenu(e.clientX, e.clientY);
  });
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".menu")) hideMenus();
  });
  document.addEventListener("scroll", hideMenus, true);
  document.querySelectorAll("dialog [data-close]").forEach(function (b) {
    b.addEventListener("click", function () { b.closest("dialog").close(); });
  });

  // Keyboard, when focus is in the list (not in a text field or dialog).
  $("list").addEventListener("keydown", function (e) {
    const items = selectedItems();
    const idx = state.anchor === null ? -1 : state.anchor;
    if (e.key === "Delete") { e.preventDefault(); act(state.mode === "trash" ? "purge" : "trash"); }
    else if (e.key === "F2" && items.length === 1 && state.mode !== "trash") { e.preventDefault(); act("rename"); }
    else if (e.key === "Enter" && items.length === 1) { e.preventDefault(); openItem(items[0]); }
    else if (e.key === "Backspace" && state.mode === "browse" && state.fid) {
      e.preventDefault();
      const parent = state.path.length > 1 ? state.path[state.path.length - 2].id : null;
      go({ mode: "browse", cid: state.cid, fid: parent });
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
      e.preventDefault();
      state.selected = new Set(state.items.map(key));
      paintSelection();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "ArrowRight" || e.key === "ArrowLeft") {
      if (!state.items.length) return;
      e.preventDefault();
      const step = (e.key === "ArrowDown" || e.key === "ArrowRight") ? 1 : -1;
      const next = Math.max(0, Math.min(state.items.length - 1, idx + step));
      clickSelect(state.items[next], next, { shiftKey: e.shiftKey, ctrlKey: false, metaKey: false });
      const node = $("list").querySelector('[data-index="' + next + '"]');
      if (node) node.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Escape") {
      state.selected.clear();
      paintSelection();
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") hideMenus();
  });

  window.addEventListener("hashchange", function () { refresh(); });
  window.addEventListener("beforeunload", function (e) {
    if (queue.some(function (j) { return j.state === "waiting" || j.state === "uploading"; })) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  (async function start() {
    try {
      await loadLocations();
    } catch (e) {
      toast(e.message, true);
      return;
    }
    await refresh();
  })();
})();
