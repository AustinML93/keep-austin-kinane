"""
What the app says.

⚠️ READ THIS BEFORE ADDING A LINE ⚠️

Everything in this file is ORIGINAL COPY, written in his register and attributed
to NOBODY. It is not, and must never become, a place where lines get attributed
to Kyle Kinane. An LLM will happily produce something that sounds exactly like
him and is entirely invented, and putting fabricated words in a real person's
mouth is the one thing this project refuses to do.

If a line is attributed, it is sourced — with a link to the clip or transcript
it came from — and it lives with the bits, not here. Same rule for Uncle BBQ:
the app never explains it.

The register: warm, defeated, over-articulated grandeur about mundane misery.
Kind underneath the grime. The nags escalate in *disappointment*, not volume —
by level 4 the app isn't angry, it has simply stopped expecting better.

STATUS: first pass. Good enough to build the ladder against; the real voice pass
is its own step and deserves a sitting.
"""

from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────────────
# Tier 1 — Austin. The announcement is the emergency.
# ──────────────────────────────────────────────────────────────────────────────

TIER1 = {
    0: ("KINANE. AUSTIN.",
        "{venue}, {when}. This is the whole reason you have this app. Go."),
    1: ("Still Kinane. Still Austin.",
        "Two hours and not a word from you. {venue}, {when}. Tickets do not "
        "wait for a man to finish what he's doing."),
    2: ("This is the part where it sells out",
        "Six hours. {venue}, {when}. Somebody who likes him less than you is "
        "buying your seat right now, and they'll talk through it."),
    3: ("Morning. He's still coming.",
        "{venue}, {when}. You've done this before. You know exactly how the "
        "next part feels, and you have the option not to feel it."),
    4: ("Day {day}.",
        "{venue}, {when}. Not asking anymore, just noting it. The offer stands "
        "until it doesn't."),
}

# Cross-user awareness. The single best comedy in the app falls straight out of
# the data model — one of them has tickets and the other is asleep.
TIER1_OTHER_HAS_TICKETS = {
    2: ("{other} bought tickets. You did nothing.",
        "{venue}, {when}. He didn't even hesitate. Sat right down and did it."),
    3: ("{other} still has tickets. You still don't.",
        "{venue}, {when}. He's told people. He's made it part of his "
        "personality now. Catch up."),
    4: ("{other}'s going. Day {day} of you thinking about it.",
        "{venue}, {when}. At some point this stops being indecision and starts "
        "being a decision."),
}

# ──────────────────────────────────────────────────────────────────────────────
# Tier 2 — road trip. Slower, and honest about what it doesn't know.
# ──────────────────────────────────────────────────────────────────────────────

TIER2 = {
    0: ("Kinane, {city}",
        "{venue}, {when}. {austin_note}"),
    1: ("{city} is starting to matter",
        "{venue}, {when}. A week out from having to actually decide. "
        "{austin_note}"),
    2: ("Decide about {city}",
        "{venue}, {when}. Two days. This is the part where you find out how "
        "much you meant it."),
    3: ("Last call on {city}",
        "{venue}, {when}. After this it's just a story about how you almost went."),
}

AUSTIN_NOTE = {
    "unknown": "No Austin date announced yet — this tour might not be fully routed, "
               "so this could be the only shot or it could be the warm-up.",
    "superseded": "He's coming to Austin too, so nobody has to drive anywhere. "
                  "Filed away in case you want the drive anyway.",
    "owed_an_apology": "He announced Austin. After you bought these. That one's on "
                       "the app, and the app is sorry.",
}

# ──────────────────────────────────────────────────────────────────────────────
# Status readout — the signature piece. Silence has to be EARNED.
# ──────────────────────────────────────────────────────────────────────────────

ALL_QUIET = [
    "{n} sources watched. Nothing to report. He's out there somewhere, "
    "presumably in a parking lot, reconsidering something.",
    "{n} sources, all of them awake, none of them with news. This is what "
    "working correctly looks like, unfortunately.",
    "Everything's being watched and nothing's happening. Enjoy the quiet, it's "
    "the honest kind.",
]

BLIND = ("{source} has redesigned its website with real ambition and I can't "
         "read it anymore. Go look yourself, and don't trust my silence "
         "until this is fixed.")

CONFIG_LOST = ("The tour feed changed its locks. I can still see the door, I "
               "just can't get in. Assume I'm missing things.")

NOTHING_UPCOMING = ("Nothing on the calendar. Which is not the same as nothing "
                    "coming — it's just all I can see from here.")


def nag(level: int, tier: int, *, venue: str, when: str, city: str = "",
        other: str | None = None, other_has_tickets: bool = False,
        austin_status: str | None = None, day: int = 0) -> tuple[str, str]:
    """Return (title, body) for a nag. Never attributes anything to anyone."""
    if tier == 1:
        table = TIER1
        if other_has_tickets and other:
            table = {**TIER1, **{k: v for k, v in TIER1_OTHER_HAS_TICKETS.items()}}
        title, body = table.get(min(level, 4), table[4])
    else:
        title, body = TIER2.get(min(level, 3), TIER2[3])

    fields = {
        "venue": venue or "somewhere", "when": when, "city": city or "out of town",
        "other": other or "Your friend", "day": max(day, 1),
        "austin_note": AUSTIN_NOTE.get(austin_status or "", ""),
    }
    return title.format(**fields), body.format(**fields).strip()


def status_line(n_sources: int, blind: list[str], config_lost: bool = False) -> str:
    if config_lost:
        return CONFIG_LOST
    if blind:
        return BLIND.format(source=blind[0])
    # Deterministic pick so the line is stable within a day rather than
    # flickering on every poll. Seeded by the caller in practice.
    return ALL_QUIET[n_sources % len(ALL_QUIET)].format(n=n_sources)
