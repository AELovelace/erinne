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

  /* Ask the user for a location. Resolves to {collection_id, folder_id, label},
     {all: true} (when allowAll and "All my documents" is chosen), or null. */
  async function pickLocation(options) {
    const opts = Object.assign({ title: "Choose a folder", ok: "Choose", writableOnly: false, allowAll: false,
                                 current: null }, options || {});
    const data = await api("GET", "/api/locations");
    const locations = data.locations.filter(function (l) { return l.writable || !opts.writableOnly; });

    const dlg = el("dialog", "dlg glass picker");
    const form = el("form");
    form.method = "dialog";
    form.appendChild(el("h2", null, opts.title));
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

    function folderNodes(loc, list, parentId, depth) {
      const ul = el("ul", "tree-children");
      list.filter(function (f) { return (f.parent_id || null) === parentId; }).forEach(function (f) {
        const li = el("li");
        const label = el("button", "tree-label", "📁 " + f.name);
        label.type = "button";
        label.style.paddingLeft = (0.6 + depth * 0.9) + "rem";
        label.addEventListener("click", function () {
          choose(label, { collection_id: loc.id, folder_id: f.id, label: loc.name + " / " + f.name });
        });
        li.appendChild(label);
        const kids = folderNodes(loc, list, f.id, depth + 1);
        if (kids.childElementCount) li.appendChild(kids);
        ul.appendChild(li);
      });
      return ul;
    }

    if (opts.allowAll) {
      const li = el("li");
      const label = el("button", "tree-label", "🌸 All my documents");
      label.type = "button";
      label.addEventListener("click", function () { choose(label, { all: true }); });
      li.appendChild(label);
      tree.appendChild(li);
    }
    for (const loc of locations) {
      const li = el("li");
      const label = el("button", "tree-label", (loc.kind === "private" ? "🏠 " : "📚 ") + loc.name);
      label.type = "button";
      label.addEventListener("click", function () {
        choose(label, { collection_id: loc.id, folder_id: null, label: loc.name });
      });
      li.appendChild(label);
      try {
        const list = await folders(loc.id);
        const kids = folderNodes(loc, list, null, 1);
        if (kids.childElementCount) li.appendChild(kids);
      } catch (e) { /* unreadable: show the root only */ }
      tree.appendChild(li);
      if (opts.current && opts.current.collection_id === loc.id && !opts.current.folder_id) {
        choose(label, { collection_id: loc.id, folder_id: null, label: loc.name });
      }
    }

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

  window.Erinne = { el: el, api: api, folders: folders, forgetFolders: forget, pickLocation: pickLocation };
})();
