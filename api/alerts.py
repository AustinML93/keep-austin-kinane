"""
Dispatch: turn a NagPlan into an actual notification on an actual phone.

Kept separate from nagger.py so the ladder logic stays pure and testable —
nagger decides *what is owed*, this decides *how it gets said and sent*.
"""

from __future__ import annotations

from datetime import datetime, timedelta

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


# How long to give a device to confirm before we assume the push was lost, and
# how far back to bother looking. A phone in a pocket in a tunnel is normal;
# fifteen minutes of silence is not.
DELIVERY_GRACE = timedelta(minutes=15)
RETRY_HORIZON = timedelta(hours=6)


def retry_undelivered(con, now: datetime, dry_run: bool = False) -> list[dict]:
    """
    Re-send tier 1 and 2 nags that were accepted by the push service but never
    confirmed by a device.

    We learned the hard way that a push can vanish — one disappeared entirely
    while a service worker was being swapped during a deploy, with 'sent=1'
    recorded and nothing on the phone. Without reconciliation that is a silent
    miss, which is the exact failure this app exists to prevent.

    ONE retry per level, marked channel='push-retry' so it can't loop. If the
    second attempt also goes unconfirmed, the escalation ladder is already the
    backstop — the next level will fire anyway.
    """
    out = []
    for ev in db.upcoming(con):
        if ev["tier"] == 3:
            continue
        for user in db.users(con):
            if db.state_of(con, user["id"], ev["id"]) in db.DECIDED:
                continue
            for level in db.nags_sent(con, user["id"], ev["id"]):
                attempts = db.nag_attempts(con, user["id"], ev["id"], level)
                first = attempts[0]
                sent_at = _parse_iso(first["sent_at"])
                if not sent_at:
                    continue
                age = now - sent_at
                if age < DELIVERY_GRACE or age > RETRY_HORIZON:
                    continue
                if any(x["channel"] == "push-retry" for x in attempts):
                    continue
                if db.was_delivered(con, user["id"], ev["id"], first["sent_at"]):
                    continue

                record = {"user": user["id"], "event": ev["id"], "level": level,
                          "tier": ev["tier"], "retry": True,
                          "age_min": int(age.total_seconds() // 60)}
                if dry_run:
                    out.append({**record, "sent": 0, "failed": 0, "dry_run": True})
                    continue

                title, body = _copy_for(con, ev, user, level)
                payload = push.build_payload(
                    title=title, body=body, event_id=ev["id"], tier=ev["tier"],
                    ticket_url=ev["ticket_url"], token=user["token"], level=level)
                sent, failed, errors = push.send_to_user(con, user["id"], payload)
                db.record_nag(con, user["id"], ev["id"], level, ok=sent > 0,
                              channel="push-retry")
                out.append({**record, "sent": sent, "failed": failed, "errors": errors})
    return out


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d.astimezone(LOCAL) if d.tzinfo else d.replace(tzinfo=LOCAL)


def _copy_for(con, ev, user, level: int) -> tuple[str, str]:
    """The same wording the original attempt used — seeded on the show."""
    states = db.states_for_event(con, ev["id"])
    others = [u for u in db.users(con) if u["id"] != user["id"]]
    other = others[0] if others else None
    return voice.nag(
        level, ev["tier"], venue=ev["venue"], when=_when(ev["starts_at"]),
        city=ev["city"] or "", other=other["name"] if other else None,
        other_has_tickets=bool(other and states.get(other["id"]) == "got_tickets"),
        austin_status=ev["austin_status"], day=max(level - 2, 1), seed=ev["id"])


def run_nags(con, now: datetime | None = None, dry_run: bool = False) -> list[dict]:
    now = now or datetime.now(LOCAL)
    # Reconcile what was accepted against what a device actually showed, before
    # deciding what's newly owed.
    out = retry_undelivered(con, now, dry_run=dry_run)

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
            # Seeded by the show, so the same alert never rewords itself between
            # two glances at the same notification.
            seed=p.event_id,
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
        sent, failed, errors = push.send_to_user(con, p.user_id, payload)

        # Record the nag even when delivery failed. The ladder must advance on
        # *attempts*, or a user with no registered device would re-trigger the
        # same level forever and never escalate.
        db.record_nag(con, p.user_id, p.event_id, p.level, ok=sent > 0)
        out.append({**record, "sent": sent, "failed": failed, "errors": errors})

    return out
