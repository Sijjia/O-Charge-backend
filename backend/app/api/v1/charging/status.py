"""
📊 Endpoint для получения статуса зарядки
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from app.db.session import get_db

from .schemas import ChargingStatusResponse
from .service import ChargingService
from app.api.v1.schemas.common import ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/charging/active",
    summary="Get current user's active charging session",
    description="Returns the active charging session for the authenticated user, or active=false if none.",
)
async def get_active_session(
    request: Request,
    db: Session = Depends(get_db),
):
    """Returns active session for the current user (if any)"""
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        result = db.execute(text("""
            SELECT cs.id, cs.station_id, cs.connector_id, cs.start_time, cs.energy,
                   cs.reserved_amount, cs.status, cs.limit_type, cs.limit_value, cs.tariff_rate,
                   s.serial_number, s.model,
                   l.name as location_name, l.address as location_address
            FROM charging_sessions cs
            LEFT JOIN stations s ON cs.station_id = s.id
            LEFT JOIN locations l ON s.location_id = l.id
            WHERE cs.user_id = :client_id AND cs.status = 'started'
            ORDER BY cs.start_time DESC LIMIT 1
        """), {"client_id": client_id}).fetchone()

        if not result:
            return {"success": True, "active": False}

        row = result._mapping
        return {
            "success": True,
            "active": True,
            "session": {
                "id": str(row["id"]),
                "station_id": row["station_id"],
                "connector_id": row["connector_id"],
                "start_time": row["start_time"].isoformat() if row["start_time"] else None,
                "energy_kwh": float(row["energy"] or 0),
                "reserved_amount": float(row["reserved_amount"] or 0),
                "status": row["status"],
                "limit_type": row["limit_type"],
                "limit_value": float(row["limit_value"]) if row["limit_value"] else None,
                "tariff_rate": float(row["tariff_rate"]) if row["tariff_rate"] else None,
                "station_serial": row["serial_number"],
                "station_model": row["model"],
                "location_name": row["location_name"],
                "location_address": row["location_address"],
            },
        }
    except Exception as e:
        logger.error(f"Error getting active session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/charging/status/{session_id}",
    response_model=ChargingStatusResponse,
    summary="Get charging session status",
    description="Returns real-time status of a charging session including energy consumed, power, duration, and cost.",
    responses={
        401: {"description": "Not authenticated", "model": ErrorResponse},
        404: {"description": "Session not found", "model": ErrorResponse},
    },
)
async def get_charging_status(
    session_id: str, 
    db: Session = Depends(get_db)
):
    """📊 Проверить статус зарядки с полными данными из OCPP"""
    
    try:
        # Создаем сервис и получаем статус
        charging_service = ChargingService(db)
        result = await charging_service.get_charging_status(session_id)
        
        # Возвращаем результат
        if result["success"]:
            logger.debug(f"📊 Статус зарядки получен: сессия {session_id}, статус {result.get('status', 'unknown')}")
            return ChargingStatusResponse(**result)
        else:
            logger.warning(f"❌ Ошибка получения статуса зарядки: {result.get('error', 'unknown')}")
            raise HTTPException(
                status_code=404 if result.get("error") == "session_not_found" else 500,
                detail={
                    "error": result.get("error", "internal_error"),
                    "message": result.get("message", "Внутренняя ошибка сервера"),
                    "success": False
                }
            )
    
    except HTTPException:
        # Пропускаем HTTP исключения как есть
        raise
    
    except Exception as e:
        logger.error(f"💥 Критическая ошибка в endpoint статуса зарядки: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Внутренняя ошибка сервера",
                "success": False
            }
        )