"""Corporate panel response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class CorporateInfoResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"company_name": "Acme Corp", "role": "admin", "balance": 50000.0}}
    ]}}


class CorporateDashboardResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"total_employees": 15, "monthly_usage": 2500.0, "balance": 50000.0}}
    ]}}


class CorporateEmployeesResponse(BaseModel):
    success: bool = True
    employees: Optional[List[dict]] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "employees": [{"id": "emp-001", "phone": "+996700000001", "position": "Manager", "monthly_limit": 5000.0}]}
    ]}}


class CorporateProfileResponse(BaseModel):
    success: bool = True
    company_name: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    is_corporate: bool = False

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "company_name": "Acme Corp", "name": "Иван", "role": "admin", "is_corporate": True}
    ]}}


class CorporateReportResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"period": "2025-01", "total_kwh": 150.0, "total_amount": 1800.0}}
    ]}}
