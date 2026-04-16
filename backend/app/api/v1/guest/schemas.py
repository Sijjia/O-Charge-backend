"""
Pydantic schemas для Guest Charging API
"""
import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class GuestStartRequest(BaseModel):
    """Запрос на начало гостевой зарядки"""
    phone: str = Field(..., min_length=10, max_length=20, description="Телефон гостя (+996XXXXXXXXX)")
    station_id: str = Field(..., min_length=1, max_length=50, description="ID станции (CHR-BGK-001)")
    connector_id: int = Field(..., ge=1, le=10, description="Номер коннектора (1-10)")
    amount_kgs: float = Field(..., gt=0, le=100000, description="Сумма оплаты в сомах")

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        v = "".join(c for c in v if c.isdigit() or c == "+")
        if not v.startswith("+"):
            v = "+" + v
        if len(v) < 10:
            raise ValueError("Номер телефона слишком короткий")
        return v

    @field_validator("station_id")
    @classmethod
    def validate_station_id(cls, v: str) -> str:
        if not re.match(r'^[A-Za-z0-9\-]+$', v):
            raise ValueError("Недопустимые символы в station_id")
        return v


class GuestStartResponse(BaseModel):
    """Ответ на создание гостевой сессии"""
    success: bool
    session_id: Optional[str] = None
    phone: Optional[str] = None
    station_id: Optional[str] = None
    connector_id: Optional[int] = None
    amount_kgs: Optional[float] = None
    status: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


class GuestStatusResponse(BaseModel):
    """Ответ о статусе гостевой сессии"""
    success: bool
    session_id: Optional[str] = None
    status: Optional[str] = None
    phone: Optional[str] = None
    station_id: Optional[str] = None
    connector_id: Optional[int] = None
    amount_kgs: Optional[float] = None
    charging_session_id: Optional[str] = None
    energy_consumed: Optional[float] = None
    duration_minutes: Optional[int] = None
    message: Optional[str] = None
    error: Optional[str] = None


class GuestStopRequest(BaseModel):
    """Запрос на остановку гостевой зарядки (требует phone для ownership)"""
    phone: str = Field(..., min_length=10, max_length=20, description="Телефон гостя для подтверждения ownership")

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        v = "".join(c for c in v if c.isdigit() or c == "+")
        if not v.startswith("+"):
            v = "+" + v
        return v


class GuestStopResponse(BaseModel):
    """Ответ на остановку гостевой зарядки"""
    success: bool
    session_id: Optional[str] = None
    energy_consumed: Optional[float] = None
    actual_cost: Optional[float] = None
    message: Optional[str] = None
    error: Optional[str] = None
