/* Placeholder shell. The real screens (shared status, nags, bits) come later. */

const $ = (s) => document.querySelector(s);

const fmt = (iso) => {
  const d = new Date(iso.length > 10 ? iso : iso + "T00:00");
  const date = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return iso.length > 10
    ? `${date}, ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`
    : date;
};

const AUSTIN_NOTE = {
  unknown: "no Austin date announced yet",
  superseded: "he's also coming to Austin",
  owed_an_apology: "Austin date appeared after you bought. sorry.",
};

function renderShows(data) {
  const shows = data.shows || [];
  if (!shows.length) { $("#empty").classList.remove("hidden"); return; }

  for (const tier of [1, 2, 3]) {
    const rows = shows.filter((s) => s.tier === tier);
    if (!rows.length) continue;
    const sec = $(`#tier${tier}`);
    sec.classList.remove("hidden");
    sec.querySelector(".rows").innerHTML = rows.map((s) => `
      <a class="show" ${s.ticket_url ? `href="${s.ticket_url}" target="_blank" rel="noopener"` : ""}>
        <span class="when">${fmt(s.starts_at)}</span>
        <span class="venue">${s.venue || ""}</span>
        <span class="city">${s.city || ""}${
          s.distance_mi != null ? ` · ${Math.round(s.distance_mi)}mi` : ""
        }</span>
        ${s.austin_status ? `<span class="note">${AUSTIN_NOTE[s.austin_status] || ""}</span>` : ""}
      </a>`).join("");
  }
}

function renderHealth(h) {
  // Silence has to be earned — say what is actually being watched, and admit
  // what isn't. A source that has gone blind must never read as "no news".
  const ok = h.all_eyes_open;
  $("#status").textContent = ok
    ? `${h.sources.length} sources watched. Nothing to report.`
    : `Can't see: ${h.blind.join(", ")}. Go look yourself.`;
  $("#status").classList.toggle("bad", !ok);

  $("#sources").innerHTML = h.sources.map((s) => `
    <div class="src ${s.health}">
      <b>${s.name}</b>
      <span>${s.health}</span>
      <span>${s.last_total_seen ?? "—"} listed</span>
    </div>`).join("");
}

Promise.all([
  fetch("/api/shows").then((r) => r.json()),
  fetch("/api/health").then((r) => r.json()),
])
  .then(([shows, health]) => { renderShows(shows); renderHealth(health); })
  .catch(() => { $("#status").textContent = "Can't reach the server."; $("#status").classList.add("bad"); });
