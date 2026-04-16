"""
Эндпоинты для мобильного приложения — плоский список станций
Формат согласован с клиентом (мобильная команда)
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
import logging

from app.db.session import get_db
from ocpp_ws_server.redis_manager import redis_manager

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class LocationCoordinates(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None


class MobileStationItem(BaseModel):
    id: str
    location_id: Optional[str] = None
    max_power: Optional[float] = None
    tariff_per_kwh: Optional[float] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location_coordinates: LocationCoordinates = LocationCoordinates()
    is_online: bool = False
    total_connectors: int = 0
    occupied_connectors: int = 0
    available_connectors: int = 0
    faulted_connectors: int = 0


class MobileStationsListResponse(BaseModel):
    success: bool = True
    data: List[MobileStationItem] = []


class MobileConnectorItem(BaseModel):
    connector_number: int
    connector_type: Optional[str] = None
    max_power: Optional[float] = None
    status_name: str = "Неизвестно"
    status: str = "Unavailable"
    current_progress: Optional[int] = None


class MobileStationDetail(BaseModel):
    id: str
    station_display_name: Optional[str] = None
    location_id: Optional[str] = None
    max_power: Optional[float] = None
    tariff_per_kwh: Optional[float] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location_coordinates: LocationCoordinates = LocationCoordinates()
    is_online: bool = False
    total_connectors: int = 0
    occupied_connectors: int = 0
    available_connectors: int = 0
    faulted_connectors: int = 0
    connectors: List[MobileConnectorItem] = []


class MobileStationDetailResponse(BaseModel):
    success: bool = True
    data: Optional[MobileStationDetail] = None


# --- Status name mapping ---

STATUS_NAMES = {
    "available": "Свободен",
    "occupied": "Занят",
    "faulted": "Неисправен",
    "unavailable": "Недоступен",
}


# --- Endpoints ---

@router.get(
    "/stations",
    response_model=MobileStationsListResponse,
    summary="List all stations (mobile format)",
    description="Returns a flat list of stations with embedded location data, connector counts, and online status.",
)
async def get_stations_list(db: Session = Depends(get_db)):
    """Плоский список станций для мобильного приложения"""
    try:
        query = text("""
            SELECT
                s.id,
                s.location_id,
                s.power_capacity,
                s.price_per_kwh,
                l.name as location_name,
                l.address as location_address,
                l.latitude,
                l.longitude,
                s.serial_number,
                s.last_heartbeat_at,
                COUNT(c.id) as total_connectors,
                COUNT(CASE WHEN c.status = 'occupied' THEN 1 END) as occupied_connectors,
                COUNT(CASE WHEN c.status = 'available' THEN 1 END) as available_connectors,
                COUNT(CASE WHEN c.status = 'faulted' THEN 1 END) as faulted_connectors
            FROM stations s
            LEFT JOIN locations l ON s.location_id = l.id
            LEFT JOIN connectors c ON s.id = c.station_id
            WHERE s.status != 'inactive'
            GROUP BY s.id, s.location_id, s.power_capacity, s.price_per_kwh,
                     l.name, l.address, l.latitude, l.longitude,
                     s.serial_number, s.last_heartbeat_at
            ORDER BY l.name NULLS LAST, s.id
        """)

        result = db.execute(query)
        rows = result.fetchall()

        # Get online stations from Redis
        connected_stations = await redis_manager.get_stations()

        data = []
        for row in rows:
            station_id = row[0]
            serial_number = row[8]
            is_online = station_id in connected_stations or serial_number in connected_stations

            data.append(MobileStationItem(
                id=station_id,
                location_id=row[1],
                max_power=float(row[2]) if row[2] else None,
                tariff_per_kwh=float(row[3]) if row[3] else None,
                location_name=row[4],
                location_address=row[5],
                location_coordinates=LocationCoordinates(
                    lat=float(row[6]) if row[6] else None,
                    lng=float(row[7]) if row[7] else None,
                ),
                is_online=is_online,
                total_connectors=row[10] or 0,
                occupied_connectors=row[11] or 0,
                available_connectors=row[12] or 0,
                faulted_connectors=row[13] or 0,
            ))

        return MobileStationsListResponse(success=True, data=data)

    except Exception as e:
        logger.error(f"Ошибка получения списка станций: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


@router.get(
    "/stations/{station_id}",
    response_model=MobileStationDetailResponse,
    summary="Get station details (mobile format)",
    description="Returns station details with connectors, status names, and charging progress (SoC).",
)
async def get_station_detail(station_id: str, db: Session = Depends(get_db)):
    """Детали станции с коннекторами для мобильного приложения"""
    try:
        # Station + location data
        station_result = db.execute(text("""
            SELECT
                s.id, s.serial_number, s.location_id,
                s.power_capacity, s.price_per_kwh,
                l.name as location_name, l.address as location_address,
                l.latitude, l.longitude
            FROM stations s
            LEFT JOIN locations l ON s.location_id = l.id
            WHERE s.id = :station_id OR s.serial_number = :station_id
            LIMIT 1
        """), {"station_id": station_id})

        station_row = station_result.fetchone()
        if not station_row:
            return MobileStationDetailResponse(success=False, data=None)

        db_station_id = station_row[0]
        serial_number = station_row[1]

        # Online check via Redis
        connected_stations = await redis_manager.get_stations()
        is_online = db_station_id in connected_stations or serial_number in connected_stations

        # Connectors with active session SoC
        connectors_result = db.execute(text("""
            SELECT
                c.connector_number,
                c.connector_type,
                c.power_kw,
                c.status,
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
        """), {"station_id": db_station_id})

        connectors = []
        available_count = 0
        occupied_count = 0
        faulted_count = 0

        for conn in connectors_result.fetchall():
            connector_status = conn[3] or "unavailable"
            soc = int(conn[4]) if conn[4] is not None else None

            # Status name with charging detection
            if connector_status == "available":
                status_name = "Свободен"
                ocpp_status = "Available"
                available_count += 1
            elif connector_status == "occupied":
                status_name = "Идет зарядка" if soc is not None else "Занят"
                ocpp_status = "Charging" if soc is not None else "Occupied"
                occupied_count += 1
            elif connector_status == "faulted":
                status_name = "Неисправен"
                ocpp_status = "Faulted"
                faulted_count += 1
            else:
                status_name = "Недоступен"
                ocpp_status = "Unavailable"
                faulted_count += 1

            connectors.append(MobileConnectorItem(
                connector_number=conn[0],
                connector_type=conn[1],
                max_power=float(conn[2]) if conn[2] else None,
                status_name=status_name,
                status=ocpp_status,
                current_progress=soc,
            ))

        total_connectors = available_count + occupied_count + faulted_count

        station_detail = MobileStationDetail(
            id=db_station_id,
            station_display_name=serial_number,
            location_id=station_row[2],
            max_power=float(station_row[3]) if station_row[3] else None,
            tariff_per_kwh=float(station_row[4]) if station_row[4] else None,
            location_name=station_row[5],
            location_address=station_row[6],
            location_coordinates=LocationCoordinates(
                lat=float(station_row[7]) if station_row[7] else None,
                lng=float(station_row[8]) if station_row[8] else None,
            ),
            is_online=is_online,
            total_connectors=total_connectors,
            occupied_connectors=occupied_count,
            available_connectors=available_count,
            faulted_connectors=faulted_count,
            connectors=connectors,
        )

        return MobileStationDetailResponse(success=True, data=station_detail)

    except Exception as e:
        logger.error(f"Ошибка получения деталей станции {station_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")
