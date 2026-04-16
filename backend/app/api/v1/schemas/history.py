"""History endpoint response schemas."""
from pydantic import BaseModel, Field
from typing import Optional, List


class ChargingStationInfo(BaseModel):
    model: Optional[str] = None
    location: Optional[dict] = None


class ChargingHistoryItem(BaseModel):
    id: str = ""
    station_id: Optional[str] = None
    connector_id: Optional[int] = None
    status: Optional[str] = None
    energy_kwh: Optional[float] = None
    amount: Optional[float] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_minutes: Optional[float] = None
    limit_type: Optional[str] = None
    limit_value: Optional[float] = None
    max_power_kw: Optional[float] = None
    station: Optional[dict] = None


class ChargingHistoryResponse(BaseModel):
    success: bool = True
    data: List[ChargingHistoryItem] = []
    total: int = 0
    limit: int = 50
    offset: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"id": "sess-001", "station_id": "CHR-BGK-001", "status": "stopped", "energy_kwh": 12.5, "amount": 150.0, "started_at": "2025-01-15T10:00:00", "ended_at": "2025-01-15T11:30:00", "duration_minutes": 90}], "total": 1, "limit": 50, "offset": 0}
    ]}}


class TransactionItem(BaseModel):
    id: int = 0
    transaction_type: Optional[str] = None
    amount: Optional[float] = None
    requested_amount: Optional[float] = None
    balance_before: Optional[float] = None
    balance_after: Optional[float] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    status: Optional[str] = None
    payment_method: Optional[str] = None
    invoice_id: Optional[str] = None


class TransactionHistoryResponse(BaseModel):
    success: bool = True
    data: List[TransactionItem] = []
    total: int = 0
    limit: int = 50
    offset: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"id": 1, "transaction_type": "balance_topup", "amount": 500.0, "balance_before": 0.0, "balance_after": 500.0, "status": "approved", "payment_method": "qr"}], "total": 1, "limit": 50, "offset": 0}
    ]}}


class ChargingStatsResponse(BaseModel):
    success: bool = True
    stats: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "stats": {"total_sessions": 42, "total_energy_kwh": 520.5, "total_amount": 6500.0, "average_session_minutes": 65}}
    ]}}
