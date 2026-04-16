"""
Сервис партнёрского модуля
Red Petroleum EV — Partner Revenue Share + Partner API (P1 #8-9)

Партнёр = владелец станций (users с ролью admin/operator).
Связь: stations.user_id → users.id → partners.user_id
"""
from typing import Optional, Dict, Any, List
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

# Revenue share по умолчанию: 80% партнёру, 20% платформе
DEFAULT_PARTNER_SHARE_PERCENT = Decimal("80.00")


class PartnerService:
    """Сервис для работы с партнёрами (владельцами станций)"""

    def __init__(self, db: Session):
        self.db = db

    def get_partner_by_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Получить партнёра по user_id"""
        result = self.db.execute(text("""
            SELECT id, user_id, company_name, inn, contract_number, contract_date,
                   revenue_share_percent, contact_name, contact_phone, contact_email,
                   status, created_at
            FROM partners
            WHERE user_id = :user_id AND status = 'active'
        """), {"user_id": user_id}).fetchone()

        if not result:
            return None

        return {
            "id": str(result[0]),
            "user_id": str(result[1]),
            "company_name": result[2],
            "inn": result[3],
            "contract_number": result[4],
            "contract_date": result[5].isoformat() if result[5] else None,
            "revenue_share_percent": float(result[6]),
            "contact_name": result[7],
            "contact_phone": result[8],
            "contact_email": result[9],
            "status": result[10],
            "created_at": result[11].isoformat() if result[11] else None,
        }

    def get_partner_by_station(self, station_id: str) -> Optional[Dict[str, Any]]:
        """Получить партнёра по станции.

        Приоритет: stations.partner_id → locations.partner_id (наследование)
        """
        result = self.db.execute(text("""
            SELECT p.id, p.user_id, p.revenue_share_percent
            FROM stations s
            JOIN partners p ON p.id = COALESCE(s.partner_id, (
                SELECT l.partner_id FROM locations l WHERE l.id = s.location_id
            ))
            WHERE s.id = :station_id AND p.status = 'active'
        """), {"station_id": station_id}).fetchone()

        if not result:
            return None

        return {
            "id": str(result[0]),
            "user_id": str(result[1]),
            "revenue_share_percent": float(result[2]),
        }

    def calculate_revenue_split(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Рассчитать partner_share/platform_share при завершении сессии.

        Вызывается из ChargingService.stop_charging_session() после расчёта actual_cost.
        """
        # Получаем сессию с actual_cost
        session = self.db.execute(text("""
            SELECT cs.id, cs.station_id, cs.amount
            FROM charging_sessions cs
            WHERE cs.id = :session_id
        """), {"session_id": session_id}).fetchone()

        if not session or not session[2]:
            return None

        station_id = session[1]
        total_amount = Decimal(str(session[2]))

        # Найти партнёра станции
        partner = self.get_partner_by_station(station_id)
        if not partner:
            logger.debug(f"Партнёр не найден для станции {station_id}, revenue split не применяется")
            return None

        partner_id = partner["id"]
        share_percent = Decimal(str(partner["revenue_share_percent"]))

        partner_share = (total_amount * share_percent / Decimal("100")).quantize(Decimal("0.01"))
        platform_share = (total_amount - partner_share).quantize(Decimal("0.01"))

        # Записываем в charging_sessions
        self.db.execute(text("""
            UPDATE charging_sessions
            SET partner_id = :partner_id,
                partner_share = :partner_share,
                platform_share = :platform_share
            WHERE id = :session_id
        """), {
            "partner_id": partner_id,
            "partner_share": float(partner_share),
            "platform_share": float(platform_share),
            "session_id": session_id,
        })

        logger.info(
            f"Revenue split для сессии {session_id}: "
            f"total={total_amount}, partner={partner_share} ({share_percent}%), "
            f"platform={platform_share}"
        )

        return {
            "partner_id": partner_id,
            "partner_share": float(partner_share),
            "platform_share": float(platform_share),
            "share_percent": float(share_percent),
        }

    def get_dashboard(self, user_id: str) -> Dict[str, Any]:
        """KPI партнёра: кол-во станций, доход, активные сессии"""
        partner = self.get_partner_by_user(user_id)
        if not partner:
            return {"success": False, "error": "partner_not_found", "message": "Партнёр не найден"}

        partner_id = partner["id"]

        # Партнёрские станции — базовое условие
        partner_stations_cond = """
            (s.partner_id = :partner_id
             OR (s.partner_id IS NULL AND s.location_id IN (
                 SELECT l.id FROM locations l WHERE l.partner_id = :partner_id
             )))
        """

        # Считаем KPI одним запросом (через прямой partner_id)
        kpi = self.db.execute(text(f"""
            SELECT
                -- Количество станций партнёра (прямые + наследованные от локаций)
                (SELECT COUNT(*) FROM stations s
                 WHERE {partner_stations_cond}
                ) as total_stations,
                -- Станции онлайн (available и без активных сессий)
                (SELECT COUNT(*) FROM stations s
                 WHERE s.is_available = true AND s.status = 'active'
                   AND {partner_stations_cond}
                   AND NOT EXISTS (
                       SELECT 1 FROM charging_sessions cs2
                       WHERE cs2.station_id = s.id AND cs2.status = 'started'
                   )
                ) as online_stations,
                -- Станции в процессе зарядки
                (SELECT COUNT(DISTINCT cs2.station_id) FROM charging_sessions cs2
                 JOIN stations s ON cs2.station_id = s.id
                 WHERE cs2.status = 'started' AND {partner_stations_cond}
                ) as stations_charging,
                -- Станции оффлайн
                (SELECT COUNT(*) FROM stations s
                 WHERE (s.is_available = false OR s.status != 'active')
                   AND {partner_stations_cond}
                ) as stations_offline,
                -- Сессии за сегодня
                (SELECT COUNT(*) FROM charging_sessions cs
                 WHERE cs.partner_id = :partner_id AND cs.status = 'stopped'
                 AND cs.stop_time >= date_trunc('day', NOW())) as sessions_today,
                -- Сессии за месяц
                (SELECT COUNT(*) FROM charging_sessions cs
                 WHERE cs.partner_id = :partner_id AND cs.status = 'stopped'
                 AND cs.stop_time >= date_trunc('month', NOW())) as sessions_month,
                -- Доход за сегодня
                (SELECT COALESCE(SUM(cs.partner_share), 0) FROM charging_sessions cs
                 WHERE cs.partner_id = :partner_id AND cs.status = 'stopped'
                 AND cs.stop_time >= date_trunc('day', NOW())) as revenue_today,
                -- Доход за неделю
                (SELECT COALESCE(SUM(cs.partner_share), 0) FROM charging_sessions cs
                 WHERE cs.partner_id = :partner_id AND cs.status = 'stopped'
                 AND cs.stop_time >= date_trunc('day', NOW()) - INTERVAL '7 days') as revenue_week,
                -- Доход за месяц
                (SELECT COALESCE(SUM(cs.partner_share), 0) FROM charging_sessions cs
                 WHERE cs.partner_id = :partner_id AND cs.status = 'stopped'
                 AND cs.stop_time >= date_trunc('month', NOW())) as revenue_month,
                -- Общий доход
                (SELECT COALESCE(SUM(cs.partner_share), 0) FROM charging_sessions cs
                 WHERE cs.partner_id = :partner_id AND cs.status = 'stopped') as revenue_total,
                -- Энергия за сегодня
                (SELECT COALESCE(SUM(cs.energy), 0) FROM charging_sessions cs
                 WHERE cs.partner_id = :partner_id AND cs.status = 'stopped'
                 AND cs.stop_time >= date_trunc('day', NOW())) as energy_today,
                -- Энергия за месяц
                (SELECT COALESCE(SUM(cs.energy), 0) FROM charging_sessions cs
                 WHERE cs.partner_id = :partner_id AND cs.status = 'stopped'
                 AND cs.stop_time >= date_trunc('month', NOW())) as energy_month
        """), {"partner_id": partner_id}).fetchone()

        return {
            "success": True,
            "data": {
                "stations_total": kpi[0] or 0,
                "stations_online": kpi[1] or 0,
                "stations_charging": kpi[2] or 0,
                "stations_offline": kpi[3] or 0,
                "sessions_today": kpi[4] or 0,
                "sessions_month": kpi[5] or 0,
                "revenue_today": round(float(kpi[6] or 0), 2),
                "revenue_week": round(float(kpi[7] or 0), 2),
                "revenue_month": round(float(kpi[8] or 0), 2),
                "revenue_total": round(float(kpi[9] or 0), 2),
                "energy_today_kwh": round(float(kpi[10] or 0), 2),
                "energy_month_kwh": round(float(kpi[11] or 0), 2),
                "partner_share_percent": partner["revenue_share_percent"],
                "partner_revenue_month": round(float(kpi[8] or 0) * float(partner["revenue_share_percent"] or 80) / 100, 2),
            },
        }

    def get_stations(self, user_id: str) -> Dict[str, Any]:
        """Станции партнёра со статусами"""
        partner = self.get_partner_by_user(user_id)
        if not partner:
            return {"success": False, "error": "partner_not_found", "message": "Партнёр не найден"}

        rows = self.db.execute(text("""
            SELECT s.id, s.serial_number, s.model, s.manufacturer,
                   s.power_capacity, s.status, s.is_available, s.last_heartbeat_at,
                   s.price_per_kwh,
                   l.name as location_name, l.address, l.city,
                   l.latitude, l.longitude,
                   (SELECT COUNT(*) FROM connectors c WHERE c.station_id = s.id) as connector_count,
                   (SELECT COUNT(*) FROM charging_sessions cs
                    WHERE cs.station_id = s.id AND cs.status = 'started') as active_sessions
            FROM stations s
            LEFT JOIN locations l ON s.location_id = l.id
            WHERE s.partner_id = :partner_id
               OR (s.partner_id IS NULL AND l.partner_id = :partner_id)
            ORDER BY s.id
        """), {"partner_id": partner["id"]}).fetchall()

        stations = []
        for row in rows:
            is_available = bool(row[6])
            admin_status = row[5]
            active_sessions = row[15] or 0

            if admin_status != 'active':
                display_status = 'maintenance'
            elif active_sessions > 0:
                display_status = 'charging'
            elif is_available:
                display_status = 'online'
            else:
                display_status = 'offline'

            model_str = f"{row[3]} {row[2]}" if row[3] and row[2] else (row[2] or "")

            stations.append({
                "id": row[0],
                "serial_number": row[1] or row[0],
                "name": row[9] or row[0],
                "address": row[10],
                "city": row[11],
                "latitude": row[12],
                "longitude": row[13],
                "status": display_status,
                "model": model_str,
                "power_kw": float(row[4]) if row[4] else None,
                "connectors": row[14] or 0,
                "price_per_kwh": float(row[8]) if row[8] else None,
                "last_heartbeat": row[7].isoformat() if row[7] else None,
            })

        return {
            "success": True,
            "data": stations,
        }

    def get_sessions(
        self,
        user_id: str,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        station_id: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> Dict[str, Any]:
        """История сессий на станциях партнёра с пагинацией"""
        partner = self.get_partner_by_user(user_id)
        if not partner:
            return {"success": False, "error": "partner_not_found", "message": "Партнёр не найден"}

        partner_id = partner["id"]

        # Строим WHERE условия — фильтр по partner_id, а не user_id
        conditions = ["cs.partner_id = :partner_id", "cs.status = 'stopped'"]
        params: Dict[str, Any] = {"partner_id": partner_id}

        if from_date:
            conditions.append("cs.stop_time >= :from_date")
            params["from_date"] = datetime.combine(from_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        if to_date:
            conditions.append("cs.stop_time < :to_date + INTERVAL '1 day'")
            params["to_date"] = datetime.combine(to_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        if station_id:
            conditions.append("cs.station_id = :station_id")
            params["station_id"] = station_id

        where_clause = " AND ".join(conditions)

        # Общее количество
        count_result = self.db.execute(text(f"""
            SELECT COUNT(*)
            FROM charging_sessions cs
            WHERE {where_clause}
        """), params).fetchone()
        total = count_result[0] if count_result else 0

        # Данные с пагинацией
        offset = (page - 1) * per_page
        params["limit"] = per_page
        params["offset"] = offset

        rows = self.db.execute(text(f"""
            SELECT cs.id, cs.station_id, cs.start_time, cs.stop_time,
                   cs.energy, cs.amount, cs.partner_share,
                   cs.connector_id,
                   l.name as location_name
            FROM charging_sessions cs
            JOIN stations s ON cs.station_id = s.id
            LEFT JOIN locations l ON s.location_id = l.id
            WHERE {where_clause}
            ORDER BY cs.stop_time DESC
            LIMIT :limit OFFSET :offset
        """), params).fetchall()

        sessions = []
        for row in rows:
            duration_minutes = 0
            if row[2] and row[3]:
                duration_minutes = int((row[3] - row[2]).total_seconds() / 60)

            sessions.append({
                "id": str(row[0]),
                "station_id": row[1],
                "station_name": row[8] or row[1],
                "connector_id": row[7],
                "status": "completed",
                "energy_kwh": round(float(row[4]), 3) if row[4] else 0,
                "amount": round(float(row[5]), 2) if row[5] else 0,
                "partner_share": round(float(row[6]), 2) if row[6] else 0,
                "started_at": row[2].isoformat() if row[2] else None,
                "ended_at": row[3].isoformat() if row[3] else None,
                "duration_minutes": duration_minutes,
            })

        return {
            "success": True,
            "data": sessions,
            "total": total,
            "page": page,
            "per_page": per_page,
        }

    def get_revenue(
        self,
        user_id: str,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        group_by: str = "day",
    ) -> Dict[str, Any]:
        """Доход партнёра за период с группировкой (day/week/month)"""
        partner = self.get_partner_by_user(user_id)
        if not partner:
            return {"success": False, "error": "partner_not_found", "message": "Партнёр не найден"}

        partner_id = partner["id"]

        # Определяем функцию группировки
        group_func_map = {
            "hour": "date_trunc('hour', cs.stop_time)",
            "day": "date_trunc('day', cs.stop_time)",
            "week": "date_trunc('week', cs.stop_time)",
            "month": "date_trunc('month', cs.stop_time)",
        }
        group_func = group_func_map.get(group_by, group_func_map["day"])

        conditions = ["cs.partner_id = :partner_id", "cs.status = 'stopped'"]
        params: Dict[str, Any] = {"partner_id": partner_id}

        if from_date:
            conditions.append("cs.stop_time >= :from_date")
            params["from_date"] = datetime.combine(from_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        if to_date:
            conditions.append("cs.stop_time < :to_date + INTERVAL '1 day'")
            params["to_date"] = datetime.combine(to_date, datetime.min.time()).replace(tzinfo=timezone.utc)

        where_clause = " AND ".join(conditions)

        # Общие итоги за период
        totals = self.db.execute(text(f"""
            SELECT
                COUNT(*) as sessions_count,
                COALESCE(SUM(cs.amount), 0) as total_revenue,
                COALESCE(SUM(cs.partner_share), 0) as total_partner_share,
                COALESCE(SUM(cs.platform_share), 0) as total_platform_share,
                COALESCE(SUM(cs.energy), 0) as total_energy
            FROM charging_sessions cs
            WHERE {where_clause}
        """), params).fetchone()

        # Группировка по периодам
        rows = self.db.execute(text(f"""
            SELECT
                {group_func} as period,
                COUNT(*) as sessions_count,
                COALESCE(SUM(cs.amount), 0) as total_revenue,
                COALESCE(SUM(cs.partner_share), 0) as partner_share,
                COALESCE(SUM(cs.platform_share), 0) as platform_share,
                COALESCE(SUM(cs.energy), 0) as energy_kwh
            FROM charging_sessions cs
            WHERE {where_clause}
            GROUP BY {group_func}
            ORDER BY period
        """), params).fetchall()

        data = []
        for row in rows:
            data.append({
                "date": row[0].isoformat() if row[0] else None,
                "revenue": round(float(row[2] or 0), 2),
                "energy_kwh": round(float(row[5] or 0), 2),
                "sessions": row[1] or 0,
                "partner_share": round(float(row[3] or 0), 2),
            })

        return {
            "success": True,
            "data": data,
            "summary": {
                "sessions_count": totals[0] or 0,
                "total_revenue": round(float(totals[1] or 0), 2),
                "partner_share": round(float(totals[2] or 0), 2),
                "platform_share": round(float(totals[3] or 0), 2),
                "total_energy_kwh": round(float(totals[4] or 0), 2),
                "revenue_share_percent": partner["revenue_share_percent"],
            },
            "group_by": group_by,
        }
