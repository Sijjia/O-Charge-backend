"""Profile endpoint response schemas."""
from pydantic import BaseModel, Field
from typing import Optional


class ProfileClientResponse(BaseModel):
    success: bool = True
    user_type: str = "client"
    client_id: str = ""
    phone: Optional[str] = None
    name: Optional[str] = None
    balance: Optional[float] = None
    status: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "user_type": "client", "client_id": "uuid-1234", "phone": "+996700000001", "name": "Иван", "balance": 500.0, "status": "active"}
    ]}}


class ProfileData(BaseModel):
    client_id: str = ""
    name: Optional[str] = None
    phone: Optional[str] = None
    balance: Optional[float] = None
    status: Optional[str] = None


class ProfileUpdateResponse(BaseModel):
    success: bool = True
    message: str = ""
    profile: Optional[ProfileData] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "Profile updated", "profile": {"client_id": "uuid-1234", "name": "Иван", "phone": "+996700000001", "balance": 500.0, "status": "active"}}
    ]}}


class PhoneChangeRequestResponse(BaseModel):
    success: bool = True
    message: str = ""
    phone: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "OTP sent to new phone", "phone": "+996700000002"}
    ]}}


class PhoneChangeConfirmResponse(BaseModel):
    success: bool = True
    message: str = ""
    new_phone: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "Phone changed", "new_phone": "+996700000002"}
    ]}}
