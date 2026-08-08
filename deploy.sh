#!/bin/bash
# Deploys balling to the same EC2 instance as trading-bot, as a fully
# separate docker-compose project. Reuses trading-bot's terraform outputs
# for the instance IP/SSH key since it's the same box.
set -euo pipefail
cd "$(dirname "$0")"

TF_DIR="../trading-bot/terraform"
IP="$(cd "$TF_DIR" && terraform output -raw public_ip)"
KEY="$(cd "$TF_DIR" && terraform output -raw ssh_command | grep -oP '(?<=-i )\S+')"
KEY="$TF_DIR/$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new ubuntu@$IP"

echo "==> Syncing repo to $IP:/opt/balling"
$SSH "sudo mkdir -p /opt/balling && sudo chown ubuntu:ubuntu /opt/balling"
rsync -az --delete \
  --exclude '.git' --exclude 'venv' --exclude 'webapp_venv' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude 'bin' \
  --exclude 'data/dedup_review' --exclude 'data/football.db' \
  -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
  ./ "ubuntu@$IP:/opt/balling/"

echo "==> Bringing up the stack"
$SSH "cd /opt/balling && sudo docker compose up -d --build"

# nginx resolves the `api` upstream hostname once and can hold a keepalive
# connection to the old container's IP after `api` is recreated above,
# which serves 502s until something makes it re-resolve. Restarting it
# here (instead of relying on a manual fix after the fact) keeps every
# deploy that touches api/worker from silently breaking the live site.
echo "==> Restarting nginx so it re-resolves the api container"
$SSH "cd /opt/balling && sudo docker compose restart nginx"

echo "==> Done. Reachable at: http://$IP"
