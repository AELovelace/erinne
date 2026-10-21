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
  const scopeLabel = document.getElementById("scope-label");

  document.getElementById("scope").addEventListener("click", async function () {
    const picked = await window.Erinne.pickLocation({ title: "Search in…", ok: "Search here", allowAll: true,
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
      const label = s.file + (s.location ? " · " + s.location : (s.page_start ? " · p. " + s.page_start : ""));
      if (s.url) {
        const a = el("a", null, label);
        a.href = s.url; a.target = "_blank"; a.rel = "noopener";
        li.appendChild(a);
      } else {
        li.appendChild(el("span", "muted", label + " (file deleted)"));
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
    const sources = el("div", "sources-box");
    wrap.append(status, body, sources);
    thread.appendChild(wrap);
    wrap.scrollIntoView({ block: "end", behavior: "smooth" });
    return { wrap: wrap, body: body, status: status, sources: sources };
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

  async function ask(question) {
    addQuestion(question, scope ? scope.label : null);
    const view = addAnswerShell();
    send.disabled = true;
    let streamed = "";
    let sources = [];
    try {
      const resp = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question, conversation_id: conversationId,
                               scope: scope ? { collection_id: scope.collection_id, folder_id: scope.folder_id } : null }) });
      if (resp.status === 401) { window.location.href = "/login"; return; }
      if (!resp.ok) {
        const err = await resp.json().catch(function () { return {}; });
        finishAnswer(view, { status: "error" });
        view.body.replaceChildren(el("span", "error", err.detail || resp.statusText));
        return;
      }
      for await (const ev of readEvents(resp)) {
        if (ev.type === "conversation") {
          conversationId = ev.conversation_id;
        } else if (ev.type === "route") {
          if (ev.route !== "search") view.status.textContent = "Writing…";
        } else if (ev.type === "sources") {
          if (!ev.sources.length && view.status.textContent === "Writing…") continue;
          sources = ev.sources;
          view.status.textContent = sources.length ? "Reading " + sources.length + " passages…" : "Checking…";
        } else if (ev.type === "delta") {
          streamed += ev.text;
          renderAnswer(view.body, streamed, sources);
        } else if (ev.type === "final") {
          finishAnswer(view, ev);
        }
      }
    } catch (err) {
      finishAnswer(view, { status: "error" });
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
      del.title = "Delete conversation";
      del.addEventListener("click", async function () {
        if (!confirm("Delete this conversation?")) return;
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
    data.messages.forEach(function (m) {
      if (m.role === "user") { thread.appendChild(el("div", "msg user", m.content)); return; }
      const view = addAnswerShell();
      const sources = m.sources.map(function (s) {
        return Object.assign({}, s, { location: s.kind === "pdf" && s.page_start ? "p. " + s.page_start : "" });
      });
      finishAnswer(view, { status: m.status, answer: m.content, sources: sources, message_id: m.id,
                           route: m.route });
    });
    loadConversations();
  }

  function newChat() {
    conversationId = null;
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
