"""
Admin Bookings API — просмотр бронирований (резервов)
Доступ: admin, superadmin
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ....db.session import get_db
from .operators import get_current_admin
from ....api.v1.schemas.admin.bookings import AdminBookingsResponse
from ....api.v1.schemas.common import ADMIN_RESPONSES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bookings")


@router.get("", summary="List bookings", description="Returns paginated list of all bookings with optional status and station filtering.", response_model=AdminBookingsResponse, responses=ADMIN_RESPONSES)
async def list_bookings(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="active/completed/cancelled/expired"),
    station_id: Optional[str] = Query(None),
):
    """Список бронирований"""
    get_current_admin(request, db)

    where = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}

    if status:
        where.append("b.status = :status")
        params["status"] = status
    if station_id:
        where.append("b.station_id = :station_id")
        params["station_id"] = station_id

    where_sql = " AND ".join(where)

    total = db.execute(
        text(f"SELECT COUNT(*) FROM bookings b WHERE {where_sql}"), params
    ).scalar() or 0

    rows = db.execute(text(f"""
        SELECT
            b.id, b.station_id, b.user_id, b.connector_id as connector_number,
            b.created_at as start_time, b.expires_at as end_time,
            b.status, b.created_at,
            s.id as station_name,
            c.phone as user_phone
        FROM bookings b
        LEFT JOIN stations s ON s.id = b.station_id
        LEFT JOIN clients c ON c.id = b.user_id
        WHERE {where_sql}
        ORDER BY b.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    data = []
    for r in rows:
        data.append({
            "id": str(r.id),
            "station_id": r.station_id or "",
            "user_id": str(r.user_id) if r.user_id else "",
            "connector_number": r.connector_number or 1,
            "start_time": r.start_time.isoformat() if r.start_time else "",
            "end_time": r.end_time.isoformat() if r.end_time else "",
            "status": r.status or "unknown",
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "station_name": r.station_name,
            "user_phone": r.user_phone,
        })

    return {
        "success": True,
        "data": data,
        "total": total,
    }
