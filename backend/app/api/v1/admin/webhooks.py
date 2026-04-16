"""
Admin API: Webhook subscriptions CRUD + test + delivery logs
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
import logging

from app.db.session import get_db
from .operators import get_current_admin
from app.api.v1.schemas.admin.webhooks import (
    WebhookCreateRequest, WebhookUpdateRequest,
    WebhookListResponse, WebhookDetailResponse, WebhookMutationResponse,
    WebhookLogResponse,
)
from app.api.v1.schemas.common import ADMIN_RESPONSES
from app.services.webhook_service import WEBHOOK_EVENTS, webhook_service

router = APIRouter(prefix="/webhooks")
logger = logging.getLogger(__name__)


@router.get("/events/list", summary="List available webhook events", responses=ADMIN_RESPONSES)
async def list_events(request: Request, db: Session = Depends(get_db)):
    """Список всех доступных типов событий для подписки"""
    admin = get_current_admin(request, db)
    return {
        "success": True,
        "events": [
            {"name": "connector.status_changed", "description": "Статус коннектора изменился (available/occupied/faulted/unavailable)"},
            {"name": "charging.started", "description": "Зарядка началась (StartTransaction)"},
            {"name": "charging.progress", "description": "Обновление показаний (MeterValues — энергия, мощность, SoC)"},
            {"name": "charging.completed", "description": "Зарядка завершена (StopTransaction — финальная стоимость)"},
            {"name": "charging.error", "description": "Ошибка при зарядке (OCPP error code)"},
            {"name": "station.online", "description": "Станция вышла в онлайн (heartbeat восстановлен)"},
            {"name": "station.offline", "description": "Станция ушла в оффлайн (heartbeat > 5 мин)"},
        ],
    }


@router.get("", response_model=WebhookListResponse, summary="List webhook subscriptions", responses=ADMIN_RESPONSES)
async def list_webhooks(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    rows = db.execute(text("""
        SELECT id, name, url, events, is_active, created_at, updated_at
        FROM webhook_subscriptions ORDER BY created_at DESC
    """)).fetchall()
    return {
        "success": True,
        "data": [
            {
                "id": r.id, "name": r.name, "url": r.url,
                "events": list(r.events) if r.events else [],
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.post("", response_model=WebhookMutationResponse, summary="Create webhook subscription", responses=ADMIN_RESPONSES)
async def create_webhook(body: WebhookCreateRequest, request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)

    # Validate events
    invalid = [e for e in body.events if e not in WEBHOOK_EVENTS]
    if invalid:
        raise HTTPException(400, f"Unknown events: {invalid}. Valid: {WEBHOOK_EVENTS}")

    row = db.execute(text("""
        INSERT INTO webhook_subscriptions (name, url, secret, events)
        VALUES (:name, :url, :secret, :events)
        RETURNING id
    """), {
        "name": body.name,
        "url": body.url,
        "secret": body.secret,
        "events": body.events,
    }).fetchone()
    db.commit()

    logger.info(f"[Webhook] Created subscription {row.id} by {admin['id']}")
    return {"success": True, "webhook_id": row.id, "message": "Webhook создан"}


@router.get("/{webhook_id}", response_model=WebhookDetailResponse, summary="Get webhook details", responses=ADMIN_RESPONSES)
async def get_webhook(webhook_id: str, request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    r = db.execute(text("""
        SELECT id, name, url, events, is_active, created_at, updated_at
        FROM webhook_subscriptions WHERE id = :id
    """), {"id": webhook_id}).fetchone()
    if not r:
        raise HTTPException(404, "Webhook не найден")
    return {
        "success": True,
        "webhook": {
            "id": r.id, "name": r.name, "url": r.url,
            "events": list(r.events) if r.events else [],
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        },
    }


@router.put("/{webhook_id}", response_model=WebhookMutationResponse, summary="Update webhook", responses=ADMIN_RESPONSES)
async def update_webhook(webhook_id: str, body: WebhookUpdateRequest, request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)

    existing = db.execute(text("SELECT id FROM webhook_subscriptions WHERE id = :id"), {"id": webhook_id}).fetchone()
    if not existing:
        raise HTTPException(404, "Webhook не найден")

    if body.events:
        invalid = [e for e in body.events if e not in WEBHOOK_EVENTS]
        if invalid:
            raise HTTPException(400, f"Unknown events: {invalid}. Valid: {WEBHOOK_EVENTS}")

    sets = []
    params = {"id": webhook_id}
    for field in ["name", "url", "secret", "events", "is_active"]:
        val = getattr(body, field, None)
        if val is not None:
            sets.append(f"{field} = :{field}")
            params[field] = val
    if not sets:
        raise HTTPException(400, "Нет полей для обновления")

    sets.append("updated_at = NOW()")
    db.execute(text(f"UPDATE webhook_subscriptions SET {', '.join(sets)} WHERE id = :id"), params)
    db.commit()

    logger.info(f"[Webhook] Updated {webhook_id} by {admin['id']}")
    return {"success": True, "webhook_id": webhook_id, "message": "Webhook обновлён"}


@router.delete("/{webhook_id}", response_model=WebhookMutationResponse, summary="Delete webhook", responses=ADMIN_RESPONSES)
async def delete_webhook(webhook_id: str, request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    result = db.execute(text("DELETE FROM webhook_subscriptions WHERE id = :id"), {"id": webhook_id})
    if result.rowcount == 0:
        raise HTTPException(404, "Webhook не найден")
    db.commit()
    logger.info(f"[Webhook] Deleted {webhook_id} by {admin['id']}")
    return {"success": True, "webhook_id": webhook_id, "message": "Webhook удалён"}


@router.get("/{webhook_id}/logs", response_model=WebhookLogResponse, summary="Get delivery logs", responses=ADMIN_RESPONSES)
async def get_webhook_logs(
    webhook_id: str, request: Request, db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):
    admin = get_current_admin(request, db)
    rows = db.execute(text("""
        SELECT id, event_type, status_code, success, attempt, error, created_at,
               COUNT(*) OVER() as _total
        FROM webhook_delivery_log
        WHERE subscription_id = :id
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """), {"id": webhook_id, "limit": limit, "offset": offset}).fetchall()

    total = rows[0]._total if rows else 0
    return {
        "success": True,
        "logs": [
            {
                "id": r.id, "event_type": r.event_type,
                "status_code": r.status_code, "success": r.success,
                "attempt": r.attempt, "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": total,
    }


@router.post("/{webhook_id}/test", summary="Send test webhook", responses=ADMIN_RESPONSES)
async def test_webhook(webhook_id: str, request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)
    row = db.execute(text("SELECT url, secret FROM webhook_subscriptions WHERE id = :id"), {"id": webhook_id}).fetchone()
    if not row:
        raise HTTPException(404, "Webhook не найден")

    result = await webhook_service.send_test(row.url, row.secret)
    return {"success": result["success"], "data": result}
