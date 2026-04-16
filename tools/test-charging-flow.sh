#!/bin/bash
# test-charging-flow.sh — E2E тест зарядки через Admin API симулятора
#
# Предполагает что:
# 1. Backend запущен на localhost:9210
# 2. Симулятор запущен: ./test-ocpp.sh 16 SIM-TEST (в другом терминале)
#    Симулятор слушает Admin API на порту 9999
#
# Usage:
#   ./test-charging-flow.sh          # default: OCPP 1.6
#   ./test-charging-flow.sh 201      # OCPP 2.0.1
#
# Тестирует:
#   1. BootNotification
#   2. StatusNotification (Available)
#   3. Authorize (idTag)
#   4. StartTransaction
#   5. MeterValues (несколько раз)
#   6. StopTransaction

set -e

ADMIN_PORT="${ADMIN_PORT:-9999}"
ADMIN_URL="http://localhost:${ADMIN_PORT}/execute"
VERSION="${1:-16}"
SLEEP_BETWEEN=2  # секунды между шагами

# PostgreSQL connection for transaction ID lookup
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5433}"
PG_USER="${PG_USER:-evpower}"
PG_DB="${PG_DB:-evpower}"
export PGPASSWORD="${PG_PASSWORD:-evpower_dev_123}"
STATION_ID="${STATION_ID:-SIM-TEST}"

# Цвета
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

send() {
    local action="$1"
    local payload="$2"
    echo -e "${CYAN}[>>] $action${NC}"
    curl -s -X POST "$ADMIN_URL" \
        -H "Content-Type: application/json" \
        -d "{\"action\": \"$action\", \"payload\": $payload}" \
        && echo "" || echo " [FAILED]"
    sleep "$SLEEP_BETWEEN"
}

echo "=========================================="
echo " OCPP $VERSION — E2E Charging Flow Test"
echo " Admin API: $ADMIN_URL"
echo "=========================================="
echo ""

if [ "$VERSION" = "16" ] || [ "$VERSION" = "1.6" ]; then
    # ==============================
    # OCPP 1.6 Flow
    # ==============================

    echo -e "${YELLOW}[1/6] BootNotification${NC}"
    send "BootNotification" '{
        "chargePointVendor": "TestVendor",
        "chargePointModel": "TestModel",
        "chargePointSerialNumber": "SIM-TEST-001",
        "firmwareVersion": "1.0.0"
    }'

    echo -e "${YELLOW}[2/6] StatusNotification — Connector 1 Available${NC}"
    send "StatusNotification" '{
        "connectorId": 1,
        "errorCode": "NoError",
        "status": "Available"
    }'

    echo -e "${YELLOW}[3/6] Authorize${NC}"
    send "Authorize" '{
        "idTag": "TEST-RFID-001"
    }'

    echo -e "${YELLOW}[4/6] StartTransaction${NC}"
    send "StartTransaction" '{
        "connectorId": 1,
        "idTag": "TEST-RFID-001",
        "meterStart": 0,
        "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
    }'

    # Получаем реальный transactionId из БД (сервер назначает свой ID)
    TX_ID=$(psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -t -A -c \
        "SELECT transaction_id FROM ocpp_transactions WHERE station_id='$STATION_ID' ORDER BY created_at DESC LIMIT 1;" 2>/dev/null)
    if [ -z "$TX_ID" ]; then
        echo -e "  ${YELLOW}[!] Не удалось получить transactionId из БД, используем 1${NC}"
        TX_ID=1
    else
        echo -e "  ${GREEN}  transactionId из БД: $TX_ID${NC}"
    fi

    echo -e "${YELLOW}[5/6] MeterValues (3 updates)${NC}"
    for kwh in 5 15 30; do
        meter_wh=$((kwh * 1000))
        echo -e "  ${CYAN}  -> ${kwh} kWh (${meter_wh} Wh)${NC}"
        send "MeterValues" '{
            "connectorId": 1,
            "transactionId": '$TX_ID',
            "meterValue": [{
                "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
                "sampledValue": [{
                    "value": "'$meter_wh'",
                    "measurand": "Energy.Active.Import.Register",
                    "unit": "Wh"
                }]
            }]
        }'
    done

    echo -e "${YELLOW}[6/6] StopTransaction${NC}"
    send "StopTransaction" '{
        "transactionId": '$TX_ID',
        "meterStop": 30000,
        "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
        "reason": "Local",
        "idTag": "TEST-RFID-001"
    }'

elif [ "$VERSION" = "201" ] || [ "$VERSION" = "2.0.1" ]; then
    # ==============================
    # OCPP 2.0.1 Flow
    # ==============================

    TX_ID="test-$(date +%s)"

    echo -e "${YELLOW}[1/6] BootNotification${NC}"
    send "BootNotification" '{
        "reason": "PowerUp",
        "chargingStation": {
            "model": "TestModel",
            "vendorName": "TestVendor"
        }
    }'

    echo -e "${YELLOW}[2/6] StatusNotification — EVSE 1 Available${NC}"
    send "StatusNotification" '{
        "evseId": 1,
        "connectorId": 1,
        "connectorStatus": "Available",
        "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
    }'

    echo -e "${YELLOW}[3/6] Authorize${NC}"
    send "Authorize" '{
        "idToken": {
            "idToken": "TEST-RFID-001",
            "type": "ISO14443"
        }
    }'

    echo -e "${YELLOW}[4/6] TransactionEvent — Started${NC}"
    send "TransactionEvent" '{
        "eventType": "Started",
        "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
        "triggerReason": "Authorized",
        "seqNo": 0,
        "transactionInfo": {
            "transactionId": "'$TX_ID'"
        },
        "evse": {"id": 1, "connectorId": 1},
        "idToken": {"idToken": "TEST-RFID-001", "type": "ISO14443"}
    }'

    echo -e "${YELLOW}[5/6] TransactionEvent — Updated (MeterValues)${NC}"
    for kwh in 5 15 30; do
        meter_wh=$((kwh * 1000))
        echo -e "  ${CYAN}  -> ${kwh} kWh${NC}"
        send "TransactionEvent" '{
            "eventType": "Updated",
            "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
            "triggerReason": "MeterValuePeriodic",
            "seqNo": '$kwh',
            "transactionInfo": {
                "transactionId": "'$TX_ID'"
            },
            "meterValue": [{
                "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
                "sampledValue": [{
                    "value": '$meter_wh',
                    "measurand": "Energy.Active.Import.Register",
                    "unitOfMeasure": {"unit": "Wh"}
                }]
            }]
        }'
    done

    echo -e "${YELLOW}[6/6] TransactionEvent — Ended${NC}"
    send "TransactionEvent" '{
        "eventType": "Ended",
        "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
        "triggerReason": "StopAuthorized",
        "seqNo": 99,
        "transactionInfo": {
            "transactionId": "'$TX_ID'",
            "stoppedReason": "Local"
        },
        "meterValue": [{
            "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
            "sampledValue": [{
                "value": 30000,
                "measurand": "Energy.Active.Import.Register",
                "unitOfMeasure": {"unit": "Wh"}
            }]
        }]
    }'

else
    echo "Usage: $0 [16|201]"
    exit 1
fi

echo ""
echo -e "${GREEN}=========================================="
echo " Test complete!"
echo "=========================================="
echo -e "${NC}"

# Автоматическая проверка данных в БД
echo -e "${YELLOW}[DB Check] Результаты в БД:${NC}"
echo ""
echo "  OCPP Transactions:"
psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c \
    "SELECT id, transaction_id, station_id, connector_id, meter_start, meter_stop, status, stop_reason FROM ocpp_transactions WHERE station_id='$STATION_ID' ORDER BY created_at DESC LIMIT 3;" 2>/dev/null || echo "  (DB not available)"

echo ""
echo "  Meter Values:"
psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c \
    "SELECT id, transaction_id, energy_active_import_register, timestamp FROM ocpp_meter_values WHERE station_id='$STATION_ID' ORDER BY created_at DESC LIMIT 5;" 2>/dev/null || echo "  (DB not available)"

echo ""
echo "  Station Status:"
psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c \
    "SELECT station_id, status, is_online, last_heartbeat FROM ocpp_station_status WHERE station_id='$STATION_ID';" 2>/dev/null || echo "  (DB not available)"

unset PGPASSWORD
