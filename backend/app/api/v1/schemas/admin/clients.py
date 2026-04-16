"""Admin clients response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class AdminClientItem(BaseModel):
    id: str = ""
    phone: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    balance: Optional[float] = None
    total_sessions: Optional[int] = None
    total_energy_kwh: Optional[float] = None
    created_at: Optional[str] = None
    last_session_at: Optional[str] = None
    is_active: Optional[bool] = None


class AdminClientListResponse(BaseModel):
    success: bool = True
    users: List[AdminClientItem] = []
    total: int = 0
    page: int = 1
    per_page: int = 50
    today_sessions: Optional[int] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "users": [{"id": "uuid-001", "phone": "+996700000001", "name": "Иван", "balance": 500.0, "total_sessions": 10}], "total": 1, "page": 1, "per_page": 50}
    ]}}


class AdminClientDetailResponse(BaseModel):
    success: bool = True
    user: Optional[AdminClientItem] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "user": {"id": "uuid-001", "phone": "+996700000001", "name": "Иван", "balance": 500.0}}
    ]}}
