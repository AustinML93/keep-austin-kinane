"""
One poll cycle: fetch every source, store what they saw, judge their health,
then re-run the tiering rules over the whole event set.

The Austin rule has to run over ALL events, not per-source — whether a Dallas
show is superseded depends on an Austin show that may have come from a different
source entirely. So: gather first, then decide.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import db
from .sources.capcity import CapCity
from .sources.kylekinane import KyleKinaneOfficial
from .sources.moontower import MoontowerComedyFestival
from .sources.ticketmaster import Ticketmaster
from .tiering import apply_austin_rule, tier_for


def active_sources() -> list:
    """
    Ticketmaster only registers when a key is present.

    Registering it keyless would park a permanent 'config_failed' in the health
    readout, and a status line that always complains is a status line nobody
    reads — which is how a REAL warning gets ignored later.
    """
    # Moontower is seasonal — empty most of the year reads as 'dormant', not
    # broken. Bonus: its domain redirects to the Paramount's page, whose
    # year-round comedy card grid this parser reads, so it also watches
    # Austin's Paramount between festivals.
    sources = [KyleKinaneOfficial(), CapCity(), MoontowerComedyFestival()]
    tm = Ticketmaster()
    if tm.api_key:
        sources.append(tm)
    return sources


SOURCES = active_sources()


@dataclass
class PollReport:
    new_events: list[dict]
    health: list[dict]
    per_source: dict[str, str]


def poll_once(con=None, sources=None) -> PollReport:
    sources = sources or active_sources()
    new_events: list[dict] = []
    per_source: dict[str, str] = {}

    # FETCH EVERYTHING BEFORE WRITING ANYTHING. The first upsert opens SQLite's
    # write transaction, and holding it across the remaining sources' network
    # calls means a decision tap on a phone can sit behind a slow scraper until
    # it times out. Fetching takes as long as it takes; the write pass is
    # milliseconds.
    results = []
    for src in sources:
        try:
            result = src.fetch()
        except Exception as e:  # a source must never take down the cycle
            from .sources.base import FetchResult
            result = FetchResult(src.id, ok=False, error=f"{type(e).__name__}: {e}",
                                 error_kind="parse")
        results.append((src, result))

    own = con is None
    con = con or db.connect()
    try:
        for src, result in results:
            for ev in result.events:
                tier, dist = tier_for(ev.latitude, ev.longitude, ev.city, ev.address)
                if db.upsert_event(con, ev, tier, dist):
                    new_events.append({
                        "id": ev.dedupe_key(), "title": ev.title, "venue": ev.venue,
                        "city": ev.city, "starts_at": ev.starts_at, "tier": tier,
                        "distance_mi": dist, "ticket_url": ev.ticket_url,
                        "source": src.id,
                    })

            per_source[src.id] = db.record_health(con, src, result)

        # Tiering rules run across the merged set, not per source.
        events = db.upcoming(con)
        # "Bought" means either of them committed — the apology rule fires when
        # an Austin date lands after road-trip tickets are already in hand, and
        # decisions live in user_events, not on the event row.
        for e in events:
            e["tickets_bought"] = "got_tickets" in db.states_for_event(con, e["id"]).values()
        for e in apply_austin_rule(events):
            db.set_austin_status(con, e["id"], e.get("austin_status"))

        db.reconcile_listings(con)

        con.commit()
        return PollReport(new_events, db.source_health(con), per_source)
    finally:
        if own:
            con.close()
