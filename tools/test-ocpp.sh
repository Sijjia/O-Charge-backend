#!/bin/bash
# test-ocpp.sh — Запуск OCPP симулятора для тестирования
#
# Usage:
#   ./test-ocpp.sh 16              # OCPP 1.6 (default)
#   ./test-ocpp.sh 201             # OCPP 2.0.1
#   ./test-ocpp.sh 16  SIM-002     # Custom station ID
#   ./test-ocpp.sh 201 SIM-V201    # v2.0.1 с custom ID
#
# Env:
#   WS_HOST=localhost              # Хост сервера (default: localhost)
#   WS_PORT=9210                   # Порт сервера (default: 9210)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SIM_DIR="$SCRIPT_DIR/.simulator"

# Параметры
VERSION="${1:-16}"
STATION_ID="${2:-SIM-$(echo $VERSION | tr -d '.')}"
HOST="${WS_HOST:-localhost}"
PORT="${WS_PORT:-9210}"

# Проверяем установку
if [ ! -d "$SIM_DIR" ]; then
    echo "[!] Симулятор не установлен. Запускаю setup..."
    bash "$SCRIPT_DIR/setup-simulator.sh"
fi

cd "$SIM_DIR"

# Определяем entry point и WS URL
case "$VERSION" in
    16|1.6)
        ENTRY="index_16.ts"
        WS_URL="ws://${HOST}:${PORT}/ws"
        echo "==================================="
        echo " OCPP 1.6J Simulator"
        echo " Station: $STATION_ID"
        echo " Server:  $WS_URL"
        echo "==================================="
        ;;
    201|2.0.1)
        ENTRY="index_201.ts"
        WS_URL="ws://${HOST}:${PORT}/ws"
        echo "==================================="
        echo " OCPP 2.0.1 Simulator"
        echo " Station: $STATION_ID"
        echo " Server:  $WS_URL"
        echo "==================================="
        ;;
    *)
        echo "Usage: $0 [16|201] [station_id]"
        echo ""
        echo "  16  — OCPP 1.6J (default)"
        echo "  201 — OCPP 2.0.1"
        exit 1
        ;;
esac

echo ""

# Запуск
WS_URL="$WS_URL" CP_ID="$STATION_ID" npx tsx "$ENTRY"
