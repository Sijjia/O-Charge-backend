"""Admin logs response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class OcppLogsResponse(BaseModel):
    success: bool = True
    logs: Optional[List[dict]] = None
    total: Optional[int] = None
    page: Optional[int] = None
    per_page: Optional[int] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "logs": [{"id": 1, "station_id": "CHR-BGK-001", "event_type": "Heartbeat", "direction": "incoming"}], "total": 1, "page": 1, "per_page": 50}
    ]}}


class OcppStatsResponse(BaseModel):
    success: bool = True
    total_events: Optional[int] = None
    by_type: Optional[dict] = None
    by_station: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "total_events": 500, "by_type": {"Heartbeat": 200, "MeterValues": 150}}
    ]}}


class OnlineStationsResponse(BaseModel):
    success: bool = True
    stations: List[str] = []
    count: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "stations": ["CHR-BGK-001", "CHR-BGK-002"], "count": 2}
    ]}}
