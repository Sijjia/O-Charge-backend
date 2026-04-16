"""Admin sessions response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class AdminSessionItem(BaseModel):
    id: str = ""
    client_id: Optional[str] = None
    client_phone: Optional[str] = None
    station_id: Optional[str] = None
    connector_id: Optional[int] = None
    station_model: Optional[str] = None
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    partner_id: Optional[str] = None
    partner_name: Optional[str] = None
    status: Optional[str] = None
    energy_kwh: Optional[float] = None
    amount: Optional[float] = None
    reserved_amount: Optional[float] = None
    actual_cost: Optional[float] = None
    refund_amount: Optional[float] = None
    limit_type: Optional[str] = None
    limit_value: Optional[float] = None
    start_time: Optional[str] = None
    stop_time: Optional[str] = None
    duration_minutes: Optional[float] = None
    partner_share: Optional[float] = None
    platform_share: Optional[float] = None
    created_at: Optional[str] = None


class AdminSessionListResponse(BaseModel):
    success: bool = True
    data: List[AdminSessionItem] = []
    total: int = 0
    limit: int = 50
    offset: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"id": "sess-001", "station_id": "CHR-BGK-001", "status": "stopped", "energy_kwh": 15.0, "amount": 180.0, "duration_minutes": 90}], "total": 1, "limit": 50, "offset": 0}
    ]}}


class AdminSessionDetailResponse(BaseModel):
    success: bool = True
    session: Optional[dict] = None
    ocpp_transaction: Optional[dict] = None
    meter_values: Optional[List[dict]] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "session": {"id": "sess-001", "status": "stopped"}, "ocpp_transaction": {"transaction_id": 123, "meter_start": 1000, "meter_stop": 1015}, "meter_values": [{"energy_wh": 15000, "timestamp": "2025-01-15T11:00:00"}]}
    ]}}


class ForceStopResponse(BaseModel):
    success: bool = True
    session_id: Optional[str] = None
    station_id: Optional[str] = None
    message: str = ""

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "session_id": "sess-001", "station_id": "CHR-BGK-001", "message": "RemoteStopTransaction sent"}
    ]}}
