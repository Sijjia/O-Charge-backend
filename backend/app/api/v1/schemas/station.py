"""Station endpoint response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class ConnectorInfo(BaseModel):
    connector_number: Optional[int] = None
    connector_type: Optional[str] = None
    max_power: Optional[float] = None
    status: Optional[str] = None
    status_name: Optional[str] = None
    current_progress: Optional[int] = None
    id: Optional[int] = None
    type: Optional[str] = None
    power_kw: Optional[float] = None
    available: Optional[bool] = None
    error: Optional[str] = None


class StationStatusResponse(BaseModel):
    success: bool = True
    station_id: Optional[str] = None
    serial_number: Optional[str] = None
    model: Optional[str] = None
    manufacturer: Optional[str] = None
    online: bool = False
    station_status: Optional[str] = None
    location_status: Optional[str] = None
    available_for_charging: bool = False
    last_heartbeat_at: Optional[str] = None
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location_coordinates: Optional[dict] = None
    station_display_name: Optional[str] = None
    connectors: List[ConnectorInfo] = []
    total_connectors: int = 0
    available_connectors: int = 0
    occupied_connectors: int = 0
    faulted_connectors: int = 0
    tariff_per_kwh: Optional[float] = None
    tariff_rub_kwh: Optional[float] = None  # deprecated, use tariff_per_kwh
    session_fee: Optional[float] = None
    currency: str = "KGS"
    working_hours: Optional[str] = None
    message: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "station_id": "CHR-BGK-001", "serial_number": "CHR-BGK-001", "model": "ABB Terra AC", "manufacturer": "ABB", "online": True, "station_status": "active", "available_for_charging": True, "total_connectors": 2, "available_connectors": 1, "currency": "KGS"}
    ]}}


class TariffScheduleItem(BaseModel):
    name: Optional[str] = None
    time: Optional[str] = None
    rate: Optional[float] = None
    tariff_type: Optional[str] = None
    connector_type: Optional[str] = None


class StationTariffData(BaseModel):
    station_id: Optional[str] = None
    tariff_plan: Optional[str] = None
    current_rate: Optional[float] = None
    current_period: Optional[str] = None
    next_change_at: Optional[str] = None
    next_rate: Optional[float] = None
    currency: str = "KGS"
    schedule: List[TariffScheduleItem] = []


class StationTariffResponse(BaseModel):
    success: bool = True
    data: Optional[StationTariffData] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"station_id": "CHR-BGK-001", "tariff_plan": "Standard", "current_rate": 12.0, "currency": "KGS", "schedule": [{"name": "Day", "time": "06:00-22:00", "rate": 12.0, "tariff_type": "per_kwh"}]}}
    ]}}
