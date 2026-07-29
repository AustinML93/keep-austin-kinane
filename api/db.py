"""
SQLite storage. WAL mode, single writer.

The whole app runs in one process — FastAPI plus a background scheduler thread —
so there is exactly one writer and SQLite needs no ceremony to be safe.

User / bit tables land with their features; this is the show-tracking core.
"""

from __future__ import annotations

import json
import os
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

CREATE INDEX IF NOT EXISTS idx_events_start ON events(starts_at);
CREATE INDEX IF NOT EXISTS idx_events_tier  ON events(tier);
"""


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
