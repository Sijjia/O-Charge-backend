"""
GET /station/tariff/{station_id} — текущий тариф станции для PWA.
Возвращает текущую цену, период, расписание на день.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, time as dt_time, timezone
import logging

from app.db.session import get_db
from app.api.v1.schemas.station import StationTariffResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/station/tariff/{station_id}",
    summary="Get station tariff",
    description="Returns the current tariff plan, active rate, and full schedule for a station. Accepts station ID or serial number.",
    response_model=StationTariffResponse,
)
async def get_station_tariff(
    station_id: str,
    db: Session = Depends(get_db),
):
    """Получить текущий тариф станции с расписанием на день."""

    # 1. Данные станции
    station = db.execute(text("""
        SELECT s.id, s.price_per_kwh, s.currency, s.tariff_plan_id,
               tp.name AS plan_name
        FROM stations s
        LEFT JOIN tariff_plans tp ON s.tariff_plan_id = tp.id AND tp.is_active = true
        WHERE s.id = :station_id OR s.serial_number = :station_id
        LIMIT 1
    """), {"station_id": station_id}).fetchone()

    if not station:
        return {"success": False, "error": "station_not_found"}

    now = datetime.now(timezone.utc)
    current_time = now.time()
    currency = station[2] or "KGS"

    # 2. Если есть тарифный план — загружаем правила
    if station[3]:
        rules = db.execute(text("""
            SELECT name, time_start, time_end, price, tariff_type,
                   connector_type, is_weekend, priority
            FROM tariff_rules
            WHERE tariff_plan_id = :plan_id AND is_active = true
            ORDER BY priority DESC, time_start
        """), {"plan_id": station[3]}).fetchall()

        if rules:
            schedule = []
            current_rate = None
            current_period = None
            next_change_at = None
            next_rate = None

            def _parse_time(val) -> dt_time | None:
                """Parse time value — may be datetime.time or str like '08:00:00'."""
                if val is None:
                    return None
                if isinstance(val, dt_time):
                    return val
                try:
                    parts = str(val).split(":")
                    return dt_time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
                except (ValueError, IndexError):
                    return None

            for r in rules:
                name = r[0]
                t_start = _parse_time(r[1])
                t_end = _parse_time(r[2])
                price = float(r[3])

                time_label = ""
                if t_start and t_end:
                    time_label = f"{t_start.strftime('%H:%M')}-{t_end.strftime('%H:%M')}"

                schedule.append({
                    "name": name,
                    "time": time_label,
                    "rate": price,
                    "tariff_type": r[4] or "energy",
                    "connector_type": r[5] or "any",
                })

                # Проверяем попадание текущего времени
                if t_start and t_end:
                    if t_start < t_end:
                        in_range = t_start <= current_time < t_end
                    else:
                        in_range = current_time >= t_start or current_time < t_end

                    if in_range and current_rate is None:
                        current_rate = price
                        current_period = name

                    # Ищем следующее изменение (ближайший time_start после current_time)
                    if t_start > current_time and next_change_at is None:
                        next_change_at = t_start.strftime("%H:%M")
                        next_rate = price

            if current_rate is None:
                # Fallback на первое правило
                current_rate = float(rules[0][3])
                current_period = rules[0][0]

            return {
                "success": True,
                "data": {
                    "station_id": station_id,
                    "tariff_plan": station[4],
                    "current_rate": current_rate,
                    "current_period": current_period,
                    "next_change_at": next_change_at,
                    "next_rate": next_rate,
                    "currency": currency,
                    "schedule": schedule,
                }
            }

    # 3. Нет тарифного плана — индивидуальная цена станции
    base_rate = float(station[1]) if station[1] else 13.5

    # Ночная скидка
    from app.services.pricing_service import PricingService
    effective_rate, is_night = PricingService.get_effective_rate(base_rate, current_time)

    return {
        "success": True,
        "data": {
            "station_id": station_id,
            "tariff_plan": None,
            "current_rate": effective_rate,
            "current_period": "Ночной (-20%)" if is_night else "Стандартный",
            "next_change_at": "06:00" if is_night else "23:00",
            "next_rate": base_rate if is_night else round(base_rate * 0.8, 2),
            "currency": currency,
            "schedule": [
                {"name": "Дневной", "time": "06:00-23:00", "rate": base_rate, "tariff_type": "energy", "connector_type": "any"},
                {"name": "Ночной (-20%)", "time": "23:00-06:00", "rate": round(base_rate * 0.8, 2), "tariff_type": "energy", "connector_type": "any"},
            ],
        }
    }
