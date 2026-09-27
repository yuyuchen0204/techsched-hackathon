#!/usr/bin/env bash
# One-time setup: Python 3.12 venv via uv (or python3.12) + frontend deps.
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v uv >/dev/null 2>&1; then
  (cd backend && uv venv --python 3.12 .venv >/dev/null && uv sync --python .venv/bin/python)
else
  python3.12 -m venv backend/.venv
  backend/.venv/bin/pip install -r backend/requirements-dev.lock
fi
(cd frontend && npm install)
[ -f .env ] || cp .env.example .env
echo "setup complete. Put the catalog CSV at data/reference/repair_object_problem_database.csv (or set REPAIR_CATALOG_PATH)."
