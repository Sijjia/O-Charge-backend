"""Admin alerts response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class AlertItem(BaseModel):
    id: str = ""
    type: Optional[str] = None
    severity: Optional[str] = None
    title: Optional[str] = None
    message: Optional[str] = None
    station_id: Optional[str] = None
    created_at: Optional[str] = None
    acknowledged: bool = False


class AlertsListResponse(BaseModel):
    success: bool = True
    alerts: List[AlertItem] = []
    total: int = 0
    unacknowledged: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "alerts": [{"id": "alert-001", "type": "station_offline", "severity": "critical", "title": "Station offline", "station_id": "CHR-BGK-001"}], "total": 1, "unacknowledged": 1}
    ]}}
