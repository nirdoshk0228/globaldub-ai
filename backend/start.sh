#!/usr/bin/env sh
# Single-container start (Render / Railway / HF Spaces free tiers): API + worker in one service.
set -e
cd "$(dirname "$0")"
if [ -n "$REDIS_URL" ]; then
  python worker/run.py &
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
