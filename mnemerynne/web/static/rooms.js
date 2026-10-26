/* Rooms: channels and direct messages, live (mnemerynne/rooms.py, rooms_bus.py).
   The sidebar comes from /api/rooms, a room's messages from /api/rooms/{id}/messages, and everything
   after that from one server-sent-events stream (/api/rooms/stream), reopened from the last event id
   when it drops. Messages arrive as server-rendered HTML (raw HTML escaped, links allowlisted); all
   other text goes through textContent. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const REACTIONS = ["👍", "❤️", "😂", "🎉", "👀", "✅", "🙏", "😮"];
  const GROUP_MS = 5 * 60 * 1000;      // consecutive messages from one person within this are grouped

  const state = {
    me: "", rooms: new Map(), current: null, members: [], messages: new Map(), oldest: null, more: true,
    presence: {}, cursor: 0, es: null, retry: 1000, typing: new Map(), threadRoot: null, pane: null,
    attached: [], lastTypingSent: 0,
  };

  // ------------------------------------------------------------- helpers
  function when(iso) { return new Date(iso.replace(" ", "T") + "Z"); }
  function clock(iso) { return when(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  function day(iso) { return when(iso).toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" }); }
  function roomLabel(r) { return r.kind === "channel" ? "#" + r.name : r.name; }
  function button(text, cls, title, onClick) {
    const b = el("button", cls || "link small", text);
    b.type = "button";
    if (title) b.title = title;
    b.addEventListener("click", onClick);
    return b;
  }
  function showError(message) {
    const log = $("rooms-log");
    const p = el("p", "error small", message);
    log.appendChild(p);
    setTimeout(function () { p.remove(); }, 6000);
  }
  async function run(fn) { try { return await fn(); } catch (e) { showError(e.message); } }

  // ------------------------------------------------------------- sidebar
  function presenceDot(name) {
    const s = state.presence[name];
    const dot = el("span", "presence " + (s || "offline"), s === "online" ? "●" : "○");
    dot.title = s || "offline";
    return dot;
  }

  function renderSidebar() {
    const channels = $("rooms-channels"), dms = $("rooms-dms");
    channels.replaceChildren(); dms.replaceChildren();
    state.rooms.forEach(function (r) {
      const li = el("li");
      const a = el("a", "rooms-item" + (r.id === state.current ? " current" : "") + (r.unread ? " unread" : ""));
      a.href = "#room=" + r.id;
      if (r.kind === "channel") {
        a.appendChild(el("span", "rooms-icon", r.visibility === "private" ? "🔒" : r.visibility === "group" ? "👥" : "#"));
        a.appendChild(el("span", "rooms-name", r.name));
      } else {
        if (r.people && r.people.length === 1) a.appendChild(presenceDot(r.people[0]));
        a.appendChild(el("span", "rooms-name", r.name));
      }
      if (r.mentions) a.appendChild(el("span", "badge mention", "@" + r.mentions));
      else if (r.unread && r.kind !== "channel") a.appendChild(el("span", "badge", String(r.unread)));
      a.addEventListener("click", function (e) { e.preventDefault(); openRoom(r.id); });
      li.appendChild(a);
      (r.kind === "channel" ? channels : dms).appendChild(li);
    });
    if (!channels.children.length) channels.appendChild(el("li", "muted small", "None yet: browse or make one."));
    if (!dms.children.length) dms.appendChild(el("li", "muted small", "No messages yet."));
  }

  async function loadRooms() {
    const data = await api("GET", "/api/rooms");
    state.me = data.me;
    state.presence = data.presence || {};
    state.rooms = new Map(data.rooms.map(function (r) { return [r.id, r]; }));
    renderSidebar();
    return data;
  }

  let reloadTimer = null;
  function reloadRoomsSoon() {
    clearTimeout(reloadTimer);
    reloadTimer = setTimeout(function () { run(loadRooms); }, 300);
  }

  // ------------------------------------------------------------ messages
  function reactionChips(m) {
    const box = el("div", "rooms-reactions");
    m.reactions.forEach(function (r) {
      const mine = r.users.indexOf(state.me) >= 0;
      const chip = button(r.emoji + " " + r.users.length, "rooms-chip" + (mine ? " mine" : ""), r.users.join(", "),
                          function () { react(m, r.emoji, !mine); });
      box.appendChild(chip);
    });
    return box;
  }

  // Cards for links to Mnemerynne files and wiki pages, asked for in batches and cached; a link this
  // person can't read stays a plain link (the server never tells them its name).
  const previewCache = new Map();
  let previewQueue = [], previewTimer = null;
  function addPreviews(body) {
    body.querySelectorAll("a[href]").forEach(function (a) {
      const href = a.getAttribute("href");
      if (!/^\/(files|wiki)\/\d+/.test(href)) return;
      if (previewCache.has(href)) { showPreview(a, previewCache.get(href)); return; }
      previewQueue.push([href, a]);
    });
    clearTimeout(previewTimer);
    previewTimer = setTimeout(flushPreviews, 50);
  }
  async function flushPreviews() {
    const queue = previewQueue; previewQueue = [];
    const links = Array.from(new Set(queue.map(function (q) { return q[0]; }))).slice(0, 20);
    if (!links.length) return;
    const data = await api("POST", "/api/rooms/previews", { links: links }).catch(function () { return { previews: {} }; });
    links.forEach(function (l) { previewCache.set(l, data.previews[l] || { hidden: true }); });
    queue.forEach(function (q) { if (previewCache.has(q[0])) showPreview(q[1], previewCache.get(q[0])); });
  }
  function showPreview(a, p) {
    if (!p || p.hidden || a.dataset.previewed) return;
    a.dataset.previewed = "1";
    const card = el("a", "rooms-preview");
    card.href = a.getAttribute("href");
    card.append(el("span", null, p.kind === "wiki" ? "📖 " : "📄 "), el("strong", null, p.title),
                el("span", "muted small", " · " + p.where));
    a.closest(".rooms-body").appendChild(card);
  }

  function attachmentsView(m, opts) {
    const box = el("div", "rooms-files");
    m.attachments.forEach(function (a) {
      const url = "/api/rooms/files/" + a.id;
      if (/^image\/(png|jpeg|gif|webp)$/.test(a.mime)) {
        const link = el("a"); link.href = url; link.target = "_blank"; link.rel = "noopener";
        const img = el("img"); img.src = url; img.alt = a.name; img.loading = "lazy";
        link.appendChild(img);
        box.appendChild(link);
      } else {
        const link = el("a", "rooms-file", "📄 " + a.name + " (" + Math.max(1, Math.round(a.bytes / 1024)) + " KB)");
        link.href = url;
        box.appendChild(link);
      }
      if (!opts || !opts.noSave) {
        box.appendChild(button("Save to my files", "link small", "Copy it into your File box", async function () {
          const data = await run(function () { return api("POST", url + "/save"); });
          if (data) showError("Saved to your File box.");
        }));
      }
    });
    return box;
  }

  function messageNode(m, opts) {
    const node = el("div", "rooms-msg" + (m.deleted ? " deleted" : "") + (m.pinned ? " pinned" : "")
                   + (opts.compact ? " compact" : ""));
    node.dataset.id = m.id;
    if ((m.mentions && m.mentions.indexOf(state.me) >= 0) ||
        (!m.deleted && new RegExp("@(" + state.me.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "|channel|here)\\b", "i").test(m.body))) {
      node.classList.add("mentions-me");
    }
    if (!opts.compact) {
      const head = el("div", "rooms-msg-head");
      head.appendChild(el("strong", m.via === "erynne" ? "erynne" : "", m.who));
      if (m.user && m.via === "web") head.appendChild(presenceDot(m.user));
      head.appendChild(el("span", "muted small", " " + clock(m.created_at)));
      node.appendChild(head);
    }
    const body = el("div", "rooms-body");
    if (m.deleted) body.textContent = "message deleted";
    else { body.innerHTML = m.html; addPreviews(body); }   // server-rendered, raw HTML escaped
    if (m.edited_at && !m.deleted) body.appendChild(el("span", "muted small", " (edited)"));
    if (m.pinned) body.appendChild(el("span", "small", " 📌"));
    node.appendChild(body);
    if (m.attachments && m.attachments.length) node.appendChild(attachmentsView(m));
    if (m.reactions && m.reactions.length) node.appendChild(reactionChips(m));
    if (!opts.inThread && m.replies) {
      node.appendChild(button("💬 " + m.replies + (m.replies === 1 ? " reply" : " replies"), "link small rooms-replies",
                              null, function () { openThread(m.id); }));
    }
    if (!m.deleted) node.appendChild(actions(m, opts));
    return node;
  }

  function actions(m, opts) {
    const bar = el("div", "rooms-actions");
    const pick = el("div", "rooms-picker"); pick.hidden = true;
    REACTIONS.forEach(function (e) { pick.appendChild(button(e, "link", null, function () { pick.hidden = true; react(m, e, true); })); });
    bar.appendChild(button("😊", "link small", "React", function () { pick.hidden = !pick.hidden; }));
    if (!opts.inThread) bar.appendChild(button("💬", "link small", "Reply in thread", function () { openThread(m.id); }));
    bar.appendChild(button(m.pinned ? "📍" : "📌", "link small", m.pinned ? "Unpin" : "Pin",
                           function () { run(function () { return api("POST", "/api/rooms/messages/" + m.id + "/pin", { pinned: !m.pinned }); }); }));
    if (m.user === state.me && m.via === "web") {
      bar.appendChild(button("✏️", "link small", "Edit", function () { editMessage(m); }));
    }
    if (m.user === state.me || isOwner()) {
      bar.appendChild(button("🗑", "link small", "Delete", function () {
        if (confirm("Delete this message?")) run(function () { return api("DELETE", "/api/rooms/messages/" + m.id); });
      }));
    }
    bar.appendChild(pick);
    return bar;
  }

  function isOwner() {
    const r = state.rooms.get(state.current);
    return r && r.role === "owner";
  }

  function renderLog(keepScroll) {
    const log = $("rooms-log");
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    const before = log.scrollHeight - log.scrollTop;
    log.replaceChildren();
    if (state.more && state.messages.size) {
      log.appendChild(button("Load earlier messages", "link small rooms-more", null, function () { loadMore(); }));
    }
    let lastDay = "", last = null;
    Array.from(state.messages.values()).sort(function (a, b) { return a.id - b.id; }).forEach(function (m) {
      const d = day(m.created_at);
      if (d !== lastDay) { log.appendChild(el("div", "rooms-day", d)); lastDay = d; last = null; }
      const compact = last && last.user === m.user && last.via === m.via && !last.deleted &&
                      when(m.created_at) - when(last.created_at) < GROUP_MS;
      log.appendChild(messageNode(m, { compact: compact }));
      last = m;
    });
    if (!state.messages.size) log.appendChild(el("p", "muted rooms-empty", "No messages yet. Say hello."));
    if (keepScroll) log.scrollTop = log.scrollHeight - before;
    else if (atBottom || !keepScroll) log.scrollTop = log.scrollHeight;
  }

  async function loadMore() {
    const data = await api("GET", "/api/rooms/" + state.current + "/messages?before=" + state.oldest);
    data.messages.forEach(function (m) { state.messages.set(m.id, m); });
    state.more = data.messages.length >= 50;
    if (data.messages.length) state.oldest = data.messages[0].id;
    renderLog(true);
  }

  function markRead() {
    const r = state.rooms.get(state.current);
    const ids = Array.from(state.messages.keys());
    if (!r || !ids.length || document.hidden) return;
    const last = Math.max.apply(null, ids);
    if (r.unread || r.mentions) {
      r.unread = 0; r.mentions = 0;
      renderSidebar();
    }
    api("POST", "/api/rooms/" + state.current + "/read", { message_id: last }).catch(function () {});
  }

  async function openRoom(id) {
    if (!state.rooms.has(id)) await run(loadRooms);
    const info = await run(function () { return api("GET", "/api/rooms/" + id); });
    if (!info) return;
    const r = Object.assign(state.rooms.get(id) || {}, info.room);
    state.rooms.set(id, r);
    state.current = id;
    state.members = info.members;
    history.replaceState(null, "", "#room=" + id);
    $("room-name").textContent = roomLabel(r) + (r.archived ? " (archived)" : "");
    $("room-topic").textContent = r.topic || (r.kind === "channel" && r.visibility === "group" ? "Group: " + r.group : "");
    $("room-actions").hidden = false;
    $("rooms-compose").hidden = r.archived;
    $("rooms-text").placeholder = "Message " + roomLabel(r) + (r.erynne ? " (@Erynne to ask her)" : "");
    if (!r.joined && r.kind === "channel" && r.visibility === "open") {
      $("rooms-text").placeholder = "Message #" + r.name + " (you'll join it)";
    }
    document.body.classList.remove("rooms-drawer");
    const data = await run(function () { return api("GET", "/api/rooms/" + id + "/messages"); });
    state.messages = new Map((data ? data.messages : []).map(function (m) { return [m.id, m]; }));
    state.oldest = data && data.messages.length ? data.messages[0].id : null;
    state.more = data && data.messages.length >= 50;
    renderSidebar();
    renderLog(false);
    renderTyping();
    markRead();
    if (state.pane && state.pane !== "thread") openPane(state.pane);
    else closePane();
    $("rooms-text").focus();
  }

  // ----------------------------------------------------------- composing
  async function send(text, thread) {
    const r = state.rooms.get(state.current);
    if (r && r.kind === "channel" && r.visibility === "open" && !r.joined) {
      await api("POST", "/api/rooms/" + r.id + "/members", { action: "join" });
      r.joined = true;
    }
    const body = { text: text, thread: thread || null };
    if (!thread) body.attachments = state.attached.map(function (a) { return a.id; });
    const data = await api("POST", "/api/rooms/" + state.current + "/messages", body);
    if (!thread) { state.attached = []; renderAttached(); }
    return data.message;
  }

  function wireComposer(form, textarea, threadFn) {
    textarea.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !suggestOpen()) {
        e.preventDefault();
        form.requestSubmit();
      }
    });
    textarea.addEventListener("input", function () {
      textarea.style.height = "auto";
      textarea.style.height = Math.min(textarea.scrollHeight, 200) + "px";
      const now = Date.now();
      if (textarea.value && now - state.lastTypingSent > 3000 && state.current) {
        state.lastTypingSent = now;
        api("POST", "/api/rooms/" + state.current + "/typing").catch(function () {});
      }
      suggest(textarea);
    });
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      const text = textarea.value.trim();
      if (!text && !(state.attached.length && !threadFn())) return;
      const sendBtn = form.querySelector("button[type=submit]");
      sendBtn.disabled = true;
      try {
        const m = await send(text, threadFn());
        textarea.value = "";
        textarea.style.height = "auto";
        if (!threadFn()) { state.messages.set(m.id, m); renderLog(false); }
      } catch (err) { showError(err.message); }
      finally { sendBtn.disabled = false; textarea.focus(); }
    });
  }

  async function editMessage(m) {
    const text = prompt("Edit your message:", m.body);
    if (text === null || !text.trim() || text === m.body) return;
    await run(function () { return api("PATCH", "/api/rooms/messages/" + m.id, { text: text }); });
  }

  async function react(m, emoji, on) {
    await run(function () { return api("POST", "/api/rooms/messages/" + m.id + "/react", { emoji: emoji, on: on }); });
  }

  // @-mentions: people in the room, and @channel, @here, @Erynne.
  let suggestFor = null;
  function suggestOpen() { return !$("rooms-suggest").hidden; }
  function suggest(textarea) {
    const box = $("rooms-suggest");
    const upto = textarea.value.slice(0, textarea.selectionStart);
    const match = /(^|\s)@([\w.-]*)$/.exec(upto);
    if (!match) { box.hidden = true; return; }
    const q = match[2].toLowerCase();
    const r = state.rooms.get(state.current);
    const names = state.members.filter(function (n) { return n !== state.me; })
      .concat(r && r.kind === "channel" ? ["channel", "here"] : []).concat(r && r.erynne ? ["Erynne"] : []);
    const hits = names.filter(function (n) { return n.toLowerCase().indexOf(q) === 0; }).slice(0, 8);
    if (!hits.length) { box.hidden = true; return; }
    suggestFor = textarea;
    box.replaceChildren();
    hits.forEach(function (n) {
      box.appendChild(button("@" + n, "link rooms-suggestion", null, function () {
        const start = upto.length - match[2].length;
        textarea.value = textarea.value.slice(0, start) + n + " " + textarea.value.slice(textarea.selectionStart);
        box.hidden = true;
        textarea.focus();
      }));
    });
    box.hidden = false;
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") $("rooms-suggest").hidden = true;
  });

  // ---------------------------------------------------------- attachments
  function renderAttached() {
    const box = $("rooms-attached");
    box.replaceChildren();
    state.attached.forEach(function (a, i) {
      const chip = el("span", "rooms-chip", "📎 " + a.name + " ");
      chip.appendChild(button("✕", "link small", "Remove", function () { state.attached.splice(i, 1); renderAttached(); }));
      box.appendChild(chip);
    });
  }

  async function upload(files) {
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      const resp = await fetch("/api/rooms/" + state.current + "/files", { method: "POST", body: form });
      const data = await resp.json().catch(function () { return {}; });
      if (!resp.ok) { showError(data.detail || "Couldn't attach " + file.name); continue; }
      state.attached.push(data.file);
    }
    renderAttached();
  }
  $("rooms-file").addEventListener("change", function () { run(function () { return upload(Array.from($("rooms-file").files)); }); $("rooms-file").value = ""; });
  $("rooms-log").addEventListener("dragover", function (e) { if (state.current) e.preventDefault(); });
  $("rooms-log").addEventListener("drop", function (e) {
    if (!state.current) return;
    e.preventDefault();
    run(function () { return upload(Array.from(e.dataTransfer.files)); });
  });

  // ---------------------------------------------------------------- panes
  function closePane() { $("rooms-pane").hidden = true; state.pane = null; state.threadRoot = null; }

  async function openThread(rootId) {
    const data = await run(function () { return api("GET", "/api/rooms/messages/" + rootId + "/thread"); });
    if (!data) return;
    state.pane = "thread";
    state.threadRoot = data.messages[0].id;
    $("pane-title").textContent = "Thread";
    const body = $("pane-body");
    body.replaceChildren();
    data.messages.forEach(function (m, i) { body.appendChild(messageNode(m, { inThread: true, compact: false })); if (i === 0) body.appendChild(el("hr")); });
    $("thread-compose").hidden = false;
    $("rooms-pane").hidden = false;
    $("thread-text").focus();
  }

  async function openPane(kind) {
    state.pane = kind;
    $("thread-compose").hidden = true;
    const body = $("pane-body");
    body.replaceChildren(el("p", "muted small", "Loading…"));
    $("rooms-pane").hidden = false;
    if (kind === "members") {
      $("pane-title").textContent = "Members";
      const r = state.rooms.get(state.current);
      const list = el("ul", "rooms-members");
      state.members.forEach(function (n) {
        const li = el("li");
        li.append(presenceDot(n), document.createTextNode(" " + n));
        if (n !== state.me) li.appendChild(button("Message", "link small", null, function () { openDm([n]); }));
        list.appendChild(li);
      });
      body.replaceChildren(list);
      if (r && r.visibility === "private") {
        body.appendChild(button("Invite someone", "small", null, async function () {
          const who = prompt("Username to invite:");
          if (who) { await run(function () { return api("POST", "/api/rooms/" + r.id + "/members", { action: "invite", user: who }); }); }
        }));
      }
      if (r && r.visibility === "group") body.appendChild(el("p", "muted small", "Membership follows the group " + r.group + "."));
    } else if (kind === "pins") {
      $("pane-title").textContent = "Pinned";
      const data = await run(function () { return api("GET", "/api/rooms/" + state.current + "/pins"); });
      body.replaceChildren();
      if (data && !data.messages.length) body.appendChild(el("p", "muted small", "Nothing pinned yet."));
      (data ? data.messages : []).forEach(function (m) { body.appendChild(messageNode(m, { inThread: true, compact: false })); });
    }
  }

  document.querySelectorAll("[data-pane]").forEach(function (b) {
    b.addEventListener("click", function () { openPane(b.dataset.pane); });
  });
  $("pane-close").addEventListener("click", closePane);

  // ------------------------------------------------------- dialogs (new, browse, settings)
  function dialog(title, build) {
    const dlg = el("dialog", "rooms-dialog");
    const form = el("form"); form.method = "dialog";
    form.appendChild(el("h2", null, title));
    const body = el("div"); form.appendChild(body);
    const close = button("Close", "link small", null, function () { dlg.close(); });
    build(body, dlg);
    form.appendChild(close);
    dlg.appendChild(form);
    dlg.addEventListener("close", function () { dlg.remove(); });
    document.body.appendChild(dlg);
    dlg.showModal();
    return dlg;
  }

  async function openDm(people) {
    const data = await run(function () { return api("POST", "/api/rooms/dm", { people: people }); });
    if (data) { await loadRooms(); openRoom(data.room.id); }
  }

  $("rooms-new").addEventListener("click", function () {
    dialog("New", async function (body, dlg) {
      const people = await api("GET", "/api/people").catch(function () { return { people: [] }; });
      const list = el("datalist"); list.id = "rooms-people";
      people.people.forEach(function (p) { const o = el("option"); o.value = p; list.appendChild(o); });
      body.appendChild(list);

      body.appendChild(el("h3", "small", "Message people"));
      const dmForm = el("div", "rooms-form");
      const to = el("input"); to.placeholder = "alice, bob"; to.setAttribute("list", "rooms-people");
      dmForm.append(to, button("Open", "small", null, function () {
        const names = to.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
        if (names.length) { dlg.close(); openDm(names); }
      }));
      body.appendChild(dmForm);

      body.appendChild(el("h3", "small", "New channel"));
      const ch = el("div", "rooms-form");
      const name = el("input"); name.placeholder = "front-office"; name.maxLength = 40;
      const topic = el("input"); topic.placeholder = "What it's for (optional)"; topic.maxLength = 300;
      const vis = el("select");
      [["open", "Open: anyone can join"], ["private", "Private: by invitation"], ["group", "A directory group's members"]]
        .forEach(function (o) { const opt = el("option", null, o[1]); opt.value = o[0]; vis.appendChild(opt); });
      const group = el("input"); group.placeholder = "Group name (AD or Entra)"; group.hidden = true;
      vis.addEventListener("change", function () { group.hidden = vis.value !== "group"; });
      ch.append(name, topic, vis, group, button("Create", "small", null, async function () {
        const data = await run(function () {
          return api("POST", "/api/rooms", { name: name.value, topic: topic.value, visibility: vis.value, group: group.value });
        });
        if (data) { dlg.close(); await loadRooms(); openRoom(data.room.id); }
      }));
      body.appendChild(ch);
    });
  });

  $("rooms-browse").addEventListener("click", function () {
    dialog("Channels", async function (body, dlg) {
      const data = await api("GET", "/api/rooms/browse");
      if (!data.channels.length) body.appendChild(el("p", "muted small", "No channels yet. Make one with ＋."));
      data.channels.forEach(function (c) {
        const row = el("div", "rooms-browse-row");
        row.appendChild(el("strong", null, (c.visibility === "group" ? "👥 " : "# ") + c.name));
        row.appendChild(el("span", "muted small", " " + (c.topic || "") + " · " + c.members + " joined"));
        row.appendChild(button(c.joined || c.visibility === "group" ? "Open" : "Join", "small", null, async function () {
          if (!c.joined && c.visibility === "open") await run(function () { return api("POST", "/api/rooms/" + c.id + "/members", { action: "join" }); });
          dlg.close(); await loadRooms(); openRoom(c.id);
        }));
        body.appendChild(row);
      });
    });
  });

  $("room-settings").addEventListener("click", function () {
    const r = state.rooms.get(state.current);
    if (!r) return;
    dialog(roomLabel(r), function (body, dlg) {
      const form = el("div", "rooms-form");
      const notify = el("select");
      [["all", "Notify me about every message"], ["mentions", "Only when mentioned"], ["none", "Never"]]
        .forEach(function (o) { const opt = el("option", null, o[1]); opt.value = o[0]; notify.appendChild(opt); });
      notify.value = r.notify || "all";
      form.appendChild(notify);
      let topic = null, erynne = null;
      if (r.kind === "channel" && r.role === "owner") {
        topic = el("input"); topic.value = r.topic; topic.placeholder = "Topic"; topic.maxLength = 300;
        const label = el("label", "check small");
        erynne = el("input"); erynne.type = "checkbox"; erynne.checked = r.erynne;
        label.append(erynne, document.createTextNode(" @Erynne may answer here"));
        form.append(topic, label);
      }
      form.appendChild(button("Save", "small", null, async function () {
        const patch = { notify: notify.value };
        if (topic) { patch.topic = topic.value; patch.erynne = erynne.checked; }
        const data = await run(function () { return api("PATCH", "/api/rooms/" + r.id, patch); });
        if (data) { dlg.close(); await loadRooms(); openRoom(r.id); }
      }));
      body.appendChild(form);
      if (window.Notification && Notification.permission === "default") {
        body.appendChild(button("🔔 Desktop notifications for DMs and mentions", "link small", null, function () {
          Notification.requestPermission();
        }));
      }
      const leave = r.kind === "channel" ? (r.visibility === "group" ? null : "Leave channel") : "Close conversation";
      if (leave) body.appendChild(button(leave, "link small danger", null, async function () {
        if (!confirm(leave + "?")) return;
        await run(function () { return api("POST", "/api/rooms/" + r.id + "/members", { action: "leave" }); });
        dlg.close(); state.current = null; await loadRooms();
        $("rooms-log").replaceChildren(el("p", "muted rooms-empty", "Pick a room on the left."));
        $("rooms-compose").hidden = true; $("room-actions").hidden = true; $("room-name").textContent = "Rooms";
      }));
      if (r.kind === "channel" && r.role === "owner") body.appendChild(button(r.archived ? "Unarchive" : "Archive channel", "link small danger", null, async function () {
        if (!confirm((r.archived ? "Unarchive" : "Archive") + " #" + r.name + "?")) return;
        await run(function () { return api("PATCH", "/api/rooms/" + r.id, { archived: !r.archived }); });
        dlg.close(); await loadRooms();
      }));
    });
  });

  $("rooms-menu").addEventListener("click", function () { document.body.classList.toggle("rooms-drawer"); });

  // ---------------------------------------------------------------- live
  function renderTyping() {
    const now = Date.now();
    const names = [];
    state.typing.forEach(function (until, key) {
      const parts = key.split("|");
      if (until < now) state.typing.delete(key);
      else if (Number(parts[0]) === state.current) names.push(parts[1]);
    });
    $("rooms-typing").textContent = names.length ? names.join(", ") + (names.length === 1 ? " is" : " are") + " typing…" : "";
  }
  setInterval(renderTyping, 1000);

  function notifyDesktop(m) {
    if (!window.Notification || Notification.permission !== "granted" || !document.hidden) return;
    const r = state.rooms.get(m.room_id);
    if (!r || r.notify === "none") return;
    const mentioned = (m.mentions || []).indexOf(state.me) >= 0;
    if (r.kind === "channel" && !mentioned) return;
    const n = new Notification(m.who + (r.kind === "channel" ? " in #" + r.name : ""), { body: m.body.slice(0, 140), tag: "room" + r.id });
    n.onclick = function () { window.focus(); openRoom(r.id); };
  }

  function updated(m) {
    if (m.room_id === state.current && (state.messages.has(m.id) || m.thread_root_id === null)) {
      if (m.thread_root_id === null || state.messages.has(m.id)) { state.messages.set(m.id, m); renderLog(true); }
    }
    if (state.pane === "thread" && (m.id === state.threadRoot || m.thread_root_id === state.threadRoot)) openThread(state.threadRoot);
    if (state.pane === "pins" && m.room_id === state.current) openPane("pins");
  }

  const handlers = {
    hello: function (d) { state.presence = d.presence || {}; state.cursor = d.cursor; renderSidebar(); },
    "message.new": function (d) {
      const m = d.message;
      if (m.room_id === state.current) {
        if (m.thread_root_id === null) { state.messages.set(m.id, m); renderLog(false); markRead(); }
        else if (state.pane === "thread" && m.thread_root_id === state.threadRoot) openThread(state.threadRoot);
        state.typing.delete(m.room_id + "|" + m.user); renderTyping();
      } else if (m.user !== state.me) {
        const r = state.rooms.get(m.room_id);
        if (r) {
          if (m.thread_root_id === null) r.unread = (r.unread || 0) + 1;
          if ((m.mentions || []).indexOf(state.me) >= 0) r.mentions = (r.mentions || 0) + 1;
          renderSidebar();
        } else {
          reloadRoomsSoon();       // a new DM, or a channel someone added us to
        }
      }
      if (m.user !== state.me) notifyDesktop(m);
    },
    "message.edit": function (d) { updated(d.message); },
    "message.delete": function (d) { updated(d.message); },
    reaction: function (d) { updated(d.message); },
    pin: function (d) { updated(d.message); },
    "room.update": function (d) { reloadRoomsSoon(); if (d.room_id === state.current) run(function () { return openRoom(d.room_id); }); },
    member: function (d) { reloadRoomsSoon(); if (d.room_id === state.current) api("GET", "/api/rooms/" + d.room_id).then(function (i) { state.members = i.members; }).catch(function () {}); },
    read: function (d) { const r = state.rooms.get(d.room_id); if (r) { r.unread = 0; r.mentions = 0; renderSidebar(); } },
    typing: function (d) { if (d.user !== state.me) { state.typing.set(d.room_id + "|" + d.user, Date.now() + 5000); renderTyping(); } },
    presence: function (d) { state.presence = d.presence || {}; renderSidebar(); },
    "erynne.status": function (d) {
      if (d.room_id === state.current) {
        state.typing.set(d.room_id + "|Erynne", d.done ? 0 : Date.now() + 60000);
        renderTyping();
        if (d.text && !d.done) $("rooms-typing").textContent = "Erynne: " + d.text;
      }
    },
    end: function (d) {
      state.stopped = true;
      if (state.es) state.es.close();
      $("rooms-log").replaceChildren(el("p", "muted rooms-empty", (d && d.reason) || "Rooms isn't available."));
      $("rooms-compose").hidden = true;
    },
  };

  function connect() {
    if (state.es) state.es.close();
    const es = new EventSource("/api/rooms/stream?after=" + state.cursor);
    state.es = es;
    Object.keys(handlers).forEach(function (kind) {
      es.addEventListener(kind, function (e) {
        if (e.lastEventId) state.cursor = Number(e.lastEventId);
        try { handlers[kind](JSON.parse(e.data)); } catch (err) { console.error(err); }
      });
    });
    es.onopen = function () { state.retry = 1000; };
    es.onerror = function () {
      // Reopen ourselves from the last event id (EventSource would reuse the original URL).
      es.close();
      if (state.stopped) return;
      setTimeout(connect, state.retry);
      state.retry = Math.min(state.retry * 2, 30000);
    };
  }

  document.addEventListener("visibilitychange", function () { if (!document.hidden) markRead(); });

  // ---------------------------------------------------------------- start
  wireComposer($("rooms-compose"), $("rooms-text"), function () { return null; });
  wireComposer($("thread-compose"), $("thread-text"), function () { return state.threadRoot; });

  (async function () {
    const data = await run(loadRooms);
    if (!data) return;
    state.cursor = data.cursor;
    connect();
    const wanted = Number((/room=(\d+)/.exec(location.hash) || [])[1]);
    if (wanted) openRoom(wanted);
    else if (data.rooms.length) openRoom(data.rooms[0].id);
  })();
})();
