"""
Admin Clients API — управление клиентами приложения (end-users)
Доступ: admin, superadmin
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ....db.session import get_db
from .operators import get_current_admin
from app.api.v1.schemas.admin.clients import AdminClientListResponse, AdminClientDetailResponse
from app.api.v1.schemas.common import ADMIN_RESPONSES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clients")


@router.get("", response_model=AdminClientListResponse, summary="List clients", description="Returns paginated list of app clients with session stats. Supports search and active filtering.", responses=ADMIN_RESPONSES)
async def list_clients(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None, max_length=100),
    is_active: Optional[bool] = None,
):
    """Список клиентов приложения (end-users)"""
    get_current_admin(request, db)

    where = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}

    if search:
        where.append("(c.phone ILIKE :search OR c.name ILIKE :search)")
        params["search"] = f"%{search}%"
    if is_active is not None:
        where.append("c.status = :status")
        params["status"] = "active" if is_active else "inactive"

    where_sql = " AND ".join(where)

    total = db.execute(
        text(f"SELECT COUNT(*) FROM clients c WHERE {where_sql}"), params
    ).scalar() or 0

    rows = db.execute(text(f"""
        SELECT
            c.id, c.phone, c.name, c.balance, c.status,
            c.created_at, c.updated_at,
            COALESCE((SELECT COUNT(*) FROM charging_sessions cs WHERE cs.user_id = c.id), 0) as total_sessions,
            COALESCE((SELECT SUM(cs.energy) FROM charging_sessions cs WHERE cs.user_id = c.id AND cs.status = 'stopped'), 0) as total_energy_kwh,
            (SELECT MAX(cs.stop_time) FROM charging_sessions cs WHERE cs.user_id = c.id AND cs.status = 'stopped') as last_session_at
        FROM clients c
        WHERE {where_sql}
        ORDER BY c.created_at DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    page = (offset // limit) + 1

    users = []
    for r in rows:
        users.append({
            "id": str(r.id),
            "phone": r.phone or "",
            "name": r.name,
            "email": None,
            "balance": float(r.balance) if r.balance else 0,
            "total_sessions": r.total_sessions or 0,
            "total_energy_kwh": round(float(r.total_energy_kwh or 0), 2),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "last_session_at": r.last_session_at.isoformat() if r.last_session_at else None,
            "is_active": r.status == "active",
        })

    # Sessions today
    today_sessions = db.execute(text("""
        SELECT COUNT(*) FROM charging_sessions
        WHERE DATE(start_time) = CURRENT_DATE
    """)).scalar() or 0

    return {
        "success": True,
        "users": users,
        "total": total,
        "page": page,
        "per_page": limit,
        "today_sessions": today_sessions,
    }


@router.get("/{client_id}", response_model=AdminClientDetailResponse, summary="Get client details", description="Returns full client info including balance and session stats.", responses=ADMIN_RESPONSES)
async def get_client(
    client_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Детали клиента"""
    get_current_admin(request, db)

    row = db.execute(text("""
        SELECT
            c.id, c.phone, c.name, c.balance, c.status,
            c.created_at, c.updated_at,
            COALESCE((SELECT COUNT(*) FROM charging_sessions cs WHERE cs.user_id = c.id), 0) as total_sessions,
            COALESCE((SELECT SUM(cs.energy) FROM charging_sessions cs WHERE cs.user_id = c.id AND cs.status = 'stopped'), 0) as total_energy_kwh,
            (SELECT MAX(cs.stop_time) FROM charging_sessions cs WHERE cs.user_id = c.id AND cs.status = 'stopped') as last_session_at
        FROM clients c
        WHERE c.id = :id
    """), {"id": client_id}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    return {
        "success": True,
        "user": {
            "id": str(row.id),
            "phone": row.phone or "",
            "name": row.name,
            "email": None,
            "balance": float(row.balance) if row.balance else 0,
            "total_sessions": row.total_sessions or 0,
            "total_energy_kwh": round(float(row.total_energy_kwh or 0), 2),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_session_at": row.last_session_at.isoformat() if row.last_session_at else None,
            "is_active": row.status == "active",
        },
    }
