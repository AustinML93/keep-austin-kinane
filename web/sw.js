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

const CACHE = "kak-v9";
const SHELL = [
  "/",
  "/index.html",
  "/css/styles.css?v=9",
  "/js/app.js?v=9",
  "/manifest.webmanifest?v=9",
  "/icons/icon-192.png?v=9",
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

/*
 * A delivery RECEIPT, not just an accepted send.
 *
 * "sent=1" only means the push service took the message. It says nothing about
 * whether this device ever received it or whether showNotification succeeded.
 * For an app whose whole promise is that silence means nothing is happening,
 * that gap is unacceptable — so the device reports back what actually happened.
 */
function receipt(stage, detail, data) {
  return fetch("/api/push/receipt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      stage,
      detail: detail ? String(detail).slice(0, 300) : null,
      event_id: (data && data.event_id) || null,
      token: (data && data.token) || null,
    }),
    keepalive: true,
  }).catch(() => {});
}

self.addEventListener("push", (e) => {
  let p = {};
  let parseErr = null;
  try { p = e.data ? e.data.json() : {}; } catch (err) { parseErr = err; p = {}; }

  const data = p.data || {};

  e.waitUntil((async () => {
    await receipt("received", parseErr ? `payload parse failed: ${parseErr}` : null, data);
    try {
      await self.registration.showNotification(p.title || "Kinane", {
        body: p.body || "",
        tag: p.tag,
        // renotify REQUIRES tag — without one Chrome throws a TypeError and the
        // notification never appears at all.
        renotify: p.tag ? p.renotify !== false : false,
        requireInteraction: !!p.requireInteraction,
        actions: p.actions || [],
        data,
        badge: "/icons/badge.png",
        icon: "/icons/icon-192.png",
      });
      await receipt("shown", null, data);
    } catch (err) {
      await receipt("show_failed", err && (err.message || err), data);
      // Never leave the user with nothing. A bare notification beats silence.
      try {
        await self.registration.showNotification(p.title || "Kinane", { body: p.body || "" });
        await receipt("shown_fallback", null, data);
      } catch (err2) {
        await receipt("fallback_failed", err2 && (err2.message || err2), data);
      }
    }
  })());
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
