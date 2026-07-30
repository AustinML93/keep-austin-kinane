"""
The bit of the day, the pool, and the asymmetry.

No network — synthetic items in the shape the YouTube modules produce.
"""

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from api.sources.youtube import classify, parse_duration, video_id


class TestYouTubeHelpers(unittest.TestCase):

    def test_duration_parsing(self):
        self.assertEqual(parse_duration("PT45S"), 45)
        self.assertEqual(parse_duration("PT3M20S"), 200)
        self.assertEqual(parse_duration("PT1H2M3S"), 3723)
        self.assertEqual(parse_duration(None), 0)

    def test_kind_from_duration(self):
        self.assertEqual(classify(45), "short")
        self.assertEqual(classify(60), "short")
        self.assertEqual(classify(400), "clip")
        self.assertEqual(classify(70 * 60), "special")

    def test_video_id_from_every_url_shape(self):
        cases = {
            "https://www.youtube.com/watch?v=BfOgjzv-6lc": "BfOgjzv-6lc",
            "https://youtu.be/BfOgjzv-6lc?si=abc": "BfOgjzv-6lc",
            "https://www.youtube.com/shorts/BfOgjzv-6lc": "BfOgjzv-6lc",
            "https://www.youtube.com/embed/BfOgjzv-6lc": "BfOgjzv-6lc",
            "https://www.youtube.com/watch?v=WeN0D2Ra3Xc&pp=ygUS": "WeN0D2Ra3Xc",
        }
        for url, want in cases.items():
            self.assertEqual(video_id(url), want, url)


class TestPool(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KAK_DB"] = str(Path(self.tmp.name) / "t.sqlite3")
        import importlib

        from api import bits as _bits
        from api import db as _db
        importlib.reload(_db)
        importlib.reload(_bits)
        self.db, self.bits = _db, _bits
        self.con = _db.connect()
        self.db.add_user(self.con, "mike", "Mike", True)
        self.db.add_user(self.con, "rob", "Rob")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def item(self, vid, kind="short", **kw):
        return {"video_id": vid, "url": f"https://youtu.be/{vid}", "title": f"bit {vid}",
                "kind": kind, "duration_s": 45, "channel": "Kyle Kinane", **kw}

    def seed(self, n=8, kind="short"):
        for i in range(n):
            self.bits.upsert_bit(self.con, self.item(f"vid{i:08d}", kind),
                                 added_by="mike", source="playlist")

    # ── the daily pick ───────────────────────────────────────────────────────

    def test_pick_is_stable_within_a_day(self):
        """
        Both users must see the same clip all day, and they compute it rather
        than receive it — no scheduler, no stored 'today's pick'.
        """
        self.seed()
        d = date(2026, 8, 14)
        picks = {self.bits.pick_for(self.con, d)["id"] for _ in range(5)}
        self.assertEqual(len(picks), 1)

    def test_pick_changes_with_the_date(self):
        self.seed(20)
        days = {self.bits.pick_for(self.con, date(2026, 8, d))["id"] for d in range(1, 15)}
        self.assertGreater(len(days), 3)

    def test_blocked_bits_are_never_served(self):
        """Gout Flare-Up means never again."""
        self.seed(4)
        for i in range(3):
            self.bits.rate(self.con, f"vid{i:08d}", "mike", "gout", is_curator=True)
        for d in range(1, 40):
            got = self.bits.pick_for(self.con, date(2026, 8, 1) + timedelta(days=d))
            self.assertEqual(got["id"], "vid00000003")

    def test_specials_never_become_the_bit_of_the_day(self):
        """Nobody wants an hour of video as their daily hit."""
        self.bits.upsert_bit(self.con, self.item("longone001", "special", duration_s=4200),
                             added_by="mike", source="manual")
        self.assertIsNone(self.bits.pick_for(self.con, date(2026, 8, 14)))
        self.seed(2)
        for d in range(1, 20):
            got = self.bits.pick_for(self.con, date(2026, 8, 1) + timedelta(days=d))
            self.assertNotEqual(got["id"], "longone001")

    def test_serving_the_bit_does_not_change_todays_pick(self):
        """
        The endpoint records the bit as served on every fetch. If that counted
        against eligibility, Mike and Rob would see DIFFERENT bits of the day
        depending on who opened the app first — and the second person to look
        would get a different clip than the first. Found in a live run.
        """
        self.seed(6)
        d = date(2026, 8, 14)
        first = self.bits.pick_for(self.con, d)
        for _ in range(3):
            self.bits.record_served(self.con, first["id"], d)
            again = self.bits.pick_for(self.con, d)
            self.assertEqual(again["id"], first["id"])

    def test_yesterdays_bit_is_skipped_today(self):
        self.seed(6)
        d = date(2026, 8, 14)
        yesterday = self.bits.pick_for(self.con, d - timedelta(days=1))
        self.bits.record_served(self.con, yesterday["id"], d - timedelta(days=1))
        self.assertNotIn(yesterday["id"], [b["id"] for b in self.bits.eligible(self.con, d)])

    def test_no_repeat_window_scales_to_the_pool(self):
        """
        A fixed 30-day window with 17 bits means everything has been served by
        day 17 and the rule silently stops applying. The window has to describe
        something real.
        """
        self.assertEqual(self.bits.no_repeat_days(2), 0)      # too few to bother
        self.assertGreaterEqual(self.bits.no_repeat_days(17), 10)
        self.assertLess(self.bits.no_repeat_days(17), 17)     # never exceeds the pool
        self.assertEqual(self.bits.no_repeat_days(100), 30)   # capped

    def test_falls_back_rather_than_showing_nothing(self):
        """When everything's been served recently, repeat instead of going blank."""
        self.seed(2)
        d = date(2026, 8, 14)
        for i in range(2):
            self.bits.record_served(self.con, f"vid{i:08d}", d)
        self.assertIsNotNone(self.bits.pick_for(self.con, d))

    def test_unratable_bits_are_excluded(self):
        """A clip with embedding disabled would render an empty box."""
        self.bits.upsert_bit(self.con, self.item("noembed001", embeddable=False),
                             added_by="mike", source="playlist")
        self.assertIsNone(self.bits.pick_for(self.con, date(2026, 8, 14)))

    # ── weighting ────────────────────────────────────────────────────────────

    def test_struts_outrank_fine_over_time(self):
        """
        Heavy rotation for what holds up, filler for what merely runs.
        Unrated sits between them so new clips still surface to be judged.
        """
        self.seed(2)
        self.bits.rate(self.con, "vid00000000", "mike", "struts", is_curator=True)
        self.bits.rate(self.con, "vid00000001", "mike", "fine", is_curator=True)
        counts = {"vid00000000": 0, "vid00000001": 0}
        for d in range(1, 200):
            got = self.bits.pick_for(self.con, date(2026, 1, 1) + timedelta(days=d))
            counts[got["id"]] += 1
        self.assertGreater(counts["vid00000000"], counts["vid00000001"])

    # ── the asymmetry ────────────────────────────────────────────────────────

    def test_robs_rating_is_signal_not_control(self):
        """
        He gets all three buttons and none of the consequences. Nothing he
        presses can remove a clip from the pool.
        """
        self.seed(1)
        self.bits.rate(self.con, "vid00000000", "rob", "gout", is_curator=False)
        state = self.con.execute("SELECT state FROM bits WHERE id=?", ("vid00000000",)).fetchone()
        self.assertEqual(state["state"], "active")
        self.assertEqual(self.bits.ratings_for(self.con, "vid00000000")["rob"], "gout")

    def test_mikes_rating_does_control_the_pool(self):
        self.seed(1)
        self.bits.rate(self.con, "vid00000000", "mike", "gout", is_curator=True)
        state = self.con.execute("SELECT state FROM bits WHERE id=?", ("vid00000000",)).fetchone()
        self.assertEqual(state["state"], "blocked")

    def test_unknown_duration_does_not_make_a_special_eligible(self):
        """
        oEmbed carries no duration, so a pasted URL defaults to kind='clip'.
        If that clip is actually a 70-minute special it would land in the daily
        rotation — exactly what the shelf exists to prevent. add_url enriches
        from the API when a key is available; this guards the classification.
        """
        from api.sources.youtube import classify
        self.assertEqual(classify(0), "clip")          # the unavoidable default
        self.assertEqual(classify(70 * 60), "special")  # once we know, it moves

        self.bits.upsert_bit(self.con, self.item("longpaste1", classify(70 * 60),
                                                 duration_s=4200),
                             added_by="mike", source="manual")
        self.seed(3)
        for d in range(1, 25):
            got = self.bits.pick_for(self.con, date(2026, 8, 1) + timedelta(days=d))
            self.assertNotEqual(got["id"], "longpaste1")

    # ── sync behaviour ───────────────────────────────────────────────────────

    def test_resync_does_not_resurrect_a_blocked_bit(self):
        """
        The playlist is an inbox and is allowed to be messy — that's the whole
        premise. Re-syncing must not undo a Gout Flare-Up just because the clip
        is still sitting in the playlist.
        """
        self.seed(1)
        self.bits.rate(self.con, "vid00000000", "mike", "gout", is_curator=True)
        self.bits.upsert_bit(self.con, self.item("vid00000000"), added_by="mike",
                             source="playlist")
        state = self.con.execute("SELECT state FROM bits WHERE id=?", ("vid00000000",)).fetchone()
        self.assertEqual(state["state"], "blocked")

    def test_resync_does_not_clobber_a_custom_title(self):
        """"the one about the raccoon" must survive a metadata refresh."""
        self.seed(1)
        self.bits.rename(self.con, "vid00000000", "the one about the raccoon")
        self.bits.upsert_bit(self.con, self.item("vid00000000"), added_by="mike",
                             source="playlist")
        row = self.con.execute("SELECT custom_title FROM bits WHERE id=?",
                               ("vid00000000",)).fetchone()
        self.assertEqual(row["custom_title"], "the one about the raccoon")


if __name__ == "__main__":
    unittest.main()
