"""Admin partners response schemas."""
from pydantic import BaseModel, Field
from typing import Optional, List


class AdminPartnerItem(BaseModel):
    id: str = ""
    user_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company_name: Optional[str] = None
    station_count: Optional[int] = None
    total_revenue: Optional[float] = None
    commission_rate: Optional[float] = None
    is_active: Optional[bool] = None
    created_at: Optional[str] = None


class AdminPartnerListResponse(BaseModel):
    success: bool = True
    data: List[AdminPartnerItem] = []
    total: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"id": "ptr-001", "company_name": "EcoCharge", "station_count": 5, "total_revenue": 50000.0}], "total": 1}
    ]}}


class AdminPartnerSelectResponse(BaseModel):
    success: bool = True
    partners: List[dict] = []

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "partners": [{"id": "ptr-001", "user_id": "usr-001", "label": "EcoCharge (ptr-001)"}]}
    ]}}


class AdminPartnerDetailResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"id": "ptr-001", "company_name": "EcoCharge", "revenue_share": 30, "stations": [], "kpi": {}}}
    ]}}


class PartnerCreateRequest(BaseModel):
    user_id: str = Field(..., description="User UUID to associate as partner")
    company_name: Optional[str] = Field(None, description="Company name")
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    revenue_share_percent: Optional[float] = Field(None, description="Revenue share percentage (0-100)")

    model_config = {"json_schema_extra": {"examples": [
        {"user_id": "usr-001", "company_name": "EcoCharge", "revenue_share_percent": 30.0}
    ]}}


class PartnerMutationResponse(BaseModel):
    success: bool = True
    partner_id: Optional[str] = None
    message: str = ""

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "partner_id": "ptr-001", "message": "Partner created"}
    ]}}
