#!/bin/sh
# Invoked by xdg-open when Chromium hands off a custom URL scheme.
set -eu
url="${1:-}"
if [ -z "$url" ]; then
  exit 0
fi
exec curl -sS --max-time 5 \
  -X POST \
  -H "Content-Type: application/json" \
  --data-binary "{\"url\":$(printf '%s' "$url" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}" \
  http://127.0.0.1:8100/internal/capture
