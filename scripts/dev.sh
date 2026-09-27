#!/usr/bin/env bash
# Start backend (:8100) and frontend (:5174) together. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8100 --reload) &
BACK=$!
(cd frontend && npx vite --host 127.0.0.1 --port 5174) &
FRONT=$!
trap 'kill $BACK $FRONT 2>/dev/null || true' EXIT INT TERM
echo "backend: http://127.0.0.1:8100/docs   frontend: http://127.0.0.1:5174"
wait
