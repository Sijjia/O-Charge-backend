# OCPP E2E Testing Results

> **Автор**: Эрмек (Backend)
> **Дата**: 2026-02-13
> **Статус**: Завершено
> **Симулятор**: solidstudiosh/ocpp-virtual-charge-point (одобрен оркестратором)

---

## 1. Что было сделано

### 1.1 Локальное dev-окружение

Создан полноценный стек для локального тестирования без внешних зависимостей:

| Компонент | Реализация | Порт |
|-----------|-----------|------|
| PostgreSQL 15 | Docker (`docker-compose.dev.yml`) | 5433 |
| Redis Alpine | Docker (`docker-compose.dev.yml`) | 6380 |
| Backend (FastAPI) | Python 3.11 venv | 9210 |
| OCPP Simulator | Node.js (gitignored `.simulator/`) | WS → 9210, Admin API → 9999 |

**Файлы dev-окружения:**

```
ocpp-rp/
├── docker-compose.dev.yml              # PG + Redis
├── backend/
│   ├── .env                            # Локальный конфиг (gitignored)
│   ├── sql/000_base_schema_local.sql   # Полная схема + тестовые данные
│   └── venv/                           # Python 3.11 (gitignored)
└── tools/
    ├── setup-local-dev.sh              # Автоматическая настройка всего
    ├── setup-simulator.sh              # Клонирование симулятора
    ├── test-ocpp.sh                    # Запуск симулятора (1.6 / 2.0.1)
    └── test-charging-flow.sh           # E2E тест полного цикла зарядки
```

### 1.2 Тестовые данные в БД

| Сущность | ID | Описание |
|----------|----|----------|
| Admin user | `test-admin-001` | operator/admin аккаунт |
| Client | `test-client-001` | Баланс 5000 сом, телефон +996700000001 |
| Location | `test-loc-001` | Бишкек, ул. Тестовая 1 |
| Station (1.6) | `SIM-TEST` | 22 кВт, Type2+CCS2, тариф 12 сом/кВт·ч |
| Station (2.0.1) | `SIM-TEST-201` | 50 кВт, CCS2, тариф 15 сом/кВт·ч |
| RFID Tag | `TEST-RFID-001` | Привязан к test-client-001, статус Accepted |
| Tariff | `test-tariff-001` | Стандартный 12 сом/кВт·ч |

---

## 2. Результаты E2E теста — OCPP 1.6

### Тест: полный цикл зарядки

**Дата прогона**: 2026-02-13 04:31 UTC+6
**Станция**: SIM-TEST (OCPP 1.6J)
**Симулятор**: ocpp-virtual-charge-point → Admin API (port 9999)

| # | Сообщение | Payload | Результат | Время |
|---|-----------|---------|-----------|-------|
| 1 | **BootNotification** | vendor=TestVendor, model=TestModel | **OK** — Accepted, interval=300s | 22:28:36 |
| 2 | **StatusNotification** | connector=1, status=Available, NoError | **OK** — connector_status обновлён в БД | 22:28:38 |
| 3 | **Authorize** | idTag=TEST-RFID-001 | **OK** — Accepted (idTag найден в ocpp_authorization) | 22:28:40 |
| 4 | **StartTransaction** | connector=1, idTag=TEST-RFID-001, meterStart=0 | **OK** — transaction_id=1770913916 | 22:28:54 |
| 5a | **MeterValues** | 5000 Wh (5 кВт·ч) | **OK** — записано в ocpp_meter_values | 22:31:58 |
| 5b | **MeterValues** | 15000 Wh (15 кВт·ч) | **OK** — записано | 22:32:00 |
| 5c | **MeterValues** | 30000 Wh (30 кВт·ч) | **OK** — записано | 22:32:02 |
| 6 | **StopTransaction** | tx=1770913916, meterStop=30000, reason=Local | **OK** — status=Stopped, stop_reason=Local | 22:32:04 |

### Данные в БД после теста

**ocpp_transactions:**
```
id=2, transaction_id=1770913916, station_id=SIM-TEST
connector_id=1, id_tag=TEST-RFID-001
meter_start=0, meter_stop=30000, status=Stopped, stop_reason=Local
```

**ocpp_meter_values (3 записи):**
```
id=5: energy=5000 Wh   (tx=1770913916)
id=6: energy=15000 Wh  (tx=1770913916)
id=7: energy=30000 Wh  (tx=1770913916)
```

**ocpp_station_status:**
```
station_id=SIM-TEST, status=Available, error_code=NoError
connector_status=[{connector_id:1, status:Available, error_code:NoError}]
```

### Вердикт OCPP 1.6: PASSED

---

## 3. Что проверено и работает

### Протокол OCPP 1.6

| Возможность | Статус | Детали |
|-------------|--------|--------|
| WebSocket подключение | OK | `ws://localhost:9210/ws/SIM-TEST` |
| Subprotocol negotiation | OK | `ocpp1.6` / `ocpp1.6j` |
| BootNotification | OK | Регистрация станции, heartbeat interval |
| Heartbeat | OK | Обновление last_heartbeat, is_available |
| StatusNotification | OK | Обновление connector_status JSON |
| Authorize | OK | Поиск idTag в БД, возврат Accepted/Blocked |
| StartTransaction | OK | Создание ocpp_transaction, назначение transaction_id |
| MeterValues | OK | Сохранение sampled_values, парсинг Energy.Active.Import.Register |
| StopTransaction | OK | Обновление meter_stop, stop_reason, status=Stopped |
| Redis pub/sub | OK | Станция появляется в connected_stations |
| Background station status | OK | Scheduler обновляет is_available каждые 2 мин |

### Инфраструктура

| Компонент | Статус | Детали |
|-----------|--------|--------|
| FastAPI + Uvicorn | OK | Запускается на порту 9210 |
| PostgreSQL подключение | OK | SQLAlchemy sync + async |
| Redis подключение | OK | redis://localhost:6380/0 |
| Health check `/health` | OK | Возвращает redis status + connected_stations |
| Swagger UI `/docs` | OK | Все endpoints видны |
| CORS | OK | localhost разрешён в dev-mode |

---

## 4. Что НЕ тестировалось (и почему)

| Фича | Причина | Когда тестировать |
|-------|---------|-------------------|
| **OCPP 2.0.1 E2E** | Нужен отдельный прогон с index_201.ts | Следующий спринт |
| **Enforcement лимита** (авто-стоп при 95%) | Требует активную charging_session через API | Интеграционный тест |
| **Резервирование 95%/90%** | Запускается через `POST /charging/start`, не через OCPP | Интеграционный тест |
| **Ночной тариф -20%** | PricingService работает при расчёте стоимости сессии | Unit-тест / интеграционный |
| **PDF чеки** | Генерируется при остановке через API | Ручной тест |
| **Namba One платежи** | Нет credentials (B4 blocker) | После получения ключей |
| **Partner revenue split** | Рассчитывается в ChargingService.stop | Интеграционный тест |
| **Booking + charging** | Сценарий: забронировать → зарядить → проверить | Интеграционный тест |
| **Load testing** | Множество одновременных станций | Отложено (решение оркестратора) |

---

## 5. Обнаруженные особенности

### 5.1 Transaction ID

Сервер назначает свой `transaction_id` (например `1770913916`) в ответе на StartTransaction. Симулятор получает его в CallResult, но Admin API (`/execute`) не возвращает response клиенту.

**Решение**: тест-скрипт запрашивает реальный transaction_id из БД после StartTransaction:
```bash
TX_ID=$(psql ... -c "SELECT transaction_id FROM ocpp_transactions
  WHERE station_id='SIM-TEST' ORDER BY created_at DESC LIMIT 1;")
```

### 5.2 Charging Session vs OCPP Transaction

OCPP-уровень (simulator → backend) создаёт только `ocpp_transactions`. Запись в `charging_sessions` создаётся через клиентский API (`POST /charging/start`). Это два разных уровня:

```
Client API flow:
  POST /charging/start → charging_sessions (id, reserved_amount, tariff)
  → RemoteStartTransaction → station

OCPP flow (от станции):
  StartTransaction → ocpp_transactions (transaction_id, meter_start)
  MeterValues → ocpp_meter_values
  StopTransaction → ocpp_transactions (meter_stop, status=Stopped)
```

Полный E2E (клиент → API → OCPP → станция) потребует два вызова: HTTP API + OCPP. Это задача для интеграционного теста.

### 5.3 Python 3.11+

Код использует `X | None` union syntax (PEP 604), требуется Python 3.10+. Системный Python на macOS — 3.9. Решение: `python3.11` из Homebrew.

### 5.4 .env не автозагружается

`pydantic-settings` в нашей конфигурации не имеет `env_file = ".env"`. Нужно загружать переменные перед запуском:
```bash
export $(cat .env | grep -v '^#' | grep -v '^$' | xargs)
```

---

## 6. Как воспроизвести тест

### Быстрый старт (всё уже настроено)

```bash
# 1. Docker (PG + Redis)
cd ocpp-rp
docker compose -f docker-compose.dev.yml up -d

# 2. Backend
cd backend
export $(cat .env | grep -v '^#' | grep -v '^$' | xargs)
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9210

# 3. Simulator (другой терминал)
cd tools/.simulator
WS_URL="ws://localhost:9210/ws" CP_ID="SIM-TEST" ADMIN_PORT=9999 npx tsx index_16.ts

# 4. E2E Test (третий терминал)
cd tools
bash test-charging-flow.sh 16
```

### С нуля

```bash
cd ocpp-rp
./tools/setup-local-dev.sh   # Docker + PG schema + venv + .env
# Далее шаги 2-4 выше
```

### OCPP 2.0.1 (когда будем тестировать)

```bash
# Simulator
WS_URL="ws://localhost:9210/ws" CP_ID="SIM-TEST-201" ADMIN_PORT=9999 npx tsx index_201.ts

# Test
bash test-charging-flow.sh 201
```

---

## 7. Рекомендации и следующие шаги

### Ближайшие

1. **Интеграционный тест**: Client API → OCPP — полный цикл с charging_session + billing
2. **OCPP 2.0.1 E2E**: Прогнать аналогичный тест через TransactionEvent
3. **CI pipeline**: Добавить docker-compose.dev.yml + test-charging-flow.sh в GitHub Actions

### Средняя перспектива

4. **Тест enforcement**: Создать сессию с лимитом, слать MeterValues до 95% — проверить авто-стоп
5. **Тест ночного тарифа**: Запустить сессию в 23:00-06:00, проверить скидку -20%
6. **Multi-station**: Запустить 5-10 симуляторов одновременно, проверить стабильность

### Блокеры

| Блокер | Влияет на | Статус |
|--------|-----------|--------|
| Namba One credentials | Тест платежей, пополнения | Ждём от RP |
| Дизайн Frontend | Полный E2E (PWA → API → OCPP) | Ждём от дизайнера |

---

## 8. Структура тестовых скриптов

```
tools/
├── setup-local-dev.sh          # Развёртывание всего dev-окружения
│   ├── Docker PG + Redis
│   ├── SQL schema + migrations
│   ├── .env creation
│   └── Python venv + deps
│
├── setup-simulator.sh          # Клонирование ocpp-virtual-charge-point
│
├── test-ocpp.sh                # Запуск симулятора
│   ├── ./test-ocpp.sh 16       # OCPP 1.6 (default)
│   ├── ./test-ocpp.sh 201      # OCPP 2.0.1
│   └── ./test-ocpp.sh 16 MY-STATION  # Custom station ID
│
└── test-charging-flow.sh       # E2E test через Admin API
    ├── BootNotification
    ├── StatusNotification (Available)
    ├── Authorize (RFID tag)
    ├── StartTransaction → fetch real txId from DB
    ├── MeterValues x3 (5/15/30 kWh)
    └── StopTransaction → verify in DB
```

---

*Документ готов. Вопросы — к Эрмеку.*
