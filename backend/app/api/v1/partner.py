"""
Partner API — кабинет партнёра (владельца станций)
Red Petroleum EV — P1 #8-9

Endpoints:
- GET /partner/dashboard — KPI (PA-B01)
- GET /partner/stations — станции со статусами (PA-B02)
- GET /partner/sessions — история сессий с пагинацией (PA-B03)
- GET /partner/revenue — доход за период (PA-B04)

Auth: user_id из JWT должен иметь запись в таблице partners.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Optional
from datetime import date
from sqlalchemy.orm import Session
import logging

from app.db.session import get_db
from app.services.partner_service import PartnerService
from app.api.v1.schemas.partner import PartnerDashboardResponse, PartnerStationsResponse, PartnerSessionsResponse, PartnerRevenueResponse
from app.api.v1.schemas.common import AUTH_RESPONSES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/partner")


def _get_partner_user_id(request: Request) -> Optional[str]:
    """Извлечь user_id из JWT (через middleware)"""
    return getattr(request.state, "client_id", None)


@router.get("/dashboard", summary="Partner dashboard", description="Returns partner's aggregated dashboard: station count, revenue, active sessions.", response_model=PartnerDashboardResponse, responses=AUTH_RESPONSES)
async def get_dashboard(
    db: Session = Depends(get_db),
    http_request: Request = None,
):
    """KPI партнёра: станции, доход, активные сессии (PA-B01)"""
    user_id = _get_partner_user_id(http_request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing or invalid authentication")

    service = PartnerService(db)

    try:
        result = service.get_dashboard(user_id)
        return result
    except Exception as e:
        logger.error(f"Ошибка получения dashboard партнёра: {e}", exc_info=True)
        return {
            "success": False,
            "error": "internal_error",
            "message": "Внутренняя ошибка сервера",
        }


@router.get("/stations", summary="Partner stations", description="Returns list of stations owned by the partner with online status.", response_model=PartnerStationsResponse, responses=AUTH_RESPONSES)
async def get_stations(
    db: Session = Depends(get_db),
    http_request: Request = None,
):
    """Станции партнёра со статусами и метриками (PA-B02)"""
    user_id = _get_partner_user_id(http_request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing or invalid authentication")

    service = PartnerService(db)

    try:
        result = service.get_stations(user_id)
        return result
    except Exception as e:
        logger.error(f"Ошибка получения станций партнёра: {e}", exc_info=True)
        return {
            "success": False,
            "error": "internal_error",
            "message": "Внутренняя ошибка сервера",
        }


@router.get("/sessions", summary="Partner sessions", description="Returns paginated charging sessions on partner's stations with date filtering.", response_model=PartnerSessionsResponse, responses=AUTH_RESPONSES)
async def get_sessions(
    from_date: Optional[date] = Query(None, alias="from", description="Начало периода (YYYY-MM-DD)"),
    to_date: Optional[date] = Query(None, alias="to", description="Конец периода (YYYY-MM-DD)"),
    station_id: Optional[str] = Query(None, description="Фильтр по станции"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    per_page: int = Query(20, ge=1, le=100, description="Записей на страницу"),
    db: Session = Depends(get_db),
    http_request: Request = None,
):
    """История сессий на станциях партнёра с пагинацией (PA-B03)"""
    user_id = _get_partner_user_id(http_request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing or invalid authentication")

    service = PartnerService(db)

    try:
        result = service.get_sessions(
            user_id=user_id,
            from_date=from_date,
            to_date=to_date,
            station_id=station_id,
            page=page,
            per_page=per_page,
        )
        return result
    except Exception as e:
        logger.error(f"Ошибка получения сессий партнёра: {e}", exc_info=True)
        return {
            "success": False,
            "error": "internal_error",
            "message": "Внутренняя ошибка сервера",
        }


@router.get("/revenue", summary="Partner revenue", description="Returns revenue breakdown by period (day/week/month) for partner's stations.", response_model=PartnerRevenueResponse, responses=AUTH_RESPONSES)
async def get_revenue(
    from_date: Optional[date] = Query(None, alias="from", description="Начало периода (YYYY-MM-DD)"),
    to_date: Optional[date] = Query(None, alias="to", description="Конец периода (YYYY-MM-DD)"),
    group_by: str = Query("day", pattern="^(hour|day|week|month)$", description="Группировка: hour/day/week/month"),
    db: Session = Depends(get_db),
    http_request: Request = None,
):
    """Доход партнёра за период с группировкой (PA-B04)"""
    user_id = _get_partner_user_id(http_request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing or invalid authentication")

    service = PartnerService(db)

    try:
        result = service.get_revenue(
            user_id=user_id,
            from_date=from_date,
            to_date=to_date,
            group_by=group_by,
        )
        return result
    except Exception as e:
        logger.error(f"Ошибка получения дохода партнёра: {e}", exc_info=True)
        return {
            "success": False,
            "error": "internal_error",
            "message": "Внутренняя ошибка сервера",
        }
