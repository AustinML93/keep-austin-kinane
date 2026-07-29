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

/* ── Push ────────────────────────────────────────────────────────────────── */

const b64ToUint8 = (b64) => {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
};

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
  $("#push-btn").textContent = "Notifications on";
  $("#push-btn").disabled = true;
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
    .then(([shows, health]) => { renderShows(shows); renderHealth(health); })
    .catch(() => {
      $("#status").textContent = "Can't reach the server. Which is its own kind of answer.";
      $("#status").classList.add("bad");
    });
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
$("#push-btn")?.addEventListener("click", enablePush);
load();
