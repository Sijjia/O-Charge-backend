"""
Admin Partners API — управление партнёрами
GET /admin/partners — список партнёров
GET /admin/partners/:id — детали партнёра
POST /admin/partners — создать партнёра
PUT /admin/partners/:id — обновить
DELETE /admin/partners/:id — деактивировать
"""
import logging
from uuid import uuid4
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ....db.session import get_db
from .operators import get_current_admin, get_current_owner
from app.api.v1.schemas.admin.partners import AdminPartnerListResponse, AdminPartnerSelectResponse, AdminPartnerDetailResponse, PartnerMutationResponse
from app.api.v1.schemas.common import ADMIN_RESPONSES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/partners")


@router.get("", response_model=AdminPartnerListResponse, summary="List partners", description="Returns paginated list of all partners with station counts and revenue.", responses=ADMIN_RESPONSES)
async def list_partners(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
):
    """Список всех партнёров"""
    get_current_admin(request, db)

    where = "1=1"
    params: dict = {"limit": limit, "offset": offset}

    if search:
        where += " AND (p.company_name ILIKE :search OR p.contact_name ILIKE :search OR p.contact_phone ILIKE :search)"
        params["search"] = f"%{search}%"

    total = db.execute(
        text(f"SELECT COUNT(*) FROM partners p WHERE {where}"), params
    ).scalar() or 0

    rows = db.execute(text(f"""
        SELECT p.id, p.user_id, p.company_name, p.contact_name, p.contact_phone,
               p.contact_email, p.revenue_share_percent, p.status, p.created_at,
               u.email,
               (SELECT COUNT(*) FROM stations s
                WHERE s.partner_id = p.id
                   OR (s.partner_id IS NULL AND s.location_id IN (
                       SELECT l.id FROM locations l WHERE l.partner_id = p.id
                   ))
               ) as station_count,
               COALESCE((
                   SELECT SUM(cs.partner_share) FROM charging_sessions cs
                   WHERE cs.partner_id = p.id AND cs.status = 'stopped'
               ), 0) as total_revenue
        FROM partners p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE {where}
        ORDER BY p.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    data = []
    for r in rows:
        data.append({
            "id": r.id,
            "user_id": r.user_id,
            "name": r.contact_name or r.company_name or "",
            "email": r.email or r.contact_email,
            "phone": r.contact_phone,
            "company_name": r.company_name,
            "station_count": r.station_count or 0,
            "total_revenue": float(r.total_revenue or 0),
            "commission_rate": float(r.revenue_share_percent or 0),
            "is_active": r.status == "active",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {"success": True, "data": data, "total": total}


@router.get("/select", response_model=AdminPartnerSelectResponse, summary="List partners for dropdown", description="Returns simplified partner list for UI select/dropdown.", responses=ADMIN_RESPONSES)
async def list_partners_for_select(
    request: Request,
    db: Session = Depends(get_db),
):
    """Краткий список партнёров для dropdown (id + name)"""
    get_current_owner(request, db)

    rows = db.execute(text("""
        SELECT p.id, p.user_id, p.company_name, p.contact_name
        FROM partners p
        WHERE p.status = 'active'
        ORDER BY p.company_name
    """)).fetchall()

    return {
        "success": True,
        "partners": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "label": r.company_name or r.contact_name or r.user_id,
            }
            for r in rows
        ],
    }


@router.get("/{partner_id}", response_model=AdminPartnerDetailResponse, summary="Get partner details", description="Returns full partner info with KPI, stations, and revenue breakdown.", responses=ADMIN_RESPONSES)
async def get_partner_detail(
    partner_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Детальная информация о партнёре"""
    get_current_admin(request, db)

    partner = db.execute(text("""
        SELECT p.*, u.email
        FROM partners p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE p.id = :id
    """), {"id": partner_id}).fetchone()

    if not partner:
        raise HTTPException(status_code=404, detail="Партнёр не найден")

    # Станции партнёра (прямые + наследованные)
    stations = db.execute(text("""
        SELECT s.id, s.model as serial_number, s.model,
               l.name as location, s.status,
               s.power_capacity,
               COALESCE((
                   SELECT SUM(cs.amount) FROM charging_sessions cs
                   WHERE cs.station_id = s.id AND cs.status = 'stopped'
               ), 0) as total_revenue,
               COALESCE((
                   SELECT ss.last_heartbeat FROM ocpp_station_status ss WHERE ss.station_id = s.id
               ), NOW()) as last_heartbeat
        FROM stations s
        LEFT JOIN locations l ON s.location_id = l.id
        WHERE s.partner_id = :partner_id
           OR (s.partner_id IS NULL AND l.partner_id = :partner_id)
        ORDER BY s.created_at
    """), {"partner_id": partner.id}).fetchall()

    # Revenue by day (last 30 days)
    from datetime import date, timedelta
    start_date = date.today() - timedelta(days=30)
    revenue_rows = db.execute(text("""
        SELECT DATE(cs.start_time) as day, SUM(cs.partner_share) as revenue
        FROM charging_sessions cs
        WHERE cs.partner_id = :partner_id AND cs.status = 'stopped'
          AND cs.start_time >= :start_date
        GROUP BY DATE(cs.start_time)
        ORDER BY day
    """), {"partner_id": partner.id, "start_date": start_date}).fetchall()

    # KPI
    today_revenue = db.execute(text("""
        SELECT COALESCE(SUM(cs.partner_share), 0)
        FROM charging_sessions cs
        WHERE cs.partner_id = :partner_id AND cs.status = 'stopped'
          AND DATE(cs.start_time) = CURRENT_DATE
    """), {"partner_id": partner.id}).scalar() or 0

    month_revenue = db.execute(text("""
        SELECT COALESCE(SUM(cs.partner_share), 0)
        FROM charging_sessions cs
        WHERE cs.partner_id = :partner_id AND cs.status = 'stopped'
          AND cs.start_time >= DATE_TRUNC('month', CURRENT_DATE)
    """), {"partner_id": partner.id}).scalar() or 0

    active_sessions = db.execute(text("""
        SELECT COUNT(*)
        FROM charging_sessions cs
        WHERE cs.partner_id = :partner_id AND cs.status = 'started'
    """), {"partner_id": partner.id}).scalar() or 0

    return {
        "success": True,
        "data": {
            "id": partner.id,
            "company_name": partner.company_name or "",
            "contact_name": partner.contact_name or "",
            "phone": partner.contact_phone or "",
            "email": partner.email or partner.contact_email or "",
            "billing_type": "postpaid",
            "balance": 0,
            "revenue_share": float(partner.revenue_share_percent or 0),
            "kpi": {
                "total_stations": len(stations),
                "active_sessions": active_sessions,
                "today_revenue": float(today_revenue),
                "month_revenue": float(month_revenue),
            },
            "stations": [
                {
                    "id": s.id,
                    "serial_number": s.serial_number or s.id,
                    "model": s.model or "",
                    "location": s.location or "",
                    "status": s.status or "active",
                    "power_capacity": float(s.power_capacity or 0),
                    "total_revenue": float(s.total_revenue or 0),
                    "last_heartbeat": s.last_heartbeat.isoformat() if s.last_heartbeat else "",
                }
                for s in stations
            ],
            "revenue_by_day": [
                {"date": str(r.day), "revenue": float(r.revenue or 0)}
                for r in revenue_rows
            ],
        },
    }


@router.post("", response_model=PartnerMutationResponse, summary="Create partner", description="Creates a new partner from an existing user.", responses=ADMIN_RESPONSES)
async def create_partner(
    request: Request,
    db: Session = Depends(get_db),
):
    """Создать нового партнёра"""
    admin = get_current_admin(request, db)
    body = await request.json()

    user_id = body.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id обязателен")

    # Check user exists
    user_row = db.execute(
        text("SELECT id FROM users WHERE id = :id"), {"id": user_id}
    ).fetchone()
    if not user_row:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Check not already a partner
    existing = db.execute(
        text("SELECT id FROM partners WHERE user_id = :uid"), {"uid": user_id}
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="Пользователь уже является партнёром")

    partner_id = str(uuid4())
    db.execute(text("""
        INSERT INTO partners (id, user_id, company_name, contact_name, contact_phone,
                              contact_email, revenue_share_percent, status, created_at, updated_at)
        VALUES (:id, :user_id, :company, :name, :phone, :email, :share, 'active', NOW(), NOW())
    """), {
        "id": partner_id,
        "user_id": user_id,
        "company": body.get("company_name", ""),
        "name": body.get("contact_name", ""),
        "phone": body.get("contact_phone", ""),
        "email": body.get("contact_email", ""),
        "share": body.get("revenue_share_percent", 70),
    })
    db.commit()
    logger.info(f"[Admin] Partner created: {partner_id} by {admin['id']}")

    return {"success": True, "partner_id": partner_id, "message": "Партнёр создан"}


@router.delete("/{partner_id}", response_model=PartnerMutationResponse, summary="Deactivate partner", description="Deactivates a partner.", responses=ADMIN_RESPONSES)
async def deactivate_partner(
    partner_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Деактивировать партнёра"""
    admin = get_current_admin(request, db)

    existing = db.execute(
        text("SELECT id FROM partners WHERE id = :id"), {"id": partner_id}
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Партнёр не найден")

    db.execute(text("""
        UPDATE partners SET status = 'inactive', updated_at = NOW() WHERE id = :id
    """), {"id": partner_id})
    db.commit()
    logger.info(f"[Admin] Partner deactivated: {partner_id} by {admin['id']}")

    return {"success": True, "message": "Партнёр деактивирован"}
