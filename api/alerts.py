"""
Dispatch: turn a NagPlan into an actual notification on an actual phone.

Kept separate from nagger.py so the ladder logic stays pure and testable —
nagger decides *what is owed*, this decides *how it gets said and sent*.
"""

from __future__ import annotations

from datetime import datetime

from . import db, push, voice
from .nagger import LOCAL, plan


def _when(starts_at: str) -> str:
    try:
        d = datetime.fromisoformat(starts_at)
    except ValueError:
        return starts_at
    if len(starts_at) > 10:
        return d.strftime("%a %b %-d, %-I:%M%p").replace("AM", "am").replace("PM", "pm")
    return d.strftime("%a %b %-d")


def run_nags(con, now: datetime | None = None, dry_run: bool = False) -> list[dict]:
    now = now or datetime.now(LOCAL)
    out = []

    for p in plan(con, now):
        ev = con.execute("SELECT * FROM events WHERE id=?", (p.event_id,)).fetchone()
        if not ev:
            continue
        me = con.execute("SELECT * FROM users WHERE id=?", (p.user_id,)).fetchone()

        # Cross-user awareness — the best jokes in the app come from here.
        states = db.states_for_event(con, p.event_id)
        others = [u for u in db.users(con) if u["id"] != p.user_id]
        other = others[0] if others else None
        other_has = bool(other and states.get(other["id"]) == "got_tickets")

        title, body = voice.nag(
            p.level, p.tier,
            venue=ev["venue"], when=_when(ev["starts_at"]), city=ev["city"] or "",
            other=other["name"] if other else None, other_has_tickets=other_has,
            austin_status=ev["austin_status"], day=max(p.level - 2, 1),
        )

        record = {
            "user": p.user_id, "event": p.event_id, "level": p.level,
            "tier": p.tier, "title": title, "body": body,
        }

        if dry_run:
            out.append({**record, "sent": 0, "failed": 0, "dry_run": True})
            continue

        payload = push.build_payload(
            title=title, body=body, event_id=p.event_id, tier=p.tier,
            ticket_url=ev["ticket_url"], token=me["token"], level=p.level,
        )
        sent, failed = push.send_to_user(con, p.user_id, payload)

        # Record the nag even when delivery failed. The ladder must advance on
        # *attempts*, or a user with no registered device would re-trigger the
        # same level forever and never escalate.
        db.record_nag(con, p.user_id, p.event_id, p.level, ok=sent > 0)
        out.append({**record, "sent": sent, "failed": failed})

    return out
