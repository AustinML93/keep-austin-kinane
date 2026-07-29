"""Tiering and the Austin-supersedes rule — the subtle logic that earns tests."""

import unittest
from datetime import date

from api.tiering import apply_austin_rule, tier_for

# Real coordinates, so the boundaries are tested where they actually sit.
AUSTIN = (30.2672, -97.7431, "Austin, TX")
GEORGETOWN = (30.6333, -97.6772, "Georgetown, TX")
SAN_MARCOS = (29.8833, -97.9414, "San Marcos, TX")
NEW_BRAUNFELS = (29.7030, -98.1245, "New Braunfels, TX")
SAN_ANTONIO = (29.4241, -98.4936, "San Antonio, TX")
HOUSTON = (29.7604, -95.3698, "Houston, TX")
DALLAS = (32.7767, -96.7970, "Dallas, TX")
FORT_WORTH = (32.7555, -97.3308, "Fort Worth, TX")
WACO = (31.5493, -97.1467, "Waco, TX")
NEW_ORLEANS = (29.9511, -90.0715, "New Orleans, LA")
CHICAGO = (41.8781, -87.6298, "Chicago, IL")


class TestTiers(unittest.TestCase):

    def test_austin_metro_is_tier_1(self):
        """A Georgetown show is a Tuesday, not a road trip."""
        for lat, lon, city in (AUSTIN, GEORGETOWN, SAN_MARCOS, NEW_BRAUNFELS):
            tier, _ = tier_for(lat, lon, city)
            self.assertEqual(tier, 1, f"{city} should be tier 1")

    def test_texas_metros_are_tier_2(self):
        for lat, lon, city in (SAN_ANTONIO, HOUSTON, DALLAS, FORT_WORTH, WACO):
            tier, dist = tier_for(lat, lon, city)
            self.assertEqual(tier, 2, f"{city} should be tier 2 (got {dist:.0f}mi)")

    def test_out_of_state_is_tier_3_even_when_close(self):
        """New Orleans is ~450mi; Louisiana is not a Tuesday. Distance AND state."""
        tier, _ = tier_for(*NEW_ORLEANS)
        self.assertEqual(tier, 3)

    def test_far_away_is_tier_3(self):
        self.assertEqual(tier_for(*CHICAGO)[0], 3)

    def test_falls_back_to_city_name_without_coordinates(self):
        """Cap City's JSON-LD often has no lat/long. Must still tier correctly."""
        self.assertEqual(tier_for(None, None, "Austin, TX")[0], 1)
        self.assertEqual(tier_for(None, None, "Dallas, TX")[0], 2)
        self.assertEqual(tier_for(None, None, "Portland, OR")[0], 3)


class TestAustinRule(unittest.TestCase):

    TODAY = date(2026, 8, 1)

    def ev(self, day, tier, **kw):
        return {"id": f"e{day}", "starts_at": f"2026-{day}", "tier": tier, **kw}

    def test_road_trip_with_no_austin_date_is_flagged_unknown(self):
        """Alert anyway — but honestly. The tour may not be fully routed."""
        events = [self.ev("09-15", 2)]
        out = apply_austin_rule(events, self.TODAY)
        self.assertEqual(out[0]["austin_status"], "unknown")

    def test_austin_date_in_the_same_swing_supersedes(self):
        events = [self.ev("09-15", 2), self.ev("10-02", 1)]  # 17 days apart
        out = apply_austin_rule(events, self.TODAY)
        self.assertEqual(out[0]["austin_status"], "superseded")

    def test_distant_austin_date_does_not_supersede(self):
        """Dallas in September and Austin next March are different tours."""
        events = [self.ev("09-15", 2), self.ev("12-20", 1)]  # 96 days apart
        out = apply_austin_rule(events, self.TODAY)
        self.assertEqual(out[0]["austin_status"], "unknown")

    def test_apology_when_austin_appears_after_tickets_bought(self):
        """The soul-crushing inverse: you drove, and he came to you anyway."""
        events = [self.ev("09-15", 2, tickets_bought=True), self.ev("10-02", 1)]
        out = apply_austin_rule(events, self.TODAY)
        self.assertEqual(out[0]["austin_status"], "owed_an_apology")

    def test_past_austin_dates_do_not_supersede(self):
        """A show that already happened cannot save you from driving to Dallas."""
        events = [self.ev("09-15", 2), self.ev("07-01", 1)]  # Austin date in the past
        out = apply_austin_rule(events, self.TODAY)
        self.assertEqual(out[0]["austin_status"], "unknown")

    def test_tier_1_and_3_are_never_annotated(self):
        events = [self.ev("09-15", 1), self.ev("09-16", 3)]
        out = apply_austin_rule(events, self.TODAY)
        self.assertIsNone(out[0]["austin_status"])
        self.assertIsNone(out[1]["austin_status"])


if __name__ == "__main__":
    unittest.main()
