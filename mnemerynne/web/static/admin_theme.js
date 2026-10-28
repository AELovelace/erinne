/* Access → Settings → Themes (web/theme.py, mnemerynne/theme.py): the themes as cards (on, pickable,
   edit, copy, download, reset or delete), new themes from a base or a theme pack, the default and the
   enforced theme, the organisation's branding; and the editor: scene, palettes, gradients, shape and
   type, effects and custom CSS, previewed on this very page as you go. Nothing here uses innerHTML. */
(function () {
  "use strict";

  const { el, api, form, toast } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const box = $("theme-settings");
  if (!box) return;

  const TOKEN_LABELS = {
    page: "Page", primary: "Primary", primary_hover: "Primary, hover", text: "Text", muted: "Muted text",
    surface: "Surface", surface_strong: "Surface, strong", line: "Lines", highlight: "Highlight",
    highlight_soft: "Highlight, soft", error: "Error", ok_bg: "OK, background", ok_fg: "OK, text",
    bad_bg: "Bad, background", bad_fg: "Bad, text", busy_bg: "Busy, background", busy_fg: "Busy, text",
  };
  const ALPHA_TOKENS = ["surface", "surface_strong", "line", "highlight_soft", "ok_bg", "bad_bg", "busy_bg"];
  const NEON_LABELS = { cyan: "Second colour", fringe_l: "Fringe, left", fringe_r: "Fringe, right", bloom: "Glow" };
  const PLACES = [["page", "Page background"], ["bar", "Top bar"], ["button", "Buttons"]];
  const EFFECT_LABELS = { scanlines: "Scanlines", flicker: "Flicker", jitter: "Text jitter", pulse: "Pulse" };
  const BASE_NAMES = { sakura: "Sakura", neon: "Neon", plain: "Plain" };
  const LOOK_ATTRS = ["data-scene", "data-border", "data-flat", "data-texture", "data-fx", "data-page-bg", "data-bar-bg", "data-button-bg"];
  const html = document.documentElement;

  let data = null;          // the last answer from the server
  let editing = null;       // { theme, draft } while the editor is open
  let previewOn = true;
  let previewTimer = null, previewController = null;
  const pageLook = snapshotLook();   // what this page came with: put back when the preview ends

  // ------------------------------------------------------------- colours
  function parseColor(s) {
    s = String(s || "").trim();
    let m;
    if ((m = s.match(/^#([0-9a-f]{3})$/i))) {
      return { r: parseInt(m[1][0] + m[1][0], 16), g: parseInt(m[1][1] + m[1][1], 16), b: parseInt(m[1][2] + m[1][2], 16), a: 1 };
    }
    if ((m = s.match(/^#([0-9a-f]{6})([0-9a-f]{2})?$/i))) {
      return { r: parseInt(m[1].slice(0, 2), 16), g: parseInt(m[1].slice(2, 4), 16), b: parseInt(m[1].slice(4, 6), 16),
               a: m[2] ? parseInt(m[2], 16) / 255 : 1 };
    }
    if ((m = s.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/i))) {
      return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
    }
    return null;
  }
  function hex2(n) { return Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0"); }
  function toHex(c) { return "#" + hex2(c.r) + hex2(c.g) + hex2(c.b); }
  function colorString(c) {
    return c.a >= 1 ? toHex(c) : "rgba(" + Math.round(c.r) + ", " + Math.round(c.g) + ", " + Math.round(c.b) + ", " + (Math.round(c.a * 100) / 100) + ")";
  }
  function rgbToHsl(c) {
    const r = c.r / 255, g = c.g / 255, b = c.b / 255, max = Math.max(r, g, b), min = Math.min(r, g, b);
    let h = 0, s = 0; const l = (max + min) / 2;
    if (max !== min) {
      const d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      if (max === r) h = (g - b) / d + (g < b ? 6 : 0); else if (max === g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
      h /= 6;
    }
    return { h: h, s: s, l: l, a: c.a };
  }
  function hslToRgb(c) {
    const f = function (p, q, t) {
      if (t < 0) t += 1; if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t; if (t < 1 / 2) return q; if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    let r, g, b;
    if (c.s === 0) { r = g = b = c.l; } else {
      const q = c.l < 0.5 ? c.l * (1 + c.s) : c.l + c.s - c.l * c.s, p = 2 * c.l - q;
      r = f(p, q, c.h + 1 / 3); g = f(p, q, c.h); b = f(p, q, c.h - 1 / 3);
    }
    return { r: r * 255, g: g * 255, b: b * 255, a: c.a };
  }
  function shade(color, l, s, a) {
    const h = rgbToHsl(parseColor(color) || { r: 128, g: 128, b: 128, a: 1 });
    h.l = l; if (s !== undefined && s !== null) h.s = s; h.a = a === undefined ? 1 : a;
    return colorString(hslToRgb(h));
  }
  // The rest of a palette from its primary colour: hover, highlights, lines; text and page follow the mode.
  function derive(p, dark) {
    const c = parseColor(p.primary);
    if (!c) return;
    const h = rgbToHsl(c);
    p.primary_hover = shade(p.primary, dark ? Math.min(0.85, h.l + 0.12) : Math.max(0.12, h.l - 0.12));
    p.highlight = shade(p.primary, dark ? 0.28 : 0.86, Math.min(1, h.s));
    p.highlight_soft = shade(p.primary, dark ? 0.18 : 0.94, Math.min(1, h.s));
    p.line = shade(p.primary, h.l, h.s, 0.2);
    p.busy_bg = shade(p.primary, dark ? 0.2 : 0.93, h.s); p.busy_fg = shade(p.primary, dark ? 0.8 : 0.3, h.s);
    if (!dark) {
      p.page = p.page || "#f6f7fa"; p.text = p.text || "#1b2330"; p.muted = p.muted || "#5b6677";
      p.surface = "rgba(255, 255, 255, 0.82)"; p.surface_strong = "rgba(255, 255, 255, 0.94)";
    } else {
      p.page = p.page || shade(p.primary, 0.07, Math.min(0.6, h.s)); p.text = p.text || "#eef1f6"; p.muted = p.muted || "#a2abb8";
      p.surface = shade(p.primary, 0.12, Math.min(0.5, h.s), 0.82); p.surface_strong = shade(p.primary, 0.14, Math.min(0.5, h.s), 0.94);
    }
  }
  // Light to dark: each colour's lightness flipped, opacities kept.
  function flip(light) {
    const dark = JSON.parse(JSON.stringify(light));
    Object.keys(TOKEN_LABELS).forEach(function (k) {
      const c = parseColor(light[k]);
      if (!c) return;
      const h = rgbToHsl(c);
      h.l = 1 - h.l;
      dark[k] = colorString(hslToRgb(h));
    });
    return dark;
  }

  // -------------------------------------------------------- the look
  function snapshotLook() {
    const attrs = {};
    LOOK_ATTRS.forEach(function (a) { if (html.hasAttribute(a)) attrs[a] = html.getAttribute(a); });
    const link = $("look-css");
    return { base: html.dataset.theme, slug: html.dataset.look || "", attrs: attrs, css: link ? link.getAttribute("href") : "" };
  }
  function applyLook(look, css) {
    html.dataset.theme = look.base;
    if (look.slug) html.dataset.look = look.slug; else delete html.dataset.look;
    LOOK_ATTRS.forEach(function (a) { html.removeAttribute(a); });
    Object.keys(look.attrs || {}).forEach(function (a) { html.setAttribute(a, look.attrs[a]); });
    let link = $("look-css");
    if (look.css) {
      if (!link) { link = el("link"); link.rel = "stylesheet"; link.id = "look-css"; document.head.appendChild(link); }
      if (link.getAttribute("href") !== look.css) link.setAttribute("href", look.css);
    } else if (link) link.remove();
    let style = $("theme-draft");
    if (css !== undefined) {
      if (!style) { style = el("style"); style.id = "theme-draft"; document.head.appendChild(style); }
      style.textContent = css;
    } else if (style) style.remove();
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.content = look.base === "neon" ? "dark" : "light dark";
  }
  function endPreview() {
    clearTimeout(previewTimer);
    if (previewController) previewController.abort();
    applyLook(pageLook);
  }
  function schedulePreview() {
    if (!editing || !previewOn) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(runPreview, 300);
  }
  async function runPreview() {
    if (!editing || !previewOn) return;
    if (previewController) previewController.abort();
    previewController = new AbortController();
    try {
      const resp = await fetch("/api/admin/themes/preview", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: previewController.signal,
        body: JSON.stringify({ slug: editing.theme.slug, base: editing.theme.base, config: editing.draft }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(function () { return {}; });
        setNote(err.detail || ("Preview failed (" + resp.status + ")"), true);
        return;
      }
      const out = await resp.json();
      setNote("");
      // The page becomes the draft: its base, the slug (so the draft's rules match) and attributes; the
      // saved stylesheet comes off so the draft's inline one is the only voice.
      applyLook({ base: out.base, slug: editing.theme.slug, attrs: out.attrs, css: "" }, out.css);
    } catch (e) {
      if (e.name !== "AbortError") setNote("Preview failed: " + e.message, true);
    }
  }

  // --------------------------------------------------------------- bits
  let noteNode = null;
  function setNote(text, isError) {
    if (!noteNode) return;
    noteNode.textContent = text || "";
    noteNode.className = "th-note " + (isError ? "error" : "muted");
    noteNode.hidden = !text;
  }
  function fail(err) { toast(err.message || String(err), true); }
  function themeBySlug(slug) { return (data.themes || []).find(function (t) { return t.slug === slug; }); }
  function swatchFor(theme) {
    const eff = theme.effective || {};
    const p = eff.light || eff.dark || {};
    const sw = el("span", "theme-swatch look");
    sw.setAttribute("aria-hidden", "true");
    sw.style.setProperty("--sw-page", p.page || "#888");
    sw.style.setProperty("--sw-primary", p.primary || "#444");
    sw.style.setProperty("--sw-text", p.text || "#fff");
    sw.style.setProperty("--sw-surface", p.surface || "rgba(255,255,255,.5)");
    sw.style.setProperty("--sw-highlight", p.highlight || "transparent");
    sw.append(el("span"), el("span"), el("span"));
    return sw;
  }
  function badge(text) { const b = el("span", "badge", text); return b; }
  function fileInput(accept, onFile) {
    const input = el("input");
    input.type = "file"; input.accept = accept; input.hidden = true;
    input.addEventListener("change", function () { if (input.files[0]) onFile(input.files[0]); input.value = ""; });
    box.appendChild(input);
    return input;
  }
  async function upload(url, file) {
    const body = new FormData();
    body.append("file", file, file.name);
    const resp = await fetch(url, { method: "POST", body: body });
    const out = await resp.json().catch(function () { return {}; });
    if (!resp.ok) throw new Error(out.detail || ("Upload failed (" + resp.status + ")"));
    return out;
  }
  function assetBox(label, hint, current, previewUrl, accept, onUpload, onRemove, isFont) {
    const wrap = el("div", "th-asset");
    wrap.appendChild(el("strong", "small", label));
    const preview = el("div", "th-asset-preview");
    if (current && previewUrl && !isFont) { const img = el("img"); img.src = previewUrl; img.alt = ""; preview.appendChild(img); }
    else if (current) preview.appendChild(el("span", "muted", current));
    else preview.appendChild(el("span", "muted", hint));
    wrap.appendChild(preview);
    const actions = el("div", "th-asset-actions");
    const input = fileInput(accept, onUpload);
    actions.appendChild(form.button(current ? "Replace…" : "Upload…", "small", function () { input.click(); }));
    if (current) actions.appendChild(form.button("Remove", "link small", onRemove));
    wrap.appendChild(actions);
    return wrap;
  }

  // ----------------------------------------------------------- overview
  function renderOverview() {
    editing = null;
    box.replaceChildren();
    noteNode = null;
    box.appendChild(el("p", "muted", "Every look the box can wear. Sakura and Neon are the two bundled looks (tweak them, or " +
      "reset them to stock); Corporate and Sad Girls Club are ready-made themes that are off until you turn them on; " +
      "your own themes start from any of the three bases. People pick from the themes that are on under Preferences, " +
      "unless one theme is set for everyone."));

    const cards = el("div", "th-cards");
    data.themes.forEach(function (t) {
      const card = el("div", "th-card" + (t.slug === data.settings.default ? " current" : ""));
      card.dataset.slug = t.slug;
      card.appendChild(swatchFor(t));
      const name = el("div", "th-card-name", t.name);
      if (t.builtin) name.appendChild(badge("built-in"));
      if (t.slug === data.settings.default) name.appendChild(badge("default"));
      if (t.slug === data.settings.enforce) name.appendChild(badge("everyone"));
      if (!t.enabled) name.appendChild(badge("off"));
      else if (!t.pickable) name.appendChild(badge("hidden"));
      card.appendChild(name);
      card.appendChild(el("div", "small muted", BASE_NAMES[t.base] + " base" + (t.has_css ? " · changed" : " · stock")));
      const switches = el("div", "th-switches");
      switches.appendChild(form.check("On", t.enabled, function (v) { save("PUT", "/api/admin/themes/" + t.slug, { enabled: v }); }));
      switches.appendChild(form.check("People may pick it", t.pickable, function (v) { save("PUT", "/api/admin/themes/" + t.slug, { pickable: v }); }));
      card.appendChild(switches);
      const actions = el("div", "th-actions");
      actions.appendChild(form.button("Edit", "small", function () { openEditor(t.slug); }));
      actions.appendChild(form.button("Duplicate", "link small", async function () {
        const name = prompt("Name for the copy", t.name + " copy");
        if (name === null) return;
        try { const out = await api("POST", "/api/admin/themes/" + t.slug + "/duplicate", { name: name }); data = out; openEditor(out.slug); }
        catch (e) { fail(e); }
      }));
      const dl = el("a", "button-like soft small", "Download");
      dl.href = "/api/admin/themes/" + t.slug + "/pack"; dl.download = t.slug + ".theme.json";
      actions.appendChild(dl);
      if (t.builtin) {
        if (t.has_css || t.slug === "corporate" || t.slug === "sadgirlsclub") {
          actions.appendChild(form.button("Reset to stock", "link small", function () {
            if (!confirm("Put " + t.name + " back to its stock look? Its changes are lost.")) return;
            save("POST", "/api/admin/themes/" + t.slug + "/reset", {});
          }));
        }
      } else {
        actions.appendChild(form.button("Delete", "link small danger", function () {
          if (!confirm("Delete the theme " + t.name + "? People using it get the default.")) return;
          save("DELETE", "/api/admin/themes/" + t.slug);
        }));
      }
      card.appendChild(actions);
      cards.appendChild(card);
    });
    box.appendChild(cards);

    // New themes.
    const fresh = el("div", "th-new");
    fresh.appendChild(el("span", "small", "New theme from:"));
    data.bases.forEach(function (b) {
      fresh.appendChild(form.button(BASE_NAMES[b], "small", async function () {
        const name = prompt("Name for the new theme (" + BASE_NAMES[b] + " base)", "");
        if (!name) return;
        try { const out = await api("POST", "/api/admin/themes", { name: name, base: b }); data = out; openEditor(out.slug); }
        catch (e) { fail(e); }
      }));
    });
    const packInput = fileInput(".json,application/json", async function (file) {
      try { const out = await upload("/api/admin/themes/import", file); data = out; toast("Theme imported."); openEditor(out.slug); }
      catch (e) { fail(e); }
    });
    fresh.appendChild(form.button("Upload a theme pack…", "small", function () { packInput.click(); }, "A .theme.json downloaded from another box"));
    box.appendChild(fresh);

    // Default and enforced.
    const rows = el("div", "th-rows");
    const on = data.themes.filter(function (t) { return t.enabled; }).map(function (t) { return [t.slug, t.name]; });
    rows.appendChild(el("span", "small", "Default theme"));
    const def = el("div");
    def.appendChild(form.select(on, data.settings.default, function (v) { save("PUT", "/api/admin/themes/settings", { default: v }); }));
    def.appendChild(el("span", "small muted", " New accounts and the sign-in page."));
    rows.appendChild(def);
    rows.appendChild(el("span", "small", "Who gets it"));
    const who = el("div", "th-switches");
    const choose = el("label", "check small"), everyone = el("label", "check small");
    const r1 = el("input"), r2 = el("input");
    r1.type = r2.type = "radio"; r1.name = r2.name = "th-who";
    r1.checked = !data.settings.enforce; r2.checked = !!data.settings.enforce;
    choose.append(r1, document.createTextNode(" People choose from the themes that are on"));
    const enforceSel = form.select(on, data.settings.enforce || data.settings.default, null);
    everyone.append(r2, document.createTextNode(" Everyone gets "));
    everyone.appendChild(enforceSel);
    r1.addEventListener("change", function () { if (r1.checked) save("PUT", "/api/admin/themes/settings", { enforce: "" }); });
    r2.addEventListener("change", function () { if (r2.checked) save("PUT", "/api/admin/themes/settings", { enforce: enforceSel.value }); });
    enforceSel.addEventListener("change", function () { if (r2.checked) save("PUT", "/api/admin/themes/settings", { enforce: enforceSel.value }); });
    who.append(choose, everyone);
    rows.appendChild(who);
    box.appendChild(rows);

    // Branding.
    box.appendChild(el("h3", null, "Branding"));
    box.appendChild(el("p", "muted small", "The organisation's name, logo and mark, on every theme: the top bar, the sign-in page, " +
      "the browser tab and the home-screen icon. The assistant is still called " + (window.ErynneAssistantName || "Erynne") + " in chat."));
    const brand = data.brand;
    const brows = el("div", "th-rows");
    const nameIn = form.input("text", brand.name, null, { maxlength: 60, placeholder: "Erynne" });
    const tagIn = form.input("text", brand.tagline, null, { maxlength: 120, placeholder: "Ask questions about your documents." });
    const heightIn = form.input("range", brand.logo_height, null, { min: 1, max: 4, step: 0.1 });
    const saveBrand = form.button("Save", "small", function () {
      save("PUT", "/api/admin/brand", { name: nameIn.value, tagline: tagIn.value, logo_height: Number(heightIn.value) });
    });
    brows.appendChild(el("label", "small", "Product name")); brows.appendChild(nameIn);
    brows.appendChild(el("label", "small", "Sign-in tagline")); brows.appendChild(tagIn);
    brows.appendChild(el("label", "small", "Logo height"));
    const hwrap = el("div", "th-switches"); hwrap.append(heightIn, el("span", "small muted", "1 to 4 rem in the top bar"));
    brows.appendChild(hwrap);
    brows.appendChild(el("span")); brows.appendChild(saveBrand);
    box.appendChild(brows);
    const assets = el("div", "th-assets");
    [["logo", "Logo", "PNG, JPEG, WebP or SVG, up to 2 MB"], ["logo_dark", "Logo for dark surfaces", "Optional: used on dark themes"],
     ["mark", "Mark", "A square PNG: favicon and home-screen icon"]].forEach(function (a) {
      const kind = a[0];
      assets.appendChild(assetBox(a[1], a[2], brand.assets[kind], brand[kind], kind === "mark" ? "image/png,image/jpeg,image/webp" : "image/*,.svg",
        async function (file) { try { data = await upload("/api/admin/brand/assets/" + kind, file); renderOverview(); toast("Uploaded."); } catch (e) { fail(e); } },
        function () { save("DELETE", "/api/admin/brand/assets/" + kind); }));
    });
    box.appendChild(assets);
  }

  async function save(method, url, body) {
    try {
      data = await api(method, url, body);
      if (editing) {
        const fresh = themeBySlug(editing.theme.slug);
        if (fresh) { editing.theme = fresh; editing.draft.assets = fresh.effective.assets || {}; renderEditor(); }
      } else renderOverview();
    } catch (e) { fail(e); if (!editing) renderOverview(); }
  }

  // ------------------------------------------------------------- editor
  function openEditor(slug) {
    const theme = themeBySlug(slug);
    if (!theme) return;
    editing = { theme: theme, draft: JSON.parse(JSON.stringify(theme.effective)), name: theme.name };
    renderEditor();
    schedulePreview();
  }

  function renderEditor() {
    const t = editing.theme, d = editing.draft;
    const dark = t.base === "neon";
    box.replaceChildren();
    const wrap = el("div", "th-editor");

    const head = el("div", "th-head");
    head.appendChild(form.button("‹ Back", "link small", function () { endPreview(); renderOverview(); }));
    const nameIn = form.input("text", editing.name, function (v) { editing.name = v; }, { maxlength: 60, "aria-label": "Theme name" });
    head.appendChild(nameIn);
    head.appendChild(el("span", "small muted", BASE_NAMES[t.base] + " base" + (t.builtin ? " · built-in" : "")));
    head.appendChild(form.check("Preview on this page", previewOn, function (v) {
      previewOn = v;
      if (v) runPreview(); else endPreview();
    }));
    const right = el("div", "push-right th-switches");
    right.appendChild(form.button("Revert", "link small", function () { openEditor(t.slug); toast("Back to the saved look."); }));
    right.appendChild(form.button("Save", "small", async function () {
      try {
        data = await api("PUT", "/api/admin/themes/" + t.slug, { name: editing.name, config: editing.draft });
        const fresh = themeBySlug(t.slug);
        endPreview();
        if (fresh && pageLook.slug === t.slug) {   // this page wears the theme just saved: fetch its new stylesheet
          const look = { base: fresh.base, slug: t.slug, attrs: {}, css: fresh.has_css ? "/theme/" + t.slug + ".css?v=" + fresh.version : "" };
          applyLook(look);
          pageLook.css = look.css;
          location.reload();
          return;
        }
        toast("Saved.");
        editing = null;
        renderOverview();
      } catch (e) { fail(e); }
    }));
    head.appendChild(right);
    wrap.appendChild(head);
    noteNode = el("p", "th-note muted"); noteNode.hidden = true;
    wrap.appendChild(noteNode);

    // Scene.
    const scene = section(wrap, "Scene", dark ? "What is behind the pages: Neon's drifting cities, nothing, or a picture of your own."
      : (t.base === "sakura" ? "What is behind the pages: Sakura's blossoms over Mt Fuji, nothing, or a picture of your own." : "Nothing behind the pages, or a picture of your own."));
    const sceneRow = el("div", "th-shape");
    sceneRow.appendChild(form.field("Scene", form.select([["base", t.base === "plain" ? "Plain page" : "The base's own"], ["none", "None (page colour and gradient)"], ["image", "Background picture"]],
      d.scene || "base", function (v) { d.scene = v; schedulePreview(); })));
    sceneRow.appendChild(assetBox("Background picture", "JPEG, PNG or WebP up to 8 MB; shown with the scene set to it", d.assets && d.assets.background,
      d.assets && d.assets.background ? "/theme/" + t.slug + "/background?v=" + t.version : null, "image/*",
      function (file) { assetUpload("background", file); }, function () { assetRemove("background"); }));
    scene.appendChild(sceneRow);

    // Palettes.
    const pal = section(wrap, "Palette", dark ? "Neon is always dark: one palette." : "Two palettes: light, and dark for devices set to dark mode.");
    const cols = el("div", "th-palettes");
    (dark ? [["dark", "Colours"]] : [["light", "Light"], ["dark", "Dark"]]).forEach(function (pair) {
      const key = pair[0];
      d[key] = d[key] || {};
      const col = el("div", "th-palette");
      const h = el("h4", null, pair[1]);
      col.appendChild(h);
      const tools = el("div", "th-switches");
      tools.appendChild(form.button("Derive the rest from Primary", "link small", function () { derive(d[key], key === "dark"); renderEditor(); schedulePreview(); }));
      if (key === "dark" && !dark) tools.appendChild(form.button("Copy light → dark", "link small", function () { d.dark = flip(d.light || {}); renderEditor(); schedulePreview(); }));
      col.appendChild(tools);
      Object.keys(TOKEN_LABELS).forEach(function (token) { col.appendChild(tokenRow(d[key], token, TOKEN_LABELS[token], ALPHA_TOKENS.indexOf(token) >= 0)); });
      const tint = form.input("range", d[key].tint === undefined || d[key].tint === null ? 0.35 : d[key].tint, function (v) { d[key].tint = Number(v); schedulePreview(); }, { min: 0, max: 1, step: 0.05 });
      col.appendChild(form.field("Tint over the background picture", tint));
      cols.appendChild(col);
    });
    pal.appendChild(cols);
    if (dark) {
      d.neon = d.neon || {};
      const extra = el("div", "th-palette");
      extra.appendChild(el("h4", null, "Neon extras"));
      Object.keys(NEON_LABELS).forEach(function (k) { extra.appendChild(tokenRow(d.neon, k, NEON_LABELS[k], k.indexOf("fringe") === 0)); });
      pal.appendChild(extra);
    }

    // Gradients.
    const grads = section(wrap, "Gradients", "Solid colour, or a gradient of two to four stops, for the page, the top bar and the buttons" + (dark ? "." : ", per palette."));
    (dark ? ["dark"] : ["light", "dark"]).forEach(function (key) {
      if (!dark) grads.appendChild(el("h4", null, key === "light" ? "Light" : "Dark"));
      const row = el("div", "th-gradients");
      PLACES.forEach(function (pl) { row.appendChild(gradientBox(d[key], pl[0], pl[1])); });
      grads.appendChild(row);
    });

    // Shape and type.
    const shape = section(wrap, "Shape and type", "");
    const grid = el("div", "th-shape");
    grid.appendChild(form.field("Corner radius: " + (d.radius === undefined ? "" : d.radius) + " px", form.input("range", d.radius === undefined || d.radius === null ? 10 : d.radius, function (v) { d.radius = Number(v); schedulePreview(); }, { min: 0, max: 20, step: 1 })));
    grid.appendChild(form.field("Surfaces", form.select([["glass", "Frosted glass"], ["flat", "Flat"]], d.glass === false ? "flat" : "glass", function (v) { d.glass = v === "glass"; schedulePreview(); })));
    grid.appendChild(form.field("Borders", form.select([["soft", "Soft (the base's)"], ["hard", "Hard, 2 px"], ["ridge", "Ridge"]], d.border || "soft", function (v) { d.border = v; schedulePreview(); })));
    const fontOptions = data.fonts.map(function (f) { return [f.key, f.name + (f.bundled ? " (bundled)" : "")]; }).concat([["upload", "Uploaded font"]]);
    d.fonts = d.fonts || {};
    grid.appendChild(form.field("Heading font", form.select(fontOptions, d.fonts.heading || "system-sans", function (v) { d.fonts.heading = v; schedulePreview(); })));
    grid.appendChild(form.field("Body font", form.select(fontOptions, d.fonts.body || "system-sans", function (v) { d.fonts.body = v; schedulePreview(); })));
    shape.appendChild(grid);
    const files = el("div", "th-assets");
    files.appendChild(assetBox("Panel texture", "A small PNG tile drawn under panels and the top bar", (d.assets && d.assets.texture) || (d.texture_bundled ? "bundled" : null),
      d.assets && d.assets.texture ? "/theme/" + t.slug + "/texture?v=" + t.version : (d.texture_bundled ? "/static/" + d.texture_bundled : null), "image/png,image/jpeg,image/webp",
      function (file) { assetUpload("texture", file); }, function () { assetRemove("texture"); }));
    files.appendChild(assetBox("Heading font file", ".woff2 up to 1 MB; pick “Uploaded font” above", d.assets && d.assets.font_heading, null, ".woff2,.woff",
      function (file) { assetUpload("font_heading", file); }, function () { assetRemove("font_heading"); }, true));
    files.appendChild(assetBox("Body font file", ".woff2 up to 1 MB; pick “Uploaded font” above", d.assets && d.assets.font_body, null, ".woff2,.woff",
      function (file) { assetUpload("font_body", file); }, function () { assetRemove("font_body"); }, true));
    shape.appendChild(files);

    // Effects.
    const fx = section(wrap, "Effects", "An old monitor's tricks over every page. They stop for people who asked their device for reduced motion.");
    const fxRow = el("div", "th-effects");
    d.effects = d.effects || {};
    Object.keys(EFFECT_LABELS).forEach(function (k) {
      fxRow.appendChild(form.check(EFFECT_LABELS[k], !!d.effects[k], function (v) { d.effects[k] = v; renderEditor(); schedulePreview(); }));
    });
    fxRow.appendChild(form.field("Intensity", form.input("range", d.effects.intensity === undefined ? 0.6 : d.effects.intensity, function (v) { d.effects.intensity = Number(v); schedulePreview(); }, { min: 0, max: 1, step: 0.05 })));
    if (d.effects.pulse) {
      fxRow.appendChild(tokenRow(d.effects, "pulse_a", "Pulse from", false, d[dark ? "dark" : "light"].page));
      fxRow.appendChild(tokenRow(d.effects, "pulse_b", "Pulse to", false, d[dark ? "dark" : "light"].page));
    }
    fx.appendChild(fxRow);

    // Custom CSS.
    const css = section(wrap, "Advanced CSS", "Appended last, for what the controls don't cover. It is served as part of the theme's stylesheet, so treat it like configuration: no @import.");
    const ta = el("textarea", "th-css");
    ta.value = d.custom_css || "";
    ta.maxLength = 20480;
    ta.spellcheck = false;
    ta.addEventListener("input", function () { d.custom_css = ta.value; schedulePreview(); });
    css.appendChild(ta);

    box.appendChild(wrap);
  }

  function section(parent, title, hint) {
    const s = el("section", "th-section");
    s.appendChild(el("h3", null, title));
    if (hint) s.appendChild(el("p", "muted small", hint));
    parent.appendChild(s);
    return s;
  }

  // One colour: a picker, the text, and for see-through tokens an opacity slider.
  function tokenRow(target, key, label, alpha, fallback) {
    const row = el("div", "th-token" + (alpha ? " alpha" : ""));
    row.appendChild(el("span", "small", label));
    const current = parseColor(target[key] || fallback || "#888888") || { r: 136, g: 136, b: 136, a: 1 };
    const pick = el("input"); pick.type = "color"; pick.value = toHex(current); pick.setAttribute("aria-label", label);
    const text = el("input"); text.type = "text"; text.value = target[key] || ""; text.placeholder = fallback || "#rrggbb"; text.setAttribute("aria-label", label + " value");
    let range = null;
    function set(c) { target[key] = colorString(c); text.value = target[key]; pick.value = toHex(c); schedulePreview(); }
    pick.addEventListener("input", function () { const c = parseColor(pick.value); c.a = alpha && range ? Number(range.value) : (parseColor(text.value) || c).a; if (!alpha) c.a = 1; set(c); });
    text.addEventListener("change", function () {
      const c = parseColor(text.value);
      if (!c) { text.value = target[key] || ""; return; }
      if (range) range.value = c.a;
      set(c);
    });
    row.append(pick, text);
    if (alpha) {
      range = el("input"); range.type = "range"; range.min = 0; range.max = 1; range.step = 0.02; range.value = current.a; range.setAttribute("aria-label", label + " opacity");
      range.addEventListener("input", function () { const c = parseColor(text.value) || parseColor(pick.value); c.a = Number(range.value); set(c); });
      row.appendChild(range);
    }
    return row;
  }

  function gradientBox(palette, place, label) {
    palette.gradients = palette.gradients || {};
    const g = palette.gradients[place];
    const wrap = el("div", "th-gradient");
    wrap.appendChild(el("strong", "small", label));
    const swatch = el("div", "th-gradient-swatch");
    swatch.style.background = g ? gradientCss(g) : (palette[place === "page" ? "page" : "primary"] || "transparent");
    wrap.appendChild(swatch);
    const row = el("div", "th-gradient-row");
    row.appendChild(form.select([["solid", "Solid"], ["gradient", "Gradient"]], g ? "gradient" : "solid", function (v) {
      palette.gradients[place] = v === "gradient"
        ? { angle: 160, stops: [{ color: palette[place === "page" ? "page" : "primary"] || "#888888", at: 0 }, { color: palette.primary_hover || "#444444", at: 100 }] }
        : null;
      renderEditor(); schedulePreview();
    }));
    if (g) {
      const angle = form.input("range", g.angle, function (v) { g.angle = Number(v); swatch.style.background = gradientCss(g); schedulePreview(); }, { min: 0, max: 360, step: 5, "aria-label": "Angle" });
      row.appendChild(angle);
      row.appendChild(el("span", "small muted", "angle"));
    }
    wrap.appendChild(row);
    if (g) {
      g.stops.forEach(function (stop, i) {
        const s = el("div", "th-stop");
        const pick = el("input"); pick.type = "color"; pick.value = toHex(parseColor(stop.color) || { r: 136, g: 136, b: 136 });
        pick.addEventListener("input", function () { stop.color = pick.value; swatch.style.background = gradientCss(g); schedulePreview(); });
        const at = el("input"); at.type = "range"; at.min = 0; at.max = 100; at.step = 1; at.value = stop.at; at.setAttribute("aria-label", "Stop position");
        at.addEventListener("input", function () { stop.at = Number(at.value); pct.textContent = stop.at + "%"; swatch.style.background = gradientCss(g); schedulePreview(); });
        const pct = el("span", "small muted", stop.at + "%");
        const remove = form.button("✕", "link small", function () { g.stops.splice(i, 1); renderEditor(); schedulePreview(); }, "Remove this stop");
        remove.disabled = g.stops.length <= 2;
        s.append(pick, at, pct, remove);
        wrap.appendChild(s);
      });
      if (g.stops.length < 4) {
        wrap.appendChild(form.button("Add a stop", "link small", function () {
          const last = g.stops[g.stops.length - 1];
          g.stops.push({ color: last.color, at: Math.min(100, last.at + 10) });
          renderEditor(); schedulePreview();
        }));
      }
    }
    return wrap;
  }
  function gradientCss(g) {
    return "linear-gradient(" + g.angle + "deg, " + g.stops.map(function (s) { return s.color + " " + s.at + "%"; }).join(", ") + ")";
  }

  async function assetUpload(kind, file) {
    try {
      data = await upload("/api/admin/themes/" + editing.theme.slug + "/assets/" + kind, file);
      const fresh = themeBySlug(editing.theme.slug);
      editing.theme = fresh;
      editing.draft.assets = fresh.effective.assets || {};
      if (kind === "texture") delete editing.draft.texture_bundled;
      if (kind === "font_heading") editing.draft.fonts = Object.assign({}, editing.draft.fonts, { heading: "upload" });
      if (kind === "font_body") editing.draft.fonts = Object.assign({}, editing.draft.fonts, { body: "upload" });
      if (kind === "background" && editing.draft.scene !== "image") editing.draft.scene = "image";
      renderEditor(); schedulePreview(); toast("Uploaded.");
    } catch (e) { fail(e); }
  }
  async function assetRemove(kind) {
    try {
      data = await api("DELETE", "/api/admin/themes/" + editing.theme.slug + "/assets/" + kind);
      const fresh = themeBySlug(editing.theme.slug);
      editing.theme = fresh;
      editing.draft.assets = fresh.effective.assets || {};
      if (kind === "texture") delete editing.draft.texture_bundled;
      renderEditor(); schedulePreview();
    } catch (e) { fail(e); }
  }

  // --------------------------------------------------------------- load
  async function load() {
    try {
      data = await api("GET", "/api/admin/themes");
      if (editing) { const fresh = themeBySlug(editing.theme.slug); if (fresh) { editing.theme = fresh; renderEditor(); return; } }
      renderOverview();
    } catch (e) {
      box.replaceChildren(el("p", "error", e.message));
    }
  }

  window.ErynneTheme = { load: load };
  const panel = box.closest("[data-panel]");
  if (panel && !panel.hidden) load();
})();
