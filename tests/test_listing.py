"""
Vanish detection — noticing a show its sources quietly stopped listing.

A cancelled show doesn't announce itself; it just disappears from the feed.
The dangerous failure here is the opposite one: a broken parser must never be
allowed to condemn its whole catalogue as cancelled, because that converts a
parse bug into "the app said the show was off". Every test below is one side
of that line.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.sources.base import FetchResult


class FakeSource:
    def __init__(self, sid="feed", seasonal=False):
        self.id, self.name, self.kind, self.seasonal = sid, sid, "scrape", seasonal


def iso(dt):
    return dt.isoformat(timespec="seconds")


class ListingBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KAK_DB"] = str(Path(self.tmp.name) / "t.sqlite3")
        import importlib

        from api import db as _db
        importlib.reload(_db)
        self.db = _db
        self.con = _db.connect()
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def add_event(self, eid="e1", sources=("feed",), seen_ago_h=0.0, tier=1):
        starts = (self.now + timedelta(days=30)).date().isoformat()
        self.con.execute(
            """INSERT INTO events (id, venue, city, starts_at, tier,
                                   first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?)""",
            (eid, "Cap City", "Austin, TX", starts, tier, iso(self.now), iso(self.now)))
        for s in sources:
            self.con.execute(
                """INSERT INTO event_sources (event_id, source_id, external_id, raw, last_seen_at)
                   VALUES (?,?,?,?,?)""",
                (eid, s, "x", "{}", iso(self.now - timedelta(hours=seen_ago_h))))
        self.con.commit()

    def healthy_poll(self, sid="feed"):
        self.db.record_health(self.con, FakeSource(sid),
                              FetchResult(sid, ok=True, total_seen=50))

    def status(self, eid="e1"):
        return self.con.execute(
            "SELECT listing_status FROM events WHERE id=?", (eid,)).fetchone()["listing_status"]


class TestListingReconciliation(ListingBase):

    def test_vanished_from_a_healthy_feed_goes_unconfirmed(self):
        self.add_event(seen_ago_h=72)
        self.healthy_poll()
        self.db.reconcile_listings(self.con)
        self.assertEqual(self.status(), "unconfirmed")

    def test_recently_seen_stays_listed(self):
        self.add_event(seen_ago_h=1)
        self.healthy_poll()
        self.db.reconcile_listings(self.con)
        self.assertEqual(self.status(), "listed")

    def test_a_broken_source_cannot_condemn_its_catalogue(self):
        """
        The feed goes suspicious (0-of-0 where it used to list 50). Its events
        vanished because the PARSER broke, not because Kinane cancelled a tour.
        A source that can't see anything can't vouch for anything — either way.
        """
        self.add_event(seen_ago_h=72)
        self.healthy_poll()
        self.db.record_health(self.con, FakeSource(),
                              FetchResult("feed", ok=True, total_seen=0))
        self.db.reconcile_listings(self.con)
        self.assertEqual(self.status(), "listed")

    def test_a_down_source_cannot_condemn_either(self):
        self.add_event(seen_ago_h=72)
        for _ in range(3):
            self.db.record_health(self.con, FakeSource(),
                                  FetchResult("feed", ok=False, error="boom",
                                              error_kind="network"))
        self.db.reconcile_listings(self.con)
        self.assertEqual(self.status(), "listed")

    def test_manual_notes_do_not_expire(self):
        """A human's note doesn't go stale because a scraper never mentioned it."""
        self.add_event(sources=("manual",), seen_ago_h=500)
        self.db.reconcile_listings(self.con)
        self.assertEqual(self.status(), "listed")

    def test_one_healthy_witness_keeps_it_listed(self):
        """Feed A dropped it, feed B is down: B's silence is ignorance, not
        evidence, so the show keeps the benefit of the doubt."""
        self.add_event(sources=("feed", "other"), seen_ago_h=72)
        self.healthy_poll("feed")
        for _ in range(3):
            self.db.record_health(self.con, FakeSource("other"),
                                  FetchResult("other", ok=False, error="x",
                                              error_kind="network"))
        self.db.reconcile_listings(self.con)
        self.assertEqual(self.status(), "listed")

    def test_reappearing_heals_the_flag(self):
        self.add_event(seen_ago_h=72)
        self.healthy_poll()
        self.db.reconcile_listings(self.con)
        self.assertEqual(self.status(), "unconfirmed")

        self.con.execute("UPDATE event_sources SET last_seen_at=? WHERE event_id='e1'",
                         (iso(self.now),))
        self.con.commit()
        self.db.reconcile_listings(self.con)
        self.assertEqual(self.status(), "listed")


class TestUnconfirmedPausesTheLadder(ListingBase):

    def plan_count(self):
        from api.nagger import plan
        return len(plan(self.con, datetime.now(timezone.utc).astimezone()))

    def test_unconfirmed_pauses_and_relisting_resumes(self):
        """
        A pause, not a decision: nothing here writes user state, so the ladder
        picks up where it left off the moment any source lists the show again.
        """
        self.db.add_user(self.con, "mike", "Mike")
        self.add_event(seen_ago_h=72)
        self.healthy_poll()
        self.assertEqual(self.plan_count(), 1)

        self.db.reconcile_listings(self.con)
        self.assertEqual(self.status(), "unconfirmed")
        self.assertEqual(self.plan_count(), 0)

        self.con.execute("UPDATE event_sources SET last_seen_at=? WHERE event_id='e1'",
                         (iso(self.now),))
        self.con.commit()
        self.db.reconcile_listings(self.con)
        self.assertEqual(self.plan_count(), 1)


if __name__ == "__main__":
    unittest.main()
