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


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _pem_to_raw(pem: str) -> str:
    """
    Convert a PKCS8 PEM to the raw base64url private scalar.

    ⚠️ THE KEY FORMAT MATTERS AND THE FAILURE IS SILENT.

    `pywebpush(vapid_private_key=...)` hands a string to
    `py_vapid.Vapid01.from_string`, which does NOT understand PEM. It strips
    newlines, base64url-decodes the whole thing — header line included — and
    passes the resulting garbage to the DER parser. You get

        ValueError: Could not deserialize key data … ASN.1 parsing error

    on every single send, before anything touches the network. We shipped a PEM
    and every push failed while the app cheerfully reported `failed=2`.

    from_string only accepts two things: a 32-byte raw scalar (base64url), or
    DER. So we store raw.

    This CONVERTS the existing key rather than generating a new one —
    regenerating would invalidate every subscription already registered on a
    phone, which would mean re-enabling notifications on both devices.
    """
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(pem.encode(), password=None)
    return _b64(key.private_numbers().private_value.to_bytes(32, "big"))


def ensure_vapid(con) -> tuple[str, str]:
    """Return (private_key_b64url, public_key_b64url), generating on first use."""
    priv = db.get_setting(con, VAPID_PRIVATE)
    pub = db.get_setting(con, VAPID_PUBLIC)

    # Migrate a previously-stored PEM in place. Same key, so subscriptions live.
    if priv and "-----BEGIN" in priv:
        priv = _pem_to_raw(priv)
        db.set_setting(con, VAPID_PRIVATE, priv)

    if priv and pub:
        return priv, pub

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    priv = _b64(v.private_key.private_numbers().private_value.to_bytes(32, "big"))
    pub = _b64(v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))

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


def send_to_user(con, user_id: str, payload: dict) -> tuple[int, int, list[str]]:
    """
    Push to every device a user has registered. Returns (sent, failed, errors).

    ⚠️ ERRORS MUST BE RETURNED, NOT COUNTED.

    The first version caught everything and reported `failed=2`. That number is
    worthless: a dead subscription, an unreachable push service, and a private
    key the library can't even parse all look identical. We spent an hour
    suspecting the phone when the key had never once left the box.

    An app whose entire promise is "silence means nothing is happening" cannot
    have a delivery layer that fails quietly. The error text goes back to the
    caller, into the log, and onto the subscription row.
    """
    import logging

    from pywebpush import WebPushException, webpush

    log = logging.getLogger("kak.push")
    priv, _ = ensure_vapid(con)
    subs = db.subscriptions_for(con, user_id)
    sent = failed = 0
    errors: list[str] = []

    for s in subs:
        tail = s["endpoint"][-12:]
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
            db.mark_subscription_ok(con, s["endpoint"])
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            body = (getattr(e.response, "text", "") or "")[:200]
            msg = f"…{tail}: HTTP {status} {body or e}"
            if status in (404, 410):
                # The browser threw this subscription away. Stop pushing to a
                # ghost — a dead subscription that looks alive is how one of
                # them silently stops getting alerts.
                db.drop_subscription(con, s["endpoint"])
                msg += " (dropped — browser discarded it)"
            else:
                db.mark_subscription_failed(con, s["endpoint"], msg)
            errors.append(msg)
            failed += 1
            log.warning("push failed for %s: %s", user_id, msg)
        except Exception as e:
            msg = f"…{tail}: {type(e).__name__}: {e}"
            errors.append(msg)
            db.mark_subscription_failed(con, s["endpoint"], msg)
            failed += 1
            log.warning("push failed for %s: %s", user_id, msg)

    return sent, failed, errors
