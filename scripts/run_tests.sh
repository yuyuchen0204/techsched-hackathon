#!/usr/bin/env bash
# Backend tests + lint + types, frontend type-check + build. Browser E2E: scripts/e2e (needs both servers running).
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== backend: pytest"; (cd backend && .venv/bin/python -m pytest -q)
echo "== backend: ruff";   (cd backend && .venv/bin/ruff check app tests)
echo "== backend: mypy";   (cd backend && .venv/bin/mypy app)
echo "== frontend: tsc + build"; (cd frontend && npx tsc -b && npm run build)
