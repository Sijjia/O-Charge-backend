"""Admin stations response schemas."""
from pydantic import BaseModel, Field
from typing import Optional, List


class AdminStationItem(BaseModel):
    id: str = ""
    location_id: Optional[str] = None
    model: Optional[str] = None
    vendor: Optional[str] = None
    max_power: Optional[float] = None
    status: Optional[str] = None
    is_available: Optional[bool] = None
    tariff_per_kwh: Optional[float] = None
    created_at: Optional[str] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    is_online: Optional[bool] = None
    last_heartbeat: Optional[str] = None
    connector_count: Optional[int] = None
    active_sessions: Optional[int] = None
    is_partner: Optional[bool] = None
    partner_id: Optional[str] = None
    partner_name: Optional[str] = None


class AdminStationListResponse(BaseModel):
    success: bool = True
    data: List[AdminStationItem] = []
    total: int = 0
    limit: int = 50
    offset: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"id": "CHR-BGK-001", "model": "ABB Terra AC", "vendor": "ABB", "status": "active", "is_online": True, "location_name": "Bishkek Mall"}], "total": 1, "limit": 50, "offset": 0}
    ]}}


class AdminStationDetailResponse(BaseModel):
    success: bool = True
    station: Optional[dict] = None
    connectors: Optional[List[dict]] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "station": {"id": "CHR-BGK-001", "model": "ABB Terra AC"}, "connectors": [{"connector_number": 1, "connector_type": "Type2", "status": "Available"}]}
    ]}}


class StationCreateRequest(BaseModel):
    id: str = Field(..., description="Station ID (e.g. CHR-BGK-001)")
    location_id: str = Field(..., description="Location UUID")
    model: Optional[str] = Field(None, description="Station model name")
    vendor: Optional[str] = Field(None, description="Manufacturer/vendor")
    max_power: Optional[float] = Field(None, description="Max power in kW")
    tariff_per_kwh: Optional[float] = Field(None, description="Price per kWh in KGS")
    status: Optional[str] = Field("active", description="Station status")
    user_id: Optional[str] = Field(None, description="Owner user ID")
    ocpp_ws_url: Optional[str] = Field(None, description="OCPP WebSocket URL")
    connectors: Optional[List[dict]] = Field(None, description="Connector definitions")

    model_config = {"json_schema_extra": {"examples": [
        {"id": "CHR-BGK-002", "location_id": "loc-001", "model": "ABB Terra AC", "vendor": "ABB", "max_power": 22.0, "tariff_per_kwh": 12.0}
    ]}}


class StationUpdateRequest(BaseModel):
    model: Optional[str] = None
    vendor: Optional[str] = None
    max_power: Optional[float] = None
    tariff_per_kwh: Optional[float] = None
    status: Optional[str] = None
    location_id: Optional[str] = None
    ocpp_ws_url: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"model": "ABB Terra AC W22", "max_power": 22.0}
    ]}}


class StationMutationResponse(BaseModel):
    success: bool = True
    station_id: Optional[str] = None
    message: str = ""

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "station_id": "CHR-BGK-001", "message": "Station created"}
    ]}}
