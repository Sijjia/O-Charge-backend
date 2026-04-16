"""
RLS Middleware — устанавливает PostgreSQL session variables для Row Level Security
"""
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RLSMiddleware(BaseHTTPMiddleware):
    """
    Устанавливает app.current_user_id и app.role в PostgreSQL сессии.
    Backend всегда работает с role='service' для полного доступа.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Extract client_id from request state (set by AuthMiddleware)
        client_id = getattr(request.state, "client_id", None)

        # Store in request state for downstream use
        request.state.rls_client_id = client_id
        request.state.rls_role = "service"  # Backend always uses service role

        return await call_next(request)
