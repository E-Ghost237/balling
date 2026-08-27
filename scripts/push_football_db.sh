#!/bin/bash
# Pushes the local data/football.db (updated by the SofaScore scrapers,
# which per project policy only ever run locally — never on the server)
# to the production EC2 box. Backs up the remote file first since this is
# a wholesale replace, not a merge: any bug in a local scrape run would
# otherwise overwrite good production data with no way back.
#
# Reuses the same terraform-output IP/key lookup as deploy.sh — same box.
#
# Snapshots via `sqlite3 .backup` rather than scp'ing data/football.db
# directly — confirmed the hard way 2026-08-27: a plain file copy is a
# raw byte read with no coordination with SQLite's own locking, so a
# scrape running concurrently in the background (writing new pages,
# possibly mid-checkpoint) can get caught mid-write, producing a torn,
# structurally-corrupt copy on the other end ("database disk image is
# malformed" on production, /simulate 500ing for every user until a
# backup was restored). `.backup` uses SQLite's own online-backup API,
# which IS safe against concurrent writers — it always produces a
# transactionally-consistent snapshot, whatever else is writing to the
# live file at the time.
set -euo pipefail
cd "$(dirname "$0")/.."

SNAPSHOT="$(mktemp /tmp/football.db.snapshot.XXXXXX)"
trap 'rm -f "$SNAPSHOT"' EXIT
# python3's stdlib sqlite3 module rather than the `sqlite3` CLI — the CLI
# isn't installed on this machine and pulling in a new system package
# just for this felt like more moving parts than the stdlib already lying
# around everywhere. Connection.backup() is the same online-backup API,
# and doubles as the integrity check: a torn/concurrent-write source
# would surface as an exception here rather than silently succeeding.
python3 - "$SNAPSHOT" <<'PYEOF'
import sqlite3
import sys

dest = sqlite3.connect(sys.argv[1])
with sqlite3.connect("data/football.db") as src:
    src.backup(dest)
integrity = dest.execute("PRAGMA integrity_check").fetchone()[0]
dest.close()
if integrity != "ok":
    print(f"==> ABORTED — snapshot failed integrity_check: {integrity}", file=sys.stderr)
    sys.exit(1)
PYEOF

TF_DIR="../trading-bot/terraform"
IP="$(cd "$TF_DIR" && terraform output -raw public_ip)"
KEY="$(cd "$TF_DIR" && terraform output -raw ssh_command | grep -oP '(?<=-i )\S+')"
KEY="$TF_DIR/$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new ubuntu@$IP"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "==> Backing up remote football.db (football.db.bak-$STAMP)"
$SSH "cd /opt/balling/data && sudo cp football.db football.db.bak-$STAMP"

echo "==> Copying local data/football.db (consistent snapshot) to $IP:/opt/balling/data/"
scp -i "$KEY" -o StrictHostKeyChecking=accept-new "$SNAPSHOT" "ubuntu@$IP:/tmp/football.db.new"
# uid/gid 1000, not root — matches the api container's appuser (Dockerfile
# creates it at 1000:1000). Chowning to root left the file read-only from
# inside the container: harmless for the webapp itself (only ever reads),
# but broke the on-server daily fixtures job the first time anything tried
# to write to this file from inside a running container.
$SSH "sudo mv /tmp/football.db.new /opt/balling/data/football.db && sudo chown 1000:1000 /opt/balling/data/football.db"

echo "==> Restarting api/worker so they pick up the new file"
$SSH "cd /opt/balling && sudo docker compose restart api worker"

echo "==> Done. Remote backup kept at /opt/balling/data/football.db.bak-$STAMP"
echo "    (old backups aren't auto-pruned — clean up periodically: ssh in and 'ls data/*.bak-*')"
