"""Admin bookings response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class AdminBookingItem(BaseModel):
    id: str = ""
    station_id: Optional[str] = None
    user_id: Optional[str] = None
    connector_number: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    station_name: Optional[str] = None
    user_phone: Optional[str] = None


class AdminBookingsResponse(BaseModel):
    success: bool = True
    data: List[AdminBookingItem] = []
    total: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"id": "bk-001", "station_id": "CHR-BGK-001", "status": "active", "user_phone": "+996700000001"}], "total": 1}
    ]}}
