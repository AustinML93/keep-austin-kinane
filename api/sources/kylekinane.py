"""
The official tour feed — our primary source.

kylekinane.com is a GoHighLevel funnel page. Its tour widget (upnex) reads a
Supabase-backed JSON API, and the widget's locationId + bearer token are embedded
in the page HTML.

So this is a TWO-STEP fetch, and it must stay that way:

    1. GET the site, scrape locationId + eventPortalToken
    2. GET the API with that token

Never hardcode the token. When it rotates, a hardcoded one starts returning 401
and — if we weren't careful — the app would report "no upcoming shows" forever.
That is the exact failure this project exists to prevent, so a failed config
scrape returns error_kind="config" and is surfaced as its own health state.

Payload shape (verified 2026-07-29, 78 top-level events):
    startDate startTime timezone status presaleActive
    venue displayVenue address city displayCity latitude longitude
    ticketLinks[]  {linkType, ticketLink, buttonText}
    showtimes[]    {date, time, utcTimestamp, ticketLinks[]}

A top-level entry is one BOOKING, not one show. A club run shows up as a
single entry whose showtimes[] span several dates (Helium Comedy Club,
2026-01-08 through 2026-01-10, five showtimes across three nights) — so we
expand every showtime into its own Event rather than taking showtimes[0].
78 top-level entries currently expand to 131 shows.
"""

from __future__ import annotations

import json
import re

from .base import Event, FetchResult, classify_http_error, http_get

SITE = "https://kylekinane.com/"
API = "https://events-portal-sage.vercel.app/api/events/{loc}"

LOC_RE = re.compile(r'locationId\s*[:=]\s*["\']([^"\']+)["\']')
TOK_RE = re.compile(r'eventPortalToken\s*[:=]\s*["\']([^"\']+)["\']')


class KyleKinaneOfficial:
    id = "kylekinane"
    name = "kylekinane.com official tour feed"
    kind = "api"
    seasonal = False

    def resolve_config(self) -> tuple[str, str] | None:
        status, body, err = http_get(SITE)
        if status != 200 or not body:
            return None
        loc = LOC_RE.search(body)
        tok = TOK_RE.search(body)
        if not (loc and tok):
            return None
        return loc.group(1), tok.group(1)

    def fetch(self) -> FetchResult:
        cfg = self.resolve_config()
        if not cfg:
            return FetchResult(
                self.id, ok=False, error_kind="config",
                error="could not scrape locationId/eventPortalToken from kylekinane.com — "
                      "the tour widget changed or the site is down",
            )
        loc, tok = cfg

        status, body, err = http_get(
            API.format(loc=loc),
            {"Authorization": f"Bearer {tok}", "Accept": "application/json"},
        )
        if status != 200 or not body:
            # A 401 here means the token rotated between the scrape and the call,
            # or the vendor changed auth. Either way it's config, not "no shows".
            kind = "config" if status in (401, 403) else classify_http_error(status, err)
            return FetchResult(self.id, ok=False, error=err or f"HTTP {status}", error_kind=kind)

        return self.parse(body)

    def parse(self, body: str) -> FetchResult:
        """Split out from fetch() so tests run against recon/raw fixtures."""
        try:
            payload = json.loads(body)
            raw_events = payload["data"]["events"]
        except Exception as e:
            return FetchResult(self.id, ok=False, error=f"unexpected payload: {e}", error_kind="parse")

        events = [e for r in raw_events for e in self._to_events(r)]
        return FetchResult(
            self.id, ok=True, events=events, total_seen=len(raw_events),
            note=f"{len(events)} events from {len(raw_events)} listed",
        )

    def _to_events(self, r: dict) -> list[Event]:
        date = r.get("startDate")
        if not date:
            return []

        # A "showtime" is an extra DATE, not just an extra time of night — a
        # club run like Helium's Jan 8-10 comes back as ONE top-level entry
        # with five showtimes spanning three nights. Collapsing to showtimes[0]
        # (the old behavior) silently deletes whole nights of a run; the fix is
        # to emit one Event per showtime. Entries with no showtimes array at
        # all fall back to the top-level startDate/startTime, same as before.
        showtimes = r.get("showtimes") or []
        if not showtimes:
            return [self._build(r, date, r.get("startTime"), r.get("ticketLinks") or [])]

        return [
            self._build(r, st.get("date") or date, st.get("time"),
                        st.get("ticketLinks") or r.get("ticketLinks") or [])
            for st in showtimes
        ]

    def _build(self, r: dict, date: str, time_raw: str | None, links: list[dict]) -> Event:
        time_s = (time_raw or "")[:5]
        starts_at = f"{date}T{time_s}" if time_s else date

        ticket_url = next(
            (l.get("ticketLink") for l in links
             if l.get("ticketLink") and "ticketlink.com" not in l["ticketLink"]),
            None,
        )

        # Every showtime shares the parent's r["id"], so the date+time has to
        # be baked in here — otherwise five nights of the same club run
        # collide onto one external_id and four of them vanish on upsert.
        parent_id = r.get("id") or f"{date}-{r.get('venue', '')}"
        external_id = f"{parent_id}-{date}-{time_s or 'na'}"

        return Event(
            source_id=self.id,
            external_id=external_id,
            starts_at=starts_at,
            title=r.get("displayVenue") or r.get("venue"),
            venue=r.get("displayVenue") or r.get("venue"),
            city=r.get("displayCity") or r.get("city"),
            address=r.get("address"),
            latitude=_f(r.get("latitude")),
            longitude=_f(r.get("longitude")),
            ticket_url=ticket_url,
            presale_active=bool(r.get("presaleActive")),
            artist_confidence=1.0,   # it's his own tour feed
            raw=r,
        )


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
