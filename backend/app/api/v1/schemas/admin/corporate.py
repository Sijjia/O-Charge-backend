"""Admin corporate response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class AdminCorporateGroupsResponse(BaseModel):
    success: bool = True
    groups: List[dict] = []
    total: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "groups": [{"id": "grp-001", "company_name": "Acme Corp", "employee_count": 10}], "total": 1}
    ]}}


class AdminCorporateGroupDetailResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": {"id": "grp-001", "company_name": "Acme Corp", "employees": []}}
    ]}}


class AdminCorporateEmployeesResponse(BaseModel):
    success: bool = True
    employees: List[dict] = []

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "employees": [{"id": "emp-001", "user_id": "usr-001", "role": "employee"}]}
    ]}}
