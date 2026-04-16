# Отчёт: Полное логирование ошибок OCPP

**Дата:** 19 февраля 2026
**Коммит:** `17607f1`
**Автор:** Эрмек + Claude AI
**Ветка:** main

---

## Проблема

Система логирования OCPP-сервера имела серьёзные пробелы:

| Проблема | Количество | Риск |
|----------|-----------|------|
| `logger.error()` без трейсбека | 56 из 62 | Невозможно понять **где** произошла ошибка |
| `except Exception: pass` (тихое проглатывание) | 11 мест | Ошибки **полностью скрыты** — отладка невозможна |
| Нет категоризации ошибок | 100% | Нельзя отличить падение БД от ошибки данных |
| Ошибки не сохраняются в БД | 100% | Нет истории инцидентов для анализа |
| Путь к лог-файлу захардкожен | 1 место | `/tmp/app_errors.log` — не подходит для продакшена |

**Пример проблемы (до):**
```
ERROR OCPP.SIM-TEST Error in StartTransaction: relation "ocpp_transactions" does not exist
```
— Нет трейсбека. Нет контекста. Где ошибка? Redis? БД? Валидация?

---

## Что сделано

### 1. Трейсбеки (`exc_info=True`)

Добавлен полный стек вызовов ко **всем** 62 вызовам `logger.error()`.

**Было:** `logger.error(f"Error: {e}")`
**Стало:** `logger.error(f"Error: {e}", exc_info=True)`

Теперь каждая ошибка содержит полный путь до строки кода, где произошёл сбой.

### 2. Замена тихих `except:pass`

Все 11 мест, где ошибки проглатывались молча, заменены на `logger.warning()` с трейсбеком.

**Было:**
```python
except Exception:
    pass  # ошибка исчезает бесследно
```

**Стало:**
```python
except Exception as e:
    self.logger.warning(f"Не удалось записать OCPP-событие (BootNotification): {e}", exc_info=True)
```

### 3. Классификация ошибок — 100% покрытие

Функция `_classify_error(e)` автоматически определяет категорию по типу исключения:

| Категория | Что ловит | Что означает |
|-----------|-----------|-------------|
| `redis_error` | ConnectionError к Redis | Redis не запущен или недоступен |
| `db_error` | OperationalError, IntegrityError | БД недоступна или не применена миграция |
| `validation_error` | ValueError, TypeError, KeyError | Некорректные данные от станции |
| `financial_error` | Decimal, Overflow | Ошибка расчёта стоимости зарядки |
| `connection_error` | ConnectionReset, Timeout | Станция отключилась или сеть упала |
| `unknown_error` | Всё остальное | Непредвиденная ошибка |

**Покрытие:**
- OCPP 1.6 (`ws_handler.py`): **32 точки** — все обработчики
- OCPP 2.0.1 (`ws_handler_v201.py`): **14 точек** — все обработчики
- **Итого: 46 точек, 100% покрытие**

**Пример лога (после):**
```
ERROR OCPP.SIM-TEST Error in StartTransaction [db_error]: station=SIM-TEST, connector=1, id_tag=996555123456: relation "ocpp_transactions" does not exist
Traceback (most recent call last):
  File "ocpp_ws_server/ws_handler.py", line 588, in on_start_transaction
    transaction = OCPPTransactionService.start_transaction(
  ...
sqlalchemy.exc.ProgrammingError: relation "ocpp_transactions" does not exist
```

### 4. Сохранение ошибок в БД

В 10 критических путях ошибки записываются в таблицу `ocpp_event_logs`:

| Обработчик | Протокол |
|-----------|----------|
| StartTransaction | v1.6 |
| StopTransaction | v1.6 |
| StopTransaction finalize | v1.6 |
| MeterValues | v1.6 |
| Redis command handler | v1.6 |
| TransactionEvent (router) | v2.0.1 |
| TransactionEvent.Started | v2.0.1 |
| TransactionEvent.Ended | v2.0.1 |
| Session finalize | v2.0.1 |
| _check_limits (x2) | v2.0.1 |

### 5. Новые поля в `charging_sessions`

| Поле | Тип | Назначение |
|------|-----|-----------|
| `stop_reason` | VARCHAR | Причина остановки по OCPP (Local, Remote, EnergyLimitReached...) |
| `error_details` | TEXT | Структурированная информация об ошибке для отладки |

**SQL-миграция:** `sql/012_session_error_details.sql`

### 6. Настраиваемый путь к лог-файлу

**Было:** захардкожен `/tmp/app_errors.log`
**Стало:** переменная окружения `LOG_PATH` (по умолчанию `/var/log/redpetroleum-ocpp/errors.log`)

---

## Изменённые файлы

| Файл | Изменения |
|------|-----------|
| `ocpp_ws_server/ws_handler.py` | +143/-52 — классификация всех 32 обработчиков v1.6 |
| `ocpp_ws_server/ws_handler_v201.py` | +82/-21 — классификация всех 14 обработчиков v2.0.1 |
| `app/core/logging_config.py` | +13/-2 — LOG_PATH env var |
| `ocpp_ws_server/redis_manager.py` | +10/-5 — exc_info в Redis |
| `app/api/v1/charging/start.py` | +2/-2 — exc_info |
| `app/api/v1/charging/stop.py` | +2/-2 — exc_info |
| `app/api/v1/charging/validators.py` | +1/-1 — exc_info |
| `app/api/v1/charging/ocpp_bridge.py` | +1/-1 — exc_info |
| `app/db/models/ocpp.py` | +2 — stop_reason, error_details |
| `sql/012_session_error_details.sql` | НОВЫЙ — миграция |
| `docs/error-logging-demo.html` | НОВЫЙ — интерактивная демо-страница |

**Итого:** 11 файлов, +1379 / -85 строк

---

## Статистика до/после

| Метрика | Было | Стало |
|---------|------|-------|
| `exc_info=True` (трейсбеки) | 6 | **62** |
| Тихие `except:pass` | 11 | **0** |
| Классификация ошибок `[категория]` | 0% | **100%** (46 точек) |
| Запись ошибок в БД | 0 путей | **10 путей** |
| Категории ошибок | 0 | **6** |
| Путь к лог-файлу | захардкожен | **настраиваемый** |

---

## Как применить миграцию

```sql
-- Выполнить на Supabase или локальной БД:
ALTER TABLE charging_sessions ADD COLUMN IF NOT EXISTS stop_reason VARCHAR;
ALTER TABLE charging_sessions ADD COLUMN IF NOT EXISTS error_details TEXT;
```

---

## Интерактивная демо-страница

Файл `docs/error-logging-demo.html` — открыть в браузере для просмотра:
- Вкладка **"До / После"** — сравнение логов до и после изменений
- Вкладка **"Поток логов"** — как выглядит продакшен-лог
- Вкладка **"Таблицы БД"** — примеры записей в ocpp_event_logs и charging_sessions
- Вкладка **"Категории ошибок"** — полная таблица всех 46 обработчиков с форматами логов
