"""Common response schemas used across multiple endpoints."""
from pydantic import BaseModel, Field
from typing import Optional, Any


class SuccessResponse(BaseModel):
    success: bool = True

    model_config = {"json_schema_extra": {"examples": [{"success": True}]}}


class MessageResponse(BaseModel):
    success: bool = True
    message: str = ""

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "Operation completed"}
    ]}}


class ErrorResponse401(BaseModel):
    success: bool = False
    error: str = "unauthorized"
    message: str = "Authentication required"

class ErrorResponse403(BaseModel):
    success: bool = False
    error: str = "forbidden"
    message: str = "Insufficient permissions"

class ErrorResponse404(BaseModel):
    success: bool = False
    error: str = "not_found"
    message: str = "Resource not found"

# Backward compat
ErrorResponse = ErrorResponse401


# Reusable `responses` dicts for OpenAPI decorators
AUTH_RESPONSES = {
    401: {"description": "Not authenticated", "model": ErrorResponse401},
    403: {"description": "Forbidden — insufficient role", "model": ErrorResponse403},
}

ADMIN_RESPONSES = {
    **AUTH_RESPONSES,
    404: {"description": "Resource not found", "model": ErrorResponse404},
}
