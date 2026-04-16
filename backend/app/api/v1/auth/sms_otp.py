"""
SMS OTP Authentication API
Авторизация через SMS OTP (Nikita SMS)
"""
import logging
from typing import Optional, Dict, Any

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.services.otp_service import otp_service
from app.api.v1.auth.otp import (
    create_access_token,
    create_refresh_token,
    _cookie_params,
    ACCESS_TOKEN_EXPIRE_SECONDS,
    REFRESH_TOKEN_EXPIRE_SECONDS,
)
from app.api.v1.schemas.auth import OtpSendResponse, OtpVerifyResponse

logger = logging.getLogger("app.api.v1.auth.sms_otp")

router = APIRouter(prefix="/auth/sms")


# ========== Pydantic Schemas ==========

class SmsSendOTPRequest(BaseModel):
    """Запрос на отправку OTP кода через SMS"""
    phone: str = Field(..., min_length=10, max_length=20, description="Номер телефона (+996XXXXXXXXX)")

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        v = "".join(c for c in v if c.isdigit() or c == "+")
        if not v.startswith("+"):
            v = "+" + v
        if len(v) < 10:
            raise ValueError("Номер телефона слишком короткий")
        return v


class SmsVerifyOTPRequest(BaseModel):
    """Запрос на верификацию SMS OTP кода"""
    phone: str = Field(..., min_length=10, max_length=20)
    code: str = Field(..., min_length=6, max_length=6, description="6-значный OTP код")

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        v = "".join(c for c in v if c.isdigit() or c == "+")
        if not v.startswith("+"):
            v = "+" + v
        return v

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("Код должен содержать только цифры")
        return v


# ========== API Endpoints ==========

@router.post("/send-otp", summary="Send OTP via SMS", description="Sends a 6-digit OTP code via SMS. Fallback for WhatsApp OTP.", response_model=OtpSendResponse)
async def send_sms_otp(
    request: Request,
    body: SmsSendOTPRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Отправить OTP код через SMS.

    Регистрация и вход объединены: если пользователь не существует,
    он будет создан при успешной верификации кода.

    Rate limit: 1 код в минуту на номер телефона.
    """
    try:
        client_ip = request.client.host if request.client else "unknown"
        success, message = await otp_service.create(
            db, body.phone, purpose="auth", channel="sms", client_ip=client_ip,
        )

        if success:
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "message": message,
                    "phone": body.phone,
                },
            )
        else:
            return JSONResponse(
                status_code=429 if "Подождите" in message else 400,
                content={
                    "success": False,
                    "error": "rate_limit" if "Подождите" in message else "otp_error",
                    "message": message,
                },
            )

    except Exception as e:
        logger.exception(f"Ошибка отправки SMS OTP: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "internal_error",
                "message": "Ошибка сервера",
            },
        )


@router.post("/verify", summary="Verify SMS OTP code", description="Verifies the SMS OTP code and creates a session. Sets auth cookies on success.", response_model=OtpVerifyResponse)
async def verify_sms_otp(
    request: Request,
    body: SmsVerifyOTPRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Проверить SMS OTP код и выполнить вход.

    При успешной верификации:
    1. Проверяем users (владельцы) по phone -> user_type="owner"
    2. Проверяем clients по phone -> user_type="client"
    3. Если не найден -> создаём нового client

    Устанавливает cookie: evp_access, evp_refresh
    """
    try:
        # Верификация OTP
        verified, verify_message = await otp_service.verify(
            db, body.phone, body.code, purpose="auth"
        )

        if not verified:
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error": "invalid_code",
                    "message": verify_message,
                },
            )

        # OTP верный - ищем или создаём пользователя
        user_id: Optional[str] = None
        user_type: str = "client"
        owner_role: Optional[str] = None
        owner_admin_id: Optional[str] = None

        # 1) Проверяем в users (владельцы станций)
        owner_result = await db.execute(
            text("SELECT id, email, role, is_active, admin_id FROM users WHERE phone = :phone LIMIT 1"),
            {"phone": body.phone},
        )
        owner_row = owner_result.fetchone()

        if owner_row:
            user_id = owner_row.id
            user_type = "owner"
            owner_role = owner_row.role
            owner_admin_id = str(owner_row.admin_id) if owner_row.admin_id else None

            # Авто-создание client записи для гибридного функционала
            client_check = await db.execute(
                text("SELECT id FROM clients WHERE id = :id"),
                {"id": user_id},
            )
            if not client_check.fetchone():
                await db.execute(
                    text("""
                        INSERT INTO clients (id, phone, name, balance, status, created_at, updated_at)
                        VALUES (:id, :phone, :name, 0, 'active', NOW(), NOW())
                    """),
                    {"id": user_id, "phone": body.phone, "name": owner_row.email or ""},
                )
                await db.commit()
                logger.info(f"[SMS OTP] Created client record for owner: {body.phone}")

            logger.info(f"[SMS OTP] Owner login: {body.phone} -> {user_id}")
        else:
            # 2) Проверяем в clients
            client_result = await db.execute(
                text("SELECT id, name, status FROM clients WHERE phone = :phone LIMIT 1"),
                {"phone": body.phone},
            )
            client_row = client_result.fetchone()

            if client_row:
                user_id = client_row.id
                user_type = "client"
                logger.info(f"[SMS OTP] Client login: {body.phone} -> {user_id}")
            else:
                # 3) Создаём нового client
                from uuid import uuid4
                new_id = str(uuid4())
                await db.execute(
                    text("""
                        INSERT INTO clients (id, phone, status, created_at, updated_at)
                        VALUES (:id, :phone, 'active', NOW(), NOW())
                    """),
                    {"id": new_id, "phone": body.phone},
                )
                await db.commit()
                user_id = new_id
                user_type = "client"
                logger.info(f"[SMS OTP] New client created: {body.phone} -> {user_id}")

        # Создаём JWT токены
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token(user_id)

        # Формируем ответ
        resp_content: Dict[str, Any] = {
            "success": True,
            "message": "Авторизация успешна",
            "user_type": user_type,
            "user_id": user_id,
        }
        if user_type == "owner":
            resp_content["role"] = owner_role
            resp_content["admin_id"] = owner_admin_id

        resp = JSONResponse(content=resp_content)

        # Устанавливаем новые cookies (очистка старых не нужна — перезапись)
        resp.set_cookie("evp_access", access_token, **_cookie_params(ACCESS_TOKEN_EXPIRE_SECONDS, request))
        resp.set_cookie("evp_refresh", refresh_token, **_cookie_params(REFRESH_TOKEN_EXPIRE_SECONDS, request))

        return resp

    except Exception as e:
        logger.exception(f"Ошибка верификации SMS OTP: {e}")
        await db.rollback()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "internal_error",
                "message": "Ошибка сервера",
            },
        )
