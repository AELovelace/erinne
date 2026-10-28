/* The top bar on every page: measures how much of the screen the bar and the announcement cards
   take (--chrome, which the full-height pages subtract from the viewport, phone or not), runs the
   ☰ menu on phones (the tabs, the username, Help and Sign out), and closes the
   collapsible side lists (details.ax-side) on narrow screens. Nothing here uses innerHTML. */
(function () {
  "use strict";

  const root = document.documentElement;
  const bar = document.querySelector(".topbar");
  const strip = document.querySelector(".tv-strip");   // a screen box's small bar (base.html)
  const notices = document.getElementById("notices");
  const narrow = window.matchMedia("(max-width: 760px)");

  // --chrome: the bar's height plus the notices block (0 when hidden), as the layout sees it.
  function measure() {
    let px = 0;
    if (bar) px += bar.getBoundingClientRect().height;
    if (strip) {                                         // 0 where the full top bar hides it
      const h = strip.getBoundingClientRect().height;
      px += h;
      root.style.setProperty("--tv-strip", Math.ceil(h) + "px");   // a page's own bar sticks under it
    }
    if (notices && !notices.hidden) px += notices.getBoundingClientRect().height + 8;   // its top margin
    root.style.setProperty("--chrome", Math.ceil(px) + "px");
  }
  if ((bar || strip) && "ResizeObserver" in window) {
    const watch = new ResizeObserver(measure);
    if (bar) watch.observe(bar);
    if (strip) watch.observe(strip);
    if (notices) watch.observe(notices);
  }
  measure();
  window.addEventListener("load", measure);
  if (notices) new MutationObserver(measure).observe(notices, { attributes: true, attributeFilter: ["hidden"], childList: true });

  // ☰: the tabs, the username, Help and Sign out, in a menu on phones (inline on a desktop).
  const menuButton = document.getElementById("nav-menu");
  const more = document.getElementById("nav-more");
  if (menuButton && more) {
    function setOpen(open) {
      more.classList.toggle("open", open);
      menuButton.setAttribute("aria-expanded", String(open));
    }
    menuButton.addEventListener("click", function (e) {
      e.stopPropagation();
      setOpen(!more.classList.contains("open"));
    });
    document.addEventListener("click", function (e) {
      if (more.classList.contains("open") && !more.contains(e.target)) setOpen(false);
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
    narrow.addEventListener("change", function () { setOpen(false); });
  }

  // Signed in on a screen box's TV (its top bar): untouched for KIOSK_IDLE_MS, back to the TV page, which
  // signs out (web/media.py screen_page), so nobody is left signed in on a TV people walk past.
  const KIOSK_IDLE_MS = 10 * 60 * 1000;
  const kioskBack = document.querySelector("[data-kiosk-back]");
  if (kioskBack) {
    let idle = null;
    const awake = function () {
      clearTimeout(idle);
      idle = setTimeout(function () { location.href = kioskBack.href; }, KIOSK_IDLE_MS);
    };
    ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"].forEach(function (name) {
      window.addEventListener(name, awake, { capture: true, passive: true });
    });
    awake();
  }

  // A screen box's ‹ › (the small bar, or the top bar on the TV): the kiosk has no browser buttons.
  document.querySelectorAll("[data-tv-go]").forEach(function (b) {
    b.addEventListener("click", function () { if (b.dataset.tvGo === "back") history.back(); else history.forward(); });
  });

  // Side lists (wiki pages, help contents, Access trees) start closed on a phone; a desktop shows them open.
  function foldSides() {
    document.querySelectorAll("details.ax-side").forEach(function (d) { d.open = !narrow.matches; });
  }
  foldSides();
  narrow.addEventListener("change", foldSides);
})();
