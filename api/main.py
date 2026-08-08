"""
FastAPI app + the background poller + the nag tick, all in ONE process.

Deliberately not separate services. The API, the watcher, and the nagger share a
database and a lifetime, so splitting them would buy nothing and cost extra
SQLite writers. One process means one writer, which makes WAL-mode SQLite safe
with no ceremony.

Both loops catch everything. A scraper blowing up must never take the API down,
and a bad nag must never stop the polling.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import bits as bits_mod
from . import db, push, venues, voice
from .alerts import run_nags
from .poll import poll_once

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "3600"))   # hourly; polite
NAG_INTERVAL = int(os.environ.get("NAG_INTERVAL", "900"))      # 15min; the ladder is time-based
BITS_INTERVAL = int(os.environ.get("BITS_INTERVAL", "21600"))  # 6h; nobody's in a hurry for a laugh
BACKUP_INTERVAL = int(os.environ.get("BACKUP_INTERVAL", "86400"))  # daily

app = FastAPI(title="Keep Austin Kinane", docs_url=None, redoc_url=None)
_last_poll: dict = {"at": None, "new": 0, "error": None}
_last_nag: dict = {"at": None, "sent": 0, "error": None}
_last_backup: dict = {"at": None, "path": None, "error": None}

# 'unseen' is accepted so the notification's UNDO can put someone back on the
# hook after a mis-tap. It is NOT in db.DECIDED, so the ladder resumes.
VALID_STATES = ("unseen", "seen", "got_tickets", "cant_make_it", "passing")
BIT_PLAYLIST = os.environ.get("KAK_BITS_PLAYLIST", "")


def _loop(fn, interval, state):
    while True:
        try:
            fn()
        except Exception as e:
            state.update(at=db.now(), error=f"{type(e).__name__}: {e}")
        time.sleep(interval)


# A dead-man switch (e.g. a healthchecks.io ping URL). The machine promising
# "silence means nothing is happening" cannot be the only machine checking
# whether it's alive — if these pings stop, the external service raises the
# alarm through its own channel, which this box can't take down with it.
WATCHDOG_URL = os.environ.get("WATCHDOG_URL", "")


def _do_poll():
    report = poll_once()
    _last_poll.update(at=db.now(), new=len(report.new_events), error=None)
    if WATCHDOG_URL:
        # Only after a SUCCESSFUL cycle — a watchdog pinged from a broken loop
        # is a watchdog that lies. http_get swallows its own errors; a missed
        # ping must never take down polling.
        from .sources.base import http_get
        http_get(WATCHDOG_URL)


def _do_backup():
    """
    Daily snapshot. Losing this file means re-onboarding both users and losing
    every rating — see db.backup for why it's not just a convenience.
    """
    con = db.connect()
    try:
        dest = db.backup(con)
        _last_backup.update(at=db.now(), path=dest.name, error=None)
    finally:
        con.close()


def _do_bits_sync():
    """
    Pull the playlist in on a slow loop. Curation happens on YouTube, in the
    moment; this just notices. A failure here must never be loud — a missed bit
    is a missed laugh, not a missed show.
    """
    if not BIT_PLAYLIST:
        return
    con = db.connect()
    try:
        bits_mod.sync_playlist(con, BIT_PLAYLIST)
    finally:
        con.close()


def _do_nag():
    con = db.connect()
    try:
        results = run_nags(con)
    finally:
        con.close()
    _last_nag.update(at=db.now(), sent=len(results), error=None)


@app.on_event("startup")
def start_workers() -> None:
    db.connect().close()  # ensure schema exists before serving
    threading.Thread(target=_loop, args=(_do_poll, POLL_INTERVAL, _last_poll),
                     daemon=True, name="poller").start()
    threading.Thread(target=_loop, args=(_do_nag, NAG_INTERVAL, _last_nag),
                     daemon=True, name="nagger").start()
    threading.Thread(target=_loop, args=(_do_backup, BACKUP_INTERVAL, _last_backup),
                     daemon=True, name="backup").start()
    if BIT_PLAYLIST:
        threading.Thread(target=_loop, args=(_do_bits_sync, BITS_INTERVAL, {}),
                         daemon=True, name="bits").start()


# ──────────────────────────────────────────────────────────────────────────────
# Auth — a bearer token from a magic link. No passwords, appropriate to stakes.
# ──────────────────────────────────────────────────────────────────────────────

def _user_from(con, token: str | None):
    if not token:
        raise HTTPException(401, "no token")
    user = db.user_by_token(con, token.replace("Bearer ", "").strip())
    if not user:
        raise HTTPException(401, "unknown token")
    return user


# ──────────────────────────────────────────────────────────────────────────────
# Shows
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/shows")
def shows(authorization: str | None = Header(None)):
    con = db.connect()
    try:
        rows = db.upcoming(con)
        people = db.users(con)
        # Shared state is the point — every show carries BOTH users' status.
        for r in rows:
            states = db.states_for_event(con, r["id"])
            r["states"] = {u["id"]: {"name": u["name"], "state": states.get(u["id"], "unseen")}
                           for u in people}
        me = None
        if authorization:
            try:
                me = _user_from(con, authorization)["id"]
            except HTTPException:
                me = None
        return {"shows": rows, "me": me,
                "counts": {t: sum(1 for r in rows if r["tier"] == t) for t in (1, 2, 3)}}
    finally:
        con.close()


@app.post("/api/events/{event_id}/state")
def set_state(event_id: str, payload: dict = Body(...),
              authorization: str | None = Header(None)):
    """
    Record a decision.

    Accepts the token in the BODY as well as the header, because the service
    worker calls this from a notification action button and can't read
    localStorage.
    """
    state = payload.get("state")
    if state not in VALID_STATES:
        raise HTTPException(400, f"state must be one of {VALID_STATES}")

    con = db.connect()
    try:
        user = _user_from(con, payload.get("token") or authorization)
        db.set_state(con, user["id"], event_id, state)
        return {"ok": True, "user": user["id"], "event": event_id, "state": state}
    finally:
        con.close()


@app.get("/api/copy")
def ui_copy():
    """
    Every string the interface shows.

    Copy used to be duplicated between voice.py and hardcoded English in
    app.js — which guarantees the two drift apart. The server owns it; the front
    end keeps only terse fallbacks for when it can't reach us.
    """
    return {"copy": voice.ui_copy()}


@app.get("/api/venues")
def venue_suggestions():
    """Autocomplete for the add form."""
    return {"venues": venues.suggestions()}


@app.post("/api/events/manual")
def add_manual(payload: dict = Body(...), authorization: str | None = Header(None)):
    """
    Type in a show a human told you about.

    Both users can add — the whole point is that discovery is social and either
    of them might be the one who hears about it first.
    """
    con = db.connect()
    try:
        user = _user_from(con, payload.get("token") or authorization)
        venue = (payload.get("venue") or "").strip()
        date = (payload.get("date") or "").strip()
        if not venue or not date:
            raise HTTPException(400, "venue and date are required")

        event_id, is_new = db.add_manual_event(
            con, added_by=user["id"], venue=venue,
            city=(payload.get("city") or "Austin, TX").strip(),
            date=date, time_s=(payload.get("time") or "").strip()[:5],
            ticket_url=(payload.get("ticket_url") or "").strip() or None,
            note=(payload.get("note") or "").strip() or None,
        )
        row = con.execute("SELECT tier, distance_mi FROM events WHERE id=?", (event_id,)).fetchone()
        return {"ok": True, "event_id": event_id, "is_new": is_new,
                "tier": row["tier"], "distance_mi": row["distance_mi"]}
    finally:
        con.close()


@app.delete("/api/events/{event_id}")
def remove_manual(event_id: str, authorization: str | None = Header(None),
                  token: str | None = None):
    con = db.connect()
    try:
        _user_from(con, token or authorization)
        if not db.delete_manual_event(con, event_id):
            raise HTTPException(
                400, "only hand-typed shows can be deleted, and only while no "
                     "real source has confirmed them")
        return {"ok": True}
    finally:
        con.close()


# ──────────────────────────────────────────────────────────────────────────────
# Push
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/vapid-public-key")
def vapid_key():
    con = db.connect()
    try:
        return {"key": push.ensure_vapid(con)[1]}
    finally:
        con.close()


@app.post("/api/push/subscribe")
def subscribe(payload: dict = Body(...), request: Request = None,
              authorization: str | None = Header(None)):
    con = db.connect()
    try:
        user = _user_from(con, payload.get("token") or authorization)
        sub = payload.get("subscription") or {}
        if not sub.get("endpoint"):
            raise HTTPException(400, "missing subscription.endpoint")
        db.save_subscription(con, user["id"], sub,
                             request.headers.get("user-agent") if request else None)
        return {"ok": True, "user": user["id"]}
    finally:
        con.close()


# ──────────────────────────────────────────────────────────────────────────────
# Bits
# ──────────────────────────────────────────────────────────────────────────────

VALID_RATINGS = ("struts", "fine", "gout")


def _bit_view(con, b: dict, me: str | None) -> dict:
    b = dict(b)
    b["display_title"] = b.get("custom_title") or b.get("title")
    b["ratings"] = bits_mod.ratings_for(con, b["id"])
    b["my_rating"] = b["ratings"].get(me) if me else None
    b["embed_url"] = (f"https://www.youtube-nocookie.com/embed/{b['video_id']}"
                      if b.get("video_id") else None)
    return b


@app.get("/api/bits/today")
def bit_of_the_day(authorization: str | None = Header(None)):
    con = db.connect()
    try:
        me = None
        if authorization:
            try:
                me = _user_from(con, authorization)["id"]
            except HTTPException:
                me = None
        b = bits_mod.pick_for(con)
        if not b:
            return {"bit": None}
        bits_mod.record_served(con, b["id"])
        return {"bit": _bit_view(con, b, me)}
    finally:
        con.close()


@app.get("/api/bits/specials")
def bit_specials(authorization: str | None = Header(None)):
    """The shelf. Never served as a daily bit — this is the hour-to-spend list."""
    con = db.connect()
    try:
        me = None
        if authorization:
            try:
                me = _user_from(con, authorization)["id"]
            except HTTPException:
                me = None
        return {"specials": [_bit_view(con, b, me) for b in bits_mod.specials(con)]}
    finally:
        con.close()


@app.get("/api/bits/holds-up")
def bits_hold_up(authorization: str | None = Header(None)):
    """The keepers. See bits.holds_up for why this isn't a separate Top 10."""
    con = db.connect()
    try:
        me = None
        if authorization:
            try:
                me = _user_from(con, authorization)["id"]
            except HTTPException:
                me = None
        return {"holds_up": [_bit_view(con, b, me) for b in bits_mod.holds_up(con)]}
    finally:
        con.close()


@app.post("/api/bits/{bit_id}/rate")
def rate_bit(bit_id: str, payload: dict = Body(...),
             authorization: str | None = Header(None)):
    """Both users rate. Only the curator's rating changes the pool."""
    rating = payload.get("rating")
    if rating not in VALID_RATINGS:
        raise HTTPException(400, f"rating must be one of {VALID_RATINGS}")
    con = db.connect()
    try:
        user = _user_from(con, payload.get("token") or authorization)
        bits_mod.rate(con, bit_id, user["id"], rating,
                      is_curator=bool(user["is_curator"]))
        return {"ok": True, "rating": rating, "counted": bool(user["is_curator"])}
    finally:
        con.close()


@app.post("/api/bits")
def add_bit(payload: dict = Body(...), authorization: str | None = Header(None)):
    """The flexible path — paste any URL. Curator only."""
    con = db.connect()
    try:
        user = _user_from(con, payload.get("token") or authorization)
        if not user["is_curator"]:
            raise HTTPException(403, "curation is Mike's job")
        url = (payload.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "url required")
        kind = payload.get("kind")
        if kind and kind not in ("short", "clip", "special"):
            raise HTTPException(400, "kind must be short, clip or special")
        item, is_new = bits_mod.add_url(con, url, added_by=user["id"], kind=kind)
        if not item:
            raise HTTPException(
                400, "couldn't read that one — it may be private, deleted, or have "
                     "embedding turned off")
        if payload.get("title"):
            bits_mod.rename(con, item["video_id"] or url, payload["title"])
        return {"ok": True, "is_new": is_new, "title": item.get("title")}
    finally:
        con.close()


@app.patch("/api/bits/{bit_id}")
def rename_bit(bit_id: str, payload: dict = Body(...),
               authorization: str | None = Header(None)):
    """
    Give a bit the name you actually use for it.

    Worth having even though YouTube supplies a title — the way Mike and Rob
    refer to a bit is the closest thing to real voice research this project gets.
    """
    con = db.connect()
    try:
        user = _user_from(con, payload.get("token") or authorization)
        if not user["is_curator"]:
            raise HTTPException(403, "curation is Mike's job")
        bits_mod.rename(con, bit_id, (payload.get("title") or "").strip() or None)
        return {"ok": True}
    finally:
        con.close()


@app.post("/api/bits/sync")
def sync_bits(payload: dict = Body(default={}),
              authorization: str | None = Header(None)):
    con = db.connect()
    try:
        user = _user_from(con, payload.get("token") or authorization)
        if not user["is_curator"]:
            raise HTTPException(403, "curation is Mike's job")
        pl = (payload.get("playlist") or BIT_PLAYLIST).strip()
        if not pl:
            raise HTTPException(400, "no playlist configured (KAK_BITS_PLAYLIST)")
        return bits_mod.sync_playlist(con, pl, added_by=user["id"])
    finally:
        con.close()


@app.post("/api/push/receipt")
def push_receipt(payload: dict = Body(...)):
    """
    The device reporting what actually happened to a push.

    Deliberately unauthenticated-tolerant: the service worker has a token in the
    payload, but a receipt that fails because auth was fiddly is a receipt we
    don't get, and the whole point is to stop guessing.
    """
    con = db.connect()
    try:
        user = None
        tok = payload.get("token")
        if tok:
            u = db.user_by_token(con, tok)
            user = u["id"] if u else None
        db.record_receipt(con, user, payload.get("event_id"),
                          (payload.get("stage") or "unknown")[:40],
                          payload.get("detail"))
        return {"ok": True}
    finally:
        con.close()


# ──────────────────────────────────────────────────────────────────────────────
# Health — a user-facing feature, not ops hygiene
# ──────────────────────────────────────────────────────────────────────────────

# How old the newest poll attempt can be before "all eyes open" becomes a
# claim we refuse to make. Two missed cycles plus slack: one late cycle is a
# slow scraper, two is a stopped poller.
STALE_AFTER_S = POLL_INTERVAL * 2 + 900


@app.get("/api/health")
def health():
    con = db.connect()
    try:
        sources = db.source_health(con)
        subs = {u["id"]: len(db.subscriptions_for(con, u["id"])) for u in db.users(con)}
    finally:
        con.close()

    # 'manual' can't go blind — it's a human, not a scraper. Excluding it keeps
    # the status readout about things that can actually fail.
    watchers = [s for s in sources if s["kind"] != "manual"]
    blind = [s for s in watchers if s["health"] in ("suspicious", "down", "config_failed")]
    config_lost = any(s["health"] == "config_failed" for s in watchers)

    # "All eyes open" is a claim about NOW, so it has to carry a timestamp and
    # fail closed when that timestamp is old. A poller that stopped days ago
    # would otherwise keep reporting well-informed nothing forever — read from
    # the sources table, not _last_poll, so a restart doesn't reset the clock.
    checked_ago_s = None
    stale = False
    attempts = [s["last_attempt_at"] for s in watchers if s["last_attempt_at"]]
    if attempts:
        try:
            newest = datetime.fromisoformat(max(attempts))
            checked_ago_s = int((datetime.now(newest.tzinfo) - newest).total_seconds())
            stale = checked_ago_s > STALE_AFTER_S
        except ValueError:
            stale = True
    blind_names = (["the app's own watcher"] if stale else []) + [s["name"] for s in blind]
    return {
        "sources": sources,
        "all_eyes_open": not blind and not stale,
        "stale": stale,
        "checked_ago_s": checked_ago_s,
        "blind": [s["id"] for s in blind],
        "status_line": voice.status_line(
            len(watchers), blind_names, config_lost,
            seed=datetime.now().date().isoformat()),
        "subscriptions": subs,
        "last_poll": _last_poll,
        "last_nag": _last_nag,
        "last_backup": _last_backup,
        "recent_push_receipts": db_receipts(),
    }


def db_receipts():
    con = db.connect()
    try:
        return db.recent_receipts(con, 8)
    finally:
        con.close()


# The force endpoints and the backup download require a magic-link token like
# everything else that acts. They used to be open, which meant anyone on the
# internet could make this box hammer its sources — sources this project is
# deliberately polite to, one of which already 429s strangers on principle.

@app.post("/api/poll")
def force_poll(payload: dict = Body(default={}),
               authorization: str | None = Header(None)):
    con = db.connect()
    try:
        _user_from(con, payload.get("token") or authorization)
    finally:
        con.close()
    try:
        report = poll_once()
        return {"new_events": report.new_events, "per_source": report.per_source}
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.post("/api/nags/run")
def force_nags(dry_run: bool = False, payload: dict = Body(default={}),
               authorization: str | None = Header(None)):
    con = db.connect()
    try:
        _user_from(con, payload.get("token") or authorization)
        return {"nags": run_nags(con, dry_run=dry_run)}
    finally:
        con.close()


@app.get("/api/backup/latest")
def backup_latest(authorization: str | None = Header(None),
                  token: str | None = None):
    """
    Stream the newest snapshot, so backups can live somewhere other than the
    disk they protect. Seven siblings beside the live database cover a bad
    write; they do nothing for the drive dying. scripts/pull_backup.sh on any
    other machine turns this into an off-box copy.
    """
    con = db.connect()
    try:
        _user_from(con, token or authorization)
    finally:
        con.close()
    snapshots = sorted((db.DB_PATH.parent / "backups").glob("kak-*.sqlite3"))
    if not snapshots:
        raise HTTPException(404, "no snapshots yet — the daily backup thread hasn't run")
    return FileResponse(snapshots[-1], filename=snapshots[-1].name,
                        media_type="application/octet-stream")
