/* Preferences → Desktop app (web/desktop.py): the installer to download, and the computers where the app is signed
   in, each with Sign out. Nothing here uses innerHTML. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;                     // the shared helpers (erynne.js)
  const box = document.getElementById("desktop-box");
  if (!box) return;                                      // not in the Desktop app group: no card on this page

  function size(bytes) {                                 // 52428800 -> "50.0 MB"
    const units = ["bytes", "KB", "MB", "GB"];
    let n = bytes, i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return i ? n.toFixed(1) + " " + units[i] : n + " bytes";
  }

  function note(text) {                                  // an error line under the card ("" hides it)
    const node = document.getElementById("desktop-note");
    node.textContent = text;
    node.className = "error";
    node.hidden = !text;
  }

  function when(utc) {                                   // "2026-11-25 22:40:00" (UTC) -> the reader's local time
    return utc ? new Date(utc.replace(" ", "T") + "Z").toLocaleString() : "never";
  }

  function render(data) {                                // draws the download link and the signed-in computers
    box.replaceChildren();
    const get = el("p", "small");
    if (data.download) {
      get.appendChild(document.createTextNode("Get the app " + data.latest + ": "));
      const a = el("a", "", "Windows installer (" + size(data.size) + ")");
      a.href = data.download;                            // a plain download; the browser saves it
      get.appendChild(a);
    } else {
      get.appendChild(document.createTextNode("No installer has been published on this server yet; ask an admin."));
    }
    box.appendChild(get);
    if (!data.computers.length) {
      box.appendChild(el("p", "muted small", "The app isn't signed in anywhere as you."));
      return;
    }
    const list = el("ul", "grants");
    data.computers.forEach(function (c) {
      const li = el("li", "small", c.name + " · signed in " + when(c.created_at) + " · last used " + when(c.last_used_at) + " ");
      const out = el("button", "link small", "Sign out");
      out.type = "button";
      out.addEventListener("click", async function () {  // ends that computer's tokens at once
        note("");
        try { await api("DELETE", "/api/desktop/connections/" + c.id); await load(); }
        catch (e) { note(e.message); }
      });
      li.appendChild(out);
      list.appendChild(li);
    });
    box.appendChild(list);
  }

  async function load() {                                // asks the server, then draws the card
    try { render(await api("GET", "/api/desktop/connections")); }
    catch (e) { box.replaceChildren(el("p", "error", e.message)); }
  }

  load();
})();
