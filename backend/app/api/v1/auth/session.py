"""
Cookie-based аутентификация поверх Supabase.
"""
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from starlette.responses import JSONResponse
import httpx
import os
from datetime import timedelta, datetime, timezone
from typing import Optional, Dict, Any
from jose import jwt
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging

from app.core.config import settings
from app.db.session import get_db
from app.api.v1.schemas.auth import CsrfResponse, LoginResponse
from app.api.v1.schemas.common import AUTH_RESPONSES, ErrorResponse

logger = logging.getLogger("app.api.v1.auth.session")

router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, min_length=5, max_length=32)
    password: str

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return v.strip()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: Optional[EmailStr]) -> Optional[EmailStr]:
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if not v:
            raise ValueError("password is required")
        return v

    @property
    def is_email_flow(self) -> bool:
        return self.email is not None

    @property
    def is_phone_flow(self) -> bool:
        return self.phone is not None

    @model_validator(mode="after")
    def ensure_email_or_phone(self) -> "LoginRequest":
        if not self.email and not self.phone:
            raise ValueError("either email or phone must be provided")
        return self


def _cookie_params(ttl_seconds: int, strict: bool = False, samesite: Optional[str] = None, request: Optional[Request] = None):
    """
    Генерирует параметры для установки cookie с автоматической адаптацией для localhost.

    Args:
        ttl_seconds: Время жизни cookie в секундах
        strict: Если True, использовать более строгий SameSite
        samesite: Явное указание SameSite (если None - автоопределение)
        request: Request объект для определения origin (опционально)

    Returns:
        dict: Параметры для set_cookie()

    Notes:
        - Для localhost: SameSite=Lax, Secure=False (разрешает HTTP)
        - Для production: SameSite=None, Secure=True (только HTTPS)
    """
    # Определяем окружение по origin
    import re as _re
    is_insecure = False
    if request:
        origin = request.headers.get("origin", "")
        if "localhost" in origin or "127.0.0.1" in origin:
            is_insecure = True
        elif origin.startswith("http://"):
            is_insecure = True
        elif _re.search(r"://\d+\.\d+\.\d+\.\d+", origin):
            is_insecure = True

    # Адаптируем параметры под окружение
    if is_insecure:
        same_site = "lax"
        secure = False
        domain = None
    else:
        same_site = (samesite or ("strict" if strict else "none")).lower()
        secure = True
        domain = os.getenv("COOKIE_DOMAIN") or None

    params = {
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


ACCESS_TOKEN_EXPIRE_SECONDS = 10 * 60  # 10 минут
REFRESH_TOKEN_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 дней


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _mint_jwt(subject: str, ttl_seconds: int, token_type: str) -> str:
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


@router.get("/csrf", summary="Get CSRF token", description="Returns a CSRF token and sets it as a cookie. Required before POST/PUT/DELETE requests.", response_model=CsrfResponse)
async def get_csrf(request: Request):
    """
    Получение CSRF токена для защиты от CSRF атак.

    Использует double-submit cookie pattern:
    - Токен в cookie (XSRF-TOKEN) - автоматически отправляется браузером
    - Токен в response body - фронтенд должен отправить в заголовке X-CSRF-Token

    Cookie не HttpOnly, чтобы JavaScript мог прочитать и отправить в заголовке.
    """
    # Если токен уже есть в cookie, переиспользуем его, чтобы не ломать параллельные/повторные запросы
    existing = request.cookies.get("XSRF-TOKEN")
    token = existing if existing else os.urandom(16).hex()
    resp = JSONResponse({"success": True, "csrf_token": token})

    # Определяем параметры cookie в зависимости от окружения
    origin = request.headers.get("origin", "")
    is_localhost = "localhost" in origin or "127.0.0.1" in origin

    # Не HttpOnly, чтобы фронт мог прочитать и пробросить в X-CSRF-Token
    import re as _re
    _origin = request.headers.get("origin", "")
    _is_insecure = ("localhost" in _origin or "127.0.0.1" in _origin
                     or _origin.startswith("http://")
                     or bool(_re.search(r"://\d+\.\d+\.\d+\.\d+", _origin)))
    _domain = None if _is_insecure else (os.getenv("COOKIE_DOMAIN") or None)
    resp.set_cookie(
        "XSRF-TOKEN",
        token,
        httponly=False,
        secure=not _is_insecure,
        samesite="lax" if _is_insecure else "none",
        domain=_domain,
        path="/",
        max_age=60 * 60,  # 1 час
    )
    return resp


@router.get("/cierra", summary="Get CSRF token (alias)", description="Alias for /csrf endpoint.", response_model=CsrfResponse)
async def get_csrf_alias(http_request: Request):
    # Alias для фронта: полностью идентично /csrf
    return await get_csrf(http_request)


@router.get("/me", summary="Get current user info", description="Returns profile info for the authenticated user. Response shape depends on user_type (client vs owner).", responses=AUTH_RESPONSES)
async def get_me(request: Request, db: Session = Depends(get_db)):
    """
    Получение данных текущего аутентифицированного пользователя.

    Универсальный endpoint для clients И users (владельцев станций).
    Возвращает user_type: "client" | "owner" для определения интерфейса на фронте.

    Returns:
        dict: Данные пользователя с user_type

    Raises:
        401: Если пользователь не аутентифицирован
        404: Если пользователь не найден в БД
    """
    user_id = getattr(request.state, "client_id", None)

    if not user_id:
        logger.warning("Попытка получить /auth/me без аутентификации")
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "unauthorized", "message": "Not authenticated", "status": 401}
        )

    try:
        # 1) Сначала проверяем в users (владельцы/операторы/админы имеют приоритет)
        # Dev-login создаёт запись в clients для owner-юзеров, поэтому users проверяем первым
        owner_row = db.execute(
            text("SELECT id, email, phone, role, is_active FROM users WHERE id = :id"),
            {"id": user_id}
        ).fetchone()

        if owner_row:
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
                "client_id": owner_row.id,  # Для совместимости с фронтом
                "user_id": owner_row.id,
                "email": owner_row.email,
                "phone": owner_row.phone,
                "role": owner_row.role,
                "is_active": owner_row.is_active,
                "stations_count": stats.stations_count if stats else 0,
                "locations_count": stats.locations_count if stats else 0,
            }

        # 2) Если не найден в users — проверяем в clients (обычные пользователи)
        client_row = db.execute(
            text("SELECT id, phone, name, balance, status FROM clients WHERE id = :id"),
            {"id": user_id}
        ).fetchone()

        if client_row:
            return {
                "success": True,
                "user_type": "client",
                "client_id": client_row.id,
                "email": None,
                "phone": client_row.phone,
                "name": client_row.name,
                "balance": float(client_row.balance or 0),
                "status": client_row.status,
            }

        logger.warning(f"Пользователь {user_id} не найден ни в clients, ни в users")
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "not_found", "message": "User not found", "status": 404}
        )

    except Exception as e:
        logger.error(f"Ошибка получения данных пользователя {user_id}: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "internal_error", "message": "Internal server error", "status": 500}
        )

@router.post(
    "/login",
    deprecated=True,
    summary="Login with email/password (deprecated — use OTP)",
    description=(
        "⚠️ **Устарел.** Используйте OTP-аутентификацию:\n\n"
        "- WhatsApp OTP: `POST /api/v1/auth/otp/send` → `POST /api/v1/auth/otp/verify`\n"
        "- SMS OTP: `POST /api/v1/auth/sms/send-otp` → `POST /api/v1/auth/sms/verify`\n\n"
        "Этот эндпоинт оставлен для обратной совместимости и будет удалён в v2.0."
    ),
    response_model=LoginResponse,
)
async def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    """
    [DEPRECATED] Логин через Supabase (password grant).

    Используйте новые endpoints:
    - POST /auth/otp/send - отправка OTP кода в WhatsApp
    - POST /auth/otp/verify - проверка кода и авторизация

    Этот endpoint оставлен для обратной совместимости и будет удален в будущих версиях.
    """
    try:
        # CSRF: проверяем доверенный Origin и совпадение заголовка с cookie
        origin = request.headers.get("origin")
        trusted = [o.strip() for o in settings.CSRF_TRUSTED_ORIGINS.split(",") if o.strip()]
        if not origin or origin not in trusted:
            logger.warning("CSRF origin rejected", extra={"origin": origin})
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "csrf_error", "message": "Untrusted origin", "status": 401},
            )
        header_token = request.headers.get("X-CSRF-Token")
        cookie_token = request.cookies.get("XSRF-TOKEN")
        if not header_token or not cookie_token or header_token != cookie_token:
            logger.warning(
                "CSRF token mismatch",
                extra={
                    "has_header": bool(header_token),
                    "has_cookie": bool(cookie_token),
                    "origin": origin,
                },
            )
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "csrf_error", "message": "Invalid CSRF token", "status": 401},
            )

        # 1) Password grant в Supabase
        supabase_url = settings.SUPABASE_URL.rstrip("/")
        token_url = f"{supabase_url}/auth/v1/token?grant_type=password"
        headers = {"apikey": settings.SUPABASE_ANON_KEY, "Content-Type": "application/json"}

        # Если пришёл телефон, находим email в public.clients для последующей аутентификации
        # Supabase не поддерживает grant_type=password с телефоном (только email)
        login_email: Optional[str] = None
        if body.is_email_flow:
            login_email = str(body.email)
            logger.info("Login attempt with email", extra={"email": login_email})
        elif body.is_phone_flow and body.phone:
            # Ищем email по телефону в public.clients (там phone всегда заполнен)
            try:
                result = db.execute(
                    text("SELECT email FROM public.clients WHERE phone = :phone LIMIT 1"),
                    {"phone": body.phone},
                ).fetchone()
                if result and result[0]:
                    login_email = result[0]
                    logger.info(
                        "Phone lookup successful",
                        extra={"phone": body.phone, "resolved_email": login_email}
                    )
                else:
                    logger.warning(
                        "Phone not found in database",
                        extra={"phone": body.phone}
                    )
            except Exception as e:
                logger.exception(
                    "Failed to map phone to email via DB",
                    extra={"phone": body.phone, "error": str(e)}
                )
                login_email = None

        async with httpx.AsyncClient(timeout=10) as client:
            # 1a) try phone login if phone present
            if body.is_phone_flow and body.phone:
                phone_payload: dict = {"password": body.password, "phone": body.phone}
                pr = await client.post(token_url, headers=headers, json=phone_payload)
                logger.info("Supabase phone login attempt", extra={"status": pr.status_code})
                if pr.status_code == 200:
                    r = pr
                else:
                    # 1b) fallback to email if we resolved one
                    if login_email:
                        payload: dict = {"password": body.password, "email": login_email}
                        r = await client.post(token_url, headers=headers, json=payload)
                        logger.info("Supabase email fallback attempt", extra={"status": r.status_code})
                    else:
                        r = pr  # keep last response
            else:
                payload: dict = {"password": body.password, "email": login_email}
                r = await client.post(token_url, headers=headers, json=payload)
                logger.info("Supabase email login attempt", extra={"status": r.status_code})
        if r.status_code != 200:
            try:
                err_body = r.json()
            except Exception:
                err_body = {"_": "non-json"}
            logger.warning("Supabase password grant rejected", extra={"status": r.status_code, "body": err_body})
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "invalid_credentials", "message": "Неверный логин или пароль", "status": 401},
            )
        data = r.json()
        supa_access = data.get("access_token")
        if not supa_access:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "auth_provider_error", "message": "Не удалось получить access_token", "status": 500},
            )

        # 2) Получаем user.id у Supabase (нужен subject для наших JWT)
        async with httpx.AsyncClient(timeout=10) as client:
            ur = await client.get(
                f"{supabase_url}/auth/v1/user",
                headers={"apikey": settings.SUPABASE_ANON_KEY, "Authorization": f"Bearer {supa_access}"},
            )
        if ur.status_code != 200:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "auth_provider_error", "message": "Не удалось получить пользователя", "status": 500},
            )
        user_json = ur.json()
        user_id: Optional[str] = user_json.get("id") or (user_json.get("user") or {}).get("id")
        if not user_id:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "auth_provider_error", "message": "Не удалось определить user_id", "status": 500},
            )

        # 3) Минтим НАШИ токены и кладём в cookie
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token(user_id)

        resp = JSONResponse({"success": True})

        # Устанавливаем cookies с Domain=.redp.asystem.kg (cross-subdomain)
        # evp_access ~10 минут, evp_refresh ~7 дней
        resp.set_cookie("evp_access", access_token, **_cookie_params(10 * 60, samesite="none", request=request))
        resp.set_cookie("evp_refresh", refresh_token, **_cookie_params(7 * 24 * 3600, samesite="none", request=request))
        return resp
    except Exception as e:
        logger.exception("Ошибка при обработке callback")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "internal_error", "message": "Внутренняя ошибка сервера", "status": 500},
        )


@router.post("/refresh", summary="Refresh access token", description="Rotates the access and refresh JWT cookies. Requires a valid refresh cookie.", response_model=LoginResponse, responses=AUTH_RESPONSES)
async def refresh(request: Request):
    """
    Ротация refresh: по НАШЕМУ cookie evp_refresh выдаем новую пару токенов.
    При невалидном/просроченном refresh — 401.
    """
    try:
        refresh_cookie = request.cookies.get("evp_refresh")
        if not refresh_cookie:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "unauthorized", "message": "Missing refresh token", "status": 401},
            )

        # Декодируем наш refresh
        try:
            payload = jwt.decode(refresh_cookie, settings.SECRET_KEY, algorithms=["HS256"], options={"verify_aud": False})
        except Exception:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "unauthorized", "message": "Invalid refresh token", "status": 401},
            )
        if payload.get("typ") != "refresh":
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "unauthorized", "message": "Invalid token type", "status": 401},
            )
        user_id = payload.get("sub")
        if not user_id:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "unauthorized", "message": "Invalid subject", "status": 401},
            )

        # Минтим новую пару
        access_token = create_access_token(user_id)
        new_refresh = create_refresh_token(user_id)
        resp = JSONResponse({"success": True})
        # ВАЖНО: используем samesite="none" для cross-subdomain cookies (redp.asystem.kg → ocpp.redp.asystem.kg)
        # SameSite=Strict блокирует отправку cookies при cross-site запросах после перезагрузки страницы
        resp.set_cookie("evp_access", access_token, **_cookie_params(10 * 60, samesite="none", request=request))
        resp.set_cookie("evp_refresh", new_refresh, **_cookie_params(7 * 24 * 3600, samesite="none", request=request))
        return resp
    except Exception as e:
        logger.exception("Ошибка при обновлении токена")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "internal_error", "message": "Внутренняя ошибка сервера", "status": 500},
        )


@router.post("/logout", summary="Logout", description="Clears all authentication cookies and invalidates the session.", response_model=LoginResponse, responses=AUTH_RESPONSES)
async def logout(request: Request):
    """
    Идемпотентный logout: чистим cookies.
    """
    resp = JSONResponse({"success": True})
    domain = os.getenv("COOKIE_DOMAIN") or None
    # Очистка: max_age=0
    for name in ("evp_access", "evp_refresh", "XSRF-TOKEN"):
        resp.set_cookie(
            name,
            "",
            httponly=(name != "XSRF-TOKEN"),
            secure=True,
            samesite="lax",
            domain=domain,
            path="/",
            max_age=0,
        )
    return resp


