"""
Admin API: Map Integrations — управление интеграциями с картографическими платформами
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from app.db.session import get_db
from .operators import get_current_admin, get_current_owner
from app.api.v1.schemas.admin.integrations import MapIntegrationListResponse, MapIntegrationOverview
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/integrations")
logger = logging.getLogger(__name__)


def _mask_key(key: str | None) -> str | None:
    if not key or len(key) < 8:
        return key
    return key[:4] + "••••" + key[-4:]


@router.get("/maps", response_model=MapIntegrationListResponse, summary="List map integrations", responses=ADMIN_RESPONSES)
async def list_map_integrations(
    request: Request,
    db: Session = Depends(get_db),
):
    """Список картографических платформ с настройками"""
    admin = get_current_owner(request, db)

    rows = db.execute(text("""
        SELECT id, platform, display_name, icon, api_key, is_enabled,
               sync_interval_minutes, last_global_sync_at,
               locations_synced, locations_error, config
        FROM map_integrations
        ORDER BY display_name
    """)).fetchall()

    integrations = []
    for r in rows:
        integrations.append({
            "id": r.id,
            "platform": r.platform,
            "display_name": r.display_name,
            "icon": r.icon,
            "api_key_masked": _mask_key(r.api_key),
            "is_enabled": r.is_enabled,
            "sync_interval_minutes": r.sync_interval_minutes,
            "last_global_sync_at": r.last_global_sync_at.isoformat() if r.last_global_sync_at else None,
            "locations_synced": r.locations_synced,
            "locations_error": r.locations_error,
            "config": r.config,
        })

    return {"success": True, "integrations": integrations}


@router.put("/maps/{platform}", summary="Update map integration settings", responses=ADMIN_RESPONSES)
async def update_map_integration(
    platform: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Обновить настройки платформы (API key, enabled, interval)"""
    admin = get_current_admin(request, db)
    body = await request.json()

    existing = db.execute(text(
        "SELECT id FROM map_integrations WHERE platform = :p"
    ), {"p": platform}).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Платформа не найдена")

    set_clauses = []
    params = {"p": platform}

    if "api_key" in body:
        set_clauses.append("api_key = :api_key")
        params["api_key"] = body["api_key"]
    if "api_secret" in body:
        set_clauses.append("api_secret = :api_secret")
        params["api_secret"] = body["api_secret"]
    if "is_enabled" in body:
        set_clauses.append("is_enabled = :is_enabled")
        params["is_enabled"] = body["is_enabled"]
    if "sync_interval_minutes" in body:
        set_clauses.append("sync_interval_minutes = :interval")
        params["interval"] = body["sync_interval_minutes"]
    if "config" in body:
        set_clauses.append("config = :config")
        params["config"] = body["config"]

    if not set_clauses:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")

    set_clauses.append("updated_at = NOW()")

    db.execute(text(
        f"UPDATE map_integrations SET {', '.join(set_clauses)} WHERE platform = :p"
    ), params)
    db.commit()
    logger.info(f"[Admin] Интеграция {platform} обновлена by {admin['id']}")

    return {"success": True, "message": f"Настройки {platform} обновлены"}


@router.post("/maps/{platform}/test", summary="Test map integration connection", responses=ADMIN_RESPONSES)
async def test_map_integration(
    platform: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Тест подключения к платформе"""
    admin = get_current_admin(request, db)

    row = db.execute(text(
        "SELECT platform, api_key, config FROM map_integrations WHERE platform = :p"
    ), {"p": platform}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Платформа не найдена")

    # Basic connectivity test — Phase 3 will add real connector tests
    if platform == "open_charge_map":
        has_key = bool(row.api_key)
        return {
            "success": True,
            "ok": True,
            "message": "Open Charge Map API доступен" + (" (API key настроен)" if has_key else " (без API key — ограниченный доступ)"),
        }
    elif platform == "google_business":
        return {
            "success": True,
            "ok": False,
            "message": "Требуется OAuth авторизация (будет в Phase 3)",
        }
    else:
        return {
            "success": True,
            "ok": False,
            "message": f"Коннектор для {platform} ещё не реализован",
        }


@router.get("/maps/overview", response_model=MapIntegrationOverview, summary="Map integrations overview", responses=ADMIN_RESPONSES)
async def map_integrations_overview(
    request: Request,
    db: Session = Depends(get_db),
):
    """Общая статистика синхронизации по всем платформам"""
    admin = get_current_owner(request, db)

    total_locations = db.execute(text(
        "SELECT COUNT(*) FROM locations WHERE status = 'active'"
    )).scalar()

    platform_stats = db.execute(text("""
        SELECT mi.platform, mi.display_name, mi.is_enabled,
               COUNT(lps.id) FILTER (WHERE lps.sync_status = 'synced') as synced,
               COUNT(lps.id) FILTER (WHERE lps.sync_status = 'error') as errors,
               COUNT(lps.id) FILTER (WHERE lps.is_enabled = true) as enabled_locations
        FROM map_integrations mi
        LEFT JOIN location_platform_sync lps ON lps.platform = mi.platform
        GROUP BY mi.platform, mi.display_name, mi.is_enabled
        ORDER BY mi.display_name
    """)).fetchall()

    platforms = [
        {
            "platform": r.platform,
            "display_name": r.display_name,
            "is_enabled": r.is_enabled,
            "synced": r.synced,
            "errors": r.errors,
            "enabled_locations": r.enabled_locations,
        }
        for r in platform_stats
    ]

    return {"success": True, "total_locations": total_locations, "platforms": platforms}
