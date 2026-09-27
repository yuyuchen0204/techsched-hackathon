#!/usr/bin/env bash
# Deploy the current working tree to an AWS Lightsail Ubuntu instance. Run from the dev machine:
#   scripts/lightsail/push.sh <static-ip> <path-to-LightsailDefaultKey.pem>
# Builds the frontend locally (the server needs no Node), rsyncs code + .env + route/geocode caches to
# ubuntu@<ip>:~/techsched, then runs scripts/lightsail/server_setup.sh there. The server's data/app.db is never
# overwritten, so demo state survives re-deploys (use the dashboard's Reset to re-seed).
set -euo pipefail
HOST="${1:?usage: push.sh <static-ip> <key.pem>}"
KEY="${2:?usage: push.sh <static-ip> <key.pem>}"
cd "$(dirname "$0")/../.."
chmod 600 "$KEY"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new)

echo "== build frontend (base /techsched/) =="
(cd frontend && npm run build)

echo "== sync to $HOST =="
rsync -az --delete -e "${SSH[*]}" \
  --exclude .git --exclude .DS_Store --exclude __pycache__ --exclude '*.pyc' \
  --exclude backend/.venv --exclude frontend/node_modules \
  --exclude '.pytest_cache' --exclude '.mypy_cache' --exclude '.ruff_cache' \
  --exclude 'data/app.db*' --exclude data/evaluation --exclude docs/.handbook-build \
  ./ "ubuntu@$HOST:techsched/"

echo "== remote setup =="
"${SSH[@]}" "ubuntu@$HOST" 'bash ~/techsched/scripts/lightsail/server_setup.sh'

echo "done: http://$HOST/techsched/"
