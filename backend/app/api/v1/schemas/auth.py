"""Auth endpoint response schemas."""
from pydantic import BaseModel, Field
from typing import Optional


class CsrfResponse(BaseModel):
    success: bool = True
    csrf_token: str = ""

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "csrf_token": "abc123def456"}
    ]}}


class AuthMeClientResponse(BaseModel):
    success: bool = True
    user_type: str = "client"
    client_id: str = ""
    phone: Optional[str] = None
    name: Optional[str] = None
    balance: Optional[float] = None
    status: Optional[str] = None
    email: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "user_type": "client", "client_id": "uuid-1234", "phone": "+996700000001", "name": "Иван", "balance": 500.0, "status": "active"}
    ]}}


class AuthMeOwnerResponse(BaseModel):
    success: bool = True
    user_type: str = "owner"
    client_id: Optional[str] = None
    user_id: str = ""
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    stations_count: Optional[int] = None
    locations_count: Optional[int] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "user_type": "owner", "user_id": "test-admin-001", "email": "admin@rp.kg", "role": "superadmin", "is_active": True, "stations_count": 12, "locations_count": 5}
    ]}}


class LoginResponse(BaseModel):
    success: bool = True

    model_config = {"json_schema_extra": {"examples": [{"success": True}]}}


class OtpSendResponse(BaseModel):
    success: bool = True
    message: str = ""
    phone: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "OTP sent via WhatsApp", "phone": "+996700000001"}
    ]}}


class OtpVerifyResponse(BaseModel):
    success: bool = True
    message: str = ""
    user_type: Optional[str] = None
    user_id: Optional[str] = None
    role: Optional[str] = None
    admin_id: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "Authenticated", "user_type": "client", "user_id": "uuid-1234"}
    ]}}


class OtpStatusResponse(BaseModel):
    success: bool = True
    phone: str = ""
    can_send: bool = True
    wait_seconds: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "phone": "+996700000001", "can_send": True, "wait_seconds": 0}
    ]}}


class DevLoginResponse(BaseModel):
    success: bool = True
    message: str = ""
    user_type: Optional[str] = None
    user_id: Optional[str] = None
    role: Optional[str] = None
    admin_id: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "Dev login successful", "user_type": "client", "user_id": "test-client-001"}
    ]}}
