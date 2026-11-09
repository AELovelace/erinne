/* The install buttons on phones: at the bottom of the ☰ menu (_nav.html), and on the Remote's page when the
   menu sends someone there (media_panel.html). It works out whether this is a browser tab or an installed app.
   A browser only offers to install the page it is showing, so the Remote's button goes to the Remote's page
   (/media/panel#install) and finishes there. Loaded in <head>, so it doesn't miss the browser's offer.
   Nothing here uses innerHTML. */
(function () {
  "use strict";

  const phone = window.matchMedia("(max-width: 760px)");   // the width at which the ☰ menu appears (erynne.css)
  const inApp = window.matchMedia("(display-mode: standalone)").matches   // running as an installed app
    || window.matchMedia("(display-mode: fullscreen)").matches            // the same, with no status bar
    || window.navigator.standalone === true;                              // how iPhones say it
  const iPhone = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);   // iPads call themselves Macs
  const REMOTE_PAGE = "/media/panel#install";              // the Remote's page, showing its install button
  let offer = null;                                        // the browser's install prompt, once it gives us one

  function box() { return document.getElementById("install-box"); }       // the buttons on this page, if it has any

  function showsHere(b) {   // the Remote's page shows its button only when the menu sent someone there
    return Boolean(b) && (b.dataset.installOnly !== "hash" || location.hash === "#install");
  }

  /* The browser's offer. Saved for our button, which replaces its own banner wherever the button is showing. */
  window.addEventListener("beforeinstallprompt", function (event) {
    offer = event;                                         // kept until the button is tapped
    if (phone.matches && showsHere(box())) event.preventDefault();   // no banner as well as the button
  });

  function say(text) {   // shows a line of help under the buttons
    const hint = document.getElementById("install-hint");
    hint.textContent = text;
    hint.hidden = false;
  }

  function hideThisButton() {   // installed: this page's button has nothing left to do
    const here = document.querySelector('[data-install="this"]');
    if (here) here.hidden = true;
    const hint = document.getElementById("install-hint");
    if (hint) hint.hidden = true;
  }
  window.addEventListener("appinstalled", hideThisButton);

  /* Installs the app this page belongs to: the browser's own prompt if it gave us one, otherwise directions. */
  async function installThis() {
    if (offer) {
      const asked = offer;                                 // a prompt can only be used once
      offer = null;
      asked.prompt();                                      // the browser's install dialog
      const answer = await asked.userChoice;               // what the person chose in it
      if (answer.outcome === "accepted") hideThisButton();
      return;
    }
    say(iPhone ? "Tap Share, then Add to Home Screen."     // iPhones have no install prompt for a page to open
      : "Open your browser's ⋮ menu and choose Install app. If it isn't there, this app is already installed.");
  }

  /* From inside the installed app there is no browser to install with, so this hands over the address. */
  async function sendToBrowser() {
    const address = location.origin + REMOTE_PAGE;         // the full address, to paste into a browser
    try {
      await navigator.clipboard.writeText(address);
      say("Apps install from the browser. Link copied: paste it into Chrome or Safari.");
    } catch (err) {
      say("Apps install from the browser. Open this in Chrome or Safari: " + address);   // copying was refused
    }
  }

  function start() {   // puts the right buttons on show for this page and mode
    const b = box();
    if (!showsHere(b)) return;                             // nothing to offer on this page
    const here = b.querySelector('[data-install="this"]');
    const remote = b.querySelector('[data-install="remote"]');
    if (inApp && !remote) return;                          // the installed Remote: nothing else to offer
    if (here) {
      here.hidden = inApp;                                 // no offer to install the app you're already in
      here.addEventListener("click", installThis);
    }
    if (remote) remote.addEventListener("click", function () {
      if (inApp) sendToBrowser(); else location.href = REMOTE_PAGE;   // a browser tab can go straight there
    });
    b.hidden = false;
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
}());
