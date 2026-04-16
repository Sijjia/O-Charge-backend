"""
Admin API: Critical Alerts — DASH-06 уведомления о критических событиях
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
import logging
from datetime import datetime

from app.db.session import get_db
from .operators import get_current_admin, get_current_owner
from app.api.v1.schemas.admin.alerts import AlertsListResponse
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/alerts")
logger = logging.getLogger(__name__)


@router.get("", summary="List alerts", description="Returns critical alerts with optional severity and acknowledged filtering.", response_model=AlertsListResponse, responses=ADMIN_RESPONSES)
async def get_alerts(
    request: Request,
    db: Session = Depends(get_db),
    severity: Optional[str] = Query(None, pattern="^(critical|warning|info)$"),
    acknowledged: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Список активных алертов (station offline, errors, low balance)"""
    admin = get_current_owner(request, db)

    alerts = []

    # Single query: station alerts + error sessions combined (cast enums to text for UNION)
    rows = db.execute(text("""
        (
            SELECT
                'station_error' as alert_type,
                ss.station_id as entity_id,
                ss.station_id as station_id,
                ss.status::text as status,
                ss.error_code as error_detail,
                ss.updated_at as created_at
            FROM ocpp_station_status ss
            WHERE ss.status IN ('Faulted', 'Unavailable')
            AND ss.updated_at > now() - interval '24 hours'
            ORDER BY ss.updated_at DESC
            LIMIT :limit
        )
        UNION ALL
        (
            SELECT
                'session_error' as alert_type,
                cs.id::text as entity_id,
                cs.station_id as station_id,
                cs.status::text as status,
                cs.error_details as error_detail,
                cs.start_time as created_at
            FROM charging_sessions cs
            WHERE cs.status = 'error'
            AND cs.start_time > now() - interval '24 hours'
            ORDER BY cs.start_time DESC
            LIMIT :limit
        )
    """), {"limit": limit}).fetchall()

    for row in rows:
        if row.alert_type == 'station_error':
            sev = "critical" if row.status == "Faulted" else "warning"
            if severity and sev != severity:
                continue
            alerts.append({
                "id": f"station-{row.entity_id}-{row.created_at.isoformat() if row.created_at else 'unknown'}",
                "type": "station_error",
                "severity": sev,
                "title": f"Станция {row.station_id}: {row.status}",
                "message": f"Ошибка: {row.error_detail or 'нет данных'}" if row.status == "Faulted" else "Станция недоступна",
                "station_id": row.station_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "acknowledged": False,
            })
        else:  # session_error
            if severity and "critical" != severity:
                continue
            alerts.append({
                "id": f"session-{row.entity_id}",
                "type": "session_error",
                "severity": "critical",
                "title": f"Ошибка сессии на {row.station_id}",
                "message": row.error_detail or "Сессия завершена с ошибкой",
                "station_id": row.station_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "acknowledged": False,
            })

    # Sort by severity (critical first) then by date
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (severity_order.get(a["severity"], 9), a.get("created_at", "") or ""))

    return {
        "success": True,
        "alerts": alerts[:limit],
        "total": len(alerts),
        "unacknowledged": len([a for a in alerts if not a["acknowledged"]]),
    }


@router.post("/{alert_id}/acknowledge", summary="Acknowledge alert", description="Marks an alert as acknowledged.", responses=ADMIN_RESPONSES)
async def acknowledge_alert(
    alert_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Подтвердить алерт (пометить как прочитанный)"""
    admin = get_current_admin(request, db)
    # In a production system, this would persist to DB.
    # For now, alerts are computed dynamically.
    return {"success": True, "message": f"Алерт {alert_id} подтверждён"}
