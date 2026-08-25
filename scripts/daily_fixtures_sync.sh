#!/bin/bash
# Daily job: fetch upcoming/recent fixtures from API-Football, then push
# the updated database to production — only if the fetch actually
# succeeded, so a bad run never overwrites production with a
# half-written or stale file. Runs on this machine only; the production
# server never talks to API-Football directly (see project policy on
# scraping — same reasoning as sofascore_scraper.py's docstring).
set -euo pipefail

# cron runs jobs with a minimal PATH (typically just /usr/bin:/bin) — this
# machine's terraform is a snap install at /snap/bin, which push_football_db.sh
# needs to find the server's IP/SSH key. Caught this directly: a test run
# under a stripped-down PATH failed at the push step with "terraform: command
# not found", which would otherwise have meant the daily fetch quietly kept
# working locally forever while production never actually got updated.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin:$PATH"

cd "$(dirname "$0")/.."

set -a
source .env
set +a

mkdir -p logs
LOG_FILE="logs/daily_fixtures_sync.log"

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  if ./webapp_venv/bin/python3 scrapers/api_football_daily_fixtures.py --db data/football.db; then
    echo "Fetch OK — pushing to production."
    ./scripts/push_football_db.sh
    echo "Done."
  else
    echo "Fetch FAILED — production left untouched."
    exit 1
  fi
} >> "$LOG_FILE" 2>&1
