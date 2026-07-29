"""
Command line for the show tracker — the thing you run to see if it works.

    python3 -m api.cli poll      fetch every source, store, tier, report
    python3 -m api.cli shows     what's upcoming, by tier
    python3 -m api.cli health    what each source can currently see
"""

from __future__ import annotations

import sys

from . import db
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


def main() -> int:
    cmds = {"poll": cmd_poll, "shows": cmd_shows, "health": cmd_health}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        return 1
    return cmds[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
