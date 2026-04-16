"""
Валидация клиента, станции и коннектора для операций зарядки
"""
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from app.services.pricing_service import PricingService

logger = logging.getLogger(__name__)


class ChargingValidators:
    """Валидация входных данных для зарядки"""

    def __init__(self, db: Session):
        self.db = db
        self.pricing_service = PricingService(db)

    def validate_client(self, client_id: str, for_update: bool = False) -> Dict[str, Any]:
        """Проверка существования клиента и его баланса.

        Args:
            client_id: UUID клиента
            for_update: Если True, блокирует строку для предотвращения race conditions
        """
        query = "SELECT id, balance, status FROM clients WHERE id = :client_id"
        if for_update:
            query += " FOR UPDATE"

        result = self.db.execute(
            text(query),
            {"client_id": client_id}
        ).fetchone()

        if not result:
            return {
                "success": False,
                "error": "client_not_found",
                "message": "Клиент не найден"
            }

        client_status = result[2] if len(result) > 2 else None
        if client_status == "pending_deletion":
            return {
                "success": False,
                "error": "account_deletion_pending",
                "message": "Ваш аккаунт находится в процессе удаления. Операции заблокированы."
            }

        if client_status == "blocked":
            return {
                "success": False,
                "error": "account_blocked",
                "message": "Ваш аккаунт заблокирован. Обратитесь в поддержку."
            }

        return {
            "success": True,
            "id": result[0],
            "balance": Decimal(str(result[1])),
            "status": client_status
        }

    def validate_station(self, station_id: str, connector_id: Optional[int] = None, client_id: Optional[str] = None) -> Dict[str, Any]:
        """Проверка станции и получение динамического тарифа"""
        result = self.db.execute(text("""
            SELECT s.id, s.status, s.is_available, s.last_heartbeat_at,
                   c.connector_type, c.power_kw, s.serial_number
            FROM stations s
            LEFT JOIN connectors c ON s.id = c.station_id
                AND c.connector_number = COALESCE(:connector_id, 1)
            WHERE (s.id = :station_id OR s.serial_number = :station_id) AND s.status = 'active'
            LIMIT 1
        """), {"station_id": station_id, "connector_id": connector_id}).fetchone()

        if not result:
            return {
                "success": False,
                "error": "station_not_found",
                "message": "Станция не найдена или отключена администратором"
            }

        # Используем реальный DB id для последующих операций
        db_station_id = result[0]
        serial_number = result[6]

        # Проверяем онлайн через Redis (sync client) и fallback на is_available из БД
        from ocpp_ws_server.redis_manager import redis_manager
        try:
            key_by_id = f"ocpp:station:{db_station_id}"
            key_by_sn = f"ocpp:station:{serial_number}"
            is_online = bool(
                redis_manager.redis_sync.exists(key_by_id)
                or redis_manager.redis_sync.exists(key_by_sn)
            )
        except Exception:
            is_online = bool(result[2])  # fallback на is_available из БД

        if not is_online:
            last_heartbeat = result[3]
            if last_heartbeat:
                minutes_ago = (datetime.now(timezone.utc) - last_heartbeat).total_seconds() / 60
                return {
                    "success": False,
                    "error": "station_offline",
                    "message": f"Станция недоступна (офлайн {int(minutes_ago)} минут)"
                }
            else:
                return {
                    "success": False,
                    "error": "station_never_connected",
                    "message": "Станция никогда не подключалась к системе"
                }

        try:
            pricing_result = self.pricing_service.calculate_pricing(
                station_id=db_station_id,
                connector_type=result[4],
                power_kw=result[5],
                client_id=client_id
            )

            return {
                "success": True,
                "id": result[0],
                "status": result[1],
                "pricing_result": pricing_result,
                "pricing": pricing_result.to_dict(),
                "connector_type": result[4],
                "power_kw": result[5]
            }
        except Exception as e:
            logger.error(f"Ошибка расчета тарифа для станции {station_id}: {e}", exc_info=True)
            default_pricing = self.pricing_service._get_default_pricing()
            return {
                "success": True,
                "id": result[0],
                "status": result[1],
                "pricing_result": default_pricing,
                "pricing": default_pricing.to_dict()
            }

    def validate_connector(self, station_id: str, connector_id: int, client_id: str = None) -> Dict[str, Any]:
        """Проверка доступности коннектора.

        Разрешает старт если коннектор 'available' или 'reserved' при наличии
        бронирования данного клиента.
        """
        result = self.db.execute(text("""
            SELECT connector_number, status FROM connectors
            WHERE station_id = :station_id AND connector_number = :connector_id
        """), {"station_id": station_id, "connector_id": connector_id}).fetchone()

        if not result:
            return {
                "success": False,
                "error": "connector_not_found",
                "message": "Коннектор не найден"
            }

        connector_status = result[1]

        if connector_status == "available":
            return {"success": True}

        if connector_status == "reserved" and client_id:
            booking = self.db.execute(text("""
                SELECT id FROM bookings
                WHERE station_id = :station_id
                  AND connector_id = :connector_id
                  AND user_id = :user_id
                  AND status = 'active'
            """), {
                "station_id": station_id,
                "connector_id": connector_id,
                "user_id": client_id
            }).fetchone()

            if booking:
                return {"success": True, "has_booking": True}

        return {
            "success": False,
            "error": "connector_occupied",
            "message": "Коннектор занят или неисправен"
        }

    def has_active_session(self, client_id: str) -> bool:
        """Проверка наличия активной сессии с блокировкой для предотвращения race conditions."""
        result = self.db.execute(text("""
            SELECT id FROM charging_sessions
            WHERE user_id = :client_id AND status = 'started'
            FOR UPDATE
        """), {"client_id": client_id}).fetchone()

        return result is not None
