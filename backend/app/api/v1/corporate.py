"""
Corporate Panel API — для корпоративных админов и сотрудников.
- GET  /corporate/dashboard    — KPI компании (corporate_admin)
- GET  /corporate/employees    — список сотрудников (corporate_admin)
- POST /corporate/employees    — добавить сотрудника (corporate_admin)
- PATCH /corporate/employees/{id} — изменить лимиты (corporate_admin)
- DELETE /corporate/employees/{id} — удалить сотрудника (corporate_admin)
- GET  /corporate/reports      — отчёт по зарядкам (corporate_admin)
- GET  /corporate/me           — инфо о корп. аккаунте (employee/admin)
- GET  /corporate/profile      — alias для /me (совместимость с PWA layout)
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional
from datetime import date

from app.db.session import get_db
from app.services.corporate_service import CorporateService
from app.api.v1.schemas.common import AUTH_RESPONSES

router = APIRouter(prefix="/corporate")


# ============================================
# Helpers
# ============================================

def _get_user_id(request: Request) -> str:
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return client_id


def _get_employee_group(db: Session, user_id: str) -> tuple:
    """Возвращает (employee_id, group_id, role) или raises 403."""
    try:
        row = db.execute(text("""
            SELECT ce.id, ce.corporate_group_id, ce.role
            FROM corporate_employees ce
            WHERE ce.user_id = :user_id AND ce.is_active = true
        """), {"user_id": user_id}).fetchone()
    except Exception:
        # Невалидный формат user_id (например, не UUID) — не корпоративный сотрудник
        raise HTTPException(status_code=403, detail="Not a corporate employee")
    if not row:
        raise HTTPException(status_code=403, detail="Not a corporate employee")
    return str(row[0]), str(row[1]), row[2]


# ============================================
# Pydantic Models
# ============================================

class AddEmployeeRequest(BaseModel):
    phone: str
    position: Optional[str] = None
    monthly_limit: Optional[float] = None
    daily_limit: Optional[float] = None


class UpdateEmployeeRequest(BaseModel):
    monthly_limit: Optional[float] = None
    daily_limit: Optional[float] = None
    is_active: Optional[bool] = None


# ============================================
# Endpoints
# ============================================

@router.get("/me", summary="Get my corporate info", description="Returns corporate group info for the authenticated employee.", responses=AUTH_RESPONSES)
async def get_my_corporate_info(
    request: Request,
    db: Session = Depends(get_db),
):
    """Информация о корпоративном аккаунте текущего пользователя."""
    user_id = _get_user_id(request)
    svc = CorporateService(db)
    return svc.get_employee_corporate_info(user_id)


@router.get("/dashboard", summary="Corporate dashboard", description="Returns corporate dashboard with employee count, usage stats, balance. Admin only.", responses=AUTH_RESPONSES)
async def get_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    """Дашборд корпоративного админа."""
    user_id = _get_user_id(request)
    _, group_id, role = _get_employee_group(db, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    svc = CorporateService(db)
    dashboard = svc.get_dashboard(group_id)
    return {"success": True, "data": dashboard}


@router.get("/employees", summary="List employees", description="Returns list of employees in the corporate group. Admin only.", responses=AUTH_RESPONSES)
async def list_employees(
    request: Request,
    db: Session = Depends(get_db),
):
    """Список сотрудников (только для corporate_admin)."""
    user_id = _get_user_id(request)
    _, group_id, role = _get_employee_group(db, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    svc = CorporateService(db)
    employees = svc.get_employees(group_id)
    return {"success": True, "employees": employees}


@router.post("/employees", summary="Add employee", description="Adds a client to the corporate group by phone number. Admin only.", responses=AUTH_RESPONSES)
async def add_employee(
    request: Request,
    body: AddEmployeeRequest,
    db: Session = Depends(get_db),
):
    """Добавить сотрудника по номеру телефона (только для corporate_admin)."""
    user_id = _get_user_id(request)
    _, group_id, role = _get_employee_group(db, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    # Найти user_id по телефону
    client = db.execute(text(
        "SELECT id FROM clients WHERE phone = :phone"
    ), {"phone": body.phone}).fetchone()

    if not client:
        raise HTTPException(status_code=404, detail="User with this phone not found")

    svc = CorporateService(db)
    result = svc.add_employee(group_id, {
        "user_id": str(client[0]),
        "role": "employee",
        "position": body.position,
        "monthly_limit": body.monthly_limit,
        "daily_limit": body.daily_limit,
    })
    return {"success": True, "data": result}


@router.patch("/employees/{employee_id}", summary="Update employee", description="Updates employee limits or active status. Admin only.", responses=AUTH_RESPONSES)
async def update_employee(
    request: Request,
    employee_id: str,
    body: UpdateEmployeeRequest,
    db: Session = Depends(get_db),
):
    """Изменить лимиты сотрудника (только для corporate_admin)."""
    user_id = _get_user_id(request)
    _, group_id, role = _get_employee_group(db, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    svc = CorporateService(db)
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    svc.update_employee(employee_id, data)
    return {"success": True}


@router.delete("/employees/{employee_id}", summary="Remove employee", description="Removes an employee from the corporate group. Admin only.", responses=AUTH_RESPONSES)
async def remove_employee(
    request: Request,
    employee_id: str,
    db: Session = Depends(get_db),
):
    """Удалить сотрудника (только для corporate_admin)."""
    user_id = _get_user_id(request)
    _, group_id, role = _get_employee_group(db, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    svc = CorporateService(db)
    svc.remove_employee(employee_id)
    return {"success": True}


@router.get("/reports", summary="Get corporate reports", description="Returns usage reports for a date period, optionally filtered by employee.", responses=AUTH_RESPONSES)
async def get_reports(
    request: Request,
    period_start: str = Query(...),
    period_end: str = Query(...),
    employee_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Отчёт по зарядкам (только для corporate_admin)."""
    user_id = _get_user_id(request)
    _, group_id, role = _get_employee_group(db, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    svc = CorporateService(db)
    report = svc.get_report(
        group_id,
        date.fromisoformat(period_start),
        date.fromisoformat(period_end),
        employee_id,
    )
    return {"success": True, "data": report}


@router.get("/profile", summary="Get corporate profile", description="Returns basic corporate profile: company name, role, is_corporate flag.", responses=AUTH_RESPONSES)
async def get_profile(
    request: Request,
    db: Session = Depends(get_db),
):
    """Профиль корпоративного аккаунта (alias для /me, используется CorporateLayout)."""
    user_id = _get_user_id(request)
    svc = CorporateService(db)
    info = svc.get_employee_corporate_info(user_id)
    return {
        "success": True,
        "company_name": info.get("company_name"),
        "name": info.get("position") or info.get("role", "Сотрудник"),
        "role": info.get("role"),
        "is_corporate": info.get("is_corporate", False),
    }
