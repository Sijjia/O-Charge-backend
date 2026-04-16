"""Admin tariffs response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class TariffPlanItem(BaseModel):
    id: str = ""
    name: Optional[str] = None
    description: Optional[str] = None
    is_default: bool = False
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    rules_count: Optional[int] = None
    stations_count: Optional[int] = None


class TariffRuleItem(BaseModel):
    id: str = ""
    name: Optional[str] = None
    tariff_type: str = "per_kwh"
    connector_type: str = "ALL"
    price: float = 0
    currency: str = "KGS"
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    is_weekend: Optional[bool] = None
    priority: int = 0
    is_active: bool = True


class TariffListResponse(BaseModel):
    success: bool = True
    data: List[TariffPlanItem] = []

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"id": "tp-001", "name": "Standard", "is_default": True, "is_active": True, "rules_count": 3, "stations_count": 10}]}
    ]}}


class TariffDetailResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"id": "tp-001", "name": "Standard", "rules": [{"name": "Day rate", "price": 12.0, "tariff_type": "per_kwh"}]}}
    ]}}


class TariffCreateResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"id": "tp-001", "name": "Standard", "rules_count": 2}}
    ]}}


class TariffAnalyticsResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"total_revenue": 50000.0, "avg_rate": 12.5}}
    ]}}


class RuleMutationResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"id": "rule-001", "name": "Night rate"}}
    ]}}


class AssignTariffResponse(BaseModel):
    success: bool = True
    updated_stations: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "updated_stations": 5}
    ]}}
