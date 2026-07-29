# Deploying to the OMV box

First time only. After this, shipping is `git push` here + `./deploy.sh` there.

## 1. Clone it

```bash
ssh deploy@192.168.1.200
cd /srv/dev-disk-by-uuid-5c291e74-2a76-4eb0-924b-7bf8f9eca72c/compose
git clone https://github.com/AustinML93/keep-austin-kinane.git
cd keep-austin-kinane
```

## 2. Secrets

```bash
cp .env.example .env
nano .env
```

Fill in `TICKETMASTER_API_KEY`, `YOUTUBE_API_KEY`, and `KAK_BITS_PLAYLIST`.
`.env` is gitignored and `docker compose` reads it automatically.

## 3. Up

```bash
mkdir -p data
docker compose build api      # a minute or two the first time
docker compose up -d
docker compose ps
docker compose logs -f api    # ctrl-C to stop watching
```

Healthy startup logs the poller and nagger threads starting, then
`Application startup complete.`

Check it locally on the box before touching DNS:

```bash
curl -s localhost:7730/api/health | head -c 400
```

## 4. Cloudflare tunnel

Add a route in the CF dashboard:

    keepaustinkinane.austinmlapps.com  →  http://localhost:7730

Same pattern as DawgHaus. **Mike owns tunnel/DNS changes.**

⚠️ If the LAN can't resolve it right after, that's AdGuard Home holding a
negative cache — `docker restart adguardhome`. Cellular bypasses it.

## 5. Create the two users

```bash
docker compose exec api python -m api.cli adduser mike Mike --curator
docker compose exec api python -m api.cli adduser rob Rob
```

Each prints a magic link. **Open yours on your phone, send Rob his.** The token
lands in `localStorage`; then Add to Home Screen.

Anyone holding a link is that user — send Rob's over something you'd send a
door code over, not a public channel.

## 6. Prime it

```bash
docker compose exec api python -m api.cli poll        # sources + shows
docker compose exec api python -m api.cli bits sync   # the playlist
docker compose exec api python -m api.cli health      # what can be seen
docker compose exec api python -m api.cli shows
```

## 7. Prove the alerting works — do NOT skip this

The nagging is the product. It has never run against a real phone.

1. Open the app on **both** phones, Add to Home Screen, tap **Turn on
   notifications**, accept the permission prompt.
2. Confirm both subscriptions registered:
   ```bash
   docker compose exec api python -m api.cli health   # look at "subscriptions"
   ```
3. Fire a fake Austin show and let the ladder run:
   ```bash
   docker compose exec api python -m api.cli simulate
   docker compose exec api python -m api.cli nags     # sends for real
   ```
4. On the lock screen you should get **KINANE. AUSTIN.** with two action
   buttons: **GOT 'EM** and **CAN'T MAKE IT**. Tap one and confirm the nagging
   stops for that user and the other user's continues.
5. Clean up: `docker compose exec api python -m api.cli unsimulate`

**Verify on Rob's phone specifically**, not just yours. A push setup that works
on one device and silently fails on the other is exactly the miss this app
exists to prevent.

## Shipping changes after this

```bash
# here
git push
# on the box
cd .../compose/keep-austin-kinane && ./deploy.sh
```

`deploy.sh` force-recreates on purpose — `nginx.conf` is a single-file bind
mount and `git pull` swaps the inode, so a plain reload serves the OLD config.
This cost real time on DawgHaus once already.

⚠️ On any change to a shell asset, bump `?v=N` in `web/index.html` **and** the
`SHELL` list in `web/sw.js`, plus `CACHE = "kak-vN"`. Cloudflare edge-caches for
4h and will happily serve the old one. Currently at **v3**.

## If something's wrong

| Symptom | Likely cause |
|---|---|
| `docker compose build` fails on `cryptography` | Wrong base image — the Dockerfile must be `python:3.12-slim`, not alpine |
| API up, `/api/shows` empty | Nothing polled yet — run `cli poll` |
| `ticketmaster` missing from health | `TICKETMASTER_API_KEY` not set; it only registers when a key exists |
| bits sync says `via scrape` | `YOUTUBE_API_KEY` didn't reach the container — check `.env`, then `docker compose up -d --force-recreate` |
| Push permission granted but nothing arrives | Check `subscriptions` in `cli health`; the PWA must be installed to Home Screen |
| Changes not showing in the PWA | The caching ladder above. Full close/reopen of the PWA, sometimes twice |
