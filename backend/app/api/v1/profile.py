from fastapi import APIRouter, Depends, Request
import httpx
import logging
from app.core.config import settings
from app.core.security_middleware import RedisRateLimiter
from app.core.logging_config import correlation_id
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, Field, field_validator

from app.db.session import get_db, get_async_db
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas.profile import ProfileUpdateResponse, PhoneChangeRequestResponse, PhoneChangeConfirmResponse
from app.api.v1.schemas.common import AUTH_RESPONSES, ErrorResponse, MessageResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/profile",
    summary="Get profile",
    description="Returns the authenticated user's profile. Response shape depends on user_type (client vs owner).",
    responses=AUTH_RESPONSES,
)
async def get_profile(request: Request, db: Session = Depends(get_db)):
    """
    Универсальный профиль для clients И users (владельцев станций).

    Гибридный подход: owner также может иметь клиентские данные (баланс, зарядки).
    Возвращает user_type: "client" | "owner" для определения интерфейса на фронте.
    """
    user_id = getattr(request.state, "client_id", None)
    if not user_id:
        return {"success": False, "error": "unauthorized", "message": "Missing or invalid authentication"}

    # 1) Проверяем в users (владельцы станций) — они имеют расширенные права
    owner_row = db.execute(
        text("SELECT id, email, role, is_active, admin_id FROM users WHERE id = :id"),
        {"id": user_id}
    ).fetchone()

    if owner_row:
        # Owner найден — получаем также клиентские данные если есть
        client_row = db.execute(
            text("SELECT phone, name, balance, status FROM clients WHERE id = :id"),
            {"id": user_id}
        ).fetchone()

        # Получаем количество станций и локаций для владельца
        stats = db.execute(
            text("""
                SELECT
                    (SELECT COUNT(*) FROM stations WHERE user_id = :id) as stations_count,
                    (SELECT COUNT(*) FROM locations WHERE user_id = :id OR admin_id = :id) as locations_count
            """),
            {"id": user_id}
        ).fetchone()

        return {
            "success": True,
            "user_type": "owner",
            "client_id": owner_row.id,
            "user_id": owner_row.id,
            "email": owner_row.email,
            "role": owner_row.role,
            "is_active": owner_row.is_active,
            "admin_id": str(owner_row.admin_id) if owner_row.admin_id else None,
            "stations_count": stats.stations_count if stats else 0,
            "locations_count": stats.locations_count if stats else 0,
            # Клиентские данные (если есть запись в clients)
            "phone": client_row.phone if client_row else None,
            "name": client_row.name if client_row else None,
            "balance": float(client_row.balance or 0) if client_row else 0,
            "status": client_row.status if client_row else "active",
        }

    # 2) Обычный клиент (не owner)
    client_row = db.execute(
        text("SELECT id, phone, name, balance, status FROM clients WHERE id = :id"),
        {"id": user_id}
    ).fetchone()

    if client_row:
        return {
            "success": True,
            "user_type": "client",
            "client_id": client_row.id,
            "phone": client_row.phone,
            "name": client_row.name,
            "balance": float(client_row.balance or 0),
            "status": client_row.status,
        }

    return {"success": False, "error": "not_found", "message": "User not found"}


class ProfileUpdateRequest(BaseModel):
    """Обновление профиля пользователя"""
    name: str | None = Field(None, min_length=1, max_length=100, description="Имя пользователя")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Имя не может быть пустым")
        return v


@router.put(
    "/profile",
    summary="Update profile",
    description="Updates the client's profile name.",
    response_model=ProfileUpdateResponse,
    responses=AUTH_RESPONSES,
)
async def update_profile(
    request: Request,
    body: ProfileUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Обновление профиля клиента (имя).
    ТЗ PROF-01: пользователь должен иметь возможность редактировать профиль.
    """
    user_id = getattr(request.state, "client_id", None)
    if not user_id:
        return {"success": False, "error": "unauthorized", "message": "Not authenticated"}

    if body.name is None:
        return {"success": False, "error": "no_changes", "message": "Нет данных для обновления"}

    result = db.execute(
        text("UPDATE clients SET name = :name, updated_at = NOW() WHERE id = :user_id RETURNING id, name, phone, balance, status"),
        {"name": body.name, "user_id": user_id},
    ).fetchone()

    if not result:
        return {"success": False, "error": "not_found", "message": "Пользователь не найден"}

    db.commit()

    logger.info(f"[Profile] Updated for user {user_id}: name={body.name}")

    return {
        "success": True,
        "message": "Профиль обновлён",
        "profile": {
            "client_id": result.id,
            "name": result.name,
            "phone": result.phone,
            "balance": float(result.balance or 0),
            "status": result.status,
        }
    }


class PhoneChangeRequest(BaseModel):
    """Запрос на смену номера телефона — шаг 1: отправить OTP на новый номер"""
    new_phone: str = Field(..., min_length=10, max_length=20, description="Новый номер (+996XXXXXXXXX)")

    @field_validator("new_phone")
    @classmethod
    def normalize(cls, v: str) -> str:
        v = "".join(c for c in v if c.isdigit() or c == "+")
        if not v.startswith("+"):
            v = "+" + v
        if len(v) < 10:
            raise ValueError("Номер телефона слишком короткий")
        return v


class PhoneChangeConfirm(BaseModel):
    """Подтверждение смены номера — шаг 2: код с нового номера"""
    new_phone: str = Field(..., min_length=10, max_length=20)
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("new_phone")
    @classmethod
    def normalize(cls, v: str) -> str:
        v = "".join(c for c in v if c.isdigit() or c == "+")
        if not v.startswith("+"):
            v = "+" + v
        return v


@router.post(
    "/profile/phone/request-change",
    summary="Request phone number change",
    description="Sends OTP to the new phone number for verification.",
    response_model=PhoneChangeRequestResponse,
    responses=AUTH_RESPONSES,
)
async def request_phone_change(
    request: Request,
    body: PhoneChangeRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Шаг 1: Отправить OTP на новый номер телефона"""
    user_id = getattr(request.state, "client_id", None)
    if not user_id:
        return {"success": False, "error": "unauthorized", "message": "Not authenticated"}

    # Проверяем что новый номер не занят
    existing_client = await db.execute(
        text("SELECT id FROM clients WHERE phone = :phone AND id != :user_id LIMIT 1"),
        {"phone": body.new_phone, "user_id": user_id},
    )
    if existing_client.fetchone():
        return {"success": False, "error": "phone_taken", "message": "Этот номер уже зарегистрирован"}

    existing_owner = await db.execute(
        text("SELECT id FROM users WHERE phone = :phone AND id != :user_id LIMIT 1"),
        {"phone": body.new_phone, "user_id": user_id},
    )
    if existing_owner.fetchone():
        return {"success": False, "error": "phone_taken", "message": "Этот номер уже зарегистрирован"}

    # Отправляем OTP на новый номер
    from app.services.otp_service import otp_service
    client_ip = request.client.host if request.client else "unknown"
    success, message = await otp_service.create(
        db, body.new_phone, purpose="phone_change", channel="sms", client_ip=client_ip,
    )

    if success:
        return {"success": True, "message": "OTP отправлен на новый номер", "phone": body.new_phone}
    else:
        status_code = 429 if "Подождите" in message else 400
        return {"success": False, "error": "otp_error", "message": message}


@router.post(
    "/profile/phone/confirm-change",
    summary="Confirm phone number change",
    description="Confirms phone change with the OTP code sent to the new number.",
    response_model=PhoneChangeConfirmResponse,
    responses=AUTH_RESPONSES,
)
async def confirm_phone_change(
    request: Request,
    body: PhoneChangeConfirm,
    db: AsyncSession = Depends(get_async_db),
):
    """Шаг 2: Подтвердить смену номера OTP кодом"""
    user_id = getattr(request.state, "client_id", None)
    if not user_id:
        return {"success": False, "error": "unauthorized", "message": "Not authenticated"}

    # Верифицируем OTP
    from app.services.otp_service import otp_service
    verified, verify_message = await otp_service.verify(db, body.new_phone, body.code, purpose="phone_change")

    if not verified:
        return {"success": False, "error": "invalid_code", "message": verify_message}

    # Обновляем номер в clients
    await db.execute(
        text("UPDATE clients SET phone = :new_phone, updated_at = NOW() WHERE id = :user_id"),
        {"new_phone": body.new_phone, "user_id": user_id},
    )

    # Если это также owner — обновляем в users
    await db.execute(
        text("UPDATE users SET phone = :new_phone, updated_at = NOW() WHERE id = :user_id"),
        {"new_phone": body.new_phone, "user_id": user_id},
    )

    await db.commit()

    logger.info(f"[Profile] Phone changed for user {user_id} -> {body.new_phone}")

    return {
        "success": True,
        "message": "Номер телефона успешно изменён",
        "new_phone": body.new_phone,
    }


@router.post(
    "/account/delete-request",
    summary="Request account deletion",
    description="Marks the account for deletion. Irreversible after processing.",
    responses=AUTH_RESPONSES,
)
async def delete_request(request: Request, db: Session = Depends(get_db)):
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        return {"success": False, "error": "unauthorized", "message": "Missing or invalid authentication"}

    db.execute(text("""
        INSERT INTO balance_audit_log (client_id, attempted_change, success, error_message)
        VALUES (:client_id, 0, false, 'delete_requested')
    """), {"client_id": client_id})
    db.commit()

    # Здесь можно запустить фоновые задачи анонимизации или вызвать RPC Supabase

    return {"success": True, "message": "Удаление аккаунта запрошено"}



@router.post(
    "/auth/logout-all",
    summary="Logout from all devices",
    description="Invalidates all active sessions for the current user.",
    responses=AUTH_RESPONSES,
)
async def logout_all_devices(request: Request):
    """Принудительный logout пользователя на всех устройствах через Supabase Admin API"""
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        return {"success": False, "error": "unauthorized", "message": "Missing or invalid authentication"}

    # Rate limit: не более 5 запросов в час на пользователя
    try:
        limiter = RedisRateLimiter("logout", max_requests=5, window_seconds=3600)
        allowed = await limiter.is_allowed(client_id)
        if not allowed:
            return {"success": False, "error": "too_many_requests", "message": "Logout-all rate limit exceeded"}
    except Exception as e:
        logger.warning(f"Rate limit check failed (fail-open): {e}")

    admin_url = f"{settings.SUPABASE_URL}/auth/v1/admin/users/{client_id}/logout"
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(admin_url, headers=headers)
            if resp.status_code in (200, 204):
                # Аудит
                try:
                    db = next(get_db())
                    db.execute(text("""
                        INSERT INTO payment_audit_log (
                            request_id, operation_type, client_id, amount,
                            client_ip, request_data, response_data, success, created_at
                        ) VALUES (
                            :request_id, :operation_type, :client_id, :amount,
                            :client_ip, CAST(:request_data AS jsonb), CAST(:response_data AS jsonb), :success, NOW()
                        )
                    """), {
                        "request_id": correlation_id.get(),
                        "operation_type": "logout_all",
                        "client_id": client_id,
                        "amount": None,
                        "client_ip": request.client.host if request.client else "unknown",
                        "request_data": "{}",
                        "response_data": "{}",
                        "success": True
                    })
                    db.commit()
                except Exception as e:
                    logger.warning(f"Audit log for logout_all failed: {e}")
                return {"success": True, "message": "Все сессии завершены"}
            return {"success": False, "error": "supabase_error", "status_code": resp.status_code}
    except Exception as e:
        logger.exception("Ошибка при выходе из всех сессий")
        return {"success": False, "error": "internal_error", "message": "Внутренняя ошибка сервера"}

