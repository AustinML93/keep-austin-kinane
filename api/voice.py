"""
Everything the app says.

⚠️ READ THIS BEFORE ADDING A LINE ⚠️

All of it is ORIGINAL COPY, attributed to NOBODY. It is not, and must never
become, a place where lines get attributed to Kyle Kinane. An LLM will happily
produce something that sounds exactly like him and is entirely invented, and
putting fabricated words in a real person's mouth is the one thing this project
refuses to do. If a line is attributed, it is sourced — with a link to the clip
or transcript — and it lives with the bits, not here.

Same rule for "Uncle BBQ": the app never explains it and never invents an origin.

THE REGISTER
    Warm, defeated, over-articulated grandeur about mundane misery. Specific
    concrete images, not abstractions. Kind underneath the grime — the nags
    escalate in DISAPPOINTMENT, not volume. By day four the app isn't angry, it
    has simply stopped expecting better.

    Things it is not: snarky, cruel, exclamatory, or clever at the user's
    expense. It is on their side. It is just tired.

WHY IT'S ALL IN ONE FILE
    Copy used to live here AND hardcoded in app.js and sw.js, which guarantees
    drift. Now the server owns every string: the UI fetches /api/copy, and push
    notifications carry their own acknowledgement text in the payload. The front
    end keeps terse fallbacks for when it's offline, and nothing else.

VARIETY
    Each level has several variants, chosen deterministically from a seed. Same
    show plus same level always gives the same line — so a nag doesn't reword
    itself between two glances at the same notification — but different shows get
    different phrasings, and the app doesn't wear out its four jokes in a month.
"""

from __future__ import annotations

import hashlib

# ──────────────────────────────────────────────────────────────────────────────
# Deterministic variety
# ──────────────────────────────────────────────────────────────────────────────

def pick(options: list, seed: str):
    """Stable choice from `options` for a given seed. No randomness anywhere."""
    if not options:
        return None
    h = int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16)
    return options[h % len(options)]


# ──────────────────────────────────────────────────────────────────────────────
# Tier 1 — Austin. The announcement IS the emergency.
# ──────────────────────────────────────────────────────────────────────────────

TIER1 = {
    0: [
        ("KINANE. AUSTIN.",
         "{venue}, {when}. This is the entire reason this thing exists. Go."),
        ("He's coming here.",
         "{venue}, {when}. Stop reading this and go get tickets. I'll wait."),
        ("AUSTIN. KINANE. NOW.",
         "{venue}, {when}. Good news, arriving at an inconvenient moment, as it "
         "always does."),
        ("It's happening. Here. Us.",
         "{venue}, {when}. Everything this app has ever done was leading up to "
         "sending you this."),
    ],
    1: [
        ("Two hours. Nothing from you.",
         "{venue}, {when}. Tickets do not wait for a man to finish what he's doing."),
        ("Still here. Still Kinane.",
         "{venue}, {when}. I'm going to keep bringing this up. That's the deal we made."),
        ("This is me, following up.",
         "{venue}, {when}. Two hours of you thinking about it and me watching you "
         "think about it."),
    ],
    2: [
        ("This is the part where it sells out.",
         "Six hours. {venue}, {when}. Somebody who likes him less than you is buying "
         "your seat, and they will talk through the whole set."),
        ("Six hours of nothing.",
         "{venue}, {when}. I'm not angry. I've just watched this happen before and I "
         "recognise the shape of it."),
        ("The window is closing and you're in it.",
         "{venue}, {when}. Not a lot of runway left on this one."),
    ],
    3: [
        ("Morning. He's still coming.",
         "{venue}, {when}. You know exactly how the next part feels. You have the "
         "option not to feel it."),
        ("New day. Still no tickets.",
         "{venue}, {when}. We have been here before and I remember how it turned out "
         "for both of us."),
        ("Sleep on it, did you.",
         "{venue}, {when}. It's still true this morning. That's the thing about it "
         "being true."),
    ],
    4: [
        ("Day {day}.",
         "{venue}, {when}. Not asking anymore. Just noting it, the way you'd note a "
         "leak you've decided to live with."),
        ("Day {day} of this.",
         "{venue}, {when}. The offer stands until it doesn't. I'll let you know which."),
        ("Still day {day}. Still nothing.",
         "{venue}, {when}. I've stopped expecting a different answer. I'm just here now."),
    ],
}

# Cross-user awareness — the best comedy in the app, and it falls straight out of
# the shared-state model rather than being written on top of it.
TIER1_OTHER_HAS_TICKETS = {
    2: [
        ("{other} bought tickets. You did nothing.",
         "{venue}, {when}. He didn't even hesitate. Sat right down and did it."),
        ("{other}'s in. You're a maybe.",
         "{venue}, {when}. One of you handled this in about ninety seconds."),
    ],
    3: [
        ("{other} still has tickets. You still don't.",
         "{venue}, {when}. He's told people. It's part of his personality now."),
        ("{other} is going. This is the situation.",
         "{venue}, {when}. He's already deciding what he's wearing. Catch up."),
    ],
    4: [
        ("{other}'s going. Day {day} of you thinking about it.",
         "{venue}, {when}. At some point this stops being indecision and starts being "
         "a decision."),
        ("{other} is going alone, apparently.",
         "{venue}, {when}. Day {day}. He'll have a fine time. He'll mention it."),
    ],
}

# ──────────────────────────────────────────────────────────────────────────────
# Tier 2 — road trip. Slower, and honest about what it doesn't know.
# ──────────────────────────────────────────────────────────────────────────────

TIER2 = {
    0: [
        ("Kinane, {city}.", "{venue}, {when}. {austin_note}"),
        ("There's a {city} date.", "{venue}, {when}. {austin_note}"),
    ],
    1: [
        ("{city} is starting to matter.",
         "A week out from actually deciding. {venue}, {when}. {austin_note}"),
        ("About that {city} show.",
         "{venue}, {when}. Roughly a week before this becomes a real question."),
    ],
    2: [
        ("Decide about {city}.",
         "Two days. {venue}, {when}. This is where you find out how much you meant it."),
        ("{city}. Two days.",
         "{venue}, {when}. Driving somewhere for a comedian is a young man's move, "
         "which is exactly why it's still available to you."),
    ],
    3: [
        ("Last call on {city}.",
         "{venue}, {when}. After this it's just a story about how you almost went."),
        ("{city} closes today.",
         "{venue}, {when}. Last chance to make this a thing that happened."),
    ],
}

# The apology needs its OWN title. Bolting it onto the generic road-trip line
# meant the notification announced a Dallas date and then apologised, which
# buries the only genuinely funny thing the tiering rule produces.
APOLOGY = [
    ("Well. He's coming to Austin.",
     "{venue}, {when} — the one you already bought. He announced Austin afterwards. "
     "That one's on the app, and the app is sorry."),
    ("About that drive.",
     "You bought {city}. He has since announced Austin. I had one job and the timing "
     "was not part of it."),
]

AUSTIN_NOTE = {
    "unknown": "No Austin date announced yet, so this might be the only shot — or the "
               "warm-up for one three weeks from now. I genuinely can't tell you which.",
    "superseded": "He's coming to Austin too, so nobody has to drive anywhere. Filed "
                  "away in case you want the drive anyway.",
    # Kept for the in-app row; the notification uses APOLOGY instead.
    "owed_an_apology": "He announced Austin after you bought these. That one's on the "
                       "app, and the app is sorry.",
}

# ──────────────────────────────────────────────────────────────────────────────
# Status readout — the signature piece. Silence has to be EARNED.
# ──────────────────────────────────────────────────────────────────────────────

ALL_QUIET = [
    "{n} sources watched, nothing to report. He's out there somewhere, presumably "
    "explaining something to a bartender who didn't ask.",
    "{n} sources, all of them awake, none of them with news. This is what working "
    "correctly looks like, unfortunately.",
    "Everything's being watched and nothing is happening. Enjoy the quiet — it's the "
    "honest kind.",
    "{n} sources checked. He is not coming here yet. I'll know before you do.",
    "Nothing. But it's a well-informed nothing, which is the best I can offer.",
]

BLIND = [
    "{source} has redesigned its website with real ambition and I can't read it "
    "anymore. Go look yourself, and don't trust my silence until this is fixed.",
    "I've lost sight of {source}. Everything else is still watched, but assume I'm "
    "missing whatever they're announcing.",
]

CONFIG_LOST = [
    "The tour feed changed its locks. I can see the door, I just can't get in. Assume "
    "I'm missing things.",
    "Something upstream rotated its credentials and didn't tell me. I'm partially "
    "blind until that's sorted.",
]

# ──────────────────────────────────────────────────────────────────────────────
# Everything the interface says
# ──────────────────────────────────────────────────────────────────────────────

UI = {
    "empty_shows": "Nothing on the calendar. Which is not the same as nothing coming — "
                   "it's just all I can see from here.",
    "tier1_heading": "Austin",
    "tier2_heading": "Road trip",
    "tier3_heading": "Daydream",
    "tier3_note": "Nowhere near you. Here in case a cheap flight and a bad idea line up.",

    "bit_heading": "Bit of the day",
    "bit_empty": "Nothing in the pool yet. Put something in the playlist and I'll find it.",
    "holds_up_heading": "Holds up",
    "holds_up_note": "The ones that survived a second look. Longest first, in case "
                     "you've got the evening.",
    "shelf_heading": "The shelf",
    "shelf_note": "Full sets. For when there's an hour and nowhere to be.",

    "rate_struts": "🔧 Shocks & Struts",
    "rate_fine": "〰️ Runs Fine",
    "rate_gout": "💥 Gout Flare-Up",
    "rate_thanks": "Noted.",

    "add_summary": "Somebody told you about a show",
    "add_venue_placeholder": "Venue (Comedy Mothership, Cap City…)",
    "add_city_placeholder": "City",
    "add_url_placeholder": "Ticket link, if you have one",
    "add_note_placeholder": "Who told you?",
    "add_submit": "Add it",
    "add_failed": "Couldn't add that one.",
    "add_needs_link": "Open your magic link first.",
    "add_tier3_warning": "That landed in Daydream, so it won't notify anyone. If that's "
                         "wrong, the venue or city wasn't recognised — try adding the "
                         "city explicitly.",

    "install_cta": "Install Uncle BBQ",
    "install_why": "Puts it on your home screen where you'll actually see it.",
    "install_manual": "To install: ⋮ (top right) → Add to Home screen → Install.",

    "push_on": "Notifications on",
    "push_off": "Turn on notifications",
    "push_blocked": "Notifications are blocked",
    "push_blocked_help": "Chrome ⋮ → Settings → Site settings → Notifications",
    "push_unsupported": "This browser can't do notifications. Android Chrome can.",
    "push_declined": "Without notifications this is just a website that knows things.",

    "state_unseen": "—",
    "state_seen": "seen it",
    "state_got_tickets": "GOT 'EM",
    "state_cant_make_it": "can't go",
    "state_passing": "passing",
    "act_got": "GOT 'EM",
    "act_cant": "Can't go",

    "offline": "Can't reach the server. Which is its own kind of answer.",
    "watching_summary": "What's being watched",
}

# Text the push notification carries with it, so the service worker holds no
# voice of its own beyond a bare fallback.
ACK = {
    "got_tickets": "Got 'em. I'll stop.",
    "cant_make_it": "Noted — you can't go. I'll leave it alone.",
    "undone": "Undone. You're still on the hook.",
    "undo_label": "UNDO",
}


# ──────────────────────────────────────────────────────────────────────────────
# Rendering
# ──────────────────────────────────────────────────────────────────────────────

def nag(level: int, tier: int, *, venue: str, when: str, city: str = "",
        other: str | None = None, other_has_tickets: bool = False,
        austin_status: str | None = None, day: int = 0,
        seed: str = "") -> tuple[str, str]:
    """
    (title, body) for a nag. Never attributes anything to anyone.

    `seed` should identify the show so the wording is stable for it — the same
    alert re-read an hour later must not have reworded itself.
    """
    if tier == 1:
        lv = min(level, 4)
        if other_has_tickets and other and lv in TIER1_OTHER_HAS_TICKETS:
            options = TIER1_OTHER_HAS_TICKETS[lv]
        else:
            options = TIER1[lv]
    elif austin_status == "owed_an_apology":
        # Overrides the level entirely. There is nothing to escalate — the app
        # got it wrong and says so.
        lv, options = level, APOLOGY
    else:
        lv = min(level, 3)
        options = TIER2[lv]

    title, body = pick(options, f"{seed}|{tier}|{level}")
    # "Dallas, TX" is right in a data row and wrong in a sentence — "Last call on
    # Dallas" reads like a person wrote it, "Last call on Dallas, TX" does not.
    short_city = (city or "").split(",")[0].strip() or "out of town"

    fields = {
        "venue": venue or "somewhere",
        "when": when,
        "city": short_city,
        "other": other or "Your friend",
        "day": max(day, 1),
        "austin_note": AUSTIN_NOTE.get(austin_status or "", ""),
    }
    return title.format(**fields), " ".join(body.format(**fields).split())


def status_line(n_sources: int, blind: list[str], config_lost: bool = False,
                seed: str = "") -> str:
    """
    The home-screen line that doubles as a health check.

    Seeded by the day so it varies without flickering between two glances at the
    same screen.
    """
    if config_lost:
        return pick(CONFIG_LOST, seed or "config")
    if blind:
        return pick(BLIND, seed or "blind").format(source=blind[0])
    return pick(ALL_QUIET, seed or "quiet").format(n=n_sources)


def ui_copy() -> dict:
    """Everything the front end needs, so no copy is duplicated in JavaScript."""
    return dict(UI)
