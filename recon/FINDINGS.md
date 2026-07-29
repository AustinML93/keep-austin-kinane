# Source viability — findings

Probe run 2026-07-29. Re-run with `python3 recon/probe_sources.py`.
Raw responses in `recon/raw/` — write parsers against those fixtures, not live sites.

**Verdict: the design survives contact with reality, and the source list changes.**

---

## 1. kylekinane.com official tour feed — PRIMARY SOURCE ✅

The big finding. His site is a GoHighLevel funnel page, but its tour widget (`upnex`)
reads a **Supabase-backed JSON API**. Clean, structured, authoritative, and current
(payload timestamp matched the probe minute).

**78 events, 22 of them in the future**, spanning 2025-11-13 → 2026-12-12.

Per-event fields:

```
startDate  startTime  timezone  status ("live")  presaleActive
venue  displayVenue  address  city  displayCity  latitude  longitude
ticketLinks[]  { linkType, ticketLink, buttonText }        ← real ticket URLs
showtimes[]    { date, time, utcTimestamp, ticketLinks[] } ← multi-show nights
```

This is better than anything we planned for. It carries **ticket links**, **per-showtime
detail** (the two-shows-a-night club pattern), a **presale flag**, and **lat/long**.

**Two-step access, and it must stay two-step.** The widget's `locationId` and bearer token
are embedded in the page HTML. The probe scrapes them fresh on every run and then calls
the API. **Do not hardcode the token** — when it rotates, a hardcoded one fails silently,
which is the precise failure this project exists to prevent. Config-scrape failure must
report as a source health error, not as "no events."

⚠️ **Risk: the widget host is `upnex-events-test.pages.dev`** — note the `test`. This is a
third-party vendor endpoint that could move or vanish without notice. It's the best source
we have and it should be primary, but it must not be the *only* source. The health check
matters here more than anywhere.

## 2. Cap City Comedy Club — PARSEABLE ✅

Runs on **SeatEngine**, and embeds **277 schema.org `Event` objects** directly in the
page HTML. `name`, `startDate`, `url` — trivially parseable, no JS rendering, no API key.

Kinane not currently booked. That is the *correct* negative result and exactly why the
probe separates parseability from presence: a full calendar is visible, he just isn't on
it. This is the source most likely to catch the soul-crushing miss, and it's the easiest
one we have.

## 3. Moontower Comedy Festival — PARSEABLE, with a caveat ✅

**`moontowercomedy.com` is a HugeDomains parking page** — the domain is for sale, and it
returns the same 55KB shell for *every* path, which is why an early probe read as "site
exists, no calendar." A parked domain that returns 200 for everything is a nice preview of
how sources lie.

Real site: **`moontowercomedyfestival.com`** — WordPress, run by the Paramount Theatre.
`wp-json` returns 522, so there's no REST shortcut; this is an HTML scrape.

Caveat: the festival is in April, and this probe ran in July. **The lineup only exists
seasonally.** The health baseline must not treat an empty off-season lineup page as a
failure — this source is expected to be quiet for most of the year, which makes it the one
source where silence is genuinely uninformative.

## 4. Comedy Mothership — BLOCKED ⛔

Returns **HTTP 429 to every request**, immediately, with any User-Agent, including after
backoff. That's edge bot protection, not real rate limiting.

**We are not going to defeat it.** No UA spoofing, no proxying, no evasion. Options:

- Cover Mothership shows via the official tour feed (which carries ticket links to whatever
  system the venue uses) and Ticketmaster.
- Re-probe occasionally; the block may be misconfigured rather than deliberate.
- Accept it as a known coverage gap and *say so in the app's health readout*, so the
  silence stays honest.

Given the last Kinane show Mike and Rob saw was at the Mothership, this is the most
important gap in the design. It is the strongest argument for keeping several
overlapping sources rather than trusting any one.

## 5. Bandsintown — 403 ⛔

A free-form `app_id` no longer works; it needs a registered one. **Largely moot now** —
the official feed is better on every axis (ticket links, showtimes, coordinates). Demote
to optional backup; revisit only if a registered `app_id` is easy to get.

## 6. Ticketmaster Discovery — UNTESTED ⏳

Needs a free API key from `developer.ticketmaster.com`. Still worth adding: it's the most
plausible independent coverage for Paramount/Moody, and the only realistic path to
Mothership inventory. **Get a key and re-run the probe.**

---

## Consequences for the design

1. **Tiering becomes a distance calculation, not a city list.** The official feed carries
   `latitude`/`longitude` on every event. Haversine from Austin beats maintaining a list of
   metros forever — Georgetown, Buda, Waco, and every other edge case answer themselves.
2. **Config-scrape failure is a distinct health state** from "source returned no events."
   Conflating them recreates false confidence.
3. **Moontower needs a seasonal health baseline.** Empty in August is correct; empty in
   March is alarming.
4. **The Mothership gap must be visible in the app**, not silently absent.

## A bug worth keeping in mind

The probe's first version reported **FOUND HIM** for kylekinane.com on 43 name matches —
every one of them his name in the page title, `og:` tags, and branding. Of course his name
is all over his own website. None of it was a tour date.

That is exactly the false-confidence failure this whole app is built to prevent,
reproduced inside the tool built to check for it, within an hour of writing the spec that
warned about it. Presence now requires the name to appear **near something date-shaped**,
and `<head>` matches are counted separately and discounted.

Worth remembering when writing the real matchers: *a hit is not a show.*
