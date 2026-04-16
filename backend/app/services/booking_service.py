"""
Сервис бронирования коннекторов
Red Petroleum EV — PRD modules/booking/PRD.md

Клиент может забронировать коннектор на 30 минут (макс 1 активное бронирование).
"""
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)


class BookingService:
    """Сервис для управления бронированиями коннекторов"""

    def __init__(self, db: Session):
        self.db = db

    def create_booking(
        self,
        user_id: str,
        station_id: str,
        connector_id: int,
        duration_minutes: int = 30
    ) -> Dict[str, Any]:
        """Создать бронирование коннектора.

        Args:
            user_id: UUID клиента
            station_id: ID станции
            connector_id: Номер коннектора
            duration_minutes: Длительность бронирования в минутах (по умолчанию 30)

        Returns:
            Dict с результатом бронирования
        """
        # 1. Проверить что у клиента нет активного бронирования (макс 1)
        existing = self.db.execute(text("""
            SELECT id FROM bookings
            WHERE user_id = :user_id AND status = 'active'
        """), {"user_id": user_id}).fetchone()

        if existing:
            return {
                "success": False,
                "error": "booking_already_exists",
                "message": "У вас уже есть активное бронирование"
            }

        # 2. Проверить станцию (active, is_available)
        station = self.db.execute(text("""
            SELECT id, status, is_available
            FROM stations
            WHERE id = :station_id AND status = 'active'
        """), {"station_id": station_id}).fetchone()

        if not station:
            return {
                "success": False,
                "error": "station_not_found",
                "message": "Станция не найдена или отключена"
            }

        if not station[2]:  # is_available
            return {
                "success": False,
                "error": "station_offline",
                "message": "Станция недоступна"
            }

        # 3. Проверить коннектор (status = 'available')
        connector = self.db.execute(text("""
            SELECT connector_number, status
            FROM connectors
            WHERE station_id = :station_id AND connector_number = :connector_id
        """), {"station_id": station_id, "connector_id": connector_id}).fetchone()

        if not connector:
            return {
                "success": False,
                "error": "connector_not_found",
                "message": "Коннектор не найден"
            }

        if connector[1] != "available":
            return {
                "success": False,
                "error": "connector_not_available",
                "message": "Коннектор занят или неисправен"
            }

        # 3.5 Проверить нет ли активного бронирования на этом коннекторе
        existing_booking = self.db.execute(text("""
            SELECT id FROM bookings
            WHERE station_id = :station_id AND connector_id = :connector_id AND status = 'active'
        """), {"station_id": station_id, "connector_id": connector_id}).fetchone()

        if existing_booking:
            return {
                "success": False,
                "error": "connector_already_booked",
                "message": "Коннектор уже забронирован"
            }

        # 4. Создать запись в bookings
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=duration_minutes)

        result = self.db.execute(text("""
            INSERT INTO bookings (user_id, station_id, connector_id, status, expires_at)
            VALUES (:user_id, :station_id, :connector_id, 'active', :expires_at)
            RETURNING id, created_at
        """), {
            "user_id": user_id,
            "station_id": station_id,
            "connector_id": connector_id,
            "expires_at": expires_at
        }).fetchone()

        if not result:
            return {
                "success": False,
                "error": "booking_failed",
                "message": "Не удалось создать бронирование"
            }

        booking_id = result[0]
        created_at = result[1]

        # 5. Обновить коннектор: status = 'reserved'
        self.db.execute(text("""
            UPDATE connectors
            SET status = 'reserved'
            WHERE station_id = :station_id AND connector_number = :connector_id
        """), {"station_id": station_id, "connector_id": connector_id})

        self.db.commit()

        logger.info(
            f"Бронирование создано: booking_id={booking_id}, "
            f"user={user_id}, station={station_id}, connector={connector_id}, "
            f"expires_at={expires_at.isoformat()}"
        )

        return {
            "success": True,
            "booking": {
                "id": str(booking_id),
                "user_id": user_id,
                "station_id": station_id,
                "connector_id": connector_id,
                "status": "active",
                "created_at": created_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "duration_minutes": duration_minutes
            },
            "message": f"Коннектор забронирован на {duration_minutes} минут"
        }

    def get_active_booking(self, user_id: str) -> Dict[str, Any]:
        """Получить активное бронирование клиента.

        Args:
            user_id: UUID клиента

        Returns:
            Dict с данными бронирования или null
        """
        result = self.db.execute(text("""
            SELECT b.id, b.user_id, b.station_id, b.connector_id,
                   b.status, b.created_at, b.expires_at,
                   s.id as s_id
            FROM bookings b
            LEFT JOIN stations s ON b.station_id = s.id
            WHERE b.user_id = :user_id AND b.status = 'active'
        """), {"user_id": user_id}).fetchone()

        if not result:
            return {
                "success": True,
                "booking": None,
                "message": "Нет активных бронирований"
            }

        now = datetime.now(timezone.utc)
        expires_at = result[6]
        remaining_seconds = max(0, int((expires_at - now).total_seconds()))

        return {
            "success": True,
            "booking": {
                "id": str(result[0]),
                "user_id": str(result[1]),
                "station_id": result[2],
                "connector_id": result[3],
                "status": result[4],
                "created_at": result[5].isoformat(),
                "expires_at": expires_at.isoformat(),
                "remaining_seconds": remaining_seconds
            }
        }

    def cancel_booking(self, booking_id: str, user_id: str) -> Dict[str, Any]:
        """Отменить бронирование.

        Args:
            booking_id: UUID бронирования
            user_id: UUID клиента (проверка владельца)

        Returns:
            Dict с результатом отмены
        """
        # 1. Проверить владельца и статус
        booking = self.db.execute(text("""
            SELECT id, user_id, station_id, connector_id, status
            FROM bookings
            WHERE id = :booking_id AND status = 'active'
            FOR UPDATE
        """), {"booking_id": booking_id}).fetchone()

        if not booking:
            return {
                "success": False,
                "error": "booking_not_found",
                "message": "Активное бронирование не найдено"
            }

        if str(booking[1]) != user_id:
            return {
                "success": False,
                "error": "access_denied",
                "message": "Вы не можете отменить чужое бронирование"
            }

        station_id = booking[2]
        connector_id = booking[3]

        # 2. Обновить status = 'cancelled'
        self.db.execute(text("""
            UPDATE bookings
            SET status = 'cancelled', cancelled_at = NOW()
            WHERE id = :booking_id
        """), {"booking_id": booking_id})

        # 3. Вернуть коннектор в status = 'available'
        self.db.execute(text("""
            UPDATE connectors
            SET status = 'available'
            WHERE station_id = :station_id AND connector_number = :connector_id
        """), {"station_id": station_id, "connector_id": connector_id})

        self.db.commit()

        logger.info(
            f"Бронирование отменено: booking_id={booking_id}, "
            f"user={user_id}, station={station_id}, connector={connector_id}"
        )

        return {
            "success": True,
            "message": "Бронирование отменено"
        }

    def use_booking(
        self,
        station_id: str,
        connector_id: int,
        session_id: str
    ) -> Optional[str]:
        """Пометить бронирование как использованное при старте зарядки.

        Вызывается из ChargingService при старте зарядки.

        Args:
            station_id: ID станции
            connector_id: Номер коннектора
            session_id: UUID сессии зарядки

        Returns:
            ID бронирования если оно было найдено, иначе None
        """
        booking = self.db.execute(text("""
            SELECT id FROM bookings
            WHERE station_id = :station_id
              AND connector_id = :connector_id
              AND status = 'active'
        """), {
            "station_id": station_id,
            "connector_id": connector_id
        }).fetchone()

        if not booking:
            return None

        booking_id = booking[0]

        self.db.execute(text("""
            UPDATE bookings
            SET status = 'used', used_at = NOW(), session_id = :session_id
            WHERE id = :booking_id
        """), {"booking_id": booking_id, "session_id": session_id})

        logger.info(
            f"Бронирование использовано: booking_id={booking_id}, "
            f"session_id={session_id}"
        )

        return str(booking_id)

    def expire_bookings(self) -> List[Dict[str, Any]]:
        """Истечь все просроченные бронирования.

        Вызывается cron job каждую минуту.

        Returns:
            Список истёкших бронирований
        """
        now = datetime.now(timezone.utc)

        # Найти все просроченные активные бронирования
        expired = self.db.execute(text("""
            SELECT id, user_id, station_id, connector_id
            FROM bookings
            WHERE status = 'active' AND expires_at < :now
        """), {"now": now}).fetchall()

        if not expired:
            return []

        expired_list = []
        for row in expired:
            booking_id = row[0]
            station_id = row[2]
            connector_id = row[3]

            # Обновить статус бронирования
            self.db.execute(text("""
                UPDATE bookings SET status = 'expired' WHERE id = :booking_id
            """), {"booking_id": booking_id})

            # Вернуть коннектор в 'available'
            self.db.execute(text("""
                UPDATE connectors
                SET status = 'available'
                WHERE station_id = :station_id
                  AND connector_number = :connector_id
                  AND status = 'reserved'
            """), {"station_id": station_id, "connector_id": connector_id})

            expired_list.append({
                "booking_id": str(booking_id),
                "user_id": str(row[1]),
                "station_id": station_id,
                "connector_id": connector_id
            })

        self.db.commit()

        logger.info(f"Истекло бронирований: {len(expired_list)}")
        for item in expired_list:
            logger.info(
                f"  - booking={item['booking_id']}, "
                f"station={item['station_id']}, connector={item['connector_id']}"
            )

        return expired_list
