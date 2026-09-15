#!/bin/bash
# REMOVED FROM THE SERVER CRONTAB 2026-09-15, per explicit instruction:
# SofaScore (via sofascore_weekly_fixtures.py, run locally, see
# balling-local-data-fetch-workflow) is now the sole source for fixtures
# and results — api_football_daily_fixtures.py's ongoing ingestion was
# creating a second, redundant/conflicting source. This file is left in
# the repo, unscheduled, as a manual fallback only (e.g. if SofaScore
# were ever unreachable for an extended stretch) — do not re-add its
# cron entry without checking that's still wanted.
#
# Formerly: server-side cron target
# (`15 4 * * * /opt/balling/daily_fixtures_sync.sh`). Runs the
# API-Football fetch inside the already-running `api` container,
# straight against the live, bind-mounted data/football.db — no separate
# push step needed since this *is* the production file.
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
