/* Automator: the person's scheduled automations. A list of them; the chosen one's editor (prompt,
   schedule, who runs it, its tools, where the answer goes) and its runs. All text through textContent;
   a run's answer is HTML the server rendered from Markdown (raw HTML escaped). */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const RUNNERS = { erynne: "Erynne", claude_key: "Claude (your key)", claude_routine: "A Claude routine" };
  const STATUS = { running: "running…", waiting: "waiting for Claude…", done: "done", quiet: "nothing to report",
                   failed: "failed", timed_out: "timed out" };
  const state = { data: null, selected: null, run: null };

  function showError(message) {
    const node = $("auto-error");
    node.textContent = message || "";
    node.hidden = !message;
  }

  function button(text, cls, onClick) {
    const b = el("button", cls, text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function field(labelText, input, hint) {
    const label = el("label", "api-field");
    label.appendChild(el("span", "small", labelText));
    label.appendChild(input);
    if (hint) label.appendChild(el("span", "muted small", hint));
    return label;
  }

  function input(value, type) {
    const i = el(type === "textarea" ? "textarea" : "input");
    if (type !== "textarea") i.type = type || "text";
    i.value = value === undefined || value === null ? "" : value;
    return i;
  }

  function select(value, choices) {
    const s = el("select");
    choices.forEach(function (c) { const o = el("option", null, c[1]); o.value = c[0]; s.appendChild(o); });
    s.value = value === undefined || value === null ? "" : String(value);
    return s;
  }

  function check(labelText, checked) {
    const label = el("label", "check small");
    const box = el("input");
    box.type = "checkbox";
    box.checked = !!checked;
    label.append(box, document.createTextNode(" " + labelText));
    return { label: label, box: box };
  }

  // ------------------------------------------------------------- the list
  function renderList() {
    const box = $("auto-list");
    box.replaceChildren();
    const list = state.data.automations;
    if (!list.length) {
      box.appendChild(el("p", "muted", "No automations yet. Start one with New automation; the templates are a good " +
        "place to begin."));
      return;
    }
    const ul = el("ul", "automator-list");
    list.forEach(function (a) {
      const li = el("li", "glass automator-card" + (a.id === state.selected ? " active" : ""));
      const head = el("div", "ax-bar");
      head.appendChild(el("strong", null, a.name));
      head.appendChild(el("span", "small muted", RUNNERS[a.runner]));
      li.appendChild(head);
      li.appendChild(el("p", "small", a.enabled ? "Runs " + a.when + (a.next_run_at ? "; next " + a.next_run_at : "")
                                                : "Paused" + (a.paused_reason ? ": " + a.paused_reason : "")));
      if (a.last_run) li.appendChild(el("p", "small muted", "Last run " + a.last_run.started_at + " UTC: " +
                                                              (STATUS[a.last_run.status] || a.last_run.status)));
      const actions = el("div", "ax-bar");
      actions.append(button("Open", "small", function () { open(a.id); }),
                     button("Run now", "small link", function () { runNow(a.id); }));
      li.appendChild(actions);
      ul.appendChild(li);
    });
    box.appendChild(ul);
  }

  // ----------------------------------------------------------- the editor
  function scheduleEditor(schedule) {
    const wrap = el("div", "automator-schedule");
    const kind = select(schedule.every === "minutes" ? "minutes" : "day",
                        [["day", "At a time of day"], ["minutes", "Every so often"]]);
    const at = input(schedule.at || "07:30", "time");
    const days = DAYS.map(function (d) {
      return check(d.charAt(0).toUpperCase() + d.slice(1), !schedule.days || schedule.days.indexOf(d) >= 0);
    });
    const dayRow = el("div", "automator-days");
    days.forEach(function (d) { dayRow.appendChild(d.label); });
    const every = select(String(schedule.n || 240), [["15", "15 minutes"], ["30", "30 minutes"], ["60", "hour"],
      ["120", "2 hours"], ["240", "4 hours"], ["360", "6 hours"], ["720", "12 hours"]]);
    if (schedule.n && !every.querySelector('option[value="' + schedule.n + '"]')) {
      const o = el("option", null, schedule.n + " minutes"); o.value = String(schedule.n); every.appendChild(o);
      every.value = String(schedule.n);
    }
    const daily = el("div"), often = el("div");
    daily.append(field("At", at), dayRow);
    often.append(field("Every", every));
    function show() { daily.hidden = kind.value !== "day"; often.hidden = kind.value === "day"; }
    kind.addEventListener("change", show);
    wrap.append(field("When", kind), daily, often);
    show();
    return {
      node: wrap,
      value: function () {
        if (kind.value === "day") {
          return { every: "day", at: at.value, days: DAYS.filter(function (d, i) { return days[i].box.checked; }) };
        }
        return { every: "minutes", n: parseInt(every.value, 10) };
      }
    };
  }

  function toolPicker(chosen) {
    const wrap = el("details", "automator-tools");
    wrap.appendChild(el("summary", "small", "Tools (" + (chosen.length ? chosen.length + " picked" : "all reading tools") + ")"));
    wrap.appendChild(el("p", "small muted", "Leave everything unticked to let it use every tool that only reads. " +
      "Tools that change something are used only if it may propose changes."));
    const boxes = [];
    let group = null, list = null;
    state.data.tools.forEach(function (t) {
      if (t.group !== group) {
        group = t.group;
        wrap.appendChild(el("strong", "small", group));
        list = el("div", "automator-tool-group");
        wrap.appendChild(list);
      }
      const c = check(t.label + (t.write ? " (proposes)" : "") + (t.subgroup ? " · " + t.subgroup : ""),
                      chosen.indexOf(t.name) >= 0);
      boxes.push([t.name, c.box]);
      list.appendChild(c.label);
    });
    return { node: wrap, value: function () { return boxes.filter(function (b) { return b[1].checked; }).map(function (b) { return b[0]; }); } };
  }

  function editor(a) {
    const form = el("form", "api-form");
    const name = input(a.name), prompt = input(a.prompt, "textarea");
    prompt.rows = 6;
    const runner = select(a.runner, Object.keys(RUNNERS).filter(function (k) {
      return k === "erynne" || state.data.claude;
    }).map(function (k) { return [k, RUNNERS[k]]; }));
    const tz = input(a.tz || state.data.timezone);
    const schedule = scheduleEditor(a.schedule || { every: "day", at: "07:30" });
    const routineUrl = input(a.routine_url), routineToken = input("", "password");
    routineToken.placeholder = a.routine_token ? "Saved (type to replace)" : "sk-ant-oat01-…";
    const routine = el("div", "automator-routine");
    routine.append(el("p", "small muted", "Make a routine at claude.ai/code/routines with the Mnemerynne connector, " +
      "give it an API trigger, and paste the trigger's address and token here. Claude reports back with " +
      "report_automation_run."), field("Routine address", routineUrl), field("Routine token", routineToken));
    const tools = toolPicker(a.tools || []);
    const propose = check("May propose changes (files, mail drafts, API actions) for me to apply", a.may_propose);
    const deliver = a.deliver || { inbox: true };
    const inbox = check("My Inbox", deliver.inbox);
    const room = select(deliver.room_id || "", [["", "No room"]].concat(state.data.rooms.map(function (r) {
      return [String(r.id), (r.kind === "channel" ? "#" : "") + r.name];
    })));
    const draft = check("A draft in my Outlook", deliver.email_draft);
    const hooks = state.data.destinations.map(function (d) {
      return [d.id, check("Webhook: " + d.name, (deliver.destinations || []).indexOf(d.id) >= 0)];
    });
    const sendTo = el("fieldset", "automator-deliver");
    sendTo.appendChild(el("legend", "small", "Send the answer to"));
    sendTo.append(inbox.label, draft.label);
    hooks.forEach(function (h) { sendTo.appendChild(h[1].label); });
    const roomField = field("A room", room, "Posted by Erynne. Everyone there reads it, so she reads only what they all may.");
    if (state.data.rooms.length) sendTo.appendChild(roomField);
    const enabled = check("On", a.enabled !== false);

    function showRunner() {
      routine.hidden = runner.value !== "claude_routine";
      roomField.hidden = runner.value !== "erynne";
      tools.node.hidden = runner.value === "claude_routine";
    }
    runner.addEventListener("change", showRunner);
    form.append(field("Name", name), field("What to do", prompt, "Write it as a request: Erynne does it with your " +
      "access and answers with a report. If there's nothing to report she can say so, and nothing is sent."),
      schedule.node, field("Time zone", tz), field("Who runs it", runner), routine, tools.node, propose.label,
      sendTo, enabled.label);
    showRunner();

    const save = el("button", null, a.id ? "Save" : "Create");
    save.type = "submit";
    form.appendChild(save);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      const body = { name: name.value, prompt: prompt.value, schedule: schedule.value(), tz: tz.value,
                     runner: runner.value, tools: tools.value(), may_propose: propose.box.checked,
                     enabled: enabled.box.checked,
                     deliver: { inbox: inbox.box.checked, email_draft: draft.box.checked,
                                room_id: runner.value === "erynne" && room.value ? parseInt(room.value, 10) : null,
                                destinations: hooks.filter(function (h) { return h[1].box.checked; }).map(function (h) { return h[0]; }) } };
      if (runner.value === "claude_routine") {
        body.routine_url = routineUrl.value;
        if (routineToken.value) body.routine_token = routineToken.value;
      }
      try {
        const saved = a.id ? await api("PATCH", "/api/automations/" + a.id, body) : await api("POST", "/api/automations", body);
        await load(saved.id);
      } catch (err) { showError(err.message); }
    });
    return form;
  }

  // ------------------------------------------------------------- the runs
  async function renderRuns(a, box) {
    box.replaceChildren(el("p", "muted small", "Loading runs…"));
    let data;
    try { data = await api("GET", "/api/automations/" + a.id + "/runs"); }
    catch (err) { box.replaceChildren(el("p", "error", err.message)); return; }
    box.replaceChildren();
    if (!data.runs.length) { box.appendChild(el("p", "muted small", "It hasn't run yet.")); return; }
    const ul = el("ul", "automator-runs");
    data.runs.forEach(function (r) {
      const li = el("li");
      const b = button(r.started_at + " · " + (STATUS[r.status] || r.status) + (r.trigger !== "schedule" ? " (" + r.trigger + ")" : ""),
                       "link small", function () { showRun(r.run_id, out); });
      li.appendChild(b);
      ul.appendChild(li);
    });
    const out = el("div", "automator-output");
    box.append(ul, out);
    showRun(state.run && data.runs.some(function (r) { return r.run_id === state.run; }) ? state.run : data.runs[0].run_id, out);
  }

  async function showRun(runId, box) {
    state.run = runId;
    box.replaceChildren(el("p", "muted small", "Loading…"));
    let r;
    try { r = await api("GET", "/api/automations/runs/" + runId); }
    catch (err) { box.replaceChildren(el("p", "error", err.message)); return; }
    box.replaceChildren();
    box.appendChild(el("p", "small muted", "Run " + r.run_id + " · " + (STATUS[r.status] || r.status) +
      (r.finished_at ? " · finished " + r.finished_at : "") + (r.model ? " · " + r.model : "")));
    if (r.error) box.appendChild(el("p", "error small", r.error));
    if (r.claude_session_url) {
      const link = el("a", "small", "Claude's session");
      link.href = r.claude_session_url; link.target = "_blank"; link.rel = "noopener";
      box.appendChild(link);
    }
    if (r.html) {
      const body = el("div", "md automator-answer");
      body.innerHTML = r.html;   // rendered server-side from Markdown, raw HTML escaped
      box.appendChild(body);
    }
    if (r.delivered && r.delivered.length) {
      box.appendChild(el("p", "small muted", "Sent to " + r.delivered.map(function (d) {
        return d.to + (d.ok ? "" : " (failed" + (d.error ? ": " + d.error : "") + ")");
      }).join(", ")));
    }
    if (r.status === "running" || r.status === "waiting") {
      setTimeout(function () { if (state.run === runId && document.body.contains(box)) showRun(runId, box); }, 5000);
    }
  }

  // --------------------------------------------------------------- detail
  function renderDetail() {
    const pane = $("auto-detail");
    pane.replaceChildren();
    if (state.selected === null) { pane.hidden = true; return; }
    pane.hidden = false;
    const a = state.selected === "new" ? newDraft() : state.data.automations.find(function (x) { return x.id === state.selected; });
    if (!a) { pane.hidden = true; return; }
    const head = el("div", "ax-bar");
    head.appendChild(el("h2", null, a.id ? a.name : "New automation"));
    if (a.id) {
      head.appendChild(button("Delete", "link danger small", async function () {
        if (!confirm("Delete “" + a.name + "” and its runs?")) return;
        try { await api("DELETE", "/api/automations/" + a.id); state.selected = null; await load(); }
        catch (err) { showError(err.message); }
      }));
    } else {
      const pick = select("", [["", "Start from a template…"]].concat(state.data.templates.map(function (t) { return [t.key, t.name]; })));
      pick.addEventListener("change", function () {
        const t = state.data.templates.find(function (x) { return x.key === pick.value; });
        if (t) { state.template = t; renderDetail(); }
      });
      head.appendChild(pick);
    }
    pane.appendChild(head);
    if (a.paused_reason && !a.enabled) pane.appendChild(el("p", "small error", "Paused: " + a.paused_reason));
    pane.appendChild(editor(a));
    if (a.id) {
      const runsHead = el("div", "ax-bar");
      runsHead.appendChild(el("h3", null, "Runs"));
      runsHead.appendChild(button("Run now", "small", function () { runNow(a.id); }));
      pane.appendChild(runsHead);
      const runs = el("div");
      pane.appendChild(runs);
      renderRuns(a, runs);
    }
  }

  function newDraft() {
    const t = state.template || {};
    return { name: t.name || "", prompt: t.prompt || "", runner: t.runner || "erynne", schedule: t.schedule,
             deliver: t.deliver || { inbox: true }, tools: [], enabled: true, tz: state.data.timezone };
  }

  function open(id) {
    state.selected = id;
    state.run = null;
    if (id !== "new") state.template = null;
    location.hash = id === "new" ? "" : "a=" + id;
    renderList();
    renderDetail();
  }

  async function runNow(id) {
    showError("");
    try {
      const got = await api("POST", "/api/automations/" + id + "/run");
      state.run = got.run_id;
      await load(id);
    } catch (err) { showError(err.message); }
  }

  async function load(select) {
    try { state.data = await api("GET", "/api/automations"); }
    catch (err) { showError(err.message); return; }
    if (select !== undefined) state.selected = select;
    renderList();
    renderDetail();
  }

  $("auto-new").addEventListener("click", function () { open("new"); });
  const hash = location.hash.slice(1);
  const wanted = /^a=(\d+)$/.exec(hash), run = /^run=(\d+)$/.exec(hash);
  if (run) {
    api("GET", "/api/automations/runs/" + run[1]).then(function (r) {
      state.run = r.run_id;
      load(r.automation_id);
    }).catch(function () { load(); });
  } else {
    load(wanted ? parseInt(wanted[1], 10) : undefined);
  }
})();
