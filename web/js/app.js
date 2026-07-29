/*
 * Keep Austin Kinane — app shell.
 *
 * Presentation is still a first pass; the real voice pass is its own step. But
 * the wiring is real: magic-link auth, push subscription, and the shared state
 * that makes this a two-person app instead of two alarm clocks.
 */

const $ = (s) => document.querySelector(s);
const TOKEN_KEY = "kak_token";

/* ── Auth: a magic link, once. No passwords, appropriate to the stakes. ───── */
(function captureToken() {
  const t = new URLSearchParams(location.search).get("t");
  if (t) {
    localStorage.setItem(TOKEN_KEY, t);
    // Don't leave the token sitting in the URL bar or in history.
    history.replaceState({}, "", location.pathname);
  }
})();

const token = () => localStorage.getItem(TOKEN_KEY);
const authed = () => !!token();

/* ── Install ─────────────────────────────────────────────────────────────── */

/*
 * Chrome fires beforeinstallprompt once it considers the app installable AND
 * its engagement heuristic is satisfied. Capturing it lets us offer one button
 * instead of "dig through the ⋮ menu", which is not a thing you can reasonably
 * ask a second person to do.
 *
 * The event is NOT guaranteed to fire — Chrome withholds it on a first visit,
 * and other browsers never send it — so there's a plain-text fallback. Silence
 * from Chrome must not leave someone with no way in.
 */
let deferredInstall = null;
const installed = () =>
  window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstall = e;
  $("#install-hint").classList.add("hidden");
  $("#install-box").classList.remove("hidden");
});

window.addEventListener("appinstalled", () => {
  deferredInstall = null;
  $("#install-box").classList.add("hidden");
  $("#install-hint").classList.add("hidden");
});

$("#install-btn")?.addEventListener("click", async () => {
  if (!deferredInstall) return;
  deferredInstall.prompt();
  await deferredInstall.userChoice;
  deferredInstall = null;
  $("#install-box").classList.add("hidden");
});

// If Chrome hasn't offered the event a few seconds in, show the manual route
// rather than leaving a returning visitor with nothing.
setTimeout(() => {
  if (!installed() && !deferredInstall) $("#install-hint").classList.remove("hidden");
}, 4000);

/* ── Push ────────────────────────────────────────────────────────────────── */

const b64ToUint8 = (b64) => {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
};

/*
 * Reflect the ACTUAL subscription state on every load.
 *
 * The button used to always read "Turn on notifications", so there was no way
 * to tell whether you were covered — and for an app whose promise is that
 * silence means nothing is happening, "am I even subscribed?" is not a question
 * the user should have to guess at.
 *
 * It also silently re-registers an existing subscription with the server. That
 * is idempotent (the server upserts on endpoint) and it HEALS the dangerous
 * case: a subscription the browser still holds but the server has forgotten or
 * dropped as stale. Without this, one of them could quietly stop receiving
 * alerts while the app looked fine.
 */
async function refreshPushState() {
  const btn = $("#push-btn");
  if (!btn) return;

  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    btn.classList.add("hidden");
    return;
  }
  if (Notification.permission === "denied") {
    btn.textContent = "Notifications are blocked";
    btn.disabled = true;
    btn.title = "Chrome ⋮ → Settings → Site settings → Notifications";
    return;
  }

  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();

  if (sub) {
    btn.textContent = "Notifications on";
    btn.disabled = true;
    if (authed()) {
      fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token(), subscription: sub }),
      }).catch(() => {});
    }
  } else {
    btn.textContent = "Turn on notifications";
    btn.disabled = false;
  }
}

async function enablePush() {
  if (!authed()) return alert("Open your magic link first.");
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    return alert("This browser can't do push. Android Chrome can.");
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") {
    return alert("Without notifications this is just a website that knows things.");
  }

  const reg = await navigator.serviceWorker.ready;
  const { key } = await fetch("/api/vapid-public-key").then((r) => r.json());
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: b64ToUint8(key),
  });
  await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: token(), subscription: sub }),
  });
  await refreshPushState();
}

/* ── Rendering ───────────────────────────────────────────────────────────── */

const fmt = (iso) => {
  const d = new Date(iso.length > 10 ? iso : iso + "T00:00");
  const date = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return iso.length > 10
    ? `${date}, ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`
    : date;
};

const AUSTIN_NOTE = {
  unknown: "no Austin date announced yet — this tour may not be fully routed",
  superseded: "he's coming to Austin too; nobody has to drive",
  owed_an_apology: "Austin date appeared after you bought these. that one's on the app.",
};

const STATE_LABEL = {
  unseen: "—",
  seen: "seen it",
  got_tickets: "GOT 'EM",
  cant_make_it: "can't make it",
  passing: "passing",
};

async function setState(eventId, state) {
  await fetch(`/api/events/${encodeURIComponent(eventId)}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state, token: token() }),
  });
  load();
}

function showRow(s, me) {
  // The scoreboard IS the feature. Two guys, one line, no coordination workflow.
  const scoreboard = Object.entries(s.states || {})
    .map(([id, v]) => `<span class="who ${v.state}">${id === me ? "You" : v.name}: ${
      STATE_LABEL[v.state] || v.state}</span>`)
    .join("");

  // Only tiers 1 and 2 get decision buttons — a daydream needs no answer.
  const buttons = s.tier < 3 && me
    ? `<span class="acts">
         <button data-ev="${s.id}" data-st="got_tickets">GOT 'EM</button>
         <button data-ev="${s.id}" data-st="cant_make_it">CAN'T MAKE IT</button>
       </span>`
    : "";

  return `
    <div class="show">
      <span class="when">${fmt(s.starts_at)}</span>
      <span class="venue">${s.venue || ""}</span>
      <span class="city">${s.city || ""}${
        s.distance_mi != null ? ` · ${Math.round(s.distance_mi)}mi` : ""}</span>
      ${s.austin_status ? `<span class="note">${AUSTIN_NOTE[s.austin_status] || ""}</span>` : ""}
      ${s.ticket_url ? `<a class="tix" href="${s.ticket_url}" target="_blank" rel="noopener">tickets</a>` : ""}
      <span class="scoreboard">${scoreboard}</span>
      ${buttons}
    </div>`;
}

function renderShows(data) {
  const shows = data.shows || [];
  $("#empty").classList.toggle("hidden", shows.length > 0);

  for (const tier of [1, 2, 3]) {
    const rows = shows.filter((s) => s.tier === tier);
    const sec = $(`#tier${tier}`);
    if (!rows.length) { sec.classList.add("hidden"); continue; }
    sec.classList.remove("hidden");
    sec.querySelector(".rows").innerHTML = rows.map((s) => showRow(s, data.me)).join("");
  }

  document.querySelectorAll(".acts button").forEach((b) =>
    b.addEventListener("click", () => setState(b.dataset.ev, b.dataset.st)));
}

function renderHealth(h) {
  // Silence has to be EARNED. Say what's being watched, and admit what isn't.
  $("#status").textContent = h.status_line;
  $("#status").classList.toggle("bad", !h.all_eyes_open);

  $("#sources").innerHTML = h.sources.map((s) => `
    <div class="src ${s.health}">
      <b>${s.name}</b><span>${s.health}</span><span>${s.last_total_seen ?? "—"} listed</span>
    </div>`).join("");
}

function load() {
  const headers = authed() ? { Authorization: `Bearer ${token()}` } : {};
  Promise.all([
    fetch("/api/shows", { headers }).then((r) => r.json()),
    fetch("/api/health").then((r) => r.json()),
  ])
    .then(([shows, health]) => {
      if (shows.me) localStorage.setItem("kak_me", shows.me);
      renderShows(shows);
      renderHealth(health);
    })
    .catch(() => {
      $("#status").textContent = "Can't reach the server. Which is its own kind of answer.";
      $("#status").classList.add("bad");
    });
}

/* ── Bits ────────────────────────────────────────────────────────────────── */

const RATINGS = [
  { r: "struts", label: "🔧 Shocks & Struts" },
  { r: "fine",   label: "〰️ Runs Fine" },
  { r: "gout",   label: "💥 Gout Flare-Up" },
];

function bitFrame(b, autoplay) {
  const cls = `bit-frame${b.vertical ? " vertical" : ""}`;
  if (!b.embed_url) {
    return `<a class="${cls}" href="${b.url}" target="_blank" rel="noopener">
              <img src="${b.thumbnail || ""}" alt=""></a>`;
  }
  // Thumbnail until tapped. Autoplay with sound is blocked everywhere, and a
  // phone that starts talking when you open it is a phone you stop opening.
  return autoplay
    ? `<div class="${cls}"><iframe src="${b.embed_url}?autoplay=1&rel=0"
         allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe></div>`
    : `<div class="${cls}">
         <img src="${b.thumbnail || ""}" alt="">
         <button class="play" data-play="${b.id}" aria-label="Play">▶</button>
       </div>`;
}

function renderBit(b, me, autoplay = false) {
  if (!b) { $("#bit").classList.add("hidden"); return; }
  $("#bit").classList.remove("hidden");

  const others = Object.entries(b.ratings || {})
    .filter(([who]) => who !== me)
    .map(([who, r]) => `${who} said ${RATINGS.find((x) => x.r === r)?.label || r}`)
    .join(" · ");

  $("#bit-body").innerHTML = `
    ${bitFrame(b, autoplay)}
    <p class="bit-title">${b.display_title || ""}</p>
    <p class="bit-meta">${b.channel || ""}</p>
    ${me ? `<div class="rate">${RATINGS.map((x) =>
      `<button data-bit="${b.id}" data-r="${x.r}" class="${b.my_rating === x.r ? "on" : ""}">${
        x.label}</button>`).join("")}</div>` : ""}
    ${others ? `<p class="rate-note">${others}</p>` : ""}`;

  $("#bit-body").querySelector("[data-play]")?.addEventListener("click", () =>
    renderBit(b, me, true));

  $("#bit-body").querySelectorAll(".rate button").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await fetch(`/api/bits/${encodeURIComponent(btn.dataset.bit)}/rate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating: btn.dataset.r, token: token() }),
      });
      b.my_rating = btn.dataset.r;
      b.ratings = { ...(b.ratings || {}), [me]: btn.dataset.r };
      renderBit(b, me, autoplay);
    }));
}

function renderShelf(list) {
  if (!list || !list.length) { $("#shelf").classList.add("hidden"); return; }
  $("#shelf").classList.remove("hidden");
  $("#shelf-body").innerHTML = list.map((b) => `
    <a class="special" href="${b.url}" target="_blank" rel="noopener">
      ${b.thumbnail ? `<img src="${b.thumbnail}" alt="">` : ""}
      <span><span class="t">${b.display_title || ""}</span><br>
      <span class="c">${b.channel || ""}${b.official_where ? ` · also on ${b.official_where}` : ""}</span></span>
    </a>`).join("");
}

function loadBits() {
  const headers = authed() ? { Authorization: `Bearer ${token()}` } : {};
  fetch("/api/bits/today", { headers }).then((r) => r.json())
    .then((d) => {
      const me = localStorage.getItem("kak_me");
      renderBit(d.bit, me);
    }).catch(() => {});
  fetch("/api/bits/specials", { headers }).then((r) => r.json())
    .then((d) => renderShelf(d.specials)).catch(() => {});
}

/* ── Manual add ──────────────────────────────────────────────────────────── */

fetch("/api/venues")
  .then((r) => r.json())
  .then(({ venues }) => {
    $("#venue-list").innerHTML = venues.map((v) => `<option value="${v}">`).join("");
  })
  .catch(() => {});

const TIER_NAME = { 1: "Austin — full alarm", 2: "road trip", 3: "daydream (won't notify)" };

$("#add-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#add-result");
  if (!authed()) { out.textContent = "Open your magic link first."; out.className = "bad"; return; }

  const body = Object.fromEntries(new FormData(e.target).entries());
  const res = await fetch("/api/events/manual", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, token: token() }),
  }).then((r) => r.json()).catch(() => null);

  if (!res || !res.ok) {
    out.textContent = (res && res.detail) || "Couldn't add it.";
    out.className = "bad";
    return;
  }
  // Tell them which tier it landed in — a show that silently lands in tier 3
  // will never notify anyone, and that's exactly the failure to avoid.
  out.textContent = `${res.is_new ? "Added" : "Merged into a show we already had"} · ${
    TIER_NAME[res.tier]}`;
  out.className = res.tier === 3 ? "bad" : "";
  e.target.reset();
  e.target.city.value = "Austin, TX";
  load();
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
$("#push-btn")?.addEventListener("click", enablePush);
load();
loadBits();
refreshPushState();
