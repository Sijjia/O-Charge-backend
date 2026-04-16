# Plan: OCPP 2.0.1 Support (P0 #6)

## Context

OCPP 2.0.1 — следующая P0 задача. Новые станции (2025+) поддерживают только 2.0.1. Нужна dual-protocol поддержка: и 1.6J и 2.0.1 одновременно на одном сервере. Определение версии через WebSocket subprotocol header. Внутренняя модель данных остаётся единой — v201 handler переводит сообщения в те же таблицы и сервисы.

**Текущее состояние:**
- `ocpp` Python library (unpinned в requirements.txt) уже поддерживает `ocpp.v201`
- `ws_handler.py` — `OCPPChargePoint(ocpp.v16.ChargePoint)`, 20 обработчиков
- Subprotocol negotiation принимает только `{"ocpp1.6", "ocpp1.6j", "ocpp1.6-json"}`
- `active_sessions` dict на уровне модуля — для мониторинга лимитов
- Сервисы: `OCPPTransactionService.start_transaction(transaction_id: int)`, `.stop_transaction()`, `OCPPMeterService.add_meter_values()`

**Scope P0**: BootNotification, Heartbeat, StatusNotification, Authorize, TransactionEvent (Started/Updated/Ended), RequestStart/StopTransaction. Device Management (SetVariables/GetVariables) — P2.

---

## Архитектура

```
WebSocket Connection → OCPPWebSocketHandler.handle_connection()
    │
    ├─ Sec-WebSocket-Protocol: ocpp1.6*   → OCPPChargePoint (v16)    [БЕЗ ИЗМЕНЕНИЙ]
    ├─ Sec-WebSocket-Protocol: ocpp2.0.1  → OCPP201ChargePoint (v201) [НОВЫЙ]
    └─ Нет subprotocol                    → OCPPChargePoint (v16)    [обратная совместимость]
    │
    └─ Общие: Redis pubsub, DB таблицы, сервисы, active_sessions
```

---

## Что делаем

### 1. SQL миграция `007_ocpp201.sql`

**Файл**: `ocpp-rp/backend/sql/007_ocpp201.sql`

```sql
-- stations.ocpp_version для отслеживания протокола
ALTER TABLE stations ADD COLUMN IF NOT EXISTS ocpp_version VARCHAR(10) DEFAULT '1.6';

-- Маппинг v201 string transaction_id → numeric (наши ocpp_transactions используют INTEGER)
CREATE TABLE IF NOT EXISTS ocpp_v201_tx_map (
    id SERIAL PRIMARY KEY,
    v201_transaction_id VARCHAR(36) NOT NULL,
    numeric_transaction_id INTEGER NOT NULL,
    station_id VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(v201_transaction_id, station_id)
);
CREATE INDEX IF NOT EXISTS idx_v201_tx_map_station ON ocpp_v201_tx_map(station_id);
CREATE INDEX IF NOT EXISTS idx_v201_tx_map_v201_id ON ocpp_v201_tx_map(v201_transaction_id);
```

Зачем `ocpp_v201_tx_map`: в v201 `transactionId` — строка (UUID), а `ocpp_transactions.transaction_id` — INTEGER. Храним маппинг `hash(uuid) → int` для совместимости.

---

### 2. Новый файл `ws_handler_v201.py`

**Файл**: `ocpp-rp/backend/ocpp_ws_server/ws_handler_v201.py` (~500-600 строк)

Класс `OCPP201ChargePoint(ocpp.v201.ChargePoint)`:

| v201 Handler | Эквивалент v16 | Что делает |
|---|---|---|
| `on_boot_notification(charging_station, reason)` | BootNotification | Та же логика + UPDATE stations.ocpp_version='2.0.1' |
| `on_heartbeat()` | Heartbeat | Идентична v16 |
| `on_status_notification(timestamp, connector_status, evse_id, connector_id)` | StatusNotification | evse_id=connector_number, нет error_code (Faulted→'OtherError') |
| `on_authorize(id_token)` | Authorize | `id_token.id_token` = id_tag, маппинг статусов |
| `on_transaction_event(event_type, timestamp, trigger_reason, seq_no, transaction_info, **kwargs)` | Start/Stop/MeterValues | **Ключевой** — 3 ветки по event_type |

**TransactionEvent маппинг:**

| event_type | v16 эквивалент | DB операции |
|---|---|---|
| `Started` | StartTransaction | Redis pending lookup → INSERT ocpp_transactions + ocpp_v201_tx_map → UPDATE charging_sessions.transaction_id → UPDATE connectors=occupied → active_sessions |
| `Updated` | MeterValues | OCPPMeterService.add_meter_values() → Enforcement лимитов (95%/90%) → UPDATE charging_sessions.energy |
| `Ended` | StopTransaction | OCPPTransactionService.stop_transaction() → Финальный расчёт (энергия, тариф, ночная скидка, возврат) → UPDATE connectors=available |

**transaction_id совместимость**: `numeric_tx_id = abs(hash(v201_tx_id)) % (2**31)` — коллизии невозможны в контексте одной станции.

**Финансовая логика** (`_finalize_charging_session`): Копия из v16 StopTransaction (energy_consumed, PricingService.get_effective_rate, refund/overage, payment_processed=TRUE). Прямой порт — безопаснее чем рефакторинг.

**Helper методы:**
- `_extract_energy_from_meter_values(meter_value)` — парсинг Energy.Active.Import.Register из v201 формата
- `_parse_v201_meter_values(meter_value)` — конвертация v201 meter_value в v16-совместимый формат для OCPPMeterService

**Импорт shared state**: `from .ws_handler import active_sessions` — обе версии используют один dict (станция подключается либо v16, либо v201, не обоими).

---

### 3. Изменения в `ws_handler.py`

**Файл**: `ocpp-rp/backend/ocpp_ws_server/ws_handler.py`

Минимальные изменения (~40 строк):

**a) Import** (добавить вверху):
```python
from ocpp.v201 import call as call_201
```

**b) `handle_connection()`** — расширение subprotocol detection (~15 строк):
- Добавить `{"ocpp2.0.1", "ocpp2.0", "ocpp20"}` в acceptable
- Сохранить `self._ocpp_version` = "1.6" или "2.0.1"
- Если v201: `from .ws_handler_v201 import OCPP201ChargePoint` → создать `OCPP201ChargePoint(station_id, adapter)`

**c) `_handle_redis_commands()`** — версионное ветвление команд:
- `RemoteStartTransaction`: если v201 → `call_201.RequestStartTransaction(id_token={...}, evse_id=...)`
- `RemoteStopTransaction`: если v201 → `call_201.RequestStopTransaction(transaction_id=v201_tx_id)`
- `Reset`: если v201 → `call_201.Reset(type="OnIdle"/"Immediate")` (строка, не enum)

---

### 4. Обновления `main.py`

**Файл**: `ocpp-rp/backend/app/main.py`

Минимально:
- Обновить docstring websocket_endpoint — "Supports OCPP 1.6 and 2.0.1"
- Обновить root endpoint — `"protocols": ["OCPP 1.6 JSON", "OCPP 2.0.1"]`

Новые WS роуты **НЕ нужны** — определение протокола через subprotocol header на существующих `/ws/` и `/ocpp/` роутах.

---

### 5. Обновить STATE.md

Отметить OCPP 2.0.1 как ✅ (или 80% если Device Management остаётся P2).

---

## Файлы для изменения

| Файл | Действие |
|------|----------|
| `ocpp-rp/backend/sql/007_ocpp201.sql` | Создать |
| `ocpp-rp/backend/ocpp_ws_server/ws_handler_v201.py` | Создать (~500-600 строк) |
| `ocpp-rp/backend/ocpp_ws_server/ws_handler.py` | Изменить (~40 строк: import, subprotocol, redis commands) |
| `ocpp-rp/backend/app/main.py` | Обновить docstring + root endpoint |
| `.planning/STATE.md` | Обновить статус |

---

## Ключевые решения

1. **Новый файл vs расширение ws_handler.py** → Новый файл. ws_handler.py уже 1850 строк, добавлять ещё 500 нечитаемо.
2. **Numeric transaction_id** → hash(v201_uuid) + маппинг таблица. Не ломаем INTEGER схему.
3. **evse_id = connector_number** → Стандартное упрощение для одноразъёмных EVSE.
4. **Финансовая логика** → Копия из v16, не рефакторинг. Безопаснее для P0.
5. **Redis команды** → Charging API продолжает слать `RemoteStartTransaction`. `_handle_redis_commands()` переводит в v201 формат.

---

## Verification

1. SQL миграция 007 в Supabase
2. Подключение OCPP 2.0.1 симулятора с `Sec-WebSocket-Protocol: ocpp2.0.1`
3. BootNotification → stations.ocpp_version = '2.0.1'
4. TransactionEvent(Started) → запись в ocpp_transactions + ocpp_v201_tx_map
5. TransactionEvent(Updated) → meter values + enforcement лимитов
6. TransactionEvent(Ended) → финальный расчёт, возврат средств
7. RemoteStart через REST API → RequestStartTransaction на v201 станцию
8. Проверить что v16 станции продолжают работать без изменений
9. `GET /admin/logs/ocpp?event_type=TransactionEvent.Started` — логи v201
