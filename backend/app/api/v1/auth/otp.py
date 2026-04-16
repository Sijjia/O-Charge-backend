"""
OTP Authentication API
Phone-only авторизация через WhatsApp OTP
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from uuid import uuid4

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from jose import jwt

from app.core.config import settings
from app.db.session import get_async_db
from app.services.otp_service import otp_service
from app.api.v1.schemas.auth import OtpSendResponse, OtpVerifyResponse, OtpStatusResponse

logger = logging.getLogger("app.api.v1.auth.otp")

router = APIRouter(prefix="/auth/otp")


# ========== Pydantic Schemas ==========

class SendOTPRequest(BaseModel):
    """Запрос на отправку OTP кода"""
    phone: str = Field(..., min_length=10, max_length=20, description="Номер телефона в международном формате")

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        # Убираем пробелы и дефисы
        v = "".join(c for c in v if c.isdigit() or c == "+")
        # Добавляем + если нет
        if not v.startswith("+"):
            v = "+" + v
        # Проверяем минимальную длину (+ и 9+ цифр)
        if len(v) < 10:
            raise ValueError("Номер телефона слишком короткий")
        return v


class VerifyOTPRequest(BaseModel):
    """Запрос на верификацию OTP кода"""
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


# ========== Helper Functions ==========

ACCESS_TOKEN_EXPIRE_SECONDS = 24 * 3600  # 24 часа (для тестирования)
REFRESH_TOKEN_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 дней


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _mint_jwt(subject: str, ttl_seconds: int, token_type: str) -> str:
    """Создание JWT токена"""
    now = _now_ts()
    payload: Dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + ttl_seconds,
        "typ": token_type,
        "iss": "redpetroleum-backend",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def create_access_token(user_id: str) -> str:
    return _mint_jwt(user_id, ACCESS_TOKEN_EXPIRE_SECONDS, "access")


def create_refresh_token(user_id: str) -> str:
    return _mint_jwt(user_id, REFRESH_TOKEN_EXPIRE_SECONDS, "refresh")


def _cookie_params(ttl_seconds: int, request: Optional[Request] = None):
    """Параметры для cookie с адаптацией для localhost и HTTP"""
    import re

    is_insecure = False
    if request:
        origin = request.headers.get("origin", "")
        # localhost / 127.0.0.1 → insecure
        if "localhost" in origin or "127.0.0.1" in origin:
            is_insecure = True
        # HTTP origin (не HTTPS) → insecure (напр. http://62.171.149.139)
        elif origin.startswith("http://"):
            is_insecure = True
        # IP-адрес в origin → insecure (куки с domain не работают для IP)
        elif re.search(r"://\d+\.\d+\.\d+\.\d+", origin):
            is_insecure = True

    if is_insecure:
        same_site = "lax"
        secure = False
        domain = None  # Не ставим domain для IP-адресов и localhost
    else:
        same_site = "none"
        secure = True
        domain = os.getenv("COOKIE_DOMAIN") or None
        # Auto-derive cookie domain from DOMAIN env var (e.g. https://ocpp.redp.asystem.kg → .redp.asystem.kg)
        if not domain:
            domain_env = os.getenv("DOMAIN", "")
            if domain_env:
                from urllib.parse import urlparse
                host = urlparse(domain_env).hostname or ""
                # Strip first subdomain (e.g. ocpp.redp.asystem.kg → .redp.asystem.kg)
                parts = host.split(".")
                if len(parts) >= 3:
                    domain = "." + ".".join(parts[1:])

    params: Dict[str, Any] = {
        "httponly": True,
        "secure": secure,
        "samesite": same_site,
        "path": "/",
        "max_age": ttl_seconds,
        "expires": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
    }
    if domain:
        params["domain"] = domain
    return params


# ========== API Endpoints ==========

@router.post("/send", summary="Send OTP via WhatsApp", description="Sends a 6-digit OTP code to the specified phone number via WhatsApp. Rate limited.", response_model=OtpSendResponse)
async def send_otp(
    request: Request,
    body: SendOTPRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Отправить OTP код в WhatsApp.

    Регистрация и вход объединены: если пользователь не существует,
    он будет создан при успешной верификации кода.

    Rate limit: 1 код в минуту на номер телефона.
    """
    try:
        # CSRF проверка (опционально для /send, но рекомендуется)
        # Пропускаем для упрощения первого запроса

        client_ip = request.client.host if request.client else "unknown"
        success, message = await otp_service.create(
            db, body.phone, purpose="auth", client_ip=client_ip,
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
        logger.exception(f"Ошибка отправки OTP: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "internal_error",
                "message": "Ошибка сервера",
            },
        )


@router.post("/verify", summary="Verify OTP code", description="Verifies the OTP code and creates a session. Sets evp_access and evp_refresh cookies on success.", response_model=OtpVerifyResponse)
async def verify_otp(
    request: Request,
    body: VerifyOTPRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Проверить OTP код и выполнить вход.

    При успешной верификации:
    1. Проверяем users (владельцы) по phone → user_type="owner"
    2. Проверяем clients по phone → user_type="client"
    3. Если не найден → создаём нового client

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

        # 1) Проверяем в users (владельцы станций) по phone
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
                logger.info(f"[OTP] Created client record for owner: {body.phone}")

            logger.info(f"[OTP] Owner login: {body.phone} -> {user_id}, role={owner_role}")
        else:
            # 2) Проверяем в clients по phone
            client_result = await db.execute(
                text("SELECT id, email, name, status FROM clients WHERE phone = :phone LIMIT 1"),
                {"phone": body.phone},
            )
            client_row = client_result.fetchone()

            if client_row:
                user_id = client_row.id
                user_type = "client"
                logger.info(f"[OTP] Client login: {body.phone} -> {user_id}")
            else:
                # 3) Создаём нового client
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
                logger.info(f"[OTP] New client created: {body.phone} -> {user_id}")

        # Создаём JWT токены
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token(user_id)

        # Формируем ответ
        resp_content = {
            "success": True,
            "message": "Авторизация успешна",
            "user_type": user_type,
            "user_id": user_id,
        }
        # Добавляем owner-специфичные поля
        if user_type == "owner":
            resp_content["role"] = owner_role
            resp_content["admin_id"] = owner_admin_id

        resp = JSONResponse(content=resp_content)

        # Устанавливаем новые cookies (перезапись)
        resp.set_cookie("evp_access", access_token, **_cookie_params(ACCESS_TOKEN_EXPIRE_SECONDS, request))
        resp.set_cookie("evp_refresh", refresh_token, **_cookie_params(REFRESH_TOKEN_EXPIRE_SECONDS, request))

        return resp

    except Exception as e:
        logger.exception(f"Ошибка верификации OTP: {e}")
        await db.rollback()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "internal_error",
                "message": "Ошибка сервера",
            },
        )


@router.get("/status", summary="Check OTP send availability", description="Returns whether a new OTP can be sent to the given phone number, and wait time if rate-limited.", response_model=OtpStatusResponse)
async def otp_status(
    phone: str,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Проверить статус последнего OTP кода (для отладки).

    Returns:
        can_send: Можно ли отправить новый код
        wait_seconds: Сколько секунд ждать до следующей отправки
    """
    try:
        # Нормализация телефона
        phone = "".join(c for c in phone if c.isdigit() or c == "+")
        if not phone.startswith("+"):
            phone = "+" + phone

        can_send, wait_seconds = await otp_service.check_rate_limit(db, phone)

        return {
            "success": True,
            "phone": phone,
            "can_send": can_send,
            "wait_seconds": wait_seconds or 0,
        }

    except Exception as e:
        logger.exception(f"Ошибка проверки статуса OTP: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "internal_error",
                "message": "Ошибка сервера",
            },
        )
