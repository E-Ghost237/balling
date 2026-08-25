#!/bin/bash
# Server-side cron target (crontab: `30 4 * * * /opt/balling/weekly_fixtures_sync.sh`).
# Runs sofascore_weekly_fixtures.py inside the already-running `api`
# container, straight against the live, bind-mounted data/football.db —
# no separate push step needed since this *is* the production file.
#
# Per the project's usual rule, SofaScore fetch work runs locally only —
# this is the one deliberate exception (same carve-out already made for
# daily_fixtures_sync.sh / API-Football), justified specifically by how
# cheap this particular script is: one request per distinct SofaScore
# tournament/season, once a day (see sofascore_weekly_fixtures.py's own
# docstring) — not a license to widen this job without re-checking that
# math first.
#
# Lives at the repo root, tracked in git, so deploy.sh's rsync keeps it
# present on every deploy — see daily_fixtures_sync.sh's comment for why
# that matters (a hand-created, untracked copy of that script was
# silently deleted by `rsync --delete` on a later deploy).
set -euo pipefail
cd /opt/balling
mkdir -p logs

{
  echo ""
  echo "=== $(date -u +%FT%TZ) ==="
  sudo docker compose exec -T api python3 scrapers/sofascore_weekly_fixtures.py \
    --db data/football.db
} >> logs/weekly_fixtures_sync.log 2>&1
