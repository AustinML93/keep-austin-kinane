/*
 * Service worker: shell cache + push.
 *
 * The important part is notificationclick. Android web push supports action
 * buttons, so a tier-1 alert carries GOT 'EM / CAN'T MAKE IT on the lock screen
 * and acknowledging never requires opening the app. That directly attacks the
 * real failure: a notification that arrived correctly and got swiped away at a
 * red light.
 *
 * Service workers can't read localStorage, so the auth token rides inside the
 * encrypted push payload.
 *
 * ⚠️ Bump CACHE on every shell change, and bump ?v=N in index.html to match.
 *    See CLAUDE.md — Cloudflare's 4h edge cache will otherwise serve the old one.
 */

const CACHE = "kak-v7";
const SHELL = [
  "/",
  "/index.html",
  "/css/styles.css?v=7",
  "/js/app.js?v=7",
  "/manifest.webmanifest?v=7",
  "/icons/icon-192.png?v=7",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  // Never cache the API — stale show data is exactly the lie this app exists
  // to avoid telling.
  if (url.pathname.startsWith("/api/")) return;

  // NETWORK-FIRST FOR THE PAGE ITSELF.
  //
  // A cache-first shell pins whatever index.html you first loaded, and with it
  // the manifest URL that index.html points at. That's how a phone ends up
  // stuck on a version with no icons, unable to install, while the server has
  // been serving the fix for an hour. The page is small; fetch it fresh when
  // there's a network and fall back to cache only when there isn't.
  if (e.request.mode === "navigate") {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
          return res;
        })
        .catch(() => caches.match(e.request).then((hit) => hit || caches.match("/")))
    );
    return;
  }

  // Versioned assets (?v=N) are immutable by convention, so cache-first is safe.
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});

self.addEventListener("push", (e) => {
  let p = {};
  try { p = e.data ? e.data.json() : {}; } catch (_) { p = {}; }

  e.waitUntil(
    self.registration.showNotification(p.title || "Kinane", {
      body: p.body || "",
      tag: p.tag,
      renotify: p.renotify !== false,
      requireInteraction: !!p.requireInteraction,
      actions: p.actions || [],
      data: p.data || {},
      badge: "/icons/badge.png",
      icon: "/icons/icon-192.png",
      vibrate: p.data && p.data.tier === 1 ? [200, 80, 200, 80, 400] : [150],
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  const d = e.notification.data || {};
  e.notification.close();

  // An action button IS a decision — it stops the ladder.
  if (e.action === "got_tickets" || e.action === "cant_make_it") {
    e.waitUntil(
      fetch(`/api/events/${encodeURIComponent(d.event_id)}/state`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: e.action, token: d.token }),
      })
        // On GOT 'EM, take them to the tickets. That's the whole job.
        .then(() => (e.action === "got_tickets" && d.ticket_url
          ? clients.openWindow(d.ticket_url)
          : null))
        .catch(() => clients.openWindow("/"))
    );
    return;
  }

  // Body tap: straight to tickets if we have a link, otherwise the app.
  // Deliberately NOT recorded as a decision — opening the app is not choosing.
  e.waitUntil(clients.openWindow(d.ticket_url || "/"));
});
