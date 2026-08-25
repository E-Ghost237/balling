#!/bin/bash
# Server-side cron target (crontab: `15 4 * * * /opt/balling/daily_fixtures_sync.sh`).
# Runs the API-Football fetch inside the already-running `api` container,
# straight against the live, bind-mounted data/football.db — no separate
# push step needed since this *is* the production file.
#
# Deliberately the one piece of fetch work that runs on the server rather
# than the user's own machine (explicit instruction) — a handful of rate
# limited API calls, not scraping traffic, so it's safe here.
#
# Lives at the repo root, tracked in git, specifically so deploy.sh's
# rsync keeps it present on every deploy — it used to be a one-off file
# created by hand over SSH, and got silently deleted by `rsync --delete`
# on the next deploy since it had no local counterpart to sync from.
set -euo pipefail
cd /opt/balling
mkdir -p logs

{
  echo ""
  echo "=== $(date -u +%FT%TZ) ==="
  sudo docker compose exec -T api python3 scrapers/api_football_daily_fixtures.py \
    --db data/football.db --days 2 --lookback-days 1
} >> logs/daily_fixtures_sync.log 2>&1
