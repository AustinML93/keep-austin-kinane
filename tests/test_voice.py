"""
The copy.

The valuable test here isn't taste — it's that every variant actually renders.
A nag with a leftover {venue} in it, or a KeyError at 6am on announcement day,
is a broken alert for the one event the whole app exists to catch. So: render
every line, every level, every combination, and assert nothing is left dangling.
"""

import re
import unittest

from api import voice

FIELDS = dict(venue="Cap City Comedy Club", when="Sat Oct 12, 7:30pm",
              city="Dallas, TX", other="Rob", day=3)


class TestEveryLineRenders(unittest.TestCase):

    def test_tier1_all_levels_and_variants(self):
        for level in range(0, 12):          # well past the table, to catch clamping
            for seed in ("a", "b", "c", "d", "e", "f"):
                title, body = voice.nag(level, 1, seed=seed, **FIELDS)
                self.assertTrue(title and body, f"empty at L{level}")
                self.assertNotIn("{", title + body, f"unfilled placeholder at L{level}")

    def test_tier1_cross_user_variants(self):
        for level in range(0, 8):
            for seed in ("a", "b", "c"):
                title, body = voice.nag(level, 1, other_has_tickets=True,
                                        seed=seed, **FIELDS)
                self.assertNotIn("{", title + body)

    def test_tier2_all_levels_and_every_austin_status(self):
        for status in (None, "unknown", "superseded", "owed_an_apology"):
            for level in range(0, 8):
                for seed in ("a", "b", "c"):
                    title, body = voice.nag(level, 2, austin_status=status,
                                            seed=seed, **FIELDS)
                    self.assertNotIn("{", title + body,
                                     f"unfilled at tier2 L{level} status={status}")

    def test_missing_optional_fields_still_render(self):
        """A source may give us no venue, no city, and no friend's name."""
        title, body = voice.nag(0, 1, venue=None, when="Sat", seed="x")
        self.assertNotIn("{", title + body)
        title, body = voice.nag(0, 2, venue=None, when="Sat", city=None, seed="x")
        self.assertNotIn("{", title + body)

    def test_status_lines_render(self):
        for seed in ("a", "b", "c", "d", "e"):
            self.assertNotIn("{", voice.status_line(3, [], seed=seed))
            self.assertNotIn("{", voice.status_line(3, ["Cap City"], seed=seed))
            self.assertNotIn("{", voice.status_line(3, [], True, seed=seed))

    def test_no_ui_string_has_a_placeholder(self):
        for key, val in voice.ui_copy().items():
            self.assertNotIn("{", val, f"UI copy {key!r} has an unfilled placeholder")


class TestProse(unittest.TestCase):

    def test_city_loses_its_state_code_in_sentences(self):
        """
        "Last call on Dallas" reads like a person wrote it. "Last call on
        Dallas, TX" reads like a database did.
        """
        title, body = voice.nag(3, 2, venue="Majestic", when="Sat",
                                city="Dallas, TX", seed="x")
        self.assertIn("Dallas", title + body)
        self.assertNotIn("Dallas, TX", title + body)

    def test_the_apology_has_its_own_title(self):
        """
        Bolting it onto the generic road-trip line announced a Dallas date and
        THEN apologised, burying the only funny thing the tiering rule produces.
        """
        plain = voice.nag(0, 2, austin_status="unknown", venue="Majestic",
                          when="Sat", city="Dallas, TX", seed="x")
        sorry = voice.nag(0, 2, austin_status="owed_an_apology", venue="Majestic",
                          when="Sat", city="Dallas, TX", seed="x")
        self.assertNotEqual(plain[0], sorry[0])
        self.assertRegex(sorry[0] + sorry[1], r"(?i)austin")
        self.assertRegex(sorry[1], r"(?i)sorry|one job")

    def test_the_apology_does_not_escalate(self):
        """Nothing to escalate — the app was wrong and says so, once per level."""
        for lv in range(4):
            t, b = voice.nag(lv, 2, austin_status="owed_an_apology", venue="M",
                             when="Sat", city="Dallas, TX", seed="x")
            self.assertNotIn("{", t + b)
            self.assertRegex(t + b, r"(?i)austin")


class TestDeterminism(unittest.TestCase):

    def test_same_show_and_level_always_gives_the_same_line(self):
        """
        An alert re-read an hour later must not have reworded itself. Stability
        per show is the whole reason the seed exists.
        """
        first = voice.nag(2, 1, seed="2026-10-12|cap city|19:30", **FIELDS)
        for _ in range(5):
            self.assertEqual(voice.nag(2, 1, seed="2026-10-12|cap city|19:30", **FIELDS),
                             first)

    def test_different_shows_get_different_wording(self):
        """Otherwise the app wears out its four jokes in a month."""
        seen = {voice.nag(0, 1, seed=f"show-{i}", **FIELDS)[0] for i in range(40)}
        self.assertGreater(len(seen), 1)

    def test_escalation_actually_changes_the_words(self):
        titles = [voice.nag(lv, 1, seed="same-show", **FIELDS)[0] for lv in range(5)]
        self.assertEqual(len(set(titles)), 5, "levels should not reuse a title")

    def test_cross_user_copy_differs_from_the_solo_copy(self):
        solo = voice.nag(2, 1, seed="s", **FIELDS)
        crossed = voice.nag(2, 1, other_has_tickets=True, seed="s", **FIELDS)
        self.assertNotEqual(solo, crossed)
        self.assertIn("Rob", crossed[0] + crossed[1])


class TestNoFabricatedAttribution(unittest.TestCase):
    """
    The hard rule, enforced as far as a test can enforce it: nothing in here may
    present itself as a quotation. A test can't judge whether a line sounds like
    him — but it can catch copy that claims to BE him.
    """

    def test_no_line_is_presented_as_a_quotation(self):
        blobs = []
        for level in range(0, 6):
            for seed in "abcdef":
                blobs += list(voice.nag(level, 1, seed=seed, **FIELDS))
                blobs += list(voice.nag(level, 2, seed=seed, **FIELDS))
        blobs += list(voice.ui_copy().values())
        blobs += list(voice.ACK.values())
        for s in blobs:
            self.assertNotRegex(s, r'["“”]', f"quotation marks in copy: {s!r}")
            self.assertNotRegex(s, r"(?i)\bkinane (?:said|says|once)\b", f"attribution: {s!r}")
            self.assertNotRegex(s, r"(?i)\bas he (?:said|puts it)\b", f"attribution: {s!r}")

    def test_uncle_bbq_is_never_explained(self):
        """It's a real recurring bit of his. The app does not invent an origin."""
        blobs = " ".join(list(voice.ui_copy().values()) + list(voice.ACK.values()))
        self.assertNotRegex(blobs, r"(?i)uncle bbq (?:is|refers|comes from|means)")


if __name__ == "__main__":
    unittest.main()
