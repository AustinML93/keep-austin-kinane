"""
FastAPI app + the background poller, in ONE process.

Deliberately not two services. The API and the watcher share a database and a
lifetime, so splitting them would buy nothing and cost a second SQLite writer.
One process means exactly one writer, which makes WAL-mode SQLite safe with no
ceremony at all.

The poll thread catches everything. A scraper blowing up must never take the
API down with it.
"""

from __future__ import annotations

import os
import threading
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import db
from .poll import poll_once

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "3600"))  # hourly; polite

app = FastAPI(title="Keep Austin Kinane", docs_url=None, redoc_url=None)
_last_poll: dict = {"at": None, "new": 0, "error": None}


def _poll_loop() -> None:
    while True:
        try:
            report = poll_once()
            _last_poll.update(at=db.now(), new=len(report.new_events), error=None)
        except Exception as e:  # never let the loop die
            _last_poll.update(at=db.now(), error=f"{type(e).__name__}: {e}")
        time.sleep(POLL_INTERVAL)


@app.on_event("startup")
def start_poller() -> None:
    db.connect().close()  # ensure schema exists before serving
    threading.Thread(target=_poll_loop, daemon=True, name="poller").start()


@app.get("/api/shows")
def shows():
    con = db.connect()
    try:
        rows = db.upcoming(con)
    finally:
        con.close()
    return {
        "shows": rows,
        "counts": {t: sum(1 for r in rows if r["tier"] == t) for t in (1, 2, 3)},
    }


@app.get("/api/health")
def health():
    """
    What each source can currently see. This is a user-facing feature, not ops
    hygiene — the app's promise is that silence means something, and this is
    the evidence for it.
    """
    con = db.connect()
    try:
        sources = db.source_health(con)
    finally:
        con.close()
    blind = [s for s in sources if s["health"] in ("suspicious", "down", "config_failed")]
    return {
        "sources": sources,
        "all_eyes_open": not blind,
        "blind": [s["id"] for s in blind],
        "last_poll": _last_poll,
    }


@app.post("/api/poll")
def force_poll():
    try:
        report = poll_once()
        return {"new_events": report.new_events, "per_source": report.per_source}
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
