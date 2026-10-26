/* Chat: streamed answers with citation links, history, feedback.
   Model output is never inserted as HTML: text nodes and DOM-built links only. */
(function () {
  "use strict";

  const thread = document.getElementById("thread");
  const form = document.getElementById("ask-form");
  const input = document.getElementById("question");
  const send = document.getElementById("send");
  const list = document.getElementById("conversations");
  let conversationId = null;
  let scope = null;  // {collection_id, folder_id, label} or null for everything
  // Advanced settings: a panel of Erynne's tools to switch single ones off. Remembered in this browser;
  // the server only ever narrows what a person may use, never widens it.
  const advanced = document.getElementById("advanced");
  const toolsPanel = document.getElementById("tools-panel");
  const OFF_KEY = "erynne.toolsOff", OPEN_KEY = "erynne.advanced", CLOSED_KEY = "erynne.toolsClosed";
  function remembered(key, fallback) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
  }
  function remember(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage off */ }
  }
  let toolsOff = new Set(remembered(OFF_KEY, []));
  let toolsLoaded = false;

  // Token counter (right of Advanced settings): what the models used for this conversation.
  const tokenCount = document.getElementById("token-count");
  let tokens = null;
  function resetTokens() {
    tokens = { in: 0, out: 0, external: 0, estimated: false, last: 0 };
    showTokens();
  }
  function addTokens(t) {
    if (!t) return;
    const used = (t.in || 0) + (t.out || 0);
    tokens.in += t.in || 0;
    tokens.out += t.out || 0;
    tokens.external += t.external || 0;
    tokens.estimated = tokens.estimated || !!t.estimated;
    tokens.last = used;
    showTokens();
  }
  function showTokens() {
    const total = tokens.in + tokens.out;
    const n = function (x) { return x.toLocaleString(); };
    tokenCount.hidden = !total;
    tokenCount.textContent = (tokens.estimated ? "≈ " : "") + n(total) + " tokens" +
      (tokens.external ? " · " + n(tokens.external) + " external API" : "");
    tokenCount.title = "This conversation: " + n(tokens.in) + " read, " + n(tokens.out) + " written" +
      (tokens.last ? ". Last answer: " + n(tokens.last) : "") +
      (tokens.external ? ". External API: " + n(tokens.external) + " (billed to your key)" : "") +
      (tokens.estimated ? ". ≈: part of it is estimated." : "");
  }
  resetTokens();

  async function loadTools() {
    const box = document.getElementById("tool-list");
    const data = await fetch("/api/me/tools").then(function (r) { return r.ok ? r.json() : null; })
                                              .catch(function () { return null; });
    box.replaceChildren();
    if (!data) { box.appendChild(el("p", "error small", "Couldn't load the tools.")); return; }
    toolsLoaded = true;
    // Collapsible groups (and one per API under Internal systems); which are closed is remembered.
    const closed = new Set(remembered(CLOSED_KEY, []));
    const sections = {};
    function section(parent, key, title) {
      if (sections[key]) return sections[key];
      const details = el("details", "tools-group");
      details.open = !closed.has(key);
      details.addEventListener("toggle", function () {
        if (details.open) closed.delete(key); else closed.add(key);
        remember(CLOSED_KEY, Array.from(closed));
      });
      const summary = el("summary", "small");
      const count = el("span", "muted small tools-count");
      summary.append(document.createTextNode(title + " "), count);
      const list = el("ul", "tool-items");
      details.append(summary, list);
      // An API's own section sits in its group's list, so it goes in a list item.
      parent.appendChild(parent.tagName === "UL" ? el("li", "tools-sub") : details);
      if (parent.tagName === "UL") parent.lastChild.appendChild(details);
      sections[key] = { details: details, list: list, count: count, names: [] };
      return sections[key];
    }
    function recount() {
      Object.keys(sections).forEach(function (key) {
        const sec = sections[key];
        const on = sec.names.filter(function (n) { return !toolsOff.has(n); }).length;
        sec.count.textContent = on + "/" + sec.names.length;
      });
    }
    data.tools.forEach(function (t) {
      const group = section(box, t.group, t.group);
      group.names.push(t.name);
      let target = group;
      if (t.subgroup) {
        target = section(group.list, t.group + "/" + t.subgroup, t.subgroup);
        target.names.push(t.name);
      }
      const li = el("li");
      const label = el("label", "tools-toggle small");
      const check = el("input");
      check.type = "checkbox";
      check.checked = !toolsOff.has(t.name);
      check.addEventListener("change", function () {
        if (check.checked) toolsOff.delete(t.name); else toolsOff.add(t.name);
        remember(OFF_KEY, Array.from(toolsOff));
        recount();
      });
      label.append(check, document.createTextNode(" " + (t.write ? "✏️ " : "") + t.label));
      li.appendChild(label);
      if (t.note) li.appendChild(el("span", "muted small", " " + t.note));
      target.list.appendChild(li);
    });
    recount();
  }

  function showTools(open) {
    if (!toolsPanel) return;
    toolsPanel.hidden = !open;
    toolsPanel.parentElement.classList.toggle("tools-open", open);
    remember(OPEN_KEY, open);
    if (open && !toolsLoaded) loadTools();
  }
  if (advanced) {
    advanced.checked = !!remembered(OPEN_KEY, false);
    showTools(advanced.checked);
    advanced.addEventListener("change", function () { showTools(advanced.checked); });
    document.getElementById("tools-reset").addEventListener("click", function () {
      toolsOff = new Set();
      remember(OFF_KEY, []);
      toolsLoaded = false;
      loadTools();
    });
  }
  const useClaude = document.getElementById("use-claude");  // only for the Claude group, with a key saved
  if (useClaude) {
    // Remembered in this browser only; the server checks the group and key on every question.
    try { useClaude.checked = localStorage.getItem("erynne.askClaude") === "1"; } catch (e) { /* storage off */ }
    useClaude.addEventListener("change", function () {
      try { localStorage.setItem("erynne.askClaude", useClaude.checked ? "1" : "0"); } catch (e) { /* storage off */ }
      if (useClaude.checked && useBfgguf && useBfgguf.checked) { useBfgguf.checked = false; rememberBfgguf(); }
    });
  }
  const useBfgguf = document.getElementById("use-bfgguf");  // only for the experimental group, with BFGguf set up
  function rememberBfgguf() {
    try { localStorage.setItem("erynne.askBfgguf", useBfgguf.checked ? "1" : "0"); } catch (e) { /* storage off */ }
  }
  if (useBfgguf) {
    // Remembered in this browser only; the server checks the group and the BFGguf on every question.
    try { useBfgguf.checked = localStorage.getItem("erynne.askBfgguf") === "1"; } catch (e) { /* storage off */ }
    if (useBfgguf.checked && useClaude && useClaude.checked) useBfgguf.checked = false;
    useBfgguf.addEventListener("change", function () {
      rememberBfgguf();
      // One or the other: both would mean Claude, so untick Claude.
      if (useBfgguf.checked && useClaude && useClaude.checked) {
        useClaude.checked = false;
        try { localStorage.setItem("erynne.askClaude", "0"); } catch (e) { /* storage off */ }
      }
    });
  }
  const scopeLabel = document.getElementById("scope-label");

  document.getElementById("scope").addEventListener("click", async function () {
    const picked = await window.Erynne.pickLocation({ title: "Search in…", ok: "Search here", allowAll: true,
                                                      current: scope });
    if (!picked) return;
    scope = picked.all ? null : picked;
    scopeLabel.textContent = scope ? scope.label : "All my documents";
  });

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  // Text with [n] markers -> text nodes + links to the cited page/heading.
  function renderAnswer(node, text, sources) {
    const byN = new Map((sources || []).map(function (s) { return [s.n, s]; }));
    node.replaceChildren();
    const re = /\[(\d{1,2})\]/g;
    let last = 0, m;
    while ((m = re.exec(text)) !== null) {
      node.appendChild(document.createTextNode(text.slice(last, m.index)));
      const src = byN.get(Number(m[1]));
      if (src && src.url) {
        const a = el("a", "cite", m[1]);
        a.href = src.url;
        a.target = "_blank";
        a.rel = "noopener";
        a.title = src.file + (src.location ? " · " + src.location : "");
        node.appendChild(a);
      } else {
        node.appendChild(document.createTextNode(m[0]));
      }
      last = re.lastIndex;
    }
    node.appendChild(document.createTextNode(text.slice(last)));
  }

  function renderSources(container, sources) {
    container.replaceChildren();
    const cited = (sources || []).filter(function (s) { return s.cited; });
    if (!cited.length) return;
    container.appendChild(el("div", "sources-title muted", "Sources"));
    const ol = el("ol", "sources");
    cited.forEach(function (s) {
      const li = el("li");
      li.value = s.n;
      const icon = { api: "🔌 ", mail: "✉️ ", event: "📅 ", sharepoint: "🪟 " }[s.kind] || "";
      const label = icon + s.file +
        (s.location ? " · " + s.location : (s.page_start ? " · p. " + s.page_start : ""));
      if (s.url) {
        const a = el("a", null, label);
        a.href = s.url; a.target = "_blank"; a.rel = "noopener";
        li.appendChild(a);
      } else {
        li.appendChild(el("span", "muted", label + " (file deleted)"));
      }
      if (s.web_url) {
        const sp = el("a", "small", "Open in SharePoint");
        sp.href = s.web_url; sp.target = "_blank"; sp.rel = "noopener";
        li.append(document.createTextNode(" · "), sp);
      }
      if (s.section) li.appendChild(el("div", "muted small", s.section));
      ol.appendChild(li);
    });
    container.appendChild(ol);
  }

  function feedbackButtons(messageId) {
    const box = el("div", "feedback");
    [["👍", 1, "Helpful"], ["👎", -1, "Not helpful"]].forEach(function (f) {
      const b = el("button", "link", f[0]);
      b.type = "button";
      b.title = f[2];
      b.setAttribute("aria-label", f[2]);
      b.addEventListener("click", async function () {
        const r = await fetch("/api/messages/" + messageId + "/feedback", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rating: f[1] }) });
        box.replaceChildren(el("span", "muted small", r.ok ? "Thanks for the feedback." : "Couldn't save feedback."));
      });
      box.appendChild(b);
    });
    return box;
  }

  function addQuestion(text, where) {
    const welcome = thread.querySelector(".welcome");
    if (welcome) welcome.remove();
    const msg = el("div", "msg user");
    if (where) msg.appendChild(el("div", "scope-chip", "🔎 " + where));
    msg.appendChild(document.createTextNode(text));
    thread.appendChild(msg);
  }

  function addAnswerShell() {
    const wrap = el("div", "msg assistant");
    const body = el("div", "answer");
    const status = el("div", "muted small status", "Searching your documents…");
    const steps = el("div", "steps-box");
    const sources = el("div", "sources-box");
    const proposals = el("div", "proposals-box");
    wrap.append(status, steps, body, sources, proposals);
    thread.appendChild(wrap);
    wrap.scrollIntoView({ block: "end", behavior: "smooth" });
    return { wrap: wrap, body: body, status: status, steps: steps, sources: sources, proposals: proposals };
  }

  function finishAnswer(view, final) {
    view.status.remove();
    if (final.status === "error") {
      view.body.replaceChildren(el("span", "error", "Something went wrong answering this. Please try again."));
    } else if (final.status === "not_found") {
      view.wrap.classList.add("not-found");
      view.body.replaceChildren(document.createTextNode(final.answer));
    } else {
      renderAnswer(view.body, final.answer, final.sources);
      renderSources(view.sources, final.sources);
      if (final.route === "general") {
        view.wrap.insertBefore(el("div", "route-label muted small", "General answer, not from your documents"),
                               view.body);
      }
    }
    if (final.model && String(final.model).indexOf("bfgguf:") === 0 && final.status !== "error") {
      view.wrap.insertBefore(el("div", "route-label muted small", "🧠 BFGguf · " + final.model.slice(7)), view.body);
    }
    if (final.message_id && final.status !== "error") view.wrap.appendChild(feedbackButtons(final.message_id));
  }

  // Minimal SSE reader over fetch (EventSource can't POST).
  async function* readEvents(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const data = block.split("\n").filter(function (l) { return l.startsWith("data:"); })
          .map(function (l) { return l.slice(5).trim(); }).join("");
        if (data) yield JSON.parse(data);
      }
    }
  }

  // The question runs on the server as a job (a "run"); this page only
  // watches its events. When the connection drops, it picks them up again
  // from where it was, so a long answer isn't lost.
  function watcher(view) {
    return { view: view, runId: null, seen: 0, streamed: "", sources: [], finished: false };
  }

  function handle(w, ev) {
    const view = w.view;
    w.seen += 1;
    if (ev.type === "conversation") {
      conversationId = ev.conversation_id;
      w.runId = ev.run_id;
    } else if (ev.type === "route") {
      if (ev.route === "agent") view.status.textContent = "Working on it…";
      else if (ev.route !== "search") view.status.textContent = "Writing…";
      if (ev.claude && ev.fallback) {   // "Use External API" was on but couldn't be used: say why
        view.wrap.insertBefore(el("div", "route-label muted small", "Answered without the external API: " +
                                  ev.fallback), view.body);
      }
      if (ev.bfgguf && ev.fallback) {   // BFGguf was on but couldn't be used: say why
        view.wrap.insertBefore(el("div", "route-label muted small", "Answered without BFGguf: " + ev.fallback),
                               view.body);
      }
    } else if (ev.type === "tool") {
      window.ErynneAgent.tool(view.steps, ev);
      if (ev.state === "running") view.status.textContent = (ev.label || ev.name) + "…";
    } else if (ev.type === "proposal") {
      view.proposals.appendChild(window.ErynneAgent.proposal(ev));
    } else if (ev.type === "sources") {
      if (!ev.sources.length && view.status.textContent === "Writing…") return;
      w.sources = ev.sources;
      view.status.textContent = w.sources.length ? "Reading " + w.sources.length + " passages…" : "Checking…";
    } else if (ev.type === "delta") {
      w.streamed += ev.text;
      renderAnswer(view.body, w.streamed, w.sources);
    } else if (ev.type === "reset") {
      // What streamed so far didn't hold up; a "couldn't find it" reply follows.
      w.streamed = "";
      w.sources = [];
      renderAnswer(view.body, w.streamed, w.sources);
    } else if (ev.type === "final") {
      w.finished = true;
      finishAnswer(view, ev);
      addTokens(ev.tokens);
    }
  }

  async function follow(w, resp) {
    for await (const ev of readEvents(resp)) handle(w, ev);
  }

  // Pick a run's events up again after a dropped connection (or when a
  // conversation still being answered is opened).
  async function reattach(w) {
    for (let attempt = 0; attempt < 30 && !w.finished; attempt++) {
      if (attempt) await new Promise(function (r) { setTimeout(r, Math.min(2000 * attempt, 10000)); });
      w.view.status.textContent = attempt ? "Reconnecting…" : w.view.status.textContent;
      try {
        const resp = await fetch("/api/runs/" + w.runId + "/events?after=" + w.seen);
        if (resp.status === 404) break;          // finished long ago: the saved answer is in the conversation
        if (!resp.ok) continue;
        await follow(w, resp);
      } catch (e) { /* still offline; try again */ }
    }
    if (!w.finished && conversationId) await openConversation(conversationId);
  }

  async function ask(question) {
    addQuestion(question, scope ? scope.label : null);
    const w = watcher(addAnswerShell());
    send.disabled = true;
    try {
      const resp = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question, conversation_id: conversationId,
                               scope: scope ? { collection_id: scope.collection_id, folder_id: scope.folder_id } : null,
                               tools: true, disabled_tools: Array.from(toolsOff),
                               claude: useClaude ? useClaude.checked : false,
                               bfgguf: useBfgguf ? useBfgguf.checked : false }) });
      if (resp.status === 401) { window.location.href = "/login"; return; }
      if (!resp.ok) {
        const err = await resp.json().catch(function () { return {}; });
        finishAnswer(w.view, { status: "error" });
        w.view.body.replaceChildren(el("span", "error", err.detail || resp.statusText));
        return;
      }
      try { await follow(w, resp); } catch (e) { /* connection dropped: the answer goes on on the server */ }
      if (!w.finished && w.runId) await reattach(w);
      if (!w.finished) finishAnswer(w.view, { status: "error" });
    } catch (err) {
      finishAnswer(w.view, { status: "error" });
    } finally {
      send.disabled = false;
      loadConversations();
    }
  }

  async function loadConversations() {
    const r = await fetch("/api/conversations");
    if (!r.ok) return;
    const data = await r.json();
    list.replaceChildren.apply(list, data.conversations.map(function (c) {
      const li = el("li", c.id === conversationId ? "active" : "");
      const a = el("a", null, c.title || "Untitled");
      a.href = "#" + c.id;
      a.addEventListener("click", function (e) { e.preventDefault(); openConversation(c.id); });
      const del = el("button", "link danger small", "×");
      del.type = "button";
      del.title = "Delete conversation (Shift-click: no question asked)";
      del.addEventListener("click", async function (e) {
        if (!e.shiftKey && !confirm("Delete this conversation?")) return;
        await fetch("/api/conversations/" + c.id, { method: "DELETE" });
        if (c.id === conversationId) newChat();
        loadConversations();
      });
      li.append(a, del);
      return li;
    }));
  }

  async function openConversation(id) {
    const r = await fetch("/api/conversations/" + id);
    if (!r.ok) return;
    const data = await r.json();
    conversationId = id;
    thread.replaceChildren();
    resetTokens();
    data.messages.forEach(function (m) {
      if (m.role === "user") { thread.appendChild(el("div", "msg user", m.content)); return; }
      addTokens({ in: m.tokens_in, out: m.tokens_out, external: m.tokens_external, estimated: !!m.tokens_estimated });
      const view = addAnswerShell();
      const sources = m.sources.map(function (s) {
        return Object.assign({}, s, { location: s.kind === "pdf" && s.page_start ? "p. " + s.page_start
                                                                                   : (s.location || "") });
      });
      finishAnswer(view, { status: m.status, answer: m.content, sources: sources, message_id: m.id,
                           route: m.route, model: m.model });
      (m.proposals || []).forEach(function (p) { view.proposals.appendChild(window.ErynneAgent.proposal(p)); });
    });
    loadConversations();
    if (data.running) {
      // Still being answered (the page was closed or reloaded meanwhile): watch it from the start.
      const w = watcher(addAnswerShell());
      w.runId = data.running;
      send.disabled = true;
      try { await reattach(w); } finally { send.disabled = false; }
    }
  }

  function newChat() {
    conversationId = null;
    resetTokens();
    thread.replaceChildren(el("div", "welcome muted", "Ask a question about your documents."));
    loadConversations();
    input.focus();
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    const q = input.value.trim();
    if (!q || send.disabled) return;
    input.value = "";
    ask(q);
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  document.getElementById("new-chat").addEventListener("click", newChat);
  loadConversations();
})();
