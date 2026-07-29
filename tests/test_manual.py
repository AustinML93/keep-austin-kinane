"""
Hand-typed shows.

This is a first-class source, not a workaround. Discovery was always social —
"sometimes it's my friend, and sometimes it's just someone that knows we love
him" — and it's the only thing that will ever catch a Comedy Mothership booking
(their site blocks us, and no aggregator carries them) or a surprise drop-in.
"""

import os
import tempfile
import unittest
from pathlib import Path

from api.venues import locate


class TestVenueLookup(unittest.TestCase):

    def test_finds_the_mothership_however_you_type_it(self):
        """Somebody entering a show from a text message won't be precise."""
        for typed in ("Comedy Mothership", "comedy mothership", "the Mothership",
                      "MOTHERSHIP", "Mothership "):
            lat, lon, city = locate(typed)
            self.assertIsNotNone(lat, f"failed to locate {typed!r}")
            self.assertEqual(city, "Austin, TX")

    def test_known_venues_canonicalise(self):
        """
        The dedupe key contains the venue name, so every spelling of a known
        venue must collapse to one — otherwise a show typed in as "the
        Mothership" and the same show from a real source are two rows, one of
        them permanently unacknowledged and still nagging.
        """
        from api.venues import canonical
        names = {canonical(v) for v in
                 ("the Mothership", "Comedy Mothership", "MOTHERSHIP", "mothership")}
        self.assertEqual(len(names), 1, names)

    def test_unknown_venues_keep_what_was_typed(self):
        from api.venues import canonical
        self.assertEqual(canonical("Some New Room"), "Some New Room")

    def test_falls_back_to_the_city_when_the_venue_is_unknown(self):
        lat, lon, city = locate("Some Bar That Just Opened", "Dallas")
        self.assertIsNotNone(lat)
        self.assertEqual(city, "Dallas, TX")

    def test_unknown_place_returns_no_coordinates(self):
        """
        Better to land in tier 3 than to guess it's local. A daydream is a safer
        wrong answer than a false alarm about a show nowhere near you.
        """
        lat, lon, _ = locate("The Laughatorium", "Ulaanbaatar")
        self.assertIsNone(lat)


class TestManualEvents(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KAK_DB"] = str(Path(self.tmp.name) / "t.sqlite3")
        import importlib

        from api import db as _db
        importlib.reload(_db)
        self.db = _db
        self.con = _db.connect()
        self.db.add_user(self.con, "mike", "Mike")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def add(self, **kw):
        args = dict(added_by="mike", venue="Comedy Mothership", city="Austin, TX",
                    date="2026-11-14", time_s="20:00")
        args.update(kw)
        return self.db.add_manual_event(self.con, **args)

    def test_a_mothership_show_lands_in_tier_1(self):
        """The venue we cannot see any other way. This is the whole point."""
        eid, is_new = self.add()
        self.assertTrue(is_new)
        row = self.con.execute("SELECT tier, distance_mi FROM events WHERE id=?", (eid,)).fetchone()
        self.assertEqual(row["tier"], 1)
        self.assertLess(row["distance_mi"], 5)

    def test_it_nags_like_any_other_show(self):
        """A hand-typed show is a real show. It gets the full ladder."""
        from datetime import datetime

        from api.nagger import LOCAL, plan
        eid, _ = self.add()
        plans = plan(self.con, datetime.now(LOCAL))
        self.assertIn((("mike"), eid, 0), [(p.user_id, p.event_id, p.level) for p in plans])

    def test_different_spellings_are_the_same_show(self):
        """Rob types 'the Mothership'; Mike types 'Comedy Mothership'. One row."""
        eid1, new1 = self.add(venue="the Mothership")
        eid2, new2 = self.add(venue="Comedy Mothership")
        self.assertEqual(eid1, eid2)
        self.assertTrue(new1)
        self.assertFalse(new2)
        self.assertEqual(self.con.execute("SELECT COUNT(*) c FROM events").fetchone()["c"], 1)

    def test_a_real_source_confirming_it_merges_rather_than_duplicating(self):
        """Typed in from a text, then the official feed catches up. One row."""
        from api.sources.base import Event
        eid, _ = self.add(venue="the Mothership")
        ev = Event(source_id="kylekinane", external_id="x", starts_at="2026-11-14T20:00",
                   venue="Comedy Mothership", city="Austin, TX",
                   latitude=30.2672, longitude=-97.7395,
                   ticket_url="https://example.com/tickets")
        self.assertFalse(self.db.upsert_event(self.con, ev, 1, 0.5))  # not new

        rows = self.con.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        self.assertEqual(rows, 1)
        srcs = {r["source_id"] for r in self.con.execute(
            "SELECT source_id FROM event_sources WHERE event_id=?", (eid,))}
        self.assertEqual(srcs, {"manual", "kylekinane"})

    def test_confirmed_shows_cannot_be_deleted_as_manual(self):
        """
        Deleting your hand-typed note must not delete a show a real source has
        since confirmed — that would be a self-inflicted miss.
        """
        from api.sources.base import Event
        eid, _ = self.add()
        self.db.upsert_event(self.con, Event(
            source_id="ticketmaster", external_id="y", starts_at="2026-11-14T20:00",
            venue="Comedy Mothership", city="Austin, TX"), 1, 0.5)

        self.assertFalse(self.db.delete_manual_event(self.con, eid))
        self.assertEqual(self.con.execute("SELECT COUNT(*) c FROM events").fetchone()["c"], 1)

    def test_a_purely_manual_show_can_be_deleted(self):
        eid, _ = self.add()
        self.assertTrue(self.db.delete_manual_event(self.con, eid))
        self.assertEqual(self.con.execute("SELECT COUNT(*) c FROM events").fetchone()["c"], 0)

    def test_manual_source_never_counts_as_blind(self):
        """A human isn't a scraper and can't 'go dark'."""
        self.add()
        manual = [s for s in self.db.source_health(self.con) if s["id"] == "manual"][0]
        self.assertEqual(manual["health"], "ok")
        self.assertEqual(manual["kind"], "manual")

    def test_a_dallas_show_lands_in_tier_2(self):
        eid, _ = self.add(venue="Majestic Theatre", city="Dallas, TX")
        self.assertEqual(
            self.con.execute("SELECT tier FROM events WHERE id=?", (eid,)).fetchone()["tier"], 2)


if __name__ == "__main__":
    unittest.main()
