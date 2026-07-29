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

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import db, push, voice
from .alerts import run_nags
from .poll import poll_once

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "3600"))   # hourly; polite
NAG_INTERVAL = int(os.environ.get("NAG_INTERVAL", "900"))      # 15min; the ladder is time-based

app = FastAPI(title="Keep Austin Kinane", docs_url=None, redoc_url=None)
_last_poll: dict = {"at": None, "new": 0, "error": None}
_last_nag: dict = {"at": None, "sent": 0, "error": None}

VALID_STATES = ("seen", "got_tickets", "cant_make_it", "passing")


def _loop(fn, interval, state):
    while True:
        try:
            fn()
        except Exception as e:
            state.update(at=db.now(), error=f"{type(e).__name__}: {e}")
        time.sleep(interval)


def _do_poll():
    report = poll_once()
    _last_poll.update(at=db.now(), new=len(report.new_events), error=None)


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
# Health — a user-facing feature, not ops hygiene
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    con = db.connect()
    try:
        sources = db.source_health(con)
        subs = {u["id"]: len(db.subscriptions_for(con, u["id"])) for u in db.users(con)}
    finally:
        con.close()

    blind = [s for s in sources if s["health"] in ("suspicious", "down", "config_failed")]
    config_lost = any(s["health"] == "config_failed" for s in sources)
    return {
        "sources": sources,
        "all_eyes_open": not blind,
        "blind": [s["id"] for s in blind],
        "status_line": voice.status_line(
            len(sources), [s["name"] for s in blind], config_lost),
        "subscriptions": subs,
        "last_poll": _last_poll,
        "last_nag": _last_nag,
    }


@app.post("/api/poll")
def force_poll():
    try:
        report = poll_once()
        return {"new_events": report.new_events, "per_source": report.per_source}
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.post("/api/nags/run")
def force_nags(dry_run: bool = False):
    con = db.connect()
    try:
        return {"nags": run_nags(con, dry_run=dry_run)}
    finally:
        con.close()
