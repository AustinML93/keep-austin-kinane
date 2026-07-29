"""
Tiering, and the Austin-supersedes rule.

Tier is a DISTANCE CALCULATION, not a city list. The official feed carries
lat/long on every event, which permanently dissolves the "does Georgetown count?"
question — Georgetown is 26 miles out, so it's tier 1, because a Georgetown show
is a Tuesday, not a road trip.

    Tier 1  ≤ 50mi of Austin      full alarm, escalating until acknowledged
    Tier 2  ≤ 250mi and in Texas  conditional — see the Austin rule below
    Tier 3  everywhere else       silent, browsable, never notifies
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta

AUSTIN_LAT, AUSTIN_LON = 30.2672, -97.7431

TIER1_MILES = 50      # Austin metro: Georgetown, Buda, Round Rock, San Marcos, New Braunfels
TIER2_MILES = 250     # DFW, Houston, San Antonio, Waco, College Station, Corpus

# How far apart an Austin date and a road-trip date can be and still be
# considered "the same tour swing" for the supersedes rule.
SWING_DAYS = 45

# Rough Texas bounding box — only used when a source gives coordinates but no
# usable address string.
TX_BOX = (25.8, 36.5, -106.7, -93.5)

_TX_RE = re.compile(r",\s*(TX|Texas)\b|\bTX\s+\d{5}", re.I)


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def in_texas(city: str | None, address: str | None, lat=None, lon=None) -> bool:
    if _TX_RE.search(f"{city or ''} {address or ''}"):
        return True
    if lat is not None and lon is not None:
        lo_lat, hi_lat, lo_lon, hi_lon = TX_BOX
        return lo_lat <= lat <= hi_lat and lo_lon <= lon <= hi_lon
    return False


def distance_from_austin(lat, lon) -> float | None:
    if lat is None or lon is None:
        return None
    return haversine_miles(AUSTIN_LAT, AUSTIN_LON, lat, lon)


def tier_for(lat, lon, city=None, address=None) -> tuple[int, float | None]:
    """Return (tier, distance_miles). Falls back to city-name matching when a
    source gives us no coordinates."""
    d = distance_from_austin(lat, lon)

    if d is None:
        blob = f"{city or ''} {address or ''}".lower()
        if "austin" in blob:
            return 1, None
        if in_texas(city, address) and any(
            m in blob for m in ("dallas", "fort worth", "houston", "san antonio",
                                "waco", "college station", "corpus")
        ):
            return 2, None
        return 3, None

    if d <= TIER1_MILES:
        return 1, d
    if d <= TIER2_MILES and in_texas(city, address, lat, lon):
        return 2, d
    return 3, d


def apply_austin_rule(events: list[dict], today: date | None = None) -> list[dict]:
    """
    Annotate tier-2 events with their relationship to any Austin date.

    Deliberately an ANNOTATION, not suppression. A Dallas show always alerts —
    it just alerts honestly:

      no Austin date on the calendar  -> austin_status = "unknown"
          "no Austin date announced yet — this tour might not be fully routed"

      Austin date within ±45 days     -> austin_status = "superseded"
          the road trip is probably unnecessary; downgrade the nagging

      Austin date appears AFTER we    -> austin_status = "owed_an_apology"
      already bought road-trip tickets
          the app says sorry. sincerely. that's the whole joke.
    """
    today = today or date.today()
    austin_dates = [
        _d(e["starts_at"]) for e in events
        if e.get("tier") == 1 and _d(e["starts_at"]) and _d(e["starts_at"]) >= today
    ]

    for e in events:
        if e.get("tier") != 2:
            e["austin_status"] = None
            continue
        ed = _d(e["starts_at"])
        if not ed:
            e["austin_status"] = "unknown"
            continue
        near = [a for a in austin_dates if abs((a - ed).days) <= SWING_DAYS]
        if not near:
            e["austin_status"] = "unknown"
        elif e.get("tickets_bought"):
            e["austin_status"] = "owed_an_apology"
        else:
            e["austin_status"] = "superseded"
    return events


def _d(s: str | None):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None
