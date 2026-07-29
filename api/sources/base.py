"""
Source protocol, the normalized Event shape, and a polite fetcher.

Every source implements the same tiny contract and returns a FetchResult that
keeps two things strictly separate:

    events      — Kinane shows we found                    (PRESENCE)
    total_seen  — how many events the source listed at all  (PARSEABILITY)

That split is the whole reliability story. A source returning zero Kinane shows
out of a 277-event calendar is *healthy and correct*. A source returning zero
events out of zero is *broken and lying*. Collapsing those two into one number
is how an app like this quietly stops working while looking fine.

stdlib only — no deps — so parsers and their tests run on bare python3.
"""

from __future__ import annotations

import gzip
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

UA = "KeepAustinKinane/0.1 (personal show-tracker; 2 users; hourly at most)"
TIMEOUT = 25

# Match on the surname alone — catches "KINANE", "Kyle Kinane", and festival
# lineup listings that render him as part of a block of names.
NEEDLE = "kinane"


# ──────────────────────────────────────────────────────────────────────────────
# Normalized event
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Event:
    source_id: str
    external_id: str
    starts_at: str                      # ISO local: "2026-09-11" or "2026-09-11T21:30"
    title: str | None = None
    venue: str | None = None
    city: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    onsale_at: str | None = None
    ticket_url: str | None = None
    presale_active: bool = False
    # 1.0 = the source is literally his tour feed. Lower = a name match on a
    # lineup we're less sure about. Never used to suppress — only to label.
    artist_confidence: float = 1.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def date(self) -> str:
        return self.starts_at[:10]

    @property
    def time(self) -> str:
        return self.starts_at[11:16] if len(self.starts_at) >= 16 else ""

    def dedupe_key(self) -> str:
        """
        Identity across sources: date + venue + showtime.

        Showtime is in the key deliberately — clubs run two shows a night and the
        official feed lists them as separate entries. Dropping time would merge
        the 7:30 and the 10:00 into one show and hide half the ticket links.

        KNOWN LIMITATION: two sources reporting the same show with slightly
        different times (7:30 vs 7:45, or a UTC/local mixup) will produce two
        events instead of one. That is a false alarm, which we have explicitly
        chosen over a miss. Revisit with a ±90min fuzzy merge if it gets noisy.
        """
        return f"{self.date}|{norm(self.venue) or norm(self.city)}|{self.time}"


@dataclass
class FetchResult:
    source_id: str
    ok: bool
    events: list[Event] = field(default_factory=list)
    total_seen: int = 0
    error: str | None = None
    # network | blocked | config | parse — 'config' is called out separately
    # because a failed credential scrape must never look like "no shows".
    error_kind: str | None = None
    note: str | None = None


class Source(Protocol):
    id: str
    name: str
    kind: str          # "api" | "scrape"
    seasonal: bool     # True = allowed to be empty for months (Moontower)

    def fetch(self) -> FetchResult: ...


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def norm(s: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace. For dedupe keys."""
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def mentions_kinane(*parts: str | None) -> bool:
    return any(NEEDLE in (p or "").lower() for p in parts)


def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int | None, str, str | None]:
    """Return (status, body, error). Never raises."""
    h = {
        "User-Agent": UA,
        "Accept": "application/json,text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
    }
    h.update(headers or {})
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            return r.status, raw.decode(r.headers.get_content_charset() or "utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        return e.code, "", f"HTTP {e.code}"
    except Exception as e:
        return None, "", f"{type(e).__name__}: {e}"


def classify_http_error(status: int | None, err: str | None) -> str:
    if status in (403, 429, 503):
        return "blocked"
    return "network"


LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


def ldjson_events(html: str) -> list[dict]:
    """Pull every schema.org Event object out of a page's JSON-LD blocks."""
    out: list[dict] = []
    for blob in LDJSON_RE.findall(html):
        try:
            data = json.loads(blob.strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                t = node.get("@type", "")
                types = t if isinstance(t, list) else [t]
                if any("Event" in str(x) for x in types):
                    out.append(node)
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return out
