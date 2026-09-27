#!/usr/bin/env bash
# Browser main flow (Playwright, headless Chromium). Requires backend :8100 and vite :5174 running.
#   first time: (cd frontend && npx playwright install chromium-headless-shell)
set -euo pipefail
cd "$(dirname "$0")/../../frontend"
mkdir -p ../data/evaluation/screenshots
cp ../scripts/e2e/main_flow.mjs ./.e2e-main.mjs
SHOT_DIR=../data/evaluation/screenshots node ./.e2e-main.mjs
rm -f ./.e2e-main.mjs
