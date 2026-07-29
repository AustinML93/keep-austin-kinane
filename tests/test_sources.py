"""
Parser tests against the real fixtures captured in recon/raw/.

No network. These run on bare python3 — that's why the source layer uses stdlib
urllib rather than httpx.
"""

import json
import unittest
from pathlib import Path

from api.sources.capcity import CapCity
from api.sources.kylekinane import KyleKinaneOfficial

RAW = Path(__file__).resolve().parent.parent / "recon" / "raw"


class TestOfficialFeed(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body = (RAW / "kylekinane-events-api.json").read_text()
        cls.result = KyleKinaneOfficial().parse(cls.body)

    def test_parses_the_whole_feed(self):
        self.assertTrue(self.result.ok)
        self.assertEqual(self.result.total_seen, 78)
        self.assertEqual(len(self.result.events), 78)

    def test_events_carry_coordinates(self):
        """Tiering is a distance calculation — no coordinates, no tiers."""
        with_coords = [e for e in self.result.events if e.latitude and e.longitude]
        self.assertGreater(len(with_coords), 70)

    def test_two_shows_a_night_stay_separate(self):
        """
        Clubs run a 7:00 and a 9:15. Merging them would hide half the ticket
        links — and the late show is often the one that's still available.
        """
        keys = [e.dedupe_key() for e in self.result.events]
        self.assertEqual(len(keys), len(set(keys)), "dedupe key collapsed distinct showtimes")

        omaha = sorted(e.starts_at for e in self.result.events
                       if (e.city or "").startswith("Omaha"))
        self.assertGreaterEqual(len(omaha), 2)
        self.assertNotEqual(omaha[0], omaha[1])

    def test_ticket_links_are_real_not_placeholders(self):
        """The feed contains a literal 'ticketlink.com' placeholder we skip."""
        urls = [e.ticket_url for e in self.result.events if e.ticket_url]
        self.assertGreater(len(urls), 40)
        self.assertFalse([u for u in urls if "ticketlink.com" in u])

    def test_malformed_payload_reports_parse_error_not_emptiness(self):
        r = KyleKinaneOfficial().parse('{"data": {"nope": 1}}')
        self.assertFalse(r.ok)
        self.assertEqual(r.error_kind, "parse")
        self.assertEqual(r.events, [])


class TestCapCity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body = (RAW / "cap-city-comedy-club.html").read_text()
        cls.result = CapCity().parse(cls.body)

    def test_sees_the_calendar(self):
        """Parseability: the whole calendar is readable."""
        self.assertTrue(self.result.ok)
        self.assertGreater(self.result.total_seen, 200)

    def test_kinane_not_currently_booked(self):
        """
        Presence: he isn't on the calendar right now, and that is the CORRECT
        answer — not a failure. This is the split the whole design rests on.
        """
        self.assertEqual(self.result.events, [])
        self.assertTrue(self.result.ok)

    def test_finds_him_when_he_is_there(self):
        """Synthetic listing — proves the matcher works before he's ever booked."""
        html = """
        <script type="application/ld+json">
        {"@type":"Event","name":"Kyle Kinane","startDate":"2026-10-12T19:30:00Z",
         "url":"https://capcitycomedy.com/shows/12345",
         "location":{"@type":"Place","name":"Cap City Comedy Club"}}
        </script>"""
        r = CapCity().parse(html)
        self.assertEqual(len(r.events), 1)
        ev = r.events[0]
        self.assertEqual(ev.date, "2026-10-12")
        self.assertEqual(ev.time, "19:30")
        self.assertEqual(ev.ticket_url, "https://capcitycomedy.com/shows/12345")
        self.assertIsNotNone(ev.latitude)

    def test_matches_a_festival_style_lineup_listing(self):
        """He won't always be the headline name — lineups list him mid-block."""
        html = """
        <script type="application/ld+json">
        {"@type":"Event","name":"Comedy Showcase","startDate":"2026-04-18T20:00:00Z",
         "description":"With Ron Funches, Kyle Kinane, and more"}
        </script>"""
        self.assertEqual(len(CapCity().parse(html).events), 1)

    def test_redesign_reports_parse_failure_not_an_empty_calendar(self):
        """
        A page that loads but has no JSON-LD is a redesign, not a quiet week.
        Reporting ok=True here is how the app would go blind without saying so.
        """
        r = CapCity().parse("<html><body>hello</body></html>")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_kind, "parse")


if __name__ == "__main__":
    unittest.main()
