#!/bin/bash
set -e

PORT="${APP_PORT:-9210}"

echo "=== Red Petroleum OCPP Server ==="
echo "Port: $PORT | Env: ${APP_ENV:-development}"
echo "Working directory: $(pwd)"

mkdir -p logs 2>/dev/null || true

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
