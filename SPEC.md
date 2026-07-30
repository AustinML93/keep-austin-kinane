# Keep Austin Kinane 🎤

*A mildly stupid PWA with one serious job: Mike and Rob never miss a chance to see
Kyle Kinane live.*

---

## Problem

There is no single reliable way to find out that Kyle Kinane is playing Austin.

The signal arrives scattered and by luck — a Bandsintown email that gets swiped away, a
social feed that surfaces it three days late, a podcast where he mentions the run offhand,
or a friend who knows we're fans and texts us. Sometimes Mike catches it. Sometimes Rob
does. Sometimes neither.

Two failure modes have already happened, and they are not equally bad:

| Outcome | Feeling |
|---|---|
| Found out, couldn't make it | Fine. That's life. |
| Found out after it sold out | Bad. |
| Found out after the show happened, and we could have gone | **Soul crushing.** |

The last row is the entire reason this app exists. Everything else is decoration on top of
a machine built to make that specific feeling impossible.

Two structural gaps make the status quo fail:

1. **Artist-level trackers miss non-tour appearances.** Bandsintown follows *Kyle Kinane
   the touring act*. It does not reliably see a Moontower lineup drop, a multi-comedian
   bill at the Paramount, or a one-off Mothership set that only ever appears on the venue's
   own calendar.
2. **Austin club shows announce and go on sale the same day.** For Cap City and Comedy
   Mothership runs, there is no leisurely announcement→presale→onsale arc. The window
   between "announced" and "sold out" is measured in hours. A notification that arrives
   correctly but goes unread is indistinguishable from no notification at all.

---

## Goals

1. **Never miss a ticketing window for an Austin show.** Announcement-day alerting, loud,
   escalating until explicitly acknowledged.
2. **Surface regional shows conditionally** — Dallas / Houston / San Antonio / Fort Worth
   matter only if there's no Austin date on the table.
3. **Earned silence.** When the app is quiet, that quiet must be trustworthy. A broken
   scraper must announce itself rather than fail into false confidence.
4. **Shared, not parallel.** Mike and Rob see each other's status on every show. Half the
   fun is the coordination.
5. **Make us laugh.** Open the app, leave with a smile. If it isn't fun, it's just a
   worse Bandsintown.

**Success test:** the next time Kinane plays Austin, we know within minutes of it going
on sale, and neither of us had to be the one paying attention.

---

## Non-goals

- **Not a general concert tracker.** One comedian. Adding artists is not a feature, it's a
  different app.
- **No signup, accounts, passwords, or user management.** Two users, seeded as data.
- **No ticket purchasing.** The app gets us to the buy page fast. It does not transact.
- **No social features.** No feed, no comments, no sharing. Two guys and a bit.
- **Not a Kinane archive or fan wiki.** The clip library exists to make us laugh on a
  Tuesday, not to be complete.
- **Not multi-city.** Austin is home, Texas is the road-trip radius, everything else is a
  daydream list.

---

## Users

| User | Role | Capabilities |
|---|---|---|
| **Mike** | curator + full user | Everything, plus bit curation and the candidate queue |
| **Rob** | full user | All show alerts and state, consumes bits, reacts to them |

**Asymmetry boundary — draw it precisely:**

- **Shows are fully symmetric.** Both users get every alert, every escalation, identical
  tiers. Rob must never depend on Mike's phone working. This is non-negotiable; any
  asymmetry here reintroduces the "designated alerter" failure the app exists to kill.
- **Bits are asymmetric.** Mike curates; Rob consumes. Rob never sees the candidate queue,
  the rejected pool, or the machinery. Bits just arrive, and they're always good. That's
  the magic.
- **Rob can react to bits** (a themed thumbs-up equivalent). His reactions are *signal to
  Mike*, not control — nothing Rob does can remove a clip or alter the pool.

Users are stored as rows, not hardcoded constants. Adding a third friend later is an
`INSERT` plus a magic link, not a refactor. **We are explicitly not designing for that
now** — the shared-state bit is delightful at two people and turns into noise at eight.
That's a real design problem and it gets its own thinking if it ever becomes real.

**Auth:** none. Each user gets a one-time magic link containing a token; the PWA stores it
in `localStorage` and sends it as a bearer header. Losing it means re-issuing a link.
Appropriate to the stakes.

---

## Approach

### Naming

Three separable decisions, deliberately not forced to agree — the same split DawgHaus uses
with its *Purple Reign* public-name idea.

| Thing | Value | Why |
|---|---|---|
| Repo / project / spec | **Keep Austin Kinane** | Self-documenting. Boring on purpose. |
| Domain | **`keepaustinkinane.austinmlapps.com`** | Typed twice, ever. Matches the repo, so tunnel routes and nginx configs read clearly in eighteen months. |
| Manifest `name` | **Keep Austin Kinane** | Install prompt, app info. |
| Manifest `short_name` | **Uncle BBQ** | 9 chars — fits Android's ~12-char truncation. What's under the icon every day. |
| Port | **7730** | 773, outer Chicago. |

**Uncle BBQ** is a recurring Kinane reference. It's the right home-screen name precisely
because it's opaque to anyone else who picks up the phone — it came from inside the
friendship rather than from a naming exercise. Icon design should follow from it.

⚠️ **The app must never explain Uncle BBQ or invent an origin for it.** Same rule as the
quotes: if it's attributed, it's sourced. A confidently fabricated backstory for a real
comedian's real bit is exactly the failure mode the voice rules exist to prevent. The name
sits there unexplained, which is funnier anyway.

### Architecture

Deliberately the DawgHaus shape — **two containers, no build step** — with the one
departure that the app has real writes.

```
keep-austin-kinane/
├── web/                    # vanilla PWA, no build step (DawgHaus pattern)
│   ├── index.html
│   ├── css/styles.css
│   ├── js/{app,shows,bits,voice,push,admin}.js
│   ├── sw.js               # service worker: shell cache + push handler
│   ├── manifest.webmanifest
│   └── icons/
├── api/
│   ├── Dockerfile          # python:3.12-alpine + pip deps
│   ├── requirements.txt    # fastapi, uvicorn, pywebpush, httpx, selectolax
│   ├── main.py             # FastAPI app + background scheduler thread
│   ├── db.py               # SQLite (WAL), schema, migrations
│   ├── tiering.py          # tier rules + the Austin-supersedes logic
│   ├── nagger.py           # escalation engine, runs on tick
│   ├── push.py             # VAPID / web push
│   ├── voice.py            # copy strings, weighted random selection
│   └── sources/
│       ├── base.py         # Source protocol: fetch() -> [Event], health contract
│       ├── ticketmaster.py # Discovery API (keyword search)
│       ├── bandsintown.py  # artist API
│       ├── kylekinane.py   # his own site
│       ├── capcity.py      # scraper
│       ├── mothership.py   # scraper
│       └── moontower.py    # festival lineup scraper
├── data/                   # sqlite volume (bind mount, backed up)
├── docker-compose.yml
├── nginx.conf
├── deploy.sh
└── CLAUDE.md
```

**Why one Python service instead of two:** the API and the watcher share a database and a
process lifetime. Splitting them buys nothing and costs a second SQLite writer. One
FastAPI process with a background scheduler thread means **exactly one writer**, which
makes SQLite in WAL mode completely safe with zero ceremony. Scraper exceptions are caught
per-source and can never take down the API.

**Deploy:** identical to DawgHaus. Commit + push, `./deploy.sh` on
`deploy@192.168.1.200` (stash → pull → pull images → `up -d --force-recreate`).
Cloudflare tunnel routes a subdomain of `austinmlapps.com` → `http://localhost:<port>`.

⚠️ **Inherit the DawgHaus caching lore verbatim** — Cloudflare's 4h edge cache, `?v=N`
bumps in both `index.html` and the `sw.js` shell list, `CACHE = "kak-vN"` on every shell
change, `--force-recreate` because bind-mounted `nginx.conf` swaps inodes on pull, and
AdGuard holding stale LAN DNS. These each cost real time once. See `Dawghaus/CLAUDE.md`.

**Considered and rejected: Cloudflare Workers + D1 + Cron Triggers.** Would run free with
no box and no tunnel, and the CF account already exists. Rejected because it breaks the
established self-hosting pattern for no gain at this scale, and OMV is where the rest of
the stuff lives. Worth revisiting only if the OMV box becomes a liability.

### Data model (sketch)

```
users        (id, name, is_curator, push_subscription, magic_token)
events       (id, title, artist_confidence, venue, city, region, starts_at,
              onsale_at, ticket_url, tier, show_kind, first_seen_at, status)
event_sources(event_id, source_id, external_id, raw_json, last_seen_at)
user_events  (user_id, event_id, state, acknowledged_at, notes)
sources      (id, name, kind, last_success_at, last_event_count,
              baseline_event_count, consecutive_failures, health)
nags         (id, user_id, event_id, level, sent_at, channel)
bits         (id, kind, url, title, source, official_where, added_by, state,
              link_checked_at, link_ok, added_at)   -- kind ∈ short | special
bit_ratings  (bit_id, user_id, rating, rated_at)  -- rating ∈ struts | fine | gout
bit_history  (bit_id, served_on)
```

`user_events.state` ∈ `unseen | seen | got_tickets | cant_make_it | passing`.

**`got_tickets` and `cant_make_it` both silence the nagging.** This matters — "I saw it and
genuinely can't go" must be expressible, or the app can't tell disinterest from a phone in
a pocket, and it will nag a man who has already made peace with missing the show.

### Sources and health

Every source implements the same tiny protocol: `fetch() -> list[Event]`, plus it reports
health. Two classes:

**Probed 2026-07-29 — see `recon/FINDINGS.md` for full results.** The list below reflects
what actually works, not what we guessed would.

| Source | Status | Notes |
|---|---|---|
| **kylekinane.com official feed** | ✅ **primary** | Supabase-backed JSON. 78 events, ticket links, showtimes, presale flag, **lat/long**. |
| **Cap City (SeatEngine)** | ✅ parseable | 277 schema.org `Event` objects embedded in HTML. Trivial. |
| **Moontower** | ✅ parseable | Real domain is `moontowercomedyfestival.com` (WordPress). Seasonal. |
| **Ticketmaster Discovery** | ⏳ untested | Needs a free API key. Best independent coverage for Paramount/Moody. |
| **Bandsintown** | ⛔ 403 | Needs a registered `app_id`. Largely moot — the official feed is better. |
| **Comedy Mothership** | ⛔ blocked | HTTP 429 to everything. Bot protection. |

**The official feed is a two-step fetch and must stay that way.** The tour widget's
`locationId` and bearer token are embedded in the page HTML; scrape them fresh, then call
the API. **Never hardcode the token** — when it rotates, a hardcoded one fails silently.
Config-scrape failure is its own health state, distinct from "returned no events."

⚠️ Its host is `upnex-events-test.pages.dev` — note the `test`. Third-party vendor
endpoint, could vanish. Primary, but never the only source.

**The Mothership gap is the most important known hole**, given it's where they last saw
him. It stays visible in the health readout rather than silently absent. **We do not
attempt to defeat the block** — no UA spoofing, no evasion. Coverage comes from the
official feed and Ticketmaster instead.

**Moontower needs a seasonal health baseline.** The festival is in April; an empty lineup
page in August is correct, in March it's alarming. It's the one source where silence is
genuinely uninformative.

Politeness throughout: hourly at most, identifying User-Agent, conditional requests where
supported.

**Health is a first-class feature, not ops hygiene.** The whole value proposition is that
silence means something. A scraper that quietly returns `[]` forever converts the app into
a machine for generating false confidence — the exact failure it was built to prevent,
delivered with a friendly interface.

Each source tracks `last_success_at`, `last_event_count`, and a rolling
`baseline_event_count`. A source is **unhealthy** if it errors repeatedly *or* if it
returns zero events where it reliably used to return a full calendar. Unhealthy sources
surface on the home screen in the app's voice — "Cap City's website is being weird and I
can't see it, go look yourself" — and, if unhealthy for more than ~48h, generate a
low-priority push.

### Matching and dedup

The same show will arrive from Ticketmaster, Bandsintown, and the venue. Merge on
normalized `(date, city, venue-ish)`. Keep the source list per event so the UI can show
who saw it.

**Tuned toward false positives, per explicit direction.** A borderline name match on a
Moontower lineup fires anyway. A wrong alert costs ten seconds; a missed one costs a soul.
`artist_confidence` is stored so low-confidence hits can be visually flagged as "pretty
sure that's him" rather than suppressed.

### Tiering and the Austin rule

**Tiering is a distance calculation, not a city list.** The official feed carries
`latitude`/`longitude` on every event, so haversine from downtown Austin decides the tier.
This makes the "does Georgetown count?" question answer itself, permanently.

| Tier | Rule | Behavior |
|---|---|---|
| **1 — Full alarm** | ≤ 50 mi of Austin (incl. Georgetown, Buda, Round Rock, San Marcos, New Braunfels) | Immediate, loud, escalating until acknowledged |
| **2 — Road trip** | ≤ 250 mi, in Texas (DFW, Houston, San Antonio, Waco, College Station, Corpus) | Conditional (below) |
| **3 — Daydream** | Everywhere else on earth | Silent. Browsable list. No notifications ever. |

A Georgetown show is a Tuesday, not a road trip — hence tier 1, not tier 2. Sources
lacking coordinates fall back to a city-name lookup for the handful of known metros.

**The Austin-supersedes rule.** A tier-2 show is only interesting if he isn't also coming
to Austin. Implemented as a recomputed advisory rather than suppression:

- Dallas announced, no Austin date on the calendar → **alert immediately, but honestly**:
  flag it as *"no Austin date announced yet — this tour might not be fully routed."* Plant
  the idea rather than force a decision.
- As the tier-2 show's decision deadline approaches (on-sale date, or a heuristic
  "tickets are moving" horizon), escalate the framing from *heads up* to *decide: is Austin
  happening or do we drive?*
- If an Austin date appears within ±45 days of a tier-2 show → the tier-2 event is
  **downgraded** and both users are told the road trip is probably unnecessary.
- **If an Austin date appears after tickets were already bought for the tier-2 show, the
  app owes an apology.** This is a real feature, in voice, and it is funny precisely
  because it is sincere.

Tier 3 exists because a cheap points flight to a city we've never been, where Kinane
happens to be playing, is a genuinely good reason to leave the state. It costs one list
view to support.

### Alerting and escalation

**Channel: web push.** Android PWAs get real push via FCM, so the phone receives alerts on
cell data with no inbound connection to the OMV box.

**The key move: notification action buttons.** Android web push supports actions, so a
tier-1 notification carries `GOT 'EM` and `CAN'T MAKE IT` directly on the lock screen.
Acknowledging takes one tap and never requires opening the app. This directly attacks the
observed failure — the notification that arrives correctly and gets swiped away at a red
light.

**Escalation ladder** (tier 1, unacknowledged):

| Level | Timing | Character |
|---|---|---|
| 0 | Immediately | The news. Loud. Buy link front and center. |
| 1 | +2h | "Still nothing from you." |
| 2 | +6h | Openly disappointed. |
| 3 | Next morning | Personal. Invokes past failures. |
| 4 | Daily until on-sale, then daily until the show | Weary. Has stopped expecting better. |

Escalation **stops on any explicit state** — `got_tickets`, `cant_make_it`, or `passing`.
It never stops on "opened the app," because opening the app is not a decision.

**Cross-user awareness makes the nags smarter and funnier.** If Rob has tickets and Mike
hasn't responded, level 2 knows that and says so. This is the single best source of comedy
in the app and it falls out of the data model for free.

**Escalation redundancy (v1.5):** if a tier-1 alert goes unacknowledged past level 3, fire
a second channel — `ntfy` is free, self-hostable, has a native Android app, and requires
one HTTP POST. Guards against web push silently failing on one device, which is the one
remaining path to a soul-crushing miss.

**Tick loop:** every 15 minutes the nagger evaluates open (user, event) pairs against the
ladder. Table-driven, no per-event scheduling.

### Shared state

The core screen is the upcoming show with both users' status side by side:

```
KINANE — Cap City Comedy Club — Oct 12, 7:30pm
  You:  ——
  Rob:  GOT 'EM (about 4 hours before you, incidentally)
```

Read-only awareness. No approvals, no coordination workflow, no chat. The comedy is in
the scoreboard.

### Bits

Two kinds of content, one table, split by `kind`:

- **`short`** — YouTube Shorts and clips under a few minutes. The daily bit draws *only*
  from here, so a 70-minute special can never land on the front page.
- **`special`** — full sets. A browsable shelf, never served as a daily bit.

**Bit of the day.** One clip, chosen deterministically from the approved `short` pool by
seeding on the date — no scheduler, no state, same clip for both users all day, and it
changes at midnight because the date changed. `bit_history` prevents near-term repeats.

**The pool.** Mike-curated. v1 ships with a hand-seeded list of clips he and Rob already
love (`data/bits.seed.json`). Adding one is a URL and a title.

**The specials shelf (v1).** Full sets available on YouTube, browsable when there's an hour
to spend. Cheap to build — it's a curated list, not a discovery problem.

⚠️ **Unofficial full-special uploads rot.** They get taken down, channels vanish, and a
shelf of dead links is a sad object. Two mitigations:

- **The shelf entry is the special, not the URL.** Record `official_where` (the legitimate
  streaming home, where one exists) alongside whatever YouTube link is currently working.
  When the upload dies, the entry survives and still tells you where to watch it.
- **Periodic link check.** A dead link is *marked*, not silently dropped — in voice. Same
  philosophy as source health: the app admits what it can't see rather than going quiet.

**The rating vocabulary — the mechanic theme, three states.** A binary forces every clip
into *banger* or *blocked*, and most clips are neither. Three states make the rotation
genuinely better and the theme extends to three naturally, because a car has a middle
state and a hot dog doesn't.

| Rating | Meaning | Rotation |
|---|---|---|
| 🔧 **Shocks & Struts** | Smooth ride, holds up strong | Heavy |
| 〰️ **Runs Fine** | No complaints, no poetry | Occasional filler |
| 💥 **Gout Flare-Up** | Painful, hard to watch | Never again |

The positive measures **durability**, which is the real curation criterion — a bit that
lands once is easy, a bit that survives its fourth appearance in rotation is worth keeping.
The negative is **merciful rather than contemptuous**, because most rejects aren't crimes,
they're just tired. That's the correct register: warm underneath the grime.

*"Gout Flare-Up" is there because Rob has actually had gout.*

**Both users get all three buttons; the consequences differ.**

- **Mike's rating is control.** It sets the clip's pool state and rotation weight.
- **Rob's rating is pure signal.** Visible to Mike, changes nothing. He cannot remove a
  clip or alter the pool — but he *can* say a bit didn't do it for him, and he should be
  able to. If Rob only had a positive button the app would be relentlessly upbeat at him,
  and half the comedy between two friends is being allowed to register a complaint.

**Discovery (v2, deliberately deferred).** The rating-and-mixing half is trivial — a
`rating` column and a shuffle weighted toward known-good with some unjudged mixed in. The
*expensive* half is candidate discovery: searching YouTube for "Kyle Kinane" returns full
specials, 90-minute podcast episodes, reuploads, audio-over-static-image, and clips of
other comedians merely mentioning him. Automatic discovery means building a junk filter,
and a bad filter poisons the well — Mike would be thumbing down garbage instead of
enjoying bits, which inverts the point.

The v2 shape, when it happens: a scheduled search applies dumb-but-effective filters
(duration < ~15 min, title doesn't match `full special|episode #\d+|podcast`) and drops
survivors into a **candidate queue** that only Mike sees. The daily bit is served *only*
from the approved pool, so the front page can never serve something bad. Clearing the
queue becomes its own small ritual.

v1 gets none of this, because the alerting is the part with a soul-crushing failure mode
attached and it deserves all of v1's attention.

### Voice

**The hybrid rule, and it is a hard rule:**

- **Original copy, unattributed** for everything the app says — empty states, nags, health
  readouts, error messages. Written in his register (warm, defeated, over-articulated
  grandeur about mundane misery; kind underneath the grime) but never presented as
  something he said.
- **Real, sourced material** for anything attributed. Every quote ships with a link to the
  clip or transcript it came from.

**Never generate quotes and attribute them to Kyle Kinane.** An LLM will produce lines that
sound exactly like him with total confidence and be entirely invented — putting words in a
real person's mouth. Asking several models doesn't fix it; they'll agree with each other.
If it's attributed, it's sourced, or it doesn't ship.

**This is why the bit of the day matters structurally, not just as a feature.** It's the
channel where the authentic voice lives. The app doesn't need to approximate him — it can
just let him talk, and keep its own writing honest and unattributed.

**The status readout** is the signature piece: a home-screen line, in the app's voice, that
doubles as a health check. When everything's watched and there's no news, it says so in a
way that's actually reassuring — the silence is confirmed, not assumed. When a source is
down, it admits it.

*Illustrative only — original copy, not attributed to anyone:*

> "Six things watched, nothing to report. He's out there somewhere. Probably in a Cracker
> Barrel parking lot, reconsidering."

> "Cap City's website has been redesigned by someone with ambition. I can't read it
> anymore. You should probably look yourself."

---

## Open questions

1. ~~Port number.~~ **Decided: 7730** — the 773 Chicago area code, his home turf, outer
   neighborhoods rather than downtown 312. Verify nothing's already bound to it on the OMV
   box before committing.
2. ~~Subdomain.~~ **Decided: `keepaustinkinane.austinmlapps.com`.** See Naming.
3. ~~Regional city list.~~ **Dissolved.** The official feed carries lat/long, so tiering is
   a distance calculation. See Tiering.
4. **What "decision deadline" means for a tier-2 show** with no published on-sale date.
   Probably `min(onsale_at, show_date - 21d)`, but it wants a real-world sanity check.
5. **Does Rob get a curator-lite role over time?** Currently no, by design. Worth revisiting
   only if the asymmetry ever feels like exclusion instead of magic.
6. **Backup story for the SQLite file.** Whatever OMV already does, presumably — but the
   ratings and the bit pool are the only genuinely unrecoverable data here.
7. ~~Reaction vocabulary.~~ **Decided: three-state mechanic theme** — Shocks & Struts /
   Runs Fine / Gout Flare-Up. See Bits.
8. ~~App name vs. domain vs. home-screen name.~~ **Decided.** See Naming.

---

## Status — end of build day 1

Everything below is live at `keepaustinkinane.austinmlapps.com`, self-hosted on the
OMV box behind the Cloudflare tunnel. 130 tests.

| Area | State |
|---|---|
| Sources | ✅ `kylekinane` (official feed), `capcity`, `ticketmaster`, `manual` |
| Tiering + Austin rule | ✅ distance-based, tested |
| Escalation ladder | ✅ both tiers, verified on a real phone |
| Web push | ✅ proven end to end, with delivery receipts and one retry on non-delivery |
| Shared state | ✅ scoreboard, cross-user nag copy fires correctly |
| Manual entry | ✅ the only thing that will ever catch a Mothership booking |
| Bits | ✅ 32 in daily rotation, 22-day window, playlist sync + paste |
| Holds up / shelf | ✅ 6 full specials, keepers list built from the ratings |
| Voice | ✅ one owner (`api/voice.py`), variants per level, `/api/copy` |
| UX | ✅ app frame, palette sampled from the artwork, Anton display face, grain |
| Backups | ✅ daily consistent snapshot, 7 kept |
| **Rob** | ⏳ **not set up — no push subscription yet.** The last unverified thing. |
| Moontower | ⏳ not built. Seasonal; dormant until April. |
| ntfy fallback | ⏳ not built, and now lower priority — see below. |

**Run `python3 -m api.cli health` on the box for the current picture** — sources,
who's set up, show counts, bits pool, and the last few delivery receipts.

### What today actually taught us

Three separate bugs were hidden behind **error handling that reported the wrong
cause**, and each cost real time:

1. `failed=2` for a VAPID private key the library could not parse. Every push had
   been failing before leaving the box; we spent an hour suspecting the phone.
2. `"Can't reach the server"` for a `ReferenceError` in our own rendering code,
   while all six endpoints returned 200.
3. A silent no-op `.replace()` during an edit, which caused #2.

For an app whose entire promise is *silence means nothing is happening*, a
delivery layer that fails quietly is the worst possible shape. The delivery
receipts exist because of this, and they are probably the most valuable thing
built today.

The same pattern showed up in the design, twice: a **destructive lock-screen
button next to the safe one**, and a **no-repeat window that silently stopped
applying** when the pool was smaller than the window. Both degraded quietly
rather than failing loudly.

**ntfy is now lower priority than when it was specced.** Retry-on-unconfirmed
covers the transient-loss case it was meant to insure against, and it needs a
topic decision.

## Next steps

**Immediate, and the only one that matters:**

0. **Get Rob set up.** His magic link is in the DB (`cli health` doesn't print
   tokens; `cli adduser rob Rob` reissues one, or read it from the `users` table).
   He must open it in **Chrome directly**, not from a messaging app's webview.
   Then install, then enable notifications, then fire a test alert **at his phone
   specifically** — everything we've proven about push, we proved on Mike's.

**Then, in rough order of value:**

1. ~~Verify the sources.~~ ✅ **Done — `recon/probe_sources.py`, results in
   `recon/FINDINGS.md`.** The design survives; the source list changed.
2. **Get a Ticketmaster Discovery API key** (free, `developer.ticketmaster.com`) and re-run
   the probe. It's the only realistic independent coverage for Mothership inventory.
3. Scaffold the repo in the DawgHaus shape: `web/`, `api/`, `data/`, compose, nginx,
   `deploy.sh`, `CLAUDE.md`.
4. Schema + `sources/base.py` protocol + one real source end to end, with health tracking.
5. Remaining sources.
6. Tiering + the Austin-supersedes rule, with tests — this logic is subtle enough to
   deserve them.
7. Web push: VAPID keys, subscription storage, service worker handler, **action buttons**.
8. Nagger tick loop + escalation ladder.
9. PWA shell: upcoming shows with shared state, tier-3 browse list, status readout.
10. Voice pass — write the copy properly rather than filling in placeholders. This is the
   part that makes it worth using.
11. Bits: seed pool, deterministic daily pick, Rob's reactions, Mike's admin screens.
12. Deploy behind the tunnel, install on both phones, **verify push end-to-end on Rob's
    device specifically** before declaring it done.
13. Fire a fake tier-1 alert and let the full escalation ladder run. The nagging is the
    product; it needs to be experienced before it's trusted.
