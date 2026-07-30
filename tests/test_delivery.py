"""
Delivery reconciliation.

A push can be accepted by the push service and never appear on a phone — one
vanished entirely while a service worker was being swapped during a deploy, with
sent=1 recorded and nothing on the lock screen. Without reconciliation that's a
silent miss, which is precisely what this app exists to prevent.

So: 'sent' and 'delivered' are different facts, and the gap between them gets
one retry.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from api.nagger import LOCAL

SHOW = datetime(2026, 10, 12, 19, 30, tzinfo=LOCAL)


class TestReconciliation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KAK_DB"] = str(Path(self.tmp.name) / "t.sqlite3")
        import importlib

        from api import db as _db
        importlib.reload(_db)
        from api import alerts as _alerts
        importlib.reload(_alerts)
        self.db, self.alerts = _db, _alerts
        self.con = _db.connect()
        self.db.add_user(self.con, "mike", "Mike", True)
        self.db.add_user(self.con, "rob", "Rob")
        self.now = datetime.now(LOCAL)
        self.con.execute(
            """INSERT INTO events (id, venue, city, starts_at, tier, ticket_url,
                                   first_seen_at, last_seen_at)
               VALUES ('e1','Cap City','Austin, TX',?,1,'http://x',?,?)""",
            (SHOW.isoformat(), self.now.isoformat(), self.now.isoformat()))
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def sent_at(self, minutes_ago: int) -> str:
        return (datetime.now(LOCAL) - timedelta(minutes=minutes_ago)).isoformat()

    def log_nag(self, level=0, minutes_ago=30, channel="push", user="mike"):
        self.con.execute(
            "INSERT INTO nags (user_id, event_id, level, sent_at, channel, ok) "
            "VALUES (?,?,?,?,?,1)", (user, "e1", level, self.sent_at(minutes_ago), channel))
        self.con.commit()

    def plans(self, **kw):
        return self.alerts.retry_undelivered(self.con, datetime.now(LOCAL),
                                             dry_run=True, **kw)

    # ── the case that matters ────────────────────────────────────────────────

    def test_an_unconfirmed_push_is_retried(self):
        """Accepted by FCM, never shown on a device. That's a miss in progress."""
        self.log_nag(minutes_ago=30)
        retries = [r for r in self.plans() if r["user"] == "mike"]
        self.assertEqual(len(retries), 1)
        self.assertTrue(retries[0]["retry"])

    def test_a_confirmed_push_is_not_retried(self):
        self.log_nag(minutes_ago=30)
        self.db.record_receipt(self.con, "mike", "e1", "shown", None)
        self.assertEqual([r for r in self.plans() if r["user"] == "mike"], [])

    def test_received_alone_does_not_count_as_delivered(self):
        """
        'received' only means the worker woke up. It says nothing about whether
        anything reached a screen — and showNotification can still fail.
        """
        self.log_nag(minutes_ago=30)
        self.db.record_receipt(self.con, "mike", "e1", "received", None)
        self.assertEqual(len([r for r in self.plans() if r["user"] == "mike"]), 1)

    def test_a_fallback_notification_counts_as_delivered(self):
        """The bare fallback still put something in front of someone."""
        self.log_nag(minutes_ago=30)
        self.db.record_receipt(self.con, "mike", "e1", "shown_fallback", None)
        self.assertEqual([r for r in self.plans() if r["user"] == "mike"], [])

    # ── restraint ────────────────────────────────────────────────────────────

    def test_a_recent_push_is_left_alone(self):
        """A phone in a pocket in a tunnel is normal. Don't panic at 2 minutes."""
        self.log_nag(minutes_ago=2)
        self.assertEqual([r for r in self.plans() if r["user"] == "mike"], [])

    def test_an_ancient_push_is_not_retried(self):
        """Re-sending yesterday's alert is noise, not diligence."""
        self.log_nag(minutes_ago=60 * 20)
        self.assertEqual([r for r in self.plans() if r["user"] == "mike"], [])

    def test_only_one_retry_per_level(self):
        """It must not loop. The ladder is the backstop after this."""
        self.log_nag(minutes_ago=40, channel="push")
        self.log_nag(minutes_ago=30, channel="push-retry")
        self.assertEqual([r for r in self.plans() if r["user"] == "mike"], [])

    def test_a_decided_user_is_not_retried(self):
        """He answered. Stop, even if the original never showed."""
        self.log_nag(minutes_ago=30)
        self.db.set_state(self.con, "mike", "e1", "got_tickets")
        self.assertEqual([r for r in self.plans() if r["user"] == "mike"], [])

    def test_daydreams_are_never_retried(self):
        self.con.execute("UPDATE events SET tier=3 WHERE id='e1'")
        self.con.commit()
        self.log_nag(minutes_ago=30)
        self.assertEqual(self.plans(), [])

    def test_each_user_is_reconciled_independently(self):
        """Rob's phone failing must not depend on Mike's having worked."""
        self.log_nag(minutes_ago=30, user="mike")
        self.log_nag(minutes_ago=30, user="rob")
        self.db.record_receipt(self.con, "mike", "e1", "shown", None)
        users = {r["user"] for r in self.plans()}
        self.assertEqual(users, {"rob"})


if __name__ == "__main__":
    unittest.main()
