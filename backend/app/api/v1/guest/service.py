"""
Сервисный слой для Guest Charging
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.api.v1.guest.service")


class GuestChargingService:
    """Сервис гостевой зарядки"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_session(
        self,
        phone: str,
        station_id: str,
        connector_id: int,
        amount_kgs: float,
    ) -> Dict[str, Any]:
        """Создать гостевую сессию зарядки

        Сценарий:
        1. Проверяем станцию и коннектор
        2. Создаём запись в guest_sessions (status=pending)
        3. Гость оплачивает (через QR/платёжную ссылку)
        4. Webhook подтверждает оплату -> status=paid -> запускаем зарядку
        """

        # 1. Проверка станции
        station = await self.db.execute(
            text("""
                SELECT s.id, s.status, s.is_available
                FROM stations s
                WHERE (s.id = :station_id OR s.serial_number = :station_id) AND s.status = 'active'
                LIMIT 1
            """),
            {"station_id": station_id},
        )
        station_row = station.fetchone()

        if not station_row:
            return {
                "success": False,
                "error": "station_not_found",
                "message": "Станция не найдена или отключена",
            }

        if not station_row[2]:  # is_available
            return {
                "success": False,
                "error": "station_offline",
                "message": "Станция временно недоступна",
            }

        # 2. Проверка коннектора
        connector = await self.db.execute(
            text("""
                SELECT connector_number, status
                FROM connectors
                WHERE station_id = :station_id AND connector_number = :connector_id
            """),
            {"station_id": station_id, "connector_id": connector_id},
        )
        connector_row = connector.fetchone()

        if not connector_row:
            return {
                "success": False,
                "error": "connector_not_found",
                "message": "Коннектор не найден",
            }

        if connector_row[1] != "available":
            return {
                "success": False,
                "error": "connector_occupied",
                "message": "Коннектор занят",
            }

        # 3. Проверка: нет ли уже активной гостевой сессии на этот номер
        existing = await self.db.execute(
            text("""
                SELECT id FROM guest_sessions
                WHERE phone = :phone AND status IN ('pending', 'paid', 'charging')
                LIMIT 1
            """),
            {"phone": phone},
        )
        if existing.fetchone():
            return {
                "success": False,
                "error": "session_exists",
                "message": "У вас уже есть активная гостевая сессия",
            }

        # 4. Создаём гостевую сессию
        session_id = str(uuid4())

        await self.db.execute(
            text("""
                INSERT INTO guest_sessions
                    (id, phone, station_id, connector_id, amount_kgs, status, created_at)
                VALUES
                    (:id, :phone, :station_id, :connector_id, :amount_kgs, 'pending', NOW())
            """),
            {
                "id": session_id,
                "phone": phone,
                "station_id": station_id,
                "connector_id": connector_id,
                "amount_kgs": amount_kgs,
            },
        )
        await self.db.commit()

        logger.info(
            f"[Guest] Сессия создана: {session_id}, "
            f"phone={phone}, station={station_id}, amount={amount_kgs}"
        )

        return {
            "success": True,
            "session_id": session_id,
            "phone": phone,
            "station_id": station_id,
            "connector_id": connector_id,
            "amount_kgs": amount_kgs,
            "status": "pending",
            "message": "Гостевая сессия создана. Ожидается оплата.",
        }

    async def get_status(self, session_id: str) -> Dict[str, Any]:
        """Получить статус гостевой сессии"""

        result = await self.db.execute(
            text("""
                SELECT
                    gs.id, gs.phone, gs.station_id, gs.connector_id,
                    gs.amount_kgs, gs.status, gs.payment_id,
                    gs.charging_session_id, gs.created_at,
                    cs.energy, cs.start_time, cs.stop_time, cs.status as charging_status
                FROM guest_sessions gs
                LEFT JOIN charging_sessions cs ON gs.charging_session_id = cs.id
                WHERE gs.id = :session_id
            """),
            {"session_id": session_id},
        )
        row = result.fetchone()

        if not row:
            return {
                "success": False,
                "error": "session_not_found",
                "message": "Гостевая сессия не найдена",
            }

        energy_consumed = float(row[9]) if row[9] else 0.0
        duration_minutes = 0
        if row[10]:  # start_time
            end_time = row[11] or datetime.now(timezone.utc)
            duration_minutes = int((end_time - row[10]).total_seconds() / 60)

        return {
            "success": True,
            "session_id": row[0],
            "phone": row[1],
            "station_id": row[2],
            "connector_id": row[3],
            "amount_kgs": float(row[4]) if row[4] else 0,
            "status": row[5],
            "payment_id": row[6],
            "charging_session_id": row[7],
            "energy_consumed": round(energy_consumed, 3),
            "duration_minutes": duration_minutes,
            "charging_status": row[12],
            "message": self._status_message(row[5]),
        }

    async def stop_session(self, session_id: str, phone: Optional[str] = None) -> Dict[str, Any]:
        """Остановить гостевую зарядку. Требует phone для ownership check."""

        # Получаем сессию
        result = await self.db.execute(
            text("""
                SELECT id, phone, station_id, status, charging_session_id
                FROM guest_sessions
                WHERE id = :session_id
            """),
            {"session_id": session_id},
        )
        row = result.fetchone()

        if not row:
            return {
                "success": False,
                "error": "session_not_found",
                "message": "Гостевая сессия не найдена",
            }

        # Ownership check: phone must match session phone
        if not phone or row[1] != phone:
            logger.warning(f"[Guest] Ownership check failed: session {session_id}, expected phone {row[1]}, got {phone}")
            return {
                "success": False,
                "error": "forbidden",
                "message": "Номер телефона не совпадает с сессией",
            }

        current_status = row[3]
        charging_session_id = row[4]

        if current_status == "completed":
            return {
                "success": False,
                "error": "already_completed",
                "message": "Сессия уже завершена",
            }

        if current_status == "pending":
            # Отменяем неоплаченную сессию
            await self.db.execute(
                text("UPDATE guest_sessions SET status = 'cancelled' WHERE id = :id"),
                {"id": session_id},
            )
            await self.db.commit()
            return {
                "success": True,
                "session_id": session_id,
                "message": "Сессия отменена (оплата не была произведена)",
            }

        if current_status in ("paid", "charging") and charging_session_id:
            # Отправляем OCPP RemoteStop через Redis
            from ocpp_ws_server.redis_manager import redis_manager

            station_id = row[2]

            # Получаем OCPP transaction_id
            tx_result = await self.db.execute(
                text("""
                    SELECT transaction_id FROM ocpp_transactions
                    WHERE charging_session_id = :cs_id
                    ORDER BY created_at DESC LIMIT 1
                """),
                {"cs_id": charging_session_id},
            )
            tx_row = tx_result.fetchone()

            if tx_row and tx_row[0]:
                command_data = {
                    "action": "RemoteStopTransaction",
                    "transaction_id": tx_row[0],
                }
                await redis_manager.publish_command(station_id, command_data)
                logger.info(f"[Guest] RemoteStop sent: station={station_id}, tx={tx_row[0]}")

            # Обновляем статус
            await self.db.execute(
                text("UPDATE guest_sessions SET status = 'completed' WHERE id = :id"),
                {"id": session_id},
            )
            await self.db.commit()

            # Получаем итоговые данные
            energy_result = await self.db.execute(
                text("SELECT energy, amount FROM charging_sessions WHERE id = :id"),
                {"id": charging_session_id},
            )
            energy_row = energy_result.fetchone()

            return {
                "success": True,
                "session_id": session_id,
                "energy_consumed": float(energy_row[0]) if energy_row and energy_row[0] else 0.0,
                "actual_cost": float(energy_row[1]) if energy_row and energy_row[1] else 0.0,
                "message": "Гостевая зарядка остановлена",
            }

        # Для прочих статусов — просто завершаем
        await self.db.execute(
            text("UPDATE guest_sessions SET status = 'completed' WHERE id = :id"),
            {"id": session_id},
        )
        await self.db.commit()

        return {
            "success": True,
            "session_id": session_id,
            "message": "Сессия завершена",
        }

    @staticmethod
    def _status_message(status: str) -> str:
        messages = {
            "pending": "Ожидается оплата",
            "paid": "Оплата получена, запуск зарядки",
            "charging": "Зарядка в процессе",
            "completed": "Зарядка завершена",
            "cancelled": "Сессия отменена",
            "failed": "Ошибка",
        }
        return messages.get(status, "Неизвестный статус")

    @staticmethod
    async def cancel_expired_sessions(db, timeout_minutes: int = 15) -> int:
        """Отменить гостевые сессии, не оплаченные в течение timeout_minutes.
        Возвращает количество отменённых сессий."""
        result = db.execute(
            text("""
                UPDATE guest_sessions SET status = 'cancelled'
                WHERE status = 'pending'
                  AND created_at < NOW() - INTERVAL :timeout
                RETURNING id
            """),
            {"timeout": f"{timeout_minutes} minutes"},
        )
        cancelled = result.fetchall()
        if cancelled:
            db.commit()
            logger.info(f"[Guest] Отменено {len(cancelled)} просроченных сессий (>{timeout_minutes} мин)")
        return len(cancelled)
