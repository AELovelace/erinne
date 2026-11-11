/* The service worker: it receives push notifications for the installed app (mnemerynne/push.py) and nothing else.
   There is no fetch handler on purpose, so it can never change how a page loads or serve an old copy of one.

   A push only says what kind of news it is. The words shown are the fixed ones below: whatever else a message
   carries (a title, a body, a link) is ignored, so a notification can never show text or open an address that
   didn't ship with this file. */
"use strict";

const LUMI = { // Lumi Pet's reminders (mnemerynne/lumipet.py sweep): one per need, at most one every few hours.
  hunger: "Lumi's tummy is rumbling. A snack would make her day.",
  energy: "Lumi is getting very sleepy. A nap would help.",
  joy: "Lumi misses you. Come and play for a minute?",
  nap: "Lumi is up from her nap and looking for you.",
  garden: "Lumi's flowers are ready to trim.",
};
const LUMI_PAGE = "/media/games/lumi-pet", LUMI_GARDEN = "/media/games/lumi-garden"; // Her needs open Lumi Pet; the garden reminder opens Lumi's Garden, an app of its own.
const LUMI_OLD_GARDEN = LUMI_PAGE + "#garden"; // A reminder shown before the garden moved out of Lumi Pet: her page sends that on to the garden.

self.addEventListener("install", function () { self.skipWaiting(); }); // A newer copy of this file takes over at once.
self.addEventListener("activate", function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener("push", function (event) { // Show the fixed words for a known kind; ignore anything else.
  let data = {};
  try { data = event.data.json(); } catch (_) { /* not ours */ }
  if (!data || data.kind !== "lumi-pet" || !Object.prototype.hasOwnProperty.call(LUMI, data.need)) return;
  event.waitUntil(self.registration.showNotification("Lumi", {
    body: LUMI[data.need], tag: "lumi-pet", renotify: true, // One Lumi notification at a time: a newer one replaces it.
    icon: "/static/icons/icon-192.png", badge: "/static/icons/icon-192.png", data: { page: data.need === "garden" ? LUMI_GARDEN : LUMI_PAGE },
  }));
});

self.addEventListener("notificationclick", function (event) { // Tapping it opens Lumi, in the app if it's already open.
  event.notification.close();
  const asked = event.notification.data && event.notification.data.page, page = asked === LUMI_PAGE || asked === LUMI_GARDEN || asked === LUMI_OLD_GARDEN ? asked : "/"; // Only an address that shipped with this file.
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (windows) {
    const open = windows.find(function (client) { return new URL(client.url).pathname === page.split("#")[0]; });
    return open ? open.focus() : self.clients.openWindow(page);
  }));
});
