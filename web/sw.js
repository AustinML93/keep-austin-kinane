/*
 * Service worker: shell cache + push.
 *
 * The important part is notificationclick. Android web push supports action
 * buttons, so a tier-1 alert carries a single GOT 'EM on the lock screen and
 * acknowledging never requires opening the app. That directly attacks the real
 * failure: a notification that arrived correctly and got swiped away at a red
 * light.
 *
 * cant_make_it is still handled here — notifications sent before the switch to
 * one button may still be sitting on a phone, and their buttons must keep
 * working rather than silently doing nothing.
 *
 * Service workers can't read localStorage, so the auth token rides inside the
 * encrypted push payload.
 *
 * ⚠️ Bump CACHE on every shell change, and bump ?v=N in index.html to match.
 *    See CLAUDE.md — Cloudflare's 4h edge cache will otherwise serve the old one.
 */

const CACHE = "kak-v14";
const SHELL = [
  "/",
  "/index.html",
  "/css/styles.css?v=14",
  "/js/app.js?v=14",
  "/manifest.webmanifest?v=14",
  "/icons/icon-192.png?v=14",
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

const setState = (d, state) =>
  fetch(`/api/events/${encodeURIComponent(d.event_id)}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state, token: d.token }),
  });

const ACK = {
  got_tickets: "Got 'em. I'll stop.",
  cant_make_it: "Noted — you can't go. I'll stop.",
};

self.addEventListener("notificationclick", (e) => {
  const d = e.notification.data || {};
  e.notification.close();

  /*
   * An action button IS a decision — it stops the ladder.
   *
   * And because it stops the ladder, a MIS-TAP IS DANGEROUS. Two buttons sit
   * side by side on a lock screen; hitting "can't make it" by accident silently
   * switches off every future alert for a show you actually wanted. That's the
   * soul-crushing miss arriving through a fat finger, so every decision made
   * from a notification comes back with a confirmation and an UNDO.
   */
  if (e.action === "got_tickets" || e.action === "cant_make_it") {
    e.waitUntil((async () => {
      const ack = { ...ACK_FALLBACK, ...(d.ack || {}) };
      try {
        // Report EXACTLY what Chrome handed us, before acting on it. Tapping the
        // button labelled GOT 'EM twice produced cant_make_it, and guessing at
        // why is how you fix the wrong thing.
        await receipt(
          "click",
          `action=${e.action} labels=${JSON.stringify(
            (e.notification.actions || []).map((a) => `${a.action}:${a.title}`))}`,
          d
        );
        await setState(d, e.action);
        await self.registration.showNotification("Uncle BBQ", {
          body: ack[e.action],
          tag: `ack-${d.event_id}`,
          icon: "/icons/icon-192.png",
          badge: "/icons/badge.png",
          actions: [{ action: "undo", title: ack.undo_label }],
          data: d,
        });
        // Deliberately does NOT open the ticket page.
        //
        // "GOT 'EM" means the tickets are already bought. Sending someone to buy
        // them again is nonsense — the original version conflated "I intend to
        // buy" with "I have bought". The ticket link belongs on the BODY tap,
        // which is the act-on-this path.
      } catch (err) {
        await receipt("action_failed", err && (err.message || err), d);
        await clients.openWindow("/");
      }
    })());
    return;
  }

  // Undo puts them back on the hook — 'unseen' resumes the ladder from wherever
  // it had got to, rather than restarting it.
  if (e.action === "undo") {
    e.waitUntil((async () => {
      await setState(d, "unseen").catch(() => {});
      await self.registration.showNotification("Uncle BBQ", {
        body: (d.ack && d.ack.undone) || ACK_FALLBACK.undone,
        tag: `ack-${d.event_id}`,
        icon: "/icons/icon-192.png",
        badge: "/icons/badge.png",
        data: d,
      });
    })());
    return;
  }

  // Body tap: straight to tickets if we have a link, otherwise the app.
  // Deliberately NOT recorded as a decision — opening the app is not choosing.
  e.waitUntil(clients.openWindow(d.ticket_url || "/"));
});
