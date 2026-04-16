"""
Admin OCPP Logs API — просмотр OCPP событий с фильтрацией.
P1 #10: GET /admin/logs/ocpp + /admin/logs/ocpp/stats
Добавлено: Real-time потоковые логи сервера через Server-Sent Events
Добавлено: Per-station OCPP event stream через SSE
"""
import asyncio
import logging
import json
import uuid
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ....db.session import get_db
from ....services.log_service import OCPPLogService
from .operators import get_current_admin, get_current_owner
from ....api.v1.schemas.admin.logs import OnlineStationsResponse
from ....api.v1.schemas.common import ADMIN_RESPONSES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs")

# ============================================================================
# GLOBAL SERVER LOG STREAMING (all logs)
# ============================================================================

# Хранилище подписчиков для потоковой трансляции (fan-out pattern)
_subscribers: list[asyncio.Queue] = []

class LogStreamHandler(logging.Handler):
    """Обработчик логов для отправки в WebUI"""
    def emit(self, record: logging.LogRecord):
        try:
            log_entry = {
                "id": str(uuid.uuid4())[:8],
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "message": self.format(record),
                "module": record.module,
                "function": record.funcName,
            }
            # Отправляем лог всем подписчикам (fan-out)
            for q in list(_subscribers):
                try:
                    q.put_nowait(log_entry)
                except asyncio.QueueFull:
                    pass
        except Exception:
            self.handleError(record)

# Добавляем наш обработчик к корневому логгеру
_stream_handler = LogStreamHandler()
_stream_handler.setFormatter(logging.Formatter(
    '%(levelname)s - %(name)s - %(message)s'
))
logging.getLogger().addHandler(_stream_handler)

# ============================================================================
# PER-STATION OCPP EVENT STREAMING
# ============================================================================

# station_id -> list of subscriber queues
_station_subscribers: dict[str, list[asyncio.Queue]] = {}

def broadcast_station_event(station_id: str, event: dict) -> None:
    """
    Отправить OCPP-событие подписчикам конкретной станции.
    Вызывается из OCPPLogService.log_event() после записи в БД.
    Также отправляем подписчикам "*" (все станции).
    """
    targets = []
    if station_id in _station_subscribers:
        targets.extend(_station_subscribers[station_id])
    # wildcard subscribers (watching all stations)
    if "*" in _station_subscribers:
        targets.extend(_station_subscribers["*"])
    
    for q in list(targets):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


# ============================================================================
# OCPP LOGS API (historical, from DB)
# ============================================================================

@router.get("/ocpp", summary="Get OCPP logs", description="Returns paginated OCPP event logs with filtering by station, type, direction, severity.", responses=ADMIN_RESPONSES)
async def get_ocpp_logs(
    request: Request,
    db: Session = Depends(get_db),
    station_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
):
    """OCPP логи с фильтрацией и пагинацией."""
    get_current_owner(request, db)

    result = OCPPLogService.get_logs(
        db=db,
        station_id=station_id,
        event_type=event_type,
        direction=direction,
        severity=severity,
        from_dt=from_dt,
        to_dt=to_dt,
        search=search,
        page=page,
        per_page=per_page,
    )
    return {"success": True, **result}


@router.get("/ocpp/stats", summary="Get OCPP statistics", description="Returns aggregated OCPP event statistics.", responses=ADMIN_RESPONSES)
async def get_ocpp_stats(
    request: Request,
    db: Session = Depends(get_db),
    station_id: Optional[str] = Query(None),
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
):
    """Статистика OCPP событий по типам и severity."""
    get_current_owner(request, db)

    result = OCPPLogService.get_stats(
        db=db,
        station_id=station_id,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    return {"success": True, **result}


# ============================================================================
# GLOBAL SERVER LOG STREAM (SSE)
# ============================================================================

@router.get("/stream", summary="Stream server logs (SSE)", description="Server-Sent Events stream of real-time server logs.", responses=ADMIN_RESPONSES)
async def stream_server_logs(request: Request, db: Session = Depends(get_db)):
    """Server-Sent Events потоковая трансляция логов сервера (Backend, WebSocket, OCPP)"""
    get_current_admin(request, db)

    client_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.append(client_queue)

    async def event_generator():
        try:
            while True:
                try:
                    log_entry = await asyncio.wait_for(client_queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: log\ndata: {json.dumps(log_entry)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if client_queue in _subscribers:
                _subscribers.remove(client_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================================
# PER-STATION OCPP EVENT STREAM (SSE)
# ============================================================================

@router.get("/station/{station_id}/stream", summary="Stream station OCPP logs (SSE)", description="Server-Sent Events stream of real-time OCPP events for a station. Use station_id='*' for all stations.", responses=ADMIN_RESPONSES)
async def stream_station_logs(
    station_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    SSE: real-time OCPP события конкретной станции.
    station_id = "*" для всех станций.
    """
    get_current_owner(request, db)

    client_queue: asyncio.Queue = asyncio.Queue(maxsize=300)

    if station_id not in _station_subscribers:
        _station_subscribers[station_id] = []
    _station_subscribers[station_id].append(client_queue)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(client_queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: ocpp\ndata: {json.dumps(event, default=str)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            subs = _station_subscribers.get(station_id, [])
            if client_queue in subs:
                subs.remove(client_queue)
            if not subs and station_id in _station_subscribers:
                del _station_subscribers[station_id]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================================
# CONNECTED STATIONS LIST
# ============================================================================

@router.get("/stations/online", summary="Get online stations", description="Returns list of currently online station IDs from Redis.", response_model=OnlineStationsResponse, responses=ADMIN_RESPONSES)
async def get_online_stations(
    request: Request,
    db: Session = Depends(get_db),
):
    """Список станций сейчас подключённых по OCPP WebSocket."""
    get_current_owner(request, db)

    from ocpp_ws_server.redis_manager import redis_manager
    stations = list(await redis_manager.get_stations())
    return {"success": True, "stations": sorted(stations), "count": len(stations)}
