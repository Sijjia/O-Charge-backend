#!/bin/bash
# setup-simulator.sh — Установка OCPP симулятора (одноразово)
# Клонирует ocpp-virtual-charge-point в .simulator/ (gitignored)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SIM_DIR="$SCRIPT_DIR/.simulator"

if [ -d "$SIM_DIR" ]; then
    echo "[OK] Симулятор уже установлен: $SIM_DIR"
    echo "     Для обновления: rm -rf $SIM_DIR && $0"
    exit 0
fi

echo "[1/3] Клонирую ocpp-virtual-charge-point..."
git clone --depth 1 https://github.com/solidstudiosh/ocpp-virtual-charge-point.git "$SIM_DIR"

echo "[2/3] Устанавливаю зависимости..."
cd "$SIM_DIR"
npm install

echo "[3/3] Готово!"
echo ""
echo "Запуск:"
echo "  OCPP 1.6:   $SCRIPT_DIR/test-ocpp.sh 16"
echo "  OCPP 2.0.1: $SCRIPT_DIR/test-ocpp.sh 201"
