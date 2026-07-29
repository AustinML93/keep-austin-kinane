"""
The bit of the day, the pool, and the ratings.

The pool is fed from two places (a synced YouTube playlist, and pasted URLs) but
RATED in exactly one: here. That separation is what makes the whole thing work —
the playlist is an inbox and is allowed to be messy, because Mike's rating is
the filter, not his tidiness.

Rotation weights:

    🔧 Shocks & Struts   3   holds up. heavy rotation.
    〰️ Runs Fine          1   no complaints, no poetry. filler.
    (unrated)            2   it's in the playlist, so it was worth saving —
                             mixed in often enough to actually get judged.
    💥 Gout Flare-Up      —   blocked. never served again.

Unrated sitting BETWEEN the two rated tiers is deliberate. It's the mechanism
that makes the pool improve over time: new clips surface often enough to earn a
verdict, without crowding out the ones already known to land.

Specials are never served as a bit of the day. Nobody wants an hour of video as
their daily hit; that's what the shelf is for.

Asymmetry, per the design: Mike's rating is CONTROL, Rob's is SIGNAL. Rob can
say a bit didn't do it for him — nothing he presses alters the pool.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from . import db

WEIGHTS = {"struts": 3, "fine": 1, None: 2}
NO_REPEAT_DAYS = 30
DAILY_KINDS = ("short", "clip")


# ──────────────────────────────────────────────────────────────────────────────
# Pool
# ──────────────────────────────────────────────────────────────────────────────

def upsert_bit(con, item: dict, *, added_by: str, source: str) -> bool:
    """Insert or refresh a bit. Returns True if newly added."""
    bid = item.get("video_id") or item.get("url")
    if not bid:
        return False
    ts = db.now()
    existing = con.execute("SELECT id FROM bits WHERE id = ?", (bid,)).fetchone()

    if existing:
        # Refresh metadata but NEVER touch state or custom_title — a re-sync
        # must not resurrect something rated Gout Flare-Up, or overwrite the
        # name Mike gave it.
        con.execute(
            """UPDATE bits SET title=COALESCE(?,title), channel=COALESCE(?,channel),
                 thumbnail=COALESCE(?,thumbnail), duration_s=COALESCE(?,duration_s),
                 kind=COALESCE(?,kind), vertical=?, embeddable=?, available=1,
                 last_checked_at=? WHERE id=?""",
            (item.get("title"), item.get("channel"), item.get("thumbnail"),
             item.get("duration_s"), item.get("kind"), int(bool(item.get("vertical"))),
             int(bool(item.get("embeddable", True))), ts, bid),
        )
        con.commit()
        return False

    con.execute(
        """INSERT INTO bits (id, provider, video_id, url, title, channel, thumbnail,
                             duration_s, kind, vertical, embeddable, state, added_by,
                             source, added_at, last_checked_at, available)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?,1)""",
        (bid, item.get("provider", "youtube"), item.get("video_id"), item.get("url"),
         item.get("title"), item.get("channel"), item.get("thumbnail"),
         item.get("duration_s", 0), item.get("kind", "clip"),
         int(bool(item.get("vertical"))), int(bool(item.get("embeddable", True))),
         added_by, source, ts, ts),
    )
    con.commit()
    return True


def sync_playlist(con, playlist_id: str, *, added_by: str = "mike") -> dict:
    """
    Pull the playlist in. Anything new lands ACTIVE and unrated.

    Active-on-arrival is the right default: Mike put it in the playlist, and
    that IS the endorsement. Landing as an unrated 'candidate' would mean an
    empty pool until he sat down and processed a queue — which is the chore
    this design exists to avoid.
    """
    from .sources.youtube import fetch_playlist

    items, how = fetch_playlist(playlist_id)
    added = sum(1 for i in items if upsert_bit(con, i, added_by=added_by, source="playlist"))

    # A video pulled from the playlist isn't deleted — it just stops being
    # served. Ratings and history are worth keeping.
    seen = {i.get("video_id") for i in items}
    dropped = 0
    if seen:
        rows = con.execute(
            "SELECT id FROM bits WHERE source='playlist' AND available=1").fetchall()
        for r in rows:
            if r["id"] not in seen:
                con.execute("UPDATE bits SET available=0 WHERE id=?", (r["id"],))
                dropped += 1
    con.commit()
    return {"fetched": len(items), "added": added, "dropped": dropped, "via": how}


def add_url(con, url: str, *, added_by: str) -> tuple[dict | None, bool]:
    """The flexible path: paste anything."""
    from .sources.youtube import classify, lookup

    item = lookup(url)
    if not item:
        return None, False
    item.setdefault("duration_s", 0)
    item.setdefault("kind", classify(item.get("duration_s") or 0))
    is_new = upsert_bit(con, item, added_by=added_by, source="manual")
    return item, is_new


def rename(con, bit_id: str, custom_title: str | None) -> None:
    """
    Mike's name for a bit — "the one about the raccoon".

    Worth having even though YouTube gives us a title, because the way he and
    Rob actually refer to a bit is the closest thing to real voice research this
    project will ever get.
    """
    con.execute("UPDATE bits SET custom_title=? WHERE id=?",
                (custom_title or None, bit_id))
    con.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Ratings
# ──────────────────────────────────────────────────────────────────────────────

def rate(con, bit_id: str, user_id: str, rating: str, *, is_curator: bool) -> None:
    """
    Record a rating. Only the curator's rating changes the pool.

    Rob gets all three buttons and none of the consequences — he can say a bit
    didn't do it for him, and that's signal Mike sees, but nothing he presses
    can remove a clip.
    """
    con.execute(
        """INSERT INTO bit_ratings (bit_id, user_id, rating, rated_at) VALUES (?,?,?,?)
           ON CONFLICT(bit_id, user_id) DO UPDATE SET
             rating=excluded.rating, rated_at=excluded.rated_at""",
        (bit_id, user_id, rating, db.now()),
    )
    if is_curator:
        state = "blocked" if rating == "gout" else "active"
        con.execute("UPDATE bits SET state=? WHERE id=?", (state, bit_id))
    con.commit()


def curator_rating(con, bit_id: str) -> str | None:
    row = con.execute(
        """SELECT r.rating FROM bit_ratings r JOIN users u ON u.id = r.user_id
           WHERE r.bit_id = ? AND u.is_curator = 1""", (bit_id,)).fetchone()
    return row["rating"] if row else None


def ratings_for(con, bit_id: str) -> dict:
    return {r["user_id"]: r["rating"] for r in con.execute(
        "SELECT user_id, rating FROM bit_ratings WHERE bit_id=?", (bit_id,))}


# ──────────────────────────────────────────────────────────────────────────────
# The daily pick
# ──────────────────────────────────────────────────────────────────────────────

def eligible(con, on: date) -> list[dict]:
    """
    The pool for a given day.

    ⚠️ TODAY'S OWN HISTORY IS EXCLUDED FROM THE EXCLUSION.

    Serving the bit records it, and if that record counted against eligibility
    the pick would change the moment anyone fetched it — so Mike and Rob would
    see different "bits of the day" depending on who opened the app first. The
    whole point is that they compute the same answer, not receive a broadcast.

    So: skip what was served in the last 30 days, up to but NOT including today.
    """
    cutoff = (on - timedelta(days=NO_REPEAT_DAYS)).isoformat()
    rows = con.execute(
        f"""SELECT b.* FROM bits b
            WHERE b.state='active' AND b.available=1 AND b.embeddable=1
              AND b.kind IN ({','.join('?' * len(DAILY_KINDS))})
              AND b.id NOT IN (
                    SELECT bit_id FROM bit_history WHERE served_on > ? AND served_on < ?)
            ORDER BY b.id""",
        (*DAILY_KINDS, cutoff, on.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


def pick_for(con, on: date | None = None) -> dict | None:
    """
    The bit of the day.

    Deterministic from the date — no scheduler, no stored 'today's pick', and
    both users see the same clip all day because they're computing the same
    answer, not receiving a broadcast. It changes at midnight because the date
    changed.
    """
    on = on or date.today()
    pool = eligible(con, on)
    if not pool:
        # Everything's been served recently — drop the no-repeat rule rather
        # than show nothing.
        pool = [dict(r) for r in con.execute(
            f"""SELECT * FROM bits WHERE state='active' AND available=1 AND embeddable=1
                AND kind IN ({','.join('?' * len(DAILY_KINDS))}) ORDER BY id""",
            DAILY_KINDS)]
    if not pool:
        return None

    weighted = []
    for b in pool:
        weighted.append((b, WEIGHTS.get(curator_rating(con, b["id"]), 2)))
    total = sum(w for _, w in weighted)

    seed = int(hashlib.sha256(on.isoformat().encode()).hexdigest()[:12], 16)
    target = seed % total
    running = 0
    for b, w in weighted:
        running += w
        if target < running:
            return b
    return weighted[-1][0]


def record_served(con, bit_id: str, on: date | None = None) -> None:
    con.execute(
        "INSERT OR IGNORE INTO bit_history (bit_id, served_on) VALUES (?,?)",
        (bit_id, (on or date.today()).isoformat()))
    con.commit()


def specials(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        """SELECT * FROM bits WHERE kind='special' AND state='active' AND available=1
           ORDER BY added_at DESC""")]
