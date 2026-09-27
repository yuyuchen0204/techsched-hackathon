#!/usr/bin/env bash
# Rebuild docs/TechSched-产品手册.pdf from docs/product-handbook.zh.md.
# Run this after editing the handbook (e.g. filling in the team / competition / demo-video fields on the cover).
#
#   scripts/build_handbook_pdf.sh                         # product handbook, Chinese
#   scripts/build_handbook_pdf.sh en                      # product handbook, English
#   scripts/build_handbook_pdf.sh zh technical-design     # technical design doc, Chinese
#   scripts/build_handbook_pdf.sh en technical-design     # technical design doc, English
#   scripts/build_handbook_pdf.sh zh business-proposal    # business proposal, Chinese
#   scripts/build_handbook_pdf.sh en business-proposal    # business proposal, English
#
# Needs: python3 with `markdown` (pip install markdown) and the frontend's Playwright Chromium
# (`cd frontend && npm install && npx playwright install chromium`).
set -euo pipefail
cd "$(dirname "$0")/.."
export HANDBOOK_LANG="${1:-zh}"
export HANDBOOK_DOC="${2:-product-handbook}"
BUILD_DIR="docs/.handbook-build/$HANDBOOK_DOC.$HANDBOOK_LANG"
python3 scripts/handbook/make_figures.py
rm -f "$BUILD_DIR/pages.json"
python3 scripts/handbook/build_html.py        # pass 1: no page numbers yet
node scripts/handbook/measure_pages.mjs       # measure where each contents entry lands
python3 scripts/handbook/build_html.py        # pass 2: contents table now carries page numbers
node scripts/handbook/to_pdf.mjs
