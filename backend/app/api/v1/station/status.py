from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from app.db.session import get_db
from ocpp_ws_server.redis_manager import redis_manager
from app.api.v1.schemas.station import StationStatusResponse

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get(
    "/station/status/{station_id}",
    summary="Get station status",
    description="Returns real-time station status including online state, connector availability, tariff info. Accepts station ID or serial number.",
    response_model=StationStatusResponse,
)
async def get_station_status(
    station_id: str, 
    db: Session = Depends(get_db)
):
    """🏢 Статус станции и коннекторов"""
    try:
        # Получаем данные станции с локацией через JOIN
        # Ищем по id ИЛИ по serial_number (фронт может передать любой)
        result = db.execute(text("""
            SELECT
                s.id,
                s.serial_number,
                s.model,
                s.manufacturer,
                s.status,
                s.power_capacity,
                s.connector_types,
                s.connectors_count,
                s.price_per_kwh,
                s.session_fee,
                s.currency,
                l.name as location_name,
                l.address as location_address,
                l.status as location_status,
                s.location_id,
                l.latitude,
                l.longitude
            FROM stations s
            LEFT JOIN locations l ON s.location_id = l.id
            WHERE s.id = :station_id OR s.serial_number = :station_id
            LIMIT 1
        """), {"station_id": station_id})

        station_data = result.fetchone()

        # Используем реальный station_id из БД для последующих запросов
        if station_data:
            db_station_id = station_data[0]
            serial_number = station_data[1]
        else:
            return {
                "success": False,
                "error": "station_not_found",
                "message": "Станция не найдена"
            }

        # Проверяем подключение станции (Redis может хранить id или serial_number)
        connected_stations = await redis_manager.get_stations()
        is_online = db_station_id in connected_stations or serial_number in connected_stations

        station_id = db_station_id
        
        # Получаем последний heartbeat
        last_heartbeat_row = db.execute(text("""
            SELECT last_heartbeat
            FROM ocpp_station_status
            WHERE station_id = :station_id
            ORDER BY last_heartbeat DESC
            LIMIT 1
        """), {"station_id": station_id}).fetchone()
        last_heartbeat_at = (
            last_heartbeat_row[0].isoformat() if last_heartbeat_row and last_heartbeat_row[0] else None
        )
        
        # Получаем статус коннекторов с SoC из активных сессий
        connectors_result = db.execute(text("""
            SELECT
                c.connector_number, c.connector_type, c.power_kw, c.status, c.error_code,
                mv.soc as current_progress
            FROM connectors c
            LEFT JOIN charging_sessions cs
                ON cs.station_id = c.station_id
                AND cs.connector_id = c.connector_number
                AND cs.status = 'started'
            LEFT JOIN ocpp_transactions ot
                ON cs.id = ot.charging_session_id
            LEFT JOIN LATERAL (
                SELECT soc FROM ocpp_meter_values
                WHERE ocpp_transaction_id = ot.id
                ORDER BY timestamp DESC
                LIMIT 1
            ) mv ON true
            WHERE c.station_id = :station_id
            ORDER BY c.connector_number
        """), {"station_id": station_id})
        
        connectors = []
        available_count = 0
        occupied_count = 0
        faulted_count = 0
        
        connector_rows = connectors_result.fetchall()
        logger.info(f"Station {station_id}: найдено {len(connector_rows)} коннекторов")
        
        for conn in connector_rows:
            connector_status = conn[3]  # status
            soc = int(conn[5]) if conn[5] is not None else None
            logger.info(f"Коннектор {conn[0]}: тип={conn[1]}, мощность={conn[2]}, статус={connector_status}, soc={soc}")

            # Статусы коннекторов с локализацией
            if connector_status == "available":
                connector_available = is_online
                available_count += 1
                status_text = "Свободен"
                ocpp_status = "Available"
            elif connector_status == "occupied":
                connector_available = False
                occupied_count += 1
                status_text = "Идет зарядка" if soc is not None else "Занят"
                ocpp_status = "Charging" if soc is not None else "Occupied"
            elif connector_status == "faulted":
                connector_available = False
                faulted_count += 1
                status_text = "Неисправен"
                ocpp_status = "Faulted"
            else:
                connector_available = False
                faulted_count += 1
                status_text = "Недоступен"
                ocpp_status = "Unavailable"

            connectors.append({
                "connector_number": conn[0],
                "connector_type": conn[1],
                "max_power": float(conn[2]) if conn[2] else None,
                "status": status_text,
                "status_name": status_text,
                "current_progress": soc,
                "id": conn[0],
                "type": conn[1],
                "power_kw": float(conn[2]) if conn[2] else None,
                "available": connector_available,
                "error": conn[4] if conn[4] and conn[4] != "NoError" else None,
            })
        
        # Формируем ответ
        return {
            "success": True,
            "station_id": station_id,
            "serial_number": station_data[1],
            "model": station_data[2],
            "manufacturer": station_data[3],
            
            # Статусы
            "online": is_online,
            "station_status": station_data[4],  # active/maintenance/inactive
            "location_status": station_data[13],  # active/maintenance/inactive
            "available_for_charging": is_online and station_data[4] == "active" and available_count > 0,
            "last_heartbeat_at": last_heartbeat_at,
            
            # Локация
            "location_id": station_data[14],
            "location_name": station_data[11],
            "location_address": station_data[12],
            "location_coordinates": {
                "lat": float(station_data[15]) if station_data[15] else None,
                "lng": float(station_data[16]) if station_data[16] else None,
            },
            "station_display_name": station_data[1],  # serial_number
            
            # Коннекторы
            "connectors": connectors,
            "total_connectors": station_data[7],  # connectors_count
            "available_connectors": available_count,
            "occupied_connectors": occupied_count,
            "faulted_connectors": faulted_count,
            
            # Тарифы
            "tariff_per_kwh": float(station_data[8]) if station_data[8] else 13.5,
            "tariff_rub_kwh": float(station_data[8]) if station_data[8] else 13.5,  # deprecated, use tariff_per_kwh
            "session_fee": float(station_data[9]) if station_data[9] else 0.0,
            "currency": station_data[10] or "KGS",
            "working_hours": "Круглосуточно",
            
            "message": "Станция работает" if is_online and station_data[4] == "active" 
                      else "Станция на обслуживании" if station_data[4] == "maintenance"
                      else "Станция недоступна"
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения статуса станции {station_id}: {e}", exc_info=True)
        return {
            "success": False,
            "error": "internal_error",
            "message": "Внутренняя ошибка сервера",
            "status": 500
        }