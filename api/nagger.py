"""
The escalation ladder.

The nagging IS the product. Everything else is a database.

Two schedules, because the two failure modes are shaped differently:

TIER 1 (Austin) — anchored to the ANNOUNCEMENT, because for a Cap City or
Mothership run the announcement *is* the emergency. Tickets go on sale the same
day and the window is hours, not weeks.

    L0  immediately          the news, loud, buy link front and centre
    L1  +2h                  still nothing from you
    L2  +6h                  openly disappointed
    L3  next morning 9am     personal. invokes past failures.
    L4+ daily at 9am         weary. has stopped expecting better.

TIER 2 (road trip) — anchored to the DECISION DEADLINE, because there's real
time to think and the question isn't "buy now", it's "are we driving?"

    L0  immediately          heads up, with the honest caveat about Austin
    L1  deadline − 7d        starting to matter
    L2  deadline − 2d        decide
    L3  deadline             last call

TIER 3 never nags. It's a daydream list.

Stopping rules — all of them matter:
  · Any explicit decision stops everything. got_tickets, cant_make_it, and
    passing are equally final. The app is not entitled to keep shouting once a
    human has actually chosen.
  · Opening the app is NOT a decision, so 'seen' does not stop the ladder.
  · A superseded road trip (he's also coming to Austin) never escalates past L0.
  · Nothing nags about a show that already happened.

Catch-up behaviour: if the poller was down for two days we do NOT fire five
notifications in a row. We jump to the highest level that's currently due and
record it, so the ladder stays monotonic and the phone stays usable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("America/Chicago")
MORNING = time(9, 0)

TIER1_MAX_LEVEL = 40      # a very long-running show announcement; a backstop, not a policy
TIER2_LEVELS = 3

# Fallback when a show has no published on-sale date: assume the decision has to
# be made three weeks out. Sanity-check this against reality — see SPEC open Qs.
DEFAULT_DECISION_LEAD = timedelta(days=21)


@dataclass
class NagPlan:
    user_id: str
    event_id: str
    level: int
    tier: int


# ──────────────────────────────────────────────────────────────────────────────
# Schedules
# ──────────────────────────────────────────────────────────────────────────────

def _next_morning(after: datetime) -> datetime:
    """The next 9am local strictly after `after`."""
    candidate = datetime.combine(after.date(), MORNING, tzinfo=LOCAL)
    if candidate <= after:
        candidate += timedelta(days=1)
    return candidate


def tier1_due_at(level: int, first_seen: datetime) -> datetime:
    if level == 0:
        return first_seen
    if level == 1:
        return first_seen + timedelta(hours=2)
    if level == 2:
        return first_seen + timedelta(hours=6)
    # L3 is "next morning", but never less than 12h after the announcement —
    # an 8pm announcement should not produce a 9am nag 13 hours later *and*
    # count as the whole overnight escalation.
    l3 = _next_morning(first_seen + timedelta(hours=12))
    if level == 3:
        return l3
    return l3 + timedelta(days=level - 3)


def decision_deadline(onsale_at: datetime | None, show_at: datetime,
                      first_seen: datetime | None = None) -> datetime:
    """
    When the road-trip decision has to be made.

    Defensive against a nonsense on-sale date, because one source is already
    known to emit them — Ticketmaster uses 1900-01-01 as a sentinel for "no
    meaningful on-sale date". The source filters that, but this is the layer
    where a bad value does real damage: a deadline in the past makes every
    escalation level overdue at once, so a show announced this morning would get
    "Last call" before lunch. Anything at or before the announcement is not a
    deadline, it's a data error — fall back to the lead-time guess.
    """
    fallback = show_at - DEFAULT_DECISION_LEAD
    if not onsale_at:
        return fallback
    if onsale_at >= show_at:
        return fallback
    if first_seen and onsale_at <= first_seen:
        return fallback
    return onsale_at


def tier2_due_at(level: int, first_seen: datetime, deadline: datetime) -> datetime:
    if level == 0:
        return first_seen
    offsets = {1: timedelta(days=7), 2: timedelta(days=2), 3: timedelta(0)}
    if level not in offsets:
        return datetime.max.replace(tzinfo=LOCAL)
    # Never schedule a reminder before we even knew about the show.
    return max(deadline - offsets[level], first_seen)


# ──────────────────────────────────────────────────────────────────────────────
# The decision
# ──────────────────────────────────────────────────────────────────────────────

def due_level(now: datetime, first_seen: datetime, tier: int, sent: list[int],
              show_at: datetime, onsale_at: datetime | None = None,
              austin_status: str | None = None) -> int | None:
    """Highest level currently due and not yet sent, or None."""
    if tier == 3:
        return None
    if show_at < now:
        return None

    next_level = (max(sent) + 1) if sent else 0

    if tier == 1:
        max_level = TIER1_MAX_LEVEL
        due_at = lambda lv: tier1_due_at(lv, first_seen)
    else:
        # A road trip he's superseded gets the heads-up and then shuts up.
        max_level = 0 if austin_status == "superseded" else TIER2_LEVELS
        deadline = decision_deadline(onsale_at, show_at, first_seen)
        due_at = lambda lv: tier2_due_at(lv, first_seen, deadline)

    if next_level > max_level:
        return None

    # FIRST CONTACT IS ALWAYS THE NEWS.
    #
    # Polling is hourly, so a show can be three hours old before we ever see it.
    # Without this, catch-up would skip straight to level 2 and the very first
    # thing a user ever hears about the show would be the app complaining about
    # their silence. Tell them it exists first; catch up on the next tick.
    if not sent:
        return 0 if due_at(0) <= now else None

    # Walk forward to the highest level that's due — catch-up without a burst.
    best = None
    for lv in range(next_level, max_level + 1):
        if due_at(lv) <= now:
            best = lv
        else:
            break
    return best


def plan(con, now: datetime | None = None) -> list[NagPlan]:
    """Everything owed right now, across every user and every open show."""
    from . import db

    now = now or datetime.now(LOCAL)
    plans: list[NagPlan] = []
    people = db.users(con)
    if not people:
        return plans

    for ev in db.upcoming(con):
        if ev["tier"] == 3:
            continue
        show_at = _parse(ev["starts_at"])
        first_seen = _parse(ev["first_seen_at"])
        onsale = _parse(ev["onsale_at"]) if ev["onsale_at"] else None
        if not show_at or not first_seen:
            continue

        for user in people:
            if db.state_of(con, user["id"], ev["id"]) in db.DECIDED:
                continue
            lv = due_level(now, first_seen, ev["tier"], db.nags_sent(con, user["id"], ev["id"]),
                           show_at, onsale, ev["austin_status"])
            if lv is not None:
                plans.append(NagPlan(user["id"], ev["id"], lv, ev["tier"]))
    return plans


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.combine(date.fromisoformat(s[:10]), time(0, 0))
        except ValueError:
            return None
    return d.replace(tzinfo=LOCAL) if d.tzinfo is None else d.astimezone(LOCAL)
