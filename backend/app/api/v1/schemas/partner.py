"""Partner cabinet response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class PartnerDashboardResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"stations_total": 5, "stations_online": 3, "revenue_today": 1500.0, "sessions_today": 12}}
    ]}}


class PartnerStationsResponse(BaseModel):
    success: bool = True
    data: Optional[List[dict]] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"id": "CHR-BGK-001", "serial_number": "SN-001", "status": "online", "model": "ABB Terra", "power_kw": 50}]}
    ]}}


class PartnerSessionsResponse(BaseModel):
    success: bool = True
    data: Optional[List[dict]] = None
    total: int = 0
    page: int = 1
    per_page: int = 20

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"id": "sess-001", "station_id": "CHR-BGK-001", "status": "completed", "energy_kwh": 15.0, "amount": 180.0}], "total": 1, "page": 1, "per_page": 20}
    ]}}


class PartnerRevenueResponse(BaseModel):
    success: bool = True
    data: Optional[List[dict]] = None
    summary: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"date": "2025-01-15", "sessions": 10, "revenue": 1200.0}], "summary": {"sessions_count": 10, "total_revenue": 1200.0}}
    ]}}
