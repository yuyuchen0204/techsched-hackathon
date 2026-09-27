#!/usr/bin/env bash
# Rebuild frontend + restart backend for the live deployment at https://byyyc.com/techsched
# (nginx: /etc/nginx/sites-available/byyyc.com, systemd: techsched-backend.service)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== backend deps =="
(cd backend && .venv/bin/pip install --quiet -r requirements.lock)

echo "== frontend build =="
(cd frontend && npm ci --silent && npm run build)

echo "== publish static files =="
sudo rsync -a --delete frontend/dist/ /var/www/techsched/
sudo chown -R ubuntu:ubuntu /var/www/techsched
sudo chmod -R a+rX /var/www/techsched

echo "== restart backend =="
sudo systemctl restart techsched-backend
sleep 1
curl -sf http://127.0.0.1:8100/health >/dev/null && echo "backend OK"

echo "== reload nginx (picks up any config changes) =="
sudo nginx -t && sudo systemctl reload nginx

echo "done: https://byyyc.com/techsched/"
