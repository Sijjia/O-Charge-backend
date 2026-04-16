"""
Сервисный слой для операций зарядки — ФАСАД

Делегирует в sub-сервисы:
- validators.py  — валидация клиента, станции, коннектора
- billing.py     — резервирование, расчёт стоимости, refund/charge
- ocpp_bridge.py — Redis команды, OCPP auth, коннектор статус
- status_builder.py — запрос данных, energy calc, progress, ответ
"""
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from .validators import ChargingValidators
from .billing import ChargingBilling
from .ocpp_bridge import ChargingOCPPBridge
from .status_builder import ChargingStatusBuilder

logger = logging.getLogger(__name__)


class ChargingService:
    """Сервис для управления сессиями зарядки"""

    def __init__(self, db: Session):
        self.db = db
        self.validators = ChargingValidators(db)
        self.billing = ChargingBilling(db)
        self.ocpp = ChargingOCPPBridge(db)
        self.status = ChargingStatusBuilder(db)

    async def start_charging_session(
        self,
        client_id: str,
        station_id: str,
        connector_id: int,
        energy_kwh: Optional[float],
        amount_som: Optional[float],
        redis_manager: Any,
        promo_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """Начать сессию зарядки с резервированием средств"""

        # 0. Валидация входных параметров
        if energy_kwh is not None and energy_kwh <= 0:
            logger.warning(f"Попытка начать зарядку с отрицательной энергией: {energy_kwh}")
            return {
                "success": False,
                "error": "invalid_parameters",
                "message": "Энергия должна быть положительным числом"
            }

        if amount_som is not None and amount_som <= 0:
            logger.warning(f"Попытка начать зарядку с отрицательной суммой: {amount_som}")
            return {
                "success": False,
                "error": "invalid_parameters",
                "message": "Сумма должна быть положительным числом"
            }

        if amount_som is not None and amount_som > 100000:
            logger.warning(f"Попытка начать зарядку с суммой выше лимита: {amount_som}")
            return {
                "success": False,
                "error": "invalid_parameters",
                "message": "Максимальная сумма резервирования: 100,000 сом"
            }

        if connector_id < 1 or connector_id > 10:
            logger.warning(f"Попытка начать зарядку с некорректным connector_id: {connector_id}")
            return {
                "success": False,
                "error": "invalid_parameters",
                "message": "Номер коннектора должен быть от 1 до 10"
            }

        # 1. Проверка клиента и баланса
        client = self.validators.validate_client(client_id, for_update=True)
        if not client['success']:
            return client

        # 2. Проверка станции и тарифов
        station_info = self.validators.validate_station(station_id, connector_id, client_id)
        if not station_info['success']:
            return station_info

        # Используем реальный DB id (фронт может передать serial_number)
        station_id = station_info['id']

        # 3. Расчет стоимости и резервирования
        reservation = self.billing.calculate_reservation(
            client['balance'],
            station_info['pricing_result'],
            energy_kwh,
            amount_som,
            promo_code=promo_code,
            client_id=client_id
        )
        if not reservation['success']:
            return reservation

        # 4. Проверка коннектора (с учётом бронирования)
        connector = self.validators.validate_connector(station_id, connector_id, client_id)
        if not connector['success']:
            return connector

        # 5. Проверка активных сессий
        if self.validators.has_active_session(client_id):
            return {
                "success": False,
                "error": "session_already_active",
                "message": "У вас уже есть активная сессия зарядки"
            }

        # 6. Резервирование средств
        new_balance = self.billing.reserve_funds(client_id, reservation['amount'], station_id)

        # 7. Создание сессии
        session_id = self.ocpp.create_charging_session(
            client_id,
            station_id,
            reservation,
            station_info['pricing_result'],
            energy_kwh,
            amount_som,
            connector_id=connector_id
        )

        # 7.1. Если коннектор был забронирован — пометить бронирование как использованное
        if connector.get('has_booking'):
            from app.services.booking_service import BookingService
            booking_service = BookingService(self.db)
            booking_service.use_booking(station_id, connector_id, session_id)

        # 8. Создание OCPP авторизации
        id_tag = self.ocpp.setup_ocpp_authorization(client_id, session_id)

        # 9. Обновление статуса коннектора
        self.ocpp.update_connector_status(station_id, connector_id, 'occupied')

        # 10. Коммит транзакции
        self.db.commit()

        # 11. Отправка команды на станцию
        station_online = await self.ocpp.send_start_command(
            redis_manager,
            station_id,
            connector_id,
            id_tag,
            session_id,
            reservation['limit_type'],
            reservation['limit_value']
        )

        night_tariff = station_info['pricing_result'].rule_details.get('night_discount', False)

        return {
            "success": True,
            "session_id": session_id,
            "station_id": station_id,
            "client_id": client_id,
            "connector_id": connector_id,
            "energy_kwh": energy_kwh,
            "pricing": station_info['pricing'],
            "estimated_cost": reservation['amount'],
            "reserved_amount": reservation['amount'],
            "tariff_rate": float(station_info['pricing_result'].rate_per_kwh),
            "night_tariff_applied": night_tariff,
            "new_balance": float(new_balance),
            "message": "Зарядка запущена, средства зарезервированы" if station_online else "Сессия создана, средства зарезервированы. Зарядка начнется при подключении станции.",
            "station_online": station_online
        }

    async def stop_charging_session(
        self,
        session_id: str,
        client_id: str,
        redis_manager: Any
    ) -> Dict[str, Any]:
        """Остановить сессию зарядки с расчетом и возвратом средств"""

        # 1. Получение информации о сессии
        session_info = self.ocpp.get_session_info(session_id)
        if not session_info:
            return {
                "success": False,
                "error": "session_not_found",
                "message": "Активная сессия зарядки не найдена"
            }

        # 2. Проверка владельца
        if session_info['client_id'] != client_id:
            logger.warning(
                f"Попытка остановить чужую сессию: "
                f"session_id={session_id}, owner={session_info['client_id']}, "
                f"requester={client_id}"
            )
            return {
                "success": False,
                "error": "access_denied",
                "message": "У вас нет прав для остановки этой сессии"
            }

        # 3. Расчет фактического потребления
        actual_energy = self.billing.get_actual_energy_consumed(session_id, session_info.get('energy'))

        # 4. Расчет стоимости
        rate_per_kwh = self.billing.get_session_rate(session_info)
        actual_cost = Decimal(str(actual_energy * rate_per_kwh))
        reserved_amount = Decimal(str(session_info['reserved_amount']))

        # 5. Обработка превышения резерва или возврата
        refund_amount, additional_charge = self.billing.calculate_refund_or_charge(
            session_info['client_id'],
            actual_cost,
            reserved_amount,
            session_id
        )

        # 6. Обновление баланса
        new_balance = self.billing.process_session_payment(
            session_info['client_id'],
            refund_amount,
            additional_charge,
            session_id,
            actual_energy
        )

        # 7. Обновление сессии в БД
        self.billing.finalize_session(session_id, actual_energy, float(actual_cost))

        # 7.1. Revenue split
        revenue_split = None
        try:
            from app.services.partner_service import PartnerService
            partner_service = PartnerService(self.db)
            revenue_split = partner_service.calculate_revenue_split(session_id)
        except Exception as e:
            logger.warning(f"Revenue split не удался для сессии {session_id}: {e}")

        # 8. Освобождение коннектора
        session_connector = self.ocpp.get_session_connector_id(session_id)
        self.ocpp.update_connector_status(session_info['station_id'], session_connector, 'available')

        # 9. Коммит транзакции ДО Redis-команды (чтобы не было рассинхрона если commit упадёт)
        self.db.commit()

        # 10. Отправка команды остановки (после коммита — данные уже в БД)
        station_online = await self.ocpp.send_stop_command(
            redis_manager,
            session_info['station_id'],
            session_id
        )

        logger.info(f"Зарядка остановлена: сессия {session_id}, потреблено {actual_energy} кВт*ч")

        response = {
            "success": True,
            "session_id": session_id,
            "station_id": session_info['station_id'],
            "client_id": session_info['client_id'],
            "start_time": session_info['start_time'].isoformat() if session_info['start_time'] else None,
            "stop_time": datetime.now(timezone.utc).isoformat(),
            "energy_consumed": actual_energy,
            "rate_per_kwh": rate_per_kwh,
            "reserved_amount": float(reserved_amount),
            "actual_cost": float(actual_cost),
            "refund_amount": float(refund_amount),
            "new_balance": float(new_balance),
            "message": f"Зарядка завершена. Потреблено {actual_energy} кВт*ч",
            "station_online": station_online
        }

        if revenue_split:
            response["partner_share"] = revenue_split["partner_share"]
            response["platform_share"] = revenue_split["platform_share"]

        return response

    async def get_charging_status(self, session_id: str) -> Dict[str, Any]:
        """Получить полный статус сессии зарядки с OCPP данными"""
        return await self.status.build_status(session_id)

    async def check_and_stop_hanging_sessions(self, redis_manager: Any, max_hours: int = 12, connection_timeout_minutes: int = 10) -> Dict[str, Any]:
        """Автоматически останавливает зависшие сессии зарядки

        Проверяет два типа зависших сессий:
        1. Сессии длительностью > max_hours (по умолчанию 12 часов)
        2. Сессии без OCPP transaction > connection_timeout_minutes (по умолчанию 10 минут)
        """
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_hours)
        connection_timeout = datetime.now(timezone.utc) - timedelta(minutes=connection_timeout_minutes)

        long_sessions_query = text("""
            SELECT id, user_id, station_id, start_time, amount
            FROM charging_sessions
            WHERE status = 'started'
            AND start_time < :cutoff_time
            ORDER BY start_time ASC
        """)

        no_transaction_query = text("""
            SELECT cs.id, cs.user_id, cs.station_id, cs.start_time, cs.amount
            FROM charging_sessions cs
            LEFT JOIN ocpp_transactions ot ON cs.id = ot.charging_session_id
            WHERE cs.status = 'started'
            AND cs.start_time < :connection_timeout
            AND ot.id IS NULL
            ORDER BY cs.start_time ASC
        """)

        long_result = self.db.execute(long_sessions_query, {"cutoff_time": cutoff_time})
        long_sessions = long_result.fetchall()

        no_transaction_result = self.db.execute(no_transaction_query, {"connection_timeout": connection_timeout})
        no_transaction_sessions = no_transaction_result.fetchall()

        all_hanging_sessions = {}
        for session in long_sessions:
            all_hanging_sessions[session[0]] = ("long_duration", session)
        for session in no_transaction_sessions:
            if session[0] not in all_hanging_sessions:
                all_hanging_sessions[session[0]] = ("no_connection", session)

        if not all_hanging_sessions:
            logger.info(f"Зависших сессий не найдено (проверка: {max_hours}ч активных, {connection_timeout_minutes}мин без подключения)")
            return {
                "success": True,
                "stopped_count": 0,
                "sessions": [],
                "long_sessions": 0,
                "no_connection_sessions": 0
            }

        logger.warning(f"Найдено зависших сессий: {len(long_sessions)} длинных, {len(no_transaction_sessions)} без подключения")

        stopped_sessions = []
        errors = []

        for sess_id, (reason, session) in all_hanging_sessions.items():
            session_id = session[0]
            client_id = session[1]
            station_id = session[2]
            start_time = session[3]
            reserved_amount = session[4]

            duration_hours = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600
            duration_minutes = duration_hours * 60

            try:
                if reason == "no_connection":
                    logger.warning(
                        f"ЗАВИСШАЯ СЕССИЯ (НЕТ ПОДКЛЮЧЕНИЯ): session_id={session_id}, "
                        f"client={client_id}, время с создания={duration_minutes:.0f}мин, резерв={reserved_amount} сом"
                    )
                else:
                    logger.warning(
                        f"ЗАВИСШАЯ СЕССИЯ (СЛИШКОМ ДОЛГО): session_id={session_id}, "
                        f"client={client_id}, длительность={duration_hours:.1f}ч"
                    )

                stop_result = await self.stop_charging_session(session_id, client_id, redis_manager)

                if stop_result.get("success"):
                    stopped_sessions.append({
                        "session_id": session_id,
                        "client_id": client_id,
                        "station_id": station_id,
                        "reason": reason,
                        "duration_hours": round(duration_hours, 1),
                        "duration_minutes": round(duration_minutes, 0),
                        "energy_consumed": stop_result.get("energy_consumed", 0),
                        "actual_cost": stop_result.get("actual_cost", 0),
                        "refund_amount": stop_result.get("refund_amount", 0)
                    })
                    if reason == "no_connection":
                        logger.info(
                            f"Зависшая сессия {session_id} остановлена (НЕТ ПОДКЛЮЧЕНИЯ за {duration_minutes:.0f}мин). "
                            f"Возврат: {stop_result.get('refund_amount', 0)} сом"
                        )
                    else:
                        logger.info(
                            f"Зависшая сессия {session_id} остановлена (СЛИШКОМ ДОЛГО: {duration_hours:.1f}ч). "
                            f"Потреблено: {stop_result.get('energy_consumed', 0)} кВт*ч"
                        )
                else:
                    errors.append({
                        "session_id": session_id,
                        "error": stop_result.get("error", "unknown_error"),
                        "message": stop_result.get("message", "Неизвестная ошибка")
                    })
                    logger.error(f"Не удалось остановить зависшую сессию {session_id}: {stop_result.get('message')}")

            except Exception as e:
                logger.error(f"Критическая ошибка при остановке зависшей сессии {session_id}: {e}", exc_info=True)
                errors.append({
                    "session_id": session_id,
                    "error": "exception",
                    "message": str(e)
                })

        logger.info(
            f"Проверка зависших сессий завершена: "
            f"найдено={len(all_hanging_sessions)} ({len(long_sessions)} длинных, {len(no_transaction_sessions)} без подключения), "
            f"остановлено={len(stopped_sessions)}, ошибок={len(errors)}"
        )

        return {
            "success": True,
            "stopped_count": len(stopped_sessions),
            "error_count": len(errors),
            "sessions": stopped_sessions,
            "errors": errors,
            "max_hours": max_hours,
            "connection_timeout_minutes": connection_timeout_minutes,
            "long_sessions_found": len(long_sessions),
            "no_connection_sessions_found": len(no_transaction_sessions)
        }
