"""
Cap City Comedy Club — the venue source most likely to catch the soul-crushing miss.

Runs on SeatEngine and embeds schema.org Event objects directly in the page HTML
(277 of them when probed 2026-07-29). No JS rendering, no API key, no fragile
CSS selectors — just JSON-LD.

This is the source where the parseability/presence split earns its keep. Kinane
isn't currently booked here, so this source correctly returns:

    events     = []      (he's not on the calendar)
    total_seen = 277     (the calendar is perfectly readable)

If total_seen ever drops to 0, the parser broke — and the health check will say
so rather than letting the app imply nobody's playing Cap City this year.
"""

from __future__ import annotations

from .base import (Event, FetchResult, classify_http_error, http_get,
                   ldjson_events, mentions_kinane)

URL = "https://capcitycomedy.com/"


class CapCity:
    id = "capcity"
    name = "Cap City Comedy Club"
    kind = "scrape"
    seasonal = False

    # Cap City is in Austin; the feed's events rarely carry coordinates, so we
    # stamp the venue's own location and let tiering do the distance math.
    VENUE_LAT = 30.4014
    VENUE_LON = -97.7244

    def fetch(self) -> FetchResult:
        status, body, err = http_get(URL)
        if status != 200 or not body:
            return FetchResult(
                self.id, ok=False, error=err or f"HTTP {status}",
                error_kind=classify_http_error(status, err),
            )

        return self.parse(body)

    def parse(self, body: str) -> FetchResult:
        """Split out from fetch() so tests run against recon/raw fixtures."""
        listed = ldjson_events(body)
        if not listed:
            # Page loaded but no JSON-LD at all — almost certainly a redesign.
            return FetchResult(
                self.id, ok=False, total_seen=0, error_kind="parse",
                error="page loaded but contained no schema.org Event objects — "
                      "SeatEngine markup changed?",
            )

        events = []
        for node in listed:
            name = node.get("name") or ""
            performer = node.get("performer")
            performer_name = ""
            if isinstance(performer, dict):
                performer_name = performer.get("name") or ""
            elif isinstance(performer, list):
                performer_name = " ".join(
                    p.get("name", "") for p in performer if isinstance(p, dict)
                )
            if not mentions_kinane(name, performer_name, node.get("description")):
                continue
            ev = self._to_event(node)
            if ev:
                events.append(ev)

        return FetchResult(
            self.id, ok=True, events=events, total_seen=len(listed),
            note=f"{len(events)} Kinane of {len(listed)} listed",
        )

    def _to_event(self, node: dict) -> Event | None:
        start = node.get("startDate")
        if not start:
            return None
        # schema.org gives us "2027-11-11T01:30:00Z" — keep the date+time prefix.
        starts_at = start[:16] if len(start) >= 16 else start[:10]

        loc = node.get("location") or {}
        venue = loc.get("name") if isinstance(loc, dict) else None

        return Event(
            source_id=self.id,
            external_id=str(node.get("url") or f"{start}-{node.get('name','')}"),
            starts_at=starts_at,
            title=node.get("name"),
            venue=venue or "Cap City Comedy Club",
            city="Austin, TX",
            latitude=self.VENUE_LAT,
            longitude=self.VENUE_LON,
            ticket_url=node.get("url"),
            # A name match on a club listing, not his own feed. Still plenty
            # confident — but labelled so the UI can say "pretty sure".
            artist_confidence=0.9,
            raw=node,
        )
