"""
Guest Charging API endpoints
Гостевая зарядка без регистрации
"""
import logging
import re
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from .schemas import (
    GuestStartRequest,
    GuestStartResponse,
    GuestStatusResponse,
    GuestStopResponse,
    GuestStopRequest,
)
from .service import GuestChargingService

logger = logging.getLogger("app.api.v1.guest")

router = APIRouter(prefix="/guest")

# Simple in-memory rate limiter: {ip: [timestamps]}
_rate_limits: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_REQUESTS = 5  # max per window for /start


def _check_rate_limit(client_ip: str, max_requests: int = _RATE_LIMIT_MAX_REQUESTS) -> bool:
    """Returns True if request is allowed, False if rate-limited."""
    now = time.time()
    # Clean old entries
    _rate_limits[client_ip] = [t for t in _rate_limits[client_ip] if now - t < _RATE_LIMIT_WINDOW]
    if len(_rate_limits[client_ip]) >= max_requests:
        return False
    _rate_limits[client_ip].append(now)
    return True


def _get_client_ip(request: Request) -> str:
    return (
        request.headers.get("x-real-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )


@router.post("/start", response_model=GuestStartResponse, summary="Start guest charging", description="Creates a guest charging session. Payment via QR code. No registration required. Rate limited: 5 req/min/IP.")
async def start_guest_charging(
    body: GuestStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Начать гостевую зарядку.

    Создаёт сессию со статусом 'pending'.
    После оплаты (webhook) зарядка запускается автоматически.

    Не требует авторизации — доступно для гостей.
    Rate limited: 5 requests per minute per IP.
    """
    client_ip = _get_client_ip(request)
    if not _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests. Try again later.")

    service = GuestChargingService(db)
    result = await service.create_session(
        phone=body.phone,
        station_id=body.station_id,
        connector_id=body.connector_id,
        amount_kgs=body.amount_kgs,
    )
    return result


@router.get("/status/{session_id}", response_model=GuestStatusResponse, summary="Get guest session status", description="Returns real-time status of a guest charging session. Rate limited: 20 req/min/IP.")
async def get_guest_status(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Получить статус гостевой сессии.

    Статусы: pending -> paid -> charging -> completed
    Rate limited: 20 requests per minute per IP.
    """
    client_ip = _get_client_ip(request)
    if not _check_rate_limit(client_ip, max_requests=20):
        raise HTTPException(status_code=429, detail="Too many requests. Try again later.")

    # Валидация session_id (UUID формат)
    if not re.match(r'^[a-fA-F0-9\-]{36}$', session_id):
        return {"success": False, "error": "invalid_id", "message": "Неверный формат session_id"}

    service = GuestChargingService(db)
    return await service.get_status(session_id)


@router.post("/stop/{session_id}", response_model=GuestStopResponse, summary="Stop guest charging", description="Stops an active guest charging session. Requires the phone number used at start for verification. Rate limited: 10 req/min/IP.")
async def stop_guest_charging(
    session_id: str,
    body: GuestStopRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Остановить гостевую зарядку.

    Если оплата не произведена — отменяет сессию.
    Если зарядка идёт — отправляет RemoteStop на станцию.

    Требует phone для подтверждения ownership.
    """
    client_ip = _get_client_ip(request)
    if not _check_rate_limit(client_ip, max_requests=10):
        raise HTTPException(status_code=429, detail="Too many requests. Try again later.")

    if not re.match(r'^[a-fA-F0-9\-]{36}$', session_id):
        return {"success": False, "error": "invalid_id", "message": "Неверный формат session_id"}

    service = GuestChargingService(db)
    return await service.stop_session(session_id, phone=body.phone)
