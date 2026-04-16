"""Booking endpoint response schemas."""
from pydantic import BaseModel
from typing import Optional


class BookingCreateResponse(BaseModel):
    success: bool = True
    booking_id: Optional[str] = None
    station_id: Optional[str] = None
    connector_id: Optional[int] = None
    expires_at: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "booking_id": "bk-001", "station_id": "CHR-BGK-001", "connector_id": 1, "expires_at": "2025-01-15T10:30:00", "message": "Connector booked for 30 minutes"}
    ]}}


class ActiveBookingResponse(BaseModel):
    success: bool = True
    booking: Optional[dict] = None
    message: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "booking": {"id": "bk-001", "station_id": "CHR-BGK-001", "connector_id": 1, "status": "active", "expires_at": "2025-01-15T10:30:00"}}
    ]}}


class BookingCancelResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None
    error: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "Booking cancelled"}
    ]}}
