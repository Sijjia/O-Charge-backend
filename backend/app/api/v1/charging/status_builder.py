"""
Статус мониторинг: запрос данных, energy calc, progress, построение ответа
"""
from typing import Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)


class ChargingStatusBuilder:
    """Построение статуса сессии зарядки"""

    def __init__(self, db: Session):
        self.db = db

    async def build_status(self, session_id: str) -> Dict[str, Any]:
        """Получить полный статус сессии зарядки с OCPP данными (по подходу Voltera)"""

        logger.info(f"Запрос статуса зарядки для сессии: {session_id}")

        try:
            session_query = text("""
                SELECT
                    cs.id as session_id,
                    cs.user_id,
                    cs.station_id,
                    cs.start_time,
                    cs.stop_time,
                    cs.energy as session_energy,
                    cs.amount,
                    cs.reserved_amount,
                    cs.status,
                    cs.transaction_id,
                    cs.limit_type,
                    cs.limit_value,
                    s.price_per_kwh,
                    s.session_fee,

                    -- Данные транзакции
                    ot.id as ocpp_transaction_id,
                    ot.transaction_id as ocpp_tx_id,
                    ot.meter_start,
                    ot.meter_stop,
                    ot.status as ocpp_status,

                    -- Последние meter values через LATERAL
                    mv.energy_active_import_register as current_meter,
                    mv.power_active_import as power_w,
                    mv.current_import,
                    mv.voltage,
                    mv.soc as ev_battery_soc,
                    mv.timestamp as meter_timestamp,

                    COALESCE(
                        NULLIF(cs.energy, 0),
                        (mv.energy_active_import_register - ot.meter_start) / 1000.0,
                        0
                    ) as energy_kwh,

                    cs.connector_id as session_connector_id,
                    cs.tariff_rate

                FROM charging_sessions cs
                LEFT JOIN stations s ON cs.station_id = s.id
                LEFT JOIN ocpp_transactions ot ON cs.id = ot.charging_session_id
                LEFT JOIN LATERAL (
                    SELECT * FROM ocpp_meter_values
                    WHERE ocpp_transaction_id = ot.id
                    ORDER BY timestamp DESC
                    LIMIT 1
                ) mv ON true
                WHERE cs.id = :session_id
            """)

            session_result = self.db.execute(session_query, {"session_id": session_id})
            row = session_result.fetchone()

            if not row:
                logger.warning(f"Сессия {session_id} не найдена в БД")
                return {
                    "success": False,
                    "error": "session_not_found",
                    "message": "Сессия зарядки не найдена"
                }

            (
                session_id_db, user_id, station_id, start_time, stop_time,
                session_energy, amount, reserved_amount, status, transaction_id,
                limit_type, limit_value, price_per_kwh, session_fee,
                ocpp_transaction_id, ocpp_tx_id, meter_start, meter_stop, ocpp_status,
                current_meter, power_w, current_import, voltage, ev_battery_soc, meter_timestamp,
                energy_kwh, session_connector_id, tariff_rate
            ) = row

            energy_kwh = float(energy_kwh) if energy_kwh else 0.0
            price_per_kwh = float(price_per_kwh) if price_per_kwh else 13.5
            session_fee = float(session_fee) if session_fee else 0.0
            reserved_amount = float(reserved_amount) if reserved_amount else 0.0
            limit_value = float(limit_value) if limit_value else 0.0
            tariff_rate = float(tariff_rate) if tariff_rate else price_per_kwh
            # power_active_import хранится в Ваттах (W) — OCPP Power.Active.Import
            # Конвертируем W → kW для отображения
            power_kw = float(power_w) / 1000.0 if power_w else 0.0

            # Для завершённых сессий берём amount из БД (финальная сумма из StopTransaction)
            # Для активных — считаем по tariff_rate (из сессии, уже с учётом ночной скидки)
            if status == 'stopped' and amount is not None and float(amount) > 0:
                current_amount = float(amount)
            else:
                current_amount = energy_kwh * tariff_rate

            progress_percent = 0.0
            if limit_type == "energy" and limit_value > 0:
                progress_percent = min(100, (energy_kwh / limit_value) * 100)
            elif limit_type == "amount" and limit_value > 0:
                progress_percent = min(100, (current_amount / limit_value) * 100)

            duration_seconds = 0
            if start_time:
                end_time = stop_time or datetime.now(timezone.utc)
                duration_seconds = int((end_time - start_time).total_seconds())

            # Получаем serial_number для проверки онлайна (Redis может хранить id или serial_number)
            sn_row = self.db.execute(text(
                "SELECT serial_number FROM stations WHERE id = :sid"
            ), {"sid": station_id}).fetchone()
            serial_number = sn_row[0] if sn_row else None
            station_online = await self._check_station_online(station_id, serial_number)

            logger.info(f"Статус получен: energy={energy_kwh:.3f} кВт*ч, power={power_kw:.1f} кВт, online={station_online}")

            return {
                "success": True,
                "session": {
                    "id": session_id_db,
                    "session_id": session_id_db,
                    "status": status or "preparing",
                    "station_id": station_id,
                    "connector_id": session_connector_id or 1,
                    "ocpp_transaction_id": ocpp_transaction_id,

                    "energy_consumed": round(energy_kwh, 3),
                    "energy_kwh": round(energy_kwh, 3),
                    "current_cost": round(current_amount, 2),
                    "current_amount": round(current_amount, 2),
                    "power_kw": round(power_kw, 2),

                    "reserved_amount": round(reserved_amount, 2),
                    "rate_per_kwh": round(tariff_rate, 2),
                    "session_fee": round(session_fee, 2),

                    "charging_duration_minutes": duration_seconds // 60,
                    "duration_seconds": duration_seconds,

                    "limit_type": limit_type or "none",
                    "limit_value": round(limit_value, 2),
                    "limit_reached": progress_percent >= 100,
                    "limit_percentage": round(progress_percent, 1),
                    "progress_percent": round(progress_percent, 1),

                    "meter_start": float(meter_start) if meter_start else 0,
                    "meter_current": float(current_meter) if current_meter else 0,

                    "ev_battery_soc": int(ev_battery_soc) if ev_battery_soc else None,

                    "station_online": station_online,

                    "start_time": start_time.isoformat() if start_time else None,
                    "stop_time": stop_time.isoformat() if stop_time else None,
                }
            }

        except Exception as e:
            logger.error(f"Критическая ошибка при получении статуса зарядки: {e}", exc_info=True)
            return {
                "success": False,
                "error": "internal_error",
                "message": "Внутренняя ошибка сервера"
            }

    async def _check_station_online(self, station_id: str, serial_number: str = None) -> bool:
        """Проверка онлайн статуса станции (по id и serial_number)"""
        try:
            from ocpp_ws_server.redis_manager import redis_manager
            connected_stations = await redis_manager.get_stations()
            return station_id in connected_stations or (serial_number and serial_number in connected_stations)
        except Exception as e:
            logger.warning(f"Не удалось проверить статус станции {station_id}: {e}")
            return False
