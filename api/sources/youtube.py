"""
YouTube: playlist sync and single-URL lookup.

Two capture paths, because storage location and capture path are different
decisions and each one wants a different answer:

  PLAYLIST  the fast path. Mike saves a clip to the "Bits" playlist in the
            moment he finds it, on whatever device he's holding. No context
            switch, which is the only reason a curation habit survives.

  oEMBED    the flexible path. Paste any URL into the app. Works with no API
            key at all, and returns title, channel and thumbnail — so adding
            something is one field, not a metadata chore.

The playlist is an INBOX, not a curated pool. It's allowed to be messy; the
ratings in the app are the filter. That's what lets "Gout Flare-Up" block a clip
without anyone having to go tidy up YouTube.

API key is optional. Without it we scrape the playlist page, which works today
but is exactly the fragile-scraper class we already have health machinery for.
With it we also learn `embeddable` BEFORE trying to play something, and get real
player dimensions instead of guessing at aspect ratio from duration.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse

from .base import http_get

API = "https://www.googleapis.com/youtube/v3"
OEMBED = "https://www.youtube.com/oembed"

ID_RE = re.compile(r"(?:v=|/shorts/|youtu\.be/|/embed/)([\w-]{11})")
DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def api_key() -> str:
    return os.environ.get("YOUTUBE_API_KEY", "")


def video_id(url: str) -> str | None:
    m = ID_RE.search(url or "")
    return m.group(1) if m else None


def parse_duration(iso: str | None) -> int:
    """PT1H2M3S -> seconds."""
    if not iso:
        return 0
    m = DURATION_RE.fullmatch(iso.strip())
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def classify(duration_s: int) -> str:
    """
    short   <= 60s   Shorts. Vertical, punchy — the daily-bit bread and butter.
    clip    <= 25min a set on a talk show, a podcast segment.
    special >  25min a full set. Shelf only; never served as a bit of the day,
                     because nobody wants an hour of video as their daily hit.
    """
    if duration_s and duration_s <= 60:
        return "short"
    if duration_s and duration_s > 25 * 60:
        return "special"
    return "clip"


# ──────────────────────────────────────────────────────────────────────────────
# Single URL (no key required)
# ──────────────────────────────────────────────────────────────────────────────

def lookup(url: str) -> dict | None:
    """
    Metadata for one URL via oEmbed. No API key.

    A non-200 here is meaningful: YouTube returns 401/404 for videos that are
    private, deleted, or have embedding disabled. So this doubles as the
    can-we-actually-play-it check.
    """
    q = urllib.parse.urlencode({"format": "json", "url": url})
    status, body, _ = http_get(f"{OEMBED}?{q}")
    if status != 200 or not body:
        return None
    try:
        d = json.loads(body)
    except Exception:
        return None

    w, h = d.get("width") or 0, d.get("height") or 0
    return {
        "video_id": video_id(url),
        "url": url,
        "title": d.get("title"),
        "channel": d.get("author_name"),
        "thumbnail": d.get("thumbnail_url"),
        "vertical": bool(w and h and h > w),
        "embeddable": True,       # a 200 from oEmbed means it embeds
        "provider": "youtube" if video_id(url) else "other",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Playlist
# ──────────────────────────────────────────────────────────────────────────────

def playlist_ids_scraped(playlist_id: str) -> list[str]:
    """Video IDs straight out of the playlist page. No key, order preserved."""
    status, body, _ = http_get(
        f"https://www.youtube.com/playlist?list={urllib.parse.quote(playlist_id)}")
    if status != 200 or not body:
        return []
    seen, order = set(), []
    for vid in re.findall(r'"videoId":"([\w-]{11})"', body):
        if vid not in seen:
            seen.add(vid)
            order.append(vid)
    return order


def playlist_ids_api(playlist_id: str, key: str) -> list[str]:
    ids, page = [], ""
    while True:
        q = urllib.parse.urlencode({
            "part": "contentDetails", "playlistId": playlist_id,
            "maxResults": 50, "key": key, **({"pageToken": page} if page else {}),
        })
        status, body, _ = http_get(f"{API}/playlistItems?{q}")
        if status != 200:
            return ids
        d = json.loads(body)
        ids += [i["contentDetails"]["videoId"] for i in d.get("items", [])
                if i.get("contentDetails", {}).get("videoId")]
        page = d.get("nextPageToken", "")
        if not page:
            break
    return ids


def videos_api(ids: list[str], key: str) -> dict[str, dict]:
    """Full metadata for up to 50 ids per call."""
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        q = urllib.parse.urlencode({
            "part": "snippet,contentDetails,status,player",
            "id": ",".join(chunk), "key": key, "maxWidth": 480,
        })
        status, body, _ = http_get(f"{API}/videos?{q}")
        if status != 200:
            continue
        for v in json.loads(body).get("items", []):
            out[v["id"]] = _from_api(v)
    return out


def _from_api(v: dict) -> dict:
    snip = v.get("snippet") or {}
    dur = parse_duration((v.get("contentDetails") or {}).get("duration"))
    thumbs = snip.get("thumbnails") or {}
    thumb = (thumbs.get("maxres") or thumbs.get("high") or thumbs.get("medium")
             or thumbs.get("default") or {})

    # Real player dimensions beat guessing aspect ratio from duration — a
    # vertical Short and a 16:9 clip need different containers, and some
    # sub-60s clips are landscape.
    embed = (v.get("player") or {}).get("embedHtml") or ""
    ew = re.search(r'width="(\d+)"', embed)
    eh = re.search(r'height="(\d+)"', embed)
    vertical = bool(ew and eh and int(eh.group(1)) > int(ew.group(1)))

    return {
        "video_id": v["id"],
        "url": f"https://www.youtube.com/watch?v={v['id']}",
        "title": snip.get("title"),
        "channel": snip.get("channelTitle"),
        "thumbnail": thumb.get("url"),
        "duration_s": dur,
        "kind": classify(dur),
        "vertical": vertical,
        # Knowing this BEFORE we try to play something is the main reason the
        # API key is worth having.
        "embeddable": bool((v.get("status") or {}).get("embeddable", True)),
        "provider": "youtube",
    }


def fetch_playlist(playlist_id: str) -> tuple[list[dict], str]:
    """
    Everything in a playlist. Returns (items, how) where `how` is 'api' or
    'scrape' so the caller can say which path it used.
    """
    key = api_key()
    if key:
        ids = playlist_ids_api(playlist_id, key)
        meta = videos_api(ids, key)
        return [meta[i] for i in ids if i in meta], "api"

    # Keyless fallback: ids from the page, metadata one oEmbed call at a time.
    items = []
    for vid in playlist_ids_scraped(playlist_id):
        got = lookup(f"https://www.youtube.com/watch?v={vid}")
        if got:
            got.setdefault("duration_s", 0)
            got.setdefault("kind", "clip")   # no duration without the API
            items.append(got)
    return items, "scrape"
