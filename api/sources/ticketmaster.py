"""
Ticketmaster Discovery API — the independent read.

Not the primary source, and that's the point. The official feed and Cap City
already cover most of the ground; Ticketmaster's job is to see things they
can't, and to disagree with them when something's wrong. It's also the only
realistic line of sight into Comedy Mothership inventory, which blocks us
outright.

It carries one field nobody else does: sales.public.startDateTime — the actual
ON-SALE time. That's what the tier-2 decision deadline should be anchored to
instead of the "three weeks before the show" guess.

TWO QUERIES PER CYCLE, deliberately:

    presence      keyword="Kyle Kinane"    -> the shows we care about
    parseability  Austin comedy events     -> proof the API still works

Without the control query, a broken or throttled key would return zero Kinane
shows and look exactly like a quiet month. Two calls an hour is nothing.

Needs TICKETMASTER_API_KEY. Free from developer.ticketmaster.com — the Consumer
Key, not the Consumer Secret.
"""

from __future__ import annotations

import json
import os
import urllib.parse

from .base import Event, FetchResult, classify_http_error, http_get, mentions_kinane

BASE = "https://app.ticketmaster.com/discovery/v2/events.json"


class Ticketmaster:
    id = "ticketmaster"
    name = "Ticketmaster Discovery"
    kind = "api"
    seasonal = False

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("TICKETMASTER_API_KEY", "")

    def _url(self, **params) -> str:
        return f"{BASE}?{urllib.parse.urlencode({'apikey': self.api_key, **params})}"

    def fetch(self) -> FetchResult:
        if not self.api_key:
            return FetchResult(
                self.id, ok=False, error_kind="config",
                error="TICKETMASTER_API_KEY not set",
            )

        # 1. Presence.
        status, body, err = http_get(self._url(keyword="Kyle Kinane", size=100))
        if status != 200:
            # 401 means the key is wrong or not yet active — config, not silence.
            kind = "config" if status == 401 else classify_http_error(status, err)
            return FetchResult(self.id, ok=False, error=err or f"HTTP {status}", error_kind=kind)

        try:
            events = self._parse(body)
        except Exception as e:
            return FetchResult(self.id, ok=False, error=f"unexpected payload: {e}",
                               error_kind="parse")

        # 2. Parseability — a control query so an empty result can be trusted.
        c_status, c_body, _ = http_get(
            self._url(classificationName="Comedy", city="Austin", stateCode="TX", size=50))
        control = 0
        if c_status == 200:
            try:
                control = len(json.loads(c_body).get("_embedded", {}).get("events", []))
            except Exception:
                control = 0

        return FetchResult(
            self.id, ok=True, events=events,
            # total_seen is the CONTROL count, not the Kinane count. This source
            # is healthy when it can see Austin comedy, whether or not he's in it.
            total_seen=max(control, len(events)),
            note=f"{len(events)} Kinane; {control} Austin comedy events visible",
        )

    def _parse(self, body: str) -> list[Event]:
        data = json.loads(body)
        listed = data.get("_embedded", {}).get("events", [])
        out = []
        for e in listed:
            attractions = " ".join(
                a.get("name", "")
                for a in e.get("_embedded", {}).get("attractions", []) or []
            )
            if not mentions_kinane(e.get("name"), attractions):
                continue
            ev = self._to_event(e)
            if ev:
                out.append(ev)
        return out

    def _to_event(self, e: dict) -> Event | None:
        start = (e.get("dates") or {}).get("start") or {}
        local_date = start.get("localDate")
        if not local_date:
            return None
        local_time = (start.get("localTime") or "")[:5]
        starts_at = f"{local_date}T{local_time}" if local_time else local_date

        venues = (e.get("_embedded") or {}).get("venues") or []
        v = venues[0] if venues else {}
        loc = v.get("location") or {}
        city = (v.get("city") or {}).get("name")
        state = (v.get("state") or {}).get("stateCode")

        return Event(
            source_id=self.id,
            external_id=str(e.get("id") or f"{local_date}-{e.get('name','')}"),
            starts_at=starts_at,
            title=e.get("name"),
            venue=v.get("name"),
            city=f"{city}, {state}" if city and state else city,
            address=(v.get("address") or {}).get("line1"),
            latitude=_f(loc.get("latitude")),
            longitude=_f(loc.get("longitude")),
            # The field nobody else gives us — a real on-sale time for the
            # tier-2 decision deadline instead of a three-week guess.
            onsale_at=_onsale(e),
            ticket_url=e.get("url"),
            artist_confidence=0.95,
            raw=e,
        )


def _onsale(e: dict) -> str | None:
    """
    Ticketmaster's on-sale time, with the sentinel filtered out.

    Real payloads contain `1900-01-01T06:00:00Z` for events with no meaningful
    on-sale date (already on sale, or unknown). Observed on 2 of the 8 events in
    the first live pull.

    Passing that through would be genuinely bad: the tier-2 decision deadline is
    anchored to the on-sale time, so a 1900 deadline makes every escalation
    level overdue at once, and a road trip announced this morning would get
    "Last call" the same day.
    """
    raw = ((e.get("sales") or {}).get("public") or {}).get("startDateTime")
    if not raw or raw < "2000":
        return None
    return raw


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
