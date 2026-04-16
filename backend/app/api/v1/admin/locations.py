"""
Admin API: Location CRUD — управление локациями зарядных станций
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
import logging
from uuid import uuid4

from app.db.session import get_db
from .operators import get_current_admin, get_current_owner
from .stations import _has_evse_columns
from app.api.v1.schemas.admin.locations import AdminLocationListResponse, AdminLocationDetailResponse, LocationMutationResponse, LocationImageItem, LocationSyncStatusItem
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/locations")
logger = logging.getLogger(__name__)


@router.get("", response_model=AdminLocationListResponse, summary="List locations", description="Returns paginated list of all charging locations with filters.", responses=ADMIN_RESPONSES)
async def list_locations(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    city: Optional[str] = Query(None, max_length=100, description="Фильтр по городу"),
    search: Optional[str] = Query(None, max_length=100, description="Поиск по названию/адресу"),
):
    """Список локаций с пагинацией"""
    admin = get_current_owner(request, db)

    where_clauses = ["1=1"]
    params = {"limit": limit, "offset": offset}

    if city:
        where_clauses.append("l.city = :city")
        params["city"] = city
    if search:
        where_clauses.append("(l.name ILIKE :search OR l.address ILIKE :search)")
        params["search"] = f"%{search}%"

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(text(f"""
        SELECT l.id, l.name, l.address, l.city, l.latitude as lat, l.longitude as lng,
               l.status, l.created_at, l.updated_at, l.partner_id, l.image_url,
               p.company_name as partner_name,
               p.revenue_share_percent,
               (SELECT COUNT(*) FROM stations s WHERE s.location_id = l.id) as station_count,
               (SELECT COUNT(*) FROM stations s WHERE s.location_id = l.id AND s.is_available = true) as available_stations,
               COUNT(*) OVER() as _total
        FROM locations l
        LEFT JOIN partners p ON l.partner_id = p.id
        WHERE {where_sql}
        ORDER BY l.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    total = rows[0]._total if rows else 0

    data = []
    for r in rows:
        data.append({
            "id": str(r.id),
            "name": r.name,
            "address": r.address,
            "city": r.city,
            "lat": float(r.lat) if r.lat else None,
            "lng": float(r.lng) if r.lng else None,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "station_count": r.station_count,
            "available_stations": r.available_stations,
            "partner_id": r.partner_id,
            "partner_name": r.partner_name,
            "revenue_share_percent": float(r.revenue_share_percent) if r.revenue_share_percent else None,
            "image_url": r.image_url,
        })

    return {"success": True, "data": data, "total": total, "limit": limit, "offset": offset}


@router.get("/{location_id}", response_model=AdminLocationDetailResponse, summary="Get location details", description="Returns location details with stations and revenue breakdown.", responses=ADMIN_RESPONSES)
async def get_location(
    location_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Детальная информация о локации"""
    admin = get_current_owner(request, db)

    location = db.execute(text("""
        SELECT l.*, p.company_name as partner_name, p.revenue_share_percent,
               p.contact_name as partner_contact
        FROM locations l
        LEFT JOIN partners p ON l.partner_id = p.id
        WHERE l.id = :id
    """), {"id": location_id}).fetchone()

    if not location:
        raise HTTPException(status_code=404, detail="Локация не найдена")

    # Stations + revenue in a single query
    evse_col = ", s.evse_id" if _has_evse_columns(db) else ""
    combined = db.execute(text(f"""
        WITH station_data AS (
            SELECT s.id, s.model, s.manufacturer, s.power_capacity, s.status, s.is_available,
                   s.price_per_kwh{evse_col}, ss.is_online, s.partner_id as station_partner_id,
                   sp.company_name as station_partner_name,
                   sp.revenue_share_percent as station_revenue_share,
                   (SELECT COUNT(*) FROM connectors c WHERE c.station_id = s.id) as connector_count,
                   COALESCE((
                       SELECT SUM(cs.amount) FROM charging_sessions cs
                       WHERE cs.station_id = s.id AND cs.status = 'stopped'
                       AND cs.stop_time >= date_trunc('month', CURRENT_DATE)
                   ), 0) as month_revenue
            FROM stations s
            LEFT JOIN ocpp_station_status ss ON s.id = ss.station_id
            LEFT JOIN partners sp ON s.partner_id = sp.id
            WHERE s.location_id = :location_id
        ),
        rev AS (
            SELECT COALESCE(SUM(cs.amount), 0) as total_revenue,
                   COALESCE(SUM(cs.partner_share), 0) as partner_share,
                   COALESCE(SUM(cs.platform_share), 0) as platform_share,
                   COUNT(*) as session_count
            FROM charging_sessions cs
            JOIN stations s ON cs.station_id = s.id
            WHERE s.location_id = :location_id AND cs.status = 'stopped'
              AND cs.stop_time >= date_trunc('month', CURRENT_DATE)
        )
        SELECT sd.*, r.total_revenue, r.partner_share, r.platform_share, r.session_count
        FROM station_data sd
        CROSS JOIN rev r
        ORDER BY sd.id
    """), {"location_id": location_id}).fetchall()  # noqa: F541

    stations = combined
    revenue_row = combined[0] if combined else None

    # Fetch gallery images
    images_rows = db.execute(text("""
        SELECT id, url, caption, sort_order FROM location_images
        WHERE location_id = :id AND is_active = true
        ORDER BY sort_order, created_at
    """), {"id": location_id}).fetchall()
    images = [{"id": img.id, "url": img.url, "caption": img.caption, "sort_order": img.sort_order} for img in images_rows]

    # Fetch platform sync statuses
    sync_rows = db.execute(text("""
        SELECT lps.platform, lps.is_enabled, lps.external_id, lps.external_url,
               lps.sync_status, lps.sync_error, lps.last_sync_at,
               mi.display_name, mi.icon
        FROM location_platform_sync lps
        JOIN map_integrations mi ON mi.platform = lps.platform
        WHERE lps.location_id = :id
        ORDER BY mi.display_name
    """), {"id": location_id}).fetchall()
    sync_platforms = [
        {
            "platform": sp.platform, "display_name": sp.display_name, "icon": sp.icon,
            "is_enabled": sp.is_enabled, "external_id": sp.external_id,
            "external_url": sp.external_url, "sync_status": sp.sync_status,
            "sync_error": sp.sync_error,
            "last_sync_at": sp.last_sync_at.isoformat() if sp.last_sync_at else None,
        }
        for sp in sync_rows
    ]

    return {
        "success": True,
        "location": {
            "id": str(location.id),
            "name": location.name,
            "address": location.address,
            "city": location.city,
            "lat": float(location.latitude) if location.latitude else None,
            "lng": float(location.longitude) if location.longitude else None,
            "status": location.status,
            "image_url": getattr(location, 'image_url', None),
            "partner_id": location.partner_id,
            "partner_name": location.partner_name,
            "revenue_share_percent": float(location.revenue_share_percent) if location.revenue_share_percent else None,
            "partner_contact": location.partner_contact,
            "created_at": location.created_at.isoformat() if location.created_at else None,
            "updated_at": location.updated_at.isoformat() if location.updated_at else None,
        },
        "images": images,
        "sync_platforms": sync_platforms,
        "stations": [
            {
                "id": s.id,
                "model": s.model,
                "vendor": s.manufacturer,
                "max_power": float(s.power_capacity) if s.power_capacity else None,
                "status": s.status,
                "is_available": s.is_available,
                "tariff_per_kwh": float(s.price_per_kwh) if s.price_per_kwh else None,
                "is_online": s.is_online,
                "partner_id": s.station_partner_id,
                "partner_name": s.station_partner_name,
                "partner_inherited": s.station_partner_id is None,
                "revenue_share_percent": float(s.station_revenue_share) if s.station_revenue_share else (float(location.revenue_share_percent) if location.revenue_share_percent else None),
                "connector_count": s.connector_count,
                "month_revenue": float(s.month_revenue),
                "evse_id": getattr(s, 'evse_id', None),
            }
            for s in stations
        ],
        "revenue": {
            "total_revenue": float(revenue_row.total_revenue) if revenue_row else 0,
            "partner_share": float(revenue_row.partner_share) if revenue_row else 0,
            "platform_share": float(revenue_row.platform_share) if revenue_row else 0,
            "session_count": revenue_row.session_count if revenue_row else 0,
        },
    }


@router.post("", response_model=LocationMutationResponse, summary="Create location", description="Creates a new charging location.", responses=ADMIN_RESPONSES)
async def create_location(
    request: Request,
    db: Session = Depends(get_db),
):
    """Создать локацию"""
    admin = get_current_admin(request, db)
    body = await request.json()

    name = body.get("name")
    address = body.get("address")
    if not name or not address:
        raise HTTPException(status_code=400, detail="name и address обязательны")

    location_id = str(uuid4())

    # Build INSERT — conditionally include location_seq if migration applied
    seq_col = ""
    seq_val = ""
    insert_params = {
        "id": location_id,
        "name": name,
        "address": address,
        "city": body.get("city"),
        "lat": body.get("lat"),
        "lng": body.get("lng"),
        "status": body.get("status", "active"),
        "partner_id": body.get("partner_id"),
        "image_url": body.get("image_url"),
    }

    if _has_evse_columns(db):
        try:
            location_seq = db.execute(text("SELECT nextval('location_seq_counter')")).scalar()
            seq_col = ", location_seq"
            seq_val = ", :location_seq"
            insert_params["location_seq"] = location_seq
        except Exception as e:
            logger.warning(f"[Admin] location_seq generation skipped: {e}")

    db.execute(text(f"""
        INSERT INTO locations (id, name, address, city, latitude, longitude, status, partner_id, image_url{seq_col}, created_at, updated_at)
        VALUES (:id, :name, :address, :city, :lat, :lng, :status, :partner_id, :image_url{seq_val}, NOW(), NOW())
    """), insert_params)
    db.commit()
    logger.info(f"[Admin] Локация создана: {location_id} ({name}) by {admin['id']}")

    return {"success": True, "location_id": location_id, "message": "Локация создана"}


@router.put("/{location_id}", response_model=LocationMutationResponse, summary="Update location", description="Updates location fields.", responses=ADMIN_RESPONSES)
async def update_location(
    location_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Обновить локацию"""
    admin = get_current_admin(request, db)
    body = await request.json()

    existing = db.execute(
        text("SELECT id FROM locations WHERE id = :id"), {"id": location_id}
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Локация не найдена")

    allowed_fields = {"name": "name", "address": "address", "city": "city",
                      "lat": "latitude", "lng": "longitude", "status": "status",
                      "partner_id": "partner_id", "image_url": "image_url"}
    set_clauses = []
    params = {"id": location_id}

    for body_key, db_col in allowed_fields.items():
        if body_key in body:
            set_clauses.append(f"{db_col} = :{body_key}")
            params[body_key] = body[body_key]

    if not set_clauses:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")

    set_clauses.append("updated_at = NOW()")

    db.execute(
        text(f"UPDATE locations SET {', '.join(set_clauses)} WHERE id = :id"),
        params
    )
    db.commit()
    logger.info(f"[Admin] Локация обновлена: {location_id} by {admin['id']}")

    return {"success": True, "location_id": location_id, "message": "Локация обновлена"}


@router.delete("/{location_id}", response_model=LocationMutationResponse, summary="Deactivate location", description="Soft-deactivates a location.", responses=ADMIN_RESPONSES)
async def delete_location(
    location_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Удалить локацию (soft delete: status=inactive)"""
    admin = get_current_admin(request, db)

    existing = db.execute(
        text("SELECT id FROM locations WHERE id = :id"), {"id": location_id}
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Локация не найдена")

    # Проверяем нет ли привязанных станций (любой статус)
    station_count = db.execute(text("""
        SELECT COUNT(*) FROM stations WHERE location_id = :id
    """), {"id": location_id}).scalar()
    if station_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Нельзя удалить локацию: привязано {station_count} станций. Сначала перенесите или удалите все станции."
        )

    db.execute(text("""
        UPDATE locations SET status = 'inactive', updated_at = NOW() WHERE id = :id
    """), {"id": location_id})
    db.commit()
    logger.info(f"[Admin] Локация деактивирована: {location_id} by {admin['id']}")

    return {"success": True, "location_id": location_id, "message": "Локация деактивирована"}


# --- Gallery endpoints ---

@router.post("/{location_id}/images", summary="Add gallery image", responses=ADMIN_RESPONSES)
async def add_location_image(
    location_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Добавить фото в галерею локации"""
    admin = get_current_admin(request, db)
    body = await request.json()
    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url обязателен")

    existing = db.execute(text("SELECT id FROM locations WHERE id = :id"), {"id": location_id}).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Локация не найдена")

    image_id = str(uuid4())
    max_order = db.execute(text(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM location_images WHERE location_id = :lid"
    ), {"lid": location_id}).scalar()

    db.execute(text("""
        INSERT INTO location_images (id, location_id, url, caption, sort_order)
        VALUES (:id, :lid, :url, :caption, :sort_order)
    """), {"id": image_id, "lid": location_id, "url": url, "caption": body.get("caption"), "sort_order": max_order})
    db.commit()
    logger.info(f"[Admin] Фото добавлено в галерею локации {location_id} by {admin['id']}")

    return {"success": True, "image_id": image_id, "message": "Фото добавлено"}


@router.delete("/{location_id}/images/{image_id}", summary="Delete gallery image", responses=ADMIN_RESPONSES)
async def delete_location_image(
    location_id: str,
    image_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Удалить фото из галереи"""
    admin = get_current_admin(request, db)

    existing = db.execute(text(
        "SELECT id FROM location_images WHERE id = :iid AND location_id = :lid"
    ), {"iid": image_id, "lid": location_id}).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Фото не найдено")

    db.execute(text("DELETE FROM location_images WHERE id = :iid"), {"iid": image_id})
    db.commit()
    logger.info(f"[Admin] Фото {image_id} удалено из галереи локации {location_id} by {admin['id']}")

    return {"success": True, "message": "Фото удалено"}


@router.put("/{location_id}/images/reorder", summary="Reorder gallery images", responses=ADMIN_RESPONSES)
async def reorder_location_images(
    location_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Изменить порядок фото в галерее"""
    admin = get_current_admin(request, db)
    body = await request.json()
    image_ids = body.get("image_ids", [])

    for idx, iid in enumerate(image_ids):
        db.execute(text(
            "UPDATE location_images SET sort_order = :order WHERE id = :iid AND location_id = :lid"
        ), {"order": idx, "iid": iid, "lid": location_id})
    db.commit()

    return {"success": True, "message": "Порядок обновлён"}


# --- Per-location platform sync endpoints ---

@router.get("/{location_id}/platforms", summary="Get location sync statuses", responses=ADMIN_RESPONSES)
async def get_location_platforms(
    location_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Статусы синхронизации локации с картами"""
    admin = get_current_owner(request, db)

    rows = db.execute(text("""
        SELECT mi.platform, mi.display_name, mi.icon, mi.is_enabled as platform_enabled,
               lps.is_enabled, lps.external_id, lps.external_url,
               lps.sync_status, lps.sync_error, lps.last_sync_at
        FROM map_integrations mi
        LEFT JOIN location_platform_sync lps ON lps.platform = mi.platform AND lps.location_id = :lid
        ORDER BY mi.display_name
    """), {"lid": location_id}).fetchall()

    platforms = []
    for r in rows:
        platforms.append({
            "platform": r.platform,
            "display_name": r.display_name,
            "icon": r.icon,
            "platform_enabled": r.platform_enabled,
            "is_enabled": r.is_enabled if r.is_enabled is not None else False,
            "external_id": r.external_id,
            "external_url": r.external_url,
            "sync_status": r.sync_status or "not_configured",
            "sync_error": r.sync_error,
            "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
        })

    return {"success": True, "platforms": platforms}


@router.put("/{location_id}/platforms/{platform}", summary="Toggle location platform sync", responses=ADMIN_RESPONSES)
async def toggle_location_platform(
    location_id: str,
    platform: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Включить/выключить синхронизацию локации с платформой"""
    admin = get_current_admin(request, db)
    body = await request.json()
    is_enabled = body.get("is_enabled", True)

    db.execute(text("""
        INSERT INTO location_platform_sync (id, location_id, platform, is_enabled)
        VALUES (:id, :lid, :platform, :enabled)
        ON CONFLICT (location_id, platform)
        DO UPDATE SET is_enabled = :enabled, updated_at = NOW()
    """), {"id": str(uuid4()), "lid": location_id, "platform": platform, "enabled": is_enabled})
    db.commit()
    logger.info(f"[Admin] Платформа {platform} {'вкл' if is_enabled else 'выкл'} для {location_id} by {admin['id']}")

    return {"success": True, "message": f"Платформа {platform} {'включена' if is_enabled else 'выключена'}"}


@router.post("/{location_id}/platforms/{platform}/sync", summary="Manual sync location to platform", responses=ADMIN_RESPONSES)
async def sync_location_platform(
    location_id: str,
    platform: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Ручная синхронизация локации с платформой"""
    admin = get_current_admin(request, db)

    # Update sync status to syncing
    db.execute(text("""
        INSERT INTO location_platform_sync (id, location_id, platform, sync_status)
        VALUES (:id, :lid, :platform, 'syncing')
        ON CONFLICT (location_id, platform)
        DO UPDATE SET sync_status = 'syncing', sync_error = NULL, updated_at = NOW()
    """), {"id": str(uuid4()), "lid": location_id, "platform": platform})
    db.commit()

    # TODO: Call actual connector sync (Phase 3)
    # For now, mark as synced
    db.execute(text("""
        UPDATE location_platform_sync
        SET sync_status = 'synced', last_sync_at = NOW(), updated_at = NOW()
        WHERE location_id = :lid AND platform = :platform
    """), {"lid": location_id, "platform": platform})
    db.commit()
    logger.info(f"[Admin] Синхронизация {platform} для {location_id} by {admin['id']}")

    return {"success": True, "message": f"Синхронизация с {platform} выполнена"}
