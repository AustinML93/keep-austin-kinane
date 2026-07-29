"""
Source health — the machinery that makes silence trustworthy.

These are the most important tests in the project. The app's core promise is
that when it's quiet, nothing is happening. A parser that breaks and returns
zero events forever would convert that promise into a lie, and we'd never know
until we missed a show while owning an app built to prevent exactly that.
"""

import os
import tempfile
import unittest
from pathlib import Path

from api.sources.base import FetchResult


class FakeSource:
    def __init__(self, sid="fake", seasonal=False):
        self.id, self.name, self.kind, self.seasonal = sid, sid, "scrape", seasonal


class TestHealth(unittest.TestCase):

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

    def ok(self, n):
        return FetchResult("fake", ok=True, total_seen=n)

    def fail(self, kind="network"):
        return FetchResult("fake", ok=False, error="boom", error_kind=kind)

    # ── the silent-breakage guard ────────────────────────────────────────────

    def test_going_blind_is_suspicious_not_healthy(self):
        """
        The failure this whole app exists to prevent: a source that reliably
        showed a 277-event calendar now shows zero. That is NOT 'no shows'.
        """
        src = FakeSource()
        self.db.record_health(self.con, src, self.ok(277))
        health = self.db.record_health(self.con, src, self.ok(0))
        self.assertEqual(health, "suspicious")

    def test_legitimately_empty_source_stays_ok(self):
        """A source that never had much shouldn't cry wolf."""
        src = FakeSource()
        self.db.record_health(self.con, src, self.ok(2))
        self.assertEqual(self.db.record_health(self.con, src, self.ok(0)), "ok")

    def test_seasonal_source_going_empty_is_dormant_not_suspicious(self):
        """Moontower in August is empty and that's correct."""
        src = FakeSource("moontower", seasonal=True)
        self.db.record_health(self.con, src, self.ok(40))
        self.assertEqual(self.db.record_health(self.con, src, self.ok(0)), "dormant")

    def test_baseline_survives_an_empty_cycle(self):
        """One blind cycle must not reset the yardstick and mask the next one."""
        src = FakeSource()
        self.db.record_health(self.con, src, self.ok(277))
        self.db.record_health(self.con, src, self.ok(0))
        self.db.record_health(self.con, src, self.ok(0))
        row = self.db.source_health(self.con)[0]
        self.assertEqual(row["baseline_total"], 277)
        self.assertEqual(row["health"], "suspicious")

    # ── failure escalation ───────────────────────────────────────────────────

    def test_failures_escalate_degraded_then_down(self):
        src = FakeSource()
        self.assertEqual(self.db.record_health(self.con, src, self.fail()), "degraded")
        self.assertEqual(self.db.record_health(self.con, src, self.fail()), "degraded")
        self.assertEqual(self.db.record_health(self.con, src, self.fail()), "down")

    def test_success_clears_the_failure_streak(self):
        src = FakeSource()
        for _ in range(3):
            self.db.record_health(self.con, src, self.fail())
        self.assertEqual(self.db.record_health(self.con, src, self.ok(10)), "ok")
        self.assertEqual(self.db.source_health(self.con)[0]["consecutive_failures"], 0)

    def test_config_failure_is_its_own_state_immediately(self):
        """
        A rotated token must never read as 'no shows', and must not wait three
        cycles to be visible. It's a distinct state from the first failure.
        """
        src = FakeSource()
        self.assertEqual(
            self.db.record_health(self.con, src, self.fail("config")), "config_failed"
        )


if __name__ == "__main__":
    unittest.main()
