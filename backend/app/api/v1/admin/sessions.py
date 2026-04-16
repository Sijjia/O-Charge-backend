"""
Admin API: Sessions management — просмотр и управление сессиями зарядки
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import date
import logging

from app.db.session import get_db
from .operators import get_current_admin, get_current_owner
from app.api.v1.schemas.admin.sessions import AdminSessionListResponse, AdminSessionDetailResponse, ForceStopResponse
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/sessions")
logger = logging.getLogger(__name__)


@router.get("", summary="List charging sessions", description="Returns paginated list of all charging sessions. Supports status, station, client, and date filtering.", response_model=AdminSessionListResponse, responses=ADMIN_RESPONSES)
async def list_sessions(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(None, description="Фильтр: charging, stopped, cancelled"),
    station_id: Optional[str] = Query(None, max_length=50),
    client_id: Optional[str] = Query(None, max_length=100),
    partner_id: Optional[str] = Query(None, max_length=100, description="Фильтр по партнеру"),
    connector_id: Optional[int] = Query(None, ge=1, le=10, description="Фильтр по разъему"),
    location_id: Optional[str] = Query(None, max_length=100, description="Фильтр по локации"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
):
    """Все сессии зарядки с фильтрами"""
    admin = get_current_owner(request, db)

    where_clauses = ["1=1"]
    params = {"limit": limit, "offset": offset}

    if status:
        where_clauses.append("cs.status = :status")
        params["status"] = status
    if station_id:
        where_clauses.append("cs.station_id = :station_id")
        params["station_id"] = station_id
    if client_id:
        where_clauses.append("cs.user_id = :client_id")
        params["client_id"] = client_id
    if partner_id:
        where_clauses.append("cs.partner_id = :partner_id")
        params["partner_id"] = partner_id
    if connector_id:
        where_clauses.append("cs.connector_id = :connector_id")
        params["connector_id"] = connector_id
    if location_id:
        where_clauses.append("s.location_id = :location_id")
        params["location_id"] = location_id
    if date_from:
        where_clauses.append("cs.created_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        where_clauses.append("cs.created_at < :date_to + INTERVAL '1 day'")
        params["date_to"] = date_to

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(text(f"""
        SELECT cs.id, cs.user_id, cs.station_id, cs.connector_id, cs.status,
               cs.energy, cs.amount, cs.start_time, cs.stop_time,
               cs.limit_type, cs.limit_value, cs.reserved_amount,
               cs.amount as actual_cost, cs.refund_amount, cs.created_at,
               cs.partner_id, cs.partner_share, cs.platform_share,
               c.phone as client_phone,
               s.model as station_model, s.location_id,
               l.name as location_name,
               p.company_name as partner_name,
               COUNT(*) OVER() as _total
        FROM charging_sessions cs
        LEFT JOIN clients c ON cs.user_id = c.id
        LEFT JOIN stations s ON cs.station_id = s.id
        LEFT JOIN locations l ON s.location_id = l.id
        LEFT JOIN partners p ON cs.partner_id = p.id
        WHERE {where_sql}
        ORDER BY cs.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    total = rows[0]._total if rows else 0

    data = []
    for r in rows:
        duration_minutes = 0
        if r.start_time and r.stop_time:
            duration_minutes = int((r.stop_time - r.start_time).total_seconds() / 60)

        data.append({
            "id": str(r.id),
            "client_id": r.user_id,
            "client_phone": r.client_phone,
            "station_id": r.station_id,
            "connector_id": r.connector_id,
            "station_model": r.station_model,
            "location_id": r.location_id,
            "location_name": r.location_name,
            "partner_id": r.partner_id,
            "partner_name": r.partner_name,
            "status": r.status,
            "energy_kwh": float(r.energy or 0),
            "amount": float(r.amount or 0),
            "reserved_amount": float(r.reserved_amount) if r.reserved_amount else None,
            "actual_cost": float(r.actual_cost) if r.actual_cost else None,
            "refund_amount": float(r.refund_amount) if r.refund_amount else None,
            "limit_type": r.limit_type,
            "limit_value": float(r.limit_value) if r.limit_value else None,
            "start_time": r.start_time.isoformat() if r.start_time else None,
            "stop_time": r.stop_time.isoformat() if r.stop_time else None,
            "duration_minutes": duration_minutes,
            "partner_share": float(r.partner_share) if r.partner_share else None,
            "platform_share": float(r.platform_share) if r.platform_share else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {"success": True, "data": data, "total": total, "limit": limit, "offset": offset}


@router.get("/{session_id}", summary="Get session details", description="Returns full session details including OCPP transaction and meter values.", response_model=AdminSessionDetailResponse, responses=ADMIN_RESPONSES)
async def get_session_detail(
    session_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Детальная информация о сессии + meter values"""
    admin = get_current_owner(request, db)

    session = db.execute(text("""
        SELECT cs.*, c.phone as client_phone,
               s.model as station_model, l.name as location_name, l.address as location_address
        FROM charging_sessions cs
        LEFT JOIN clients c ON cs.user_id = c.id
        LEFT JOIN stations s ON cs.station_id = s.id
        LEFT JOIN locations l ON s.location_id = l.id
        WHERE cs.id = :id
    """), {"id": session_id}).fetchone()

    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")

    # Получаем OCPP транзакцию и meter values
    ocpp_tx = db.execute(text("""
        SELECT transaction_id, meter_start, meter_stop, status, stop_reason
        FROM ocpp_transactions
        WHERE charging_session_id = :session_id OR station_id = :station_id
        ORDER BY created_at DESC LIMIT 1
    """), {"session_id": session_id, "station_id": session.station_id}).fetchone()

    meter_values = []
    if ocpp_tx:
        mvs = db.execute(text("""
            SELECT energy_active_import_register, timestamp, created_at
            FROM ocpp_meter_values
            WHERE transaction_id = :tx_id
            ORDER BY created_at
        """), {"tx_id": ocpp_tx.transaction_id}).fetchall()

        meter_values = [
            {
                "energy_wh": float(mv.energy_active_import_register) if mv.energy_active_import_register else 0,
                "timestamp": mv.timestamp.isoformat() if mv.timestamp else None,
            }
            for mv in mvs
        ]

    duration_minutes = 0
    if session.start_time and session.stop_time:
        duration_minutes = int((session.stop_time - session.start_time).total_seconds() / 60)

    return {
        "success": True,
        "session": {
            "id": str(session.id),
            "client_id": session.user_id,
            "client_phone": session.client_phone,
            "station_id": session.station_id,
            "connector_id": session.connector_id,
            "station_model": session.station_model,
            "location_name": session.location_name,
            "location_address": session.location_address,
            "status": session.status,
            "energy_kwh": float(session.energy or 0),
            "amount": float(session.amount or 0),
            "reserved_amount": float(session.reserved_amount) if session.reserved_amount else None,
            "actual_cost": float(session.amount) if session.amount else None,
            "refund_amount": float(session.refund_amount) if hasattr(session, 'refund_amount') and session.refund_amount else None,
            "limit_type": session.limit_type,
            "start_time": session.start_time.isoformat() if session.start_time else None,
            "stop_time": session.stop_time.isoformat() if session.stop_time else None,
            "duration_minutes": duration_minutes,
            "partner_share": float(session.partner_share) if session.partner_share else None,
            "platform_share": float(session.platform_share) if session.platform_share else None,
        },
        "ocpp_transaction": {
            "transaction_id": ocpp_tx.transaction_id if ocpp_tx else None,
            "meter_start": ocpp_tx.meter_start if ocpp_tx else None,
            "meter_stop": ocpp_tx.meter_stop if ocpp_tx else None,
            "status": ocpp_tx.status if ocpp_tx else None,
            "stop_reason": ocpp_tx.stop_reason if ocpp_tx else None,
        } if ocpp_tx else None,
        "meter_values": meter_values,
    }


@router.post("/{session_id}/force-stop", summary="Force stop session", description="Sends RemoteStopTransaction to forcefully stop a charging session.", response_model=ForceStopResponse, responses=ADMIN_RESPONSES)
async def force_stop_session(
    session_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Принудительная остановка сессии через RemoteStopTransaction"""
    admin = get_current_admin(request, db)

    session = db.execute(text("""
        SELECT id, station_id, status FROM charging_sessions WHERE id = :id
    """), {"id": session_id}).fetchone()

    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")

    if session.status != "started":
        raise HTTPException(status_code=400, detail=f"Сессия не активна (status={session.status})")

    # Получаем transaction_id
    ocpp_tx = db.execute(text("""
        SELECT transaction_id FROM ocpp_transactions
        WHERE station_id = :station_id AND status = 'Active'
        ORDER BY created_at DESC LIMIT 1
    """), {"station_id": session.station_id}).fetchone()

    from ocpp_ws_server.redis_manager import redis_manager
    command = {
        "action": "RemoteStopTransaction",
        "transaction_id": ocpp_tx.transaction_id if ocpp_tx else 0,
        "reason": "AdminForceStop",
    }
    await redis_manager.publish_command(session.station_id, command)

    logger.warning(f"[Admin] Force-stop: session={session_id}, station={session.station_id} by {admin['id']}")

    return {
        "success": True,
        "session_id": session_id,
        "station_id": session.station_id,
        "message": "RemoteStopTransaction отправлен",
    }
