#!/bin/bash
# Pushes the local data/football.db (updated by the SofaScore scrapers,
# which per project policy only ever run locally — never on the server)
# to the production EC2 box. Backs up the remote file first since this is
# a wholesale replace, not a merge: any bug in a local scrape run would
# otherwise overwrite good production data with no way back.
#
# Reuses the same terraform-output IP/key lookup as deploy.sh — same box.
set -euo pipefail
cd "$(dirname "$0")/.."

TF_DIR="../trading-bot/terraform"
IP="$(cd "$TF_DIR" && terraform output -raw public_ip)"
KEY="$(cd "$TF_DIR" && terraform output -raw ssh_command | grep -oP '(?<=-i )\S+')"
KEY="$TF_DIR/$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new ubuntu@$IP"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "==> Backing up remote football.db (football.db.bak-$STAMP)"
$SSH "cd /opt/balling/data && sudo cp football.db football.db.bak-$STAMP"

echo "==> Copying local data/football.db to $IP:/opt/balling/data/"
scp -i "$KEY" -o StrictHostKeyChecking=accept-new data/football.db "ubuntu@$IP:/tmp/football.db.new"
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
