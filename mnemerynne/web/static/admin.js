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
    const wrap = el("div", "table-wrap");   // scrolls sideways on a narrow screen instead of widening the page
    wrap.appendChild(t);
    return wrap;
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
    if (!keepHash && window.Erynne.narrow.matches) {   // a phone: fold the tree and show the pane
      $("ax-side").open = false;
      $("ax-pane").scrollIntoView({ block: "start" });
    }
  }

  function renderPane() {
    const pane = $("ax-pane");
    const sel = state.selected;
    if (sel && sel.kind === "library" && library(sel.id)) pane.replaceChildren(libraryPane(library(sel.id)));
    else if (sel && sel.kind === "folder" && state.detail) pane.replaceChildren(folderPane(state.detail));
    else pane.replaceChildren(el("p", "muted", "Pick a library or folder on the left to see and change who can reach it."));
  }

  /* Libraries on the SMB share take their access from Windows permissions. */
  function shareNote(where) {
    const note = el("div", "ax-share");
    note.appendChild(el("p", null, "📂 This library is the folder " + where + " on the file share."));
    note.appendChild(el("p", "muted small", "Who can read and edit it is set on the share: in Windows, right-click the folder, " +
      "Properties → Security. People see what their linked domain account can open there, " +
      "folder by folder (see Accounts). Claude follows the same access when the switch above is on."));
    return note;
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
      const typed = prompt((lib.share_path
        ? "This removes “" + lib.name + "” and its " + lib.files + " files from the share and from Erynne, for everyone (the folder is kept in the server's trash).\n\n"
        : "This deletes “" + lib.name + "” and all " + lib.files + " of its files for good, for everyone.\n\n") +
        "Type the library name to confirm:");
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
    const editBox = checkbox("Claude and Erynne can edit files here", lib.claude_edit, function (on) {
      change("PUT", "/api/admin/libraries/" + lib.id + "/claude-edit", { enabled: on });
    }, "Lets Claude create and change text, Markdown, Word and Excel files here, for people who may edit them " +
       "themselves and whose token allows it. Every change can be undone from Versions.");
    if (!lib.claude_enabled) editBox.classList.add("muted");
    claudeRow.appendChild(editBox);
    if (lib.claude_changed_by) {
      claudeRow.appendChild(el("span", "muted small",
        "last changed by " + lib.claude_changed_by + ", " + (lib.claude_changed_at || "").slice(0, 16) + " UTC"));
    }
    box.appendChild(claudeRow);

    if (lib.share_path) { box.appendChild(shareNote(lib.share_path)); return box; }
    if (lib.source === "sharepoint") {
      const note = el("div", "ax-share");
      note.appendChild(el("p", null, "🪟 This library is a read-only copy of a SharePoint library (Settings → Microsoft 365)."));
      note.appendChild(el("p", "muted small", "Who can see it is decided by SharePoint: people with a linked Microsoft " +
        "account in that tenant see the files SharePoint lets them open, checked file by file. Grants don't apply. " +
        "Claude follows the same checks when the switch above is on."));
      box.appendChild(note);
      return box;
    }
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

    if (d.share) {
      box.appendChild(shareNote((lib ? lib.share_path : d.library.name) + "\\" +
        d.path.map(function (p) { return p.name; }).join("\\")));
      return box;
    }
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

    // Everyone in one Entra group can be members too (through linked Microsoft accounts).
    const entra = el("p", "small muted");
    entra.textContent = group.entra ? "Also everyone in the Entra group " + group.entra + ". "
                                    : "Members are listed below. ";
    entra.appendChild(button(group.entra ? "Change" : "Also include an Entra group", "link small", async function () {
      let options = [];
      try { options = (await api("GET", "/api/admin/entra-groups")).groups; } catch (e) { /* none yet */ }
      const picked = prompt("Entra group, as “Tenant / Group”" + (group.entra ? " (empty to stop)" : "") + ":" +
        (options.length ? "\n\ne.g. " + options.slice(0, 5).join("\n") : "\n\nNo Entra groups yet: add a tenant " +
        "on Settings → Microsoft 365 and wait a few minutes."), group.entra || "");
      if (picked === null) return;
      change("PUT", "/api/admin/groups/" + group.id + "/entra", { group: picked.trim() });
    }));
    section.appendChild(entra);

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
          }, "May search their own File box from Claude");
        }
        rows.push([i === 0 ? personCell(p) : "", c.kind === "private" ? "🏠 File box" : "📚 " + c.name,
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

  // -------------------------------------------------------------- settings
  /* Read-only: installing an update is `sudo mnemerynne-admin update` on the server. */
  function renderUpdates(u) {
    const box = $("updates");
    box.replaceChildren();
    const cur = u.current || {};
    const line = el("p");
    line.appendChild(el("span", null, "Installed: "));
    line.appendChild(el("code", null, cur.short || "unknown"));
    if (cur.date || cur.message) line.appendChild(el("span", "muted", " · " + [cur.date, cur.message].filter(Boolean).join(" · ")));
    box.appendChild(line);
    if (u.repo) box.appendChild(el("p", "muted small", "Following " + u.repo + ", branch " + u.branch + ". Checked " + u.checked_at + "."));

    if (u.error) box.appendChild(el("p", "error", u.error));
    else if (!u.ahead) box.appendChild(el("p", null, "✓ Mnemerynne is up to date."));
    else {
      box.appendChild(el("p", null, u.ahead + (u.ahead === 1 ? " update" : " updates") + " available:"));
      const list = el("ul", "ax-commits");
      u.commits.forEach(function (c) {
        const li = el("li");
        li.append(el("code", null, c.short), el("span", "muted", " " + c.date + " "), el("span", null, c.message));
        list.appendChild(li);
      });
      box.appendChild(list);
      const notes = [];
      if (u.flags.migrations.length) notes.push("Includes a database update (" + u.flags.migrations.length + "); a backup is taken first.");
      if (u.flags.services.length) notes.push("Changes service files: " + u.flags.services.join(", ") + " (they restart).");
      if (u.flags.packages) notes.push("Installs new or changed Python packages.");
      if (u.flags.admin_command) notes.push("Updates the mnemerynne-admin command itself.");
      notes.forEach(function (n) { box.appendChild(el("p", "small", "• " + n)); });
      const how = el("p");
      how.append(document.createTextNode("To install, on the server: "), el("code", null, u.command));
      box.appendChild(how);
      box.appendChild(el("p", "muted small", "It backs up the database, updates, restarts, and undoes everything if Mnemerynne doesn't come back healthy."));
    }
    const last = u.last_update;
    if (last) {
      box.appendChild(el("p", "muted small", "Last update: " + last.at + " — " +
        (last.result === "ok" ? "succeeded" : "failed" + (last.message ? " (" + last.message + ")" : "")) +
        ", " + (last.from || "").slice(0, 7) + " → " + (last.to || "").slice(0, 7) + "."));
    }
    box.appendChild(button("Check now", "small", function () { loadUpdates(true); }));
  }

  // ------------------------------------------------------------- accounts
  function fillDatalist(id, values) {
    $(id).replaceChildren.apply($(id), values.map(function (v) { const o = el("option"); o.value = v; return o; }));
  }

  /* The domain account: shown with Unlink, or a box to link one. `done` reloads whatever shows it. */
  function linkCell(u, done) {
    const cell = el("div", "ax-link");
    if (u.ad_account) {
      cell.append(el("span", null, u.ad_account), document.createTextNode(" "),
        button("Unlink", "link danger small", async function () {
          if (!confirm("Unlink " + u.username + " from " + u.ad_account + "? Their account and files stay.")) return;
          await done("DELETE", "/api/admin/users/" + u.id + "/domain-account");
        }));
      return cell;
    }
    const input = el("input");
    input.type = "text";
    input.setAttribute("list", "domain-accounts");
    input.placeholder = "Domain account, e.g. jsmith";
    input.setAttribute("aria-label", "Domain account for " + u.username);
    input.value = u.username;  // usually the same name
    cell.append(input, button("Link", "small", async function () {
      await done("PUT", "/api/admin/users/" + u.id + "/domain-account", { ad_account: input.value.trim() });
    }));
    return cell;
  }

  const ADMIN_VIA = { role: "local admin", bootstrap: "admin (server setting)", domain: "admin (domain group)" };

  function renderAccounts(data) {
    const box = $("accounts");
    box.replaceChildren();
    fillDatalist("domain-accounts", data.domain_accounts);
    if (!data.enabled) box.appendChild(el("p", "error", "No AD domain is configured on the server (MNEMERYNNE_AD_DOMAIN), so accounts can't be linked."));
    const waiting = data.users.filter(function (u) { return !u.ad_account && u.active; }).length;
    if (waiting) box.appendChild(el("p", null, waiting + (waiting === 1 ? " user isn't" : " users aren't") + " linked to a domain account yet."));
    box.appendChild(table(["User", "Domain account" + (data.domain ? " (" + data.domain + ")" : ""), "Groups", "Claude"],
      data.users.map(function (u) {
        const who = el("div", u.active ? null : "ax-off");
        who.append(button(u.username, "link ax-name", function () { openAccount(u.id); }),
          el("span", "muted small", (u.admin_via ? " · " + ADMIN_VIA[u.admin_via] : "") + (u.active ? "" : " · deactivated")));
        return [who, linkCell(u, accountChange), u.groups.join(", "),
                u.tokens ? u.tokens + (u.tokens === 1 ? " token" : " tokens") : ""];
      })));
  }

  async function loadAccounts() {
    try { renderAccounts(await api("GET", "/api/admin/accounts")); }
    catch (e) { $("accounts").replaceChildren(el("p", "error", e.message)); }
  }

  async function accountChange(method, url, body) {
    showError("");
    try { await api(method, url, body); } catch (e) { showError(e.message); }
    await loadAccounts();
    await load();  // new users appear in grants and "Who can see what"
  }

  /* A readable password nobody has to squint at: no 0/O, 1/l/I. */
  function generatePassword() {
    const chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    const bytes = new Uint32Array(16);
    crypto.getRandomValues(bytes);
    let out = "";
    bytes.forEach(function (b, i) { out += (i && i % 4 === 0 ? "-" : "") + chars[b % chars.length]; });
    return out;
  }

  // ------------------------------------------------------- account dialog
  let accountDirty = false;   // reload the list when the dialog closes

  function userError(message) {
    const node = $("user-error");
    node.textContent = message || "";
    node.hidden = !message;
  }

  /* Apply one change from the dialog, then redraw it with what the server now says. */
  async function accountDo(id, method, url, body) {
    userError("");
    try {
      const result = await api(method, url, body);
      accountDirty = true;
      await openAccount(id);
      return result || true;
    } catch (e) {
      userError(e.message);
      return null;
    }
  }

  function dlgSection(title) {
    const s = el("section", "acct-sec");
    s.appendChild(el("h3", "props-h", title));
    return s;
  }

  function passwordSection(u) {
    const s = dlgSection("Password");
    const row = el("div", "ax-link");
    const input = el("input");
    input.type = "text";
    input.autocomplete = "new-password";
    input.spellcheck = false;
    input.placeholder = "New password";
    input.setAttribute("aria-label", "New password for " + u.username);
    const set = button("Reset password", "small", async function () {
      if (!input.value) { input.focus(); return; }
      const value = input.value;
      if (await accountDo(u.id, "PUT", "/api/admin/users/" + u.id + "/password", { password: value })) {
        pwNote("Password changed to " + value + ". Give it to " + u.username + "; they've been signed out everywhere.");
      }
    });
    row.append(input, button("Generate", "small", function () { input.value = generatePassword(); }), set);
    s.append(row, el("p", "muted small acct-pw-note",
      u.is_you ? "Your other sessions end; this one stays signed in."
               : "They're signed out everywhere and sign in with the new one. Claude tokens keep working."));
    return s;
  }

  function accessSection(u) {
    const s = dlgSection("Access");
    const roleRow = el("div", "ax-link");
    const role = el("select");
    [["user", "User"], ["admin", "Local admin"]].forEach(function (o) {
      const opt = el("option", null, o[1]);
      opt.value = o[0];
      role.appendChild(opt);
    });
    role.value = u.role;
    role.setAttribute("aria-label", "Role");
    role.addEventListener("change", function () {
      accountDo(u.id, "PATCH", "/api/admin/users/" + u.id, { role: role.value });
    });
    roleRow.append(el("span", "small", "Role"), role);
    s.appendChild(roleRow);
    if (u.admin_via === "domain") {
      s.appendChild(el("p", "muted small", "Admin through the domain admin group, whatever the role says here."));
    } else if (u.admin_via === "bootstrap") {
      s.appendChild(el("p", "muted small", "Admin through MNEMERYNNE_ADMIN_USERNAMES on the server."));
    }
    const active = checkbox("Can sign in", u.active, function (on) {
      if (!on && !confirm("Stop " + u.username + " signing in? They're signed out at once; their files stay.")) {
        openAccount(u.id);
        return;
      }
      accountDo(u.id, "PATCH", "/api/admin/users/" + u.id, { active: on });
    }, u.is_you ? "You can't deactivate yourself." : "");
    if (u.is_you && u.active) active.querySelector("input").disabled = true;
    s.appendChild(active);
    return s;
  }

  function domainSection(u) {
    const s = dlgSection("Domain account");
    s.appendChild(linkCell(u, function (method, url, body) { return accountDo(u.id, method, url, body); }));
    return s;
  }

  function groupsSection(u) {
    const s = dlgSection("Groups");
    if (!u.all_groups.length) {
      s.appendChild(el("p", "muted small", "No groups yet. Make them on the Groups tab."));
      return s;
    }
    const list = el("div", "acct-groups");
    u.all_groups.forEach(function (g) {
      list.appendChild(checkbox(g.name, g.member, function (on) {
        const ids = u.all_groups.filter(function (x) { return x.id === g.id ? on : x.member; })
          .map(function (x) { return x.id; });
        accountDo(u.id, "PUT", "/api/admin/users/" + u.id + "/groups", { groups: ids });
      }));
    });
    s.appendChild(list);
    return s;
  }

  function claudeSection(u) {
    const s = dlgSection("Their files and Claude");
    if (u.inbox) {
      s.appendChild(el("p", "small", u.inbox.files + (u.inbox.files === 1 ? " file" : " files") +
        " in their own files (" + sizeLabel(u.inbox.bytes) + ")."));
      s.append(checkbox("Claude can search their files", u.inbox.claude_enabled, function (on) {
        accountDo(u.id, "PUT", "/api/admin/users/" + u.id + "/inbox-claude", { enabled: on });
      }), el("br"), checkbox("Claude and Erynne can edit anywhere in their files", u.inbox.claude_edit, function (on) {
        accountDo(u.id, "PUT", "/api/admin/users/" + u.id + "/inbox-claude-edit", { enabled: on });
      }, "Off: only the Agents folder and what they allow with AI edit in Files."));
    }
    const tokens = el("p", "small");
    tokens.textContent = u.tokens ? u.tokens + (u.tokens === 1 ? " Claude token in use. " : " Claude tokens in use. ")
                                  : "No Claude tokens.";
    if (u.tokens) {
      tokens.appendChild(button("Revoke all", "link danger small", function () {
        if (!confirm("Revoke all of " + u.username + "'s Claude tokens? Claude stops working for them straight away.")) return;
        accountDo(u.id, "DELETE", "/api/admin/users/" + u.id + "/tokens");
      }));
    }
    s.appendChild(tokens);
    return s;
  }

  /* A message under the password box that stays while the dialog is redrawn. */
  function pwNote(text) {
    const note = $("user-body").querySelector(".acct-pw-note");
    note.textContent = text;
    note.dataset.kept = "1";
  }

  // Claude connected through OAuth (claude.ai connectors, Claude Code; oauth.py).
  function connectionsSection(u, connected) {
    const s = dlgSection("Claude connections");
    if (!connected.length) {
      s.appendChild(el("p", "small muted", "None. They connect claude.ai or Claude Code from the Connect page's address."));
      return s;
    }
    connected.forEach(function (c) {
      const p = el("p", "small");
      p.textContent = c.app + (c.can_edit ? " (may edit)" : " (search only)") + ", last used " +
        (c.last_used_at ? c.last_used_at.slice(0, 16) + " UTC" : "never") + " ";
      p.appendChild(button("Disconnect", "link danger small", function () {
        if (!confirm("Disconnect " + c.app + " for " + u.username + "?")) return;
        accountDo(u.id, "DELETE", "/api/admin/oauth/connections/" + c.id);
      }));
      s.appendChild(p);
    });
    return s;
  }

  // Microsoft 365 accounts the person linked on the Settings page (m365.py).
  function microsoftSection(u, linked) {
    const s = dlgSection("Microsoft 365");
    if (!linked.length) {
      s.appendChild(el("p", "small muted", "No Microsoft 365 account linked. They link their own on the Preferences page."));
      return s;
    }
    linked.forEach(function (a) {
      const p = el("p", "small");
      p.textContent = a.upn + " · " + a.tenant + (a.status === "relink" ? " · needs linking again" : "") + " ";
      p.appendChild(button("Unlink", "link danger small", function () {
        if (!confirm("Unlink " + a.upn + " from " + u.username + "? Erynne stops reaching its SharePoint and mail.")) return;
        accountDo(u.id, "DELETE", "/api/admin/m365/accounts/" + a.id);
      }));
      s.appendChild(p);
    });
    return s;
  }

  async function openAccount(id) {
    const dlg = $("dlg-user");
    let u, linked = [];
    try { u = await api("GET", "/api/admin/users/" + id); }
    catch (e) { showError(e.message); return; }
    try { linked = (await api("GET", "/api/admin/users/" + id + "/m365")).accounts; } catch (e) { /* older server */ }
    let connected = [];
    try { connected = (await api("GET", "/api/admin/users/" + id + "/connections")).connections; } catch (e) { /* older */ }
    $("user-title").textContent = u.username + (u.active ? "" : " (deactivated)");
    const body = $("user-body");
    // Keep a password message the reset just wrote across the redraw.
    const note = body.querySelector(".acct-pw-note");
    const kept = note && note.dataset.kept ? note.textContent : "";
    const since = el("p", "muted small", "Since " + (u.created_at || "").slice(0, 10) +
      (u.admin_via ? " · " + ADMIN_VIA[u.admin_via] : "") + (u.is_you ? " · this is you" : ""));
    body.replaceChildren(since, passwordSection(u), accessSection(u), domainSection(u), groupsSection(u), claudeSection(u),
                         connectionsSection(u, connected), microsoftSection(u, linked));
    if (kept) pwNote(kept);
    if (!dlg.open) { userError(""); dlg.showModal(); }
  }

  $("dlg-user").addEventListener("close", async function () {
    if (!accountDirty) return;
    accountDirty = false;
    await loadAccounts();
    await load();
  });

  // ----------------------------------------------------------- new user
  $("new-user").addEventListener("click", function () {
    $("new-user-form").reset();
    $("nu-error").hidden = true;
    $("nu-password").value = generatePassword();
    $("dlg-new-user").showModal();
    $("nu-name").focus();
  });
  $("nu-generate").addEventListener("click", function () { $("nu-password").value = generatePassword(); });
  $("nu-name").addEventListener("input", function () {
    // Suggest the same domain account name when there is one.
    const name = $("nu-name").value.trim().toLowerCase();
    const known = Array.prototype.some.call($("domain-accounts").options, function (o) { return o.value.toLowerCase() === name; });
    if (known || !$("nu-account").dataset.touched) $("nu-account").value = known ? name : "";
  });
  $("nu-account").addEventListener("input", function () { $("nu-account").dataset.touched = "1"; });
  $("new-user-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const err = $("nu-error");
    err.hidden = true;
    try {
      const made = await api("POST", "/api/admin/users", {
        username: $("nu-name").value.trim(), password: $("nu-password").value, role: $("nu-role").value,
        ad_account: $("nu-account").value.trim() || null });
      const password = $("nu-password").value;
      delete $("nu-account").dataset.touched;
      $("dlg-new-user").close();
      accountDirty = true;
      await openAccount(made.id);
      pwNote("Created with password " + password + ". Give it to " + made.username + ".");
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    }
  });
  document.querySelectorAll("dialog [data-close]").forEach(function (b) {
    b.addEventListener("click", function () { b.closest("dialog").close(); });
  });

  // ----------------------------------------------------------- admin group
  function renderAdminGroup(data) {
    const box = $("admin-group");
    box.replaceChildren();
    fillDatalist("domain-groups", data.groups);
    box.appendChild(el("p", "muted", "Members of this " + (data.domain || "domain") +
      " group are admins here. Local accounts made with mnemerynne create-user --role admin always are."));
    if (!data.enabled) box.appendChild(el("p", "error", "No AD domain is configured on the server (MNEMERYNNE_AD_DOMAIN)."));
    const form = el("form", "ax-new");
    const input = el("input");
    input.type = "text";
    input.value = data.group;
    input.required = true;
    input.setAttribute("list", "domain-groups");
    input.setAttribute("aria-label", "Admin group");
    const save = el("button", "small", "Save");
    save.type = "submit";
    form.append(input, save);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try { renderAdminGroup(Object.assign(data, await api("PUT", "/api/admin/settings/admin-group", { group: input.value.trim() }))); }
      catch (err) { showError(err.message); }
    });
    box.appendChild(form);
  }

  async function loadAdminGroup() {
    try { renderAdminGroup(await api("GET", "/api/admin/settings/admin-group")); }
    catch (e) { $("admin-group").replaceChildren(el("p", "error", e.message)); }
  }

  // ------------------------------------------------------------ development
  function renderDev(data) {
    const box = $("dev-settings");
    box.replaceChildren();
    fillDatalist("domain-groups", data.groups);
    box.appendChild(el("p", "muted", "Members of this " + (data.domain || "domain") + " group may pull Mnemerynne's raw " +
      "internals from their Claude with the diagnose tool (tool-call logs with errors, retrieval scores for a question, " +
      "a file's indexing state, the job queue, recent server log, effective settings without secrets), to find out why " +
      "a tool call or a search went wrong. Nothing it returns changes anything. Leave it empty to switch diagnostics off."));
    if (!data.directory) box.appendChild(el("p", "error", "No AD domain is configured on the server (MNEMERYNNE_AD_DOMAIN), so nobody can be in the group."));
    const form = el("form", "ax-new");
    const input = el("input");
    input.type = "text";
    input.value = data.group || "";
    input.placeholder = "No group: diagnostics are off";
    input.setAttribute("list", "domain-groups");
    input.setAttribute("aria-label", "Development group");
    const save = el("button", "small", "Save");
    save.type = "submit";
    form.append(input, save);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try { renderDev(await api("PUT", "/api/admin/settings/diagnostics", { group: input.value.trim() })); }
      catch (err) { showError(err.message); }
    });
    box.appendChild(form);
    box.appendChild(el("p", "muted small", data.enabled
      ? "On for: " + (data.members.length ? data.members.join(", ") : "nobody yet (no linked account in that group).")
      : "Off."));
  }

  async function loadDev() {
    try { renderDev(await api("GET", "/api/admin/settings/diagnostics")); }
    catch (e) { $("dev-settings").replaceChildren(el("p", "error", e.message)); }
  }

  // --------------------------------------------- mail and calendar for Claude
  function renderClaudeMail(data) {
    const box = $("claude-mail-settings");
    box.replaceChildren();
    fillDatalist("domain-groups", data.groups);
    box.appendChild(el("p", "muted", "Members of this " + (data.domain || "domain") + " group get mail and calendar " +
      "tools in their Claude through the connector: search and read their own mail and the shared mailboxes they may " +
      "use, see their calendar, and draft replies and new emails as proposals they save to Outlook Drafts themselves. " +
      "Nothing is ever sent, moved or deleted. What Claude reads goes to Anthropic, so put only people in it who " +
      "accept that; each still needs a linked Microsoft 365 account on Preferences. Leave it empty to keep mail away " +
      "from Claude."));
    if (!data.directory) box.appendChild(el("p", "error", "No AD domain is configured on the server (MNEMERYNNE_AD_DOMAIN), so nobody can be in the group."));
    const form = el("form", "ax-new");
    const input = el("input");
    input.type = "text";
    input.value = data.group || "";
    input.placeholder = "No group: mail stays away from Claude";
    input.setAttribute("list", "domain-groups");
    input.setAttribute("aria-label", "Mail group for Claude");
    const save = el("button", "small", "Save");
    save.type = "submit";
    form.append(input, save);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try { renderClaudeMail(await api("PUT", "/api/admin/settings/claude-mail", { group: input.value.trim() })); }
      catch (err) { showError(err.message); }
    });
    box.appendChild(form);
    box.appendChild(el("p", "muted small", data.enabled
      ? "On for: " + (data.members.length ? data.members.join(", ") : "nobody yet (no linked account in that group).")
      : "Off."));
  }

  async function loadClaudeMail() {
    try { renderClaudeMail(await api("GET", "/api/admin/settings/claude-mail")); }
    catch (e) { $("claude-mail-settings").replaceChildren(el("p", "error", e.message)); }
  }

  // ------------------------------------------------------ organisation wiki
  function renderWikiLibrary(data) {
    const box = $("wiki-settings");
    box.replaceChildren();
    box.appendChild(el("p", "muted", "The Wiki tab shows one library of Markdown pages. By default it's the library " +
      "called " + data.default_name + " (on the share: a folder named " + data.default_name + " at its root, with the " +
      "Windows permissions deciding who reads and who edits). Pick another library here instead."));
    const form = el("form", "ax-new");
    const select = el("select");
    select.setAttribute("aria-label", "Wiki library");
    const byName = el("option", null, "The library called " + data.default_name);
    byName.value = "";
    select.appendChild(byName);
    data.libraries.forEach(function (lib) {
      const o = el("option", null, lib.name);
      o.value = String(lib.id);
      select.appendChild(o);
    });
    select.value = data.source === "setting" ? String(data.library_id) : "";
    const save = el("button", "small", "Save");
    save.type = "submit";
    form.append(select, save);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try {
        renderWikiLibrary(await api("PUT", "/api/admin/settings/wiki-library",
          { library_id: select.value ? Number(select.value) : null }));
      } catch (err) { showError(err.message); }
    });
    box.appendChild(form);
    box.appendChild(el("p", "muted small", data.name
      ? "Showing: " + data.name + (data.source === "name" ? " (by its name)" : "") + "."
      : "No library called " + data.default_name + " yet: the Wiki tab tells people it isn't set up."));
  }

  async function loadWikiLibrary() {
    try { renderWikiLibrary(await api("GET", "/api/admin/settings/wiki-library")); }
    catch (e) { $("wiki-settings").replaceChildren(el("p", "error", e.message)); }
  }

  // -------------------------------------------------------- balancer nodes
  let balancerTimer = null;

  // Swap a node between the usual small models and BFGguf (the big model over all its cards).
  async function swapRole(n, rows) {
    const to = n.role === "bfgguf" ? "balancer" : "bfgguf";
    const others = rows.filter(function (r) { return r.url !== n.url && r.role === "balancer"; }).length;
    let ask = to === "bfgguf"
      ? "Make " + n.url + " a BFGguf node? It stops its small models and loads BFGguf on all its cards; " +
        "that can take a few minutes."
      : "Make " + n.url + " a balancer node again? It unloads BFGguf and starts its small models.";
    if (to === "bfgguf" && !others) ask += "\n\nIt's the last balancer node: until it's switched back, normal " +
      "answers come from the fallback address, and BFGguf gets no MinErynnes.";
    if (!confirm(ask)) return;
    showError("");
    let anyway = false;
    for (;;) {
      try {
        renderBalancer(await api("POST", "/api/admin/settings/balancer/role", { url: n.url, role: to, anyway: anyway }));
        return;
      } catch (err) {
        // The balancer's "needs about X GB but only Y GB is free across the cards" refusal.
        if (!anyway && /free across the cards|fit/i.test(err.message) && confirm(err.message + "\n\nStart it anyway?")) { anyway = true; continue; }
        showError(err.message);
        return;
      }
    }
  }

  function balancerRow(n, rows) {
    let stateText;
    if (n.reachable === null) stateText = "not asked yet";
    else if (!n.reachable) stateText = "✗ " + n.error;
    else if (n.sitting_out_s) stateText = "✗ skipped for " + n.sitting_out_s + " s: " + n.error;
    else if (!n.ready) stateText = "answering, but no model is ready";
    else stateText = "✓ " + n.models.join(", ");
    const bad = n.reachable === false || n.sitting_out_s > 0;
    const role = el("span", "small");
    role.appendChild(document.createTextNode(n.switching ? "switching… " : n.role === "bfgguf" ? "🧠 BFGguf " : "Balancer "));
    if (n.reachable && !n.switching) {
      const swap = el("button", "link small", n.role === "bfgguf" ? "Make balancer" : "Make BFGguf");
      swap.type = "button";
      swap.addEventListener("click", function () { swapRole(n, rows); });
      role.appendChild(swap);
    }
    return [n.url, role, el("span", "small" + (bad ? " error" : ""), stateText), n.ready ? n.in_flight + " / " + n.slots : "–",
            String(n.sent_last_hour), n.polled_s_ago === null ? "–" : n.polled_s_ago + " s ago"];
  }

  function renderBalancer(data) {
    const box = $("balancer-settings");
    box.replaceChildren();
    box.appendChild(el("p", "muted", "mnemerynne-balancer addresses, separated by commas. Each answer goes to " +
      "the node with the fewest requests per slot; a node that fails sits out for 30 seconds and the next one " +
      "is used. With none set, or none answering, answers come from " + data.fallback + "."));
    const form = el("form");
    const grid = el("div", "agent-settings");
    const field = function (label, id, control) {
      control.id = id;
      const l = el("label", "small", label);
      l.htmlFor = id;
      grid.append(l, control);
      return control;
    };
    const nodes = field("Nodes", "balancer-nodes", agentInput("text", data.nodes.join(", ")));
    nodes.placeholder = "http://10.0.5.20:9180, http://10.0.5.21:9180";
    const key = field("API key", "balancer-key", agentInput("password", ""));
    key.placeholder = data.api_key_set ? "Set; leave blank to keep" : "BALANCER_API_KEY from the balancer";
    key.autocomplete = "off";
    form.appendChild(grid);
    const actions = el("div", "ax-new");
    const save = el("button", "small", "Save");
    save.type = "submit";
    actions.appendChild(save);
    actions.appendChild(el("span", "small muted", "Each node is checked with the key before the list is saved."));
    form.appendChild(actions);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      const body = { nodes: nodes.value.trim() };
      if (key.value) body.api_key = key.value;
      save.disabled = true;
      try { renderBalancer(await api("PUT", "/api/admin/settings/balancer", body)); }
      catch (err) { showError(err.message); }
      finally { save.disabled = false; }
    });
    box.appendChild(form);
    const statusBox = el("div", "table-wrap");
    statusBox.id = "balancer-status";
    box.appendChild(statusBox);
    renderBalancerStatus(data.status);
  }

  function renderBalancerStatus(rows) {
    const wrap = $("balancer-status");
    if (!wrap) return;
    if (!rows.length) { wrap.replaceChildren(); return; }
    wrap.replaceChildren(table(["Node", "Role", "State", "In flight / slots", "Answers (1 h)", "Checked"],
                               rows.map(function (n) { return balancerRow(n, rows); })));
  }

  async function loadBalancer() {
    try { renderBalancer(await api("GET", "/api/admin/settings/balancer")); }
    catch (e) { $("balancer-settings").replaceChildren(el("p", "error", e.message)); return; }
    // Keep the state column live while Settings is open (only the table; typing isn't disturbed).
    clearInterval(balancerTimer);
    balancerTimer = setInterval(async function () {
      if (state.tab !== "settings" || document.hidden) return;
      try { renderBalancerStatus((await api("GET", "/api/admin/settings/balancer")).status); } catch (e) { /* next time */ }
    }, 5000);
  }

  // ---------------------------------------------------------- Erynne agent
  function agentField(grid, label, control) {
    const id = "agent-" + label.toLowerCase().replace(/[^a-z]+/g, "-");
    control.id = id;
    const l = el("label", "small", label);
    l.htmlFor = id;
    grid.append(l, control);
    return control;
  }

  function agentInput(type, value) {
    const i = el("input");
    i.type = type;
    if (type === "checkbox") i.checked = !!value; else i.value = value === null || value === undefined ? "" : value;
    return i;
  }

  function renderAgent(data) {
    const box = $("agent-settings");
    box.replaceChildren();
    if (data.groups && data.groups.length) fillDatalist("domain-groups", data.groups);
    box.appendChild(el("p", "muted", "Everyone gets Erynne with tools in chat: she searches, opens files and asks " +
      "internal systems, and drafts changes to .txt, .md, .docx and .xlsx files that people apply with a click. " +
      "Members of this " + (data.domain || "domain") + " group may have her change files anywhere they may edit, " +
      "draft their email and propose API actions; everyone else only in their own Agents folder. People can " +
      "switch single tools off in chat (Advanced settings). Leave it empty to keep everyone to their Agents folder."));
    if (!data.enabled) box.appendChild(el("p", "error", "No AD domain is configured on the server (MNEMERYNNE_AD_DOMAIN), so nobody can be in the group."));

    const groupForm = el("form", "ax-new");
    const group = agentInput("text", data.group);
    group.setAttribute("list", "domain-groups");
    group.setAttribute("aria-label", "Agent group");
    group.placeholder = "No group: the agent is off";
    const saveGroup = el("button", "small", "Save");
    saveGroup.type = "submit";
    groupForm.append(group, saveGroup);
    groupForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try { renderAgent(await api("PUT", "/api/admin/settings/agent-group", { group: group.value.trim() })); }
      catch (err) { showError(err.message); }
    });
    box.appendChild(groupForm);
    if (data.group && data.members) {
      box.appendChild(el("p", "small muted", data.members.length
        ? "In the group now: " + data.members.join(", ") + "."
        : "No one linked to a domain account is in this group yet (Accounts tab)."));
    }

    const m = data.model;
    box.appendChild(el("h3", "ax-h small", "Agent model"));
    box.appendChild(el("p", "small muted", "The agent uses the same model as the answers: the balancer nodes " +
      "below, the least busy first, then " + data.fallback + " when none answers. If nothing answers, " +
      "everyone gets the normal answers. These are its limits:"));
    const form = el("form");
    const grid = el("div", "agent-settings");
    const timeout = agentField(grid, "Timeout (seconds)", agentInput("number", m.timeout));
    const steps = agentField(grid, "Most tool steps", agentInput("number", m.max_steps));
    const maxTokens = agentField(grid, "Tokens per turn", agentInput("number", m.max_tokens));
    const reasoning = agentField(grid, "Thinking tokens", agentInput("number", m.reasoning_tokens));
    const context = agentField(grid, "Context per request (tokens)", agentInput("number", m.context_tokens));
    context.title = "The model server's -c divided by -np; older tool results are shortened to fit";
    const thinking = agentField(grid, "Let it think", agentInput("checkbox", m.thinking));
    form.appendChild(grid);
    const actions = el("div", "ax-new");
    const save = el("button", "small", "Save");
    save.type = "submit";
    actions.appendChild(save);
    const via = data.nodes ? data.nodes + " balancer node" + (data.nodes === 1 ? "" : "s") + " or " + data.fallback
      : data.fallback;
    const reach = data.reachable === null ? "not checked"
      : data.reachable ? "✓ answering (" + via + ")" : "✗ not answering (" + via + "): everyone gets the normal answers";
    actions.appendChild(el("span", "small " + (data.reachable === false ? "error" : "muted"), "Model: " + reach));
    form.appendChild(actions);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      const body = { timeout: Number(timeout.value), max_steps: Number(steps.value),
                     max_tokens: Number(maxTokens.value), reasoning_tokens: Number(reasoning.value),
                     context_tokens: Number(context.value),
                     thinking: thinking.checked };
      try { renderAgent(await api("PUT", "/api/admin/settings/agent-model", body)); }
      catch (err) { showError(err.message); }
    });
    box.appendChild(form);
  }

  function renderClaude(data) {
    const box = $("claude-settings");
    box.replaceChildren();
    if (data.groups && data.groups.length) fillDatalist("domain-groups", data.groups);
    box.appendChild(el("p", "muted", "Members of this " + (data.domain || "domain") + " group may tick Use External API " +
      "in chat: Claude answers with Erynne's tools, using each person's own Anthropic API key (saved on their " +
      "Preferences page, billed to them). It gets Erynne's full reach: everything they can read, their internal " +
      "systems, mail and calendar if they're in the agent group, memories and the conversation, all sent to " +
      "Anthropic under their key. Put only people in it who accept that. It can hand reading to up to two " +
      "MinErynnes (small local Erynnes). Leave it empty to turn it off."));
    if (!data.enabled) box.appendChild(el("p", "error", "No AD domain is configured on the server (MNEMERYNNE_AD_DOMAIN), so nobody can be in the group."));
    if (!data.secret_key_set) box.appendChild(el("p", "error", "MNEMERYNNE_SECRET_KEY isn't set on the server, so nobody can save a key yet."));

    const form = el("form", "ax-new");
    const group = agentInput("text", data.group);
    group.setAttribute("list", "domain-groups");
    group.setAttribute("aria-label", "Claude group");
    group.placeholder = "No group: Claude in chat is off";
    const save = el("button", "small", "Save");
    save.type = "submit";
    form.append(group, save);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try { renderClaude(await api("PUT", "/api/admin/settings/claude-group", { group: group.value.trim() })); }
      catch (err) { showError(err.message); }
    });
    box.appendChild(form);
    if (data.group && data.members) {
      box.appendChild(el("p", "small muted", data.members.length
        ? "In the group now: " + data.members.map(function (m) {
            return m.username + (m.key ? " (key saved)" : " (no key yet)");
          }).join(", ") + "."
        : "No one linked to a domain account is in this group yet (Accounts tab)."));
    }
  }

  // ----------------------------------------------------- experimental (BFGguf)
  function renderExperimental(data) {
    const box = $("experimental-settings");
    box.replaceChildren();
    if (data.groups && data.groups.length) fillDatalist("domain-groups", data.groups);
    box.appendChild(el("p", "muted", "Members of this " + (data.domain || "domain") + " group get experimental " +
      "features. Now: a 🧠 BFGguf toggle in chat, which has BFGguf (a much bigger model) answer the whole question " +
      "with Erynne's documents and tools, sending MinErynnes to read while a balancer node is up; and POST " +
      "/api/bfgguf for scripts. Leave it empty to turn it off."));
    if (!data.enabled) box.appendChild(el("p", "error", "No AD domain is configured on the server (MNEMERYNNE_AD_DOMAIN), so nobody can be in the group."));
    const groupForm = el("form", "ax-new");
    const group = agentInput("text", data.group);
    group.setAttribute("list", "domain-groups");
    group.setAttribute("aria-label", "Experimental group");
    group.placeholder = "No group: experimental features are off";
    const saveGroup = el("button", "small", "Save");
    saveGroup.type = "submit";
    groupForm.append(group, saveGroup);
    groupForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try { renderExperimental(await api("PUT", "/api/admin/settings/experimental-group", { group: group.value.trim() })); }
      catch (err) { showError(err.message); }
    });
    box.appendChild(groupForm);
    if (data.group && data.members) {
      box.appendChild(el("p", "small muted", data.members.length ? "In the group now: " + data.members.join(", ") + "."
        : "No one linked to a domain account is in this group yet (Accounts tab)."));
    }

    const b = data.bfgguf;
    box.appendChild(el("h3", "small", "Where BFGguf runs"));
    const form = el("form");
    const grid = el("div", "agent-settings");
    const field = function (label, id, control) {
      control.id = id;
      const l = el("label", "small", label);
      l.htmlFor = id;
      grid.append(l, control);
      return control;
    };
    const kind = el("select");
    [["balancer", "Balancer nodes in the BFGguf role"], ["local", "A server on the network (OpenAI-compatible)"],
     ["openai", "A hosted API (OpenAI-compatible, with a key)"]].forEach(function (o) {
      const opt = el("option", null, o[1]);
      opt.value = o[0];
      kind.appendChild(opt);
    });
    kind.value = b.kind || "balancer";
    field("Runs on", "bfgguf-kind", kind);
    const url = field("Address", "bfgguf-url", agentInput("text", b.url));
    url.placeholder = "http://10.0.5.30:8080";
    const model = field("Model", "bfgguf-model", agentInput("text", b.model));
    const key = field("API key", "bfgguf-key", agentInput("password", ""));
    key.autocomplete = "off";
    const timeout = field("Timeout (s)", "bfgguf-timeout", agentInput("number", b.timeout));
    timeout.min = 10; timeout.max = 1800;
    const nodesNote = el("p", "small muted");
    const hostedNote = el("p", "small error", "A hosted API receives each question, the passages and tool results " +
      "Erynne finds, and the person's memories; its tokens count as external.");
    function sync() {
      const onBalancer = kind.value === "balancer";
      [url, key].forEach(function (c) { c.disabled = onBalancer; });
      model.placeholder = onBalancer ? "Optional: the node's model is used" : kind.value === "openai" ? "Required" : "Optional (\"local\")";
      key.placeholder = b.api_key_set ? "Set; leave blank to keep" : kind.value === "openai" ? "Required by most hosted APIs" : "Optional";
      nodesNote.hidden = !onBalancer;
      nodesNote.textContent = data.bfgguf_nodes.length
        ? "BFGguf nodes now: " + data.bfgguf_nodes.map(function (n) {
            return n.url + (n.ready ? " (" + n.models.join(", ") + ")" : " (not ready)");
          }).join("; ") + "."
        : "No balancer node is in the BFGguf role yet: switch one under Balancer nodes above.";
      hostedNote.hidden = kind.value !== "openai";
    }
    kind.addEventListener("change", sync);
    sync();
    form.append(grid, nodesNote, hostedNote);
    const actions = el("div", "ax-new");
    const save = el("button", "small", "Save");
    save.type = "submit";
    actions.append(save, el("span", "small muted", b.kind ? "Set up." : "Not set up yet."),
                   el("span", "small muted", "BFGguf must answer before this is saved."));
    form.appendChild(actions);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      const body = { kind: kind.value, url: url.value.trim(), model: model.value.trim(),
                     timeout: Number(timeout.value) || 300 };
      if (key.value && kind.value !== "balancer") body.api_key = key.value;
      save.disabled = true;
      try { renderExperimental(await api("PUT", "/api/admin/settings/bfgguf", body)); }
      catch (err) { showError(err.message); }
      finally { save.disabled = false; }
    });
    box.appendChild(form);
  }

  async function loadExperimental() {
    try { renderExperimental(await api("GET", "/api/admin/settings/experimental")); }
    catch (e) { $("experimental-settings").replaceChildren(el("p", "error", e.message)); }
  }

  function renderChatGPT(data) {
    const box = $("chatgpt-settings");
    box.replaceChildren();
    box.appendChild(el("p", "muted", "Lets people connect ChatGPT to " + data.mcp_url + " (in ChatGPT: add a " +
      "custom MCP server with that address and OAuth). It sees exactly what is shared with Claude: the same " +
      "libraries and APIs, so turning this on also sends those to OpenAI. Turning it off ends every ChatGPT " +
      "connection; people connect again if it's turned back on."));
    const form = el("form", "ax-new");
    const on = agentInput("checkbox", data.enabled);
    on.setAttribute("aria-label", "Allow ChatGPT");
    const onLabel = el("label", "check small");
    onLabel.append(on, document.createTextNode(" Allow ChatGPT connections"));
    const save = el("button", "small", "Save");
    save.type = "submit";
    form.append(onLabel, save, el("span", "small muted", data.connections + " connected"));
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      if (data.enabled && !on.checked && data.connections &&
          !confirm("End all " + data.connections + " ChatGPT connection(s)?")) return;
      try {
        renderChatGPT(await api("PUT", "/api/admin/settings/chatgpt", { enabled: on.checked }));
      } catch (err) { showError(err.message); }
    });
    box.appendChild(form);
  }

  // ------------------------------------------------------------- Rooms
  function renderRooms(data) {
    const box = $("rooms-settings");
    box.replaceChildren();
    box.appendChild(el("p", "muted", "Rooms is the team chat (channels and direct messages). Anyone can message " +
      "anyone; this group may make channels (leave it empty for everyone). Admins can archive any channel, and " +
      "delete any message from the room itself."));
    const onForm = el("form", "ax-new");
    const on = agentInput("checkbox", data.enabled);
    on.setAttribute("aria-label", "Rooms on");
    const onLabel = el("label", "check small");
    onLabel.append(on, document.createTextNode(" Rooms is on for this organisation"));
    const saveOn = el("button", "small", "Save");
    saveOn.type = "submit";
    onForm.append(onLabel, saveOn, el("span", "small muted", data.enabled
      ? "Turning it off hides Rooms from everyone (and from Claude); messages are kept for when it's back."
      : "Off: nobody sees Rooms. Turn it on to give everyone the Rooms tab."));
    onForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      if (data.enabled && !on.checked && !confirm("Turn Rooms off for everyone? Messages are kept.")) return;
      try { renderRooms(await api("PUT", "/api/admin/settings/rooms", { enabled: on.checked })); }
      catch (err) { showError(err.message); }
    });
    box.appendChild(onForm);
    if (!data.enabled && !data.channels.length) return;
    const form = el("form", "ax-new");
    const group = agentInput("text", data.creators);
    group.setAttribute("list", "domain-groups");
    group.setAttribute("aria-label", "Who may make channels");
    group.placeholder = "Everyone may make channels";
    const save = el("button", "small", "Save");
    save.type = "submit";
    form.append(group, save);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try { renderRooms(await api("PUT", "/api/admin/settings/rooms", { group: group.value.trim() })); }
      catch (err) { showError(err.message); }
    });
    box.appendChild(form);
    if (!data.channels.length) { box.appendChild(el("p", "small muted", "No channels yet.")); return; }
    box.appendChild(table(["Channel", "Who", "Messages", "Last", ""], data.channels.map(function (c) {
      const toggle = el("button", "link small", c.archived ? "Unarchive" : "Archive");
      toggle.type = "button";
      toggle.addEventListener("click", async function () {
        if (!confirm((c.archived ? "Unarchive" : "Archive") + " #" + c.name + "?")) return;
        try { await api("PATCH", "/api/rooms/" + c.id, { archived: !c.archived }); loadRooms(); }
        catch (err) { showError(err.message); }
      });
      return ["#" + c.name + (c.archived ? " (archived)" : ""),
              c.visibility === "group" ? "group: " + c.group : c.visibility, String(c.messages), c.last_at || "–", toggle];
    })));
  }

  async function loadRooms() {
    try { renderRooms(await api("GET", "/api/admin/settings/rooms")); }
    catch (e) { $("rooms-settings").replaceChildren(el("p", "error", e.message)); }
  }

  // ------------------------------------------------------------- Media
  function renderMedia(data) {
    const box = $("media-settings");
    box.replaceChildren();
    box.appendChild(el("p", "muted", "A Kodi-style media centre: films, music and pictures from the Media folder on " +
      "the share (Music, Movies and Pictures inside it), plus live streams and internet radio. Who sees what follows " +
      "the share's permissions on those folders."));
    async function save(body) {
      showError("");
      try { renderMedia(await api("PUT", "/api/admin/settings/media", body)); }
      catch (err) { showError(err.message); }
    }

    const onForm = el("form", "ax-new");
    const on = agentInput("checkbox", data.enabled);
    on.setAttribute("aria-label", "Media on");
    const onLabel = el("label", "check small");
    onLabel.append(on, document.createTextNode(" Media is on for this organisation"));
    const saveOn = el("button", "small", "Save");
    saveOn.type = "submit";
    onForm.append(onLabel, saveOn, el("span", "small muted", data.enabled
      ? "Turning it off hides Media from everyone and stops playback; the files and the catalog are kept."
      : "Off: nobody sees Media. Turning it on makes Music, Movies and Pictures in the Media folder below."));
    onForm.addEventListener("submit", function (e) {
      e.preventDefault();
      if (data.enabled && !on.checked && !confirm("Turn Media off for everyone? Files and the catalog are kept.")) return;
      save({ enabled: on.checked });
    });
    box.appendChild(onForm);

    // Which folder at the top of the share is Media: "Media" unless an admin picks another.
    const current = data.library ? data.library.share_path : data.settings.folder;
    const folderForm = el("form", "ax-new");
    const folder = agentInput("text", current);
    folder.setAttribute("aria-label", "Media folder");
    folder.setAttribute("list", "media-folders");
    folder.placeholder = "Media";
    const known = el("datalist");
    known.id = "media-folders";
    data.folders.forEach(function (f) { known.appendChild(el("option", null, f)); });
    const use = el("button", "small", "Use this folder");
    use.type = "submit";
    folderForm.append(el("span", "small", "Media folder on the share:"), folder, known, use);
    folderForm.addEventListener("submit", function (e) {
      e.preventDefault();
      const name = folder.value.trim();
      if (!name || name.toLowerCase() === String(current).toLowerCase()) return;
      const exists = data.folders.some(function (f) { return f.toLowerCase() === name.toLowerCase(); });
      if (!confirm((exists ? "Use the existing folder “" + name + "”" : "Make a new folder “" + name + "”") +
                   " on the share as Media? Music, Movies and Pictures are made inside it if missing. " +
                   "The catalog of “" + current + "” is kept for switching back.")) return;
      save({ folder: name });
    });
    box.appendChild(folderForm);
    if (!data.enabled && !data.library) return;

    const c = data.catalog;
    const facts = [];
    if (data.library) facts.push("Folder: " + data.library.share_path);
    facts.push(c.counts.movies + " films, " + c.counts.music + " tracks, " + c.counts.pictures + " pictures");
    if (c.last_pass) {
      facts.push("last catalog pass " + c.last_pass.at + " UTC" + (c.last_pass.pending ? ", " + c.last_pass.pending + " waiting" : ""));
    }
    box.appendChild(el("p", "small", facts.join(" · ")));
    if (c.errors.length) {
      const details = el("details", "small");
      details.appendChild(el("summary", null, c.errors.length + " file(s) couldn't be read"));
      const ul = el("ul");
      c.errors.forEach(function (e) { ul.appendChild(el("li", null, e.name + ": " + e.error)); });
      details.appendChild(ul);
      box.appendChild(details);
    }

    const s = data.settings;
    const form = el("form");
    const grid = el("div", "agent-settings");
    const hw = el("select");
    [["auto", "Automatic (AMD VAAPI, then NVIDIA NVENC, then CPU)"], ["vaapi", "AMD GPU (VAAPI)"],
     ["nvenc", "NVIDIA GPU (NVENC)"], ["software", "CPU only (libx264)"]].forEach(function (o) {
      const opt = el("option", null, o[1]);
      opt.value = o[0];
      hw.appendChild(opt);
    });
    hw.value = s.hwaccel;
    const slots = agentInput("number", s.max_transcodes);
    slots.min = "0"; slots.max = "8";
    const online = agentInput("checkbox", s.online_metadata);
    const tmdb = agentInput("password", "");
    tmdb.placeholder = data.tmdb_key_set ? "TMDB key saved — type to replace" : "TMDB API key or read token";
    tmdb.autocomplete = "off";
    const radio = agentInput("url", s.radio_directory_url);
    radio.placeholder = "https://sadgirlsclub.wtf/rr/healthz";
    const mediaGroup = agentInput("text", s.media_group);
    mediaGroup.setAttribute("list", "domain-groups");
    mediaGroup.placeholder = "Nobody: Media is hidden";
    const screensGroup = agentInput("text", s.screens_group);
    screensGroup.setAttribute("list", "domain-groups");
    screensGroup.placeholder = "Nobody: screens are off";
    agentField(grid, "Transcoding", hw).title = "For films the browser can't play as they are";
    agentField(grid, "Transcodes at once", slots).title = "0 turns transcoding off; copying into HLS (remux) doesn't count";
    agentField(grid, "Look up artwork online", online).title = "TMDB for films, MusicBrainz and the Cover Art Archive for albums";
    agentField(grid, "TMDB key", tmdb).title = "themoviedb.org → Settings → API; only film titles and years are sent";
    agentField(grid, "Radio station list", radio).title = "SGCRelay's /healthz; empty for none";
    agentField(grid, "Who may use Media", mediaGroup).title =
      "AD or Entra group(s), separated by ;. Only members see the Media tab; within it, the share's folder " +
      "permissions still decide which files. Empty: nobody. Leaving the group closes it at once.";
    agentField(grid, "Who may use screens", screensGroup).title =
      "AD or Entra group(s), separated by ;. Members may pair a TV as a screen and cast to it from their phone. " +
      "Empty: nobody. Leaving the group stops their screens at once.";
    form.appendChild(grid);
    const actions = el("div", "ax-new");
    const saveOpts = el("button", "small", "Save");
    saveOpts.type = "submit";
    actions.appendChild(saveOpts);
    if (data.tmdb_key_set) {
      const drop = el("button", "link small", "Remove TMDB key");
      drop.type = "button";
      drop.addEventListener("click", function () { save({ tmdb_key: "" }); });
      actions.appendChild(drop);
    }
    actions.appendChild(el("span", "small muted", "Online lookups send only film titles and years, and album and artist names."));
    form.appendChild(actions);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      const body = { hwaccel: hw.value, max_transcodes: Number(slots.value), online_metadata: online.checked,
                     radio_directory_url: radio.value.trim(), media_group: mediaGroup.value.trim(),
                     screens_group: screensGroup.value.trim() };
      if (tmdb.value.trim()) body.tmdb_key = tmdb.value.trim();
      save(body);
    });
    box.appendChild(form);

    const tools = el("div", "ax-new");
    const test = el("button", "small", "Test transcoding");
    test.type = "button";
    const result = el("span", "small muted");
    test.addEventListener("click", async function () {
      test.disabled = true;
      result.textContent = "Testing each encoder (a few seconds)…";
      try {
        const r = (await api("POST", "/api/admin/media/hwtest")).backends;
        result.textContent = Object.keys(r).map(function (k) {
          return k + (r[k].ok ? " ✓" : " ✗ " + r[k].detail) + (r[k].device ? " (" + r[k].device + ")" : "");
        }).join(" · ");
      } catch (err) { result.textContent = err.message; }
      test.disabled = false;
    });
    const reread = el("button", "link small", "Read every file again");
    reread.type = "button";
    reread.addEventListener("click", async function () {
      try { const r = await api("POST", "/api/admin/media/rescan", { online: false }); result.textContent = r.queued + " item(s) will be read again on the next pass."; }
      catch (err) { showError(err.message); }
    });
    const relook = el("button", "link small", "Look everything up online again");
    relook.type = "button";
    relook.addEventListener("click", async function () {
      try { const r = await api("POST", "/api/admin/media/rescan", { online: true }); result.textContent = r.queued + " item(s) will be read and looked up again."; }
      catch (err) { showError(err.message); }
    });
    tools.append(test, reread, relook, result);
    box.appendChild(tools);

    box.appendChild(el("h3", "ax-h small", "Live streams and stations"));
    if (data.sources.length) {
      box.appendChild(table(["Name", "Kind", "Address", "", ""], data.sources.map(function (src) {
        const toggle = el("button", "link small", src.enabled ? "Turn off" : "Turn on");
        toggle.type = "button";
        toggle.addEventListener("click", async function () {
          try { renderMedia(await api("PUT", "/api/admin/media/sources/" + src.id,
                                      { kind: src.kind, name: src.name, url: src.url, enabled: !src.enabled })); }
          catch (err) { showError(err.message); }
        });
        const drop = el("button", "link small danger", "Remove");
        drop.type = "button";
        drop.addEventListener("click", async function () {
          if (!confirm("Remove " + src.name + "?")) return;
          try { renderMedia(await api("DELETE", "/api/admin/media/sources/" + src.id)); }
          catch (err) { showError(err.message); }
        });
        return [src.name + (src.enabled ? "" : " (off)"), src.kind.toUpperCase(), src.url, toggle, drop];
      })));
    } else {
      box.appendChild(el("p", "small muted", "None yet. RTMP, HLS and YouTube sources show under Live TV; stations under Radio, after SGCRelay's."));
    }
    const add = el("form", "ax-new");
    const kind = el("select");
    [["hls", "HLS stream"], ["rtmp", "RTMP stream"], ["youtube", "YouTube link"], ["radio", "Radio station"]].forEach(function (o) {
      const opt = el("option", null, o[1]);
      opt.value = o[0];
      kind.appendChild(opt);
    });
    kind.setAttribute("aria-label", "Kind");
    const name = agentInput("text", "");
    name.placeholder = "Name";
    name.setAttribute("aria-label", "Name");
    const url = agentInput("text", "");
    url.placeholder = "http://10.1.1.21/stream/hls/live.m3u8";
    url.setAttribute("aria-label", "Address");
    kind.addEventListener("change", function () {
      url.placeholder = { hls: "http://10.1.1.21/stream/hls/live.m3u8", rtmp: "rtmp://10.1.1.21/live/stream",
                          youtube: "A video, playlist or youtube.com/channel/UC… (its live stream) link",
                          radio: "https://example.org/stream.mp3" }[kind.value];
    });
    const addBtn = el("button", "small", "Add");
    addBtn.type = "submit";
    add.append(kind, name, url, addBtn);
    add.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try { renderMedia(await api("POST", "/api/admin/media/sources", { kind: kind.value, name: name.value, url: url.value })); }
      catch (err) { showError(err.message); }
    });
    box.appendChild(add);

    if (data.sessions.length) {
      box.appendChild(el("h3", "ax-h small", "Playing now"));
      box.appendChild(table(["Session", "Mode", "Encoder", "Idle"], data.sessions.map(function (x) {
        return [x.id, x.mode + (x.source_id ? " (live)" : ""), x.backend || "–", x.idle_s + " s"];
      })));
    }
  }

  async function loadMedia() {
    try { renderMedia(await api("GET", "/api/admin/settings/media")); }
    catch (e) { $("media-settings").replaceChildren(el("p", "error", e.message)); }
  }

  async function loadChatGPT() {
    try { renderChatGPT(await api("GET", "/api/admin/settings/chatgpt")); }
    catch (e) { $("chatgpt-settings").replaceChildren(el("p", "error", e.message)); }
  }

  function renderClaudeLinks(data) {
    const box = $("claude-links-settings");
    box.replaceChildren();
    box.appendChild(el("p", "muted", "When Claude (through the connector) finds a file, it also gets a private " +
      "address it can open itself to read that file's text, without signing in. Each address belongs to one " +
      "person's Claude connection, works only while the file is still shared with Claude, ends when the " +
      "connection is removed, and expires after the time below (at most " + data.limits.fetches + " reads). " +
      "Anyone given the address could read that file's text until then, e.g. from a shared Claude chat."));
    if (!data.public_url) box.appendChild(el("p", "error", "MNEMERYNNE_PUBLIC_URL isn't set on the server, so there's no address to hand out."));
    const form = el("form", "ax-new");
    const on = agentInput("checkbox", data.enabled);
    on.setAttribute("aria-label", "Links on");
    const minutes = agentInput("number", data.minutes);
    minutes.min = data.limits.minutes[0];
    minutes.max = data.limits.minutes[1];
    minutes.setAttribute("aria-label", "Minutes each link lasts");
    const save = el("button", "small", "Save");
    save.type = "submit";
    const onLabel = el("label", "check small");
    onLabel.append(on, document.createTextNode(" On, lasting"));
    form.append(onLabel, minutes, el("span", "small muted", "minutes"), save);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try {
        renderClaudeLinks(await api("PUT", "/api/admin/settings/claude-links",
                                    { enabled: on.checked, minutes: Number(minutes.value) }));
      } catch (err) { showError(err.message); }
    });
    box.appendChild(form);
  }

  async function loadClaudeLinks() {
    try { renderClaudeLinks(await api("GET", "/api/admin/settings/claude-links")); }
    catch (e) { $("claude-links-settings").replaceChildren(el("p", "error", e.message)); }
  }

  async function loadClaude() {
    try { renderClaude(await api("GET", "/api/admin/settings/claude")); }
    catch (e) { $("claude-settings").replaceChildren(el("p", "error", e.message)); }
  }

  async function loadAgent() {
    try { renderAgent(await api("GET", "/api/admin/settings/agent")); }
    catch (e) { $("agent-settings").replaceChildren(el("p", "error", e.message)); }
  }

  let updatesLoaded = false;
  async function loadUpdates(refresh) {
    updatesLoaded = true;
    try {
      renderUpdates(await api("GET", "/api/admin/updates" + (refresh ? "?refresh=1" : "")));
    } catch (e) {
      $("updates").replaceChildren(el("p", "error", e.message));
    }
  }

  // ------------------------------------------------------------ tabs, hash
  function showTab(tab) {
    state.tab = tab;
    document.querySelectorAll("[data-tab]").forEach(function (b) {
      b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    });
    document.querySelectorAll("[data-panel]").forEach(function (p) { p.hidden = p.dataset.panel !== tab; });
    // Only contact GitHub when someone opens Settings.
    if (tab === "settings" && !updatesLoaded) {
      loadUpdates(false); loadAdminGroup(); loadBalancer(); loadWikiLibrary(); loadAgent(); loadClaude(); loadClaudeMail(); loadClaudeLinks(); loadChatGPT(); loadExperimental(); loadRooms(); loadMedia(); loadDev();
      if (window.ErynneM365) window.ErynneM365.load();   // admin_m365.js
    }
    if (tab === "accounts") loadAccounts();
    if (tab === "apis" && window.ErynneApis) window.ErynneApis.load();   // admin_apis.js
    if (tab === "remote" && window.ErynneRemote) window.ErynneRemote.load();   // admin_remote.js
    if (tab === "approvals" && window.ErynneApprovals) window.ErynneApprovals.load();   // admin_approvals.js
    if (tab === "announcements" && window.ErynneNotices) window.ErynneNotices.load();   // admin_notices.js
    if (tab === "signage" && window.ErynneSignage) window.ErynneSignage.load();   // admin_signage.js
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
