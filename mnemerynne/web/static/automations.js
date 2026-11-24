/* Automator: the person's scheduled automations. A list of them; the chosen one's editor (prompt,
   schedule, who runs it, its tools, where the answer goes) and its runs. All text through textContent;
   a run's answer is HTML the server rendered from Markdown (raw HTML escaped). */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const RUNNERS = { erynne: "Erynne", claude_key: "Claude (your key)", claude_routine: "A Claude routine" };
  const STATUS = { queued: "starting…", running: "running…", waiting: "waiting for Claude…", reported: "delivering…",
                   done: "done", quiet: "nothing to report", failed: "failed", timed_out: "timed out",
                   cancelled: "cancelled" };
  const FINAL = ["done", "quiet", "failed", "timed_out", "cancelled"];   // a run in one of these won't change any more
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

  /* Cancel a run that's going (queued, running, waiting for Claude, delivering): it stops and nothing is
     delivered. after() runs once the server has said yes, to redraw what shows it. */
  async function cancelRun(runId, name, after) {
    if (!confirm("Cancel this run of “" + name + "”? It stops now and nothing is delivered.")) return;
    showError("");
    try {
      await api("POST", "/api/automations/runs/" + runId + "/cancel");   // 409 if it ended meanwhile
    } catch (err) { showError(err.message); }
    if (after) after();                                               // redraw either way: it has ended
  }

  function cancelButton(runId, name, after) {                         // the red Cancel beside a running run
    return button("Cancel", "link small danger", function () { cancelRun(runId, name, after); });
  }

  /* A run's proposals live on the Proposals page (/proposals, the flower menu): a link there, to the first one. */
  function proposalsLink(ids, cls) {
    const link = el("a", cls, ids.length + (ids.length === 1 ? " proposal" : " proposals") + " → Proposals");
    link.href = "/proposals#p" + ids[0];                               // proposals.html highlights #p<id>
    return link;
  }

  function fmtDuration(seconds) {                                     // 45s · 4m 12s · 1h 05m
    if (seconds === null || seconds === undefined) return "—";
    const s = Math.round(seconds);
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m " + String(s % 60).padStart(2, "0") + "s";
    return Math.floor(s / 3600) + "h " + String(Math.floor(s / 60) % 60).padStart(2, "0") + "m";
  }

  function fmtClock(date) {                                           // this browser's time of day, e.g. 07:30
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  // --------------------------------------------------------------- sorting
  /* The list (and the Timeline's lanes) by name, or by the time of day they run: daily ones by their clock time,
     then the every-so-often ones, most frequent first. The pick is remembered in this browser. */
  const SORT_KEY = "erynne.automatorSort";
  function loadSort() { try { return localStorage.getItem(SORT_KEY) || "name"; } catch (e) { return "name"; } }
  function saveSort(v) { try { localStorage.setItem(SORT_KEY, v); } catch (e) { /* still sorts this page */ } }
  state.sort = loadSort();

  function clockMinutes(a) {                                          // minutes after midnight it runs
    const s = a.schedule || {};
    const clock = s.every === "day" ? s.at : s.between ? s.between[0] : null;   // daily: its time; a window: its start
    if (clock) { const p = clock.split(":"); return parseInt(p[0], 10) * 60 + parseInt(p[1], 10); }
    return 24 * 60 + (s.n || 0);                                      // all-day every N minutes: after the rest
  }

  function sorted(list) {                                             // a sorted copy; the server sends A–Z
    const byName = function (x, y) { return x.name.localeCompare(y.name, undefined, { sensitivity: "base" }); };
    const copy = list.slice();
    if (state.sort === "time") copy.sort(function (x, y) { return clockMinutes(x) - clockMinutes(y) || byName(x, y); });
    else copy.sort(byName);
    return copy;
  }

  function sortPicker() {                                             // "Sort: A–Z | Time of day", on both tabs
    const s = select(state.sort, [["name", "A–Z"], ["time", "Time of day"]]);
    s.setAttribute("aria-label", "Sort automations");
    s.addEventListener("change", function () {
      state.sort = s.value;
      saveSort(s.value);
      renderList();                                                   // one choice: the list and the Timeline
      if (state.timeline) renderTimeline($("auto-timeline"), state.timeline);
    });
    const label = el("label", "small automator-sort");
    label.append(document.createTextNode("Sort "), s);
    return label;
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
    box.appendChild(sortPicker());                                    // redraws the list (and Timeline) in the new order
    const ul = el("ul", "automator-list");
    sorted(list).forEach(function (a) {
      const li = el("li", "glass automator-card" + (a.id === state.selected ? " active" : ""));
      const head = el("div", "ax-bar");
      head.appendChild(el("strong", null, a.name));
      head.appendChild(el("span", "small muted", RUNNERS[a.runner]));
      li.appendChild(head);
      li.appendChild(el("p", "small", a.enabled ? "Runs " + a.when + (a.next_run_at ? "; next " + a.next_run_at : "")
                                                : "Paused" + (a.paused_reason ? ": " + a.paused_reason : "")));
      if (a.prompt_file) {                                            // its instructions live in a file
        li.appendChild(el("p", "small" + (a.prompt_file.missing ? " error" : " muted"), a.prompt_file.missing
          ? "Its prompt file (file " + a.prompt_file.id + ") is gone"
          : "Follows " + a.prompt_file.name));
      }
      if (a.last_run) li.appendChild(el("p", "small muted", "Last run " + a.last_run.started_at + " UTC: " +
                                                              (STATUS[a.last_run.status] || a.last_run.status)));
      const actions = el("div", "ax-bar");
      actions.append(button("Open", "small", function () { open(a.id); }),
                     button("Run now", "small link", function () { runNow(a.id); }));
      if (a.running && a.last_run) {                                  // its last run is still going
        actions.appendChild(cancelButton(a.last_run.id, a.name, function () { load(state.selected); }));
      }
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
    // "Every so often", optionally only in a window of the day on chosen days: every hour, 08:00 to 17:00, Mon to
    // Fri is business hours. It runs on the window's own beat (08:00, 09:00, … 17:00); an end earlier than the
    // start runs overnight.
    const win = schedule.between || null;
    const only = check("Only between", !!win);
    const from = input(win ? win[0] : "08:00", "time"), to = input(win ? win[1] : "17:00", "time");
    const winDays = DAYS.map(function (d, i) {                        // business hours: weekdays ticked to start
      return check(d.charAt(0).toUpperCase() + d.slice(1), win ? (!schedule.days || schedule.days.indexOf(d) >= 0) : i < 5);
    });
    const winDayRow = el("div", "automator-days");
    winDays.forEach(function (d) { winDayRow.appendChild(d.label); });
    const winTimes = el("div", "automator-window-times");
    winTimes.append(field("From", from), field("To", to));
    const winBox = el("div", "automator-window");
    winBox.append(winTimes, winDayRow, el("span", "muted small", "Runs at the start time, then every interval up to " +
      "and including the end time, on the ticked days. An end earlier than the start runs overnight."));
    function showWindow() { winBox.hidden = !only.box.checked; }
    only.box.addEventListener("change", showWindow);
    often.append(field("Every", every), only.label, winBox);
    showWindow();
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
        const out = { every: "minutes", n: parseInt(every.value, 10) };
        if (only.box.checked) {                                       // the window, and the days it opens on
          out.between = [from.value, to.value];
          out.days = DAYS.filter(function (d, i) { return winDays[i].box.checked; });
        }
        return out;
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

  /* "What to do": typed into the box, or a prompt file (a .md or .txt in Mnemerynne) read fresh on every run.
     The box keeps its text either way, so switching back loses nothing. value() gives the API's two fields. */
  function promptEditor(a) {
    const wrap = el("div", "automator-prompt");
    const from = select(a.prompt_file ? "file" : "text", [["text", "Write it here"], ["file", "Use a prompt file"]]);
    const prompt = input(a.prompt, "textarea");
    prompt.rows = 6;
    const typed = field("What to do", prompt, "Write it as a request: Erynne does it with your access and answers " +
      "with a report. If there's nothing to report she can say so, and nothing is sent.");
    let fileId = a.prompt_file ? a.prompt_file.id : null;             // the picked file, null until one is
    const chosen = el("p", "small automator-file");                    // which file it follows now
    const search = input("", "search");
    search.placeholder = "Search your files by name…";
    const results = el("ul", "automator-file-results");                // the matches, as buttons
    const fileBox = el("div", "automator-file-box");
    fileBox.append(el("span", "small", "Prompt file"), chosen, search, results,
      el("span", "muted small", "A .md or .txt file you can read. Every run reads it fresh, so editing the file " +
        "changes what this does (Versions on the file keeps the history). If it has a fenced code block, the first " +
        "one is the instructions and the rest of the file can explain them; otherwise the whole file is."));

    function showChosen(file) {                                        // the picked file's name, linked to open it
      chosen.replaceChildren();
      if (!file) { chosen.appendChild(el("span", "muted", "None picked yet: search below.")); return; }
      if (file.missing) { chosen.appendChild(el("span", "error", "File " + file.id + " is gone: pick another.")); return; }
      const link = el("a", null, file.name);
      link.href = file.link || "/files/" + file.id + "/view";
      link.target = "_blank";                                          // opens beside the editor
      chosen.append(document.createTextNode("Follows "), link, el("span", "muted", " (file " + file.id + ")"));
    }

    let timer = null;
    search.addEventListener("input", function () {                     // look up names as they type, gently
      clearTimeout(timer);
      timer = setTimeout(async function () {
        const q = search.value.trim();
        results.replaceChildren();
        if (q.length < 2) return;                                      // a letter matches everything
        let data;
        try { data = await api("GET", "/api/automations/prompt-files?q=" + encodeURIComponent(q)); }
        catch (err) { results.appendChild(el("li", "error small", err.message)); return; }
        if (!data.files.length) { results.appendChild(el("li", "muted small", "No .md or .txt files by that name.")); return; }
        data.files.forEach(function (f) {
          const li = el("li");
          li.appendChild(button(f.name, "link small", function () {    // pick it
            fileId = f.id;
            showChosen({ id: f.id, name: f.name, missing: false });
            results.replaceChildren();
            search.value = "";
          }));
          li.appendChild(el("span", "muted small", " · " + f.library));
          results.appendChild(li);
        });
      }, 250);
    });

    function show() { typed.hidden = from.value !== "text"; fileBox.hidden = from.value !== "file"; }
    from.addEventListener("change", show);
    showChosen(a.prompt_file);
    wrap.append(field("Instructions", from), typed, fileBox);
    show();
    return {
      node: wrap,
      value: function () {                                             // 0 = no file: the box's text is used
        return { prompt: prompt.value, prompt_file_id: from.value === "file" ? fileId : 0 };
      },
      problem: function () {                                           // said before saving, so nothing half-saves
        return from.value === "file" && !fileId ? "Pick a prompt file, or switch back to writing it here." : "";
      }
    };
  }

  function editor(a) {
    const form = el("form", "api-form");
    const name = input(a.name);
    const instructions = promptEditor(a);
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
    const autoApprove = check("Approve its proposals automatically (applied as me at the end of each run; ones that " +
                              "need an admin still wait)", a.auto_approve);
    autoApprove.label.classList.add("automator-sub");                  // indented under “May propose”
    function showApprove() {                                           // auto-approval only means something while it may propose
      autoApprove.box.disabled = !propose.box.checked;
      if (!propose.box.checked) autoApprove.box.checked = false;
    }
    propose.box.addEventListener("change", showApprove);
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
    const roomHint = el("span", "muted small");                       // says whose access the post may carry
    const roomField = field("A room", room);
    roomField.appendChild(roomHint);
    const roomShare = check("Share what my access finds with everyone in this room", deliver.room_share);
    roomShare.label.classList.add("automator-sub");                   // Claude runs only: the owner says they mean it
    if (state.data.rooms.length) sendTo.append(roomField, roomShare.label);
    const enabled = check("On", a.enabled !== false);

    function showRoom() {                                             // the warning only matters for Claude with a room
      const erynne = runner.value === "erynne";
      roomHint.textContent = erynne
        ? "Posted by Erynne. Everyone there reads it, so she reads only what they all may."
        : "Posted by Erynne, but Claude did the job with your own access: its report can hold what only you may see. " +
          "The post says it ran with your access.";
      roomShare.label.hidden = erynne || !room.value;
    }
    room.addEventListener("change", showRoom);

    function showRunner() {
      routine.hidden = runner.value !== "claude_routine";
      tools.node.hidden = runner.value === "claude_routine";
      showRoom();                                                     // the hint and the share box follow the runner
    }
    runner.addEventListener("change", showRunner);
    form.append(field("Name", name), instructions.node, schedule.node, field("Time zone", tz), field("Who runs it", runner), routine, tools.node, propose.label,
      autoApprove.label, sendTo, enabled.label);
    showRunner();
    showApprove();

    const save = el("button", null, a.id ? "Save" : "Create");
    save.type = "submit";
    form.appendChild(save);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      if (instructions.problem()) { showError(instructions.problem()); return; }
      const body = { name: name.value, ...instructions.value(), schedule: schedule.value(), tz: tz.value,
                     runner: runner.value, tools: tools.value(), may_propose: propose.box.checked,
                     auto_approve: propose.box.checked && autoApprove.box.checked,
                     enabled: enabled.box.checked,
                     deliver: { inbox: inbox.box.checked, email_draft: draft.box.checked,
                                room_id: room.value ? parseInt(room.value, 10) : null,   // any runner (Claude: with the box ticked)
                                room_share_ack: runner.value !== "erynne" && roomShare.box.checked,
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
      li.append(b, document.createTextNode(" "), button("Raw output", "link small muted", function () {
        liveRun(r.run_id, a.name);                                    // the step-by-step trace, live if it's running
      }));
      if (FINAL.indexOf(r.status) < 0) {                              // still going: it can be stopped
        li.append(document.createTextNode(" "), cancelButton(r.run_id, a.name, function () { load(a.id); }));
      }
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
    box.appendChild(button("Raw output (" + (r.trace ? r.trace.length : 0) + " steps)", "small", function () {
      liveRun(r.run_id, r.name);                                      // every step it took, with each tool's raw result
    }));
    if (FINAL.indexOf(r.status) < 0) {                                // still going: offer to stop it
      box.append(document.createTextNode(" "), cancelButton(r.run_id, r.name, function () { load(r.automation_id); }));
    }
    if (r.proposals && r.proposals.length) box.appendChild(proposalsLink(r.proposals, "small"));
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
    if (FINAL.indexOf(r.status) < 0) {                                // still going: look again shortly
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
      const a = state.data && state.data.automations.find(function (x) { return x.id === id; });
      liveRun(got.run_id, a ? a.name : "Automation", function () { load(id); });   // the page catches up when it ends
      await load(id);
    } catch (err) { showError(err.message); }
  }

  async function load(select) {
    try { state.data = await api("GET", "/api/automations"); }
    catch (err) { showError(err.message); return; }
    if (select !== undefined) state.selected = select;
    showRunning(state.data.automations.filter(function (a) { return a.running; }).length);   // the Timeline tab's badge
    renderList();
    renderDetail();
  }

  // ------------------------------------------------------- the raw output, live
  const KINDS = { start: "Started", prompt: "Prompt", info: "Note", note: "Model's note", route: "Route", tool: "Tool",
                  proposal: "Proposal", sources: "Sources", delta: "Answer", answer: "Answer", routine: "Claude routine",
                  approval: "Auto-approval", delivered: "Delivered", finish: "Finished", error: "Error", cut: "Cut",
                  cancelled: "Cancelled" };

  function pretty(value) {                                            // a step's field as readable text
    if (typeof value === "string") {
      try { return JSON.stringify(JSON.parse(value), null, 2); } catch (e) { return value; }   // trimmed JSON stays text
    }
    return JSON.stringify(value, null, 2);
  }

  function stepLine(step) {                                           // the one-line gist of a step
    switch (step.kind) {
      case "tool": return (step.summary || step.label || step.name) + (step.state ? " · " + step.state : "");
      case "delivered": return step.to + (step.ok ? " ✓" : " ✗ " + (step.error || ""));
      case "approval": return (step.ok ? "✓ applied: " : "✗ not applied: ") + (step.summary || "#" + step.proposal) +
                              (step.error ? " (" + step.error + ")" : "");
      case "sources": return Array.isArray(step.sources) ? step.sources.length + " sources" : "sources";
      case "finish": return (STATUS[step.status] || step.status) + (step.text ? ": " + step.text : "");
      case "start": return step.automation + " · " + step.trigger + " · " + step.runner + " · for " + step.owner;
      case "proposal": return step.summary || "";
      default: return step.text || step.label || "";
    }
  }

  function stepNode(step, t0) {
    const bad = step.state === "failed" || step.kind === "error" || step.ok === false;
    const li = el("li", "automator-step" + (bad ? " is-bad" : ""));
    const head = el("div", "automator-step-head");
    head.append(el("span", "automator-step-time small", "+" + Math.max(0, step.at - t0).toFixed(1) + "s"),
                el("strong", "small", KINDS[step.kind] || step.kind));
    const line = stepLine(step);
    if (line) head.appendChild(el("span", "automator-step-line small", line.length > 220 ? line.slice(0, 220) + "…" : line));
    li.appendChild(head);
    const extra = Object.keys(step).filter(function (k) { return ["n", "at", "kind"].indexOf(k) < 0; });
    if (extra.length) {                                               // everything it carried, raw
      const more = el("details", "automator-step-raw");
      more.appendChild(el("summary", "small muted", "raw"));
      extra.forEach(function (k) {
        more.append(el("div", "small muted", k), el("pre", "small", pretty(step[k])));
      });
      li.appendChild(more);
    }
    return li;
  }

  /* A modal with a run's trace, filled in live from /api/automations/runs/{id}/stream, then its finished report.
     Closing it doesn't stop the run. onEnd (optional) runs once when the run is over. */
  function liveRun(runId, name, onEnd) {
    const dlg = el("dialog", "dlg glass wide automator-live");
    const head = el("div", "ax-bar");
    const status = el("span", "badge small", "connecting…");
    head.append(el("h2", null, name + " · run " + runId), status);
    const follow = check("Follow the newest step", true);
    const list = el("ol", "automator-trace");
    const result = el("div", "automator-live-result");
    const actions = el("div", "dlg-actions");
    const steps = [];
    let t0 = null, es = null, ended = false;
    const copy = button("Copy raw JSON", "link", async function () {
      try { await navigator.clipboard.writeText(JSON.stringify(steps, null, 2)); copy.textContent = "Copied ✓"; }
      catch (e) { copy.textContent = "Couldn't copy"; }
    });
    const stop = cancelButton(runId, name, null);                     // hidden once the run is over
    actions.append(stop, copy, button("Close", "", function () { dlg.close(); }));
    dlg.append(head, follow.label, list, result, actions);
    document.body.appendChild(dlg);

    function add(step) {
      if (step.n && step.n <= steps.length) return;                   // already shown (after a reconnect)
      steps.push(step);
      if (t0 === null) t0 = step.at;
      list.appendChild(stepNode(step, t0));
      if (follow.box.checked) list.scrollTop = list.scrollHeight;      // keep the newest in view
    }

    function finish(r) {
      ended = true;
      stop.hidden = true;                                             // nothing left to cancel
      status.textContent = r.gone ? "gone" : (STATUS[r.status] || r.status);
      if (r.gone) { result.replaceChildren(el("p", "error small", "That run is gone.")); return; }
      result.replaceChildren();
      if (r.error) result.appendChild(el("p", "error small", r.error));
      if (r.html) {
        const body = el("div", "md automator-answer");
        body.innerHTML = r.html;   // rendered server-side from Markdown, raw HTML escaped
        result.appendChild(body);
      }
      if (r.proposals && r.proposals.length) result.appendChild(proposalsLink(r.proposals, "small"));
      if (onEnd) onEnd();
    }

    function connect() {                                              // from the first step not shown yet
      es = new EventSource("/api/automations/runs/" + runId + "/stream?after=" + steps.length);
      es.addEventListener("step", function (e) { add(JSON.parse(e.data)); });
      es.addEventListener("status", function (e) {
        const s = JSON.parse(e.data).status;
        status.textContent = STATUS[s] || s;
        stop.hidden = FINAL.indexOf(s) >= 0;                          // Cancel only while it's going
      });
      es.addEventListener("end", function (e) { es.close(); finish(JSON.parse(e.data)); });
      es.onerror = function () {                                      // dropped: pick up where it left off
        es.close();
        if (!ended && dlg.open) { status.textContent = "reconnecting…"; setTimeout(connect, 2000); }
      };
    }

    dlg.addEventListener("close", function () { ended = true; if (es) es.close(); dlg.remove(); });
    dlg.showModal();
    connect();
  }

  // ------------------------------------------------------------ the tabs
  function showTab(name) {
    document.querySelectorAll("#automator [role=tab]").forEach(function (t) {
      t.setAttribute("aria-selected", String(t.dataset.tab === name));
    });
    document.querySelectorAll("#automator [data-panel]").forEach(function (p) { p.hidden = p.dataset.panel !== name; });
    $("auto-new").hidden = name !== "automations";
    clearInterval(state.timelineTimer);                               // only the open Timeline refreshes itself
    if (name === "timeline") {
      loadTimeline();
      state.timelineTimer = setInterval(function () { if (!document.hidden) loadTimeline(); }, 15000);
    }
  }

  function showRunning(n) {                                           // how many are running, on the Timeline tab
    const badge = $("auto-running");
    badge.textContent = n ? n + " running" : "";
    badge.hidden = !n;
  }

  // ------------------------------------------------------------ the timeline
  /* A day of runs, one lane per automation, from this browser's midnight: what ran (solid, coloured by how it
     ended), what's running (striped, up to the "now" line, then dashed to when it usually finishes) and what's
     planned (a slot as wide as its usual run). Each run's length is from when it was scheduled (or started, for
     Run now) until its last message went out. Hover for times; click a run for its raw output. */
  state.day = 0;                                                      // days from today: -1 yesterday, 1 tomorrow

  function dayStart() {                                               // this browser's midnight, state.day days on
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() + state.day);
    return d;
  }

  async function loadTimeline() {
    const box = $("auto-timeline");
    let data;
    try { data = await api("GET", "/api/automations/timeline?hours=24&start=" + encodeURIComponent(dayStart().toISOString())); }
    catch (err) { box.replaceChildren(el("p", "error", err.message)); return; }
    state.timeline = data;
    renderTimeline(box, data);
  }

  function renderTimeline(box, data) {
    const t0 = Date.parse(data.start), t1 = Date.parse(data.end), now = Date.parse(data.now);
    const span = t1 - t0;
    const pct = function (t) { return Math.min(100, Math.max(0, (t - t0) / span * 100)); };   // a time's place on a track
    const place = function (node, from, to) {                         // stretch a node across [from, to]
      node.style.left = pct(from) + "%";
      node.style.width = Math.max(0, pct(to) - pct(from)) + "%";
      return node;
    };
    box.replaceChildren();

    // The day picker and the sort, then the key.
    const head = el("div", "ax-bar tl-head");
    const label = el("strong", null, (state.day === 0 ? "Today" : state.day === -1 ? "Yesterday" : state.day === 1 ? "Tomorrow" : "") +
      " " + new Date(t0).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }));
    head.append(button("‹", "small", function () { state.day -= 1; loadTimeline(); }), label,
                button("›", "small", function () { state.day += 1; loadTimeline(); }));
    if (state.day !== 0) head.appendChild(button("Today", "link small", function () { state.day = 0; loadTimeline(); }));
    head.appendChild(sortPicker());
    box.appendChild(head);
    const key = el("p", "small muted tl-key");
    [["tl-run is-done", "ran"], ["tl-run is-failed", "failed"], ["tl-run is-running", "running now"],
     ["tl-expect", "should finish by"], ["tl-slot", "scheduled (as wide as it usually takes)"]].forEach(function (k) {
      key.append(el("span", "tl-swatch " + k[0]), document.createTextNode(k[1] + "  "));
    });
    box.appendChild(key);
    if (!data.automations.length) { box.appendChild(el("p", "muted", "No automations yet.")); showRunning(0); return; }

    // The hour ruler, every 3 hours.
    const grid = el("div", "tl");
    const ruler = el("div", "tl-lane tl-ruler");
    const marks = el("div", "tl-track");
    for (let h = 0; h <= 24; h += 3) {
      const tick = el("span", "tl-hour small muted", fmtClock(new Date(t0 + h * 3600000)));
      tick.style.left = (h / 24 * 100) + "%";
      marks.appendChild(tick);
    }
    if (now >= t0 && now <= t1) {
      const tag = el("span", "tl-now-tag small", "now " + fmtClock(new Date(now)));
      tag.style.left = pct(now) + "%";
      marks.appendChild(tag);
    }
    ruler.append(el("span"), marks);
    grid.appendChild(ruler);

    let running = 0;
    sorted(data.automations).forEach(function (a) {
      const going = a.runs.find(function (r) { return r.running; });  // one that's running right now, if any
      const lane = el("div", "tl-lane" + (a.enabled ? "" : " is-paused") + (going ? " is-running" : ""));
      const name = button("", "link tl-name", function () { showTab("automations"); open(a.id); });
      let sub = (a.enabled ? a.when : "paused") + (a.avg_seconds !== null ? " · ~" + fmtDuration(a.avg_seconds) : "");
      if (going) {                                                    // say it in words too: a 2-minute bar is a sliver
        const left = a.avg_seconds !== null ? a.avg_seconds - going.seconds : null;
        sub = "● Running " + fmtDuration(going.seconds) + (left === null ? "" : left > 0 ? " · ~" + fmtDuration(left) + " left"
                                                                                       : " · over its usual " + fmtDuration(a.avg_seconds));
      }
      name.append(el("span", null, a.name), el("span", "small " + (going ? "tl-live" : "muted"), sub));
      const track = el("div", "tl-track");
      for (let h = 3; h < 24; h += 3) {                               // faint hour lines
        const line = el("span", "tl-gridline");
        line.style.left = (h / 24 * 100) + "%";
        track.appendChild(line);
      }
      const avg = a.avg_seconds !== null ? a.avg_seconds * 1000 : null;

      a.runs.forEach(function (r) {                                   // what happened, and what's happening
        const start = Date.parse(r.start);
        const bar = el("button", "tl-run is-" + (r.running ? "running" : r.status));
        bar.type = "button";
        bar.addEventListener("click", function () { liveRun(r.run_id, a.name); });
        if (r.running) {
          running += 1;
          place(bar, start, now);
          bar.title = a.name + ": running since " + fmtClock(new Date(start)) + " (" + fmtDuration(r.seconds) + ")" +
            (avg === null ? "" : start + avg > now ? "; usually " + fmtDuration(a.avg_seconds) + ", so about " +
              fmtDuration((start + avg - now) / 1000) + " left" : "; longer than usual (" + fmtDuration(a.avg_seconds) + ")");
          if (avg !== null && start + avg > now) {                    // where it should end
            const expect = place(el("span", "tl-expect"), now, start + avg);
            expect.title = "Usually done by " + fmtClock(new Date(start + avg));
            track.appendChild(expect);
          }
        } else {
          place(bar, start, Date.parse(r.end));
          bar.title = a.name + ": " + (STATUS[r.status] || r.status) + ", " + fmtClock(new Date(start)) + "–" +
            fmtClock(new Date(Date.parse(r.end))) + " (" + fmtDuration(r.seconds) + ")" + (r.trigger !== "schedule" ? ", " + r.trigger : "");
        }
        track.appendChild(bar);
      });

      a.planned.forEach(function (when) {                             // what's coming, as wide as it usually takes
        const at = Date.parse(when);
        const slot = place(el("span", "tl-slot"), at, at + (avg || 0));
        slot.title = a.name + ": scheduled " + fmtClock(new Date(at)) + (avg === null ? " (no finished runs to time yet)"
          : ", usually takes " + fmtDuration(a.avg_seconds) + ", so done about " + fmtClock(new Date(at + avg)));
        track.appendChild(slot);
      });

      if (now >= t0 && now <= t1) {                                   // the "now" line through every lane
        const line = el("span", "tl-now");
        line.style.left = pct(now) + "%";
        track.appendChild(line);
      }
      lane.append(name, track);
      grid.appendChild(lane);
    });
    box.appendChild(grid);
    if (state.day === 0) showRunning(running);                        // today's view knows what's going right now

    // The same, in words: what each is doing now, and how long it takes.
    const table = el("table", "admin-table tl-table");
    const thead = el("tr");
    ["Automation", "Schedule", "Now", "Usually takes", "Longest", "Timed runs"].forEach(function (h) { thead.appendChild(el("th", null, h)); });
    table.appendChild(thead);
    sorted(data.automations).forEach(function (a) {
      const tr = el("tr");
      const going = a.runs.find(function (r) { return r.running; });
      let nowText;
      if (going) {
        const left = a.avg_seconds !== null ? a.avg_seconds - going.seconds : null;
        nowText = "Running for " + fmtDuration(going.seconds) + (left === null ? "" : left > 0 ? ", about " + fmtDuration(left) + " left" : ", longer than usual");
      } else if (!a.enabled) {
        nowText = "Paused" + (a.paused_reason ? ": " + a.paused_reason : "");
      } else if (a.next_run_at) {
        const next = new Date(Date.parse(a.next_run_at));
        nowText = "Next " + (next.toDateString() === new Date(now).toDateString() ? "" : next.toLocaleDateString([], { weekday: "short" }) + " ") + fmtClock(next);
      } else {
        nowText = "—";
      }
      [a.name, a.enabled ? a.when : "paused", nowText, fmtDuration(a.avg_seconds), fmtDuration(a.max_seconds),
       a.measured ? a.measured + " of the last " + data.sample : "none yet"].forEach(function (v, i) {
        tr.appendChild(el("td", i === 2 && going ? "tl-live" : null, v));
      });
      table.appendChild(tr);
    });
    box.appendChild(table);
    box.appendChild(el("p", "small muted", "A run's time is from when it was scheduled (or started, for Run now) until " +
      "its last message went out. “Usually takes” averages its last " + data.sample + " runs that finished well."));
  }

  document.querySelectorAll("#automator [role=tab]").forEach(function (t) {
    t.addEventListener("click", function () {
      showTab(t.dataset.tab);
      location.hash = t.dataset.tab === "timeline" ? "timeline" : (state.selected ? "a=" + state.selected : "");
    });
  });

  $("auto-new").addEventListener("click", function () { open("new"); });
  const hash = location.hash.slice(1);
  const wanted = /^a=(\d+)$/.exec(hash), run = /^run=(\d+)$/.exec(hash);
  if (hash === "approvals") {                                         // an old link to the removed Approvals tab
    location.replace("/proposals");
  } else if (hash === "timeline") {
    load();
    showTab("timeline");
  } else if (run) {
    api("GET", "/api/automations/runs/" + run[1]).then(function (r) {
      state.run = r.run_id;
      load(r.automation_id);
    }).catch(function () { load(); });
  } else {
    load(wanted ? parseInt(wanted[1], 10) : undefined);
  }
})();
