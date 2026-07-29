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

const CACHE = "kak-v5";
const SHELL = [
  "/",
  "/index.html",
  "/css/styles.css?v=5",
  "/js/app.js?v=5",
  "/manifest.webmanifest?v=5",
  "/icons/icon-192.png?v=5",
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
