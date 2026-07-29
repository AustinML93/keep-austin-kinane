#!/usr/bin/env python3
"""
Source viability probe for Keep Austin Kinane.

Answers one question before we build anything: CAN these sources actually see him?

Two checks, deliberately independent — this distinction is the whole point:

  1. PARSEABILITY — can we see a calendar of events at all? Proves the source is
     readable and tells us which parsing strategy to use.
  2. PRESENCE     — is Kinane in it right now? He may simply not be booked, which
     is NOT a broken source.

Conflating those two is exactly the false-confidence failure the app exists to
prevent, so the recon script refuses to conflate them either.

Raw responses are saved to recon/raw/ so parsers can be written against real
fixtures instead of live sites.

stdlib only, matching the DawgHaus updater convention. No install step.

Usage:
    python3 recon/probe_sources.py
    TICKETMASTER_API_KEY=xxx python3 recon/probe_sources.py
"""

import gzip
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ARTIST = "Kyle Kinane"
NEEDLE = "kinane"  # match on surname alone — catches "KINANE", "Kyle Kinane", festival lineups

RAW_DIR = Path(__file__).parent / "raw"
UA = "KeepAustinKinane/0.1 (personal show-tracker; 2 users; polite, hourly at most)"
TIMEOUT = 25


# ──────────────────────────────────────────────────────────────────────────────
# Sources
#
# Each source lists candidate URLs; we try them in order until one returns 200.
# I am not certain of every path — that uncertainty is precisely what this
# script exists to resolve, so a 404 here is a finding, not a bug.
# ──────────────────────────────────────────────────────────────────────────────

def tm_url():
    key = os.environ.get("TICKETMASTER_API_KEY", "")
    if not key:
        return None
    q = urllib.parse.urlencode({
        "apikey": key,
        "keyword": ARTIST,
        "classificationName": "Comedy",
        "size": 100,
    })
    return f"https://app.ticketmaster.com/discovery/v2/events.json?{q}"


def tm_shape_url():
    """Austin comedy events generally — proves the API works even if he isn't booked."""
    key = os.environ.get("TICKETMASTER_API_KEY", "")
    if not key:
        return None
    q = urllib.parse.urlencode({
        "apikey": key,
        "classificationName": "Comedy",
        "city": "Austin",
        "stateCode": "TX",
        "size": 50,
    })
    return f"https://app.ticketmaster.com/discovery/v2/events.json?{q}"


BIT_APP_ID = os.environ.get("BANDSINTOWN_APP_ID", "keep-austin-kinane")

# The official tour feed. kylekinane.com is a GoHighLevel funnel page whose tour
# widget (upnex) reads a Supabase-backed JSON API. The widget's locationId and
# bearer token are embedded in the page, so resolving the feed is two steps:
# scrape the config, then call the API with it.
#
# Deliberately re-derived on every run rather than hardcoded — the token can
# rotate, and a hardcoded one would fail silently, which is the exact failure
# mode this project exists to prevent.
KK_SITE = "https://kylekinane.com/"
KK_API = "https://events-portal-sage.vercel.app/api/events/{loc}"


def resolve_kinane_api():
    status, body, _, _, _ = fetch(KK_SITE)
    if status != 200 or not body:
        return []
    loc = re.findall(r'locationId\s*[:=]\s*["\']([^"\']+)["\']', body)
    tok = re.findall(r'eventPortalToken\s*[:=]\s*["\']([^"\']+)["\']', body)
    if not (loc and tok):
        return []
    return [(KK_API.format(loc=loc[0]), {"Authorization": f"Bearer {tok[0]}",
                                         "Accept": "application/json"})]


SOURCES = [
    {
        "name": "kylekinane.com official tour feed",
        "kind": "api",
        "tier": "primary",
        "resolve": resolve_kinane_api,
        "note": "two-step: scrape widget config from the site, then call the API",
    },
    {
        "name": "Ticketmaster Discovery (keyword)",
        "kind": "api",
        "tier": "primary",
        "urls": [tm_url()],
        "needs": "TICKETMASTER_API_KEY (free: developer.ticketmaster.com)",
    },
    {
        "name": "Ticketmaster Discovery (shape check)",
        "kind": "api",
        "tier": "primary",
        "urls": [tm_shape_url()],
        "needs": "TICKETMASTER_API_KEY",
        "shape_only": True,  # presence of Kinane not expected or required
    },
    {
        "name": "Bandsintown",
        "kind": "api",
        "tier": "primary",
        "urls": [
            f"https://rest.bandsintown.com/artists/{urllib.parse.quote(ARTIST)}/events?app_id={BIT_APP_ID}",
            f"https://rest.bandsintown.com/artists/{urllib.parse.quote(ARTIST)}?app_id={BIT_APP_ID}",
        ],
    },
    {
        "name": "Cap City Comedy Club",
        "kind": "scrape",
        "tier": "venue",
        "urls": [
            "https://capcitycomedy.com/",
            "https://www.capcitycomedy.com/",
            "https://capcitycomedy.com/events",
            "https://capcitycomedy.com/shows",
        ],
    },
    {
        # Returns 429 to every request regardless of headers — edge bot protection,
        # not real rate limiting. We do not attempt to defeat it. Kept in the probe
        # so we notice if it ever opens up.
        "name": "Comedy Mothership",
        "kind": "scrape",
        "tier": "venue",
        "urls": ["https://comedymothership.com/"],
    },
    {
        # NOTE: moontowercomedy.com is a HugeDomains parking page (the domain is
        # for sale) — it returns the same 55KB shell for every path, which is why
        # an earlier probe read as "site exists, no calendar." The real festival
        # site is run by the Paramount. wp-json returns 522, so this is an HTML
        # scrape, and the lineup only exists seasonally (festival is in April).
        "name": "Moontower Comedy Festival",
        "kind": "scrape",
        "tier": "venue",
        "urls": [
            "https://moontowercomedyfestival.com/",
            "https://moontowercomedyfestival.com/lineup",
        ],
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# Fetch
# ──────────────────────────────────────────────────────────────────────────────

def fetch(url, extra_headers=None):
    """Return (status, body_text, elapsed, final_url, error)."""
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/json,application/xhtml+xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
    }
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            charset = r.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, errors="replace")
            return r.status, body, time.time() - t0, r.geturl(), None
    except urllib.error.HTTPError as e:
        return e.code, "", time.time() - t0, url, f"HTTP {e.code}"
    except Exception as e:
        return None, "", time.time() - t0, url, f"{type(e).__name__}: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# Analysis
# ──────────────────────────────────────────────────────────────────────────────

class TextExtractor(HTMLParser):
    """Visible text only — script/style stripped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            s = data.strip()
            if s:
                self.parts.append(s)

    def text(self):
        return " ".join(self.parts)


# Platform fingerprints → each implies a known-good parsing strategy.
PLATFORMS = [
    ("Squarespace",  r"static1\.squarespace\.com|squarespace\.com/universal", "try ?format=json on the events collection — returns clean JSON"),
    ("Next.js",      r"__NEXT_DATA__|self\.__next_f", "event data is likely in the __NEXT_DATA__ / RSC payload; also probe /_next/data/*.json"),
    ("Wix",          r"static\.parastorage\.com|wix-code", "Wix Events has a JSON API; find the XHR the page makes"),
    ("WordPress",    r"wp-content|wp-json", "try /wp-json/wp/v2/ — often exposes events as a post type"),
    ("Shopify",      r"cdn\.shopify\.com", "products.json may list ticketed shows"),
    ("Prekindle",    r"prekindle\.com", "Prekindle hosts the ticketing; scrape its listing page, not the venue's"),
    ("Eventbrite",   r"eventbrite\.com", "Eventbrite has a public API and predictable org pages"),
    ("SeatEngine",   r"seatengine\.com", "common for comedy clubs; listing pages are simple HTML"),
    ("Ticketmaster", r"ticketmaster\.com|livenation\.com", "covered by the Discovery API — skip the scrape entirely"),
    ("DICE",         r"dice\.fm", "DICE has a public event API"),
]

MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
DATE_RE = re.compile(
    rf"\b{MONTHS}[a-z]*\.?\s+\d{{1,2}}\b|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b",
    re.I,
)
LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


def find_ldjson_events(body):
    """schema.org Event objects — if present, parsing is nearly free."""
    events = []
    for blob in LDJSON_RE.findall(body):
        try:
            data = json.loads(blob.strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                t = node.get("@type", "")
                types = t if isinstance(t, list) else [t]
                if any("Event" in str(x) for x in types):
                    events.append({
                        "name": node.get("name"),
                        "startDate": node.get("startDate"),
                        "url": node.get("url"),
                    })
                stack.extend(node.values())
    return events


def analyze(body, is_json):
    a = {}
    a["bytes"] = len(body)

    if is_json:
        a["visible_chars"] = len(body)
        a["platform"] = None
        a["platform_hint"] = None
        a["ldjson_events"] = []
        a["dates"] = len(DATE_RE.findall(body))
        a["js_rendered"] = False
    else:
        try:
            ex = TextExtractor()
            ex.feed(body)
            text = ex.text()
        except Exception:
            text = re.sub(r"<[^>]+>", " ", body)
        a["visible_chars"] = len(text)
        a["dates"] = len(DATE_RE.findall(text))
        a["ldjson_events"] = find_ldjson_events(body)

        a["platform"], a["platform_hint"] = None, None
        for name, pat, hint in PLATFORMS:
            if re.search(pat, body, re.I):
                a["platform"], a["platform_hint"] = name, hint
                break

        # Big payload, almost no visible text → the calendar is drawn by JS.
        a["js_rendered"] = len(body) > 40_000 and len(text) < 1_500

    # ── Presence, carefully ──────────────────────────────────────────────────
    #
    # The naive version of this check ("does 'kinane' appear anywhere?") reported
    # FOUND HIM for kylekinane.com — 43 hits, every one of them his name in the
    # page title, og: tags, and branding. Of course his name is on his own
    # website. That is not a tour date.
    #
    # That is precisely the false-confidence failure this app exists to prevent,
    # reproduced inside the tool built to check for it. So presence now means
    # "the name appears near something date-shaped," and metadata hits are
    # counted separately and discounted.
    low = body.lower()
    head = low.split("</head>", 1)[0] if not is_json else ""
    a["meta_hits"] = head.count(NEEDLE)
    a["needle_hits"] = low.count(NEEDLE) - a["meta_hits"]

    a["contexts"] = []
    a["dated_hits"] = 0
    search_from = len(head)
    for m in re.finditer(NEEDLE, low[search_from:]):
        start = search_from + m.start()
        window = body[max(0, start - 250): start + 250]
        if DATE_RE.search(window):
            a["dated_hits"] += 1
        if len(a["contexts"]) < 5:
            a["contexts"].append(re.sub(r"\s+", " ", window[160:410]).strip())
    return a


def verdict(ok, a, shape_only):
    if not ok:
        return "UNREACHABLE", "fix the URL or the source is gone"
    if a["dated_hits"] > 0:
        return "FOUND HIM", f"{a['dated_hits']} name-near-a-date hit(s) — write the parser"
    if a["needle_hits"] > 0 and (a["ldjson_events"] or a["dates"] >= 8):
        return "PARSEABLE", "name present but not near a date — calendar readable, verify by hand"
    if a["needle_hits"] > 0:
        return "INCONCLUSIVE", "name appears but no dates nearby — likely branding, not a listing"
    if a["ldjson_events"]:
        return "PARSEABLE", f"{len(a['ldjson_events'])} schema.org Events — trivial to parse; he's just not booked"
    if a["dates"] >= 8:
        return "PARSEABLE", "a calendar is clearly visible; he's just not booked right now"
    if a["js_rendered"]:
        return "NEEDS API", "JS-rendered — find the XHR/JSON endpoint the page calls"
    if shape_only:
        return "INCONCLUSIVE", "shape check returned little; inspect the raw file"
    return "INCONCLUSIVE", "no calendar detected — inspect the raw file by hand"


# ──────────────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────────────

def row(label, value):
    print(f"   {label:<12} {value}")


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nKeep Austin Kinane — source viability probe")
    print(f"needle: {NEEDLE!r}   raw output: {RAW_DIR}\n")

    summary = []

    for src in SOURCES:
        print(f"\n── {src['name']} " + "─" * max(0, 58 - len(src["name"])))

        if src.get("resolve"):
            targets = src["resolve"]()
            if not targets:
                row("SKIPPED", "could not resolve the feed config from the site")
                summary.append((src["name"], "UNREACHABLE", "config scrape failed — the widget changed"))
                continue
        else:
            targets = [(u, None) for u in src.get("urls", []) if u]

        if not targets:
            row("SKIPPED", f"needs {src.get('needs', 'configuration')}")
            summary.append((src["name"], "SKIPPED", src.get("needs", "")))
            continue

        got = None
        for url, hdrs in targets:
            status, body, elapsed, final, err = fetch(url, hdrs)
            shown = re.sub(r"apikey=[^&]+", "apikey=***", url)
            if status == 200 and body:
                row("URL", f"{shown}")
                row("", f"{status} · {elapsed:.1f}s · {len(body):,} bytes")
                got = (url, body, final)
                break
            row("tried", f"{shown} → {err or status}")

        if not got:
            v, why = verdict(False, None, False)
            print(f"   → {v}: {why}")
            summary.append((src["name"], v, why))
            continue

        url, body, final = got
        is_json = body.lstrip()[:1] in "{["
        a = analyze(body, is_json)

        ext = "json" if is_json else "html"
        slug = re.sub(r"[^a-z0-9]+", "-", src["name"].lower()).strip("-")
        out = RAW_DIR / f"{slug}.{ext}"
        out.write_text(body, encoding="utf-8")

        if a["platform"]:
            row("Platform", f"{a['platform']}  → {a['platform_hint']}")
        if not is_json:
            row("Rendering", "JS-rendered (calendar not in HTML)" if a["js_rendered"]
                else f"server-side ({a['visible_chars']:,} chars visible)")
        if a["ldjson_events"]:
            row("JSON-LD", f"{len(a['ldjson_events'])} Event objects ← parse these")
            for e in a["ldjson_events"][:3]:
                row("", f"  · {e.get('name')} — {e.get('startDate')}")
        row("Dates seen", a["dates"])
        if a["meta_hits"]:
            row("", f"({a['meta_hits']} name hits in <head> — branding, discounted)")
        row("KINANE", f"{a['dated_hits']} near a date / {a['needle_hits']} in content"
            if a["needle_hits"] else "not found")
        for c in a["contexts"][:3]:
            row("", f"  …{c[:130]}…")
        row("Raw saved", out.name)

        v, why = verdict(True, a, src.get("shape_only", False))
        print(f"   → {v}: {why}")
        summary.append((src["name"], v, why))

    print("\n\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for name, v, why in summary:
        print(f"  {v:<14} {name}")
        if v in ("UNREACHABLE", "NEEDS API", "INCONCLUSIVE", "SKIPPED"):
            print(f"  {'':<14}   → {why}")

    blocked = [s for s in summary if s[1] in ("UNREACHABLE", "INCONCLUSIVE")]
    print(f"\n{len(summary) - len(blocked)}/{len(summary)} sources look workable.")
    if blocked:
        print("Inspect the raw/ files for the ones that don't before writing parsers.")
    print()

    (RAW_DIR.parent / "probe_summary.json").write_text(
        json.dumps([{"source": n, "verdict": v, "note": w} for n, v, w in summary], indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
