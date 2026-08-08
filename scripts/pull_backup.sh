#!/bin/sh
# Pull the newest database snapshot OFF the box.
#
# The daily backups in data/backups/ protect against a bad write; they do
# nothing for the OMV disk dying, and losing that database costs the VAPID
# keypair (both users re-enable notifications), both magic-link tokens, every
# rating, and the whole decision history. Run this from any OTHER machine —
# Mike's Mac via cron/launchd is the intended home.
#
# Usage:
#   KAK_TOKEN=<your magic-link token> ./scripts/pull_backup.sh [dest_dir]
#
# Keeps the newest 14 pulls; a backup directory that grows forever is how the
# second disk fills up too.

set -eu

BASE="${KAK_BASE_URL:-https://keepaustinkinane.austinmlapps.com}"
DEST="${1:-$HOME/Backups/keep-austin-kinane}"
KEEP=14

[ -n "${KAK_TOKEN:-}" ] || { echo "set KAK_TOKEN (your magic-link token)" >&2; exit 1; }

mkdir -p "$DEST"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$DEST/kak-pulled-$STAMP.sqlite3"

curl -fsS -H "Authorization: Bearer $KAK_TOKEN" \
  -o "$OUT" "$BASE/api/backup/latest"

# A truncated download is worse than none — it looks like a backup right up
# until the restore. sqlite's magic bytes are a cheap integrity floor.
head -c 16 "$OUT" | grep -q "SQLite format 3" || {
  rm -f "$OUT"
  echo "downloaded file is not a SQLite database — not keeping it" >&2
  exit 1
}

ls -1t "$DEST"/kak-pulled-*.sqlite3 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old"
done

echo "pulled $(du -h "$OUT" | cut -f1) -> $OUT"
