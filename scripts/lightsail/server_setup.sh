#!/usr/bin/env bash
# Runs ON the AWS Lightsail instance (Ubuntu 24.04 LTS, user "ubuntu"). Idempotent: safe to re-run on every deploy.
# Invoked by scripts/lightsail/push.sh after the code (incl. the prebuilt frontend in frontend/dist) is rsynced
# to ~/techsched. Result: nginx on :80 serves the SPA at /techsched/ and proxies /techsched/api → uvicorn :8100.
set -euo pipefail
APP_DIR="$HOME/techsched"
WEB_DIR=/var/www/techsched
cd "$APP_DIR"

echo "== system packages =="
if ! command -v nginx >/dev/null || ! python3.12 -m venv --help >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3.12 python3.12-venv nginx rsync curl >/dev/null
fi

echo "== swap (small Lightsail plans have 512 MB–1 GB RAM) =="
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== backend venv =="
[ -x backend/.venv/bin/python ] || python3.12 -m venv backend/.venv
backend/.venv/bin/pip install --quiet --upgrade pip
backend/.venv/bin/pip install --quiet -r backend/requirements.lock
mkdir -p data/cache

echo "== publish frontend =="
[ -f frontend/dist/index.html ] || { echo "frontend/dist missing — run push.sh from the dev machine"; exit 1; }
sudo mkdir -p "$WEB_DIR"
sudo rsync -a --delete frontend/dist/ "$WEB_DIR/"
sudo chmod -R a+rX "$WEB_DIR"

echo "== systemd service =="
sudo tee /etc/systemd/system/techsched-backend.service >/dev/null <<EOF
[Unit]
Description=TechSched backend (FastAPI/uvicorn)
After=network-online.target
Wants=network-online.target

[Service]
User=ubuntu
WorkingDirectory=$APP_DIR/backend
# single worker: the background risk-scan loop and SQLite assume one process
ExecStart=$APP_DIR/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8100
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --quiet techsched-backend
sudo systemctl restart techsched-backend

echo "== nginx =="
sudo tee /etc/nginx/sites-available/techsched >/dev/null <<'EOF'
server {
  listen 80 default_server;
  listen [::]:80 default_server;
  server_name _;

  location = / { return 302 /techsched/; }
  location = /techsched { return 302 /techsched/; }

  location /techsched/api/ {
    proxy_pass http://127.0.0.1:8100/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 180s;   # real-LLM agent turns can take tens of seconds
  }
  location = /techsched/health { proxy_pass http://127.0.0.1:8100/health; }

  location /techsched/ {
    alias /var/www/techsched/;
    try_files $uri $uri/ /techsched/index.html;
  }
}
EOF
sudo ln -sf /etc/nginx/sites-available/techsched /etc/nginx/sites-enabled/techsched
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t -q && sudo systemctl reload nginx

echo "== health check =="
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1/techsched/health >/dev/null; then
    curl -s http://127.0.0.1/techsched/health; echo
    echo "OK — open http://<static-ip>/techsched/"
    exit 0
  fi
  sleep 2
done
echo "backend did not become healthy; last logs:"; sudo journalctl -u techsched-backend -n 40 --no-pager; exit 1
