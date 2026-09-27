#!/usr/bin/env bash
# Explicit demo reset through the API (backend must be running). Usage: scripts/reset_demo.sh [scenario]
curl -s -X POST "http://127.0.0.1:${BACKEND_PORT:-8100}/api/demo/reset" -H 'content-type: application/json' -d "{\"scenario\":\"${1:-main}\"}"; echo
