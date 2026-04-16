"""
Admin Corporate API — управление корпоративными клиентами (superadmin).
- GET    /admin/corporate/groups          — список компаний
- POST   /admin/corporate/groups          — создать компанию
- GET    /admin/corporate/groups/{id}     — детали компании
- PUT    /admin/corporate/groups/{id}     — обновить данные
- POST   /admin/corporate/groups/{id}/topup   — пополнить баланс
- POST   /admin/corporate/groups/{id}/block   — заблокировать
- POST   /admin/corporate/groups/{id}/unblock — разблокировать
- GET    /admin/corporate/groups/{id}/report  — отчёт по зарядкам
- GET    /admin/corporate/groups/{id}/employees — сотрудники
- POST   /admin/corporate/groups/{id}/employees — добавить сотрудника
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import date

from app.db.session import get_db
from app.services.corporate_service import CorporateService
from .operators import get_current_admin, get_current_owner
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/corporate")


# ============================================
# Pydantic Models
# ============================================

class CreateGroupRequest(BaseModel):
    company_name: str
    inn: Optional[str] = None
    legal_address: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    billing_type: str = "prepaid"
    monthly_limit: Optional[float] = None
    credit_limit: float = 0
    contract_number: Optional[str] = None
    contract_date: Optional[str] = None
    contract_expires: Optional[str] = None


class UpdateGroupRequest(BaseModel):
    company_name: Optional[str] = None
    inn: Optional[str] = None
    legal_address: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    monthly_limit: Optional[float] = None
    credit_limit: Optional[float] = None
    billing_type: Optional[str] = None


class TopupRequest(BaseModel):
    amount: float
    description: str = ""


class BlockRequest(BaseModel):
    reason: str


class AddEmployeeRequest(BaseModel):
    user_id: str
    role: str = "employee"
    position: Optional[str] = None
    monthly_limit: Optional[float] = None
    daily_limit: Optional[float] = None


class UpdateEmployeeRequest(BaseModel):
    role: Optional[str] = None
    position: Optional[str] = None
    monthly_limit: Optional[float] = None
    daily_limit: Optional[float] = None
    is_active: Optional[bool] = None


# ============================================
# Groups CRUD
# ============================================

@router.get("/groups", summary="List corporate groups", description="Returns all corporate groups with employee counts.", responses=ADMIN_RESPONSES)
async def list_groups(
    request: Request,
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Список корпоративных клиентов."""
    get_current_owner(request, db)
    svc = CorporateService(db)
    groups = svc.list_groups(active_only=not include_inactive)
    return {"success": True, "groups": groups, "total": len(groups)}


@router.post("/groups", summary="Create corporate group", description="Creates a new corporate client group.", responses=ADMIN_RESPONSES)
async def create_group(
    request: Request,
    body: CreateGroupRequest,
    db: Session = Depends(get_db),
):
    """Создать корпоративный аккаунт."""
    get_current_admin(request, db)
    svc = CorporateService(db)
    result = svc.create_group(body.model_dump())
    return {"success": True, "data": result}


@router.get("/groups/{group_id}", summary="Get corporate group", description="Returns group details with employee list.", responses=ADMIN_RESPONSES)
async def get_group(
    request: Request,
    group_id: str,
    db: Session = Depends(get_db),
):
    """Детали корпоративной группы."""
    get_current_owner(request, db)
    svc = CorporateService(db)
    group = svc.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Corporate group not found")

    employees = svc.get_employees(group_id, active_only=False)
    group["employees"] = employees
    return {"success": True, "data": group}


@router.put("/groups/{group_id}", summary="Update corporate group", description="Updates group settings and billing info.", responses=ADMIN_RESPONSES)
async def update_group(
    request: Request,
    group_id: str,
    body: UpdateGroupRequest,
    db: Session = Depends(get_db),
):
    """Обновить данные компании."""
    get_current_admin(request, db)
    svc = CorporateService(db)
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    svc.update_group(group_id, data)
    return {"success": True}


@router.post("/groups/{group_id}/topup", summary="Topup group balance", description="Adds funds to a corporate group balance.", responses=ADMIN_RESPONSES)
async def topup_balance(
    request: Request,
    group_id: str,
    body: TopupRequest,
    db: Session = Depends(get_db),
):
    """Пополнить баланс корпоративного клиента (prepaid)."""
    get_current_admin(request, db)
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    svc = CorporateService(db)
    result = svc.topup_balance(group_id, body.amount, body.description)
    return {"success": True, "data": result}


@router.post("/groups/{group_id}/block", summary="Block corporate group", description="Blocks a corporate group from charging.", responses=ADMIN_RESPONSES)
async def block_group(
    request: Request,
    group_id: str,
    body: BlockRequest,
    db: Session = Depends(get_db),
):
    """Заблокировать корпоративный аккаунт."""
    get_current_admin(request, db)
    svc = CorporateService(db)
    svc.block_group(group_id, body.reason)
    return {"success": True}


@router.post("/groups/{group_id}/unblock", summary="Unblock corporate group", description="Reactivates a blocked corporate group.", responses=ADMIN_RESPONSES)
async def unblock_group(
    request: Request,
    group_id: str,
    db: Session = Depends(get_db),
):
    """Разблокировать корпоративный аккаунт."""
    get_current_admin(request, db)
    svc = CorporateService(db)
    svc.unblock_group(group_id)
    return {"success": True}


@router.get("/groups/{group_id}/report", summary="Get group usage report", description="Returns usage report for a date period, optionally by employee.", responses=ADMIN_RESPONSES)
async def get_report(
    request: Request,
    group_id: str,
    period_start: str = Query(...),
    period_end: str = Query(...),
    employee_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Отчёт по зарядкам компании."""
    get_current_admin(request, db)
    svc = CorporateService(db)
    report = svc.get_report(
        group_id,
        date.fromisoformat(period_start),
        date.fromisoformat(period_end),
        employee_id,
    )
    return {"success": True, "data": report}


# ============================================
# Employees
# ============================================

@router.get("/groups/{group_id}/employees", summary="List group employees", description="Returns all employees in a corporate group.", responses=ADMIN_RESPONSES)
async def list_employees(
    request: Request,
    group_id: str,
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Список сотрудников корпоративной группы."""
    get_current_admin(request, db)
    svc = CorporateService(db)
    employees = svc.get_employees(group_id, active_only=not include_inactive)
    return {"success": True, "employees": employees}


@router.post("/groups/{group_id}/employees", summary="Add employee to group", description="Adds a user as an employee in the corporate group.", responses=ADMIN_RESPONSES)
async def add_employee(
    request: Request,
    group_id: str,
    body: AddEmployeeRequest,
    db: Session = Depends(get_db),
):
    """Добавить сотрудника в корпоративную группу."""
    get_current_admin(request, db)
    svc = CorporateService(db)
    result = svc.add_employee(group_id, body.model_dump())
    return {"success": True, "data": result}


@router.put("/groups/{group_id}/employees/{employee_id}", summary="Update employee", description="Updates employee role, limits, or active status.", responses=ADMIN_RESPONSES)
async def update_employee(
    request: Request,
    group_id: str,
    employee_id: str,
    body: UpdateEmployeeRequest,
    db: Session = Depends(get_db),
):
    """Обновить данные/лимиты сотрудника."""
    get_current_admin(request, db)
    svc = CorporateService(db)
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    svc.update_employee(employee_id, data)
    return {"success": True}


@router.delete("/groups/{group_id}/employees/{employee_id}", summary="Remove employee", description="Removes an employee from the corporate group.", responses=ADMIN_RESPONSES)
async def remove_employee(
    request: Request,
    group_id: str,
    employee_id: str,
    db: Session = Depends(get_db),
):
    """Удалить сотрудника из корпоративной группы (деактивация)."""
    get_current_admin(request, db)
    svc = CorporateService(db)
    svc.remove_employee(employee_id)
    return {"success": True}
