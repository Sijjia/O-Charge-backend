"""
Admin API: Connector CRUD — управление портами (разъёмами) станций
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging
from uuid import uuid4

from app.db.session import get_db
from .operators import get_current_admin, get_current_owner

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stations/{station_id}/connectors", summary="List connectors", description="Returns all connectors for a station.")
async def list_connectors(
    station_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Список коннекторов станции"""
    get_current_owner(request, db)

    station = db.execute(
        text("SELECT id FROM stations WHERE id = :id"), {"id": station_id}
    ).fetchone()
    if not station:
        raise HTTPException(status_code=404, detail="Станция не найдена")

    rows = db.execute(text("""
        SELECT connector_number, connector_type, power_kw, status
        FROM connectors WHERE station_id = :station_id
        ORDER BY connector_number
    """), {"station_id": station_id}).fetchall()

    return {
        "success": True,
        "data": [
            {
                "connector_number": r.connector_number,
                "connector_type": r.connector_type,
                "power_kw": float(r.power_kw) if r.power_kw else None,
                "status": r.status,
            }
            for r in rows
        ],
    }


@router.post("/stations/{station_id}/connectors", summary="Add connector", description="Adds a new connector (port) to a station.")
async def add_connector(
    station_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Добавить порт к станции"""
    admin = get_current_admin(request, db)
    body = await request.json()

    station = db.execute(
        text("SELECT id FROM stations WHERE id = :id"), {"id": station_id}
    ).fetchone()
    if not station:
        raise HTTPException(status_code=404, detail="Станция не найдена")

    connector_type = body.get("connector_type", "Type2")
    power_kw = body.get("power_kw")

    # Auto-assign next connector_number
    max_num = db.execute(text("""
        SELECT COALESCE(MAX(connector_number), 0) FROM connectors WHERE station_id = :station_id
    """), {"station_id": station_id}).scalar()
    next_num = max_num + 1

    db.execute(text("""
        INSERT INTO connectors (id, station_id, connector_number, connector_type, power_kw, status, created_at, updated_at)
        VALUES (:id, :station_id, :number, :type, :power_kw, 'available', NOW(), NOW())
    """), {
        "id": str(uuid4()),
        "station_id": station_id,
        "number": next_num,
        "type": connector_type,
        "power_kw": power_kw,
    })
    db.commit()
    logger.info(f"[Admin] Коннектор #{next_num} добавлен к станции {station_id} by {admin['id']}")

    return {"success": True, "connector_number": next_num, "message": f"Порт #{next_num} добавлен"}


@router.put("/stations/{station_id}/connectors/{connector_number}", summary="Update connector", description="Updates connector type and power.")
async def update_connector(
    station_id: str,
    connector_number: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Обновить тип/мощность порта"""
    admin = get_current_admin(request, db)
    body = await request.json()

    existing = db.execute(text("""
        SELECT id FROM connectors WHERE station_id = :station_id AND connector_number = :num
    """), {"station_id": station_id, "num": connector_number}).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Порт #{connector_number} не найден")

    set_clauses = []
    params = {"station_id": station_id, "num": connector_number}

    if "connector_type" in body:
        set_clauses.append("connector_type = :connector_type")
        params["connector_type"] = body["connector_type"]
    if "power_kw" in body:
        set_clauses.append("power_kw = :power_kw")
        params["power_kw"] = body["power_kw"]

    if not set_clauses:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")

    set_clauses.append("updated_at = NOW()")

    db.execute(text(f"""
        UPDATE connectors SET {', '.join(set_clauses)}
        WHERE station_id = :station_id AND connector_number = :num
    """), params)
    db.commit()
    logger.info(f"[Admin] Коннектор #{connector_number} обновлён на станции {station_id} by {admin['id']}")

    return {"success": True, "connector_number": connector_number, "message": f"Порт #{connector_number} обновлён"}


@router.delete("/stations/{station_id}/connectors/{connector_number}", summary="Delete connector", description="Deletes a connector if no active sessions.")
async def delete_connector(
    station_id: str,
    connector_number: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Удалить порт (с проверкой активных сессий)"""
    admin = get_current_admin(request, db)

    existing = db.execute(text("""
        SELECT id FROM connectors WHERE station_id = :station_id AND connector_number = :num
    """), {"station_id": station_id, "num": connector_number}).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Порт #{connector_number} не найден")

    # Проверяем нет ли активных сессий на этом коннекторе
    active = db.execute(text("""
        SELECT COUNT(*) FROM charging_sessions
        WHERE station_id = :station_id AND connector_id = :num AND status = 'started'
    """), {"station_id": station_id, "num": connector_number}).scalar()
    if active > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Нельзя удалить порт: {active} активных сессий зарядки"
        )

    db.execute(text("""
        DELETE FROM connectors WHERE station_id = :station_id AND connector_number = :num
    """), {"station_id": station_id, "num": connector_number})
    db.commit()
    logger.info(f"[Admin] Коннектор #{connector_number} удалён со станции {station_id} by {admin['id']}")

    return {"success": True, "connector_number": connector_number, "message": f"Порт #{connector_number} удалён"}
