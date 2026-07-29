"""
Command line for the show tracker — the thing you run to see if it works.

    python3 -m api.cli poll        fetch every source, store, tier, report
    python3 -m api.cli shows       what's upcoming, by tier
    python3 -m api.cli health      what each source can currently see

    python3 -m api.cli adduser mike Mike --curator     issue a magic link
    python3 -m api.cli vapid                           show the push public key
    python3 -m api.cli nags [--dry-run]                fire what's owed
    python3 -m api.cli simulate                        fake a tier-1 Austin show
    python3 -m api.cli unsimulate                      clear it

    python3 -m api.cli add mike "Comedy Mothership" 2026-10-12 20:00
                                                       type in a show by hand

    python3 -m api.cli bits sync <playlist_id>         pull the playlist in
    python3 -m api.cli bits list                       what's in the pool
    python3 -m api.cli bits today                      today's pick
"""

from __future__ import annotations

import os
import sys

from . import db
from .alerts import run_nags
from .poll import poll_once

TIER_LABEL = {1: "AUSTIN", 2: "ROAD TRIP", 3: "daydream"}
HEALTH_MARK = {
    "ok": "ok", "dormant": "dormant (seasonal, expected)",
    "suspicious": "SUSPICIOUS — went blind", "degraded": "degraded",
    "down": "DOWN", "config_failed": "CONFIG FAILED — credentials unresolvable",
    "unknown": "never polled",
}


def cmd_poll() -> int:
    r = poll_once()
    print("\nsources:")
    for s in r.health:
        seen = s["last_total_seen"]
        seen_s = f"{seen} listed" if seen is not None else "—"
        print(f"  {s['id']:<14} {HEALTH_MARK.get(s['health'], s['health']):<38} {seen_s}")
        if s["last_error"]:
            print(f"  {'':<14} └ {s['last_error'][:100]}")

    print(f"\n{len(r.new_events)} new event(s) this cycle")
    for e in r.new_events[:25]:
        d = f"{e['distance_mi']:.0f}mi" if e.get("distance_mi") is not None else "?"
        print(f"  [{TIER_LABEL[e['tier']]:<9}] {e['starts_at']:<16} {(e['venue'] or '')[:34]:<34} "
              f"{(e['city'] or '')[:24]:<24} {d}")
    if len(r.new_events) > 25:
        print(f"  … and {len(r.new_events) - 25} more")
    return 0


def cmd_shows() -> int:
    con = db.connect()
    rows = db.upcoming(con)
    if not rows:
        print("nothing upcoming.")
        return 0
    for tier in (1, 2, 3):
        group = [r for r in rows if r["tier"] == tier]
        if not group:
            continue
        print(f"\n── {TIER_LABEL[tier]} ({len(group)}) " + "─" * 40)
        for r in group:
            d = f"{r['distance_mi']:.0f}mi" if r["distance_mi"] is not None else ""
            flag = ""
            if r["austin_status"] == "unknown":
                flag = "  ← no Austin date announced yet"
            elif r["austin_status"] == "superseded":
                flag = "  ← Austin date exists; road trip probably unnecessary"
            elif r["austin_status"] == "owed_an_apology":
                flag = "  ← AUSTIN DATE APPEARED AFTER YOU BOUGHT. sorry."
            print(f"  {r['starts_at']:<17} {(r['venue'] or '')[:32]:<32} "
                  f"{(r['city'] or '')[:26]:<26} {d:>6}{flag}")
    con.close()
    return 0


def cmd_health() -> int:
    con = db.connect()
    for s in db.source_health(con):
        print(f"\n{s['name']}  [{s['id']}]")
        print(f"  health         {HEALTH_MARK.get(s['health'], s['health'])}")
        print(f"  last success   {s['last_success_at'] or 'never'}")
        print(f"  last seen      {s['last_total_seen']} events (baseline {s['baseline_total']})")
        if s["last_error"]:
            print(f"  error          [{s['last_error_kind']}] {s['last_error'][:120]}")
    con.close()
    return 0


def cmd_adduser() -> int:
    """adduser <id> <Name> [--curator]"""
    if len(sys.argv) < 4:
        print("usage: adduser <id> <Name> [--curator]")
        return 1
    uid, name = sys.argv[2], sys.argv[3]
    curator = "--curator" in sys.argv
    con = db.connect()
    token = db.add_user(con, uid, name, curator)
    base = os.environ.get("KAK_BASE_URL", "https://keepaustinkinane.austinmlapps.com")
    print(f"\n{name} ({uid}){'  [curator]' if curator else ''}")
    print(f"magic link:  {base}/?t={token}")
    print("\nOpen it on their phone, then Add to Home Screen. The token lands in")
    print("localStorage; there's no password to forget.\n")
    con.close()
    return 0


def cmd_nags() -> int:
    dry = "--dry-run" in sys.argv or "-n" in sys.argv
    con = db.connect()
    results = run_nags(con, dry_run=dry)
    if not results:
        print("nothing owed right now.")
    for r in results:
        mark = "DRY RUN" if r.get("dry_run") else f"sent={r['sent']} failed={r['failed']}"
        print(f"\n[{r['user']}] tier {r['tier']} · level {r['level']} · {mark}")
        print(f"  {r['title']}")
        print(f"  {r['body']}")
    con.close()
    return 0


def cmd_simulate() -> int:
    """
    Insert a fake tier-1 Austin show announced right now, so the full escalation
    ladder can be watched end to end before trusting it with a real one.

        python3 -m api.cli simulate
        python3 -m api.cli nags --dry-run
    """
    from datetime import datetime, timedelta
    con = db.connect()
    # first_seen_at must be UTC-aware like every other timestamp the app writes.
    # A naive local one lands hours off on any machine that isn't in Chicago,
    # which silently skips the announcement and opens with a level-2 nag.
    announced = db.now()
    show = (datetime.now() + timedelta(days=42)).replace(
        hour=19, minute=30, second=0, microsecond=0)
    eid = "SIMULATED|cap city|19:30"
    con.execute("DELETE FROM events WHERE id=?", (eid,))
    con.execute("DELETE FROM nags WHERE event_id=?", (eid,))
    con.execute("DELETE FROM user_events WHERE event_id=?", (eid,))
    con.execute(
        """INSERT INTO events (id, title, venue, city, starts_at, ticket_url, tier,
                               distance_mi, artist_confidence, first_seen_at, last_seen_at)
           VALUES (?,?,?,?,?,?,1,0,1.0,?,?)""",
        (eid, "Kyle Kinane (SIMULATED)", "Cap City Comedy Club", "Austin, TX",
         show.isoformat(timespec="minutes"), "https://capcitycomedy.com/",
         announced, announced),
    )
    con.commit()
    con.close()
    print(f"simulated tier-1 Austin show on {show:%a %b %d}, announced just now.")
    print("run:  python3 -m api.cli nags --dry-run")
    print("clear: python3 -m api.cli unsimulate")
    return 0


def cmd_unsimulate() -> int:
    con = db.connect()
    for t in ("events", "nags", "user_events"):
        col = "id" if t == "events" else "event_id"
        con.execute(f"DELETE FROM {t} WHERE {col} LIKE 'SIMULATED%'")
    con.commit()
    con.close()
    print("simulation cleared.")
    return 0


def cmd_add() -> int:
    """add <user> <venue> <YYYY-MM-DD> [HH:MM] [--city "X"] [--url U] [--note N]"""
    if len(sys.argv) < 5:
        print('usage: add <user> "<venue>" <YYYY-MM-DD> [HH:MM] [--city "Austin, TX"] '
              '[--url <ticket url>] [--note "..."]')
        return 1
    user, venue, date = sys.argv[2], sys.argv[3], sys.argv[4]
    rest = sys.argv[5:]
    time_s = rest[0] if rest and ":" in rest[0] and not rest[0].startswith("--") else ""

    def opt(flag, default=None):
        return rest[rest.index(flag) + 1] if flag in rest else default

    con = db.connect()
    if not con.execute("SELECT 1 FROM users WHERE id=?", (user,)).fetchone():
        print(f"no such user '{user}'. try: adduser {user} {user.title()}")
        return 1
    eid, is_new = db.add_manual_event(
        con, added_by=user, venue=venue, city=opt("--city", "Austin, TX"),
        date=date, time_s=time_s, ticket_url=opt("--url"), note=opt("--note"))
    row = con.execute("SELECT tier, distance_mi, city FROM events WHERE id=?", (eid,)).fetchone()
    d = f"{row['distance_mi']:.0f}mi" if row["distance_mi"] is not None else "unknown distance"
    print(f"\n{'added' if is_new else 'merged into an existing show'}: {venue} — {date} {time_s}")
    print(f"  {TIER_LABEL[row['tier']]}  ({row['city']}, {d})")
    if row["tier"] == 3:
        print("  NOTE: tier 3 never notifies. If that's wrong, the venue or city")
        print("        wasn't recognised — try adding the city explicitly.")
    con.close()
    return 0


def cmd_bits() -> int:
    """bits sync [playlist_id] | bits list | bits today"""
    from . import bits as bits_mod
    from .sources.youtube import api_key

    sub = sys.argv[2] if len(sys.argv) > 2 else "list"
    con = db.connect()

    if sub == "sync":
        pl = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("KAK_BITS_PLAYLIST", "")
        if not pl:
            print("usage: bits sync <playlist_id>   (or set KAK_BITS_PLAYLIST)")
            return 1
        print(f"key: {'YES — full metadata' if api_key() else 'no — scraping, no durations'}")
        r = bits_mod.sync_playlist(con, pl)
        print(f"via {r['via']}: fetched {r['fetched']}, added {r['added']}, dropped {r['dropped']}")
        sub = "list"

    if sub == "today":
        b = bits_mod.pick_for(con)
        if not b:
            print("no bits in the pool yet.")
        else:
            print(f"\n  {b.get('custom_title') or b['title']}")
            print(f"  {b['channel']}  ·  {b['kind']}  ·  {b['url']}")
        con.close()
        return 0

    rows = con.execute("SELECT * FROM bits ORDER BY kind, added_at").fetchall()
    if not rows:
        print("pool is empty. try: bits sync <playlist_id>")
    counts = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        rating = bits_mod.curator_rating(con, r["id"]) or "-"
        flag = "BLOCKED " if r["state"] != "active" else ""
        dur = f"{r['duration_s']//60}:{r['duration_s']%60:02d}" if r["duration_s"] else "  ?  "
        orient = "9:16" if r["vertical"] else "16:9"
        print(f"  {r['kind']:<7} {orient}  {dur:>6}  {rating:<7} {flag}"
              f"{(r['custom_title'] or r['title'] or '')[:52]}")
    print(f"\n  {dict(counts)}")
    con.close()
    return 0


def cmd_vapid() -> int:
    from .push import ensure_vapid
    con = db.connect()
    _, pub = ensure_vapid(con)
    con.close()
    print(f"VAPID public key:\n{pub}\n")
    print("Stored in the settings table. Regenerating invalidates every existing")
    print("push subscription, so don't.")
    return 0


def main() -> int:
    cmds = {"poll": cmd_poll, "shows": cmd_shows, "health": cmd_health,
            "adduser": cmd_adduser, "nags": cmd_nags, "vapid": cmd_vapid,
            "simulate": cmd_simulate, "unsimulate": cmd_unsimulate,
            "add": cmd_add, "bits": cmd_bits}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        return 1
    return cmds[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
