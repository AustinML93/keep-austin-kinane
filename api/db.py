"""
SQLite storage. WAL mode, single writer.

The whole app runs in one process — FastAPI plus a background scheduler thread —
so there is exactly one writer and SQLite needs no ceremony to be safe.

User / bit tables land with their features; this is the show-tracking core.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("KAK_DB", "data/kak.sqlite3"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id                TEXT PRIMARY KEY,   -- dedupe key: date|venue|time
    title             TEXT,
    venue             TEXT,
    city              TEXT,
    address           TEXT,
    latitude          REAL,
    longitude         REAL,
    starts_at         TEXT NOT NULL,
    onsale_at         TEXT,
    ticket_url        TEXT,
    presale_active    INTEGER DEFAULT 0,
    tier              INTEGER,
    distance_mi       REAL,
    austin_status     TEXT,
    artist_confidence REAL DEFAULT 1.0,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_sources (
    event_id     TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    external_id  TEXT,
    raw          TEXT,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (event_id, source_id)
);

CREATE TABLE IF NOT EXISTS sources (
    id                   TEXT PRIMARY KEY,
    name                 TEXT,
    kind                 TEXT,
    seasonal             INTEGER DEFAULT 0,
    last_attempt_at      TEXT,
    last_success_at      TEXT,
    last_total_seen      INTEGER,
    baseline_total       INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    health               TEXT DEFAULT 'unknown',
    last_error           TEXT,
    last_error_kind      TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,          -- 'mike', 'rob'
    name       TEXT NOT NULL,
    is_curator INTEGER DEFAULT 0,
    token      TEXT UNIQUE NOT NULL,      -- magic-link token; no passwords
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    user_agent TEXT,
    created_at TEXT NOT NULL,
    last_ok_at TEXT,
    failures   INTEGER DEFAULT 0
);

-- The shared state. This is the fun part AND the thing that stops the nagging.
-- 'cant_make_it' matters as much as 'got_tickets': without it the app cannot
-- tell "I've made peace with missing this" from "my phone is in my pocket",
-- and it will harass a man who already made his decision.
CREATE TABLE IF NOT EXISTS user_events (
    user_id         TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'unseen',
    acknowledged_at TEXT,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, event_id)
);

CREATE TABLE IF NOT EXISTS nags (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  TEXT NOT NULL,
    event_id TEXT NOT NULL,
    level    INTEGER NOT NULL,
    sent_at  TEXT NOT NULL,
    channel  TEXT DEFAULT 'push',
    ok       INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Bits. Fed from two places (a synced YouTube playlist and pasted URLs) but
-- rated in exactly one. The playlist is an inbox and is allowed to be messy;
-- the curator's rating is the filter, not his tidiness.
CREATE TABLE IF NOT EXISTS bits (
    id              TEXT PRIMARY KEY,    -- youtube video id, or the url
    provider        TEXT DEFAULT 'youtube',
    video_id        TEXT,
    url             TEXT NOT NULL,
    title           TEXT,                -- the uploader's title
    custom_title    TEXT,                -- "the one about the raccoon"
    channel         TEXT,
    thumbnail       TEXT,
    duration_s      INTEGER DEFAULT 0,
    kind            TEXT DEFAULT 'clip', -- short | clip | special
    vertical        INTEGER DEFAULT 0,   -- 9:16 needs a different container
    embeddable      INTEGER DEFAULT 1,
    available       INTEGER DEFAULT 1,   -- still in the playlist / still exists
    state           TEXT DEFAULT 'active', -- active | blocked (Gout Flare-Up)
    added_by        TEXT,
    source          TEXT,                -- playlist | manual
    added_at        TEXT NOT NULL,
    last_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS bit_ratings (
    bit_id   TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    rating   TEXT NOT NULL,   -- struts | fine | gout
    rated_at TEXT NOT NULL,
    PRIMARY KEY (bit_id, user_id)
);

CREATE TABLE IF NOT EXISTS bit_history (
    bit_id    TEXT NOT NULL,
    served_on TEXT NOT NULL,
    PRIMARY KEY (bit_id, served_on)
);

CREATE INDEX IF NOT EXISTS idx_events_start ON events(starts_at);
CREATE INDEX IF NOT EXISTS idx_events_tier  ON events(tier);
CREATE INDEX IF NOT EXISTS idx_nags_lookup  ON nags(user_id, event_id, level);
CREATE INDEX IF NOT EXISTS idx_bits_pool    ON bits(state, available, kind);
"""

# States that END the nagging. Any explicit decision counts — the app is not
# entitled to keep shouting once a human has actually chosen.
DECIDED = ("got_tickets", "cant_make_it", "passing")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


# ──────────────────────────────────────────────────────────────────────────────
# Events
# ──────────────────────────────────────────────────────────────────────────────

def upsert_event(con, ev, tier: int, distance_mi: float | None) -> bool:
    """Insert or refresh an event. Returns True if it's newly seen."""
    key = ev.dedupe_key()
    ts = now()
    row = con.execute("SELECT id FROM events WHERE id = ?", (key,)).fetchone()

    # A source that gives a date but no showtime (the official feed does this —
    # Cobb's 2026-12-04) must not become a phantom extra row for a night another
    # source already described with real times. Fold it into the earliest known
    # showtime at that venue on that date instead.
    if row is None and not ev.time:
        loose = con.execute(
            "SELECT id FROM events WHERE id LIKE ? ORDER BY id LIMIT 1",
            (ev.loose_key() + "|%",),
        ).fetchone()
        if loose:
            key, row = loose["id"], loose

    is_new = row is None

    if is_new:
        con.execute(
            """INSERT INTO events (id, title, venue, city, address, latitude, longitude,
                                   starts_at, onsale_at, ticket_url, presale_active,
                                   tier, distance_mi, artist_confidence,
                                   first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, ev.title, ev.venue, ev.city, ev.address, ev.latitude, ev.longitude,
             ev.starts_at, ev.onsale_at, ev.ticket_url, int(ev.presale_active),
             tier, distance_mi, ev.artist_confidence, ts, ts),
        )
    else:
        # Refresh the volatile fields; keep the highest confidence we've seen and
        # never clobber a real ticket URL with a null from a thinner source.
        con.execute(
            """UPDATE events SET
                 last_seen_at = ?,
                 ticket_url = COALESCE(?, ticket_url),
                 onsale_at = COALESCE(?, onsale_at),
                 presale_active = ?,
                 artist_confidence = MAX(artist_confidence, ?),
                 tier = ?, distance_mi = COALESCE(?, distance_mi)
               WHERE id = ?""",
            (ts, ev.ticket_url, ev.onsale_at, int(ev.presale_active),
             ev.artist_confidence, tier, distance_mi, key),
        )

    con.execute(
        """INSERT INTO event_sources (event_id, source_id, external_id, raw, last_seen_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(event_id, source_id) DO UPDATE SET
             raw = excluded.raw, last_seen_at = excluded.last_seen_at""",
        (key, ev.source_id, ev.external_id, json.dumps(ev.raw)[:20000], ts),
    )
    return is_new


def add_manual_event(con, *, added_by: str, venue: str, city: str, date: str,
                     time_s: str = "", ticket_url: str | None = None,
                     note: str | None = None) -> tuple[str, bool]:
    """
    A show a human typed in. Returns (event_id, is_new).

    This is a first-class source, not a workaround. The original problem was
    that discovery is social — "sometimes it's my friend, and sometimes it's
    just someone that knows we love him" — and until now the app had no home for
    that signal. It's also the only thing that will ever catch a Comedy
    Mothership booking or a surprise drop-in, since their site blocks us and no
    aggregator carries them.

    Manual events merge with automatic ones by the normal dedupe key, so a show
    typed in from a text message quietly becomes the same row when a real source
    catches up to it.
    """
    from .sources.base import Event
    from .tiering import tier_for
    from .venues import canonical, locate

    lat, lon, resolved_city = locate(venue, city)
    # Canonicalise BEFORE building the event: the dedupe key contains the venue
    # name, so "the Mothership" and "Comedy Mothership" must normalise to the
    # same string or they become two shows on the same night.
    venue = canonical(venue)
    ev = Event(
        source_id="manual",
        external_id=f"manual-{added_by}-{date}",
        starts_at=f"{date}T{time_s}" if time_s else date,
        title=venue,
        venue=venue,
        city=resolved_city or city,
        latitude=lat,
        longitude=lon,
        ticket_url=ticket_url or None,
        artist_confidence=1.0,   # a human vouched for it
        raw={"added_by": added_by, "note": note, "manual": True},
    )
    tier, dist = tier_for(lat, lon, ev.city, None)
    is_new = upsert_event(con, ev, tier, dist)

    con.execute(
        """INSERT INTO sources (id, name, kind, seasonal, health, last_success_at,
                                last_total_seen, baseline_total)
           VALUES ('manual','Typed in by hand','manual',0,'ok',?,0,0)
           ON CONFLICT(id) DO UPDATE SET last_success_at=excluded.last_success_at""",
        (now(),),
    )
    con.commit()
    return ev.dedupe_key(), is_new


def delete_manual_event(con, event_id: str) -> bool:
    """
    Remove a hand-typed show. Only ever removes events that came from `manual`
    and nowhere else — if a real source has since confirmed the same show,
    deleting the manual entry must not delete the show.
    """
    srcs = [r["source_id"] for r in con.execute(
        "SELECT source_id FROM event_sources WHERE event_id = ?", (event_id,))]
    if srcs != ["manual"]:
        return False
    con.execute("DELETE FROM event_sources WHERE event_id = ?", (event_id,))
    con.execute("DELETE FROM user_events WHERE event_id = ?", (event_id,))
    con.execute("DELETE FROM nags WHERE event_id = ?", (event_id,))
    con.execute("DELETE FROM events WHERE id = ?", (event_id,))
    con.commit()
    return True


def upcoming(con, today: str | None = None) -> list[dict]:
    today = today or datetime.now().date().isoformat()
    rows = con.execute(
        "SELECT * FROM events WHERE substr(starts_at,1,10) >= ? ORDER BY starts_at",
        (today,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_austin_status(con, event_id: str, status: str | None) -> None:
    con.execute("UPDATE events SET austin_status = ? WHERE id = ?", (status, event_id))


# ──────────────────────────────────────────────────────────────────────────────
# Source health — the feature that makes silence trustworthy
# ──────────────────────────────────────────────────────────────────────────────

def record_health(con, source, result) -> str:
    """
    Update a source's health from a FetchResult and return the new health state.

        ok           working, and it saw a plausible calendar
        suspicious   returned ZERO events where it reliably used to return many.
                     This is the important one — the silent-breakage guard.
        dormant      seasonal source, currently empty, and that's expected
        degraded     one or two consecutive failures
        down         three or more consecutive failures
        config_failed  credentials/config could not be resolved. Distinct on
                     purpose: it must never read as "no shows".
    """
    ts = now()
    con.execute(
        """INSERT INTO sources (id, name, kind, seasonal) VALUES (?,?,?,?)
           ON CONFLICT(id) DO NOTHING""",
        (source.id, source.name, source.kind, int(source.seasonal)),
    )
    row = con.execute("SELECT * FROM sources WHERE id = ?", (source.id,)).fetchone()
    baseline = row["baseline_total"] or 0
    fails = row["consecutive_failures"] or 0

    if not result.ok:
        fails += 1
        health = "config_failed" if result.error_kind == "config" else (
            "down" if fails >= 3 else "degraded"
        )
        con.execute(
            """UPDATE sources SET last_attempt_at=?, consecutive_failures=?, health=?,
                                  last_error=?, last_error_kind=? WHERE id=?""",
            (ts, fails, health, result.error, result.error_kind, source.id),
        )
        return health

    baseline = max(baseline, result.total_seen)
    if result.total_seen == 0 and baseline >= 8:
        # It used to show us a full calendar and now shows nothing. Do not
        # interpret that as "no shows" — interpret it as "we've gone blind".
        health = "dormant" if source.seasonal else "suspicious"
    else:
        health = "ok"

    con.execute(
        """UPDATE sources SET last_attempt_at=?, last_success_at=?, last_total_seen=?,
                              baseline_total=?, consecutive_failures=0, health=?,
                              last_error=NULL, last_error_kind=NULL WHERE id=?""",
        (ts, ts, result.total_seen, baseline, health, source.id),
    )
    return health


def source_health(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM sources ORDER BY id")]


# ──────────────────────────────────────────────────────────────────────────────
# Users, subscriptions, shared state
# ──────────────────────────────────────────────────────────────────────────────

def add_user(con, user_id: str, name: str, is_curator: bool = False) -> str:
    token = secrets.token_urlsafe(32)
    con.execute(
        """INSERT INTO users (id, name, is_curator, token, created_at) VALUES (?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, is_curator=excluded.is_curator""",
        (user_id, name, int(is_curator), token, now()),
    )
    con.commit()
    return con.execute("SELECT token FROM users WHERE id=?", (user_id,)).fetchone()["token"]


def user_by_token(con, token: str) -> dict | None:
    row = con.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
    return dict(row) if row else None


def users(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM users ORDER BY id")]


def save_subscription(con, user_id: str, sub: dict, user_agent: str | None) -> None:
    keys = sub.get("keys", {})
    con.execute(
        """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, created_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(endpoint) DO UPDATE SET
             user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth,
             failures=0""",
        (user_id, sub["endpoint"], keys.get("p256dh"), keys.get("auth"), user_agent, now()),
    )
    con.commit()


def subscriptions_for(con, user_id: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,))]


def drop_subscription(con, endpoint: str) -> None:
    """410/404 from the push service means the browser threw the sub away."""
    con.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    con.commit()


def set_state(con, user_id: str, event_id: str, state: str) -> None:
    ts = now()
    ack = ts if state in DECIDED else None
    con.execute(
        """INSERT INTO user_events (user_id, event_id, state, acknowledged_at, updated_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(user_id, event_id) DO UPDATE SET
             state=excluded.state, acknowledged_at=excluded.acknowledged_at,
             updated_at=excluded.updated_at""",
        (user_id, event_id, state, ack, ts),
    )
    con.commit()


def states_for_event(con, event_id: str) -> dict[str, str]:
    return {r["user_id"]: r["state"] for r in con.execute(
        "SELECT user_id, state FROM user_events WHERE event_id = ?", (event_id,))}


def state_of(con, user_id: str, event_id: str) -> str:
    row = con.execute(
        "SELECT state FROM user_events WHERE user_id=? AND event_id=?",
        (user_id, event_id)).fetchone()
    return row["state"] if row else "unseen"


def nags_sent(con, user_id: str, event_id: str) -> list[int]:
    return [r["level"] for r in con.execute(
        "SELECT level FROM nags WHERE user_id=? AND event_id=? ORDER BY level",
        (user_id, event_id))]


def record_nag(con, user_id: str, event_id: str, level: int, ok: bool,
               channel: str = "push") -> None:
    con.execute(
        "INSERT INTO nags (user_id, event_id, level, sent_at, channel, ok) VALUES (?,?,?,?,?,?)",
        (user_id, event_id, level, now(), channel, int(ok)),
    )
    con.commit()


def get_setting(con, key: str) -> str | None:
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(con, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    con.commit()
