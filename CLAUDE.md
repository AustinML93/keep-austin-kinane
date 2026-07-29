# Keep Austin Kinane 🎤 — project notes for Claude

A mildly stupid PWA with one serious job: **Mike and Rob never miss a chance to see Kyle
Kinane live.** Announcement-day alerting with escalating nags, shared ticket status between
the two of them, a bit of the day, and a status readout in the app's voice.

Sibling project to **DawgHaus** (`AustinML93/Dawghaus`) — same self-hosted OMV + Cloudflare
tunnel pattern, same no-build-step PWA. **Read `SPEC.md` first**; it holds the full design
and the reasoning behind every decision below.

## The one thing that matters

The app's promise is that **silence means nothing is happening**. A parser that breaks and
returns zero events forever converts that promise into a lie, and the miss it causes is the
exact failure the app exists to prevent.

So every source reports two independent numbers:

- `events` — Kinane shows found (**presence**)
- `total_seen` — how many events the source listed at all (**parseability**)

Zero Kinane out of a 277-event calendar is **healthy**. Zero out of zero is **broken**.
Never collapse these. `tests/test_health.py` is the most important file in the repo.

## Voice rules — non-negotiable

- **Original copy, unattributed** for everything the app says. His register (warm, defeated,
  over-articulated grandeur about mundane misery) but never presented as his words.
- **Never generate a quote and attribute it to Kyle Kinane.** An LLM will produce lines that
  sound exactly like him and are entirely invented. If it's attributed, it's sourced — with
  a link to the clip or transcript.
- **Never explain "Uncle BBQ" or invent an origin for it.** It's a real recurring bit of his.
  A confidently fabricated backstory is the same failure as a fabricated quote.
- Rating vocabulary: 🔧 Shocks & Struts / 〰️ Runs Fine / 💥 Gout Flare-Up.

## Stack & layout

No build step. Vanilla PWA + **two containers** (nginx + one Python service).

- `web/` — the PWA. **Currently a placeholder shell**; the voice pass is a deliberate later
  step. `index.html`, `css/styles.css`, `js/app.js`, `manifest.webmanifest`.
- `api/` — FastAPI **plus the background poller in one process**. One process = exactly one
  SQLite writer = WAL mode is safe with no ceremony. The poll thread catches everything; a
  scraper must never take the API down.
  - `sources/` — one module per source. **stdlib `urllib` only, no deps**, so parsers and
    their tests run on bare `python3`.
  - `tiering.py` — distance from Austin, not a city list. The official feed carries lat/long.
  - `db.py` — schema + the health state machine.
- `recon/` — `probe_sources.py` (re-runnable viability probe), `FINDINGS.md`, and
  `raw/` fixtures. **`raw/` is committed on purpose** — the tests parse those files.
- `data/` — SQLite volume. `bits.seed.json` is committed; the `.sqlite3` is not.

## Sources (probed 2026-07-29 — see `recon/FINDINGS.md`)

| Source | Status | Notes |
|---|---|---|
| `kylekinane` | ✅ primary | Supabase JSON API. 78 events, ticket links, showtimes, lat/long. |
| `capcity` | ✅ working | SeatEngine, 277 schema.org Events embedded in HTML. |
| Moontower | ⏳ todo | `moontowercomedyfestival.com` (WordPress). **Seasonal — dormant is correct.** |
| `ticketmaster` | ✅ working | Set `TICKETMASTER_API_KEY` (the **Consumer Key**, not the Secret). Only source with a real **on-sale time**. Registers only when the key is present. |
| Bandsintown | ⛔ 403 | Needs a registered `app_id`. Moot — the official feed is better. |
| Mothership | ⛔ blocked | HTTP 429 to everything. **Do not try to defeat it.** Confirmed NOT carried by Ticketmaster either. Covered only by `manual`. |
| `manual` | ✅ working | Hand-typed shows. A first-class source, not a fallback — see below. |

⚠️ **The official feed is a two-step fetch: scrape `locationId` + `eventPortalToken` from
kylekinane.com, then call the API.** Never hardcode the token — when it rotates, a
hardcoded one fails silently. Config-scrape failure returns `error_kind="config"` and shows
as its own health state.

⚠️ Its host is `upnex-events-test.pages.dev` — note the `test`. Third-party vendor endpoint,
could vanish without notice. Primary, but never the only source.

## Commands

```bash
python3 -m api.cli poll                      # fetch everything, store, tier, report
python3 -m api.cli shows                     # what's upcoming, by tier
python3 -m api.cli health                    # what each source can currently see
python3 -m api.cli adduser rob Rob            # issue a magic link
python3 -m api.cli vapid                      # push public key (generated once, stored in DB)
python3 -m api.cli simulate                   # fake a tier-1 Austin show...
python3 -m api.cli nags --dry-run             # ...and watch the ladder without sending
python3 -m api.cli unsimulate
python3 -m unittest discover -s tests -t .   # 49 tests, no network
python3 recon/probe_sources.py               # re-probe source viability
```

## Deploy

- **Secrets live in `.env` beside `docker-compose.yml`** on the box — gitignored, and
  `docker compose` reads it automatically. See `.env.example` for the full list
  (`TICKETMASTER_API_KEY`, `YOUTUBE_API_KEY`, `KAK_BITS_PLAYLIST`). Never the repo.
- For local CLI runs: `set -a; source .env; set +a` then `python3 -m api.cli …`
- **Port 7730** (the 773 Chicago area code). Cloudflare tunnel routes
  `keepaustinkinane.austinmlapps.com` → `http://localhost:7730`. Mike owns tunnel/DNS.
- GitHub: **https://github.com/AustinML93/keep-austin-kinane** (public). `gh` authed as
  AustinML93, commits authored as Mike Larsen.
- OMV: `deploy@192.168.1.200`. Ship: commit + push, then `./deploy.sh` on the box.
  **First-time setup and the push-verification checklist are in `DEPLOY.md`.**
- ⚠️ `api/Dockerfile` is `python:3.12-slim`, **not alpine** — `pywebpush` pulls in
  `cryptography`, which on musl can fall through to a source build needing a Rust
  toolchain. Debian gets a prebuilt wheel. Don't "fix" it back to alpine.
- Home-screen name is **Uncle BBQ** (manifest `short_name`, 9 chars — Android truncates
  around 12). The manifest `shortcuts` entry named "Kyle Kinane Shows" is what makes the app
  findable by searching "Kinane" in the app drawer; `short_name` alone would not be.

## ⚠️ Caching — inherited from DawgHaus, each of these cost real time once

1. **Cloudflare 4h edge cache** overrides origin headers. Bump `?v=N` on shell assets in
   `index.html` (and in the `sw.js` SHELL list once a service worker exists).
2. **Service worker:** bump `CACHE = "kak-vN"` on every shell change. Clients need a full
   PWA close/reopen, sometimes twice.
3. **`nginx.conf` is a single-file bind mount** — `git pull` swaps the inode, so a plain
   reload serves the OLD config. `deploy.sh` uses `--force-recreate`.
4. **LAN DNS via AdGuard Home** can hold a stale/negative cache after CF DNS changes.
   `docker restart adguardhome` clears it. Cellular bypasses it.

## Alerting

Web push via FCM. **Tier-1 notifications carry action buttons** (`GOT 'EM` /
`CAN'T MAKE IT`) so a decision is one tap from the lock screen — that directly attacks the
real failure, which was a notification arriving correctly and being swiped away at a red
light. The SW can't read localStorage, so the auth token rides inside the encrypted push
payload.

Ladder (`api/nagger.py`): tier 1 is anchored to the **announcement** (0 / +2h / +6h / next
9am / daily); tier 2 to the **decision deadline** (heads-up, −7d, −2d, deadline). Tier 3
never nags.

Three rules that are easy to break and are tested:
1. **First contact is always L0.** Polling is hourly, so a show can be hours old before we
   see it — catch-up must never open with the app complaining about silence the user was
   never given a chance to break.
2. **Catch-up jumps, it doesn't burst.** Two days offline = one notification, not five.
3. **Only an explicit decision stops the ladder.** `seen` does not — opening the app is not
   a decision. `cant_make_it` stops it as firmly as `got_tickets`.

Nags are recorded on **attempt**, not on delivery success, or a user with no registered
device would re-trigger the same level forever.

⚠️ **Ticketmaster emits `1900-01-01T06:00:00Z` as an on-sale sentinel** (2 of 8 events in
the first live pull). The tier-2 deadline is anchored to on-sale, so a past deadline makes
every level overdue at once — "Last call" on announcement day. Filtered in the source AND
defended in `decision_deadline()`. Treat any source's date fields as hostile.

**Dedupe:** `dedupe_key()` is date|venue|time so a 7:00 and a 9:15 stay separate. A source
giving a date with NO time (the official feed does — Cobb's 2026-12-04) folds into the
earliest known showtime at that venue via `loose_key()`, rather than becoming a phantom
third row.

## Manual entry

`manual` is a **first-class source**. Discovery was always social — "sometimes it's my
friend, and sometimes it's just someone that knows we love him" — and it's the only thing
that will ever catch a **Comedy Mothership** booking or a surprise drop-in. Both users can
add; either might hear first.

- `api/venues.py` maps hand-typed venue names to approximate coordinates so tiering works
  without a geocoding service. Coordinates are **downtown-block accurate on purpose** — all
  they feed is a distance bucket.
- ⚠️ **Venue names are canonicalised before the dedupe key is built.** The key is
  `date|venue|time`, so "the Mothership" and "Comedy Mothership" must collapse to one
  string or they're two shows on the same night — one permanently unacknowledged and still
  nagging.
- Manual events **merge** with automatic ones by the normal key, so a show typed in from a
  text quietly becomes the same row when a real source catches up.
- `delete_manual_event` refuses if any real source has confirmed the show. Deleting your
  note must never delete the show.
- `manual` is excluded from the blind/health calculation — a human is not a scraper and
  cannot go dark.

## Bits

Two capture paths, one rating layer.

- **Playlist sync** (`KAK_BITS_PLAYLIST`) is the fast path. Mike saves a clip on YouTube in
  the moment he finds it; the app notices every 6h. **The playlist is an INBOX, not a
  curated pool** — it's allowed to be messy, because the rating is the filter, not his
  tidiness. Synced items land `active` and unrated: putting it in the playlist IS the
  endorsement, and requiring him to process a queue is the chore this design avoids.
- **Paste a URL** (`POST /api/bits`) handles anything else. oEmbed supplies title, channel,
  and thumbnail with **no API key**, so adding is one field.
- **Rename** anything. YouTube's title is SEO; "the one about the raccoon" is how they
  actually refer to it, and that's the best voice research this project will get.

Rotation weights: `struts` 3 · unrated 2 · `fine` 1 · `gout` blocked. **Unrated sits
between the rated tiers on purpose** — new clips surface often enough to earn a verdict
without crowding out what's known to land.

⚠️ **`eligible()` excludes today's own history from the no-repeat rule.** Serving the bit
records it; if that counted, the pick would change the instant anyone fetched it and Mike
and Rob would see different "bits of the day". Found in a live run, guarded by
`test_serving_the_bit_does_not_change_todays_pick`.

⚠️ **Re-sync never resurrects a blocked bit or clobbers a custom title.**

**YOUTUBE_API_KEY is optional but wanted.** Without it the playlist still syncs by scraping
the page, but **oEmbed returns a fixed 200x113 with no duration** — verified — so we cannot
tell a Short from a clip or detect vertical video. With the key: `contentDetails.duration`
→ kind, real player dimensions → orientation, and `status.embeddable` before we try to play.

Specials (>25min) are **never** the bit of the day. That's the shelf.

## Voice files

`api/voice.py` holds every string the app says. All of it ORIGINAL and unattributed — read
the header before adding a line.

## Not built yet

Bits (pool, daily pick, ratings, Rob's reactions, curator screens), the ntfy escalation
fallback, Moontower + Ticketmaster sources, and the real voice pass on presentation.
See **Next steps** in `SPEC.md`.
