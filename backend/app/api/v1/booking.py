"""
API endpoints для бронирования коннекторов
Red Petroleum EV — PRD modules/booking/PRD.md
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, constr
from typing import Optional
from sqlalchemy.orm import Session
import logging

from app.db.session import get_db
from app.services.booking_service import BookingService
from app.api.v1.schemas.common import AUTH_RESPONSES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/booking")


class BookingCreateRequest(BaseModel):
    """Запрос на создание бронирования"""
    station_id: constr(min_length=1, max_length=50, pattern=r'^[a-zA-Z0-9\-_]+$') = Field(
        ..., description="ID станции (формат: CHR-BGK-001)"
    )
    connector_id: int = Field(..., ge=1, le=10, description="Номер коннектора (1-10)")
    duration_minutes: Optional[int] = Field(30, ge=5, le=60, description="Длительность бронирования (5-60 минут)")


@router.post(
    "/create",
    summary="Create booking",
    description="Books a connector on a station for up to 30 minutes. Prevents other users from starting on that connector.",
    responses=AUTH_RESPONSES,
)
async def create_booking(
    request: BookingCreateRequest,
    db: Session = Depends(get_db),
    http_request: Request = None
):
    """Забронировать коннектор на станции (макс 1 активное на клиента)"""
    client_id = getattr(http_request.state, "client_id", None)
    if not client_id:
        return {
            "success": False,
            "error": "unauthorized",
            "message": "Missing or invalid authentication"
        }

    service = BookingService(db)

    try:
        result = service.create_booking(
            user_id=client_id,
            station_id=request.station_id,
            connector_id=request.connector_id,
            duration_minutes=request.duration_minutes or 30
        )
        return result
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка создания бронирования: {e}", exc_info=True)
        return {
            "success": False,
            "error": "internal_error",
            "message": "Внутренняя ошибка сервера"
        }


@router.get(
    "/active",
    summary="Get active booking",
    description="Returns the client's currently active booking, if any.",
    responses=AUTH_RESPONSES,
)
async def get_active_booking(
    db: Session = Depends(get_db),
    http_request: Request = None
):
    """Получить активное бронирование клиента"""
    client_id = getattr(http_request.state, "client_id", None)
    if not client_id:
        return {
            "success": False,
            "error": "unauthorized",
            "message": "Missing or invalid authentication"
        }

    service = BookingService(db)

    try:
        result = service.get_active_booking(user_id=client_id)
        return result
    except Exception as e:
        logger.error(f"Ошибка получения бронирования: {e}", exc_info=True)
        return {
            "success": False,
            "error": "internal_error",
            "message": "Внутренняя ошибка сервера"
        }


@router.delete(
    "/{booking_id}",
    summary="Cancel booking",
    description="Cancels an active booking and releases the connector.",
    responses=AUTH_RESPONSES,
)
async def cancel_booking(
    booking_id: str,
    db: Session = Depends(get_db),
    http_request: Request = None
):
    """Отменить бронирование"""
    client_id = getattr(http_request.state, "client_id", None)
    if not client_id:
        return {
            "success": False,
            "error": "unauthorized",
            "message": "Missing or invalid authentication"
        }

    service = BookingService(db)

    try:
        result = service.cancel_booking(booking_id=booking_id, user_id=client_id)
        return result
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка отмены бронирования: {e}", exc_info=True)
        return {
            "success": False,
            "error": "internal_error",
            "message": "Внутренняя ошибка сервера"
        }
