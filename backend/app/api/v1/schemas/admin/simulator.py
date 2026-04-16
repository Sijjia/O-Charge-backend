"""Admin simulator response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class ConnectorStatus(BaseModel):
    id: int = 0
    status: str = "Available"
    energy_kwh: float = 0.0
    power_kw: float = 0.0
    duration_s: float = 0.0
    cost: float = 0.0
    transaction_id: Optional[int] = None


class SimulatorStatusResponse(BaseModel):
    success: bool = True
    active: bool = False
    station_id: Optional[str] = None
    connectors: Optional[List[ConnectorStatus]] = None
    uptime_s: Optional[float] = None
    messages_sent: Optional[int] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "active": True, "station_id": "SIM-001", "connectors": [{"id": 1, "status": "Available"}]}
    ]}}


class SimulatorLogResponse(BaseModel):
    success: bool = True
    events: List[dict] = []

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "events": [{"type": "BootNotification", "timestamp": "2025-01-15T10:00:00", "direction": "outgoing"}]}
    ]}}
