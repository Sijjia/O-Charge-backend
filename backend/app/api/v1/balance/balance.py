from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, Field
from typing import Optional
import logging

from app.db.session import get_db
from app.schemas.ocpp import ClientBalanceInfo
from app.core.config import settings
from app.api.v1.schemas.common import AUTH_RESPONSES, ErrorResponse

router = APIRouter()
logger = logging.getLogger(__name__)


# --- Auto-topup models ---

class AutoTopupSettings(BaseModel):
    auto_topup_enabled: bool = False
    auto_topup_threshold: float = Field(100, ge=0)
    auto_topup_amount: float = Field(500, ge=0)

@router.get("/balance/get", response_model=ClientBalanceInfo, summary="Get my balance", description="Returns balance for the authenticated client (client_id from auth cookie).", responses=AUTH_RESPONSES)
async def get_my_balance(
    request: Request,
    db: Session = Depends(get_db)
) -> ClientBalanceInfo:
    """💰 Получение баланса текущего авторизованного клиента"""
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return await _get_balance(client_id, db)


async def _get_balance(client_id: str, db: Session) -> ClientBalanceInfo:
    """Общая логика получения баланса клиента"""
    try:
        client_info = db.execute(text("""
            SELECT id, balance, updated_at FROM clients WHERE id = :client_id
        """), {"client_id": client_id})

        client = client_info.fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Клиент не найден")

        last_topup = db.execute(text("""
            SELECT paid_at FROM balance_topups
            WHERE client_id = :client_id AND status = 'approved'
            ORDER BY paid_at DESC LIMIT 1
        """), {"client_id": client_id})

        last_topup_date = last_topup.fetchone()

        total_spent = db.execute(text("""
            SELECT COALESCE(SUM(CASE
                WHEN transaction_type = 'charge_reserve' THEN ABS(amount)
                WHEN transaction_type = 'charge_refund' THEN -ABS(amount)
                WHEN transaction_type = 'charge_payment' THEN ABS(amount)
                ELSE 0 END), 0)
            FROM payment_transactions_odengi
            WHERE client_id = :client_id AND transaction_type IN ('charge_reserve', 'charge_refund', 'charge_payment')
        """), {"client_id": client_id})

        spent_row = total_spent.fetchone()
        spent_amount = spent_row[0] if spent_row else 0

        return ClientBalanceInfo(
            client_id=client_id,
            balance=float(client[1]),
            currency=settings.DEFAULT_CURRENCY,
            last_topup_at=last_topup_date[0] if last_topup_date else None,
            total_spent=float(spent_amount) if spent_amount else 0
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения баланса клиента {client_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка получения баланса")


@router.get("/balance/{client_id}", response_model=ClientBalanceInfo, summary="Get client balance", description="Returns the balance, currency, last topup date and total spent for a client.", responses=AUTH_RESPONSES)
async def get_client_balance(
    client_id: str,
    request: Request,
    db: Session = Depends(get_db)
) -> ClientBalanceInfo:
    """💰 Получение информации о балансе клиента (по client_id в URL)"""
    auth_client_id = getattr(request.state, "client_id", None)
    if not auth_client_id or auth_client_id != client_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    return await _get_balance(client_id, db)


# --- Auto-topup endpoints ---

@router.get("/balance/auto-topup", summary="Get auto-topup settings", description="Returns current auto-topup configuration for the authenticated client.", responses=AUTH_RESPONSES)
async def get_auto_topup_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    """Получить настройки автопополнения"""
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    row = db.execute(text("""
        SELECT auto_topup_enabled, auto_topup_threshold, auto_topup_amount
        FROM clients WHERE id = :cid
    """), {"cid": client_id}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    return {
        "success": True,
        "auto_topup_enabled": row.auto_topup_enabled or False,
        "auto_topup_threshold": float(row.auto_topup_threshold or 100),
        "auto_topup_amount": float(row.auto_topup_amount or 500),
    }


@router.put("/balance/auto-topup", summary="Update auto-topup settings", description="Configures automatic balance topup when balance falls below threshold.", responses=AUTH_RESPONSES)
async def update_auto_topup_settings(
    body: AutoTopupSettings,
    request: Request,
    db: Session = Depends(get_db),
):
    """Обновить настройки автопополнения"""
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    db.execute(text("""
        UPDATE clients
        SET auto_topup_enabled = :enabled,
            auto_topup_threshold = :threshold,
            auto_topup_amount = :amount
        WHERE id = :cid
    """), {
        "cid": client_id,
        "enabled": body.auto_topup_enabled,
        "threshold": body.auto_topup_threshold,
        "amount": body.auto_topup_amount,
    })
    db.commit()

    logger.info(f"Auto-topup updated for client {client_id}: enabled={body.auto_topup_enabled}")
    return {"success": True}
