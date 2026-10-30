/* The Scratchpad page (scratchpad.html, web/scratchpad.py): the person's agents' boards, and directions to them.
   It polls /api/scratchpad/feed (every 5 s while visible, 30 s hidden) with the cursor of the last answer, so only
   what changed comes back. #b:<board> opens a board, #archive the archive. */
(function () {
  "use strict";

  const { el, api, drawer, toast } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const sideDrawer = drawer($("sp-side"));
  const paneDrawer = drawer($("sp-pane"), { right: true });

  const state = { board: null, cursor: null, notes: new Map(), sessions: [], boards: [], replyTo: null,
                  timer: null, showDone: false, archive: false };

  // ------------------------------------------------------------- helpers
  function when(stamp) {
    if (!stamp) return "";
    const t = Date.parse(stamp.replace(" UTC", "Z").replace(" ", "T"));
    if (isNaN(t)) return stamp;
    const mins = Math.round((Date.now() - t) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + " min ago";
    if (mins < 24 * 60) return Math.round(mins / 60) + " h ago";
    return new Date(t).toLocaleString();
  }

  function tag(text, cls) { return el("span", "sp-tag" + (cls ? " " + cls : ""), text); }

  function failed(e) { if (toast) toast(e.message); else window.alert(e.message); }

  async function act(fn) {
    try { await fn(); await poll(); } catch (e) { failed(e); }
  }

  // --------------------------------------------------------------- boards
  function renderBoards() {
    const list = $("sp-boards");
    list.replaceChildren();
    if (!state.boards.length) {
      list.appendChild(el("li", "muted small", "No boards yet."));
    }
    state.boards.forEach(function (b) {
      const li = el("li");
      const a = el("a", "rooms-item" + (b.board === state.board && !state.archive ? " current" : ""));
      a.href = "#b:" + encodeURIComponent(b.board);
      a.append(el("span", "rooms-icon", "#"), el("span", "rooms-name", b.board));
      if (b.directions) {
        const badge = el("span", "badge direction", String(b.directions));
        badge.title = b.directions + " of your directions still open";
        a.appendChild(badge);
      } else if (b.open) {
        const badge = el("span", "badge", String(b.open));
        badge.title = b.open + " open notes";
        a.appendChild(badge);
      }
      li.appendChild(a);
      list.appendChild(li);
    });
    $("sp-archive-link").classList.toggle("current", state.archive);
  }

  // ---------------------------------------------------------------- notes
  function noteNode(n) {
    const node = el("article", "sp-note");
    if (n.author === "person") node.classList.add("person");
    if (n.urgent) node.classList.add("urgent");
    if (n.status !== "open") node.classList.add(n.status);
    if (n.reply_to) node.classList.add("reply");
    node.dataset.id = n.note_id;

    const meta = el("div", "sp-meta");
    meta.appendChild(el("strong", null, n.author === "person" ? "You" : n.agent));
    if (n.author === "person") meta.appendChild(tag("for " + n.for));
    else if (n.for) meta.appendChild(tag("for " + n.for));
    if (n.urgent) meta.appendChild(tag("urgent", "claim"));
    if (n.claimed_by) meta.appendChild(tag("claimed by " + n.claimed_by, "claim"));
    if (n.status === "done") meta.appendChild(tag("done"));
    if (n.status === "superseded") meta.appendChild(tag("replaced"));
    meta.appendChild(el("span", null, "#" + n.note_id + " · " + when(n.at)));
    if (n.reply_to) meta.appendChild(el("span", null, "↳ #" + n.reply_to));
    if (n.author === "person" && n.seen_by && n.seen_by.length) {
      meta.appendChild(el("span", null, "seen by " + n.seen_by.map(function (s) {
        return s.agent + " " + when(s.at); }).join(", ")));
    } else if (n.author === "person" && n.status === "open") {
      meta.appendChild(el("span", null, "not seen yet"));
    }
    node.appendChild(meta);

    const body = el("div", "sp-body");
    body.innerHTML = n.html;          // server-rendered Markdown, raw HTML escaped
    node.appendChild(body);

    const actions = el("div", "sp-actions small");
    function button(text, fn, cls) {
      const b = el("button", "link small" + (cls ? " " + cls : ""), text);
      b.type = "button";
      b.addEventListener("click", fn);
      actions.appendChild(b);
    }
    if (n.status !== "superseded") {
      button("Reply", function () { startReply(n); });
      if (n.status === "open") {
        button("Resolve", function () { act(function () {
          return api("PATCH", "/api/scratchpad/notes/" + n.note_id, { action: "resolve" }); }); });
      } else {
        button("Reopen", function () { act(function () {
          return api("PATCH", "/api/scratchpad/notes/" + n.note_id, { action: "reopen" }); }); });
      }
      if (n.claimed_by) {
        button("Release", function () { act(function () {
          return api("PATCH", "/api/scratchpad/notes/" + n.note_id, { action: "release" }); }); });
      }
      if (n.author === "person" && n.status === "open") button("Edit", function () { startEdit(n); });
    }
    button("Delete", function () {
      act(function () { return api("DELETE", "/api/scratchpad/notes/" + n.note_id); });
    }, "danger");
    node.appendChild(actions);
    return node;
  }

  function renderNotes() {
    const log = $("sp-log");
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.replaceChildren();
    const all = Array.from(state.notes.values()).sort(function (a, b) { return a.note_id - b.note_id; });
    const shown = all.filter(function (n) { return state.showDone || n.status === "open"; });
    if (!shown.length) {
      log.appendChild(el("p", "muted rooms-empty", all.length ? "Nothing open here. Tick “Done too” for the rest."
        : "Nothing on this board yet. Write the first direction below."));
    }
    // Replies sit under the note they answer (when it's shown), the rest in order.
    const byParent = new Map();
    const ids = new Set(shown.map(function (n) { return n.note_id; }));
    shown.forEach(function (n) {
      const parent = n.reply_to && ids.has(n.reply_to) ? n.reply_to : 0;
      if (!byParent.has(parent)) byParent.set(parent, []);
      byParent.get(parent).push(n);
    });
    (function add(parent) {
      (byParent.get(parent) || []).forEach(function (n) {
        log.appendChild(noteNode(n));
        add(n.note_id);
      });
    })(0);
    if (atBottom || !state.rendered) log.scrollTop = log.scrollHeight;
    state.rendered = true;
  }

  // -------------------------------------------------------------- sessions
  function renderSessions() {
    const box = $("sp-sessions");
    box.replaceChildren();
    const select = $("sp-for");
    const chosen = select.value;
    select.replaceChildren(el("option", null, "everyone on the board"));
    select.firstChild.value = "";
    if (!state.sessions.length) {
      box.appendChild(el("p", "muted small", "No session has worked on this board in the last week. Sessions show "
        + "here once they read or write it."));
    }
    state.sessions.forEach(function (s) {
      const row = el("div", "sp-session");
      const head = el("div");
      head.append(el("span", "sp-dot " + s.state), el("strong", null, s.agent));
      row.appendChild(head);
      row.appendChild(el("div", "muted small", s.state + " · " + s.last_tool + " " + when(s.last_at)));
      if (s.holds.length) row.appendChild(el("div", "small", "holds #" + s.holds.join(", #")));
      row.title = "Write to " + s.agent;
      row.addEventListener("click", function () { select.value = s.agent; $("sp-text").focus(); paneDrawer.close(); });
      box.appendChild(row);
      const opt = el("option", null, s.agent + (s.state === "active" ? "" : " (" + s.state + ")"));
      opt.value = s.agent;
      select.appendChild(opt);
    });
    select.value = Array.from(select.options).some(function (o) { return o.value === chosen; }) ? chosen : "";
  }

  // ------------------------------------------------------------------ poll
  async function poll() {
    const params = new URLSearchParams();
    if (state.board && !state.archive) params.set("board", state.board);
    if (state.cursor && state.board && !state.archive) params.set("after", state.cursor);
    let data;
    try { data = await api("GET", "/api/scratchpad/feed?" + params.toString()); }
    catch (e) { return; }      // offline for a moment: the next poll tries again
    state.boards = data.boards;
    renderBoards();
    if (!state.board || state.archive || data.board !== state.board) return;
    state.cursor = data.cursor;
    (data.notes || []).forEach(function (n) { state.notes.set(n.note_id, n); });
    (data.gone || []).forEach(function (id) { state.notes.delete(id); });
    state.sessions = data.sessions || [];
    const open = Array.from(state.notes.values()).filter(function (n) { return n.status === "open"; }).length;
    $("sp-subtitle").textContent = open + " open";
    renderNotes();
    renderSessions();
  }

  function schedule() {
    clearTimeout(state.timer);
    state.timer = setTimeout(async function () { await poll(); schedule(); }, document.hidden ? 30000 : 5000);
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { poll(); schedule(); } });

  // ---------------------------------------------------------------- views
  function openBoard(board) {
    state.board = board;
    state.archive = false;
    state.cursor = null;
    state.notes = new Map();
    state.rendered = false;
    cancelReply();
    $("sp-title").textContent = "#" + board;
    $("sp-subtitle").textContent = "";
    $("sp-actions").hidden = false;
    $("sp-log").hidden = false;
    $("sp-archive").hidden = true;
    $("sp-compose").hidden = false;
    sideDrawer.close();
    poll();
  }

  function openArchive() {
    state.archive = true;
    state.board = null;
    $("sp-title").textContent = "Archive";
    $("sp-subtitle").textContent = "notes that fell off: past their keep date, cleared or deleted";
    $("sp-actions").hidden = true;
    $("sp-log").hidden = true;
    $("sp-compose").hidden = true;
    $("sp-archive").hidden = false;
    sideDrawer.close();
    renderBoards();
    search("");
  }

  async function search(q) {
    const list = $("sp-archive-list");
    list.replaceChildren(el("p", "muted small", "Searching…"));
    try {
      const data = await api("GET", "/api/scratchpad/archive?" + new URLSearchParams({ q: q }).toString());
      list.replaceChildren();
      if (!data.notes.length) list.appendChild(el("p", "muted small", q ? "Nothing matches." : "Nothing archived yet."));
      data.notes.forEach(function (n) {
        const node = el("article", "sp-note" + (n.author === "person" ? " person" : ""));
        const meta = el("div", "sp-meta");
        meta.append(el("strong", null, n.author === "person" ? "You" : n.agent), tag("#" + n.board),
                    el("span", null, "#" + n.note_id + " · written " + when(n.at) + " · " + n.reason + " " + when(n.archived)));
        const body = el("div", "sp-body");
        body.innerHTML = n.html;
        node.append(meta, body);
        list.appendChild(node);
      });
    } catch (e) { list.replaceChildren(el("p", "error", e.message)); }
  }

  function route() {
    const hash = decodeURIComponent(location.hash.slice(1));
    if (hash === "archive") openArchive();
    else if (hash.startsWith("b:") && hash.length > 2) openBoard(hash.slice(2));
    else poll();
  }
  window.addEventListener("hashchange", route);

  // -------------------------------------------------------------- compose
  function startReply(n) {
    state.replyTo = n.note_id;
    state.replaces = null;
    $("sp-replying").hidden = false;
    $("sp-replying-text").textContent = "Replying to #" + n.note_id + " (" + (n.author === "person" ? "you" : n.agent) + ")";
    if (n.author !== "person") $("sp-for").value = Array.from($("sp-for").options).some(function (o) {
      return o.value === n.agent; }) ? n.agent : "";
    $("sp-text").focus();
  }

  function startEdit(n) {
    state.replaces = n.note_id;
    state.replyTo = n.reply_to || null;
    $("sp-replying").hidden = false;
    $("sp-replying-text").textContent = "Editing #" + n.note_id + " (sends a new direction in its place)";
    $("sp-text").value = n.text;
    $("sp-urgent").checked = !!n.urgent;
    $("sp-text").focus();
  }

  function cancelReply() {
    state.replyTo = null;
    state.replaces = null;
    $("sp-replying").hidden = true;
  }
  $("sp-reply-cancel").addEventListener("click", cancelReply);

  document.querySelectorAll("[data-quick]").forEach(function (b) {
    b.addEventListener("click", function () {
      $("sp-text").value = b.dataset.quick;
      if (b.dataset.urgent) $("sp-urgent").checked = true;
      $("sp-text").focus();
    });
  });

  $("sp-compose").addEventListener("submit", function (e) {
    e.preventDefault();
    const text = $("sp-text").value.trim();
    if (!text || !state.board) return;
    const body = { board: state.board, text: text, for_agent: $("sp-for").value || null,
                   urgent: $("sp-urgent").checked, reply_to: state.replyTo, replaces: state.replaces };
    act(async function () {
      await api("POST", "/api/scratchpad/notes", body);
      $("sp-text").value = "";
      $("sp-urgent").checked = false;
      cancelReply();
    });
  });
  $("sp-text").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("sp-compose").requestSubmit();
  });

  $("sp-new-board").addEventListener("submit", function (e) {
    e.preventDefault();
    const name = $("sp-new-board-name").value.trim().toLowerCase().replace(/\s+/g, "-");
    if (!/^[a-z0-9][a-z0-9._-]{0,59}$/.test(name)) {
      failed(new Error("A board name is letters, digits, dots, dashes or underscores, e.g. nas-migration."));
      return;
    }
    $("sp-new-board-name").value = "";
    location.hash = "b:" + name;          // a board exists once something is written on it
  });

  $("sp-show-done").addEventListener("change", function () { state.showDone = this.checked; renderNotes(); });
  $("sp-clear").addEventListener("click", function () {
    if (!state.board || !window.confirm("Clear #" + state.board + "? Its notes move to the archive.")) return;
    act(async function () {
      await api("DELETE", "/api/scratchpad?board=" + encodeURIComponent(state.board));
      state.notes = new Map();
    });
  });
  $("sp-search").addEventListener("submit", function (e) { e.preventDefault(); search($("sp-search-q").value.trim()); });
  $("sp-menu").addEventListener("click", function () { sideDrawer.toggle(); });
  $("sp-sessions-button").addEventListener("click", function () { paneDrawer.toggle(); });
  $("sp-pane-close").addEventListener("click", function () { paneDrawer.close(); });

  route();
  schedule();
})();
