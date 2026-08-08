"""
Parser tests for the Moontower Comedy Festival source, against the real
fixtures in recon/raw/ plus a couple of synthetic pages for shapes the
fixtures don't demonstrate.

Both recon/raw/moontower-*.html captures are the SAME off-season snapshot (the
only diff between them is a handful of randomly generated DOM ids on a hero
video/slideshow — verified with `diff` before writing this). Neither shows the
April festival lineup itself; what they do show is the Paramount's own
year-round "Here All Year" card grid, which is real, dated, enumerable content
and is what this parser counts for total_seen. See the module docstring in
api/sources/moontower.py for the full reasoning.

No network — stdlib urllib only, so these run on bare python3.
"""

import unittest
from datetime import date
from pathlib import Path

from api.sources.moontower import MoontowerComedyFestival, _infer_year, _parse_when

RAW = Path(__file__).resolve().parent.parent / "recon" / "raw"


class TestMoontowerRealFixtures(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body_real = (RAW / "moontower-real.html").read_text()
        cls.body_festival = (RAW / "moontower-comedy-festival.html").read_text()
        # Fixtures were captured 2026-07-29; fix "today" so year-inference on
        # the Sep/Oct card dates is deterministic regardless of when this
        # test suite runs.
        cls.today = date(2026, 7, 29)
        cls.result = MoontowerComedyFestival().parse(cls.body_real, today=cls.today)

    def test_both_fixtures_are_the_same_snapshot(self):
        """
        Confirms the premise above so this test file doesn't silently rot if
        the fixtures are ever replaced with genuinely different captures.
        """
        r2 = MoontowerComedyFestival().parse(self.body_festival, today=self.today)
        self.assertEqual(r2.total_seen, self.result.total_seen)

    def test_sees_the_year_round_card_grid(self):
        """
        Parseability: the page is off-season for the festival itself, but the
        "Here All Year" grid is real, dated, enumerable content — 8 shows in
        this capture (Josh Thomas, Comedy Bang! Bang!, Bored Teachers, Therapy
        Gecko Live, Jenny Tian, Phoebe Robinson, Chad Goes Deep, Janeane
        Garofalo). That count is total_seen, exactly as CapCity's 277 is.
        """
        self.assertTrue(self.result.ok)
        self.assertEqual(self.result.total_seen, 8)

    def test_kinane_not_currently_on_this_page(self):
        """
        Presence: he isn't in the year-round grid and the April lineup hasn't
        published yet, so zero is the CORRECT reading — not a broken parser.
        Zero events out of a nonzero total_seen is exactly the "healthy and
        quiet" shape the events/total_seen split exists to distinguish from
        "broken and lying."
        """
        self.assertEqual(self.result.events, [])
        self.assertTrue(self.result.ok)

    def test_a_card_date_parses_with_inferred_year_and_time(self):
        card_events = {e.title: e for e in self._parse_all_cards()}
        self.assertIn("Josh Thomas", card_events)
        ev = card_events["Josh Thomas"]
        self.assertEqual(ev.date, "2026-09-11")
        self.assertEqual(ev.time, "19:00")

    def test_multi_day_run_uses_the_start_date_with_no_time(self):
        """
        "Oct 23 - Oct 24" (Janeane Garofalo) carries no time at all — a run,
        not a single showtime. We take the start date and leave time absent
        rather than fabricate a slot the page never gave us.
        """
        card_events = {e.title: e for e in self._parse_all_cards()}
        ev = card_events["Janeane Garofalo"]
        self.assertEqual(ev.date, "2026-10-23")
        self.assertEqual(ev.time, "")

    def _parse_all_cards(self):
        """Re-run the card matcher without the Kinane filter, for date-shape assertions."""
        import re

        from api.sources.moontower import CARD_RE
        from api.sources.base import Event

        out = []
        for url, when, name in CARD_RE.findall(self.body_real):
            src = MoontowerComedyFestival()
            ev = src._to_event(url, when, name, self.today)
            if ev:
                out.append(ev)
        return out


class TestMoontowerKinaneMatch(unittest.TestCase):
    """
    Neither real fixture has him booked (confirmed above), so this proves the
    matcher works before he ever is — same approach as CapCity's synthetic
    lineup test. Snippet mirrors the real card markup verified above.
    """

    CARD_HTML = """
    <div class="tw-w-full">
      <div class="card tw-relative">
        <a class="links__overlay" href="https://tickets.austintheatre.org/99999">
        <span class="presentational__is-hidden">View more details about Kyle Kinane on Friday April 10th at 08:00pm.</span>
        </a>
        <figure class="card__figure"></figure>
        <div class="card__content tw-mt-6">
          <div class="styles__subheading--small">Fri&nbsp;&#183;&nbsp;Apr 10&nbsp;&#183;&nbsp;8:00pm</div>
          <div class="styles__h-base tw-text-xxl">Kyle Kinane</div>
        </div>
      </div>
    </div>
    """

    def test_finds_him_when_he_is_there(self):
        body = f"<html><title>Moontower Comedy - Paramount Theatre</title><body>{self.CARD_HTML}</body></html>"
        r = MoontowerComedyFestival().parse(body, today=date(2026, 1, 1))
        self.assertTrue(r.ok)
        self.assertEqual(r.total_seen, 1)
        self.assertEqual(len(r.events), 1)
        ev = r.events[0]
        self.assertEqual(ev.date, "2026-04-10")
        self.assertEqual(ev.time, "20:00")
        self.assertEqual(ev.ticket_url, "https://tickets.austintheatre.org/99999")
        self.assertEqual(ev.city, "Austin, TX")


class TestMoontowerDormantSeason(unittest.TestCase):
    """
    The shape this whole source exists to handle correctly: a recognizable
    Moontower page with no dated listings at all — deep off-season, before
    even the year-round grid has anything booked.

    ok=True, events=[], total_seen=0. Fed into db.record_health with
    seasonal=True (as MoontowerComedyFestival.seasonal is), that becomes
    health="dormant" rather than "suspicious" — see db.record_health and
    tests/test_health.py. This module doesn't re-test record_health itself,
    only that it emits the shape record_health depends on.
    """

    def test_no_lineup_is_ok_and_empty_not_a_failure(self):
        body = "<html><title>Moontower Comedy - Paramount Theatre</title><body>April 8-19, 2026. Lineup coming soon.</body></html>"
        r = MoontowerComedyFestival().parse(body)
        self.assertTrue(r.ok)
        self.assertEqual(r.events, [])
        self.assertEqual(r.total_seen, 0)
        self.assertIsNone(r.error_kind)


class TestMoontowerParseFailure(unittest.TestCase):

    def test_unrecognizable_page_is_a_parse_error_not_a_quiet_season(self):
        """
        A page with no trace of "moontower" anywhere is not this source gone
        quiet for the year — it's a wrong URL, a redirect that stopped
        landing here, or a total redesign. Reporting ok=True would print
        "dormant" over what is actually a broken parser.
        """
        r = MoontowerComedyFestival().parse("<html><body>hello</body></html>")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_kind, "parse")
        self.assertEqual(r.events, [])

    def test_empty_body(self):
        r = MoontowerComedyFestival().parse("")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_kind, "parse")


class TestYearInference(unittest.TestCase):
    """
    Card dates never carry a year. These pin down the "nearest occurrence at
    or after today" heuristic directly, independent of the HTML parsing.
    """

    def test_future_month_this_year(self):
        self.assertEqual(_infer_year(9, 11, today=date(2026, 7, 29)), 2026)

    def test_past_month_rolls_to_next_year(self):
        self.assertEqual(_infer_year(3, 1, today=date(2026, 7, 29)), 2027)

    def test_within_grace_window_stays_this_year(self):
        """A card scraped during the show's own week shouldn't roll forward."""
        self.assertEqual(_infer_year(7, 27, today=date(2026, 7, 29)), 2026)

    def test_parse_when_handles_missing_time(self):
        self.assertEqual(_parse_when("Oct 23 - Oct 24", today=date(2026, 7, 29)), "2026-10-23")

    def test_parse_when_rejects_unparseable_text(self):
        self.assertIsNone(_parse_when("Coming Soon", today=date(2026, 7, 29)))


if __name__ == "__main__":
    unittest.main()
