"""
Known venues, so a manually-added show gets tiered without a geocoding service.

⚠️ These coordinates are APPROXIMATE — downtown-block accurate, not surveyed.
That is entirely sufficient, because the only thing they feed is a distance
bucket: under 50 miles, under 250 miles, or elsewhere. Being 400 feet off
changes nothing. Do not use them for anything that needs real precision.

Matching is fuzzy on purpose. Someone typing a show in from a text message will
write "mothership" or "the Mothership" or "Comedy Mothership", and all three
should land in the right place.
"""

from __future__ import annotations

import re

# name -> (lat, lon, city)
KNOWN = {
    # Austin — the ones that actually matter
    "comedy mothership":      (30.2672, -97.7395, "Austin, TX"),
    "cap city comedy club":   (30.4014, -97.7244, "Austin, TX"),
    "paramount theatre":      (30.2691, -97.7424, "Austin, TX"),
    "stateside at the paramount": (30.2693, -97.7426, "Austin, TX"),
    "moody theater":          (30.2653, -97.7472, "Austin, TX"),
    "acl live":               (30.2653, -97.7472, "Austin, TX"),
    "moody center":           (30.2807, -97.7326, "Austin, TX"),
    "bass concert hall":      (30.2861, -97.7307, "Austin, TX"),
    "emo's":                  (30.2200, -97.7290, "Austin, TX"),
    "the long center":        (30.2600, -97.7480, "Austin, TX"),
    "vulcan gas company":     (30.2670, -97.7400, "Austin, TX"),
    "fallout theater":        (30.2668, -97.7440, "Austin, TX"),
    "creek and the cave":     (30.2596, -97.7247, "Austin, TX"),
    "the creek and the cave": (30.2596, -97.7247, "Austin, TX"),
    "sunset strip":           (30.2668, -97.7405, "Austin, TX"),

    # Road-trip range
    "addison improv":         (32.9618, -96.8300, "Addison, TX"),
    "hyena's comedy club":    (32.7767, -96.7970, "Dallas, TX"),
    "texas theatre":          (32.7430, -96.8280, "Dallas, TX"),
    "majestic theatre":       (32.7830, -96.7970, "Dallas, TX"),
    "punch line houston":     (29.7604, -95.3698, "Houston, TX"),
    "houston improv":         (29.7360, -95.4610, "Houston, TX"),
    "warehouse live":         (29.7510, -95.3540, "Houston, TX"),
    "lol comedy club":        (29.4241, -98.4936, "San Antonio, TX"),
    "laugh out loud comedy club": (29.4241, -98.4936, "San Antonio, TX"),
    "aztec theatre":          (29.4250, -98.4930, "San Antonio, TX"),
    "tobin center":           (29.4300, -98.4890, "San Antonio, TX"),
    "hyena's fort worth":     (32.7555, -97.3308, "Fort Worth, TX"),
}

# Bare city fallbacks, for when the venue is unknown but the city isn't.
CITIES = {
    "austin":        (30.2672, -97.7431, "Austin, TX"),
    "round rock":    (30.5083, -97.6789, "Round Rock, TX"),
    "georgetown":    (30.6333, -97.6772, "Georgetown, TX"),
    "cedar park":    (30.5052, -97.8203, "Cedar Park, TX"),
    "buda":          (30.0855, -97.8403, "Buda, TX"),
    "san marcos":    (29.8833, -97.9414, "San Marcos, TX"),
    "new braunfels": (29.7030, -98.1245, "New Braunfels, TX"),
    "san antonio":   (29.4241, -98.4936, "San Antonio, TX"),
    "houston":       (29.7604, -95.3698, "Houston, TX"),
    "dallas":        (32.7767, -96.7970, "Dallas, TX"),
    "fort worth":    (32.7555, -97.3308, "Fort Worth, TX"),
    "waco":          (31.5493, -97.1467, "Waco, TX"),
    "college station": (30.6280, -96.3344, "College Station, TX"),
    "corpus christi": (27.8006, -97.3964, "Corpus Christi, TX"),
}


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", (s or "").lower())).strip()


# Words that carry no identity. "the Mothership" and "Comedy Mothership" are the
# same place; only 'mothership' is doing any work.
FILLER = {"the", "a", "at", "of", "and", "comedy", "club", "theatre", "theater",
          "hall", "live", "company", "co", "austin"}


def _tokens(s: str) -> set[str]:
    return {w for w in s.split() if w not in FILLER}


def match(venue: str | None) -> str | None:
    """The KNOWN key this venue name refers to, or None."""
    v = _norm(venue)
    if not v:
        return None
    if v in KNOWN:
        return v
    # Substring, either direction: "capital city comedy" ↔ "cap city comedy club"
    for name in KNOWN:
        if name in v or v in name:
            return name
    # Token subset, ignoring filler. "the Mothership" -> {mothership}, which is
    # a subset of "comedy mothership" -> {mothership}.
    vt = _tokens(v)
    if vt:
        for name in KNOWN:
            nt = _tokens(name)
            if nt and (vt <= nt or nt <= vt):
                return name
    return None


def canonical(venue: str | None) -> str | None:
    """
    The canonical spelling of a known venue.

    This matters more than it looks. The dedupe key is date|venue|time, so
    "the Mothership" typed by Rob and "Comedy Mothership" from a real source
    would otherwise be two different shows on the same night — one of them
    unacknowledged, still nagging, and forever un-mergeable.
    """
    hit = match(venue)
    return hit.title() if hit else venue


def locate(venue: str | None, city: str | None = None):
    """
    Best guess at (lat, lon, city) for a hand-typed venue and/or city.

    Returns (None, None, city) when nothing matches — tier_for() then falls back
    to city-name matching, and an unrecognised place lands in tier 3 rather than
    guessing it's local. A daydream is a safer wrong answer than a false alarm
    about a show that isn't near you.
    """
    hit = match(venue)
    if hit:
        return KNOWN[hit]

    c = _norm(city)
    if c:
        for name, coords in CITIES.items():
            if name in c:
                return coords

    return (None, None, city)


def suggestions() -> list[str]:
    """Venue names for the datalist in the add form."""
    return sorted({n.title() for n in KNOWN})
