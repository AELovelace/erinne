/* Access administration: a library/folder explorer with an access pane,
   groups, a who-can-see-what overview, Claude activity and the change log.
   All user-controlled text goes through textContent, never innerHTML. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const errorNode = $("admin-error");
  let principals = { users: [], groups: [] };

  // What the explorer shows; the selection and tab also live in the URL hash.
  const state = {
    tab: "files",
    libraries: [],
    folders: {},            // library id -> [{id, parent_id, name, inherit, grants, files}]
    expanded: new Set(),    // "l:3", "f:12"
    selected: null,         // {kind: "library", id} | {kind: "folder", id, lib}
    detail: null,           // folder detail from the API
  };

  function showError(message) {
    errorNode.textContent = message || "";
    errorNode.hidden = !message;
    if (message) errorNode.scrollIntoView({ block: "nearest" });
  }

  /* Run a change, show its error if any, then reload what it may affect. */
  async function change(method, url, body) {
    showError("");
    try { await api(method, url, body); } catch (e) { showError(e.message); }
    await load();
  }

  function sizeLabel(bytes) {
    if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(1) + " GB";
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    return Math.round(bytes / 1024) + " KB";
  }

  function button(text, cls, onClick) {
    const b = el("button", cls, text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function accessSelect(value, withNone) {
    const select = el("select");
    [["read", "Can read"], ["write", "Can edit"]].concat(withNone ? [["none", "Remove"]] : []).forEach(function (o) {
      const opt = el("option", null, o[1]);
      opt.value = o[0];
      select.appendChild(opt);
    });
    select.value = value;
    return select;
  }

  function checkbox(labelText, checked, onChange, title) {
    const label = el("label", "check small");
    const input = el("input");
    input.type = "checkbox";
    input.checked = checked;
    input.addEventListener("change", function () { onChange(input.checked); });
    label.append(input, document.createTextNode(" " + labelText));
    if (title) label.title = title;
    return label;
  }

  function table(headers, rows) {
    const t = el("table", "admin-table");
    const head = el("tr");
    headers.forEach(function (h) { head.appendChild(el("th", null, h)); });
    t.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    rows.forEach(function (cells) {
      const tr = el("tr");
      cells.forEach(function (c) {
        const td = el("td");
        if (c instanceof Node) td.appendChild(c); else td.textContent = c;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    t.appendChild(body);
    return t;
  }

  function principalOptions(select, skip) {
    principals.users.forEach(function (u) {
      if (skip.has("user:" + u.id)) return;
      const o = el("option", null, "👤 " + u.name);
      o.value = "user:" + u.id;
      select.appendChild(o);
    });
    principals.groups.forEach(function (g) {
      if (skip.has("group:" + g.id)) return;
      const o = el("option", null, "👥 " + g.name);
      o.value = "group:" + g.id;
      select.appendChild(o);
    });
  }

  function who(g) { return (g.type === "group" ? "👥 " : "👤 ") + g.name; }

  /* Editable grants plus an "add someone" row; `url` takes PUT {type, id, access, claude}. */
  function grantEditor(grants, url, claudeOn) {
    const box = el("div");
    const list = el("ul", "grants");
    if (!grants.length) list.appendChild(el("li", "muted small", "Nobody yet."));
    grants.forEach(function (g) {
      const li = el("li");
      li.appendChild(el("span", null, who(g)));
      const claude = checkbox("Claude", g.claude, function (on) {
        change("PUT", url, { type: g.type, id: g.id, access: g.access, claude: on });
      }, claudeOn ? "May search this from Claude" : "The library isn't shared with Claude, so this has no effect yet");
      if (!claudeOn) claude.classList.add("muted");
      const select = accessSelect(g.access, true);
      select.setAttribute("aria-label", "Access for " + g.name);
      select.addEventListener("change", function () {
        change("PUT", url, { type: g.type, id: g.id, access: select.value });
      });
      li.append(claude, select);
      list.appendChild(li);
    });
    box.appendChild(list);

    const add = el("div", "grant-add");
    const pick = el("select");
    pick.setAttribute("aria-label", "Person or group to add");
    const blank = el("option", null, "Add a person or group…");
    blank.value = "";
    pick.appendChild(blank);
    principalOptions(pick, new Set(grants.map(function (g) { return g.type + ":" + g.id; })));
    const access = accessSelect("read", false);
    access.setAttribute("aria-label", "Access to give");
    add.append(pick, access, button("Add", "small", function () {
      if (!pick.value) return;
      const parts = pick.value.split(":");
      change("PUT", url, { type: parts[0], id: Number(parts[1]), access: access.value });
    }));
    box.appendChild(add);
    return box;
  }

  // -------------------------------------------------------------- explorer
  function library(id) { return state.libraries.find(function (l) { return l.id === id; }); }

  function folderOf(libId, id) {
    return (state.folders[libId] || []).find(function (f) { return f.id === id; });
  }

  async function loadFolders(libId) {
    state.folders[libId] = (await api("GET", "/api/admin/libraries/" + libId + "/folders")).folders;
  }

  function isSelected(kind, id) {
    return !!state.selected && state.selected.kind === kind && state.selected.id === id;
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
    if (opts.current) btn.setAttribute("aria-current", "true");
    if (opts.title) btn.title = opts.title;
    row.append(toggle, btn);
    if (opts.badge) row.appendChild(el("span", "ax-badge", opts.badge));
    li.appendChild(row);
    if (opts.expandable) li.setAttribute("aria-expanded", String(!!opts.expanded));
    return li;
  }

  async function toggle(key, libId) {
    if (state.expanded.has(key)) state.expanded.delete(key);
    else {
      state.expanded.add(key);
      if (libId && !state.folders[libId]) {
        try { await loadFolders(libId); } catch (e) { showError(e.message); }
      }
    }
    renderTree();
  }

  function folderBadge(f) {
    const parts = [];
    if (!f.inherit) parts.push("🔒");
    if (f.grants) parts.push("👥" + f.grants);
    return parts.join(" ");
  }

  function folderTitle(f) {
    return (f.inherit ? "" : "Doesn't inherit access. ") + (f.grants ? f.grants + " grant(s) set here." : "");
  }

  function folderBranch(lib, folders, parentId, depth) {
    const children = folders.filter(function (f) { return (f.parent_id || null) === parentId; });
    if (!children.length) return null;
    const ul = el("ul");
    ul.setAttribute("role", "group");
    children.forEach(function (f) {
      const key = "f:" + f.id;
      const hasKids = folders.some(function (x) { return x.parent_id === f.id; });
      const expanded = state.expanded.has(key);
      const node = treeNode("📁 " + f.name, {
        depth: depth, expandable: hasKids, expanded: expanded, current: isSelected("folder", f.id),
        badge: folderBadge(f), title: folderTitle(f),
        onToggle: function () { toggle(key); },
        onOpen: function () { select({ kind: "folder", id: f.id, lib: lib.id }); },
      });
      if (expanded) {
        const kids = folderBranch(lib, folders, f.id, depth + 1);
        if (kids) node.appendChild(kids);
      }
      ul.appendChild(node);
    });
    return ul;
  }

  function renderTree() {
    const tree = $("ax-tree");
    const frag = document.createDocumentFragment();
    if (!state.libraries.length) frag.appendChild(el("li", "muted small ax-empty", "No libraries yet."));
    state.libraries.forEach(function (lib) {
      const key = "l:" + lib.id;
      const expanded = state.expanded.has(key);
      const node = treeNode("📚 " + lib.name, {
        depth: 0, expandable: true, expanded: expanded, current: isSelected("library", lib.id),
        badge: lib.claude_enabled ? "" : "Claude off",
        onToggle: function () { toggle(key, lib.id); },
        onOpen: function () { select({ kind: "library", id: lib.id }); },
      });
      if (expanded && state.folders[lib.id]) {
        const kids = folderBranch(lib, state.folders[lib.id], null, 1);
        if (kids) node.appendChild(kids);
        else {
          const ul = el("ul");
          ul.appendChild(el("li", "muted small ax-empty", "No folders"));
          node.appendChild(ul);
        }
      }
      frag.appendChild(node);
    });
    tree.replaceChildren(frag);
  }

  /* Open every ancestor of the selected folder so it shows in the tree. */
  function reveal(libId, folderId) {
    state.expanded.add("l:" + libId);
    let f = folderOf(libId, folderId);
    while (f && f.parent_id) {
      state.expanded.add("f:" + f.parent_id);
      f = folderOf(libId, f.parent_id);
    }
  }

  async function select(sel, keepHash) {
    state.selected = sel;
    state.detail = null;
    if (!keepHash) writeHash();
    try {
      if (sel && sel.kind === "folder") {
        if (!state.folders[sel.lib]) await loadFolders(sel.lib);
        reveal(sel.lib, sel.id);
        state.detail = await api("GET", "/api/admin/folders/" + sel.id);
      }
    } catch (e) {
      showError(e.message);
      state.selected = null;
    }
    renderTree();
    renderPane();
  }

  function renderPane() {
    const pane = $("ax-pane");
    const sel = state.selected;
    if (sel && sel.kind === "library" && library(sel.id)) pane.replaceChildren(libraryPane(library(sel.id)));
    else if (sel && sel.kind === "folder" && state.detail) pane.replaceChildren(folderPane(state.detail));
    else pane.replaceChildren(el("p", "muted", "Pick a library or folder on the left to see and change who can reach it."));
  }

  function libraryPane(lib) {
    const box = el("div");
    const head = el("div", "lib-head");
    head.appendChild(el("h2", null, "📚 " + lib.name));
    head.appendChild(el("span", "muted small", lib.files + (lib.files === 1 ? " file" : " files") + " · " + sizeLabel(lib.bytes)));
    const open = el("a", "small", "Open in Files");
    open.href = "/files#c=" + lib.id;
    head.append(open, button("Rename", "link small", function () {
      const name = prompt("New name for " + lib.name, lib.name);
      if (!name || name.trim() === lib.name) return;
      change("PATCH", "/api/admin/libraries/" + lib.id, { name: name.trim() });
    }), button("Delete", "link danger small", function () {
      const typed = prompt("This deletes “" + lib.name + "” and all " + lib.files + " of its files for good, for everyone.\n\nType the library name to confirm:");
      if (typed === null) return;
      if (typed.trim() !== lib.name) { showError("The name didn't match, so nothing was deleted."); return; }
      state.selected = null;
      change("DELETE", "/api/admin/libraries/" + lib.id);
    }));
    box.appendChild(head);

    const claudeRow = el("div", "claude-row");
    claudeRow.appendChild(checkbox("Shared with Claude", lib.claude_enabled, function (on) {
      change("PUT", "/api/admin/libraries/" + lib.id + "/claude", { enabled: on });
    }, "Master switch: when off, nobody can search this library from Claude"));
    if (lib.claude_changed_by) {
      claudeRow.appendChild(el("span", "muted small",
        "last changed by " + lib.claude_changed_by + ", " + (lib.claude_changed_at || "").slice(0, 16) + " UTC"));
    }
    box.appendChild(claudeRow);

    box.appendChild(el("h3", "ax-h", "Who has access"));
    box.appendChild(el("p", "muted small", "Applies to the whole library: every folder inherits it, except folders that turn inheritance off (🔒 in the tree)."));
    box.appendChild(grantEditor(lib.grants, "/api/admin/libraries/" + lib.id + "/grants", lib.claude_enabled));
    return box;
  }

  function crumbs(d) {
    const nav = el("nav", "ax-crumbs");
    nav.setAttribute("aria-label", "Folder path");
    nav.appendChild(button("📚 " + d.library.name, "link small", function () { select({ kind: "library", id: d.library.id }); }));
    d.path.forEach(function (p, i) {
      nav.appendChild(el("span", "muted small", " › "));
      if (i === d.path.length - 1) nav.appendChild(el("span", "small", p.name));
      else nav.appendChild(button(p.name, "link small", function () { select({ kind: "folder", id: p.id, lib: d.library.id }); }));
    });
    return nav;
  }

  function parentName(d) {
    return d.path.length > 1 ? d.path[d.path.length - 2].name : d.library.name;
  }

  function inheritBlock(d) {
    const box = el("div", "ax-inherit");
    const url = "/api/admin/folders/" + d.id + "/inherit";
    const label = checkbox("Inherit access from " + parentName(d), d.inherit, function (on) {
      if (on) { change("PUT", url, { inherit: true }); return; }
      // Turning it off: ask how to start, inline.
      label.querySelector("input").checked = true;
      const ask = el("div", "ax-confirm");
      ask.appendChild(el("p", "small", "Stop inheriting? Only grants set on this folder will apply, so people who reach it only through " + parentName(d) + " lose access."));
      const keep = el("label", "check small");
      const keepInput = el("input");
      keepInput.type = "radio"; keepInput.name = "inherit-start"; keepInput.checked = true;
      keep.append(keepInput, document.createTextNode(" Keep everyone's current access as grants on this folder, then remove who shouldn't have it"));
      const fresh = el("label", "check small");
      const freshInput = el("input");
      freshInput.type = "radio"; freshInput.name = "inherit-start";
      fresh.append(freshInput, document.createTextNode(" Start with only the grants already set on this folder"));
      const actions = el("div", "grant-add");
      actions.append(button("Stop inheriting", "small", function () {
        change("PUT", url, { inherit: false, keep_access: keepInput.checked });
      }), button("Cancel", "link small", function () { ask.remove(); }));
      ask.append(keep, fresh, actions);
      box.querySelector(".ax-confirm") || box.appendChild(ask);
    }, "When on, everyone with access to " + parentName(d) + " can reach this folder too");
    box.appendChild(label);
    if (!d.inherit) box.appendChild(el("p", "muted small", "🔒 Not inheriting: only the grants set on this folder apply here and below."));
    return box;
  }

  function folderPane(d) {
    const lib = library(d.library.id);
    const claudeOn = !!(lib && lib.claude_enabled);
    const box = el("div");
    box.appendChild(crumbs(d));
    const head = el("div", "lib-head");
    head.appendChild(el("h2", null, "📁 " + d.name));
    head.appendChild(el("span", "muted small", d.files + (d.files === 1 ? " file" : " files")));
    const open = el("a", "small", "Open in Files");
    open.href = "/files#c=" + d.library.id + "&f=" + d.id;
    head.appendChild(open);
    box.appendChild(head);

    box.appendChild(inheritBlock(d));

    box.appendChild(el("h3", "ax-h", "Set on this folder"));
    box.appendChild(el("p", "muted small", "Adds access to this folder and its subfolders, on top of anything inherited."));
    box.appendChild(grantEditor(d.grants, "/api/admin/folders/" + d.id + "/grants", claudeOn));

    box.appendChild(el("h3", "ax-h", "Inherited"));
    if (!d.inherit) {
      box.appendChild(el("p", "muted small", "Nothing: this folder doesn't inherit."));
    } else if (!d.inherited.length) {
      box.appendChild(el("p", "muted small", "Nothing is granted above this folder."));
    } else {
      const list = el("ul", "grants ax-inherited");
      d.inherited.forEach(function (g) {
        const li = el("li");
        li.appendChild(el("span", null, who(g)));
        li.appendChild(el("span", "muted small", (g.access === "write" ? "can edit" : "can read") + (g.claude && claudeOn ? " · Claude" : "")));
        const src = g.source;
        li.appendChild(button("from " + (src.kind === "library" ? "📚 " : "📁 ") + src.name, "link small", function () {
          select(src.kind === "library" ? { kind: "library", id: src.id } : { kind: "folder", id: src.id, lib: d.library.id });
        }));
        list.appendChild(li);
      });
      box.appendChild(list);
      box.appendChild(el("p", "muted small", "To change these, open where they come from, or stop inheriting above."));
    }
    return box;
  }

  // ---------------------------------------------------------------- groups
  function renderGroup(group) {
    const membersUrl = "/api/admin/groups/" + group.id + "/members";
    const memberIds = group.members.map(function (m) { return m.id; });
    const section = el("section", "lib");
    const head = el("div", "lib-head");
    head.appendChild(el("h3", null, "👥 " + group.name));
    head.appendChild(el("span", "muted small", group.members.length + (group.members.length === 1 ? " member" : " members")));
    head.append(button("Rename", "link small", function () {
      const name = prompt("New name for " + group.name, group.name);
      if (!name || name.trim() === group.name) return;
      change("PATCH", "/api/admin/groups/" + group.id, { name: name.trim() });
    }), button("Delete", "link danger small", function () {
      if (!confirm("Delete the group “" + group.name + "”? Its members lose any access that came only from this group.")) return;
      change("DELETE", "/api/admin/groups/" + group.id);
    }));
    section.appendChild(head);

    const list = el("ul", "grants");
    if (!group.members.length) list.appendChild(el("li", "muted small", "No members yet."));
    group.members.forEach(function (m) {
      const li = el("li");
      li.appendChild(el("span", null, "👤 " + m.name));
      li.appendChild(button("Remove", "link small", function () {
        change("PUT", membersUrl, { members: memberIds.filter(function (id) { return id !== m.id; }) });
      }));
      list.appendChild(li);
    });
    section.appendChild(list);

    const add = el("div", "grant-add");
    const pick = el("select");
    pick.setAttribute("aria-label", "Person to add to " + group.name);
    const blank = el("option", null, "Add a person…");
    blank.value = "";
    pick.appendChild(blank);
    principals.users.forEach(function (u) {
      if (memberIds.indexOf(u.id) !== -1) return;
      const o = el("option", null, "👤 " + u.name);
      o.value = String(u.id);
      pick.appendChild(o);
    });
    add.append(pick, button("Add", "small", function () {
      if (!pick.value) return;
      change("PUT", membersUrl, { members: memberIds.concat([Number(pick.value)]) });
    }));
    section.appendChild(add);
    return section;
  }

  // -------------------------------------------------------------- overview
  function personCell(p) {
    const cell = el("div");
    cell.appendChild(el("div", null, p.name + (p.role === "admin" ? " (admin)" : "")));
    if (p.tokens) {
      cell.appendChild(button("Revoke " + p.tokens + (p.tokens === 1 ? " Claude token" : " Claude tokens"), "link danger small", function () {
        if (!confirm("Revoke all of " + p.name + "'s Claude tokens? Claude stops working for them straight away.")) return;
        change("DELETE", "/api/admin/users/" + p.id + "/tokens");
      }));
    }
    return cell;
  }

  function renderActivity(calls) {
    $("activity").replaceChildren(calls.length
      ? table(["When (UTC)", "Who", "Tool", "Asked", "Files returned"], calls.map(function (c) {
          const asked = Object.keys(c.args).filter(function (k) { return c.args[k] !== null && c.args[k] !== ""; })
            .map(function (k) { return k + ": " + c.args[k]; }).join(", ");
          return [c.at.slice(0, 16), c.user || "(deleted)", c.tool + (c.ok ? "" : " (failed)"), asked, c.files.join(", ")];
        }))
      : el("p", "muted", "No Claude searches yet."));
  }

  function renderAccess(people) {
    const rows = [];
    people.forEach(function (p) {
      p.libraries.forEach(function (c, i) {
        let claude = c.claude;
        if (c.kind === "private") {
          const on = c.claude === "everything";
          claude = checkbox(on ? "yes" : "no", on, function (value) {
            change("PUT", "/api/admin/users/" + p.id + "/inbox-claude", { enabled: value });
          }, "May search their own inbox from Claude");
        }
        rows.push([i === 0 ? personCell(p) : "", c.kind === "private" ? "Inbox" : "📚 " + c.name,
                   c.read, c.edit, claude, c.via.join(", ")]);
      });
    });
    $("access").replaceChildren(rows.length
      ? table(["Person", "Library", "Reads", "Edits", "Claude", "Through"], rows)
      : el("p", "muted", "No active users."));
  }

  function renderLog(entries) {
    $("log").replaceChildren(entries.length
      ? table(["When (UTC)", "Who", "Change", "On", "Details"], entries.map(function (e) {
          return [e.at.slice(0, 16), e.actor_name, e.action, e.target, e.detail];
        }))
      : el("p", "muted", "No changes yet."));
  }

  // ------------------------------------------------------------ tabs, hash
  function showTab(tab) {
    state.tab = tab;
    document.querySelectorAll("[data-tab]").forEach(function (b) {
      b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    });
    document.querySelectorAll("[data-panel]").forEach(function (p) { p.hidden = p.dataset.panel !== tab; });
  }

  function writeHash() {
    const parts = ["tab=" + state.tab];
    const sel = state.selected;
    if (sel && sel.kind === "library") parts.push("lib=" + sel.id);
    if (sel && sel.kind === "folder") parts.push("lib=" + sel.lib, "folder=" + sel.id);
    history.replaceState(null, "", "#" + parts.join("&"));
  }

  function readHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const lib = Number(params.get("lib")), folder = Number(params.get("folder"));
    return { tab: params.get("tab") || "files",
             sel: folder && lib ? { kind: "folder", id: folder, lib: lib } : lib ? { kind: "library", id: lib } : null };
  }

  document.querySelectorAll("[data-tab]").forEach(function (b) {
    b.addEventListener("click", function () { showTab(b.dataset.tab); writeHash(); });
  });

  // ------------------------------------------------------------------ load
  async function load() {
    try {
      const [libs, people, groups, access, log, activity] = await Promise.all([
        api("GET", "/api/admin/libraries"), api("GET", "/api/admin/principals"), api("GET", "/api/admin/groups"),
        api("GET", "/api/admin/access"), api("GET", "/api/admin/log?limit=50"),
        api("GET", "/api/admin/claude-activity?limit=50")]);
      principals = people;
      state.libraries = libs.libraries;
      // Refresh folder lists that are on screen; drop ones for deleted libraries.
      const ids = new Set(state.libraries.map(function (l) { return l.id; }));
      await Promise.all(Object.keys(state.folders).map(Number).map(function (id) {
        if (!ids.has(id)) { delete state.folders[id]; return null; }
        return loadFolders(id);
      }));
      if (state.selected && !ids.has(state.selected.kind === "library" ? state.selected.id : state.selected.lib)) {
        state.selected = null;
      }
      const groupsNode = $("groups");
      groupsNode.replaceChildren();
      if (!groups.groups.length) groupsNode.appendChild(el("p", "muted", "No groups yet."));
      groups.groups.forEach(function (g) { groupsNode.appendChild(renderGroup(g)); });
      renderAccess(access.users);
      renderLog(log.entries);
      renderActivity(activity.calls);
      await select(state.selected, true);
      writeHash();
    } catch (e) { showError(e.message); }
  }

  function createForm(formId, inputId, url, after) {
    $(formId).addEventListener("submit", async function (e) {
      e.preventDefault();
      const input = $(inputId);
      showError("");
      try {
        const made = await api("POST", url, { name: input.value.trim() });
        input.value = "";
        if (after) after(made);
      } catch (err) { showError(err.message); }
      await load();
    });
  }

  createForm("new-lib", "new-lib-name", "/api/admin/libraries", function (made) {
    state.selected = { kind: "library", id: made.id };
  });
  createForm("new-group", "new-group-name", "/api/admin/groups");

  const start = readHash();
  showTab(start.tab);
  state.selected = start.sel;
  load();
})();
