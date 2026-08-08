"""
The poll cycle's wiring — the seams between fetch, store, and the tiering rules.

The unit tests in test_tiering prove apply_austin_rule works when handed a
tickets_bought flag; these prove the poll cycle actually HANDS it one. The
apology path shipped unreachable once already: the rule read
e.get("tickets_bought") and no event row ever carried that field, because
decisions live in user_events. An annotation rule nobody feeds is just a
comment with extra steps.
"""

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from api.sources.base import Event, FetchResult

AUSTIN = dict(venue="Paramount", city="Austin, TX", latitude=30.2672, longitude=-97.7431)
DALLAS = dict(venue="Majestic", city="Dallas, TX", latitude=32.7906, longitude=-96.7996)


class FakeSource:
    def __init__(self, events=None, boom=False, sid="feed"):
        self.id, self.name, self.kind, self.seasonal = sid, sid, "api", False
        self._events = events or []
        self._boom = boom

    def fetch(self):
        if self._boom:
            raise RuntimeError("scraper exploded")
        return FetchResult(self.id, ok=True, events=self._events,
                           total_seen=len(self._events))


def ev(external_id, days_out, **place):
    starts = (date.today() + timedelta(days=days_out)).isoformat() + "T20:00"
    return Event(source_id="feed", external_id=external_id, starts_at=starts, **place)


class TestPollWiring(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KAK_DB"] = str(Path(self.tmp.name) / "t.sqlite3")
        import importlib

        from api import db as _db
        importlib.reload(_db)
        self.db = _db
        self.con = _db.connect()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def poll(self, *sources):
        from api.poll import poll_once
        return poll_once(con=self.con, sources=list(sources))

    def austin_status(self, eid):
        return self.con.execute(
            "SELECT austin_status FROM events WHERE id=?", (eid,)).fetchone()["austin_status"]

    def test_apology_fires_when_austin_lands_after_tickets_bought(self):
        """The whole joke, end to end: buy Dallas, then Austin appears."""
        dallas = ev("d1", 40, **DALLAS)
        self.poll(FakeSource([dallas]))
        self.assertEqual(self.austin_status(dallas.dedupe_key()), "unknown")

        self.db.add_user(self.con, "mike", "Mike")
        self.db.set_state(self.con, "mike", dallas.dedupe_key(), "got_tickets")

        austin = ev("a1", 55, **AUSTIN)
        self.poll(FakeSource([dallas, austin]))
        self.assertEqual(self.austin_status(dallas.dedupe_key()), "owed_an_apology")

    def test_without_tickets_the_same_shape_is_merely_superseded(self):
        dallas, austin = ev("d1", 40, **DALLAS), ev("a1", 55, **AUSTIN)
        self.poll(FakeSource([dallas, austin]))
        self.assertEqual(self.austin_status(dallas.dedupe_key()), "superseded")

    def test_an_exploding_source_does_not_take_down_the_cycle(self):
        report = self.poll(FakeSource(boom=True, sid="bad"),
                           FakeSource([ev("a1", 30, **AUSTIN)], sid="good"))
        self.assertEqual(report.per_source["good"], "ok")
        self.assertEqual(report.per_source["bad"], "degraded")
        self.assertEqual(len(report.new_events), 1)

    def test_poll_runs_the_listing_reconciler(self):
        """poll_once owns the whole judge-the-world pass, vanish detection
        included — a reconciler nobody calls is the unreachable-apology bug
        with a different name."""
        dallas = ev("d1", 40, **DALLAS)
        self.poll(FakeSource([dallas]))
        # Backdate the sighting and age the source's success past the window,
        # then poll again with the show absent from the feed.
        old = "2020-01-01T00:00:00+00:00"
        self.con.execute("UPDATE event_sources SET last_seen_at=?", (old,))
        self.con.commit()
        self.poll(FakeSource([], sid="feed"))
        row = self.con.execute(
            "SELECT listing_status FROM events WHERE id=?",
            (dallas.dedupe_key(),)).fetchone()
        self.assertEqual(row["listing_status"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
