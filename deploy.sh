#!/usr/bin/env bash
# Ship it. Run on the OMV box, not locally.
#   deploy@192.168.1.200 : /srv/.../compose/keep-austin-kinane
set -euo pipefail

git stash --include-untracked || true
git pull --ff-only
docker compose pull || true
docker compose build api
# --force-recreate because nginx.conf is a single-file bind mount and git pull
# swaps the inode; a plain reload would serve the OLD config. This bit us on
# DawgHaus. See CLAUDE.md.
docker compose up -d --force-recreate
docker compose ps
