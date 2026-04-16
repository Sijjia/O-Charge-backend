"""
OCPP интеграция: авторизация, создание сессий, Redis команды, статус коннекторов
"""
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging
import json

from app.crud.ocpp_service import payment_service
from app.services.pricing_service import PricingService

logger = logging.getLogger(__name__)


class ChargingOCPPBridge:
    """OCPP интеграция для зарядных операций"""

    def __init__(self, db: Session):
        self.db = db

    def setup_ocpp_authorization(self, client_id: str, session_id: str) -> str:
        """Создание OCPP авторизации (как Voltera - телефон клиента).

        OCPP 1.6 ограничивает id_tag до 20 символов!
        Формат: телефон без + (например: 996555123456) - до 15 символов
        """
        phone_result = self.db.execute(text("""
            SELECT phone FROM clients WHERE id = :client_id
        """), {"client_id": client_id}).fetchone()

        if phone_result and phone_result[0]:
            phone = ''.join(filter(str.isdigit, phone_result[0]))[:20]
            id_tag = phone
            logger.info(f"id_tag = телефон клиента: {id_tag} (как Voltera)")
        else:
            import hashlib
            import time
            session_hash = hashlib.md5(session_id.encode()).hexdigest()[:8].upper()
            ts_hex = hex(int(time.time()))[-4:].upper()
            id_tag = f"E{session_hash}{ts_hex}"
            logger.warning(f"Телефон не найден для {client_id}, fallback id_tag: {id_tag}")

        self.db.execute(text("""
            INSERT INTO ocpp_authorization (id_tag, status, parent_id_tag, client_id)
            VALUES (:id_tag, 'Accepted', NULL, :client_id)
            ON CONFLICT (id_tag) DO UPDATE SET status = 'Accepted', client_id = :client_id
        """), {"id_tag": id_tag, "client_id": client_id})

        return id_tag

    def create_charging_session(
        self,
        client_id: str,
        station_id: str,
        reservation: Dict[str, Any],
        pricing_result,
        energy_kwh: Optional[float],
        amount_som: Optional[float],
        connector_id: Optional[int] = None
    ) -> str:
        """Создание сессии зарядки в БД с сохранением тарифа"""

        # Сохраняем историю тарифа (SAVEPOINT чтобы ошибка не обрушила транзакцию)
        pricing_history_id = None
        if pricing_result:
            try:
                self.db.execute(text("SAVEPOINT pricing_history_sp"))
                pricing_history_result = self.db.execute(text("""
                    INSERT INTO pricing_history
                    (station_id, tariff_plan_id, rule_id, calculation_time,
                     rate_per_kwh, rate_per_minute, session_fee, parking_fee_per_minute,
                     currency, rule_name, rule_details)
                    VALUES (:station_id, :tariff_plan_id, :rule_id, :calculation_time,
                            :rate_per_kwh, :rate_per_minute, :session_fee, :parking_fee,
                            :currency, :rule_name, :rule_details)
                    RETURNING id
                """), {
                    "station_id": station_id,
                    "tariff_plan_id": pricing_result.tariff_plan_id,
                    "rule_id": pricing_result.rule_id,
                    "calculation_time": datetime.now(timezone.utc),
                    "rate_per_kwh": pricing_result.rate_per_kwh,
                    "rate_per_minute": pricing_result.rate_per_minute,
                    "session_fee": pricing_result.session_fee,
                    "parking_fee": pricing_result.parking_fee_per_minute,
                    "currency": pricing_result.currency,
                    "rule_name": pricing_result.active_rule,
                    "rule_details": json.dumps(pricing_result.rule_details)
                }).fetchone()

                if pricing_history_result:
                    pricing_history_id = pricing_history_result[0]
                self.db.execute(text("RELEASE SAVEPOINT pricing_history_sp"))
            except Exception as e:
                self.db.execute(text("ROLLBACK TO SAVEPOINT pricing_history_sp"))
                logger.warning(f"Не удалось сохранить историю тарифа: {e}", exc_info=True)

        night_tariff = pricing_result.rule_details.get('night_discount', False) if pricing_result else False

        insert_result = self.db.execute(text("""
            INSERT INTO charging_sessions
            (user_id, station_id, start_time, status, limit_type, limit_value,
             amount, pricing_history_id, base_amount, final_amount, reserved_amount,
             payment_processed, night_tariff_applied, tariff_rate, connector_id)
            VALUES (:user_id, :station_id, :start_time, 'started', :limit_type, :limit_value,
                    :amount, :pricing_history_id, :base_amount, :final_amount, :reserved_amount,
                    FALSE, :night_tariff, :tariff_rate, :connector_id)
            RETURNING id
        """), {
            "user_id": client_id,
            "station_id": station_id,
            "start_time": datetime.now(timezone.utc),
            "limit_type": reservation['limit_type'],
            "limit_value": reservation['limit_value'],
            "amount": reservation['amount'],
            "pricing_history_id": pricing_history_id,
            "base_amount": reservation.get('base_amount', reservation['amount']),
            "final_amount": reservation['amount'],
            "reserved_amount": reservation['amount'],
            "night_tariff": night_tariff,
            "tariff_rate": float(pricing_result.rate_per_kwh) if pricing_result else None,
            "connector_id": connector_id
        }).fetchone()

        if not insert_result:
            raise ValueError("Не удалось создать сессию зарядки")

        result = insert_result[0]

        if pricing_history_id:
            self.db.execute(text("""
                UPDATE pricing_history
                SET session_id = :session_id
                WHERE id = :pricing_history_id
            """), {
                "session_id": result,
                "pricing_history_id": pricing_history_id
            })

        # Логируем транзакцию резервирования
        from .validators import ChargingValidators
        validators = ChargingValidators(self.db)
        current_balance = validators.validate_client(client_id)['balance']
        new_balance = current_balance - Decimal(str(reservation['amount']))

        payment_service.create_payment_transaction(
            self.db, client_id, "charge_reserve",
            -Decimal(str(reservation['amount'])), current_balance, new_balance,
            f"Резервирование средств для сессии {result}",
            charging_session_id=result
        )

        return result

    def get_session_connector_id(self, session_id: str) -> int:
        """Получить connector_id из сессии зарядки"""
        result = self.db.execute(text("""
            SELECT connector_id FROM charging_sessions WHERE id = :session_id
        """), {"session_id": session_id}).fetchone()
        return result[0] if result and result[0] else 1

    def update_connector_status(self, station_id: str, connector_id: int, status: str):
        """Обновление статуса коннектора"""
        self.db.execute(text("""
            UPDATE connectors
            SET status = :status
            WHERE station_id = :station_id AND connector_number = :connector_id
        """), {
            "station_id": station_id,
            "connector_id": connector_id,
            "status": status
        })

    def _resolve_serial_number(self, station_id: str) -> str | None:
        """Получить serial_number станции по DB id"""
        row = self.db.execute(text(
            "SELECT serial_number FROM stations WHERE id = :sid"
        ), {"sid": station_id}).fetchone()
        return row[0] if row else None

    async def _is_station_online_multi(self, redis_manager: Any, station_id: str, serial_number: str = None):
        """Проверить онлайн: по id и serial_number. Возвращает (is_online, effective_id)."""
        if await redis_manager.is_station_online(station_id):
            return True, station_id
        if serial_number and await redis_manager.is_station_online(serial_number):
            return True, serial_number
        return False, station_id

    async def send_start_command(
        self,
        redis_manager: Any,
        station_id: str,
        connector_id: int,
        id_tag: str,
        session_id: str,
        limit_type: str,
        limit_value: float
    ) -> bool:
        """Отправка команды запуска на станцию через Redis (как Voltera).

        Сохраняет pending session в Redis для связывания при StartTransaction.
        """
        serial_number = self._resolve_serial_number(station_id)
        is_online, effective_id = await self._is_station_online_multi(redis_manager, station_id, serial_number)

        # Сохраняем pending по обоим идентификаторам
        pending_key = f"pending:{station_id}:{connector_id}"
        await redis_manager.redis.setex(pending_key, 300, session_id)
        logger.info(f"Сохранён pending session: {pending_key} -> {session_id}")

        if serial_number and serial_number != station_id:
            pending_key_sn = f"pending:{serial_number}:{connector_id}"
            await redis_manager.redis.setex(pending_key_sn, 300, session_id)
            logger.info(f"Сохранён pending session (SN): {pending_key_sn} -> {session_id}")

        idtag_key = f"idtag:{id_tag}"
        await redis_manager.redis.setex(idtag_key, 86400, session_id)
        logger.info(f"Сохранён маппинг id_tag: {idtag_key} -> {session_id}")

        if is_online:
            command_data = {
                "action": "RemoteStartTransaction",
                "connector_id": connector_id,
                "id_tag": id_tag,
                "session_id": session_id,
                "limit_type": limit_type,
                "limit_value": limit_value
            }

            # Публикуем по effective_id (тот id под которым станция зарегистрирована в Redis)
            subscribers = await redis_manager.publish_command(effective_id, command_data)

            if subscribers > 0:
                logger.info(
                    f"Команда запуска ДОСТАВЛЕНА на станцию {effective_id} "
                    f"(subscribers={subscribers}, id_tag={id_tag})"
                )
            else:
                logger.error(
                    f"0 ПОДПИСЧИКОВ для {effective_id}! "
                    f"Станция онлайн по TTL, но pubsub не готов. "
                    f"Сессия создана, команда будет отправлена при переподключении."
                )
        else:
            logger.warning(
                f"Станция {station_id} ОФЛАЙН - команда НЕ публикуется (как Voltera). "
                f"Pending session сохранён, зарядка начнётся при подключении станции."
            )

        return is_online

    async def send_stop_command(
        self,
        redis_manager: Any,
        station_id: str,
        session_id: str
    ) -> bool:
        """Отправка команды остановки на станцию (по подходу Voltera)"""
        serial_number = self._resolve_serial_number(station_id)
        is_online, effective_id = await self._is_station_online_multi(redis_manager, station_id, serial_number)

        if not is_online:
            logger.warning(f"Станция {station_id} offline - RemoteStopTransaction не отправлен")
            return False

        result = self.db.execute(text("""
            SELECT transaction_id FROM ocpp_transactions
            WHERE charging_session_id = :session_id
            ORDER BY created_at DESC LIMIT 1
        """), {"session_id": session_id}).fetchone()

        if result and result[0]:
            command_data = {
                "action": "RemoteStopTransaction",
                "transaction_id": result[0]
            }
            subscribers = await redis_manager.publish_command(effective_id, command_data)
            if subscribers > 0:
                logger.info(f"RemoteStopTransaction отправлен: station={effective_id}, transaction_id={result[0]}, subscribers={subscribers}")
            else:
                logger.error(f"RemoteStopTransaction НЕ ДОСТАВЛЕН на станцию {effective_id}! 0 подписчиков в Redis")
        else:
            logger.warning(f"OCPP транзакция не найдена для сессии {session_id} - RemoteStopTransaction не отправлен")

        return is_online

    async def check_station_online(self, station_id: str) -> bool:
        """Проверка онлайн статуса станции (по id и serial_number)"""
        try:
            from ocpp_ws_server.redis_manager import redis_manager
            serial_number = self._resolve_serial_number(station_id)
            is_online, _ = await self._is_station_online_multi(redis_manager, station_id, serial_number)
            return is_online
        except Exception as e:
            logger.warning(f"Не удалось проверить статус станции {station_id}: {e}")
            return False

    def get_session_info(self, session_id: str, for_update: bool = True) -> Optional[Dict[str, Any]]:
        """Получение информации о сессии.

        Args:
            session_id: ID сессии
            for_update: Если True, блокирует сессию для предотвращения race conditions
        """
        lock_clause = "FOR UPDATE OF cs" if for_update else ""

        result = self.db.execute(text(f"""
            SELECT cs.id, cs.user_id, cs.station_id, cs.start_time, cs.status,
                   cs.limit_value, cs.reserved_amount, cs.energy, s.price_per_kwh,
                   tp.id as tariff_plan_id, cs.payment_processed
            FROM charging_sessions cs
            LEFT JOIN stations s ON cs.station_id = s.id
            LEFT JOIN tariff_plans tp ON s.tariff_plan_id = tp.id
            WHERE cs.id = :session_id AND cs.status = 'started'
            {lock_clause}
        """), {"session_id": session_id}).fetchone()

        if not result:
            return None

        return {
            'id': result[0],
            'client_id': result[1],
            'station_id': result[2],
            'start_time': result[3],
            'status': result[4],
            'limit_value': result[5],
            'reserved_amount': result[6] or 0,
            'energy': result[7],
            'price_per_kwh': result[8],
            'tariff_plan_id': result[9],
            'payment_processed': result[10] or False
        }
