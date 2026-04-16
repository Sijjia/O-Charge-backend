"""
OCPP WebSocket Handler - Dual-protocol support (1.6 + 2.0.1)
Поддерживаемые сообщения: Tier 1, 2, 3 (все стандартные OCPP 1.6)
OCPP 2.0.1 handlers in ws_handler_v201.py
"""

import asyncio
import logging
import os
import time
from collections import deque
from datetime import datetime
from typing import Dict, Any, Optional

from fastapi import WebSocket, WebSocketDisconnect
from ocpp.v16 import ChargePoint as CP
from ocpp.routing import on
from ocpp.v16 import call_result, call
from ocpp.v201 import call as call_201
from ocpp.v16.enums import (
    RegistrationStatus, AuthorizationStatus, ConfigurationStatus,
    ResetStatus, ResetType, UnlockStatus, AvailabilityStatus,
    AvailabilityType, ClearCacheStatus, UpdateStatus,
    DiagnosticsStatus, FirmwareStatus, TriggerMessageStatus,
    MessageTrigger, UpdateType
)

from .redis_manager import redis_manager
from app.db.session import get_db
from app.crud.ocpp_service import (
    OCPPStationService,
    OCPPTransactionService,
    OCPPMeterService,
    OCPPAuthorizationService,
    OCPPConfigurationService
)
from app.db.models.ocpp import OCPPTransaction
from sqlalchemy import text
from decimal import Decimal
from app.core.station_auth import station_auth
from app.services.push_service import push_service
from app.services.realtime_service import RealtimeService
from app.services.pricing_service import PricingService
from app.services.log_service import OCPPLogService
from app.services.webhook_service import webhook_service

logger = logging.getLogger(__name__)

# Активные сессии для мониторинга лимитов
active_sessions: Dict[str, Dict[str, Any]] = {}

# Активные WebSocket handler'ы (для graceful shutdown)
_active_handlers: Dict[str, 'OCPPWebSocketHandler'] = {}

# Флаг graceful shutdown
_shutting_down = False


async def graceful_shutdown_all_connections():
    """Закрыть все WebSocket соединения при shutdown.

    Отправляет close frame каждой станции чтобы она немедленно
    начала переподключение вместо ожидания TCP timeout (до 30+ секунд).
    Также сохраняет active_sessions в Redis.
    """
    global _shutting_down
    _shutting_down = True

    logger.info(f"🛑 Graceful shutdown: закрываем {len(_active_handlers)} WebSocket соединений...")

    # Сохраняем активные сессии в Redis
    if active_sessions:
        await redis_manager.save_active_sessions(active_sessions)

    # Устанавливаем флаг перезапуска
    await redis_manager.set_shutdown_flag()

    # Закрываем все WebSocket соединения параллельно
    close_tasks = []
    for station_id, handler in list(_active_handlers.items()):
        close_tasks.append(_close_handler_safely(station_id, handler))

    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)

    logger.info(f"✅ Graceful shutdown: все соединения закрыты")


async def _close_handler_safely(station_id: str, handler: 'OCPPWebSocketHandler'):
    """Безопасно закрыть одно WebSocket соединение."""
    try:
        await asyncio.wait_for(
            handler.websocket.close(code=1012, reason="Service restart"),
            timeout=5.0
        )
        logger.info(f"  ✅ Закрыт WebSocket для {station_id}")
    except Exception as e:
        logger.warning(f"  ⚠️ Ошибка закрытия WebSocket для {station_id}: {e}")


async def restore_sessions_on_startup():
    """Восстановить active_sessions из Redis после рестарта."""
    global active_sessions
    restored = await redis_manager.restore_active_sessions()
    if restored:
        active_sessions.update(restored)
        logger.info(f"♻️ Восстановлено {len(restored)} активных сессий")
    await redis_manager.clear_shutdown_flag()


def _classify_error(e: Exception) -> str:
    """Classify exception by error category for structured logging."""
    err_type = type(e).__name__
    err_module = type(e).__module__ or ""
    if "redis" in err_module.lower() or "Redis" in err_type:
        return "redis_error"
    if any(k in err_type for k in ("OperationalError", "IntegrityError", "ProgrammingError", "DatabaseError", "SQLAlchemy")):
        return "db_error"
    if any(k in err_type for k in ("ValueError", "ValidationError", "TypeError", "KeyError")):
        return "validation_error"
    if any(k in err_type for k in ("Decimal", "Overflow", "ArithmeticError")):
        return "financial_error"
    if any(k in err_type for k in ("ConnectionError", "TimeoutError", "WebSocket", "ConnectionClosed")):
        return "connection_error"
    return "unknown_error"

class OCPPChargePoint(CP):
    """
    Расширенный OCPP 1.6 ChargePoint с поддержкой всех стандартных сообщений
    """
    
    def __init__(self, id: str, connection):
        super().__init__(id, connection)
        self.logger = logging.getLogger(f"OCPP.{id}")
        
    # ============================================================================
    # TIER 1: КРИТИЧЕСКИ ВАЖНЫЕ (обязательные для сертификации)
    # ============================================================================
    
    @on('BootNotification')
    def on_boot_notification(self, charge_point_model, charge_point_vendor, **kwargs):
        """Регистрация станции при запуске"""
        self.logger.info(f"BootNotification: {charge_point_model}, {charge_point_vendor}")
        
        try:
            # 🚀 БЫСТРЫЙ ОТВЕТ: Минимальная обработка для избежания таймаутов
            self.logger.info(f"✅ Станция {self.id} успешно зарегистрирована")
            
            # Выполняем DB операции асинхронно в background
            firmware_version = kwargs.get('firmware_version')
            asyncio.create_task(self._handle_boot_notification_background(firmware_version))
            
            return call_result.BootNotification(
                current_time=datetime.utcnow().isoformat() + 'Z',
                interval=300,
                status=RegistrationStatus.accepted
            )
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in BootNotification [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.BootNotification(
                current_time=datetime.utcnow().isoformat() + 'Z',
                interval=300,
                status=RegistrationStatus.rejected
            )
    
    async def _handle_boot_notification_background(self, firmware_version: str = None):
        """Background обработка BootNotification для избежания блокировок"""
        try:
            self.logger.info(f"🔄 Background обработка BootNotification для {self.id}")

            # OCPP DB log
            try:
                with next(get_db()) as log_db:
                    OCPPLogService.log_event(
                        db=log_db,
                        station_id=self.id,
                        event_type="BootNotification",
                        direction="inbound",
                        request_payload={"firmware_version": firmware_version},
                        severity="info",
                    )
            except Exception as e:
                self.logger.warning(f"Failed to log OCPP event (BootNotification): {e}", exc_info=True)

            with next(get_db()) as db:
                # Сохраняем информацию о станции
                OCPPStationService.mark_boot_notification_sent(
                    db, self.id, firmware_version
                )

                # Базовая конфигурация
                OCPPConfigurationService.set_configuration(
                    db, self.id, "HeartbeatInterval", "300", readonly=True
                )
                OCPPConfigurationService.set_configuration(
                    db, self.id, "MeterValueSampleInterval", "60", readonly=True
                )

                # 🚫 НЕ АВТОЗАПУСК: Завершаем зависшие сессии с возвратом средств
                # При перезагрузке станции все pending/started сессии без OCPP транзакции
                # должны быть завершены с ошибкой и возвратом средств
                hanging_sessions_query = text("""
                    SELECT id, user_id, amount
                    FROM charging_sessions
                    WHERE station_id = :station_id
                    AND status = 'started'
                    AND transaction_id IS NULL
                """)

                hanging_sessions = db.execute(hanging_sessions_query, {"station_id": self.id}).fetchall()

                for session in hanging_sessions:
                    session_id, user_id, reserved_amount = session

                    self.logger.warning(
                        f"⚠️ Найдена зависшая сессия {session_id} при BootNotification. "
                        f"Станция перезагрузилась - завершаем с возвратом средств."
                    )

                    # Завершаем сессию с ошибкой
                    update_session_query = text("""
                        UPDATE charging_sessions
                        SET status = 'error',
                            stop_time = NOW(),
                            energy = 0,
                            amount = 0,
                            error_details = 'Station rebooted before transaction started'
                        WHERE id = :session_id
                    """)
                    db.execute(update_session_query, {"session_id": session_id})

                    # Возвращаем зарезервированные средства клиенту
                    if reserved_amount and float(reserved_amount) > 0:
                        refund_query = text("""
                            UPDATE clients
                            SET balance = balance + :refund_amount
                            WHERE id = :user_id
                        """)
                        db.execute(refund_query, {
                            "refund_amount": float(reserved_amount),
                            "user_id": user_id
                        })

                        # Создаем запись о возврате
                        refund_transaction_query = text("""
                            INSERT INTO payment_transactions_odengi
                            (client_id, transaction_type, amount, description, charging_session_id)
                            VALUES (:client_id, 'charge_refund', :amount, :description, :session_id)
                        """)
                        db.execute(refund_transaction_query, {
                            "client_id": user_id,
                            "amount": float(reserved_amount),
                            "description": f"Возврат средств: станция перезагрузилась (сессия {session_id})",
                            "session_id": session_id
                        })

                        self.logger.info(
                            f"💰 Возврат {reserved_amount} сом клиенту {user_id} "
                            f"(сессия {session_id} завершена из-за перезагрузки станции)"
                        )

                    # Освобождаем коннекторы
                    release_connectors_query = text("""
                        UPDATE connectors
                        SET status = 'available', error_code = 'NoError', last_status_update = NOW()
                        WHERE station_id = :station_id AND status = 'occupied'
                    """)
                    db.execute(release_connectors_query, {"station_id": self.id})

                if hanging_sessions:
                    self.logger.info(
                        f"✅ Завершено {len(hanging_sessions)} зависших сессий "
                        f"при BootNotification станции {self.id}"
                    )

                db.commit()

                # Broadcast что станция online для PWA клиентов
                asyncio.create_task(
                    RealtimeService.broadcast_station_update(db, self.id)
                )
                self.logger.info(f"📡 Broadcast: станция {self.id} online")

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in BootNotification background [{err_cat}]: station={self.id}: {e}", exc_info=True)

    @on('Heartbeat')
    def on_heartbeat(self, **kwargs):
        """Периодические сигналы жизни"""
        self.logger.debug(f"Heartbeat from {self.id}")

        try:
            # Продлеваем TTL станции в Redis
            asyncio.create_task(redis_manager.refresh_station_ttl(self.id))

            with next(get_db()) as db:
                OCPPStationService.update_heartbeat(db, self.id)

                # Инвалидируем кэш локации при heartbeat (станция стала online)
                location_query = text("""
                    SELECT location_id FROM stations
                    WHERE id = :station_id
                """)
                location_result = db.execute(location_query, {"station_id": self.id}).fetchone()

                if location_result:
                    location_id = location_result[0]
                    from app.services.location_status_service import LocationStatusService
                    asyncio.create_task(LocationStatusService.invalidate_cache(location_id))

            return call_result.Heartbeat(
                current_time=datetime.utcnow().isoformat() + 'Z'
            )

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in Heartbeat [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.Heartbeat(
                current_time=datetime.utcnow().isoformat() + 'Z'
            )

    @on('StatusNotification')
    def on_status_notification(self, connector_id, error_code, status, **kwargs):
        """Изменения статуса коннекторов"""
        # Детальное логирование StatusNotification
        info = kwargs.get('info')
        vendor_id = kwargs.get('vendor_id') 
        vendor_error_code = kwargs.get('vendor_error_code')
        timestamp = kwargs.get('timestamp')
        
        self.logger.info(f"StatusNotification: connector={connector_id}, status={status}, error={error_code}")
        self.logger.debug(f"StatusNotification полные данные: {kwargs}")
        
        # Специальное логирование для ошибок
        if error_code != "NoError":
            self.logger.warning(f"🚨 ОШИБКА КОННЕКТОРА {connector_id}: {error_code}")
            if info:
                self.logger.warning(f"   Дополнительная информация: {info}")
            if vendor_error_code:
                self.logger.warning(f"   Vendor Error Code: {vendor_error_code}")
            if vendor_id:
                self.logger.warning(f"   Vendor ID: {vendor_id}")

            # Автоматическая диагностика при ошибках
            asyncio.create_task(self._perform_error_diagnostics(connector_id, error_code))

            # Push notification клиенту об ошибке зарядки (graceful degradation)
            asyncio.create_task(self._send_charging_error_notification(
                connector_id=connector_id,
                error_code=error_code,
                info=info,
                vendor_error_code=vendor_error_code
            ))

            # Webhook: charging error
            webhook_service.dispatch_event("charging.error", {
                "station_id": self.id,
                "connector_id": connector_id,
                "error_code": error_code,
                "info": info,
                "vendor_error_code": vendor_error_code,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })
        
        # Логирование изменений статуса
        if status in ["Faulted", "Unavailable"]:
            self.logger.error(f"🔴 КОННЕКТОР {connector_id} НЕДОСТУПЕН: {status} - {error_code}")
        elif status in ["Available", "Occupied"]:
            self.logger.info(f"🟢 Коннектор {connector_id}: {status}")
        else:
            self.logger.debug(f"Коннектор {connector_id}: {status}")
        
        # OCPP DB log
        try:
            with next(get_db()) as log_db:
                _sev = "error" if status in ("Faulted", "Unavailable") or error_code != "NoError" else "info"
                OCPPLogService.log_event(
                    db=log_db,
                    station_id=self.id,
                    event_type="StatusNotification",
                    direction="inbound",
                    connector_id=connector_id,
                    request_payload={"status": status, "error_code": error_code, "info": info},
                    severity=_sev,
                    error_message=error_code if error_code != "NoError" else None,
                )
        except Exception as e:
            self.logger.warning(f"Failed to log OCPP event (StatusNotification): {e}", exc_info=True)

        try:
            with next(get_db()) as db:

                # Обновляем статус станции (старая логика для совместимости)
                station_status = OCPPStationService.update_station_status(
                    db, self.id, status, error_code, info, vendor_id, vendor_error_code
                )
                
                # Обновляем статус коннектора в JSON поле (старая логика)
                connector_status = station_status.connector_status or []
                
                # Находим или создаем статус для коннектора
                connector_found = False
                for i, conn in enumerate(connector_status):
                    if conn.get('connector_id') == connector_id:
                        connector_status[i] = {
                            'connector_id': connector_id,
                            'status': status,
                            'error_code': error_code,
                            'info': info,
                            'timestamp': datetime.utcnow().isoformat()
                        }
                        connector_found = True
                        break
                
                if not connector_found:
                    connector_status.append({
                        'connector_id': connector_id,
                        'status': status,
                        'error_code': error_code,
                        'info': info,
                        'timestamp': datetime.utcnow().isoformat()
                    })
                
                station_status.connector_status = connector_status
                
                # 🆕 НОВАЯ ЛОГИКА: Обновляем таблицу connectors
                # Конвертируем OCPP статус в наш формат
                connector_status_mapping = {
                    'Available': 'Available',
                    'Preparing': 'Occupied', 
                    'Charging': 'Occupied',
                    'SuspendedEVSE': 'Occupied',
                    'SuspendedEV': 'Occupied',
                    'Finishing': 'Occupied',
                    'Reserved': 'Occupied',
                    'Unavailable': 'Unavailable',
                    'Faulted': 'Faulted'
                }
                
                new_status = connector_status_mapping.get(status, 'Unavailable')
                
                # Upsert запись в таблице connectors (INSERT если нет, UPDATE если есть)
                upsert_query = text("""
                    INSERT INTO connectors (station_id, connector_number, status, error_code, last_status_update)
                    VALUES (:station_id, :connector_id, :status, :error_code, NOW())
                    ON CONFLICT (station_id, connector_number)
                    DO UPDATE SET status = :status, error_code = :error_code, last_status_update = NOW()
                """)
                db.execute(upsert_query, {
                    "status": new_status.lower(),
                    "error_code": error_code,
                    "station_id": self.id,
                    "connector_id": connector_id
                })
                
                # Инвалидируем кэш локаций при изменении статуса
                # Получаем location_id для станции
                location_query = text("""
                    SELECT location_id FROM stations 
                    WHERE id = :station_id
                """)
                location_result = db.execute(location_query, {"station_id": self.id}).fetchone()
                
                if location_result:
                    location_id = location_result[0]
                    # Импортируем и вызываем инвалидацию кэша асинхронно
                    from app.services.location_status_service import LocationStatusService
                    asyncio.create_task(LocationStatusService.invalidate_cache(location_id))

                    # Broadcast обновления через WebSocket для PWA клиентов
                    asyncio.create_task(
                        RealtimeService.broadcast_connector_update(db, self.id, connector_id)
                    )
                    self.logger.debug(f"📡 Broadcast обновления коннектора {self.id}:{connector_id}")

                db.commit()

                # Webhook: connector status changed (after commit)
                webhook_service.dispatch_event("connector.status_changed", {
                    "station_id": self.id,
                    "connector_id": connector_id,
                    "status": status,
                    "error_code": error_code,
                    "info": kwargs.get("info"),
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })

            return call_result.StatusNotification()

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in StatusNotification [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.StatusNotification()

    @on('Authorize')
    def on_authorize(self, id_tag, **kwargs):
        """Авторизация RFID карт"""
        self.logger.info(f"Authorize request for id_tag: {id_tag}")

        try:
            with next(get_db()) as db:
                # Проверяем авторизацию через сервис
                auth_result = OCPPAuthorizationService.authorize_id_tag(db, id_tag)

            self.logger.info(f"Authorization result for {id_tag}: {auth_result['status']}")

            # OCPP DB log
            try:
                with next(get_db()) as log_db:
                    OCPPLogService.log_event(
                        db=log_db,
                        station_id=self.id,
                        event_type="Authorize",
                        direction="inbound",
                        request_payload={"id_tag": id_tag},
                        response_payload={"status": auth_result["status"]},
                        severity="info",
                    )
            except Exception as e:
                self.logger.warning(f"Failed to log OCPP event (Authorize): {e}", exc_info=True)

            return call_result.Authorize(id_tag_info=auth_result)
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in Authorize [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.Authorize(
                id_tag_info={"status": AuthorizationStatus.invalid}
            )

    @on('StartTransaction')
    def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **kwargs):
        """Начало сеанса зарядки"""
        self.logger.info(f"StartTransaction: connector={connector_id}, id_tag={id_tag}, meter_start={meter_start}")

        # OCPP DB log
        try:
            with next(get_db()) as log_db:
                OCPPLogService.log_event(
                    db=log_db,
                    station_id=self.id,
                    event_type="StartTransaction",
                    direction="inbound",
                    connector_id=connector_id,
                    request_payload={"connector_id": connector_id, "id_tag": id_tag, "meter_start": meter_start},
                    severity="info",
                )
        except Exception as e:
            self.logger.warning(f"Failed to log OCPP event (StartTransaction): {e}", exc_info=True)

        try:
            with next(get_db()) as db:
                # Проверяем авторизацию
                auth_result = OCPPAuthorizationService.authorize_id_tag(db, id_tag)
                if auth_result["status"] != "Accepted":
                    self.logger.warning(f"Unauthorized id_tag: {id_tag}")
                    return call_result.StartTransaction(
                        transaction_id=0,
                        id_tag_info=auth_result
                    )

                # Генерируем уникальный transaction_id (timestamp_ms + random suffix)
                import random as _random
                transaction_id = int(datetime.utcnow().timestamp() * 1000) % (2**31) + _random.randint(0, 999)

                # 🆕 ПРОВЕРКА ДУБЛИКАТА: (station_id, transaction_id) должен быть уникальным
                duplicate_check = text("""
                    SELECT id, charging_session_id FROM ocpp_transactions
                    WHERE station_id = :station_id AND transaction_id = :transaction_id
                    LIMIT 1
                """)
                duplicate_result = db.execute(duplicate_check, {
                    "station_id": self.id,
                    "transaction_id": transaction_id
                }).fetchone()

                if duplicate_result:
                    self.logger.warning(
                        f"⚠️ Дубликат transaction_id {transaction_id} для станции {self.id}. "
                        f"Возвращаем существующую транзакцию."
                    )
                    return call_result.StartTransaction(
                        transaction_id=transaction_id,
                        id_tag_info={"status": AuthorizationStatus.accepted}
                    )
                
                # === ПОИСК session_id (архитектура как Voltera) ===
                # Приоритеты:
                # 1. Pending session в Redis (по station_id:connector_id)
                # 2. По телефону через БД (id_tag = телефон клиента)
                # 3. Fallback через ocpp_authorization
                charging_session_id = None
                client_id = None

                # === МЕТОД 1: Поиск через pending session в Redis (приоритет) ===
                pending_key = f"pending:{self.id}:{connector_id}"
                try:
                    from ocpp_ws_server.redis_manager import redis_manager

                    # Используем СИНХРОННЫЙ Redis клиент (OCPP handlers синхронные!)
                    pending_session = redis_manager.get_sync(pending_key)

                    if pending_session:
                        charging_session_id = pending_session
                        self.logger.info(f"✅ НАЙДЕН pending session: {pending_key} -> {charging_session_id}")

                        # Удаляем pending (использован)
                        redis_manager.delete_sync(pending_key)

                        # Получаем client_id из сессии
                        session_query = text("""
                            SELECT user_id FROM charging_sessions WHERE id = :session_id
                        """)
                        session_row = db.execute(session_query, {"session_id": charging_session_id}).fetchone()
                        if session_row:
                            client_id = session_row[0]

                        # Обновляем сессию с transaction_id
                        db.execute(text("""
                            UPDATE charging_sessions SET transaction_id = :tx WHERE id = :sid
                        """), {"tx": str(transaction_id), "sid": charging_session_id})
                        self.logger.info(f"✅ Mobile сессия обновлена: transaction_id={transaction_id}")
                except Exception as redis_err:
                    self.logger.warning(f"⚠️ Ошибка Redis pending: {redis_err}")

                # === МЕТОД 2: Поиск по телефону через БД (как Voltera) ===
                # id_tag теперь = телефон клиента (постоянный идентификатор)
                if not charging_session_id:
                    self.logger.info(f"📱 Поиск сессии по телефону (id_tag): {id_tag}")

                    # Ищем клиента по телефону (id_tag = телефон без +)
                    phone_query = text("""
                        SELECT c.id as client_id, cs.id as session_id
                        FROM clients c
                        JOIN charging_sessions cs ON cs.user_id = c.id
                        WHERE REPLACE(REPLACE(c.phone, '+', ''), ' ', '') = :id_tag
                        AND cs.status = 'started'
                        AND cs.station_id = :station_id
                        ORDER BY cs.start_time DESC
                        LIMIT 1
                    """)
                    phone_result = db.execute(phone_query, {
                        "id_tag": id_tag,
                        "station_id": self.id
                    }).fetchone()

                    if phone_result:
                        client_id = phone_result[0]
                        charging_session_id = phone_result[1]
                        self.logger.info(f"✅ НАЙДЕНА сессия по телефону: client={client_id}, session={charging_session_id}")

                        # Обновляем сессию с transaction_id
                        db.execute(text("""
                            UPDATE charging_sessions SET transaction_id = :tx WHERE id = :sid
                        """), {"tx": str(transaction_id), "sid": charging_session_id})

                # === МЕТОД 3: Fallback через ocpp_authorization ===
                if not charging_session_id:
                    self.logger.info(f"📱 Fallback: ищем через ocpp_authorization для id_tag: {id_tag}")
                    auth_query = text("""
                        SELECT client_id FROM ocpp_authorization
                        WHERE id_tag = :id_tag AND client_id IS NOT NULL
                        LIMIT 1
                    """)
                    auth_result = db.execute(auth_query, {"id_tag": id_tag})
                    auth_row = auth_result.fetchone()

                    if auth_row:
                        client_id = auth_row[0]
                        # Ищем активную сессию для клиента на этой станции
                        find_session_query = text("""
                            SELECT id FROM charging_sessions
                            WHERE user_id = :client_id AND status = 'started' AND station_id = :station_id
                            ORDER BY start_time DESC LIMIT 1
                        """)
                        session_result = db.execute(find_session_query, {
                            "client_id": client_id,
                            "station_id": self.id
                        })
                        session_row = session_result.fetchone()
                        if session_row:
                            charging_session_id = session_row[0]
                            db.execute(text("""
                                UPDATE charging_sessions SET transaction_id = :tx WHERE id = :sid
                            """), {"tx": str(transaction_id), "sid": charging_session_id})
                            self.logger.info(f"✅ Найдена сессия через fallback: {charging_session_id}")
                    else:
                        self.logger.warning(f"⚠️ Клиент не найден для id_tag: {id_tag}")
                
                # Создаем OCPP транзакцию с связкой
                transaction = OCPPTransactionService.start_transaction(
                    db, self.id, transaction_id, connector_id, id_tag,
                    float(meter_start), datetime.fromisoformat(timestamp.replace('Z', '')),
                    charging_session_id  # Передаем charging_session_id
                )
                
                self.logger.info(f"✅ OCPP транзакция создана: {transaction_id} ↔ {charging_session_id}")
                
                # 🔍 ЗАГРУЖАЕМ ЛИМИТЫ ИЗ БАЗЫ ДАННЫХ для текущей сессии
                session_limits_query = text("""
                    SELECT limit_type, limit_value 
                    FROM charging_sessions 
                    WHERE id = :session_id
                """)
                limits_result = db.execute(session_limits_query, {"session_id": charging_session_id}).fetchone()
                
                session_limit_type = None
                session_limit_value = None
                if limits_result:
                    session_limit_type = limits_result[0]
                    session_limit_value = float(limits_result[1]) if limits_result[1] else None
                
                # Сохраняем в активные сессии с лимитами из базы данных
                # Ключ: station_id:connector_id — поддержка multi-connector станций
                session_key = f"{self.id}:{connector_id}"
                active_sessions[session_key] = {
                    'transaction_id': transaction_id,
                    'charging_session_id': charging_session_id,
                    'meter_start': meter_start,
                    'energy_delivered': 0.0,
                    'connector_id': connector_id,
                    'id_tag': id_tag,
                    'client_id': client_id,
                    # ✅ ПРАВИЛЬНЫЕ ЛИМИТЫ из базы данных
                    'limit_type': session_limit_type,
                    'limit_value': session_limit_value
                }
                # Обратная совместимость: также пишем по station_id (одиночный коннектор)
                active_sessions[self.id] = active_sessions[session_key]
                
                self.logger.info(f"📋 Загружены лимиты: {session_limit_type} = {session_limit_value} для сессии {charging_session_id}")
                
                # 🆕 АВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ: Коннектор становится занят
                update_query = text("""
                    UPDATE connectors 
                    SET status = 'occupied', last_status_update = NOW()
                    WHERE station_id = :station_id AND connector_number = :connector_id
                """)
                db.execute(update_query, {"station_id": self.id, "connector_id": connector_id})
                db.commit()

                # Webhook: charging started
                webhook_service.dispatch_event("charging.started", {
                    "station_id": self.id,
                    "connector_id": connector_id,
                    "transaction_id": transaction_id,
                    "session_id": str(charging_session_id) if charging_session_id else None,
                    "id_tag": id_tag,
                    "meter_start": meter_start,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })

            self.logger.info(f"Transaction started: {transaction_id}, connector {connector_id} marked as Occupied")
            return call_result.StartTransaction(
                transaction_id=transaction_id,
                id_tag_info={"status": AuthorizationStatus.accepted}
            )
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in StartTransaction [{err_cat}]: station={self.id}, connector={connector_id}, id_tag={id_tag}: {e}", exc_info=True)
            try:
                with next(get_db()) as err_db:
                    OCPPLogService.log_event(
                        db=err_db, station_id=self.id, event_type="StartTransaction",
                        direction="inbound", connector_id=connector_id, severity="error",
                        error_message=f"[{err_cat}] {e}",
                    )
            except Exception as log_err:
                self.logger.debug(f"Failed to log StartTransaction error to DB: {log_err}")
            return call_result.StartTransaction(
                transaction_id=0,
                id_tag_info={"status": AuthorizationStatus.invalid}
            )

    @on('StopTransaction')
    def on_stop_transaction(self, meter_stop, timestamp, transaction_id, **kwargs):
        """Завершение сеанса зарядки"""
        id_tag = kwargs.get('id_tag')
        reason = kwargs.get('reason', 'Local')

        self.logger.info(f"StopTransaction: transaction_id={transaction_id}, meter_stop={meter_stop}")

        # OCPP DB log
        try:
            with next(get_db()) as log_db:
                OCPPLogService.log_event(
                    db=log_db,
                    station_id=self.id,
                    event_type="StopTransaction",
                    direction="inbound",
                    request_payload={"transaction_id": transaction_id, "meter_stop": meter_stop, "reason": reason},
                    severity="info",
                )
        except Exception as e:
            self.logger.warning(f"Failed to log OCPP event (StopTransaction): {e}", exc_info=True)

        try:
            with next(get_db()) as db:
                # Получаем информацию о транзакции для определения коннектора
                transaction = db.query(OCPPTransaction).filter(
                    OCPPTransaction.station_id == self.id,
                    OCPPTransaction.transaction_id == transaction_id
                ).first()
                
                connector_id = transaction.connector_id if transaction else None
                
                # Завершаем транзакцию
                transaction = OCPPTransactionService.stop_transaction(
                    db, self.id, transaction_id, float(meter_stop),
                    datetime.fromisoformat(timestamp.replace('Z', '')), reason
                )
                
                # 🆕 АВТОМАТИЧЕСКОЕ ЗАВЕРШЕНИЕ МОБИЛЬНОЙ СЕССИИ
                if transaction and transaction.charging_session_id:
                    session_id = transaction.charging_session_id
                    try:
                        # Рассчитываем потребленную энергию (Wh → kWh)
                        energy_consumed = (float(meter_stop) - float(transaction.meter_start)) / 1000.0

                        # Получаем данные сессии для возврата средств
                        # ВАЖНО: Читаем reserved_amount (не amount!) и payment_processed
                        # Также читаем tariff_rate — тариф зафиксирован при старте сессии
                        session_query = text("""
                            SELECT user_id, reserved_amount, payment_processed, status, tariff_rate
                            FROM charging_sessions
                            WHERE id = :session_id
                        """)
                        session_result = db.execute(session_query, {"session_id": session_id}).fetchone()

                        if session_result:
                            user_id = session_result[0]
                            reserved_amount = float(session_result[1]) if session_result[1] else 0
                            payment_processed = session_result[2] or False
                            session_status = session_result[3]
                            session_tariff_rate = float(session_result[4]) if session_result[4] else None

                            # Используем tariff_rate из сессии (зафиксирован при старте)
                            # Если нет — fallback на stations.price_per_kwh
                            if session_tariff_rate:
                                rate_per_kwh = session_tariff_rate
                                is_night = False  # скидка уже учтена в tariff_rate
                            else:
                                tariff_query = text("""
                                    SELECT price_per_kwh FROM stations WHERE id = :station_id
                                """)
                                tariff_result = db.execute(tariff_query, {"station_id": self.id}).fetchone()
                                base_rate = float(tariff_result[0]) if tariff_result and tariff_result[0] else 13.5
                                rate_per_kwh, is_night = PricingService.get_effective_rate(base_rate)
                                if is_night:
                                    self.logger.info(f"🌙 Ночной тариф применён: {base_rate} → {rate_per_kwh} сом/кВтч")

                            actual_cost = energy_consumed * rate_per_kwh

                            # 🛡️ ЗАЩИТА ОТ ДВОЙНОЙ ОБРАБОТКИ ПЛАТЕЖА
                            if payment_processed:
                                self.logger.info(
                                    f"⏭️ Сессия {session_id} уже обработана (payment_processed=True), "
                                    f"пропускаем финансовые операции"
                                )
                                # Всегда обновляем энергию из StopTransaction — это реальные данные счётчика
                                # (даже если сессия уже stopped, meter_stop точнее наших данных)
                                update_session_query = text("""
                                    UPDATE charging_sessions
                                    SET stop_time = COALESCE(stop_time, NOW()),
                                        status = 'stopped',
                                        energy = :energy_consumed,
                                        amount = :actual_cost
                                    WHERE id = :session_id
                                """)
                                db.execute(update_session_query, {
                                    "energy_consumed": energy_consumed,
                                    "actual_cost": actual_cost,
                                    "session_id": session_id
                                })
                                self.logger.info(
                                    f"📊 Обновлены данные сессии {session_id} из StopTransaction: "
                                    f"energy={energy_consumed:.3f} кВт*ч, cost={actual_cost:.2f} сом"
                                )
                            else:
                                # Платёж ещё не обработан — выполняем финансовые операции

                                # 💳 ДОПОЛНИТЕЛЬНОЕ СПИСАНИЕ: Если actual_cost превышает резерв
                                if actual_cost > reserved_amount:
                                    additional_charge = actual_cost - reserved_amount
                                    self.logger.warning(f"⚠️ ПРЕВЫШЕНИЕ РЕЗЕРВА: actual_cost={actual_cost} > reserved={reserved_amount}. Дополнительное списание: {additional_charge} сом")

                                    # ✅ ЗАЩИТА: Проверяем текущий баланс перед списанием
                                    balance_check = db.execute(text("""
                                        SELECT balance FROM clients WHERE id = :user_id
                                    """), {"user_id": user_id}).fetchone()
                                    current_balance = float(balance_check[0]) if balance_check else 0.0

                                    # Снимаем только то, что есть (не даём уйти в минус)
                                    safe_charge = min(additional_charge, current_balance)

                                    if safe_charge > 0:
                                        additional_charge_query = text("""
                                            UPDATE clients
                                            SET balance = balance - :additional_charge
                                            WHERE id = :user_id AND balance >= :additional_charge
                                        """)
                                        result = db.execute(additional_charge_query, {
                                            "additional_charge": safe_charge,
                                            "user_id": user_id
                                        })

                                        if result.rowcount == 0:
                                            # Race condition: баланс изменился между SELECT и UPDATE
                                            self.logger.error(f"❌ Не удалось списать {safe_charge} сом с клиента {user_id} — баланс изменился")
                                            safe_charge = 0.0

                                    # Создаем запись о дополнительной транзакции (charge_payment, не balance_topup!)
                                    if safe_charge > 0:
                                        additional_transaction_query = text("""
                                            INSERT INTO payment_transactions_odengi (client_id, transaction_type, amount, description, charging_session_id)
                                            VALUES (:client_id, 'charge_payment', :amount, :description, :session_id)
                                        """)
                                        db.execute(additional_transaction_query, {
                                            "client_id": user_id,
                                            "amount": safe_charge,
                                            "description": f"Доплата за сессию {session_id} (превышение резерва на {additional_charge:.2f} сом)",
                                            "session_id": session_id
                                        })

                                    if safe_charge < additional_charge:
                                        self.logger.warning(
                                            f"⚠️ Недостаток средств: нужно было списать {additional_charge:.2f} сом, "
                                            f"списано {safe_charge:.2f} сом. Клиент {user_id}, сессия {session_id}"
                                        )
                                    self.logger.info(f"💳 Дополнительно списано {safe_charge:.2f} сом с клиента {user_id}")
                                    refund_amount = 0  # Возврата нет, так как все потрачено
                                else:
                                    refund_amount = reserved_amount - actual_cost

                                # Возврат неиспользованных средств
                                if refund_amount > 0:
                                    refund_query = text("""
                                        UPDATE clients
                                        SET balance = balance + :refund_amount
                                        WHERE id = :user_id
                                    """)
                                    db.execute(refund_query, {
                                        "refund_amount": refund_amount,
                                        "user_id": user_id
                                    })

                                    self.logger.info(f"💰 Возврат {refund_amount} сом клиенту {user_id}")

                                # Обновляем сессию с фактическими данными и ставим payment_processed=TRUE
                                update_session_query = text("""
                                    UPDATE charging_sessions
                                    SET stop_time = NOW(), status = 'stopped',
                                        energy = :energy_consumed, amount = :actual_cost,
                                        payment_processed = TRUE
                                    WHERE id = :session_id
                                """)
                                db.execute(update_session_query, {
                                    "energy_consumed": energy_consumed,
                                    "actual_cost": actual_cost,
                                    "session_id": session_id
                                })

                                self.logger.info(f"✅ Mobile сессия {session_id} завершена: {energy_consumed} кВт⋅ч, {actual_cost} сом")
                        
                    except Exception as e:
                        err_cat = _classify_error(e)
                        self.logger.error(f"Error in StopTransaction finalize [{err_cat}]: station={self.id}, session={session_id}: {e}", exc_info=True)
                        try:
                            with next(get_db()) as err_db:
                                OCPPLogService.log_event(
                                    db=err_db, station_id=self.id, event_type="StopTransaction",
                                    direction="inbound", severity="error",
                                    error_message=f"[{err_cat}] finalize session {session_id}: {e}",
                                )
                                err_db.execute(text("""
                                    UPDATE charging_sessions SET error_details = :err
                                    WHERE id = :sid
                                """), {"err": f"[{err_cat}] {e}", "sid": session_id})
                                err_db.commit()
                        except Exception as log_err:
                            self.logger.debug(f"Failed to log StopTransaction error to DB: {log_err}")

                # 🆕 АВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ: Коннектор становится свободен
                if connector_id:
                    update_query = text("""
                        UPDATE connectors 
                        SET status = 'available', error_code = 'NoError', last_status_update = NOW()
                        WHERE station_id = :station_id AND connector_number = :connector_id
                    """)
                    db.execute(update_query, {"station_id": self.id, "connector_id": connector_id})
                
                db.commit()
                
                # Очищаем активные сессии (оба ключа: station_id и station_id:connector_id)
                active_sessions.pop(self.id, None)
                if connector_id:
                    active_sessions.pop(f"{self.id}:{connector_id}", None)
                
            # Webhook: charging completed
            _wh_session = getattr(transaction, 'charging_session_id', None) if transaction else None
            _wh_energy = None
            _wh_cost = None
            if transaction and transaction.meter_start is not None:
                try:
                    _wh_energy = round((float(meter_stop) - float(transaction.meter_start)) / 1000.0, 3)
                except Exception:
                    pass
            webhook_service.dispatch_event("charging.completed", {
                "station_id": self.id,
                "connector_id": connector_id,
                "transaction_id": transaction_id,
                "session_id": str(_wh_session) if _wh_session else None,
                "meter_stop": meter_stop,
                "energy_kwh": _wh_energy,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })

            self.logger.info(f"Transaction completed: {transaction_id}, connector {connector_id} marked as Available")
            return call_result.StopTransaction(
                id_tag_info={"status": AuthorizationStatus.accepted}
            )
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in StopTransaction [{err_cat}]: station={self.id}, transaction_id={transaction_id}: {e}", exc_info=True)
            try:
                with next(get_db()) as err_db:
                    OCPPLogService.log_event(
                        db=err_db, station_id=self.id, event_type="StopTransaction",
                        direction="inbound", severity="error",
                        error_message=f"[{err_cat}] {e}",
                    )
            except Exception as log_err:
                self.logger.debug(f"Failed to log StopTransaction error to DB: {log_err}")
            return call_result.StopTransaction(
                id_tag_info={"status": AuthorizationStatus.invalid}
            )

    @on('MeterValues')
    async def on_meter_values(self, connector_id, meter_value, **kwargs):
        """Показания счетчиков энергии"""
        transaction_id = kwargs.get('transaction_id')
        self.logger.debug(f"MeterValues: connector={connector_id}, transaction_id={transaction_id}")
        
        try:
            with next(get_db()) as db:
                # 🔍 DEBUG: Логируем сырую структуру
                self.logger.info(f"🔍 RAW DEBUG: meter_value={meter_value}")
                self.logger.info(f"🔍 RAW DEBUG: type={type(meter_value)}")
                
                # Парсим timestamp
                timestamp_str = meter_value[0].get('timestamp') if meter_value else None
                if timestamp_str:
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', ''))
                else:
                    timestamp = datetime.utcnow()
                
                # Парсим sampled values
                sampled_values = []
                for mv in meter_value:
                    self.logger.info(f"🔍 MV DEBUG: mv={mv}")
                    sampled_value_list = mv.get('sampled_value', [])
                    self.logger.info(f"🔍 SV DEBUG: sampledValue={sampled_value_list}")
                    for sample in sampled_value_list:
                        self.logger.info(f"🔍 SAMPLE DEBUG: sample={sample}")
                        sampled_values.append({
                            'measurand': sample.get('measurand', ''),
                            'value': sample.get('value'),
                            'unit': sample.get('unit', ''),
                            'context': sample.get('context', ''),
                            'format': sample.get('format', ''),
                            'location': sample.get('location', '')
                        })
                
                self.logger.info(f"🔍 DEBUG: Parsed values: {sampled_values}")
                
                # Сохраняем показания
                OCPPMeterService.add_meter_values(
                    db, self.id, connector_id, timestamp, sampled_values, transaction_id
                )
                
                # Получаем активную сессию: сначала по (station:connector), fallback по station
                session_key = f"{self.id}:{connector_id}"
                session = active_sessions.get(session_key) or active_sessions.get(self.id)

                # Fallback: если session найдена но charging_session_id = None, ищем в БД
                if session and not session.get('charging_session_id'):
                    try:
                        cs_result = db.execute(text("""
                            SELECT id FROM charging_sessions
                            WHERE station_id = :station_id AND connector_id = :connector_id AND status = 'started'
                            ORDER BY start_time DESC LIMIT 1
                        """), {"station_id": self.id, "connector_id": connector_id}).fetchone()
                        if cs_result:
                            session['charging_session_id'] = cs_result[0]
                            self.logger.info(f"🔗 Restored charging_session_id from DB: {cs_result[0]}")
                    except Exception as e:
                        self.logger.error(f"DB fallback lookup failed: {e}")

                self.logger.info(f"🔍 DEBUG: Active session for {session_key}: {session}")
                
                if session and sampled_values:
                    for sample in sampled_values:
                        if sample['measurand'] == 'Energy.Active.Import.Register':
                            try:
                                current_energy = float(sample['value'])
                                unit = sample.get('unit', 'Wh')
                                meter_start = session.get('meter_start', 0.0)

                                # OCPP 1.6: meter_start всегда в Wh, а MeterValues могут быть в kWh или Wh
                                if unit == 'kWh':
                                    energy_delivered_kwh = current_energy - (meter_start / 1000.0)
                                else:
                                    energy_delivered_kwh = (current_energy - meter_start) / 1000.0
                                
                                session['energy_delivered'] = energy_delivered_kwh
                                
                                # 🆕 ПРОВЕРКА ЛИМИТОВ ЭНЕРГИИ (только если установлены)
                                limit_type = session.get('limit_type')
                                limit_value = session.get('limit_value')
                                
                                # 🔒 ПОРОГИ ПО VOLTERA: energy=95%, amount=95%, none=90%
                                if limit_type == 'energy' and limit_value and energy_delivered_kwh >= limit_value * 0.95:
                                    self.logger.warning(f"🛑 ЛИМИТ ЭНЕРГИИ (95%): {energy_delivered_kwh:.3f} >= {limit_value * 0.95:.3f} кВт⋅ч. Останавливаем зарядку!")

                                    # Инициируем остановку транзакции
                                    transaction_id = session.get('transaction_id')
                                    if transaction_id:
                                        try:
                                            # Отправляем команду остановки в Redis
                                            await redis_manager.publish_command(self.id, {
                                                "action": "RemoteStopTransaction",
                                                "transaction_id": transaction_id,
                                                "reason": "EnergyLimitReached"
                                            })
                                            self.logger.info(f"📤 Отправлена команда остановки для transaction_id: {transaction_id}")
                                        except Exception as stop_error:
                                            err_cat = _classify_error(stop_error)
                                            self.logger.error(f"Error in MeterValues stop command [{err_cat}]: station={self.id}: {stop_error}", exc_info=True)

                                elif limit_type == 'amount' and limit_value:
                                    # 🆕 ПРОВЕРКА ЛИМИТА ПО СУММЕ (95% порог) с учётом ночного тарифа
                                    session_id = session.get('charging_session_id')
                                    if session_id:
                                        try:
                                            with next(get_db()) as check_db:
                                                # Получаем базовый тариф
                                                tariff_query = text("""
                                                    SELECT price_per_kwh FROM stations WHERE id = :station_id
                                                """)
                                                tariff_result = check_db.execute(tariff_query, {"station_id": self.id}).fetchone()
                                                base_rate = float(tariff_result[0]) if tariff_result and tariff_result[0] else 13.5

                                                # Применяем ночную скидку если нужно
                                                rate_per_kwh, is_night = PricingService.get_effective_rate(base_rate)

                                                current_cost = energy_delivered_kwh * rate_per_kwh
                                                limit_amount = float(limit_value)
                                                stop_threshold = limit_amount * 0.95  # 95% порог

                                                if current_cost >= stop_threshold:
                                                    transaction_id = session.get('transaction_id')
                                                    if transaction_id:
                                                        await redis_manager.publish_command(self.id, {
                                                            "action": "RemoteStopTransaction",
                                                            "transaction_id": transaction_id,
                                                            "reason": "AmountLimitReached"
                                                        })
                                                        night_info = " (ночной тариф)" if is_night else ""
                                                        self.logger.warning(
                                                            f"🛑 ЛИМИТ ПО СУММЕ{night_info}: {current_cost:.2f} >= 95% от {limit_amount} сом. ОСТАНОВКА!"
                                                        )
                                                elif current_cost >= limit_amount * 0.80:
                                                    self.logger.info(
                                                        f"⚠️ Достигнуто 80% лимита: {current_cost:.2f} из {limit_amount} сом"
                                                    )
                                        except Exception as amount_check_error:
                                            err_cat = _classify_error(amount_check_error)
                                            self.logger.error(f"Error in MeterValues amount limit [{err_cat}]: station={self.id}: {amount_check_error}", exc_info=True)

                                elif limit_type is None or limit_type == 'none':
                                    # Неограниченная зарядка - проверяем достаточность средств
                                    session_id = session.get('charging_session_id')
                                    if session_id:
                                        try:
                                            with next(get_db()) as unlimited_db:
                                                # ✅ ФИКС: cs.reserved_amount (не cs.amount!) — зарезервировано при старте
                                                session_query = text("""
                                                    SELECT cs.user_id, cs.reserved_amount, s.price_per_kwh, cs.client_id
                                                    FROM charging_sessions cs
                                                    JOIN stations s ON cs.station_id = s.id
                                                    WHERE cs.id = :session_id
                                                """)
                                                session_result = unlimited_db.execute(session_query, {"session_id": session_id}).fetchone()

                                                if session_result:
                                                    user_id_ul, reserved_amount, base_rate, client_id_ul = session_result
                                                    base_rate = float(base_rate) if base_rate else 13.5

                                                    # Применяем ночную скидку если нужно
                                                    rate_per_kwh, is_night = PricingService.get_effective_rate(base_rate)

                                                    current_cost = energy_delivered_kwh * rate_per_kwh
                                                    reserved_amount_float = float(reserved_amount) if reserved_amount else 200.0
                                                    night_info = " (ночной тариф)" if is_night else ""

                                                    # 🔒 ФИНАНСОВАЯ ЗАЩИТА: 90% порог для безлимитной зарядки
                                                    stop_threshold = reserved_amount_float * 0.90

                                                    if current_cost >= stop_threshold:
                                                        transaction_id_ul = session.get('transaction_id')
                                                        if transaction_id_ul:
                                                            await redis_manager.publish_command(self.id, {
                                                                "action": "RemoteStopTransaction",
                                                                "transaction_id": transaction_id_ul,
                                                                "reason": "AmountLimitReached"
                                                            })
                                                            self.logger.warning(
                                                                f"🛑 БЕЗЛИМИТ (90%){night_info}: {current_cost:.2f} >= 90% от {reserved_amount_float} сом. ОСТАНОВКА!"
                                                            )
                                                    elif current_cost >= reserved_amount_float * 0.80:
                                                        self.logger.warning(
                                                            f"⚠️ СРЕДСТВА ЗАКАНЧИВАЮТСЯ: {current_cost:.2f} из {reserved_amount_float} сом "
                                                            f"({(current_cost/reserved_amount_float)*100:.1f}%)"
                                                        )
                                                        # 🔔 Push notification клиенту при 80% (если есть client_id)
                                                        _notify_client = client_id_ul or user_id_ul
                                                        if _notify_client:
                                                            asyncio.create_task(push_service.send_to_client(
                                                                db=unlimited_db,
                                                                client_id=_notify_client,
                                                                event_type="charging_low_balance",
                                                                session_id=session_id,
                                                                current_cost=current_cost,
                                                                reserved_amount=reserved_amount_float
                                                            ))
                                        except Exception as fund_check_error:
                                            err_cat = _classify_error(fund_check_error)
                                            self.logger.error(f"Error in MeterValues fund check [{err_cat}]: station={self.id}: {fund_check_error}", exc_info=True)
                                
                                # Обновляем энергию в мобильной сессии
                                cs_id = session.get('charging_session_id')
                                if cs_id:
                                    db.execute(text("""
                                        UPDATE charging_sessions
                                        SET energy = :energy_consumed
                                        WHERE id = :session_id AND status = 'started'
                                    """), {"energy_consumed": energy_delivered_kwh, "session_id": cs_id})
                                else:
                                    # Fallback: update by station + connector
                                    db.execute(text("""
                                        UPDATE charging_sessions
                                        SET energy = :energy_consumed
                                        WHERE station_id = :station_id AND connector_id = :connector_id AND status = 'started'
                                    """), {"energy_consumed": energy_delivered_kwh, "station_id": self.id, "connector_id": connector_id})
                                db.commit()

                                self.logger.info(f"⚡ ENERGY UPDATE: {energy_delivered_kwh:.3f} kWh в сессии {cs_id or f'{self.id}:{connector_id}'}")

                                # Webhook: charging progress
                                _soc = next((float(s['value']) for s in sampled_values if s.get('measurand') == 'SoC' and s.get('value')), None)
                                webhook_service.dispatch_event("charging.progress", {
                                    "station_id": self.id,
                                    "connector_id": connector_id,
                                    "transaction_id": transaction_id,
                                    "session_id": str(session.get('charging_session_id')),
                                    "energy_kwh": round(energy_delivered_kwh, 3),
                                    "power_kw": next((float(s['value']) for s in sampled_values if s.get('measurand') == 'Power.Active.Import' and s.get('value')), None),
                                    "soc": int(_soc) if _soc is not None else None,
                                    "timestamp": datetime.utcnow().isoformat() + "Z",
                                })

                            except (ValueError, TypeError) as e:
                                self.logger.warning(f"Ошибка обработки энергии: {e}")
                                break
                else:
                    self.logger.warning(f"🔍 NO SESSION DEBUG: session={session}, sampled_values={bool(sampled_values)}")
                
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in MeterValues [{err_cat}]: station={self.id}, connector={connector_id}: {e}", exc_info=True)
            try:
                with next(get_db()) as err_db:
                    OCPPLogService.log_event(
                        db=err_db, station_id=self.id, event_type="MeterValues",
                        direction="inbound", connector_id=connector_id, severity="error",
                        error_message=f"[{err_cat}] {e}",
                    )
            except Exception as log_err:
                self.logger.debug(f"Failed to log MeterValues error to DB: {log_err}")

        return call_result.MeterValues()

    # ============================================================================
    # TIER 1: ДОПОЛНИТЕЛЬНЫЕ (критически важные)
    # ============================================================================

    @on('GetConfiguration')
    def on_get_configuration(self, **kwargs):
        """Получение конфигурационных параметров"""
        keys = kwargs.get('key', [])
        self.logger.info(f"GetConfiguration request for keys: {keys}")
        
        try:
            with next(get_db()) as db:
                configurations = OCPPConfigurationService.get_configuration(
                    db, self.id, keys
                )
                
                # Формируем ответ
                configuration_key = []
                unknown_key = []
                
                if keys:
                    # Проверяем запрошенные ключи
                    found_keys = {config.key for config in configurations}
                    for key in keys:
                        if key in found_keys:
                            config = next(c for c in configurations if c.key == key)
                            configuration_key.append({
                                "key": config.key,
                                "readonly": config.readonly,
                                "value": config.value
                            })
                        else:
                            unknown_key.append(key)
                else:
                    # Возвращаем все конфигурации
                    for config in configurations:
                        configuration_key.append({
                            "key": config.key,
                            "readonly": config.readonly,
                            "value": config.value
                        })
                
            return call_result.GetConfiguration(
                configuration_key=configuration_key,
                unknown_key=unknown_key if unknown_key else None
            )
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in GetConfiguration [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.GetConfiguration(
                configuration_key=[],
                unknown_key=keys if keys else []
            )

    @on('ChangeConfiguration')
    def on_change_configuration(self, key, value, **kwargs):
        """Изменение конфигурационных параметров"""
        self.logger.info(f"ChangeConfiguration: {key} = {value}")
        
        try:
            with next(get_db()) as db:
                result = OCPPConfigurationService.change_configuration(
                    db, self.id, key, value
                )
                
            status = result.get("status", "Rejected")
            self.logger.info(f"ChangeConfiguration result: {status}")
            
            return call_result.ChangeConfiguration(
                status=ConfigurationStatus[status.lower()]
            )
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in ChangeConfiguration [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.ChangeConfiguration(
                status=ConfigurationStatus.rejected
            )

    @on('Reset')
    def on_reset(self, type, **kwargs):
        """Перезапуск станции"""
        self.logger.info(f"Reset request: {type}")
        
        try:
            # Сохраняем информацию о reset в БД
            with next(get_db()) as db:
                OCPPStationService.update_station_status(
                    db, self.id, "Unavailable", "Reset", f"Reset {type} requested"
                )
            
            # В реальной реализации здесь может быть логика
            # для инициации перезапуска станции
            
            return call_result.Reset(status=ResetStatus.accepted)
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in Reset [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.Reset(status=ResetStatus.rejected)

    @on('UnlockConnector')
    def on_unlock_connector(self, connector_id, **kwargs):
        """Разблокировка коннектора"""
        self.logger.info(f"UnlockConnector request for connector: {connector_id}")
        
        try:
            # В реальной реализации здесь проверяется
            # возможность разблокировки коннектора
            
            with next(get_db()) as db:
                # Проверяем наличие активной транзакции
                active_transaction = db.query(OCPPTransaction).filter(
                    OCPPTransaction.station_id == self.id,
                    OCPPTransaction.status == "started"
                ).first()
                
                if active_transaction:
                    # Есть активная транзакция - не можем разблокировать
                    return call_result.UnlockConnector(
                        status=UnlockStatus.not_supported
                    )
            
            return call_result.UnlockConnector(status=UnlockStatus.unlocked)
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in UnlockConnector [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.UnlockConnector(status=UnlockStatus.unlock_failed)

    # ============================================================================
    # TIER 2: ВАЖНЫЕ ДЛЯ PRODUCTION
    # ============================================================================

    @on('DataTransfer')
    def on_data_transfer(self, vendor_id, **kwargs):
        """Кастомные данные производителя"""
        message_id = kwargs.get('message_id', '')
        data = kwargs.get('data', '')
        
        self.logger.info(f"DataTransfer: vendor_id={vendor_id}, message_id={message_id}")
        
        try:
            # Здесь можно добавить обработку кастомных данных
            # В зависимости от vendor_id и message_id
            
            return call_result.DataTransfer(
                status="Accepted",
                data=f"Received: {data}"
            )
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in DataTransfer [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.DataTransfer(status="Rejected")

    @on('DiagnosticsStatusNotification')
    def on_diagnostics_status_notification(self, status, **kwargs):
        """Статус диагностики"""
        self.logger.info(f"DiagnosticsStatusNotification: status={status}")
        
        try:
            with next(get_db()) as db:
                # Сохраняем статус диагностики в конфигурации
                OCPPConfigurationService.set_configuration(
                    db, self.id, "DiagnosticsStatus", status
                )
                
            return call_result.DiagnosticsStatusNotification()
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in DiagnosticsStatusNotification [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.DiagnosticsStatusNotification()

    @on('FirmwareStatusNotification')
    def on_firmware_status_notification(self, status, **kwargs):
        """Статус обновления прошивки"""
        self.logger.info(f"FirmwareStatusNotification: status={status}")
        
        try:
            with next(get_db()) as db:
                # Обновляем статус прошивки
                station_status = OCPPStationService.get_station_status(db, self.id)
                if station_status:
                    if status == FirmwareStatus.installed:
                        # Прошивка обновлена успешно
                        station_status.firmware_version = "Updated"
                    elif status == FirmwareStatus.installation_failed:
                        # Ошибка обновления
                        station_status.error_code = "FirmwareUpdateFailed"
                    
                    db.commit()
                
            return call_result.FirmwareStatusNotification()
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in FirmwareStatusNotification [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.FirmwareStatusNotification()

    # ============================================================================
    # TIER 2: РАСШИРЕННЫЕ (важные для production)
    # ============================================================================

    @on('ChangeAvailability')
    def on_change_availability(self, connector_id, type, **kwargs):
        """Изменение доступности станции/коннектора"""
        self.logger.info(f"ChangeAvailability: connector={connector_id}, type={type}")
        
        try:
            with next(get_db()) as db:
                # Обновляем статус в зависимости от типа
                if type == AvailabilityType.operative:
                    new_status = "Available"
                else:  # inoperative
                    new_status = "Unavailable"
                
                OCPPStationService.update_station_status(
                    db, self.id, new_status, None, f"Availability changed to {type}"
                )
            
            return call_result.ChangeAvailability(
                status=AvailabilityStatus.accepted
            )
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in ChangeAvailability [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.ChangeAvailability(
                status=AvailabilityStatus.rejected
            )

    @on('ClearCache')
    def on_clear_cache(self, **kwargs):
        """Очистка локального кэша авторизации"""
        self.logger.info("ClearCache request")
        
        try:
            with next(get_db()) as db:
                # Помечаем все локальные авторизации как устаревшие (через SQL — нет импорта модели)
                db.execute(text("""
                    UPDATE ocpp_authorization
                    SET expires_at = NOW()
                    WHERE station_id = :station_id AND is_local = TRUE
                """), {"station_id": self.id})
                
                db.commit()
                
            self.logger.info(f"Cleared {len(expired_auths)} local authorizations")
            return call_result.ClearCache(status=ClearCacheStatus.accepted)
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in ClearCache [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.ClearCache(status=ClearCacheStatus.rejected)

    @on('GetDiagnostics')
    def on_get_diagnostics(self, location, **kwargs):
        """Получение диагностических данных"""
        retries = kwargs.get('retries', 1)
        retry_interval = kwargs.get('retry_interval', 60)
        start_time = kwargs.get('start_time')
        stop_time = kwargs.get('stop_time')
        
        self.logger.info(f"GetDiagnostics request to location: {location}")
        
        try:
            # В реальной реализации здесь запускается процесс
            # сбора и отправки диагностических данных
            
            # Генерируем имя файла диагностики
            filename = f"diagnostics_{self.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.log"
            
            # Сохраняем информацию о запросе диагностики
            with next(get_db()) as db:
                OCPPConfigurationService.set_configuration(
                    db, self.id, "DiagnosticsLocation", location
                )
                OCPPConfigurationService.set_configuration(
                    db, self.id, "DiagnosticsFilename", filename
                )
            
            return call_result.GetDiagnostics(file_name=filename)
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in GetDiagnostics [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.GetDiagnostics(file_name=None)

    @on('UpdateFirmware')
    def on_update_firmware(self, location, retrieve_date, **kwargs):
        """Обновление прошивки"""
        retries = kwargs.get('retries', 1)
        retry_interval = kwargs.get('retry_interval', 60)
        
        self.logger.info(f"UpdateFirmware request: {location} at {retrieve_date}")
        
        try:
            # В реальной реализации здесь планируется загрузка
            # и установка новой прошивки
            
            with next(get_db()) as db:
                # Сохраняем информацию об обновлении
                OCPPConfigurationService.set_configuration(
                    db, self.id, "FirmwareUpdateLocation", location
                )
                OCPPConfigurationService.set_configuration(
                    db, self.id, "FirmwareUpdateDate", retrieve_date
                )
                
                # Обновляем статус станции
                OCPPStationService.update_station_status(
                    db, self.id, "Unavailable", None, "Firmware update scheduled"
                )
            
            return call_result.UpdateFirmware()
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in UpdateFirmware [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.UpdateFirmware()

    # ============================================================================ 
    # TIER 3: ДОПОЛНИТЕЛЬНЫЕ (полезные для мониторинга)
    # ============================================================================

    @on('TriggerMessage')
    def on_trigger_message(self, requested_message, **kwargs):
        """Запрос отправки определенного сообщения"""
        connector_id = kwargs.get('connector_id')
        
        self.logger.info(f"TriggerMessage: {requested_message} for connector {connector_id}")
        
        try:
            # В зависимости от requested_message отправляем соответствующее сообщение
            if requested_message == MessageTrigger.boot_notification:
                # Планируем отправку BootNotification
                pass
            elif requested_message == MessageTrigger.heartbeat:
                # Планируем отправку Heartbeat
                pass
            elif requested_message == MessageTrigger.status_notification:
                # Планируем отправку StatusNotification
                pass
            elif requested_message == MessageTrigger.meter_values:
                # Планируем отправку MeterValues
                pass
            
            return call_result.TriggerMessage(
                status=TriggerMessageStatus.accepted
            )
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in TriggerMessage [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.TriggerMessage(
                status=TriggerMessageStatus.rejected
            )

    @on('SendLocalList')
    def on_send_local_list(self, list_version, update_type, **kwargs):
        """Обновление локального списка авторизации"""
        local_authorization_list = kwargs.get('local_authorization_list', [])
        
        self.logger.info(f"SendLocalList: version={list_version}, type={update_type}")
        
        try:
            with next(get_db()) as db:
                if update_type == UpdateType.full:
                    # Полная замена списка — удаляем все локальные авторизации через SQL
                    db.execute(text("""
                        DELETE FROM ocpp_authorization
                        WHERE station_id = :station_id AND is_local = TRUE
                    """), {"station_id": self.id})
                
                # Добавляем новые записи через SQL (нет импорта модели OCPPAuthorization)
                for item in local_authorization_list:
                    id_tag = item.get('id_tag')
                    id_tag_info = item.get('id_tag_info', {})
                    expires_at = None
                    if id_tag_info.get('expiry_date'):
                        expires_at = datetime.fromisoformat(id_tag_info['expiry_date'])
                    
                    db.execute(text("""
                        INSERT INTO ocpp_authorization (station_id, id_tag, status, is_local, expires_at)
                        VALUES (:station_id, :id_tag, :status, TRUE, :expires_at)
                        ON CONFLICT (station_id, id_tag) DO UPDATE
                        SET status = EXCLUDED.status, is_local = TRUE, expires_at = EXCLUDED.expires_at
                    """), {
                        "station_id": self.id,
                        "id_tag": id_tag,
                        "status": id_tag_info.get('status', 'Accepted'),
                        "expires_at": expires_at
                    })
                
                # Сохраняем версию списка
                OCPPConfigurationService.set_configuration(
                    db, self.id, "LocalListVersion", str(list_version)
                )
                
                db.commit()
            
            return call_result.SendLocalList(status=UpdateStatus.accepted)
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in SendLocalList [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.SendLocalList(status=UpdateStatus.failed)

    @on('GetLocalListVersion')
    def on_get_local_list_version(self, **kwargs):
        """Получение версии локального списка авторизации"""
        self.logger.info("GetLocalListVersion request")
        
        try:
            with next(get_db()) as db:
                configs = OCPPConfigurationService.get_configuration(
                    db, self.id, ["LocalListVersion"]
                )
                
                version = 0
                if configs:
                    try:
                        version = int(configs[0].value)
                    except (ValueError, IndexError):
                        version = 0
            
            return call_result.GetLocalListVersion(list_version=version)
            
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in GetLocalListVersion [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result.GetLocalListVersion(list_version=0)

    async def _perform_error_diagnostics(self, connector_id: int, error_code: str):
        """Автоматическая диагностика при ошибках коннекторов"""
        try:
            self.logger.info(f"🔍 Запуск диагностики для коннектора {connector_id}, ошибка: {error_code}")
            
            # Задержка перед диагностикой
            await asyncio.sleep(2)
            
            # Запрашиваем конфигурацию станции
            try:
                config_response = await self.call(call.GetConfiguration())
                self.logger.info(f"📋 Конфигурация станции получена: {len(config_response.configuration_key if config_response.configuration_key else [])} параметров")
                
                # Логируем важные конфигурационные параметры
                if config_response.configuration_key:
                    for config in config_response.configuration_key:
                        if config.key in ['HeartbeatInterval', 'MeterValueSampleInterval', 'NumberOfConnectors', 'SupportedFeatureProfiles']:
                            self.logger.info(f"   {config.key}: {config.value}")
                            
            except Exception as e:
                err_cat = _classify_error(e)
                self.logger.error(f"Error in _perform_error_diagnostics config [{err_cat}]: station={self.id}: {e}", exc_info=True)
            
            # Запрашиваем диагностику
            try:
                diag_response = await self.call(call.GetDiagnostics(
                    location=f"ftp://example.com/diagnostics_{self.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.log"
                ))
                self.logger.info(f"📊 Диагностика запрошена: {diag_response}")
                
            except Exception as e:
                self.logger.warning(f"⚠️ Диагностика недоступна: {e}")
            
            # Если это OtherError, пытаемся получить больше информации через DataTransfer
            if error_code == "OtherError":
                try:
                    data_response = await self.call(call.DataTransfer(
                        vendor_id="diagnostics",
                        message_id="error_details",
                        data=f"connector_{connector_id}"
                    ))
                    self.logger.info(f"🔍 DataTransfer ответ: {data_response}")
                    
                except Exception as e:
                    self.logger.debug(f"DataTransfer не поддерживается: {e}")
            
            self.logger.info(f"✅ Диагностика завершена для коннектора {connector_id}")

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in _perform_error_diagnostics [{err_cat}]: station={self.id}: {e}", exc_info=True)

    async def _send_charging_error_notification(
        self,
        connector_id: int,
        error_code: str,
        info: Optional[str] = None,
        vendor_error_code: Optional[str] = None
    ):
        """Отправить push notification клиенту об ошибке зарядки"""
        try:
            with next(get_db()) as db:
                # Найти активную сессию для этого коннектора через JOIN с ocpp_transactions
                session_query = text("""
                    SELECT cs.id, cs.user_id
                    FROM charging_sessions cs
                    JOIN ocpp_transactions ot ON cs.id = ot.charging_session_id
                    WHERE cs.station_id = :station_id
                      AND ot.connector_id = :connector_id
                      AND cs.status = 'started'
                    ORDER BY cs.start_time DESC
                    LIMIT 1
                """)

                session_result = db.execute(session_query, {
                    "station_id": self.id,
                    "connector_id": connector_id
                }).fetchone()

                if not session_result:
                    self.logger.debug(f"No active session found for connector {connector_id}, skipping error notification")
                    return

                session_id, client_id = session_result

                # Формируем сообщение об ошибке
                error_message = f"Ошибка коннектора {connector_id}: {error_code}"
                if info:
                    error_message += f" - {info}"

                # Отправляем push notification
                result = await push_service.send_to_client(
                    db=db,
                    client_id=client_id,
                    event_type="charging_error",
                    session_id=session_id,
                    error_code=error_code,
                    error_message=error_message
                )

                if result.get("success"):
                    self.logger.info(f"✅ Charging error notification sent to client {client_id} (session {session_id})")
                else:
                    self.logger.warning(f"Failed to send charging error notification: {result.get('reason', 'Unknown')}")

        except Exception as e:
            self.logger.warning(f"Error sending charging error notification for connector {connector_id}: {e}")


class OCPPWebSocketHandler:
    """Основной класс для обработки OCPP WebSocket подключений"""
    
    def __init__(self, station_id: str, websocket: WebSocket):
        self.station_id = station_id
        self.websocket = websocket
        self.charge_point: Optional[OCPPChargePoint] = None
        self.pubsub_task: Optional[asyncio.Task] = None
        self.logger = logging.getLogger(f"OCPPHandler.{station_id}")
        
    async def handle_connection(self):
        """Основная логика обработки WebSocket подключения"""
        connection_start = datetime.utcnow()
        client_info = getattr(self.websocket, 'client', None)
        client_ip = client_info.host if client_info else 'unknown'

        self.logger.info(f"🔌 НОВОЕ ПОДКЛЮЧЕНИЕ: Station {self.station_id} от IP {client_ip}")

        try:
            # 1. Проверяем аутентификацию станции по API ключу
            is_authorized = await station_auth.verify_station_connection(
                self.station_id,
                websocket=self.websocket
            )

            if not is_authorized:
                self.logger.warning(f"❌ Станция {self.station_id} не авторизована - отклоняем подключение")
                await self.websocket.close(code=1008, reason="Unauthorized")
                return

            # 2. Проверяем существование станции в БД (по id или serial_number)
            #    Bypass для: STRESS_TEST_MODE + LOAD-SIM, или SIM-* (симулятор)
            _stress_mode = os.environ.get("STRESS_TEST_MODE", "0") == "1"
            _is_simulator = self.station_id.startswith("SIM-")
            if (_stress_mode and self.station_id.startswith("LOAD-SIM")) or _is_simulator:
                station = (self.station_id, "available")
                self.logger.info(f"🔧 Simulator/test bypass DB check for {self.station_id}")
            else:
                with next(get_db()) as db:
                    result = db.execute(text("""
                        SELECT id, status FROM stations
                        WHERE id = :station_id OR serial_number = :station_id
                        LIMIT 1
                    """), {"station_id": self.station_id})

                    station = result.fetchone()
                    if not station:
                        self.logger.warning(f"❌ Станция {self.station_id} не найдена в базе данных")
                        await self.websocket.close(code=1008, reason="Unknown station")
                        return

            # Нормализуем station_id к реальному DB id (станция могла подключиться по serial_number)
            # Для SIM-* станций нормализация не нужна (виртуальные, нет записи в DB)
            db_station_id = station[0]
            if db_station_id != self.station_id and not _is_simulator:
                self.logger.info(f"📌 Нормализация station_id: {self.station_id} -> {db_station_id} (DB id)")
                self.station_id = db_station_id
                self.logger = logging.getLogger(f"OCPPHandler.{db_station_id}")

            self.logger.info(f"✅ Станция {self.station_id} авторизована и найдена (статус: {station[1]})")
            
            # Принимаем WebSocket подключение с гибкой договоренностью субпротокола
            # Dual-protocol: поддержка OCPP 1.6 и 2.0.1
            self.logger.debug(f"Принимаем WebSocket для {self.station_id}")
            requested_proto_header = self.websocket.headers.get('sec-websocket-protocol')
            chosen_subprotocol = None
            self._ocpp_version = "1.6"  # Default to v1.6
            if requested_proto_header:
                requested_list = [p.strip() for p in requested_proto_header.split(',') if p.strip()]
                acceptable_v16 = {"ocpp1.6", "ocpp1.6j", "ocpp1.6-json"}
                acceptable_v201 = {"ocpp2.0.1", "ocpp2.0", "ocpp20"}
                for proto in requested_list:
                    if proto.lower() in acceptable_v201:
                        chosen_subprotocol = proto
                        self._ocpp_version = "2.0.1"
                        break
                    elif proto.lower() in acceptable_v16:
                        chosen_subprotocol = proto
                        self._ocpp_version = "1.6"
                        break
            if chosen_subprotocol:
                await self.websocket.accept(subprotocol=chosen_subprotocol)
                self.logger.info(f"WebSocket принят для {self.station_id} с протоколом {chosen_subprotocol} (OCPP {self._ocpp_version})")
            else:
                # Клиент не запросил субпротокол — принимаем без него для совместимости (v1.6)
                await self.websocket.accept()
                self.logger.debug(f"WebSocket принят для {self.station_id} без субпротокола (совместимость v1.6)")

            # Создаем адаптер для OCPP библиотеки
            adapter = WebSocketAdapter(self.websocket)

            # Создаем ChargePoint в зависимости от версии протокола
            if self._ocpp_version == "2.0.1":
                from .ws_handler_v201 import OCPP201ChargePoint
                self.charge_point = OCPP201ChargePoint(self.station_id, adapter)
                self.logger.info(f"OCPP 2.0.1 ChargePoint создан для {self.station_id}")
            else:
                self.charge_point = OCPPChargePoint(self.station_id, adapter)
                self.logger.debug(f"OCPP 1.6 ChargePoint создан для {self.station_id}")
            
            # Регистрируем станцию в Redis и в active handlers
            await redis_manager.register_station(self.station_id)
            _active_handlers[self.station_id] = self
            self.logger.debug(f"Станция {self.station_id} зарегистрирована в Redis")

            # Запускаем обработчик команд из Redis
            self.pubsub_task = asyncio.create_task(
                self._handle_redis_commands()
            )
            self.logger.debug(f"Redis pub/sub task создан для {self.station_id}")

            # Простая задержка для инициализации pubsub (как Voltera)
            # Event-механизм убран - он создавал race condition при переподключениях
            await asyncio.sleep(0.1)
            self.logger.info(f"✅ Pub/sub инициализирован для {self.station_id}")

            # Запускаем OCPP charge point
            self.logger.info(f"🚀 Запуск OCPP ChargePoint для {self.station_id}")
            await self.charge_point.start()
            
        except WebSocketDisconnect:
            connection_duration = (datetime.utcnow() - connection_start).total_seconds()
            self.logger.info(f"🔌 ОТКЛЮЧЕНИЕ: Station {self.station_id} (длительность: {connection_duration:.1f}с)")
        except Exception as e:
            connection_duration = (datetime.utcnow() - connection_start).total_seconds()
            err_cat = _classify_error(e)
            self.logger.error(f"Error in handle_connection [{err_cat}]: station={self.station_id} (длительность: {connection_duration:.1f}с): {e}", exc_info=True)
        finally:
            await self._cleanup()
    
    async def _handle_redis_commands(self):
        """Обработка команд из Redis pub/sub"""
        try:
            async for command in redis_manager.listen_commands(self.station_id):
                self.logger.info(f"Received command: {command}")
                
                if not self.charge_point:
                    continue
                    
                command_type = command.get("action")
                
                try:
                    if command_type == "RemoteStartTransaction":
                        # 🆕 СОХРАНЯЕМ ЛИМИТЫ в активную сессию для последующей проверки
                        session_id = command.get("session_id")
                        limit_type = command.get("limit_type")
                        limit_value = command.get("limit_value")

                        if session_id and limit_type and limit_value:
                            active_sessions[self.station_id] = {
                                'charging_session_id': session_id,
                                'limit_type': limit_type,
                                'limit_value': float(limit_value),
                                'energy_delivered': 0.0
                            }
                            self.logger.info(f"📋 Установлен лимит: {limit_type} = {limit_value} для сессии {session_id}")

                        # v201: RequestStartTransaction, v16: RemoteStartTransaction
                        if self._ocpp_version == "2.0.1":
                            import random
                            response = await self.charge_point.call(
                                call_201.RequestStartTransaction(
                                    id_token={
                                        'id_token': command.get("id_tag", "system"),
                                        'type': 'Central'
                                    },
                                    remote_start_id=random.randint(1, 2**31),
                                    evse_id=command.get("connector_id", 1)
                                )
                            )
                            self.logger.info(f"RequestStartTransaction v201 response: {response}")
                        else:
                            response = await self.charge_point.call(
                                call.RemoteStartTransaction(
                                    connector_id=command.get("connector_id", 1),
                                    id_tag=command.get("id_tag", "system")
                                )
                            )
                            self.logger.info(f"RemoteStartTransaction response: {response}")

                        # OCPP DB log (outbound)
                        try:
                            with next(get_db()) as log_db:
                                OCPPLogService.log_event(
                                    db=log_db,
                                    station_id=self.station_id,
                                    event_type="RemoteStartTransaction",
                                    direction="outbound",
                                    connector_id=command.get("connector_id", 1),
                                    request_payload={"id_tag": command.get("id_tag"), "session_id": session_id},
                                    severity="info",
                                )
                        except Exception as e:
                            self.logger.warning(f"Failed to log OCPP event (RemoteStartTransaction): {e}", exc_info=True)

                    elif command_type == "RemoteStopTransaction":
                        session = active_sessions.get(self.station_id, {})
                        transaction_id = session.get('transaction_id',
                                                   command.get("transaction_id", 1))

                        # v201: RequestStopTransaction with v201_transaction_id string
                        if self._ocpp_version == "2.0.1":
                            v201_tx_id = session.get('v201_transaction_id', str(transaction_id))
                            response = await self.charge_point.call(
                                call_201.RequestStopTransaction(
                                    transaction_id=v201_tx_id
                                )
                            )
                            self.logger.info(f"RequestStopTransaction v201 response: {response}")
                        else:
                            response = await self.charge_point.call(
                                call.RemoteStopTransaction(transaction_id=transaction_id)
                            )
                            self.logger.info(f"RemoteStopTransaction response: {response}")

                        # OCPP DB log (outbound)
                        try:
                            with next(get_db()) as log_db:
                                OCPPLogService.log_event(
                                    db=log_db,
                                    station_id=self.station_id,
                                    event_type="RemoteStopTransaction",
                                    direction="outbound",
                                    request_payload={"transaction_id": transaction_id},
                                    severity="info",
                                )
                        except Exception as e:
                            self.logger.warning(f"Failed to log OCPP event (RemoteStopTransaction): {e}", exc_info=True)

                    elif command_type == "Reset":
                        # v201: Reset with string type ("OnIdle"/"Immediate")
                        if self._ocpp_version == "2.0.1":
                            reset_type = command.get("type", "OnIdle")
                            v201_reset_map = {"Soft": "OnIdle", "Hard": "Immediate"}
                            v201_type = v201_reset_map.get(reset_type, reset_type)
                            response = await self.charge_point.call(
                                call_201.Reset(type=v201_type)
                            )
                            self.logger.info(f"Reset v201 response: {response}")
                        else:
                            reset_type = command.get("type", "Soft")
                            response = await self.charge_point.call(
                                call.Reset(type=ResetType[reset_type.lower()])
                            )
                            self.logger.info(f"Reset response: {response}")
                        
                    elif command_type == "UnlockConnector":
                        connector_id = command.get("connectorId", 1)
                        if self._ocpp_version == "2.0.1":
                            evse_id = command.get("evseId", connector_id)
                            response = await self.charge_point.call(
                                call_201.UnlockConnector(
                                    evse_id=evse_id,
                                    connector_id=connector_id
                                )
                            )
                            self.logger.info(f"UnlockConnector v201 response: {response}")
                        else:
                            response = await self.charge_point.call(
                                call.UnlockConnector(connector_id=connector_id)
                            )
                            self.logger.info(f"UnlockConnector response: {response}")
                        
                    elif command_type == "ChangeConfiguration":
                        key = command.get("key")
                        value = command.get("value")
                        if key and value:
                            response = await self.charge_point.call(
                                call.ChangeConfiguration(key=key, value=value)
                            )
                            self.logger.info(f"ChangeConfiguration response: {response}")
                            
                    elif command_type == "GetConfiguration":
                        keys = command.get("keys", [])
                        response = await self.charge_point.call(
                            call.GetConfiguration(key=keys if keys else None)
                        )
                        self.logger.info(f"GetConfiguration response: {response}")
                        
                    elif command_type == "ChangeAvailability":
                        connector_id = command.get("connectorId", 0)
                        availability_type = command.get("type", "Operative")
                        if self._ocpp_version == "2.0.1":
                            # v201: OperationalStatus + optional EVSE
                            v201_status_map = {
                                "Operative": "Operative",
                                "operative": "Operative",
                                "Inoperative": "Inoperative",
                                "inoperative": "Inoperative",
                            }
                            op_status = v201_status_map.get(availability_type, "Operative")
                            evse = {"id": connector_id} if connector_id > 0 else None
                            response = await self.charge_point.call(
                                call_201.ChangeAvailability(
                                    operational_status=op_status,
                                    evse=evse
                                )
                            )
                            self.logger.info(f"ChangeAvailability v201 response: {response}")
                        else:
                            response = await self.charge_point.call(
                                call.ChangeAvailability(
                                    connector_id=connector_id,
                                    type=AvailabilityType[availability_type.lower()]
                                )
                            )
                            self.logger.info(f"ChangeAvailability response: {response}")
                        
                    elif command_type == "ClearCache":
                        if self._ocpp_version == "2.0.1":
                            response = await self.charge_point.call(call_201.ClearCache())
                            self.logger.info(f"ClearCache v201 response: {response}")
                        else:
                            response = await self.charge_point.call(call.ClearCache())
                            self.logger.info(f"ClearCache response: {response}")
                        
                    elif command_type == "GetDiagnostics":
                        location = command.get("location", "/tmp/diagnostics.log")
                        response = await self.charge_point.call(
                            call.GetDiagnostics(location=location)
                        )
                        self.logger.info(f"GetDiagnostics response: {response}")
                        
                    elif command_type == "UpdateFirmware":
                        location = command.get("location")
                        retrieve_date = command.get("retrieveDate")
                        if location and retrieve_date:
                            response = await self.charge_point.call(
                                call.UpdateFirmware(
                                    location=location,
                                    retrieve_date=retrieve_date
                                )
                            )
                            self.logger.info(f"UpdateFirmware response: {response}")
                            
                    elif command_type == "TriggerMessage":
                        requested_message = command.get("requestedMessage")
                        connector_id = command.get("connectorId")
                        if requested_message:
                            if self._ocpp_version == "2.0.1":
                                evse = {"id": connector_id} if connector_id else None
                                response = await self.charge_point.call(
                                    call_201.TriggerMessage(
                                        requested_message=requested_message,
                                        evse=evse
                                    )
                                )
                                self.logger.info(f"TriggerMessage v201 response: {response}")
                            else:
                                response = await self.charge_point.call(
                                    call.TriggerMessage(
                                        requested_message=MessageTrigger[requested_message.lower()],
                                        connector_id=connector_id
                                    )
                                )
                                self.logger.info(f"TriggerMessage response: {response}")
                            
                except Exception as e:
                    err_cat = _classify_error(e)
                    self.logger.error(f"Error executing command {command_type} [{err_cat}]: station={self.station_id}: {e}", exc_info=True)
                    
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in Redis command handler [{err_cat}]: station={self.station_id}: {e}", exc_info=True)
            try:
                with next(get_db()) as err_db:
                    OCPPLogService.log_event(
                        db=err_db, station_id=self.station_id, event_type="RedisCommandHandler",
                        direction="internal", severity="error",
                        error_message=f"[{err_cat}] {e}",
                    )
            except Exception as log_err:
                self.logger.debug(f"Failed to log RedisCommandHandler error to DB: {log_err}")

    async def _cleanup(self):
        """Очистка ресурсов при отключении"""
        try:
            if self.pubsub_task:
                self.pubsub_task.cancel()

            await redis_manager.unregister_station(self.station_id)

            # Broadcast что станция offline для PWA клиентов
            try:
                with next(get_db()) as db:
                    await RealtimeService.broadcast_station_update(db, self.station_id)
                    self.logger.info(f"📡 Broadcast: станция {self.station_id} offline")
            except Exception as broadcast_error:
                self.logger.warning(f"Не удалось broadcast offline: {broadcast_error}")

            if self.station_id in active_sessions:
                del active_sessions[self.station_id]

            if self.station_id in _active_handlers:
                del _active_handlers[self.station_id]

            self.logger.info(f"Cleanup completed for station {self.station_id}")

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in cleanup [{err_cat}]: station={self.station_id}: {e}", exc_info=True)


class WebSocketAdapter:
    """Адаптер FastAPI WebSocket для совместимости с OCPP библиотекой"""

    # Per-message rate limit: max messages per second per station
    _MSG_RATE_LIMIT = 20  # 20 msgs/sec (OCPP stations can be chatty during charging)
    _MSG_RATE_WINDOW = 1.0  # 1 second sliding window

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self._msg_times: deque = deque()
        self._rate_violations = 0

    async def recv(self):
        """Получение сообщения с per-message rate limiting"""
        message = await self.websocket.receive_text()
        logger = logging.getLogger(f"OCPP.{getattr(self.websocket, 'station_id', 'unknown')}")

        # Per-message rate limiting (sliding window)
        now = time.time()
        while self._msg_times and now - self._msg_times[0] > self._MSG_RATE_WINDOW:
            self._msg_times.popleft()
        if len(self._msg_times) >= self._MSG_RATE_LIMIT:
            self._rate_violations += 1
            if self._rate_violations <= 3:
                logger.warning(f"OCPP message rate limit: {len(self._msg_times)} msgs in {self._MSG_RATE_WINDOW}s")
            elif self._rate_violations == 4:
                logger.warning("OCPP message rate limit: suppressing further warnings")
            # Small backpressure delay instead of dropping OCPP messages
            await asyncio.sleep(0.1)
        self._msg_times.append(now)

        logger.debug(f"📥 ПОЛУЧЕНО: {message}")
        return message
    
    async def send(self, message):
        """Отправка сообщения"""
        # Логируем исходящее сообщение
        logger = logging.getLogger(f"OCPP.{getattr(self.websocket, 'station_id', 'unknown')}")
        logger.debug(f"📤 ОТПРАВЛЕНО: {message}")
        await self.websocket.send_text(message)
    
    async def close(self):
        """Закрытие соединения"""
        await self.websocket.close() 