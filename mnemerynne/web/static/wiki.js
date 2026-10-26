/* The Wiki tab: the page tree of the wiki library on the left, the page on the
   right (rendered by the server: /api/wiki/pages/{id}), Edit and New page into
   the editor in another tab, which tells this one when it saved. Page text
   never goes through innerHTML except as the server's rendered HTML. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const main = $("wiki");
  if (!main) return;

  const state = {
    tree: null,
    current: main.dataset.fileId ? Number(main.dataset.fileId) : null,
    expanded: new Set(),
    folder: null,                 // the folder a new page goes in (null: the library root)
    folderWritable: main.dataset.canWrite === "yes",
  };
  const libraryId = Number(main.dataset.libraryId);

  // ----------------------------------------------------------------- tree
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
    li.appendChild(row);
    if (opts.expandable) li.setAttribute("aria-expanded", String(!!opts.expanded));
    return li;
  }

  function branch(node, depth, into) {
    node.folders.forEach(function (f) {
      const key = "f:" + f.id;
      const expanded = state.expanded.has(key);
      const li = treeNode("📁 " + f.name, {
        depth: depth, expandable: true, expanded: expanded,
        current: state.folder === f.id && state.current === null,
        onToggle: function () { if (expanded) state.expanded.delete(key); else state.expanded.add(key); renderTree(); },
        onOpen: function () {
          state.folder = f.id; state.folderWritable = !!f.can_write;
          if (!expanded) state.expanded.add(key);
          state.current = null;
          showFolder(f);
          renderTree();
        },
      });
      if (expanded) {
        const ul = el("ul");
        ul.setAttribute("role", "group");
        branch(f, depth + 1, ul);
        if (ul.childElementCount) li.appendChild(ul);
      }
      into.appendChild(li);
    });
    node.files.forEach(function (p) {
      into.appendChild(treeNode("📄 " + p.title, {
        depth: depth, expandable: false, current: p.id === state.current,
        onOpen: function () { openPage(p.id, true); },
      }));
    });
  }

  function renderTree() {
    const ul = $("wiki-tree");
    ul.replaceChildren();
    const tree = state.tree;
    if (!tree) return;
    branch(tree, 0, ul);
    $("wiki-empty").hidden = tree.folders.length > 0 || tree.files.length > 0;
    $("wiki-new-page").hidden = !state.folderWritable;
  }

  function firstPage(node) {
    const preferred = ["index.md", "home.md", "readme.md", "start.md"];
    const root = node.files.find(function (p) { return preferred.includes(p.name.toLowerCase()); });
    if (root) return root;
    if (node.files.length) return node.files[0];
    for (const f of node.folders) {
      const found = firstPage(f);
      if (found) { state.expanded.add("f:" + f.id); return found; }
    }
    return null;
  }

  function expandTo(node, id, trail) {
    for (const p of node.files) if (p.id === id) { trail.forEach(function (k) { state.expanded.add(k); }); return true; }
    for (const f of node.folders) if (expandTo(f, id, trail.concat("f:" + f.id))) return true;
    return false;
  }

  async function loadTree() {
    try {
      const data = await api("GET", "/api/wiki");
      if (!data.library) { location.reload(); return; }   // the library went away: the page explains
      state.tree = data;
      if (state.folder === null) state.folderWritable = !!data.can_write;
      if (state.current !== null) expandTo(data, state.current, []);
      renderTree();
    } catch (e) {
      $("wiki-page").replaceChildren(el("p", "error", e.message));
    }
  }

  // ----------------------------------------------------------------- page
  function showFolder(f) {
    $("wiki-head").hidden = true;
    const body = $("wiki-page");
    body.replaceChildren(el("p", "muted", f.files.length || f.folders.length
      ? "Pick a page in " + f.name + "."
      : "Nothing in " + f.name + " yet." + (f.can_write ? " Press New page to start one." : "")));
  }

  async function openPage(id, push) {
    let page;
    try {
      page = await api("GET", "/api/wiki/pages/" + id);
    } catch (e) {
      $("wiki-page").replaceChildren(el("p", "error", e.message));
      return;
    }
    state.current = id;
    state.folder = page.folder_id;
    state.folderWritable = page.can_write;
    if (push) history.pushState({ id: id }, "", "/wiki/" + id);
    document.title = page.title + " · Wiki · " + document.title.split(" · ").pop();
    const crumbs = $("wiki-crumbs");
    crumbs.replaceChildren();
    page.path.concat(page.title).forEach(function (part, i) {
      if (i) crumbs.appendChild(el("span", "muted", " / "));
      crumbs.appendChild(el("span", i === page.path.length ? "wiki-title" : null, part));
    });
    const edit = $("wiki-edit");
    edit.hidden = !page.edit_url;
    if (page.edit_url) edit.href = page.edit_url;
    const open = $("wiki-open");
    open.href = page.view_url;
    open.hidden = false;
    $("wiki-head").hidden = false;
    const body = $("wiki-page");
    body.innerHTML = page.html;   // rendered by the server from the page's Markdown (HTML in it is escaped)
    if (location.hash) {
      const target = body.querySelector(location.hash);
      if (target) target.scrollIntoView();
    } else {
      body.closest(".ax-pane").scrollTop = 0;
    }
    if (state.tree) renderTree();
  }

  // Links to other wiki pages open in place; anchors scroll within the page.
  $("wiki-page").addEventListener("click", function (e) {
    const a = e.target.closest("a[href]");
    if (!a) return;
    const href = a.getAttribute("href");
    const m = href.match(/^\/wiki\/(\d+)(#.*)?$/);
    if (m) {
      e.preventDefault();
      openPage(Number(m[1]), true).then(function () {
        if (m[2]) { const t = $("wiki-page").querySelector(m[2]); if (t) t.scrollIntoView(); }
      });
    } else if (href.startsWith("#")) {
      const t = $("wiki-page").querySelector(href);
      if (t) { e.preventDefault(); t.scrollIntoView(); }
    } else if (/^https?:\/\//.test(href)) {
      a.target = "_blank";
      a.rel = "noopener";
    }
  });

  window.addEventListener("popstate", function () {
    const m = location.pathname.match(/^\/wiki\/(\d+)$/);
    if (m) openPage(Number(m[1]), false);
  });

  // ---------------------------------------------------------- new, edited
  $("wiki-new-page").addEventListener("click", function () {
    const url = "/edit/new?collection_id=" + libraryId + (state.folder ? "&folder_id=" + state.folder : "") +
      "&back=" + encodeURIComponent(location.pathname);
    window.open(url, "_blank");
  });
  $("wiki-edit").addEventListener("click", function (e) {
    e.preventDefault();
    window.open(this.href, "_blank");
  });

  // The editor tab says when it saved; a page edited elsewhere shows up on focus.
  window.addEventListener("message", function (e) {
    if (e.origin !== location.origin || !e.data || e.data.type !== "mnemerynne-saved") return;
    loadTree().then(function () {
      if (e.data.file_id === state.current || state.current === null) openPage(e.data.file_id, state.current === null);
    });
  });
  window.addEventListener("focus", function () { loadTree(); });

  loadTree().then(function () {
    if (!state.tree) return;
    if (state.current !== null) {
      openPage(state.current, false);
    } else {
      const first = firstPage(state.tree);
      if (first) openPage(first.id, false);
    }
  });
})();
