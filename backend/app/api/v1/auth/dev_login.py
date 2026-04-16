"""
Dev Login API — быстрый вход без OTP для разработки и тестирования.
Работает ТОЛЬКО если APP_ENV != "production".
"""
import logging
from typing import Optional

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_async_db
from app.api.v1.auth.otp import (
    create_access_token,
    create_refresh_token,
    _cookie_params,
    ACCESS_TOKEN_EXPIRE_SECONDS,
    REFRESH_TOKEN_EXPIRE_SECONDS,
)
from app.api.v1.schemas.auth import DevLoginResponse

logger = logging.getLogger("app.api.v1.auth.dev_login")

router = APIRouter(prefix="/auth")


class DevLoginRequest(BaseModel):
    """Запрос на dev-вход"""
    user_id: str = Field(..., description="ID пользователя из таблицы users или clients")


@router.post("/dev-login", summary="Developer login (non-production)", description="Bypasses OTP and logs in as any test user by user_id. Returns 403 in production.", response_model=DevLoginResponse)
async def dev_login(
    request: Request,
    body: DevLoginRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Быстрый вход по user_id без OTP.
    Доступен ТОЛЬКО в dev/staging окружении.
    """
    # Блокируем в production (если не разрешено явно через ALLOW_DEV_LOGIN)
    if settings.is_production and not settings.ALLOW_DEV_LOGIN:
        return JSONResponse(
            status_code=403,
            content={"success": False, "error": "forbidden", "message": "Dev login disabled in production"},
        )

    try:
        user_type = "client"
        role: Optional[str] = None
        admin_id: Optional[str] = None
        is_partner = False

        # 1) Ищем в users (владельцы/операторы/админы)
        owner_result = await db.execute(
            text("SELECT id, email, role, is_active, admin_id FROM users WHERE id = :id LIMIT 1"),
            {"id": body.user_id},
        )
        owner_row = owner_result.fetchone()

        if owner_row:
            user_type = "owner"
            role = owner_row.role
            admin_id = str(owner_row.admin_id) if owner_row.admin_id else None

            # Авто-создание client записи
            client_check = await db.execute(
                text("SELECT id FROM clients WHERE id = :id"),
                {"id": body.user_id},
            )
            if not client_check.fetchone():
                await db.execute(
                    text("""
                        INSERT INTO clients (id, phone, name, balance, status, created_at, updated_at)
                        VALUES (:id, :phone, :name, 0, 'active', NOW(), NOW())
                    """),
                    {"id": body.user_id, "phone": "", "name": owner_row.email or "Dev User"},
                )
                await db.commit()

            logger.info(f"[DevLogin] Owner login: {body.user_id}, role={role}")
        else:
            # 2) Ищем в clients
            client_result = await db.execute(
                text("SELECT id, name, status FROM clients WHERE id = :id LIMIT 1"),
                {"id": body.user_id},
            )
            client_row = client_result.fetchone()

            if client_row:
                user_type = "client"
                logger.info(f"[DevLogin] Client login: {body.user_id}")
            else:
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "not_found", "message": f"User {body.user_id} not found"},
                )

        # Создаём JWT токены
        access_token = create_access_token(body.user_id)
        refresh_token = create_refresh_token(body.user_id)

        resp_content = {
            "success": True,
            "message": "Dev login successful",
            "user_type": user_type,
            "user_id": body.user_id,
        }
        if user_type == "owner":
            resp_content["role"] = role
            resp_content["admin_id"] = admin_id

        resp = JSONResponse(content=resp_content)

        # Устанавливаем cookies
        resp.set_cookie("evp_access", access_token, **_cookie_params(ACCESS_TOKEN_EXPIRE_SECONDS, request))
        resp.set_cookie("evp_refresh", refresh_token, **_cookie_params(REFRESH_TOKEN_EXPIRE_SECONDS, request))

        return resp

    except Exception as e:
        logger.exception(f"Dev login error: {e}")
        await db.rollback()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "internal_error", "message": str(e)},
        )


class DevSetBalanceRequest(BaseModel):
    """Установка баланса для тестирования"""
    client_id: str = Field(..., description="ID клиента")
    balance: float = Field(..., ge=0, description="Новый баланс")


@router.post("/dev-set-balance", summary="Set client balance (non-production)", description="Directly sets a client's balance for testing. Returns 403 in production.")
async def dev_set_balance(
    body: DevSetBalanceRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Установка баланса клиента для тестирования. Только dev/staging."""
    if settings.is_production and not settings.ALLOW_DEV_LOGIN:
        return JSONResponse(
            status_code=403,
            content={"success": False, "error": "forbidden", "message": "Dev tools disabled in production"},
        )

    result = await db.execute(
        text("UPDATE clients SET balance = :balance, updated_at = NOW() WHERE id = :client_id RETURNING id, balance"),
        {"client_id": body.client_id, "balance": body.balance},
    )
    row = result.fetchone()
    if not row:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "not_found", "message": f"Client {body.client_id} not found"},
        )
    await db.commit()
    logger.info(f"[DevSetBalance] {body.client_id} balance set to {body.balance}")
    return {"success": True, "client_id": row.id, "balance": float(row.balance)}
