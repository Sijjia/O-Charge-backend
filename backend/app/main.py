# Environment variables загружаются из Docker/Coolify напрямую
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import uvicorn
import asyncio
from datetime import datetime
from sqlalchemy import text

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.security_middleware import SecurityMiddleware
from app.core.auth_middleware import AuthMiddleware
from app.core.idempotency_middleware import IdempotencyMiddleware
from app.core.payment_audit import PaymentAuditMiddleware
from ocpp_ws_server.ws_handler import (
    OCPPWebSocketHandler,
    graceful_shutdown_all_connections,
    restore_sessions_on_startup
)
from ocpp_ws_server.redis_manager import redis_manager
# mobile.py отключён — legacy FlutterFlow API без auth, 100% дублирует v1
from app.api.v1 import router as v1_router  # Новая модульная структура
from app.services.station_status_manager import StationStatusManager
from app.db.session import get_db
from app.db.session import get_session_local
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настройка улучшенного логирования
setup_logging()

# Настройка специфичных логгеров
logging.getLogger("OCPPHandler").setLevel(logging.INFO)
logging.getLogger("OCPP").setLevel(logging.INFO)
logging.getLogger("websockets").setLevel(logging.INFO)
logging.getLogger("fastapi").setLevel(logging.INFO)
logging.getLogger("uvicorn").setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# ============================================================================
# BACKGROUND TASKS ДЛЯ ПЛАТЕЖНОЙ СИСТЕМЫ
# ============================================================================

async def check_payment_status(payment_table: str, invoice_id: str, max_checks: int = 20):
    """
    Проверяет статус конкретного платежа до его завершения
    
    Args:
        payment_table: "balance_topups"
        invoice_id: ID платежа для проверки
        max_checks: Максимальное количество проверок (по умолчанию 20)
    """
    logger.info(f"🔍 Запуск мониторинга платежа {invoice_id} (таблица: {payment_table})")
    
    for check_number in range(1, max_checks + 1):
        try:
            # Ждем 15 секунд перед каждой проверкой
            await asyncio.sleep(15)
            
            # Проверяем статус платежа
            try:
                from app.db.session import get_session_local
                from app.crud.ocpp_service import payment_lifecycle_service
                
                SessionLocal = get_session_local()
                db = SessionLocal()
                
                result = await payment_lifecycle_service.perform_status_check(
                    db, payment_table, invoice_id
                )
                
                db.close()
                
                if result.get("success"):
                    new_status = result.get("new_status")
                    logger.info(f"🔍 Платеж {invoice_id}: проверка {check_number}/{max_checks}, статус: {new_status}")
                    
                    # Если платеж завершен - прекращаем мониторинг
                    if new_status in ['approved', 'canceled', 'refunded']:
                        logger.info(f"✅ Мониторинг платежа {invoice_id} завершен: {new_status}")
                        return
                else:
                    logger.warning(f"⚠️ Платеж {invoice_id}: ошибка проверки статуса")
                    
            except Exception as e:
                logger.error(f"❌ Ошибка проверки платежа {invoice_id}: {e}", exc_info=True)
                
        except Exception as e:
            logger.error(f"❌ Критическая ошибка мониторинга платежа {invoice_id}: {e}", exc_info=True)
            break
    
    logger.warning(f"⏰ Мониторинг платежа {invoice_id} завершен по таймауту ({max_checks} проверок)")

def start_payment_monitoring(payment_table: str, invoice_id: str, max_checks: int = 20):
    """
    Удобная функция для запуска мониторинга платежа из API endpoints
    
    Args:
        payment_table: "balance_topups"
        invoice_id: ID платежа для проверки
        max_checks: Максимальное количество проверок
    """
    asyncio.create_task(check_payment_status(payment_table, invoice_id, max_checks))
    logger.info(f"🔍 Запущен мониторинг платежа {invoice_id} (таблица: {payment_table})")

async def payment_cleanup_task():
    """Background task для периодической очистки просроченных платежей"""
    # Ждем 30 минут перед первым запуском
    await asyncio.sleep(1800)
    
    while True:
        try:
            logger.info("🧹 Запуск периодической очистки просроченных платежей...")
            
            db = None
            try:
                # Создаем connection только в момент использования
                from app.db.session import get_session_local
                SessionLocal = get_session_local()
                db = SessionLocal()
                
                from app.crud.ocpp_service import payment_lifecycle_service
                result = await payment_lifecycle_service.cleanup_expired_payments(db)
                if result.get("success"):
                    cancelled_topups = result.get('cancelled_topups', 0)
                    if cancelled_topups > 0:
                        logger.info(f"✅ Очистка завершена: отменено {cancelled_topups} пополнений")
                    else:
                        logger.info("✅ Очистка завершена: просроченных платежей не найдено")
                        
            except Exception as e:
                logger.error(f"Failed to create database connection for cleanup: {e}", exc_info=True)
            finally:
                if db is not None:
                    try:
                        db.close()
                    except Exception as e:
                        logger.debug(f"DB close failed in cleanup: {e}")
        
        except Exception as e:
            logger.error(f"Payment cleanup error: {e}", exc_info=True)
        
        # Ждем 1 час до следующей очистки
        await asyncio.sleep(3600)

async def cleanup_idempotency_keys_task():
    """Очистка устаревших ключей идемпотентности (> 7 дней)"""
    # Ждем 10 минут перед первым запуском
    await asyncio.sleep(600)
    while True:
        try:
            logger.info("🧹 Очистка idempotency_keys старше 7 дней...")
            SessionLocal = get_session_local()
            db = SessionLocal()
            try:
                db.execute(text("""
                    DELETE FROM idempotency_keys
                    WHERE created_at < NOW() - INTERVAL '7 days'
                """))
                db.commit()
                logger.info("✅ Очистка idempotency_keys выполнена")
            except Exception as e:
                logger.error(f"Ошибка очистки idempotency_keys: {e}", exc_info=True)
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Критическая ошибка задачи очистки idempotency_keys: {e}", exc_info=True)
        # Запускаем раз в сутки
        await asyncio.sleep(86400)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager для приложения"""
    logger.info("🚀 Starting OCPP WebSocket Server...")
    
    # Проверка Redis подключения
    logger.info("🔄 Initializing Redis connection...")
    try:
        ping_result = await redis_manager.ping()
        if ping_result:
            logger.info("✅ Redis connection established successfully")
        else:
            logger.error("❌ Redis connection failed")
    except Exception as e:
        logger.error(f"❌ Redis connection error: {e}", exc_info=True)
    
    logger.info("✅ Redis manager initialized")

    # Восстановление активных сессий из Redis (если был graceful restart)
    try:
        await restore_sessions_on_startup()
    except Exception as e:
        logger.warning(f"⚠️ Не удалось восстановить сессии: {e}")

    # Запуск только cleanup задачи (проверка статусов платежей теперь по событию)
    payment_cleanup_task_ref = asyncio.create_task(payment_cleanup_task())
    logger.info("🧹 Payment cleanup task started (1 час между проверками)")
    logger.info("🔍 Payment status checks будут запускаться при создании платежей")
    # Очистка идемпотентности
    idem_cleanup_task_ref = asyncio.create_task(cleanup_idempotency_keys_task())
    logger.info("🧹 Idempotency keys cleanup task started (ежедневно)")
    
    # Запуск scheduler для обновления статусов станций
    scheduler = AsyncIOScheduler()
    
    async def update_station_statuses_job():
        """Фоновая задача для обновления статусов станций"""
        try:
            with next(get_db()) as db:
                result = StationStatusManager.update_all_station_statuses(db)
                if result["deactivated"] or result["activated"]:
                    logger.info(f"📊 Обновлены статусы станций: "
                              f"активировано {len(result['activated'])}, "
                              f"деактивировано {len(result['deactivated'])}")
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче обновления статусов: {e}", exc_info=True)

    async def check_hanging_sessions_job():
        """Фоновая задача для автоматической остановки зависших сессий зарядки"""
        try:
            from app.api.v1.charging.service import ChargingService
            from app.db.session import get_session_local

            SessionLocal = get_session_local()
            db = SessionLocal()

            try:
                charging_service = ChargingService(db)
                result = await charging_service.check_and_stop_hanging_sessions(
                    redis_manager=redis_manager,
                    max_hours=12,  # Максимум 12 часов активной зарядки
                    connection_timeout_minutes=10  # Таймаут на подключение кабеля
                )

                if result.get("stopped_count", 0) > 0:
                    logger.warning(f"⚠️ Автоматически остановлено {result['stopped_count']} зависших сессий "
                                 f"({result.get('no_connection_sessions_found', 0)} без подключения, "
                                 f"{result.get('long_sessions_found', 0)} длинных)")
                    for session in result.get("sessions", []):
                        reason_text = "НЕТ ПОДКЛЮЧЕНИЯ" if session.get('reason') == 'no_connection' else "ДОЛГО"
                        logger.info(f"  - Сессия {session['session_id']} ({reason_text}): "
                                  f"{session.get('duration_minutes', 0):.0f}мин, "
                                  f"{session['energy_consumed']} кВт⋅ч, "
                                  f"возврат {session.get('refund_amount', 0)} сом")

                db.commit()
            except Exception as e:
                logger.error(f"Ошибка при проверке зависших сессий: {e}", exc_info=True)
                db.rollback()
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Критическая ошибка в задаче проверки зависших сессий: {e}", exc_info=True)

    # Запускаем каждые 2 минуты (чаще чем heartbeat timeout для надежности)
    scheduler.add_job(
        update_station_statuses_job,
        'interval',
        minutes=2,
        id='update_station_statuses',
        name='Update Station Statuses',
        misfire_grace_time=30
    )

    # Проверка зависших сессий каждые 30 минут
    scheduler.add_job(
        check_hanging_sessions_job,
        'interval',
        minutes=30,
        id='check_hanging_sessions',
        name='Check and Stop Hanging Charging Sessions',
        misfire_grace_time=60
    )

    async def expire_bookings_job():
        """Фоновая задача для автоматического истечения просроченных бронирований"""
        try:
            from app.services.booking_service import BookingService
            from app.db.session import get_session_local

            SessionLocal = get_session_local()
            db = SessionLocal()

            try:
                booking_service = BookingService(db)
                expired = booking_service.expire_bookings()

                if expired:
                    logger.info(f"Истекло бронирований: {len(expired)}")
            except Exception as e:
                logger.error(f"Ошибка при expire бронирований: {e}", exc_info=True)
                db.rollback()
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Критическая ошибка в задаче expire бронирований: {e}", exc_info=True)

    # Истечение бронирований каждую минуту
    scheduler.add_job(
        expire_bookings_job,
        'interval',
        minutes=1,
        id='expire_bookings',
        name='Expire Overdue Bookings',
        misfire_grace_time=30
    )

    async def cancel_expired_guest_sessions_job():
        """Фоновая задача для отмены неоплаченных гостевых сессий (> 15 мин)"""
        try:
            from app.api.v1.guest.service import GuestChargingService
            SessionLocal = get_session_local()
            db = SessionLocal()
            try:
                cancelled = await GuestChargingService.cancel_expired_sessions(db, timeout_minutes=15)
            except Exception as e:
                logger.error(f"Ошибка отмены гостевых сессий: {e}", exc_info=True)
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Критическая ошибка задачи отмены гостевых сессий: {e}", exc_info=True)

    scheduler.add_job(
        cancel_expired_guest_sessions_job,
        'interval',
        minutes=5,
        id='cancel_expired_guest_sessions',
        name='Cancel Expired Guest Sessions',
        misfire_grace_time=30
    )

    scheduler.start()
    logger.info("⏰ Scheduler запущен:")
    logger.info("  - Обновление статусов станций: каждые 2 минуты")
    logger.info("  - Проверка зависших сессий зарядки: каждые 30 минут")
    logger.info("    • Автоостановка без подключения: > 10 минут")
    logger.info("    • Автоостановка длинных сессий: > 12 часов")
    logger.info("  - Истечение бронирований: каждую минуту")
    logger.info("  - Отмена неоплаченных гостевых сессий: каждые 5 минут")
    
    yield

    # ====== GRACEFUL SHUTDOWN ======
    logger.info("🛑 Начинаем graceful shutdown...")

    # 1. Сначала закрываем все WebSocket соединения (сохраняем сессии в Redis)
    try:
        await graceful_shutdown_all_connections()
    except Exception as e:
        logger.error(f"Ошибка graceful shutdown WebSocket: {e}", exc_info=True)

    # 2. Останавливаем scheduler
    scheduler.shutdown()

    # 3. Отменяем background tasks
    payment_cleanup_task_ref.cancel()
    idem_cleanup_task_ref.cancel()

    # 4. Закрываем webhook HTTP клиент
    try:
        from app.services.webhook_service import webhook_service
        await webhook_service.close()
    except Exception as e:
        logger.warning(f"Webhook client close error: {e}")

    logger.info("✅ Graceful shutdown завершён — станции переподключатся к новому инстансу")

# Создание FastAPI приложения
OPENAPI_TAGS = [
    # ─── Auth ────────────────────────────────────────────────────────────────
    {"name": "auth", "description": "Session management: CSRF tokens, refresh, logout, /me. Все защищённые эндпоинты требуют cookie `evp_access`."},
    {"name": "auth-otp", "description": "Аутентификация по номеру телефона через WhatsApp OTP (основной способ входа)."},
    {"name": "auth-sms-otp", "description": "Аутентификация по номеру телефона через SMS OTP (резервный канал)."},
    {"name": "auth-sso", "description": "SSO через Keycloak (для сотрудников компании). Exchange authorization code → сессия приложения."},
    {"name": "auth-dev", "description": "⚠️ Dev only. Быстрый вход без OTP — только при `APP_ENV != production`. Не использовать в production!"},
    # ─── Client ──────────────────────────────────────────────────────────────
    {"name": "charging", "description": "Управление зарядными сессиями: старт, стоп, статус в реальном времени, PDF-квитанции."},
    {"name": "balance", "description": "Баланс клиента: просмотр, пополнение (QR / карта / Namba One), настройка авто-пополнения."},
    {"name": "payment", "description": "Платёжный процессинг: H2H оплата картой, токенизированные платежи, статус, вебхуки Namba One."},
    {"name": "station", "description": "Публичная информация о станции: статус в реальном времени, доступность коннекторов, тарифы."},
    {"name": "locations", "description": "Публичный список локаций с агрегацией станций. Доступен без авторизации."},
    {"name": "profile", "description": "Профиль клиента: просмотр, обновление, смена телефона, удаление аккаунта."},
    {"name": "history", "description": "История: зарядные сессии, транзакции, статистика использования."},
    {"name": "favorites", "description": "Избранные локации: список, добавить, удалить, переключить."},
    {"name": "booking", "description": "Бронирование коннектора: создать (макс 60 мин), просмотреть активное, отменить."},
    {"name": "notifications", "description": "Web Push уведомления: VAPID ключ, подписка / отписка браузера."},
    {"name": "corporate", "description": "Корпоративный кабинет: дашборд компании, управление сотрудниками, отчёты по флоту."},
    {"name": "guest-charging", "description": "Гостевая зарядка без регистрации — оплата по номеру телефона через QR на станции."},
    # ─── Partner ─────────────────────────────────────────────────────────────
    {"name": "partner", "description": "Кабинет партнёра: дашборд, станции, сессии, аналитика доходов и доля выручки."},
    # ─── Admin ───────────────────────────────────────────────────────────────
    {"name": "admin-users", "description": "Admin: управление пользователями и операторами (superadmin)."},
    {"name": "admin-stations", "description": "Admin: CRUD зарядных станций — создание, редактирование, OCPP-конфиг, статус."},
    {"name": "admin-connectors", "description": "Admin: CRUD коннекторов станции — добавить, обновить, удалить порты."},
    {"name": "admin-locations", "description": "Admin: CRUD локаций — адреса, координаты, фото, привязка станций."},
    {"name": "admin-tariffs", "description": "Admin: тарифные планы — создание правил, ночные тарифы, привязка к станциям, AI-ценообразование."},
    {"name": "admin-partners", "description": "Admin: онбординг партнёров, настройка доли выручки, KPI."},
    {"name": "admin-clients", "description": "Admin: список клиентов, поиск, детали профиля, баланс."},
    {"name": "admin-corporate", "description": "Admin: корпоративные группы, сотрудники, биллинг, отчёты."},
    {"name": "admin-sessions", "description": "Admin: список зарядных сессий, детали, принудительная остановка."},
    {"name": "admin-logs", "description": "Admin: OCPP логи, стриминг серверных логов, список онлайн-станций."},
    {"name": "admin-analytics", "description": "Admin: аналитика выручки, обзор инфраструктуры, тепловая карта, рост пользователей, uptime."},
    {"name": "admin-alerts", "description": "Admin: критические алерты системы и подтверждение их обработки."},
    {"name": "admin-bookings", "description": "Admin: список всех бронирований, управление, история."},
    {"name": "admin-integrations", "description": "Admin: настройки картографических интеграций (2GIS, Google Maps и др.) — API ключи, синхронизация."},
    {"name": "admin-simulator", "description": "Admin: интерактивный OCPP-симулятор станции для тестирования без физического железа."},
    {"name": "admin-equipment", "description": "Admin: каталог оборудования — производители, модели, технические характеристики."},
    {"name": "admin-webhooks", "description": "Admin: исходящие webhooks — подписка на события (зарядка, статусы, станции). Система отправляет HTTP POST на зарегистрированный URL при каждом событии."},
    {"name": "admin-stress-test", "description": "⚠️ Admin: нагрузочное тестирование API (только sandbox-окружение)."},
    # ─── System ──────────────────────────────────────────────────────────────
    {"name": "system", "description": "Health check, версия, readiness probe для оркестраторов."},
]

_API_DESCRIPTION = """
## Red Petroleum EV — Charging Platform API

**Base URL:** `https://redp.charge.redpay.kg`
**Version:** 1.1.0 · **Protocol:** OCPP 1.6J (WebSocket)

---

### 🔐 Аутентификация

Все эндпоинты (кроме `/auth/*`, `/guest/*`, `/locations`, `/station/status/*`, `/health`) требуют cookie-аутентификации.

**Шаги:**
1. Получить CSRF-токен → `GET /api/v1/auth/csrf`
2. Отправить OTP → `POST /api/v1/auth/sms/send-otp` (SMS) или `POST /api/v1/auth/otp/send` (WhatsApp)
3. Подтвердить OTP → `POST /api/v1/auth/sms/verify` или `POST /api/v1/auth/otp/verify`
   - Сервер устанавливает cookies: `evp_access` (10 мин) и `evp_refresh` (7 дней)
   - Если пользователь не существует — создаётся автоматически
4. Передавать cookies в каждом запросе (`credentials: 'include'` в fetch / `-b cookies.txt` в curl)
5. Обновить токены → `POST /api/v1/auth/refresh`
6. Текущий пользователь → `GET /api/v1/auth/me`

**CSRF защита:** для всех мутирующих запросов (POST/PUT/PATCH/DELETE) передать заголовок:
```
X-CSRF-Token: <token из GET /api/v1/auth/csrf>
```

**SSO (сотрудники):** `POST /api/v1/auth/sso/exchange` — обмен Keycloak authorization code на сессию.

**Пример (curl):**
```bash
# 1. CSRF
curl -c cookies.txt https://redp.charge.redpay.kg/api/v1/auth/csrf

# 2. Отправить OTP
curl -b cookies.txt -c cookies.txt -H "X-CSRF-Token: <token>" \\
  -H "Content-Type: application/json" \\
  -d '{"phone": "+996555000000"}' \\
  https://redp.charge.redpay.kg/api/v1/auth/sms/send-otp

# 3. Подтвердить
curl -b cookies.txt -c cookies.txt -H "X-CSRF-Token: <token>" \\
  -H "Content-Type: application/json" \\
  -d '{"phone": "+996555000000", "code": "123456"}' \\
  https://redp.charge.redpay.kg/api/v1/auth/sms/verify

# 4. Готово — запросы с cookie
curl -b cookies.txt https://redp.charge.redpay.kg/api/v1/admin/stations
```

---

### 🔌 Статусы станций

У станции **два независимых статуса**:

**Административный статус** (`status`) — ставится вручную администратором:

| Статус | Описание | Возможности |
|--------|----------|-------------|
| `active` | Станция работает | Зарядка, бронирование, мониторинг |
| `inactive` | Станция отключена | Только просмотр |
| `maintenance` | На обслуживании | Только просмотр |

**Онлайн-статус** (`is_online` / `is_available`) — определяется автоматически по heartbeat:

| Поле | Значение | Описание |
|------|----------|----------|
| `is_online: true` | heartbeat < 5 мин назад | Станция на связи |
| `is_online: false` | heartbeat > 5 мин назад | Станция оффлайн |
| `last_heartbeat` | ISO 8601 | Время последнего heartbeat |

**Станция доступна для зарядки** когда: `status = active` **И** `is_online = true` **И** есть свободные коннекторы.

### 🔋 Статусы коннекторов

| Статус | Описание | Действия | Кто ставит |
|--------|----------|----------|------------|
| `available` | Свободен | Начать зарядку, забронировать | OCPP StatusNotification, система |
| `occupied` | Занят (зарядка/подготовка/завершение) | Мониторинг сессии, остановка | OCPP StatusNotification |
| `reserved` | Забронирован через приложение | Ожидание клиента (макс 30 мин) | Booking API |
| `unavailable` | Временно недоступен | Ожидание восстановления | OCPP StatusNotification |
| `faulted` | Неисправен | Требуется обслуживание | OCPP StatusNotification |

**Маппинг OCPP → наша система:**

| OCPP статус | Наш статус | Комментарий |
|-------------|------------|-------------|
| Available | `available` | Свободен |
| Preparing, Charging, SuspendedEVSE, SuspendedEV, Finishing | `occupied` | Все фазы зарядки |
| Reserved (OCPP) | `occupied` | OCPP-резервация = занят |
| Unavailable | `unavailable` | Временно недоступен |
| Faulted | `faulted` | Аппаратная ошибка |

### 📋 Список станций с коннекторами

**Публичный (без авторизации):** `GET /api/v1/station/status/{station_id}` — полная информация о станции, включая название, все коннекторы с номерами, типами и статусами.

**Админ-список:** `GET /api/v1/admin/stations?limit=200` — все станции с пагинацией, поиском, фильтрами.

**Детали станции:** `GET /api/v1/admin/stations/{station_id}` — станция + массив коннекторов (connector_number, connector_type, power_kw, status).

---

### 📡 Исходящие Webhooks (колбэки)

Система отправляет HTTP POST на зарегистрированные URL при событиях зарядки и станций.

**Управление:** `POST/GET/PUT/DELETE /api/v1/admin/webhooks` (раздел admin-webhooks)

**Формат POST запроса на ваш URL:**
```json
{
  "event": "charging.started",
  "timestamp": "2026-03-25T10:00:00+00:00",
  "data": {
    "station_id": "st-001",
    "connector_id": 1,
    "session_id": "uuid",
    ...
  }
}
```

**Заголовки:**
- `X-Webhook-Event: charging.started`
- `X-Webhook-Signature-256: sha256={HMAC-SHA256 hex digest}`
- `Content-Type: application/json`

**Верификация подписи:** `HMAC-SHA256(raw_body_bytes, your_secret)` → сравнить с заголовком `X-Webhook-Signature-256` (используйте timing-safe сравнение)

**Retry:** 3 попытки с exponential backoff (1с, 5с, 25с). Успех = HTTP 2xx.

**Throttling:** `charging.progress` отправляется не чаще 1 раза в 30 секунд на станцию.

**Доступные события:**

| Событие | Когда | Ключевые поля data |
|---------|-------|-------------------|
| `connector.status_changed` | Статус коннектора изменился | station_id, connector_id, status, error_code |
| `charging.started` | Зарядка началась | station_id, connector_id, transaction_id, session_id, meter_start |
| `charging.progress` | Обновление показаний | station_id, session_id, energy_kwh, power_kw |
| `charging.completed` | Зарядка завершена | station_id, session_id, energy_kwh, meter_stop, reason |
| `charging.error` | Ошибка OCPP | station_id, connector_id, error_code, info |
| `station.online` | Станция в онлайне | station_id, serial_number |
| `station.offline` | Станция оффлайн | station_id, serial_number |

---

### ⚡ Процесс зарядки — ответы API

**POST /api/v1/charging/start** — ответ при успехе:
```json
{
  "success": true,
  "session_id": "uuid",
  "station_id": "st-001",
  "connector_id": 1,
  "reserved_amount": 475.0,
  "estimated_cost": 250.0,
  "tariff_rate": 12.5,
  "night_tariff_applied": false,
  "new_balance": 525.0,
  "station_online": true,
  "message": "Зарядка запущена"
}
```

**GET /api/v1/charging/status/{session_id}** — ответ во время зарядки:
```json
{
  "success": true,
  "session": {
    "id": "uuid",
    "status": "started",
    "station_id": "st-001",
    "connector_id": 1,
    "energy_consumed": 5.2,
    "current_cost": 65.0,
    "power_kw": 22.0,
    "charging_duration_minutes": 15,
    "reserved_amount": 475.0,
    "rate_per_kwh": 12.5,
    "progress_percent": 26.0,
    "ev_battery_soc": 45,
    "station_online": true
  }
}
```

**POST /api/v1/charging/stop** — ответ при завершении:
```json
{
  "success": true,
  "session_id": "uuid",
  "energy_consumed": 15.8,
  "rate_per_kwh": 12.5,
  "reserved_amount": 475.0,
  "actual_cost": 197.5,
  "refund_amount": 277.5,
  "new_balance": 802.5,
  "station_online": true
}
```

---

### ⚠️ Ограничения и правила

| Правило | Значение |
|---------|----------|
| Одновременных сессий на клиента | **1** |
| Макс. сумма резервирования | **100,000 KGS** |
| Макс. энергия за сессию | **200 кВт⋅ч** |
| Номер коннектора | **1-10** |
| Бронирование | макс **30 минут** |
| Одновременных бронирований на клиента | **1** |
| Ночной тариф | **23:00-06:00, скидка 20%** |
| Station ID | принимает `station_id` **или** `serial_number` |

---

### 📡 Прочие каналы

**WebSocket реалтайм** (`wss://redp.charge.redpay.kg/ws/locations`):
- Подписка на изменения статусов станций и коннекторов
- Каналы: `all`, `location:<id>`, `location_stations:<id>`

**Push-уведомления** (Web Push RFC 8030 + VAPID):
- `POST /api/v1/notifications/subscribe` — подписка
- События: payment_confirmed, session_started, session_stopped, incident_alert

---

### 🧪 Тестовый сервер

**URL:** `https://redp.charge.redpay.kg`

| Объект | Количество |
|--------|------------|
| Локации | 198 (по всему Кыргызстану) |
| Станции | 419 (AC 22 кВт и DC до 360 кВт) |
| Коннекторы | 743 (Type2, CCS2, CHAdeMO) |
| Сессии зарядки | 500 (завершённых) |
| Партнёры | 5 компаний |
| Корпоративные группы | 3 |

**Производители в тестовых данных:** ABB, Huawei, Autel, Delta, Schneider, TELD, Star Charge.

---

### 💰 Валюта и единицы измерения

| Параметр | Единица |
|----------|---------|
| Деньги | **KGS** (Кыргызский сом) |
| Энергия | **кВтч** (kWh) |
| Мощность | **кВт** (kW) |
| Платежи Namba One | **тийины** (1 KGS = 100 тийинов) |

---

### 📦 Формат ответов

Все эндпоинты возвращают JSON:

```json
// Успех
{"success": true, "data": {...}}

// Ошибка
{"success": false, "error": "error_code", "message": "Текст ошибки для пользователя"}
```

**Коды ошибок:**
- `unauthorized` — не авторизован
- `csrf_failed` — неверный CSRF токен
- `not_found` — ресурс не найден
- `validation_error` — ошибка валидации входных данных
- `insufficient_balance` — недостаточно средств
- `station_offline` — станция недоступна

---

### 👥 Роли пользователей

| Роль | Доступ |
|------|--------|
| `client` | Мобильное приложение: зарядка, баланс, история |
| `operator` | Мониторинг станций региона |
| `partner` | Кабинет партнёра: свои станции, выручка |
| `admin` | Полный доступ к admin-панели |
| `superadmin` | Полный доступ + управление пользователями |

---

### ⚡ OCPP WebSocket

```
wss://redp.charge.redpay.kg/ws/{station_id}
```

Протокол: OCPP 1.6J. Поддерживаемые сообщения: BootNotification, Heartbeat, Authorize, StartTransaction, StopTransaction, MeterValues, StatusNotification, RemoteStartTransaction, RemoteStopTransaction, ChangeAvailability, GetConfiguration, ChangeConfiguration, ClearCache, UnlockConnector, Reset, SendLocalList.
"""

app = FastAPI(
    title="Red Petroleum EV — API",
    description=_API_DESCRIPTION,
    version="1.1.0",
    contact={
        "name": "Red Petroleum Dev Team",
        "email": "dev@redpetroleum.kg",
        "url": "https://redp.charge.redpay.kg",
    },
    license_info={"name": "Proprietary — Red Petroleum © 2026"},
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
    docs_url=("/docs" if settings.ENABLE_SWAGGER else None),
    redoc_url=("/redoc" if settings.ENABLE_SWAGGER else None),
)

# Подключаем аутентификацию и идемпотентность до бизнес-логики
app.add_middleware(AuthMiddleware)
app.add_middleware(IdempotencyMiddleware)

# Добавляем Security Middleware (заголовки, базовый rate limiting)
security_middleware = SecurityMiddleware()
app.middleware("http")(security_middleware)

# Добавляем Payment Audit Middleware
payment_audit_middleware = PaymentAuditMiddleware()
app.middleware("http")(payment_audit_middleware)

# Получаем CORS origins из настроек (берется из env переменной CORS_ORIGINS)
cors_origins = settings.CORS_ORIGINS.split(",") if settings.CORS_ORIGINS else []
cors_origins = [origin.strip() for origin in cors_origins if origin.strip()]  # Убираем пробелы и пустые значения

# Fail-safe: если CORS origins пусты в dev, используем localhost
if not cors_origins:
    if settings.APP_ENV == "development":
        cors_origins = ["http://localhost:3000", "http://localhost:9210"]
        logger.warning("⚠️ CORS_ORIGINS not set - using development defaults")
    else:
        raise ValueError("CORS_ORIGINS must be explicitly set in production/staging environment")

# Валидация: запрещаем wildcard в production с allow_credentials
if "*" in cors_origins:
    if settings.APP_ENV == "production":
        raise ValueError("CORS wildcard (*) not allowed in production with allow_credentials=True")
    logger.warning("⚠️ CORS wildcard (*) detected - should not be used in production")

logger.info(f"📋 CORS origins configured: {len(cors_origins)} origins")

# Явно задаем разрешенные заголовки (принцип наименьших привилегий)
allowed_headers = [
    "Authorization",
    "Content-Type",
    "X-CSRF-Token",
    "X-Client-Id",
    "X-Client-Timestamp",
    "X-Client-Signature",
    "Idempotency-Key",
    "X-Correlation-ID"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=allowed_headers,  # Явный список вместо "*"
    expose_headers=["X-Correlation-ID", "Idempotency-Key"],
    max_age=86400  # 24 часа кэш для preflight запросов
)

# Global exception handler — ensures CORS headers are sent even on 500 errors
# Without this, browser blocks 500 responses and shows "Origin not allowed"
from fastapi.responses import JSONResponse as _JSONResponse
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return _JSONResponse(
        status_code=500,
        content={"success": False, "error": "internal_error", "message": "Внутренняя ошибка сервера"}
    )

# ============================================================================
# ПОДКЛЮЧЕНИЕ API РОУТЕРОВ
# ============================================================================

# V1 API - модульная структура
app.include_router(v1_router)

# ============================================================================
# HEALTH CHECK ENDPOINT (единственный HTTP endpoint)
# ============================================================================

@app.get("/version", summary="Service version", description="Returns app version and build info for PWA update checks.", tags=["system"])
async def get_version():
    """
    Возвращает версию приложения и идентификатор сборки для фронта (обновление PWA).
    """
    try:
        git_commit = os.getenv("GIT_COMMIT", "unknown")
        build_time = os.getenv("BUILD_TIME", "unknown")
        return {"success": True, "version": "1.1.0", "git_commit": git_commit, "build_time": build_time}
    except Exception:
        return {"success": True, "version": "1.1.0"}

@app.get("/health", summary="Health check", description="Returns server health status including Redis connectivity and connected station count.", tags=["system"])
async def health_check():
    """Проверка состояния OCPP WebSocket сервера"""
    try:
        redis_status = await redis_manager.ping()
        logger.info(f"Health check - Redis: {'connected' if redis_status else 'disconnected'}")

        if not redis_status:
            raise Exception("Redis недоступен - OCPP функции не работают")

        connected_stations = await redis_manager.get_stations()
        logger.info(f"Health check - Connected stations: {len(connected_stations)}")
        
        return {
            "status": "healthy",
            "service": "RedPetroleum OCPP WebSocket Server",
            "version": "1.1.0",
            "redis": "connected",
            "connected_stations": len(connected_stations),
            "endpoints": ["ws://{host}/ws/{station_id}", "ws://{host}/ocpp/{station_id}", "GET /health"],
            "note": "Все системы работают"
        }
    except Exception as e:
        logger.error(f"❌ Health check failed: {e}", exc_info=True)
        return {
            "status": "unhealthy",
            "service": "RedPetroleum OCPP WebSocket Server",
            "version": "1.1.0",
            "error": str(e),
            "redis": "disconnected",
            "note": "КРИТИЧЕСКАЯ ОШИБКА: Redis недоступен - OCPP и зарядка не работают!"
        }

@app.get("/health-force", summary="Force health check", description="Creates a new Redis connection and tests read/write operations.", tags=["system"])
async def health_check_force():
    """Принудительная диагностика с пересозданием Redis подключения"""
    from ocpp_ws_server.redis_manager import RedisOcppManager

    try:
        # Принудительно создаем новый Redis manager
        logger.info("🔄 Force check - Creating new Redis connection")

        # Создаем новый экземпляр для тестирования
        test_redis = RedisOcppManager()

        # Пытаемся подключиться
        ping_result = await test_redis.ping()
        logger.info(f"🔄 Force check - Redis ping: {ping_result}")

        if ping_result:
            # Тестируем операции
            await test_redis.redis.set("health_test", "ok", ex=10)
            test_value = await test_redis.redis.get("health_test")
            await test_redis.redis.delete("health_test")

            logger.info(f"🔄 Force check - Read/write test: {'OK' if test_value else 'FAILED'}")

            return {
                "status": "healthy",
                "service": "RedPetroleum OCPP WebSocket Server (FORCE CHECK)",
                "version": "1.1.0",
                "redis": "connected",
                "redis_configured": True,
                "ping_result": ping_result,
                "rw_test": test_value.decode() if test_value else None,
                "note": "Принудительная проверка прошла успешно"
            }
        else:
            raise Exception("Redis ping failed")

    except Exception as e:
        logger.error(f"❌ Force check failed: {e}", exc_info=True)
        return {
            "status": "unhealthy",
            "service": "RedPetroleum OCPP WebSocket Server (FORCE CHECK)",
            "version": "1.1.0",
            "error": str(e),
            "redis": "disconnected",
            "redis_configured": bool(settings.REDIS_URL),
            "note": f"Принудительная проверка не удалась: {e}"
        }

# Readiness endpoint (зависимости готовы)
@app.get("/readyz", summary="Readiness probe", description="Returns ready status when all dependencies (Redis) are available.", tags=["system"])
async def ready_check():
    try:
        redis_status = await redis_manager.ping()
        if not redis_status:
            raise Exception("Redis недоступен")
        return {"status": "ready"}
    except Exception as e:
        return {"status": "not_ready", "error": str(e)}

# Диагностика Redis Pub/Sub (по рекомендации Voltera)
# Защищён admin API-ключом (SUPABASE_SERVICE_ROLE_KEY) для предотвращения утечки топологии
@app.get("/debug/redis-pubsub", summary="Диагностика Redis Pub/Sub", include_in_schema=False)
async def debug_redis_pubsub(station_id: str = "EVP-001-01", api_key: str = ""):
    """
    Диагностический endpoint для отладки проблемы с 0 подписчиков.
    Показывает реальное состояние Redis pub/sub vs Python словарь.
    Требует api_key = SUPABASE_SERVICE_ROLE_KEY.
    """
    from app.core.config import settings
    if not api_key or api_key != settings.SUPABASE_SERVICE_ROLE_KEY:
        return {"error": "unauthorized", "message": "Valid api_key required"}

    import os

    results = {
        "pid": os.getpid(),
        "station_id": station_id,
    }

    try:
        # 1. Проверяем подключение
        results["ping"] = await redis_manager.ping()

        # 2. Получаем ВСЕ активные каналы pub/sub в Redis
        channels = await redis_manager.redis.pubsub_channels("ocpp:cmd:*")
        results["redis_pubsub_channels"] = [ch.decode() if isinstance(ch, bytes) else ch for ch in channels]

        # 3. Получаем количество подписчиков на конкретный канал
        channel_name = f"ocpp:cmd:{station_id}"
        numsub = await redis_manager.redis.pubsub_numsub(channel_name)
        results["numsub"] = dict(numsub) if numsub else {}

        # 4. Проверяем Python словарь (память процесса)
        results["python_active_pubsubs"] = list(getattr(redis_manager, '_active_pubsubs', {}).keys())

        # 5. Проверяем зарегистрированные станции (TTL ключи)
        results["registered_stations"] = list(await redis_manager.get_stations())

        # 6. Проверяем is_station_online
        results["is_station_online"] = await redis_manager.is_station_online(station_id)

    except Exception as e:
        results["error"] = str(e)
        import traceback
        results["traceback"] = traceback.format_exc()

    return results

# ============================================================================
# OCPP WEBSOCKET RATE LIMITING
# ============================================================================

import time as _time
from collections import defaultdict

# Reconnection rate limit: max 10 connections per station per minute
_ocpp_station_connects: dict[str, list[float]] = defaultdict(list)
# IP rate limit: max 50 concurrent stations per IP
_ocpp_ip_connections: dict[str, int] = defaultdict(int)
# Стресс-тест режим: STRESS_TEST_MODE=1 снимает rate limits для load simulator
_STRESS_TEST_MODE = os.getenv("STRESS_TEST_MODE", "0") == "1"
_OCPP_MAX_RECONNECTS_PER_MIN = 100000 if _STRESS_TEST_MODE else 10
_OCPP_MAX_STATIONS_PER_IP = 100000 if _STRESS_TEST_MODE else 50
if _STRESS_TEST_MODE:
    logger.warning("⚠️ STRESS_TEST_MODE=1 — OCPP rate limits ОТКЛЮЧЕНЫ!")

# ============================================================================
# OCPP WEBSOCKET ENDPOINT (основная функциональность)
# ============================================================================

@app.websocket("/ws/{station_id}")
@app.websocket("/ocpp/{station_id}")
@app.websocket("/ws/{station_id}/")
@app.websocket("/ocpp/{station_id}/")
@app.websocket("/ws/{station_id}/{rest_path:path}")
@app.websocket("/ocpp/{station_id}/{rest_path:path}")
async def websocket_endpoint(websocket: WebSocket, station_id: str, rest_path: str = ""):
    """
    WebSocket endpoint для подключения зарядных станций по протоколу OCPP 1.6 и OCPP 2.0.1
    """
    client_info = getattr(websocket, 'client', None)
    client_ip = client_info.host if client_info else 'unknown'

    # Rate limit: max reconnections per station per minute
    now = _time.time()
    connects = _ocpp_station_connects[station_id]
    # Cleanup old entries (>60s)
    _ocpp_station_connects[station_id] = [t for t in connects if now - t < 60]
    if len(_ocpp_station_connects[station_id]) >= _OCPP_MAX_RECONNECTS_PER_MIN:
        logger.warning(f"OCPP rate limit: station {station_id} exceeded {_OCPP_MAX_RECONNECTS_PER_MIN} connects/min from {client_ip}")
        await websocket.close(code=1013, reason="Too many reconnections")
        return
    _ocpp_station_connects[station_id].append(now)

    # Rate limit: max concurrent stations per IP
    if _ocpp_ip_connections[client_ip] >= _OCPP_MAX_STATIONS_PER_IP:
        logger.warning(f"OCPP rate limit: IP {client_ip} exceeded {_OCPP_MAX_STATIONS_PER_IP} concurrent stations")
        await websocket.close(code=1013, reason="Too many connections from IP")
        return
    _ocpp_ip_connections[client_ip] += 1

    handler = OCPPWebSocketHandler(station_id, websocket)
    try:
        await handler.handle_connection()
    except WebSocketDisconnect:
        logger.info(f"Station {station_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error for station {station_id}: {e}", exc_info=True)
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    finally:
        # Release IP connection slot
        _ocpp_ip_connections[client_ip] = max(0, _ocpp_ip_connections[client_ip] - 1)

# ============================================================================
# ROOT ENDPOINT
# ============================================================================

# ============================================================================
# ROOT ENDPOINT
# ============================================================================

@app.get("/", summary="Server info", description="Returns service name, version, supported protocols, and WebSocket URLs.", tags=["system"])
async def root():
    """Корневой endpoint с информацией о сервере"""
    return {
        "service": "RedPetroleum OCPP WebSocket Server",
        "description": "OCPP WebSocket сервер для зарядных станций (dual-protocol)",
        "websocket_urls": ["ws://{host}/ws/{station_id}", "ws://{host}/ocpp/{station_id}"],
        "health_check": "GET /health",
        "version": "1.1.0",
        "protocols": ["OCPP 1.6 JSON", "OCPP 2.0.1"],
        "note": "Protocol auto-detection via Sec-WebSocket-Protocol header"
    }

# ============================================================================
# CUSTOM OPENAPI — добавляем Security Scheme (cookie-based JWT)
# ============================================================================

from fastapi.openapi.utils import get_openapi

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        contact=app.contact,
        license_info=app.license_info,
        tags=app.openapi_tags,
        routes=app.routes,
    )

    # Security Schemes — cookie JWT
    schema.setdefault("components", {})
    schema["components"]["securitySchemes"] = {
        "cookieAuth": {
            "type": "apiKey",
            "in": "cookie",
            "name": "evp_access",
            "description": "JWT access token. Получить через `POST /api/v1/auth/otp/verify` или `POST /api/v1/auth/sms/verify`. Действует 1 час, обновляется через `POST /api/v1/auth/refresh`.",
        },
        "csrfHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-CSRF-Token",
            "description": "CSRF токен для мутирующих запросов (POST/PUT/PATCH/DELETE). Получить через `GET /api/v1/auth/csrf`.",
        },
    }

    # Применяем cookieAuth ко всем эндпоинтам кроме публичных
    PUBLIC_PREFIXES = (
        "/api/v1/auth/",
        "/api/v1/locations",
        "/api/v1/guest/",
        "/health",
        "/openapi",
        "/docs",
        "/redoc",
        "/scalar",
    )
    for path, path_item in schema.get("paths", {}).items():
        is_public = any(path.startswith(p) for p in PUBLIC_PREFIXES)
        if not is_public:
            for method, operation in path_item.items():
                if method in ("get", "post", "put", "patch", "delete"):
                    operation.setdefault("security", [{"cookieAuth": [], "csrfHeader": []}])

    app.openapi_schema = schema
    return schema

app.openapi = custom_openapi  # type: ignore[method-assign]


# ============================================================================
# SCALAR API REFERENCE (interactive docs with built-in HTTP client)
# ============================================================================

if settings.ENABLE_SWAGGER:
    @app.get("/scalar", include_in_schema=False)
    async def scalar_docs():
        return HTMLResponse("""<!DOCTYPE html>
<html>
<head>
    <title>Red Petroleum API — Scalar</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>body { margin: 0; padding: 0; }</style>
</head>
<body>
    <script id="api-reference" data-url="/openapi.json"></script>
    <script>
        document.getElementById('api-reference').dataset.configuration = JSON.stringify({
            theme: "deepSpace",
            layout: "sidebar",
            showSidebar: true,
            searchHotKey: "k",
            defaultHttpClient: { targetKey: "shell", clientKey: "curl" },
            metaData: { title: "Red Petroleum EV \u2014 API" }
        });
    </script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.25.72"></script>
</body>
</html>
""")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=9210,
        log_level="info"
    )

