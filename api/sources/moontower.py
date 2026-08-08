"""
Moontower Comedy Festival — SEASONAL. Being empty most of the year is correct.

moontowercomedy.com is a HugeDomains parking page (the domain is for sale) — it
returns the same shell for every path, which is why an earlier probe read as
"site exists, no calendar." The real festival is `moontowercomedyfestival.com`,
which redirects to the Paramount Theatre's own site (`austintheatre.org`) and is
plain WordPress. `wp-json` 522s, so there's no REST shortcut — this is HTML.

WHAT'S ACTUALLY ON THE PAGE (verified against both recon/raw/moontower-*.html
fixtures, captured 2026-07-29): the festival itself is announced only as a date
range — "April 8-19, 2026" — with no lineup yet. That banner names no performer
and carries no per-show data, so it is deliberately NOT turned into an Event:
counting it toward total_seen would inflate parseability without giving a real
signal, and there is nothing in it to run the Kinane match against anyway.

The one genuinely enumerable, dated listing on the page is a "Here All Year"
card grid — the Paramount's own year-round comedy/podcast booking, separate
from festival week. Each card gives a performer name, a ticket link, and a date
string, so that grid is what total_seen counts. It is real parseable content,
just not the festival lineup itself — worth knowing if total_seen ever looks
"too healthy" for a source that's supposed to go quiet most of the year.

KNOWN LIMITATION — no fixture exists for the in-season lineup page. When the
April lineup actually publishes, this parser has never been checked against
it and the card-grid markup may not be what a lineup page uses. Re-probe in
March/April and add a real fixture before trusting this source during festival
season; until then, treat "dormant" as the well-tested state and "ok" with a
lineup as unverified.

DATE GRANULARITY: card dates never carry a year (the site assumes "this
season"). We infer the nearest occurrence at or after today, which is the
standard "upcoming events" heuristic — good enough for tiering and dedupe, but
if this source is ever polled against a card more than ~11 months stale the
inferred year will be wrong. That's an honest tradeoff, not a bug: the
alternative is fabricating a year with no more basis than this one.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .base import Event, FetchResult, classify_http_error, http_get, mentions_kinane

URL = "https://moontowercomedyfestival.com/"

# Confirms we actually landed on a Moontower/Paramount page rather than a
# parked domain, a 404, or an unrelated redirect target. Cheap and specific
# enough — "moontower" essentially never appears by accident.
PAGE_MARKER_RE = re.compile(r"moontower", re.I)

# One card per "Here All Year" listing: a ticket link, a date/time subheading,
# and a name. Matched loosely (DOTALL, non-greedy) because the surrounding
# markup is deep WordPress/Tailwind soup we don't want to depend on exactly.
CARD_RE = re.compile(
    r'<a class="links__overlay" href="(?P<url>[^"]+)">.*?'
    r'styles__subheading--small">\s*(?P<when>.*?)\s*</div>.*?'
    r'styles__h-base[^"]*">(?P<name>[^<]+)</div>',
    re.S,
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

WHEN_DATE_RE = re.compile(r"([A-Za-z]{3,9})\s+0?(\d{1,2})")
WHEN_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([ap]m)", re.I)


class MoontowerComedyFestival:
    id = "moontower"
    name = "Moontower Comedy Festival"
    kind = "scrape"
    seasonal = True

    def fetch(self) -> FetchResult:
        status, body, err = http_get(URL)
        if status != 200 or not body:
            return FetchResult(
                self.id, ok=False, error=err or f"HTTP {status}",
                error_kind=classify_http_error(status, err),
            )
        return self.parse(body)

    def parse(self, body: str, today: date | None = None) -> FetchResult:
        """
        Split out from fetch() so tests run against recon/raw fixtures.

        `today` is injectable so the year-inference heuristic below is
        deterministic in tests instead of depending on the clock.
        """
        if not PAGE_MARKER_RE.search(body):
            # Not a redesign we can characterize — could be a parked page, a
            # 404 rendered as 200, or a wholesale site change. Either way we
            # did not land on Moontower, and reporting ok=True here would
            # print "quiet season" over what is actually a broken parser.
            return FetchResult(
                self.id, ok=False, total_seen=0, error_kind="parse",
                error="page loaded but did not look like Moontower/Paramount — "
                      "wrong URL, redirect changed, or a site redesign",
            )

        cards = CARD_RE.findall(body)

        events = []
        for url, when, name in cards:
            if not mentions_kinane(name):
                continue
            ev = self._to_event(url, when, name, today)
            if ev:
                events.append(ev)

        # total_seen is the year-round card grid, not the (currently unpublished)
        # festival lineup — see module docstring. Zero is the expected reading
        # for most of the year; seasonal=True is what keeps that from reading
        # as "suspicious" in db.record_health (it becomes "dormant" instead).
        return FetchResult(
            self.id, ok=True, events=events, total_seen=len(cards),
            note=f"{len(events)} Kinane of {len(cards)} listed" if cards
                 else "no dated listings on the page — expected off-season",
        )

    def _to_event(self, url: str, when: str, name: str, today: date | None) -> Event | None:
        starts_at = _parse_when(when, today)
        if not starts_at:
            return None
        return Event(
            source_id=self.id,
            external_id=url or f"{starts_at}-{name}",
            starts_at=starts_at,
            title=name.strip(),
            # The page doesn't disambiguate Paramount proper from its sibling
            # room Stateside per-card, so we stamp the operator, not a guess.
            venue="Paramount Theatre",
            city="Austin, TX",
            ticket_url=url or None,
            # A name match on a venue's own year-round listing, not the
            # festival lineup or his own feed — comparable confidence to the
            # Cap City club-listing match.
            artist_confidence=0.85,
            raw={"name": name, "when": when, "url": url},
        )


def _parse_when(when: str, today: date | None = None) -> str | None:
    text = when.replace("&nbsp;", " ").replace("&#183;", " ")
    m = WHEN_DATE_RE.search(text)
    if not m:
        return None
    month = MONTHS.get(m.group(1)[:3].lower())
    if not month:
        return None
    try:
        day = int(m.group(2))
    except ValueError:
        return None

    year = _infer_year(month, day, today)
    try:
        date_str = date(year, month, day).isoformat()
    except ValueError:
        return None

    t = WHEN_TIME_RE.search(text)
    if not t:
        return date_str
    hour = int(t.group(1)) % 12
    if t.group(3).lower() == "pm":
        hour += 12
    return f"{date_str}T{hour:02d}:{t.group(2)}"


def _infer_year(month: int, day: int, today: date | None = None) -> int:
    """
    Card dates never carry a year — the page assumes "this season." Pick the
    nearest occurrence at or after today (minus a few days' grace, so a card
    scraped during the show's own week doesn't roll forward a year).
    """
    today = today or date.today()
    for candidate_year in (today.year, today.year + 1):
        try:
            candidate = date(candidate_year, month, day)
        except ValueError:
            continue
        if candidate >= today - timedelta(days=3):
            return candidate_year
    return today.year + 1
