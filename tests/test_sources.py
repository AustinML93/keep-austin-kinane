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
from api.sources.ticketmaster import Ticketmaster

RAW = Path(__file__).resolve().parent.parent / "recon" / "raw"


class TestOfficialFeed(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body = (RAW / "kylekinane-events-api.json").read_text()
        cls.result = KyleKinaneOfficial().parse(cls.body)

    def test_parses_the_whole_feed(self):
        """
        total_seen counts top-level bookings (78); events counts actual shows
        (131) — a booking's showtimes[] can span several dates (club runs),
        and each one is a separate night someone could miss.
        """
        self.assertTrue(self.result.ok)
        self.assertEqual(self.result.total_seen, 78)
        self.assertEqual(len(self.result.events), 131)

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

    def test_club_run_expands_into_every_night(self):
        """
        Helium Comedy Club's Jan 8-10 booking is ONE top-level entry with FIVE
        showtimes across three nights. Taking only showtimes[0] (the bug) kept
        just the Jan 8 7:30 and silently dropped two full club nights.
        """
        helium = sorted(
            (e for e in self.result.events
             if e.venue == "Helium Comedy Club" and e.starts_at.startswith("2026-01")),
            key=lambda e: e.starts_at,
        )
        self.assertEqual(len(helium), 5)

        starts = [e.starts_at for e in helium]
        self.assertEqual(len(starts), len(set(starts)), "expanded showtimes collided on starts_at")
        self.assertEqual(starts, [
            "2026-01-08T19:30", "2026-01-09T19:00", "2026-01-09T21:15",
            "2026-01-10T19:00", "2026-01-10T21:30",
        ])

        ext_ids = [e.external_id for e in helium]
        self.assertEqual(len(ext_ids), len(set(ext_ids)), "expanded showtimes collided on external_id")

    def test_per_showtime_ticket_link_is_preferred_over_parent(self):
        """
        Zanies' top-level ticketLinks is the 'ticketlink.com' placeholder;
        each showtime carries its own real etix.com link. Falling back to the
        parent link here would hand every one of the five nights the same
        dead placeholder instead of its actual ticket page.
        """
        zanies = sorted(
            (e for e in self.result.events
             if e.venue == "Zanies Comedy Night Club" and e.starts_at.startswith("2026-10")),
            key=lambda e: e.starts_at,
        )
        self.assertEqual(len(zanies), 5)

        urls = [e.ticket_url for e in zanies]
        self.assertTrue(all(u and "etix.com" in u for u in urls))
        self.assertEqual(len(urls), len(set(urls)), "all five nights got the same ticket link")
        self.assertFalse(any("ticketlink.com" in u for u in urls))

    def test_date_only_showtimes_still_produce_events(self):
        """
        Moon Palace Cancun's showtimes carry a date but no time (a resort
        booking, not a club set). Each of the four still has to become its
        own dateless Event rather than being dropped or collapsed to one.
        """
        moon = [e for e in self.result.events if "Moon Palace" in (e.venue or "")]
        self.assertEqual(len(moon), 4)
        starts = sorted(e.starts_at for e in moon)
        self.assertEqual(starts, ["2026-01-28", "2026-01-29", "2026-01-30", "2026-01-31"])
        self.assertTrue(all("T" not in s for s in starts))

    def test_every_external_id_in_the_feed_is_unique(self):
        """
        All 131 showtimes share only 78 parent ids — external_id must bake in
        date+time or distinct shows collide and upsert away four of five
        nights in a club run.
        """
        ids = [e.external_id for e in self.result.events]
        self.assertEqual(len(ids), 131)
        self.assertEqual(len(ids), len(set(ids)))

    def test_entry_without_showtimes_falls_back_to_top_level_date(self):
        """No fixture entry lacks showtimes[] today, but the API doesn't
        guarantee that — the fallback path (pre-dating this fix) must keep
        working."""
        payload = json.dumps({"data": {"events": [{
            "id": "legacy-1",
            "startDate": "2026-05-01",
            "startTime": "20:00:00",
            "venue": "Some Club",
            "displayVenue": "Some Club",
            "city": "Somewhere, TX",
            "ticketLinks": [{"ticketLink": "https://example.com/tix"}],
        }]}})
        r = KyleKinaneOfficial().parse(payload)
        self.assertTrue(r.ok)
        self.assertEqual(r.total_seen, 1)
        self.assertEqual(len(r.events), 1)
        ev = r.events[0]
        self.assertEqual(ev.starts_at, "2026-05-01T20:00")
        self.assertEqual(ev.ticket_url, "https://example.com/tix")
        self.assertEqual(ev.external_id, "legacy-1-2026-05-01-20:00")


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


class TestTicketmaster(unittest.TestCase):
    """No live fixture yet — synthetic payloads in the documented shape."""

    PAYLOAD = json.dumps({"_embedded": {"events": [
        {
            "id": "G5v0Z9Yz", "name": "Kyle Kinane",
            "url": "https://www.ticketmaster.com/event/G5v0Z9Yz",
            "dates": {"start": {"localDate": "2026-10-12", "localTime": "19:30:00"}},
            "sales": {"public": {"startDateTime": "2026-08-15T15:00:00Z"}},
            "_embedded": {"venues": [{
                "name": "Paramount Theatre",
                "city": {"name": "Austin"}, "state": {"stateCode": "TX"},
                "address": {"line1": "713 Congress Ave"},
                "location": {"latitude": "30.2694", "longitude": "-97.7420"},
            }]},
        },
        {
            "id": "OTHER", "name": "Somebody Else Live",
            "dates": {"start": {"localDate": "2026-10-13"}},
        },
    ]}})

    def test_extracts_him_and_ignores_everyone_else(self):
        events = Ticketmaster("fake-key")._parse(self.PAYLOAD)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].venue, "Paramount Theatre")
        self.assertEqual(events[0].city, "Austin, TX")
        self.assertEqual(events[0].starts_at, "2026-10-12T19:30")

    def test_captures_the_onsale_time(self):
        """
        The field no other source gives us. It's what the tier-2 decision
        deadline should be anchored to instead of a three-week guess.
        """
        ev = Ticketmaster("fake-key")._parse(self.PAYLOAD)[0]
        self.assertEqual(ev.onsale_at, "2026-08-15T15:00:00Z")

    def test_matches_a_multi_comedian_bill_via_attractions(self):
        """He's often a supporting name on a showcase, not the event title."""
        payload = json.dumps({"_embedded": {"events": [{
            "id": "X", "name": "Moontower: Late Night Showcase",
            "dates": {"start": {"localDate": "2026-04-18"}},
            "_embedded": {"attractions": [{"name": "Ron Funches"}, {"name": "Kyle Kinane"}]},
        }]}})
        self.assertEqual(len(Ticketmaster("fake-key")._parse(payload)), 1)

    def test_sentinel_onsale_date_is_dropped(self):
        """1900-01-01 means 'no on-sale date', not 'on sale since 1900'."""
        payload = json.dumps({"_embedded": {"events": [{
            "id": "S", "name": "Kyle Kinane",
            "dates": {"start": {"localDate": "2026-10-15", "localTime": "20:00:00"}},
            "sales": {"public": {"startDateTime": "1900-01-01T06:00:00Z"}},
        }]}})
        self.assertIsNone(Ticketmaster("fake-key")._parse(payload)[0].onsale_at)

    def test_missing_key_is_config_not_emptiness(self):
        r = Ticketmaster("")
        r.api_key = ""
        result = r.fetch()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "config")


if __name__ == "__main__":
    unittest.main()
