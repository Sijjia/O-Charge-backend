"""
Admin API: Station CRUD — управление зарядными станциями
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
import logging
from uuid import uuid4

from app.db.session import get_db
from .operators import get_current_admin, get_current_owner
from app.api.v1.schemas.admin.stations import AdminStationListResponse, AdminStationDetailResponse, StationCreateRequest, StationUpdateRequest, StationMutationResponse
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/stations")
logger = logging.getLogger(__name__)

# --- Cached migration check ---
_evse_migrated: bool | None = None

def _has_evse_columns(db: Session) -> bool:
    """Check once if migration 017 columns exist (cached for process lifetime)."""
    global _evse_migrated
    if _evse_migrated is not None:
        return _evse_migrated
    try:
        result = db.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'stations' AND column_name = 'evse_id'"
        )).fetchone()
        _evse_migrated = result is not None
    except Exception:
        _evse_migrated = False
    return _evse_migrated


@router.get("", response_model=AdminStationListResponse, summary="List stations", description="Returns paginated list of all charging stations with filters.", responses=ADMIN_RESPONSES)
async def list_stations(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    location_id: Optional[str] = Query(None, description="Фильтр по локации"),
    is_available: Optional[bool] = Query(None, description="Фильтр по доступности"),
    search: Optional[str] = Query(None, max_length=100, description="Поиск по ID/модели"),
    ownership: Optional[str] = Query(None, pattern="^(own|partner)$", description="own=свои, partner=партнёрские"),
):
    """Список станций с пагинацией и фильтрами"""
    admin = get_current_owner(request, db)

    where_clauses = ["1=1"]
    params = {"limit": limit, "offset": offset}

    if location_id:
        where_clauses.append("s.location_id = :location_id")
        params["location_id"] = location_id
    if is_available is not None:
        where_clauses.append("s.is_available = :is_available")
        params["is_available"] = is_available
    if search:
        where_clauses.append("(s.id ILIKE :search OR s.model ILIKE :search OR s.manufacturer ILIKE :search)")
        params["search"] = f"%{search}%"
    if ownership == "partner":
        where_clauses.append("(s.partner_id IS NOT NULL OR EXISTS (SELECT 1 FROM locations l2 WHERE l2.id = s.location_id AND l2.partner_id IS NOT NULL))")
    elif ownership == "own":
        where_clauses.append("s.partner_id IS NULL AND NOT EXISTS (SELECT 1 FROM locations l2 WHERE l2.id = s.location_id AND l2.partner_id IS NOT NULL)")

    where_sql = " AND ".join(where_clauses)
    evse_col = ", s.evse_id" if _has_evse_columns(db) else ""

    rows = db.execute(text(f"""
        SELECT s.id, s.location_id, s.model, s.manufacturer, s.power_capacity,
               s.status, s.is_available, s.price_per_kwh,
               s.created_at, s.updated_at, s.user_id, s.partner_id,
               s.ocpp_ws_url{evse_col},
               l.name as location_name, l.address as location_address,
               l.partner_id as location_partner_id,
               ss.is_online, ss.last_heartbeat,
               (SELECT COUNT(*) FROM connectors c WHERE c.station_id = s.id) as connector_count,
               (SELECT COUNT(*) FROM charging_sessions cs
                WHERE cs.station_id = s.id AND cs.status = 'started') as active_sessions,
               COALESCE(sp.company_name, lp.company_name) as partner_name,
               COUNT(*) OVER() as _total
        FROM stations s
        LEFT JOIN locations l ON s.location_id = l.id
        LEFT JOIN ocpp_station_status ss ON s.id = ss.station_id
        LEFT JOIN partners sp ON s.partner_id = sp.id
        LEFT JOIN partners lp ON l.partner_id = lp.id
        WHERE {where_sql}
        ORDER BY s.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    total = rows[0]._total if rows else 0

    data = []
    for r in rows:
        data.append({
            "id": r.id,
            "location_id": r.location_id,
            "model": r.model,
            "vendor": r.manufacturer,
            "max_power": float(r.power_capacity) if r.power_capacity else None,
            "status": r.status,
            "is_available": r.is_available,
            "tariff_per_kwh": float(r.price_per_kwh) if r.price_per_kwh else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "location_name": r.location_name,
            "location_address": r.location_address,
            "is_online": r.is_online,
            "last_heartbeat": r.last_heartbeat.isoformat() if r.last_heartbeat else None,
            "connector_count": r.connector_count,
            "active_sessions": r.active_sessions,
            "is_partner": bool(r.partner_id or r.location_partner_id),
            "partner_id": r.partner_id or r.location_partner_id,
            "partner_name": r.partner_name,
            "evse_id": getattr(r, 'evse_id', None),
        })

    return {"success": True, "data": data, "total": total, "limit": limit, "offset": offset}


@router.get("/{station_id}", response_model=AdminStationDetailResponse, summary="Get station details", description="Returns full station info including connectors and OCPP config.", responses=ADMIN_RESPONSES)
async def get_station(
    station_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Детальная информация о станции"""
    admin = get_current_owner(request, db)

    station = db.execute(text("""
        SELECT s.*, l.name as location_name, l.address as location_address,
               l.partner_id as location_partner_id,
               ss.status as ocpp_status, ss.is_online, ss.last_heartbeat,
               ss.connector_status,
               COALESCE(sp.company_name, lp.company_name) as partner_name,
               COALESCE(sp.revenue_share_percent, lp.revenue_share_percent) as effective_revenue_share,
               em.name as equipment_model_name, em.image_url as equipment_image_url,
               em.type as equipment_type, em.power_kw as equipment_power_kw,
               em.connector_types as equipment_connector_types,
               em.num_connectors as equipment_num_connectors,
               em.voltage_range as equipment_voltage_range,
               em.ip_rating as equipment_ip_rating,
               em.ocpp_versions as equipment_ocpp_versions,
               em.operating_temp as equipment_operating_temp,
               em.dimensions as equipment_dimensions,
               em.weight_kg as equipment_weight_kg,
               em.price_min_usd as equipment_price_min,
               em.price_max_usd as equipment_price_max,
               emfr.name as equipment_manufacturer_name
        FROM stations s
        LEFT JOIN locations l ON s.location_id = l.id
        LEFT JOIN ocpp_station_status ss ON s.id = ss.station_id
        LEFT JOIN partners sp ON s.partner_id = sp.id
        LEFT JOIN partners lp ON l.partner_id = lp.id
        LEFT JOIN equipment_models em ON s.equipment_model_id = em.id
        LEFT JOIN equipment_manufacturers emfr ON em.manufacturer_id = emfr.id
        WHERE s.id = :id
    """), {"id": station_id}).fetchone()

    if not station:
        raise HTTPException(status_code=404, detail="Станция не найдена")

    connectors = db.execute(text("""
        SELECT connector_number, connector_type, power_kw, status
        FROM connectors WHERE station_id = :station_id
        ORDER BY connector_number
    """), {"station_id": station_id}).fetchall()

    return {
        "success": True,
        "station": {
            "id": station.id,
            "location_id": station.location_id,
            "model": station.model,
            "vendor": station.manufacturer,
            "max_power": float(station.power_capacity) if station.power_capacity else None,
            "status": station.status,
            "is_available": station.is_available,
            "tariff_per_kwh": float(station.price_per_kwh) if station.price_per_kwh else None,
            "serial_number": getattr(station, 'serial_number', None),
            "firmware_version": getattr(station, 'firmware_version', None),
            "created_at": station.created_at.isoformat() if station.created_at else None,
            "updated_at": station.updated_at.isoformat() if station.updated_at else None,
            "location_name": station.location_name,
            "location_address": station.location_address,
            "ocpp_status": station.ocpp_status,
            "is_online": station.is_online,
            "last_heartbeat": station.last_heartbeat.isoformat() if station.last_heartbeat else None,
            "connector_status": station.connector_status,
            "user_id": station.user_id,
            "partner_id": station.partner_id or station.location_partner_id,
            "partner_name": station.partner_name,
            "partner_inherited": station.partner_id is None and station.location_partner_id is not None,
            "revenue_share_percent": float(station.effective_revenue_share) if station.effective_revenue_share else None,
            "ocpp_ws_url": station.ocpp_ws_url or f"wss://ocpp.charge.redpay.kg/ws/{station.id}",
            "evse_id": getattr(station, 'evse_id', None),
            "equipment_model_id": getattr(station, 'equipment_model_id', None),
            "equipment_model_name": getattr(station, 'equipment_model_name', None),
            "equipment_image_url": getattr(station, 'equipment_image_url', None),
            "equipment_manufacturer_name": getattr(station, 'equipment_manufacturer_name', None),
            "equipment_type": getattr(station, 'equipment_type', None),
            "equipment_power_kw": float(station.equipment_power_kw) if getattr(station, 'equipment_power_kw', None) else None,
            "equipment_connector_types": list(station.equipment_connector_types) if getattr(station, 'equipment_connector_types', None) else None,
            "equipment_num_connectors": getattr(station, 'equipment_num_connectors', None),
            "equipment_voltage_range": getattr(station, 'equipment_voltage_range', None),
            "equipment_ip_rating": getattr(station, 'equipment_ip_rating', None),
            "equipment_ocpp_versions": list(station.equipment_ocpp_versions) if getattr(station, 'equipment_ocpp_versions', None) else None,
            "equipment_operating_temp": getattr(station, 'equipment_operating_temp', None),
            "equipment_dimensions": getattr(station, 'equipment_dimensions', None),
            "equipment_weight_kg": float(station.equipment_weight_kg) if getattr(station, 'equipment_weight_kg', None) else None,
            "equipment_price_min": getattr(station, 'equipment_price_min', None),
            "equipment_price_max": getattr(station, 'equipment_price_max', None),
        },
        "connectors": [
            {
                "connector_number": c.connector_number,
                "connector_type": c.connector_type,
                "max_power": float(c.power_kw) if c.power_kw else None,
                "status": c.status,
            }
            for c in connectors
        ],
    }


@router.post("", response_model=StationMutationResponse, summary="Create station", description="Creates a new charging station with connectors.", responses=ADMIN_RESPONSES)
async def create_station(
    request: Request,
    db: Session = Depends(get_db),
):
    """Создать новую станцию"""
    admin = get_current_admin(request, db)
    body = await request.json()

    station_id = body.get("id") or body.get("serial_number") or f"st-{uuid4().hex[:8]}"
    location_id = body.get("location_id")
    if not location_id:
        raise HTTPException(status_code=400, detail="location_id обязателен")

    # Проверяем уникальность
    existing = db.execute(
        text("SELECT id FROM stations WHERE id = :id"), {"id": station_id}
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="Станция с таким ID уже существует")

    # Проверяем что локация существует
    loc = db.execute(
        text("SELECT id FROM locations WHERE id = :id"), {"id": location_id}
    ).fetchone()
    if not loc:
        raise HTTPException(status_code=404, detail="Локация не найдена")

    # user_id — если указан, привязывает станцию к партнёру/владельцу
    user_id = body.get("user_id")
    if user_id:
        user_exists = db.execute(
            text("SELECT id FROM users WHERE id = :id"), {"id": user_id}
        ).fetchone()
        if not user_exists:
            raise HTTPException(status_code=404, detail="Пользователь (user_id) не найден")

    # EVSE ID generation (only if migration 017 applied)
    evse_extra_cols = ""
    evse_extra_vals = ""
    # Equipment model reference (optional)
    equip_extra_cols = ""
    equip_extra_vals = ""
    equipment_model_id = body.get("equipment_model_id")

    model_val = body.get("model", "Unknown")
    vendor_val = body.get("vendor") or body.get("manufacturer", "Unknown")
    max_power_val = body.get("max_power") or body.get("power_capacity", 22)

    insert_params = {
        "id": station_id,
        "serial_number": body.get("serial_number", station_id),
        "location_id": location_id,
        "model": model_val,
        "manufacturer": vendor_val,
        "power_capacity": max_power_val,
        "price_per_kwh": body.get("tariff_per_kwh"),
        "status": body.get("status", "active"),
        "user_id": user_id,
        "ocpp_ws_url": body.get("ocpp_ws_url"),
    }

    if equipment_model_id:
        equip_extra_cols = ", equipment_model_id"
        equip_extra_vals = ", :equipment_model_id"
        insert_params["equipment_model_id"] = equipment_model_id

    if _has_evse_columns(db):
        try:
            loc_seq = db.execute(text(
                "SELECT location_seq FROM locations WHERE id = :id"
            ), {"id": location_id}).scalar()
            if loc_seq is None:
                loc_seq = db.execute(text("SELECT nextval('location_seq_counter')")).scalar()
                db.execute(text(
                    "UPDATE locations SET location_seq = :seq WHERE id = :id"
                ), {"seq": loc_seq, "id": location_id})

            max_st_seq = db.execute(text(
                "SELECT COALESCE(MAX(station_seq), 0) FROM stations WHERE location_id = :loc_id"
            ), {"loc_id": location_id}).scalar()
            station_seq = max_st_seq + 1
            evse_id = f"KG*RPE*E{loc_seq:03d}{station_seq:02d}"

            evse_extra_cols = ", station_seq, evse_id"
            evse_extra_vals = ", :station_seq, :evse_id"
            insert_params["station_seq"] = station_seq
            insert_params["evse_id"] = evse_id
        except Exception as e:
            logger.warning(f"[Admin] EVSE ID generation skipped: {e}")

    db.execute(text(f"""
        INSERT INTO stations (id, serial_number, location_id, model, manufacturer,
                              power_capacity, price_per_kwh,
                              status, is_available, user_id, ocpp_ws_url{evse_extra_cols}{equip_extra_cols},
                              created_at, updated_at)
        VALUES (:id, :serial_number, :location_id, :model, :manufacturer,
                :power_capacity, :price_per_kwh,
                :status, true, :user_id, :ocpp_ws_url{evse_extra_vals}{equip_extra_vals}, NOW(), NOW())
    """), insert_params)

    # Создаём коннекторы если указаны
    connectors = body.get("connectors", [])
    if not connectors:
        connectors_count = body.get("connectors_count", 0)
        if connectors_count and int(connectors_count) > 0:
            connectors = [{"connector_number": i + 1, "connector_type": "Type2", "max_power": max_power_val} for i in range(int(connectors_count))]
    for conn in connectors:
        db.execute(text("""
            INSERT INTO connectors (id, station_id, connector_number, connector_type, power_kw, status)
            VALUES (:id, :station_id, :number, :type, :max_power, 'available')
        """), {
            "id": str(uuid4()),
            "station_id": station_id,
            "number": conn.get("connector_number", 1),
            "type": conn.get("connector_type", "Type2"),
            "max_power": conn.get("max_power"),
        })

    db.commit()
    logger.info(f"[Admin] Станция создана: {station_id} by {admin['id']}")

    return {"success": True, "station_id": station_id, "message": "Станция создана"}


@router.put("/{station_id}", response_model=StationMutationResponse, summary="Update station", description="Updates station fields (model, vendor, power, tariff, etc).", responses=ADMIN_RESPONSES)
async def update_station(
    station_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Обновить станцию"""
    admin = get_current_admin(request, db)
    body = await request.json()

    existing = db.execute(
        text("SELECT id FROM stations WHERE id = :id"), {"id": station_id}
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Станция не найдена")

    # Маппинг legacy полей (frontend form → backend DB)
    if "vendor" in body and "manufacturer" not in body:
        body["manufacturer"] = body.pop("vendor")
    elif "vendor" in body:
        body.pop("vendor")
    if "max_power" in body and "power_capacity" not in body:
        body["power_capacity"] = body.pop("max_power")
    elif "max_power" in body:
        body.pop("max_power")
    if "tariff_per_kwh" in body and "price_per_kwh" not in body:
        body["price_per_kwh"] = body.pop("tariff_per_kwh")
    elif "tariff_per_kwh" in body:
        body.pop("tariff_per_kwh")

    # Динамическое обновление только переданных полей
    allowed_fields = ["model", "manufacturer", "power_capacity", "price_per_kwh", "status",
                      "is_available", "location_id", "user_id", "partner_id", "ocpp_ws_url",
                      "equipment_model_id"]
    set_clauses = []
    params = {"id": station_id}

    for field in allowed_fields:
        if field in body:
            set_clauses.append(f"{field} = :{field}")
            params[field] = body[field]

    if not set_clauses:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")

    # При смене location_id — пересгенерировать evse_id (only if migration applied)
    if "location_id" in body and _has_evse_columns(db):
        new_location_id = body["location_id"]
        target_loc = db.execute(
            text("SELECT id, location_seq FROM locations WHERE id = :id"),
            {"id": new_location_id}
        ).fetchone()
        if not target_loc:
            raise HTTPException(status_code=404, detail="Целевая локация не найдена")

        try:
            loc_seq = target_loc.location_seq
            if loc_seq is None:
                loc_seq = db.execute(text("SELECT nextval('location_seq_counter')")).scalar()
                db.execute(text(
                    "UPDATE locations SET location_seq = :seq WHERE id = :id"
                ), {"seq": loc_seq, "id": new_location_id})

            max_st_seq = db.execute(text(
                "SELECT COALESCE(MAX(station_seq), 0) FROM stations WHERE location_id = :loc_id"
            ), {"loc_id": new_location_id}).scalar()
            new_station_seq = max_st_seq + 1
            new_evse_id = f"KG*RPE*E{loc_seq:03d}{new_station_seq:02d}"

            set_clauses.append("station_seq = :station_seq")
            params["station_seq"] = new_station_seq
            set_clauses.append("evse_id = :evse_id")
            params["evse_id"] = new_evse_id
        except Exception as e:
            logger.warning(f"[Admin] EVSE ID regeneration skipped: {e}")
    elif "location_id" in body and not _has_evse_columns(db):
        # Still validate target location exists
        new_location_id = body["location_id"]
        target_loc = db.execute(
            text("SELECT id FROM locations WHERE id = :id"),
            {"id": new_location_id}
        ).fetchone()
        if not target_loc:
            raise HTTPException(status_code=404, detail="Целевая локация не найдена")

    set_clauses.append("updated_at = NOW()")

    db.execute(
        text(f"UPDATE stations SET {', '.join(set_clauses)} WHERE id = :id"),
        params
    )
    db.commit()
    logger.info(f"[Admin] Станция обновлена: {station_id} by {admin['id']}")

    return {"success": True, "station_id": station_id, "message": "Станция обновлена"}


@router.delete("/{station_id}", response_model=StationMutationResponse, summary="Deactivate station", description="Soft-deactivates a station (sets status to inactive).", responses=ADMIN_RESPONSES)
async def delete_station(
    station_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Удалить станцию (soft delete: status=decommissioned)"""
    admin = get_current_admin(request, db)

    existing = db.execute(
        text("SELECT id, status FROM stations WHERE id = :id"), {"id": station_id}
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Станция не найдена")

    # Проверяем нет ли активных сессий
    active = db.execute(text("""
        SELECT COUNT(*) FROM charging_sessions
        WHERE station_id = :id AND status = 'started'
    """), {"id": station_id}).scalar()
    if active > 0:
        raise HTTPException(status_code=409, detail=f"Нельзя удалить станцию: {active} активных сессий зарядки. Дождитесь завершения.")

    db.execute(text("""
        UPDATE stations SET status = 'inactive', is_available = false, updated_at = NOW()
        WHERE id = :id
    """), {"id": station_id})
    db.commit()
    logger.info(f"[Admin] Станция деактивирована: {station_id} by {admin['id']}")

    return {"success": True, "station_id": station_id, "message": "Станция деактивирована"}
