/* Preferences → Backups (web/sync.py, sync.py): the app to download, a token for computers that can't sign in
   in a browser, the computers signed in, and each backed-up folder with its Searchable switch. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const box = document.getElementById("backups");
  if (!box) return;

  function size(bytes) {
    const units = ["bytes", "KB", "MB", "GB", "TB"];
    let n = bytes, i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return i ? n.toFixed(1) + " " + units[i] : n + " bytes";
  }

  function note(text, ok) {
    const node = document.getElementById("backups-note");
    node.textContent = text;
    node.className = ok ? "ok-note" : "error";
    node.hidden = !text;
  }

  async function act(fn) {
    note("");
    try { await fn(); await load(); }
    catch (e) { note(e.message); }
  }

  function render(data) {
    box.replaceChildren();

    // The app.
    const get = el("p", "small");
    const links = [["windows", "Windows installer"], ["linux", "Linux (AppImage)"]].filter(function (k) {
      return data.downloads[k[0]];
    });
    if (links.length) {
      get.appendChild(document.createTextNode("Get Mnemerynne Backup " + data.latest + ": "));
      links.forEach(function (k, i) {
        const a = el("a", null, k[1]);
        a.href = data.downloads[k[0]];
        get.appendChild(a);
        if (i < links.length - 1) get.appendChild(document.createTextNode(" · "));
      });
      get.appendChild(document.createTextNode(". Install it, sign in, and pick the folders to back up."));
    } else {
      get.textContent = "The app isn't on this server yet; ask an admin.";
    }
    box.appendChild(get);

    // Folders.
    box.appendChild(el("h3", "small", "Backed-up folders"));
    if (!data.roots.length) {
      box.appendChild(el("p", "muted small", "Nothing yet. Folders you add in the app show here."));
    } else {
      const table = el("table", "small");
      const head = el("tr");
      ["Computer", "Folder", "Files", "Size", "Last sync", "Searchable"].forEach(function (t) { head.appendChild(el("th", null, t)); });
      table.appendChild(head);
      data.roots.forEach(function (r) {
        const row = el("tr");
        [r.computer, r.label + (r.present ? "" : " (deleted)"), String(r.files), size(r.bytes), r.last_sync || "never"]
          .forEach(function (t) { row.appendChild(el("td", null, t)); });
        const cell = el("td");
        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        toggle.checked = r.searchable;
        toggle.disabled = !r.present;
        toggle.title = "On: Erynne can search this backup (its documents are read in the background). " +
          "Claude also needs the folder shown to Claude in Files.";
        toggle.setAttribute("aria-label", "Searchable: " + r.computer + " " + r.label);
        toggle.addEventListener("change", function () {
          act(function () { return api("PATCH", "/api/me/backups/roots/" + r.id, { searchable: toggle.checked }); });
        });
        cell.appendChild(toggle);
        row.appendChild(cell);
        table.appendChild(row);
      });
      const wrap = el("div", "table-wrap");
      wrap.appendChild(table);
      box.appendChild(wrap);
      box.appendChild(el("p", "muted small", "They're in Files under Backups. Deleted files are in the recycle bin " +
        "there, and earlier versions of changed files under each file's versions."));
    }

    // Computers.
    box.appendChild(el("h3", "small", "Computers signed in"));
    if (!data.signins.length) {
      box.appendChild(el("p", "muted small", "None."));
    } else {
      const list = el("ul", "grants");
      data.signins.forEach(function (s) {
        const li = el("li", null, s.name + " · " + (s.last_used_at ? "last seen " + s.last_used_at : "not used yet") + " ");
        const out = el("button", "link danger", "Sign out");
        out.type = "button";
        out.addEventListener("click", function () {
          act(function () { return api("DELETE", "/api/me/backups/signins/" + s.kind + "/" + s.id); });
        });
        li.appendChild(out);
        list.appendChild(li);
      });
      box.appendChild(list);
    }

    // A token, for a computer without a browser (a Linux server).
    const form = el("form", "grant-add");
    const computer = document.createElement("input");
    computer.placeholder = "Computer name";
    computer.required = true;
    computer.maxLength = 80;
    computer.setAttribute("aria-label", "Computer name for the token");
    const make = el("button", null, "Make a sync token");
    make.type = "submit";
    form.append(computer, make);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      act(async function () {
        const made = await api("POST", "/api/sync/tokens", { computer: computer.value.trim() });
        const shown = document.getElementById("backups-token");
        shown.textContent = made.token;
        shown.parentElement.hidden = false;
      });
    });
    box.appendChild(el("p", "muted small", "No browser on that computer? Make a token here and run " +
      "mnemerynne-backup login " + location.origin + " --token there."));
    box.appendChild(form);
  }

  async function load() {
    try { render(await api("GET", "/api/me/backups")); }
    catch (e) { box.replaceChildren(el("p", "error", e.message)); }
  }

  load();
})();
