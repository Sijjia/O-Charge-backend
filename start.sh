#!/bin/bash

echo "Starting RedPetroleum OCPP Server..."
echo "HTTP API + WebSocket: Port 9210"

echo "Working directory: $(pwd)"

mkdir -p logs

exec python -m uvicorn app.main:app --host 0.0.0.0 --port 9210
