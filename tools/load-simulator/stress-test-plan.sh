#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════
# Red Petroleum EV — Ступенчатый стресс-тест OCPP WebSocket
# Для презентации клиенту: метрики → рекомендации по серверу
# ══════════════════════════════════════════════════════════════════════════

set -e

# Конфигурация
WS_URL="${WS_URL:-ws://62.171.149.139:9210/ws}"
RESULTS_DIR="./stress-results"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_FILE="$RESULTS_DIR/results_${TIMESTAMP}.md"

# Ступени нагрузки
STEPS=(10 50 100 200 500 1000 2000 5000)
DURATION=60  # секунд на каждую ступень
PAUSE=10     # пауза между ступенями

mkdir -p "$RESULTS_DIR"

echo "
# 🔌 Red Petroleum EV — Результаты стресс-теста OCPP
**Дата**: $(date '+%Y-%m-%d %H:%M:%S')
**Сервер**: $WS_URL
**Длительность каждой ступени**: ${DURATION}с
**Пауза между ступенями**: ${PAUSE}с

## Характеристики сервера
$(ssh -o ConnectTimeout=3 root@62.171.149.139 'echo "- **CPU**: $(nproc) cores"; echo "- **RAM**: $(free -h | awk "/Mem:/{print \$2}")"; echo "- **OS**: $(uname -r)"; echo "- **ulimit -n**: $(ulimit -n)"' 2>/dev/null || echo "- N/A (SSH недоступен)")

---

## Результаты по ступеням

| Станций | Подключено | Ошибки | Msg Sent | Msg Recv | Rate (msg/s) | Charging | Время подключения |
|---------|------------|--------|----------|----------|--------------|----------|-------------------|" > "$RESULTS_FILE"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  🔌 OCPP Stress Test — Stepped Load"
echo "  Server: $WS_URL"
echo "  Steps: ${STEPS[*]}"
echo "══════════════════════════════════════════════════════════"
echo ""

for STATION_COUNT in "${STEPS[@]}"; do
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  🔌 Step: $STATION_COUNT stations"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Запускаем симулятор на DURATION секунд, потом SIGINT
    STATIONS=$STATION_COUNT \
    WS_URL="$WS_URL" \
    BATCH_SIZE=50 \
    BATCH_DELAY=100 \
    HEARTBEAT_INTERVAL=30000 \
    CHARGING_PROBABILITY=0.2 \
    timeout --signal=INT ${DURATION}s npx tsx src/index.ts 2>&1 | tee "$RESULTS_DIR/step_${STATION_COUNT}_${TIMESTAMP}.log" &
    
    SIM_PID=$!
    
    # Ждём завершения (timeout или natural exit)
    wait $SIM_PID 2>/dev/null || true
    
    # Парсим финальные метрики из лога
    LOG_FILE="$RESULTS_DIR/step_${STATION_COUNT}_${TIMESTAMP}.log"
    
    CONNECTED=$(grep -oP "Connected:\s*\K[\d,]+" "$LOG_FILE" | tail -1 | tr -d ',')
    ERRORS=$(grep -oP "Errors:\s*\K\d+" "$LOG_FILE" | tail -1)
    MSG_SENT=$(grep -oP "Sent:\s*\K[\d,]+" "$LOG_FILE" | tail -1 | tr -d ',')
    MSG_RECV=$(grep -oP "Recv:\s*\K[\d,]+" "$LOG_FILE" | tail -1 | tr -d ',')
    MSG_RATE=$(grep -oP "Rate:\s*\K\d+" "$LOG_FILE" | tail -1)
    CHARGING=$(grep -oP "Charging:\s*\K\d+" "$LOG_FILE" | tail -1)
    CONNECT_TIME=$(grep -oP "Time:\s*\K[\d.]+" "$LOG_FILE" | tail -1)
    
    # Дефолты
    CONNECTED=${CONNECTED:-0}
    ERRORS=${ERRORS:-0}
    MSG_SENT=${MSG_SENT:-0}
    MSG_RECV=${MSG_RECV:-0}
    MSG_RATE=${MSG_RATE:-0}
    CHARGING=${CHARGING:-0}
    CONNECT_TIME=${CONNECT_TIME:-0}
    
    echo "| $STATION_COUNT | $CONNECTED | $ERRORS | $MSG_SENT | $MSG_RECV | $MSG_RATE | $CHARGING | ${CONNECT_TIME}s |" >> "$RESULTS_FILE"

    echo ""
    echo "  ✅ Step $STATION_COUNT done: connected=$CONNECTED errors=$ERRORS rate=${MSG_RATE} msg/s"
    echo ""
    
    # Проверяем health сервера после ступени
    HEALTH=$(curl -s --max-time 5 http://62.171.149.139:9210/health 2>/dev/null)
    SERVER_STATIONS=$(echo "$HEALTH" | grep -oP '"connected_stations":\K\d+' 2>/dev/null || echo "?")
    echo "  📊 Server health: $SERVER_STATIONS stations connected"
    
    # Пауза для cleanup
    echo "  ⏸️  Pause ${PAUSE}s..."
    sleep $PAUSE
done

# Добавляем рекомендации
echo "
---

## 📊 Рекомендации по серверному оборудованию

### Вариант 1: До 200 станций (текущий план)
- **CPU**: 2 vCPU
- **RAM**: 4 GB
- **SSD**: 50 GB
- **Сеть**: 100 Mbit/s
- **Стоимость**: ~\$15-25/мес (Hetzner CX22 / DigitalOcean Basic)

### Вариант 2: До 1,000 станций
- **CPU**: 4 vCPU
- **RAM**: 8 GB
- **SSD**: 100 GB NVMe
- **Сеть**: 1 Gbit/s
- **Redis**: выделенный инстанс
- **Стоимость**: ~\$40-60/мес (Hetzner CX32 / DO Pro)

### Вариант 3: До 5,000 станций
- **CPU**: 8 vCPU
- **RAM**: 16 GB
- **SSD**: 200 GB NVMe
- **Сеть**: 1 Gbit/s
- **Redis**: кластер
- **PostgreSQL**: выделенный (Supabase Pro)
- **Uvicorn**: 4 workers
- **Стоимость**: ~\$100-150/мес

### Вариант 4: 10,000+ станций
- **Архитектура**: Горизонтальное масштабирование
- **2x API серверов** (8 vCPU / 16 GB каждый)
- **Load Balancer** (nginx / HAProxy)
- **Redis Sentinel** (3 ноды)
- **PostgreSQL**: managed (Supabase Team / RDS)
- **Стоимость**: ~\$300-500/мес

---
*Сгенерировано автоматически: $(date '+%Y-%m-%d %H:%M:%S')*
" >> "$RESULTS_FILE"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  ✅ Stress test complete!"
echo "  📄 Report: $RESULTS_FILE"
echo "══════════════════════════════════════════════════════════"
