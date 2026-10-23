/* Shared by the file manager and the chat page: a small API helper, a DOM
   helper, and a dialog for picking a location (collection + folder).
   All user-controlled text goes through textContent, never innerHTML. */
(function () {
  "use strict";

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* fetch JSON; throws Error(detail) on a non-2xx answer. */
  async function api(method, url, body) {
    const opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    let data = null;
    try { data = await resp.json(); } catch (e) { /* empty body */ }
    if (!resp.ok) {
      let detail = data && data.detail;
      if (Array.isArray(detail)) detail = detail.map(function (d) { return d.msg; }).join("; ");
      const err = new Error(detail || ("Request failed (" + resp.status + ")"));
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  const folderCache = new Map();  // collection id -> [{id, parent_id, name}]

  async function folders(collectionId, fresh) {
    if (fresh || !folderCache.has(collectionId)) {
      const data = await api("GET", "/api/collections/" + collectionId + "/folders");
      folderCache.set(collectionId, data.folders);
    }
    return folderCache.get(collectionId);
  }

  function forget(collectionId) {
    if (collectionId === undefined) folderCache.clear(); else folderCache.delete(collectionId);
  }

  /* The folders already loaded for a collection, or null (no request). */
  function cachedFolders(collectionId) {
    return folderCache.get(collectionId) || null;
  }

  /* Ask the user for a location. Resolves to {collection_id, folder_id, label},
     {all: true} (when allowAll and "All my documents" is chosen), or null. */
  async function pickLocation(options) {
    const opts = Object.assign({ title: "Choose a folder", ok: "Choose", writableOnly: false, allowAll: false,
                                 current: null }, options || {});
    const data = await api("GET", "/api/locations");
    // With writableOnly, places the user can't change stay visible (to reach
    // folders below them) but can't be chosen.
    const locations = data.locations.filter(function (l) { return l.editable || !opts.writableOnly; });
    const choosable = function (canWrite) { return canWrite || !opts.writableOnly; };

    const dlg = el("dialog", "dlg glass picker");
    const form = el("form");
    form.method = "dialog";
    const head = el("div", "picker-head");
    const all = el("button", "link small", "Expand all");
    all.type = "button";
    head.append(el("h2", null, opts.title), all);
    form.appendChild(head);
    const tree = el("ul", "tree picker-tree");
    tree.setAttribute("role", "tree");
    form.appendChild(tree);
    const actions = el("div", "dlg-actions");
    const cancel = el("button", "link", "Cancel");
    cancel.type = "button";
    const ok = el("button", null, opts.ok);
    ok.type = "submit";
    ok.disabled = true;
    actions.append(cancel, ok);
    form.appendChild(actions);
    dlg.appendChild(form);
    document.body.appendChild(dlg);

    let chosen = null;
    function choose(node, value) {
      tree.querySelectorAll(".tree-label.selected").forEach(function (n) { n.classList.remove("selected"); });
      node.classList.add("selected");
      chosen = value;
      ok.disabled = false;
    }

    // Collapsible rows: ▸/▾ for anything with folders inside; everything starts closed.
    function setOpen(li, open) {
      const kids = li.querySelector(":scope > ul");
      const toggle = li.querySelector(":scope > .tree-row > .tree-toggle");
      if (!kids || !toggle) return;
      kids.hidden = !open;
      toggle.textContent = open ? "▾" : "▸";
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", (open ? "Collapse " : "Expand ") + li.dataset.name);
    }

    function node(label, kids, depth, name) {
      const li = el("li");
      li.dataset.name = name;
      li.setAttribute("role", "treeitem");
      const row = el("div", "tree-row");
      row.style.paddingLeft = (depth * 0.9) + "rem";
      const toggle = el("button", "tree-toggle", "");
      toggle.type = "button";
      row.append(toggle, label);
      li.appendChild(row);
      if (kids && kids.childElementCount) {
        li.appendChild(kids);
        toggle.addEventListener("click", function () { setOpen(li, kids.hidden); });
        setOpen(li, false);
      } else {
        toggle.disabled = true;
        toggle.tabIndex = -1;
        toggle.setAttribute("aria-hidden", "true");
      }
      return li;
    }

    function openTo(label) {   // show the chosen place: open every row above it
      let li = label.closest("li");
      while (li && li.parentElement && li.parentElement !== tree) {
        li = li.parentElement.closest("li");
        if (li) setOpen(li, true);
      }
    }

    all.addEventListener("click", function () {
      const open = all.textContent === "Expand all";
      tree.querySelectorAll("li").forEach(function (li) { setOpen(li, open); });
      all.textContent = open ? "Collapse all" : "Expand all";
    });

    function folderNodes(loc, list, parentId, depth) {
      const ul = el("ul", "tree-children");
      ul.setAttribute("role", "group");
      list.filter(function (f) { return (f.parent_id || null) === parentId; }).forEach(function (f) {
        const label = el("button", "tree-label", "📁 " + f.name);
        label.type = "button";
        if (choosable(f.can_write)) {
          label.addEventListener("click", function () {
            choose(label, { collection_id: loc.id, folder_id: f.id, label: loc.name + " / " + f.name });
          });
        } else {
          label.disabled = true;
          label.title = "You can't change this folder";
        }
        const li = node(label, folderNodes(loc, list, f.id, depth + 1), depth, f.name);
        ul.appendChild(li);
        if (opts.current && opts.current.collection_id === loc.id && opts.current.folder_id === f.id) {
          pending.push([label, { collection_id: loc.id, folder_id: f.id, label: loc.name + " / " + f.name }]);
        }
      });
      return ul;
    }
    const pending = [];   // the current choice, selected once its row is in the tree

    if (opts.allowAll) {
      const label = el("button", "tree-label", "🌸 All my documents");
      label.type = "button";
      label.addEventListener("click", function () { choose(label, { all: true }); });
      tree.appendChild(node(label, null, 0, "All my documents"));
    }
    for (const loc of locations) {
      const label = el("button", "tree-label", (loc.kind === "private" ? "🏠 " : "📚 ") + loc.name);
      label.type = "button";
      if (choosable(loc.writable)) {
        label.addEventListener("click", function () {
          choose(label, { collection_id: loc.id, folder_id: null, label: loc.name });
        });
      } else {
        label.disabled = true;
        label.title = "You can't change the top level of this library";
      }
      let kids = null;
      try {
        kids = folderNodes(loc, await folders(loc.id), null, 1);
      } catch (e) { /* unreadable: show the root only */ }
      tree.appendChild(node(label, kids, 0, loc.name));
      if (opts.current && opts.current.collection_id === loc.id && !opts.current.folder_id) {
        choose(label, { collection_id: loc.id, folder_id: null, label: loc.name });
      }
    }
    pending.forEach(function (p) { choose(p[0], p[1]); openTo(p[0]); });

    return new Promise(function (resolve) {
      let confirmed = false;  // Escape and Cancel both close without choosing
      cancel.addEventListener("click", function () { dlg.close(); });
      form.addEventListener("submit", function () { confirmed = true; });
      dlg.addEventListener("close", function () {
        dlg.remove();
        resolve(confirmed ? chosen : null);
      });
      dlg.showModal();
    });
  }

  window.Erynne = { el: el, api: api, folders: folders, cachedFolders: cachedFolders, forgetFolders: forget,
                    pickLocation: pickLocation };
})();
