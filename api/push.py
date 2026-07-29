"""
Web push, with the feature that actually solves the observed failure.

Mike's real miss wasn't "the notification never arrived." It was a notification
that arrived correctly and got swiped away at a red light. So every tier-1 alert
carries ACTION BUTTONS — `GOT 'EM` and `CAN'T MAKE IT` — right on the lock
screen. Acknowledging is one tap and never requires opening the app.

The service worker can't read localStorage, so the acknowledgement token rides
along inside the push payload. That's safe: the payload is encrypted end-to-end
to a subscription that already belongs to exactly that user.

VAPID keys are generated once and stored in the settings table. Losing them
invalidates every existing subscription, so they live with the database, not in
the image.
"""

from __future__ import annotations

import base64
import json

from . import db

VAPID_PRIVATE = "vapid_private_pem"
VAPID_PUBLIC = "vapid_public_b64"
VAPID_SUBJECT = "mailto:mikelarsen1971@gmail.com"


def ensure_vapid(con) -> tuple[str, str]:
    """Return (private_pem, public_b64url), generating them on first use."""
    priv = db.get_setting(con, VAPID_PRIVATE)
    pub = db.get_setting(con, VAPID_PUBLIC)
    if priv and pub:
        return priv, pub

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    priv = v.private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    raw = v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    pub = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    db.set_setting(con, VAPID_PRIVATE, priv)
    db.set_setting(con, VAPID_PUBLIC, pub)
    return priv, pub


def build_payload(*, title: str, body: str, event_id: str, tier: int,
                  ticket_url: str | None, token: str, level: int = 0) -> dict:
    """
    The notification the phone actually renders.

    `requireInteraction` on tier 1 keeps it on screen instead of auto-dismissing
    — the entire point is that it should be annoying to ignore.
    """
    actions = []
    if tier in (1, 2):
        actions = [
            {"action": "got_tickets", "title": "GOT 'EM"},
            {"action": "cant_make_it", "title": "CAN'T MAKE IT"},
        ]
    return {
        "title": title,
        "body": body,
        "tag": f"show-{event_id}",       # collapse repeats of the same show
        "renotify": True,
        "requireInteraction": tier == 1,
        "actions": actions,
        "data": {
            "event_id": event_id,
            "ticket_url": ticket_url,
            "token": token,             # lets the SW acknowledge without the app
            "level": level,
            "tier": tier,
        },
    }


def send_to_user(con, user_id: str, payload: dict) -> tuple[int, int]:
    """Push to every device a user has registered. Returns (sent, failed)."""
    from pywebpush import WebPushException, webpush

    priv, _ = ensure_vapid(con)
    subs = db.subscriptions_for(con, user_id)
    sent = failed = 0

    for s in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": s["endpoint"],
                    "keys": {"p256dh": s["p256dh"], "auth": s["auth"]},
                },
                data=json.dumps(payload),
                vapid_private_key=priv,
                vapid_claims={"sub": VAPID_SUBJECT},
                timeout=15,
            )
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                # The browser threw this subscription away. Stop pushing to a
                # ghost — a dead subscription that looks alive is how one of
                # them silently stops getting alerts.
                db.drop_subscription(con, s["endpoint"])
            failed += 1
        except Exception:
            failed += 1

    return sent, failed
