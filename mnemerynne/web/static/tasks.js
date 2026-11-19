/* Clickable task lists ("- [ ] ..." / "- ✅ ..." in Markdown): the boxes markdown.render_html(tasks=True)
   draws arrive disabled; this enables them where the person may change the file, and a click saves
   at once (POST /api/files/{id}/tasks: the marker plus a "*(done date, who)*" stamp, a version kept).
   The file view attaches itself ([data-task-file] on <main>); the wiki calls ErynneTasks.attach()
   after each page it shows. A 409 means the file changed meanwhile: the box goes back and the page
   reloads, so the next tick is made on what is really there. The HTML swapped in comes from the
   server's renderer (raw HTML in the Markdown escaped there), never from user text. */
(function () {
  "use strict";

  const state = new WeakMap();   // container -> { fileId, version, status, onSaved, busy }

  async function post(url, body) {   // JSON in, JSON out; throws Error(detail) with .status on non-2xx
    const resp = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify(body) });
    let data = null;
    try { data = await resp.json(); } catch (e) { /* empty body */ }
    if (!resp.ok) {
      const err = new Error((data && typeof data.detail === "string" && data.detail) || ("Request failed (" + resp.status + ")"));
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  function boxes(container) { return container.querySelectorAll("input.md-task"); }   // every task box shown

  function setEnabled(container, on) {   // all boxes clickable (on) or held still while a save runs
    boxes(container).forEach(function (b) { b.disabled = !on; });
  }

  function say(s, text, kind) {   // the status line next to Download / Edit, when there is one
    if (!s.status) return;
    s.status.textContent = text;
    s.status.className = "small " + (kind || "muted");
  }

  async function onChange(e) {
    const container = e.currentTarget, box = e.target;
    if (!box.matches || !box.matches("input.md-task")) return;   // some other input: not ours
    const s = state.get(container);
    if (!s || s.busy) { box.checked = !box.checked; return; }    // one save at a time
    const line = Number(box.dataset.line), done = box.checked;
    s.busy = true;
    setEnabled(container, false);
    say(s, "Saving…");
    try {
      const data = await post("/api/files/" + s.fileId + "/tasks", { line: line, done: done, version: s.version });
      s.version = data.version;                 // the next tick is made against the file as saved now
      container.innerHTML = data.html;          // server-rendered: shows the new done stamp too
      const again = container.querySelector('input.md-task[data-line="' + line + '"]');
      if (again) again.focus();                 // keyboard users stay where they were
      say(s, done ? "Ticked and saved" : "Reopened and saved", "ok-note");
      if (s.onSaved) s.onSaved(data);
    } catch (err) {
      box.checked = !done;                      // nothing changed: put the box back
      if (err.status === 409) {                 // the file moved on: show it as it is now
        say(s, err.message + " Reloading…", "error");
        setTimeout(function () { location.reload(); }, 1800);
      } else {
        say(s, err.message, "error");
      }
    } finally {
      s.busy = false;
      setEnabled(container, true);
    }
  }

  /* Make the boxes in `container` clickable for file `fileId` at `version` (call again after new HTML). */
  function attach(container, opts) {
    const first = !state.has(container);
    state.set(container, { fileId: opts.fileId, version: opts.version, status: opts.status || null,
                           onSaved: opts.onSaved || null, busy: false });
    setEnabled(container, true);
    if (first) container.addEventListener("change", onChange);   // one listener per container, ever
  }

  window.ErynneTasks = { attach: attach };

  const main = document.querySelector("[data-task-file]");   // the file view (file_view.html)
  if (main) {
    attach(main, { fileId: Number(main.dataset.taskFile), version: main.dataset.taskVersion,
                   status: document.getElementById("task-status") });
  }
})();
