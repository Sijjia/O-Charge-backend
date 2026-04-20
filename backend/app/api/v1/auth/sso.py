"""
SSO Authentication via Keycloak
Staff/admin login through OIDC Authorization Code + PKCE flow.
"""
import time
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from jose import jwt as jose_jwt, jwk

from app.core.config import settings
from app.db.session import get_async_db
from app.api.v1.auth.otp import (
    create_access_token,
    create_refresh_token,
    _cookie_params,
    ACCESS_TOKEN_EXPIRE_SECONDS,
    REFRESH_TOKEN_EXPIRE_SECONDS,
)

logger = logging.getLogger("app.api.v1.auth.sso")

router = APIRouter(prefix="/auth/sso")


# ========== Keycloak JWKS Cache ==========

class KeycloakJWKSCache:
    """Cache for Keycloak JWKS keys with TTL."""

    def __init__(self):
        self.jwks: Optional[dict] = None
        self.fetched_at: float = 0.0
        self.ttl_seconds: int = 3600

    @property
    def _base_url(self) -> str:
        """Use internal URL for server-to-server if available, else public URL."""
        return settings.KEYCLOAK_INTERNAL_URL or settings.KEYCLOAK_BASE_URL

    @property
    def _jwks_url(self) -> str:
        return (
            f"{self._base_url}/realms/"
            f"{settings.KEYCLOAK_REALM}/protocol/openid-connect/certs"
        )

    async def get_jwks(self) -> dict:
        now = time.time()
        if self.jwks and (now - self.fetched_at) < self.ttl_seconds:
            return self.jwks
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(self._jwks_url)
            resp.raise_for_status()
            self.jwks = resp.json()
            self.fetched_at = now
            return self.jwks


keycloak_jwks_cache = KeycloakJWKSCache()


# ========== Pydantic Schemas ==========

class SSOExchangeRequest(BaseModel):
    code: str = Field(..., min_length=1, description="Authorization code from Keycloak")
    code_verifier: str = Field(..., min_length=43, max_length=128, description="PKCE code verifier")
    redirect_uri: str = Field(..., min_length=1, description="Redirect URI used in authorization request")


# ========== Helpers ==========

def _proxy_headers() -> dict:
    """Headers to make Keycloak use correct issuer when called via internal URL."""
    if settings.KEYCLOAK_INTERNAL_URL:
        from urllib.parse import urlparse
        public = urlparse(settings.KEYCLOAK_BASE_URL)
        return {
            "X-Forwarded-Proto": public.scheme,
            "X-Forwarded-Host": public.hostname,
        }
    return {}


async def _exchange_code(code: str, code_verifier: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens via Keycloak token endpoint."""
    base = settings.KEYCLOAK_INTERNAL_URL or settings.KEYCLOAK_BASE_URL
    token_url = (
        f"{base}/realms/"
        f"{settings.KEYCLOAK_REALM}/protocol/openid-connect/token"
    )
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.KEYCLOAK_CLIENT_ID,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(token_url, data=data, headers=_proxy_headers())
        if resp.status_code != 200:
            logger.error(f"[SSO] Token exchange failed: {resp.status_code} {resp.text}")
            raise ValueError(f"Token exchange failed: {resp.status_code}")
        return resp.json()


async def _validate_id_token(id_token: str, access_token: str = None) -> dict:
    """Validate Keycloak id_token using JWKS."""
    jwks_data = await keycloak_jwks_cache.get_jwks()

    # Get header to find kid
    unverified_header = jose_jwt.get_unverified_header(id_token)
    kid = unverified_header.get("kid")

    # Find matching key
    key = None
    for k in jwks_data.get("keys", []):
        if k.get("kid") == kid:
            key = k
            break
    if key is None and jwks_data.get("keys"):
        key = jwks_data["keys"][0]
    if not key:
        raise ValueError("No matching JWKS key found")

    expected_issuer = f"{settings.KEYCLOAK_BASE_URL}/realms/{settings.KEYCLOAK_REALM}"

    payload = jose_jwt.decode(
        id_token,
        key,
        algorithms=[key.get("alg", "RS256"), "RS256"],
        audience=settings.KEYCLOAK_CLIENT_ID,
        issuer=expected_issuer,
        access_token=access_token,
        options={"verify_aud": True, "verify_iss": True, "verify_at_hash": bool(access_token)},
    )
    return payload


# ========== API Endpoint ==========

@router.post("/exchange", summary="Exchange SSO authorization code for session")
async def sso_exchange(
    request: Request,
    body: SSOExchangeRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Exchange Keycloak authorization code for application session.

    1. Exchange code for Keycloak tokens (server-to-server)
    2. Validate id_token via JWKS
    3. Check required group membership
    4. Find user in `users` table by email
    5. Create client record if missing
    6. Issue evp_access/evp_refresh cookies
    """
    if not settings.KEYCLOAK_ENABLED:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "sso_disabled", "message": "SSO is not enabled"},
        )

    try:
        # 1. Exchange code for tokens
        token_data = await _exchange_code(body.code, body.code_verifier, body.redirect_uri)
        id_token = token_data.get("id_token")
        if not id_token:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "no_id_token", "message": "No id_token in response"},
            )

        # 2. Validate id_token (pass access_token for at_hash verification)
        access_token = token_data.get("access_token")
        claims = await _validate_id_token(id_token, access_token=access_token)
        email = claims.get("email", "").strip().lower()
        if not email:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "no_email", "message": "No email in token"},
            )

        # 3. Check group membership
        groups = claims.get("groups", [])
        required_group = settings.KEYCLOAK_REQUIRED_GROUP
        if required_group not in groups:
            logger.warning(f"[SSO] User {email} not in required group '{required_group}'. Groups: {groups}")
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error": "not_in_group",
                    "message": "Доступ запрещён. Вы не состоите в группе сотрудников.",
                },
            )

        # 4. Find user in users table by email (case-insensitive)
        owner_result = await db.execute(
            text("SELECT id, email, role, is_active, admin_id FROM users WHERE LOWER(email) = :email LIMIT 1"),
            {"email": email},
        )
        owner_row = owner_result.fetchone()

        if not owner_row:
            logger.warning(f"[SSO] No user found for email: {email}")
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "error": "user_not_found",
                    "message": "Аккаунт не найден. Обратитесь к администратору.",
                },
            )

        if not owner_row.is_active:
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error": "user_inactive",
                    "message": "Аккаунт деактивирован.",
                },
            )

        user_id = owner_row.id
        owner_role = owner_row.role
        owner_admin_id = str(owner_row.admin_id) if owner_row.admin_id else None

        # 5. Create client record if missing (same pattern as otp.py)
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
                {"id": user_id, "phone": owner_row.phone or "", "name": owner_row.email or ""},
            )
            await db.commit()
            logger.info(f"[SSO] Created client record for owner: {email}")

        # 6. Issue JWT cookies
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token(user_id)

        resp_content = {
            "success": True,
            "message": "SSO авторизация успешна",
            "user_type": "owner",
            "user_id": user_id,
            "role": owner_role,
            "admin_id": owner_admin_id,
        }

        resp = JSONResponse(content=resp_content)
        resp.set_cookie("evp_access", access_token, **_cookie_params(ACCESS_TOKEN_EXPIRE_SECONDS, request))
        resp.set_cookie("evp_refresh", refresh_token, **_cookie_params(REFRESH_TOKEN_EXPIRE_SECONDS, request))

        logger.info(f"[SSO] Successful login: {email} -> {user_id}, role={owner_role}")
        return resp

    except ValueError as e:
        logger.error(f"[SSO] Validation error: {e}")
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "validation_error", "message": str(e)},
        )
    except Exception as e:
        logger.exception(f"[SSO] Exchange error: {e}")
        await db.rollback()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "internal_error", "message": "Ошибка сервера"},
        )
