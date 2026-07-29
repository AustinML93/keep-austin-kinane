"""
The escalation ladder. The nagging is the product, so it gets real tests.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from api.nagger import (LOCAL, decision_deadline, due_level, tier1_due_at,
                        tier2_due_at)

# A Tuesday afternoon announcement — the realistic club-show shape.
ANNOUNCED = datetime(2026, 9, 1, 14, 0, tzinfo=LOCAL)
SHOW = datetime(2026, 10, 12, 19, 30, tzinfo=LOCAL)


def at(**kw):
    return ANNOUNCED + timedelta(**kw)


class TestTier1Ladder(unittest.TestCase):
    """Austin. The announcement IS the emergency."""

    def lv(self, now, sent=(), **kw):
        return due_level(now, ANNOUNCED, 1, list(sent), SHOW, **kw)

    def test_fires_immediately(self):
        self.assertEqual(self.lv(ANNOUNCED), 0)

    def test_does_not_repeat_a_sent_level(self):
        self.assertIsNone(self.lv(at(minutes=5), sent=[0]))

    def test_two_hour_nag(self):
        self.assertIsNone(self.lv(at(hours=1, minutes=59), sent=[0]))
        self.assertEqual(self.lv(at(hours=2), sent=[0]), 1)

    def test_six_hour_nag(self):
        self.assertIsNone(self.lv(at(hours=5), sent=[0, 1]))
        self.assertEqual(self.lv(at(hours=6), sent=[0, 1]), 2)

    def test_next_morning_is_at_least_twelve_hours_later(self):
        """
        An 8pm announcement must not produce a 9am nag 13 hours later and have
        that count as the whole overnight escalation.
        """
        late = datetime(2026, 9, 1, 20, 0, tzinfo=LOCAL)
        l3 = tier1_due_at(3, late)
        self.assertGreaterEqual((l3 - late).total_seconds() / 3600, 12)
        self.assertEqual(l3.hour, 9)

    def test_level_three_lands_the_next_morning(self):
        l3 = tier1_due_at(3, ANNOUNCED)
        self.assertEqual((l3.date() - ANNOUNCED.date()).days, 1)
        self.assertEqual(l3.hour, 9)

    def test_then_daily(self):
        l3, l4, l5 = (tier1_due_at(n, ANNOUNCED) for n in (3, 4, 5))
        self.assertEqual((l4 - l3).days, 1)
        self.assertEqual((l5 - l4).days, 1)

    def test_first_contact_is_always_the_news(self):
        """
        Polling is hourly, so a show can be hours old before we see it. The
        first thing a user ever hears must be THAT THE SHOW EXISTS — not the
        app complaining about silence they were never given a chance to break.
        """
        self.assertEqual(self.lv(at(days=3), sent=[]), 0)

    def test_catch_up_jumps_instead_of_bursting(self):
        """
        Poller was down three days. Do NOT fire five notifications in a row —
        jump to the highest level currently due.
        """
        lv = self.lv(at(days=3, hours=2), sent=[0])
        self.assertIsNotNone(lv)
        self.assertGreater(lv, 3)

    def test_stops_after_the_show(self):
        self.assertIsNone(due_level(SHOW + timedelta(days=1), ANNOUNCED, 1, [], SHOW))


class TestTier2Ladder(unittest.TestCase):
    """Road trip. Anchored to the decision deadline, not the announcement."""

    def test_heads_up_fires_immediately(self):
        self.assertEqual(due_level(ANNOUNCED, ANNOUNCED, 2, [], SHOW), 0)

    def test_reminders_are_anchored_to_the_deadline_not_the_announcement(self):
        deadline = SHOW - timedelta(days=21)
        self.assertEqual(tier2_due_at(1, ANNOUNCED, deadline), deadline - timedelta(days=7))
        self.assertEqual(tier2_due_at(2, ANNOUNCED, deadline), deadline - timedelta(days=2))
        self.assertEqual(tier2_due_at(3, ANNOUNCED, deadline), deadline)

    def test_onsale_date_overrides_the_default_deadline(self):
        onsale = ANNOUNCED + timedelta(days=3)
        lv = due_level(onsale, ANNOUNCED, 2, [0, 1, 2], SHOW, onsale_at=onsale)
        self.assertEqual(lv, 3)

    def test_a_reminder_never_predates_the_announcement(self):
        """A show announced 3 days before the deadline can't nag 7 days out."""
        late_news = SHOW - timedelta(days=22)
        deadline = SHOW - timedelta(days=21)
        self.assertGreaterEqual(tier2_due_at(1, late_news, deadline), late_news)

    def test_superseded_road_trip_gets_the_heads_up_then_shuts_up(self):
        """He's coming to Austin too. Say so once, then stop."""
        self.assertEqual(
            due_level(ANNOUNCED, ANNOUNCED, 2, [], SHOW, austin_status="superseded"), 0)
        self.assertIsNone(
            due_level(SHOW - timedelta(days=1), ANNOUNCED, 2, [0], SHOW,
                      austin_status="superseded"))

    def test_unknown_austin_status_still_escalates(self):
        """No Austin date announced — this might be the only shot. Keep nagging."""
        lv = due_level(SHOW - timedelta(days=21), ANNOUNCED, 2, [0], SHOW,
                       austin_status="unknown")
        self.assertIsNotNone(lv)


class TestBadOnsaleDates(unittest.TestCase):
    """
    Ticketmaster emits 1900-01-01 as a sentinel for "no meaningful on-sale
    date" — 2 of the 8 events in the first live pull had it. A deadline in the
    past makes every escalation level overdue at once, so a road trip announced
    this morning would get "Last call" before lunch.
    """

    def test_ancient_onsale_falls_back_to_the_lead_time_guess(self):
        sentinel = datetime(1900, 1, 1, 6, 0, tzinfo=LOCAL)
        self.assertEqual(decision_deadline(sentinel, SHOW, ANNOUNCED),
                         SHOW - timedelta(days=21))

    def test_onsale_after_the_show_is_ignored(self):
        self.assertEqual(decision_deadline(SHOW + timedelta(days=5), SHOW, ANNOUNCED),
                         SHOW - timedelta(days=21))

    def test_a_real_onsale_is_still_used(self):
        onsale = ANNOUNCED + timedelta(days=10)
        self.assertEqual(decision_deadline(onsale, SHOW, ANNOUNCED), onsale)

    def test_sentinel_does_not_blast_the_whole_ladder(self):
        """The bug this guards: L3 'last call' on announcement day."""
        sentinel = datetime(1900, 1, 1, 6, 0, tzinfo=LOCAL)
        self.assertEqual(due_level(ANNOUNCED, ANNOUNCED, 2, [], SHOW, onsale_at=sentinel), 0)
        self.assertIsNone(
            due_level(ANNOUNCED + timedelta(hours=1), ANNOUNCED, 2, [0], SHOW,
                      onsale_at=sentinel))


class TestTier3(unittest.TestCase):
    def test_daydreams_never_nag(self):
        self.assertIsNone(due_level(ANNOUNCED, ANNOUNCED, 3, [], SHOW))


class TestStopping(unittest.TestCase):
    """Decisions stop the ladder. Opening the app does not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KAK_DB"] = str(Path(self.tmp.name) / "t.sqlite3")
        import importlib

        from api import db as _db
        importlib.reload(_db)
        self.db = _db
        self.con = _db.connect()
        self.db.add_user(self.con, "mike", "Mike")
        self.db.add_user(self.con, "rob", "Rob")
        self.con.execute(
            """INSERT INTO events (id, venue, city, starts_at, tier, first_seen_at, last_seen_at)
               VALUES ('e1','Cap City','Austin, TX',?,1,?,?)""",
            (SHOW.isoformat(), ANNOUNCED.isoformat(), ANNOUNCED.isoformat()))
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def plan_ids(self, now):
        from api.nagger import plan
        return {(p.user_id, p.level) for p in plan(self.con, now)}

    def test_both_users_are_nagged_independently(self):
        """Rob must never depend on Mike's phone working."""
        self.assertEqual(self.plan_ids(ANNOUNCED), {("mike", 0), ("rob", 0)})

    def test_got_tickets_stops_only_that_user(self):
        self.db.set_state(self.con, "mike", "e1", "got_tickets")
        self.assertEqual(self.plan_ids(ANNOUNCED), {("rob", 0)})

    def test_cant_make_it_also_stops_the_nagging(self):
        """Peace with missing a show is a decision, and it must be respected."""
        self.db.set_state(self.con, "rob", "e1", "cant_make_it")
        self.assertEqual(self.plan_ids(ANNOUNCED), {("mike", 0)})

    def test_merely_seeing_it_does_not_stop_the_nagging(self):
        """Opening the app is not a decision. This is the whole design."""
        self.db.set_state(self.con, "mike", "e1", "seen")
        self.assertIn(("mike", 0), self.plan_ids(ANNOUNCED))


if __name__ == "__main__":
    unittest.main()
