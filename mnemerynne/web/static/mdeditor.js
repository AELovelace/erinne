/* The Markdown editor (/edit/{id} and /edit/new): CodeMirror 5 on the left (a
   plain textarea when it doesn't load), the preview on the right rendered by
   the server (POST /api/markdown/preview: the same renderer as the file view).
   Saves send the version the text was loaded from; a 409 means someone saved
   the file meanwhile, and the bar offers both ways out. The preview's HTML
   comes from the server's renderer (raw HTML escaped), never from user text. */
(function () {
  "use strict";

  const { api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const main = $("editor");
  if (!main) return;

  let mode = main.dataset.mode;                 // "new" | "edit"
  let fileId = main.dataset.fileId ? Number(main.dataset.fileId) : null;
  let version = null, loadedText = "", dirty = false, saving = false;
  const status = $("ed-status"), preview = $("ed-preview"), saveButton = $("ed-save");

  function setStatus(text, kind) {
    status.textContent = text;
    status.className = "small push-right " + (kind || "muted");
  }

  function timeNow() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  // ------------------------------------------------------------- the editor
  const textarea = $("ed-source");
  let editor;
  if (window.CodeMirror && typeof window.CodeMirror.fromTextArea === "function") {
    editor = window.CodeMirror.fromTextArea(textarea, {
      mode: "markdown", lineNumbers: true, lineWrapping: true, viewportMargin: Infinity,
      extraKeys: { "Ctrl-S": function () { save(); }, "Cmd-S": function () { save(); } },
    });
    editor.on("change", onChange);
  } else {
    editor = {
      getValue: function () { return textarea.value; },
      setValue: function (v) { textarea.value = v; },
      focus: function () { textarea.focus(); },
    };
    textarea.addEventListener("input", onChange);
  }

  function onChange() {
    dirty = editor.getValue() !== loadedText;
    setStatus(dirty ? "Unsaved changes" : "Saved");
    schedulePreview();
  }

  // ------------------------------------------------------------- the preview
  let previewTimer = null, previewController = null;

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(renderPreview, 400);
  }

  async function renderPreview() {
    if (previewController) previewController.abort();
    previewController = new AbortController();
    try {
      const resp = await fetch("/api/markdown/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: editor.getValue() }), signal: previewController.signal,
      });
      if (!resp.ok) {
        if (resp.status === 429) schedulePreview();   // too fast: try again shortly
        return;
      }
      const data = await resp.json();
      const room = preview.scrollHeight - preview.clientHeight;
      const ratio = room > 0 ? preview.scrollTop / room : 0;
      preview.innerHTML = data.html;   // server-rendered Markdown (HTML in the source is escaped there)
      preview.scrollTop = ratio * (preview.scrollHeight - preview.clientHeight);
    } catch (e) {
      if (e.name !== "AbortError") setStatus("Preview unavailable: " + e.message, "error");
    }
  }

  // ------------------------------------------------------------ load, save
  async function load() {
    if (mode === "new") {
      loadedText = "";
      setStatus("New page: give it a name and press Save");
      renderPreview();
      if (!window.Erynne.coarse) ($("ed-name") || editor).focus();
      return;
    }
    try {
      const data = await api("GET", "/api/files/" + fileId + "/text");
      version = data.version;
      loadedText = data.content;
      editor.setValue(data.content);
      dirty = false;
      setStatus("Saved");
      renderPreview();
      if (!data.can_write) {
        saveButton.disabled = true;
        setStatus("You can only read this file here", "error");
      }
      if (!window.Erynne.coarse) editor.focus();
    } catch (e) {
      setStatus(e.message, "error");
      saveButton.disabled = true;
    }
  }

  function becameFile(data) {
    fileId = data.id;
    mode = "edit";
    main.dataset.mode = "edit";
    main.dataset.fileId = String(fileId);
    history.replaceState(null, "", "/edit/" + fileId + "?back=" + encodeURIComponent(main.dataset.back));
    document.title = data.name + " · Edit";
    const name = $("ed-name");
    if (name) {
      const title = document.createElement("strong");
      title.id = "ed-title";
      title.textContent = data.name;
      name.replaceWith(title);
    }
    const view = $("ed-view");
    view.href = data.view_url || "/files/" + fileId + "/view";
    view.hidden = false;
  }

  function tellOpener() {
    if (!window.opener || window.opener.closed) return;
    try { window.opener.postMessage({ type: "mnemerynne-saved", file_id: fileId }, location.origin); } catch (e) { /* gone */ }
  }

  async function save() {
    if (saving || saveButton.disabled) return;
    const text = editor.getValue();
    saving = true;
    saveButton.disabled = true;
    setStatus("Saving…");
    try {
      let data;
      if (mode === "new") {
        const nameInput = $("ed-name");
        const name = nameInput ? nameInput.value.trim() : "";
        if (!name) {
          setStatus("Give the page a name first", "error");
          if (nameInput) nameInput.focus();
          return;
        }
        data = await api("POST", "/api/files/text", {
          collection_id: main.dataset.collectionId ? Number(main.dataset.collectionId) : null,
          folder_id: main.dataset.folderId ? Number(main.dataset.folderId) : null,
          name: name, content: text,
        });
        becameFile(data);
        if (data.content !== undefined && data.content !== text) { editor.setValue(data.content); }
      } else {
        data = await api("PUT", "/api/files/" + fileId + "/text", { content: text, version: version });
      }
      version = data.version;
      loadedText = text;
      dirty = editor.getValue() !== text;
      $("ed-conflict").hidden = true;
      setStatus(dirty ? "Unsaved changes" : "Saved at " + timeNow(), dirty ? "muted" : "ok-note");
      tellOpener();
    } catch (e) {
      if (e.status === 409) showConflict(e.message);
      else setStatus(e.message, "error");
    } finally {
      saving = false;
      saveButton.disabled = false;
    }
  }

  // ---------------------------------------------------------------- conflicts
  function showConflict(message) {
    $("ed-conflict-text").textContent = message;
    $("ed-conflict").hidden = false;
    setStatus("Not saved", "error");
  }

  $("ed-conflict-theirs").addEventListener("click", async function () {
    const mine = editor.getValue();
    try { await navigator.clipboard.writeText(mine); } catch (e) { /* no clipboard: the text stays in the page */ }
    try {
      const data = await api("GET", "/api/files/" + fileId + "/text");
      version = data.version;
      loadedText = data.content;
      editor.setValue(data.content);
      dirty = false;
      $("ed-conflict").hidden = true;
      setStatus("Loaded their version; your text is on the clipboard", "muted");
      renderPreview();
    } catch (e) { setStatus(e.message, "error"); }
  });

  $("ed-conflict-mine").addEventListener("click", async function () {
    try {
      const data = await api("GET", "/api/files/" + fileId + "/text");
      version = data.version;    // theirs becomes a version under Versions when mine lands
      $("ed-conflict").hidden = true;
      await save();
    } catch (e) { setStatus(e.message, "error"); }
  });

  // ------------------------------------------------------------- the chrome
  // Phones show one pane at a time (erynne.css .ed-switch); a desktop shows both and ignores this.
  function showPane(preview) {
    document.body.classList.toggle("ed-preview", preview);
    $("ed-show-source").setAttribute("aria-pressed", String(!preview));
    $("ed-show-preview").setAttribute("aria-pressed", String(preview));
    if (!preview && editor.refresh) editor.refresh();   // CodeMirror measured itself while hidden
  }
  $("ed-show-source").addEventListener("click", function () { showPane(false); });
  $("ed-show-preview").addEventListener("click", function () { showPane(true); });

  saveButton.addEventListener("click", function () { save(); });
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      save();
    }
  });
  $("ed-close").addEventListener("click", function () {
    if (dirty && !confirm("You have unsaved changes. Close anyway?")) return;
    dirty = false;
    window.close();
    setTimeout(function () { location.href = main.dataset.back || "/files"; }, 150);   // not a popup: go back
  });
  window.addEventListener("beforeunload", function (e) {
    if (!dirty) return;
    e.preventDefault();
    e.returnValue = "";
  });

  load();
})();
