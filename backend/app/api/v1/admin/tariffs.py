"""
Admin Tariff Plans CRUD API
- POST   /admin/tariffs           — создать тарифный план (с правилами)
- GET    /admin/tariffs           — список тарифных планов
- GET    /admin/tariffs/{id}      — детали плана с правилами
- PUT    /admin/tariffs/{id}      — обновить план
- DELETE /admin/tariffs/{id}      — деактивировать план
- POST   /admin/tariffs/{id}/rules          — добавить правило
- PUT    /admin/tariffs/{id}/rules/{rid}    — обновить правило
- DELETE /admin/tariffs/{id}/rules/{rid}    — удалить правило
- POST   /admin/tariffs/{id}/assign         — привязать план к станциям
- GET    /admin/tariffs/analytics            — аналитика тарифов
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import time as dt_time, date
import uuid
import json
import re

from app.db.session import get_db
from .operators import get_current_admin, get_current_owner
from app.api.v1.schemas.admin.tariffs import TariffListResponse, TariffDetailResponse, TariffCreateResponse, TariffAnalyticsResponse, RuleMutationResponse, AssignTariffResponse
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/tariffs")


# ============================================
# Pydantic models
# ============================================

class TariffRuleCreate(BaseModel):
    name: str
    tariff_type: str = "per_kwh"
    connector_type: str = "ALL"
    power_range_min: Optional[int] = None
    power_range_max: Optional[int] = None
    price: float
    currency: str = "KGS"
    time_start: Optional[str] = None  # "HH:MM"
    time_end: Optional[str] = None
    is_weekend: Optional[bool] = None
    days_of_week: Optional[List[int]] = None
    min_duration_minutes: Optional[int] = None
    max_duration_minutes: Optional[int] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    priority: int = 10


class TariffPlanCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_default: bool = False
    rules: Optional[List[TariffRuleCreate]] = None


class TariffPlanUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class TariffRuleUpdate(BaseModel):
    name: Optional[str] = None
    tariff_type: Optional[str] = None
    connector_type: Optional[str] = None
    power_range_min: Optional[int] = None
    power_range_max: Optional[int] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    is_weekend: Optional[bool] = None
    days_of_week: Optional[List[int]] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


class AssignTariffRequest(BaseModel):
    station_ids: List[str]


# ============================================
# Helper: parse time string
# ============================================

UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)

def validate_uuid(val: str, entity: str = "Resource") -> str:
    if not UUID_RE.match(val):
        raise HTTPException(status_code=404, detail=f"{entity} not found")
    return val

def parse_time(t: Optional[str]) -> Optional[dt_time]:
    if not t:
        return None
    parts = t.split(":")
    return dt_time(int(parts[0]), int(parts[1]))


# ============================================
# CRUD Tariff Plans
# ============================================

@router.post("", response_model=TariffCreateResponse, summary="Create tariff plan", description="Creates a new tariff plan with optional rules.", responses=ADMIN_RESPONSES)
async def create_tariff_plan(
    request: Request,
    body: TariffPlanCreate,
    db: Session = Depends(get_db),
):
    """Создать тарифный план (опционально с правилами)."""
    get_current_admin(request, db)
    plan_id = str(uuid.uuid4())

    # Если is_default=True, снять default с других
    if body.is_default:
        db.execute(text("UPDATE tariff_plans SET is_default = false WHERE is_default = true"))

    db.execute(text("""
        INSERT INTO tariff_plans (id, name, description, is_default)
        VALUES (:id, :name, :description, :is_default)
    """), {
        "id": plan_id,
        "name": body.name,
        "description": body.description,
        "is_default": body.is_default,
    })

    rules_count = 0
    if body.rules:
        for rule in body.rules:
            rule_id = str(uuid.uuid4())
            db.execute(text("""
                INSERT INTO tariff_rules (
                    id, tariff_plan_id, name, tariff_type, connector_type,
                    power_range_min, power_range_max, price, currency,
                    time_start, time_end, is_weekend, days_of_week,
                    min_duration_minutes, max_duration_minutes,
                    valid_from, valid_until, priority
                ) VALUES (
                    :id, :plan_id, :name, :tariff_type, :connector_type,
                    :power_min, :power_max, :price, :currency,
                    :time_start, :time_end, :is_weekend, :days_of_week,
                    :min_dur, :max_dur,
                    :valid_from, :valid_until, :priority
                )
            """), {
                "id": rule_id,
                "plan_id": plan_id,
                "name": rule.name,
                "tariff_type": rule.tariff_type,
                "connector_type": rule.connector_type,
                "power_min": rule.power_range_min,
                "power_max": rule.power_range_max,
                "price": rule.price,
                "currency": rule.currency,
                "time_start": parse_time(rule.time_start),
                "time_end": parse_time(rule.time_end),
                "is_weekend": rule.is_weekend,
                "days_of_week": rule.days_of_week,
                "min_dur": rule.min_duration_minutes,
                "max_dur": rule.max_duration_minutes,
                "valid_from": rule.valid_from,
                "valid_until": rule.valid_until,
                "priority": rule.priority,
            })
            rules_count += 1

    db.commit()

    return {
        "success": True,
        "data": {
            "id": plan_id,
            "name": body.name,
            "rules_count": rules_count,
        }
    }


@router.get("", response_model=TariffListResponse, summary="List tariff plans", description="Returns all tariff plans with rule and station counts.", responses=ADMIN_RESPONSES)
async def list_tariff_plans(
    request: Request,
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Список всех тарифных планов."""
    get_current_owner(request, db)
    where = "" if include_inactive else "WHERE tp.is_active = true"
    rows = db.execute(text(f"""
        SELECT
            tp.id, tp.name, tp.description, tp.is_default, tp.is_active,
            tp.created_at, tp.updated_at,
            COUNT(tr.id) FILTER (WHERE tr.is_active = true) AS rules_count,
            COUNT(DISTINCT s.id) AS stations_count
        FROM tariff_plans tp
        LEFT JOIN tariff_rules tr ON tr.tariff_plan_id = tp.id
        LEFT JOIN stations s ON s.tariff_plan_id = tp.id
        {where}
        GROUP BY tp.id
        ORDER BY tp.is_default DESC, tp.name
    """)).fetchall()

    plans = []
    for r in rows:
        plans.append({
            "id": str(r[0]),
            "name": r[1],
            "description": r[2],
            "is_default": r[3],
            "is_active": r[4],
            "created_at": r[5].isoformat() if r[5] else None,
            "updated_at": r[6].isoformat() if r[6] else None,
            "rules_count": r[7],
            "stations_count": r[8],
        })

    return {"success": True, "data": plans}


@router.get("/analytics", response_model=TariffAnalyticsResponse, summary="Tariff analytics", description="Returns tariff usage analytics for a date range.", responses=ADMIN_RESPONSES)
async def tariff_analytics(
    request: Request,
    station_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Аналитика применения тарифов."""
    get_current_admin(request, db)
    from app.services.pricing_service import PricingService
    svc = PricingService(db)

    d_from = date.fromisoformat(date_from) if date_from else None
    d_to = date.fromisoformat(date_to) if date_to else None

    analytics = svc.get_pricing_analytics(station_id, d_from, d_to)
    return {"success": True, "data": analytics}


@router.get("/{tariff_id}", response_model=TariffDetailResponse, summary="Get tariff plan details", description="Returns tariff plan with all rules.", responses=ADMIN_RESPONSES)
async def get_tariff_plan(
    request: Request,
    tariff_id: str,
    db: Session = Depends(get_db),
):
    """Детали тарифного плана с правилами."""
    get_current_owner(request, db)
    validate_uuid(tariff_id, "Tariff plan")
    plan = db.execute(text("""
        SELECT id, name, description, is_default, is_active, created_at, updated_at
        FROM tariff_plans WHERE id = :id
    """), {"id": tariff_id}).fetchone()

    if not plan:
        raise HTTPException(status_code=404, detail="Tariff plan not found")

    rules = db.execute(text("""
        SELECT
            id, name, tariff_type, connector_type,
            power_range_min, power_range_max, price, currency,
            time_start, time_end, is_weekend, days_of_week,
            min_duration_minutes, max_duration_minutes,
            valid_from, valid_until, priority, is_active
        FROM tariff_rules
        WHERE tariff_plan_id = :plan_id
        ORDER BY priority DESC, time_start
    """), {"plan_id": tariff_id}).fetchall()

    stations_count = db.execute(text(
        "SELECT COUNT(*) FROM stations WHERE tariff_plan_id = :id"
    ), {"id": tariff_id}).scalar()

    rules_list = []
    for r in rules:
        rules_list.append({
            "id": str(r[0]),
            "name": r[1],
            "tariff_type": r[2],
            "connector_type": r[3],
            "power_range_min": r[4],
            "power_range_max": r[5],
            "price": float(r[6]),
            "currency": r[7],
            "time_start": r[8].strftime("%H:%M") if r[8] else None,
            "time_end": r[9].strftime("%H:%M") if r[9] else None,
            "is_weekend": r[10],
            "days_of_week": r[11],
            "min_duration_minutes": r[12],
            "max_duration_minutes": r[13],
            "valid_from": r[14].isoformat() if r[14] else None,
            "valid_until": r[15].isoformat() if r[15] else None,
            "priority": r[16],
            "is_active": r[17],
        })

    return {
        "success": True,
        "data": {
            "id": str(plan[0]),
            "name": plan[1],
            "description": plan[2],
            "is_default": plan[3],
            "is_active": plan[4],
            "created_at": plan[5].isoformat() if plan[5] else None,
            "updated_at": plan[6].isoformat() if plan[6] else None,
            "stations_count": stations_count,
            "rules": rules_list,
        }
    }


@router.put("/{tariff_id}", summary="Update tariff plan", description="Updates tariff plan name, description, or status.", responses=ADMIN_RESPONSES)
async def update_tariff_plan(
    request: Request,
    tariff_id: str,
    body: TariffPlanUpdate,
    db: Session = Depends(get_db),
):
    """Обновить тарифный план."""
    get_current_admin(request, db)
    validate_uuid(tariff_id, "Tariff plan")
    existing = db.execute(text(
        "SELECT id FROM tariff_plans WHERE id = :id"
    ), {"id": tariff_id}).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Tariff plan not found")

    updates = []
    params = {"id": tariff_id}

    if body.name is not None:
        updates.append("name = :name")
        params["name"] = body.name
    if body.description is not None:
        updates.append("description = :description")
        params["description"] = body.description
    if body.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = body.is_active
    if body.is_default is not None:
        if body.is_default:
            db.execute(text("UPDATE tariff_plans SET is_default = false WHERE is_default = true"))
        updates.append("is_default = :is_default")
        params["is_default"] = body.is_default

    if updates:
        updates.append("updated_at = NOW()")
        db.execute(text(f"""
            UPDATE tariff_plans SET {', '.join(updates)} WHERE id = :id
        """), params)
        db.commit()

    return {"success": True}


@router.delete("/{tariff_id}", summary="Deactivate tariff plan", description="Soft-deactivates a tariff plan.", responses=ADMIN_RESPONSES)
async def delete_tariff_plan(
    request: Request,
    tariff_id: str,
    db: Session = Depends(get_db),
):
    """Деактивировать тарифный план (soft delete)."""
    get_current_admin(request, db)
    validate_uuid(tariff_id, "Tariff plan")
    db.execute(text("""
        UPDATE tariff_plans SET is_active = false, updated_at = NOW() WHERE id = :id
    """), {"id": tariff_id})
    db.commit()
    return {"success": True}


# ============================================
# CRUD Tariff Rules
# ============================================

@router.post("/{tariff_id}/rules", response_model=RuleMutationResponse, summary="Add tariff rule", description="Adds a new pricing rule to a tariff plan.", responses=ADMIN_RESPONSES)
async def add_rule(
    request: Request,
    tariff_id: str,
    body: TariffRuleCreate,
    db: Session = Depends(get_db),
):
    """Добавить правило к тарифному плану."""
    get_current_admin(request, db)
    plan = db.execute(text(
        "SELECT id FROM tariff_plans WHERE id = :id"
    ), {"id": tariff_id}).fetchone()
    if not plan:
        raise HTTPException(status_code=404, detail="Tariff plan not found")

    rule_id = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO tariff_rules (
            id, tariff_plan_id, name, tariff_type, connector_type,
            power_range_min, power_range_max, price, currency,
            time_start, time_end, is_weekend, days_of_week,
            min_duration_minutes, max_duration_minutes,
            valid_from, valid_until, priority
        ) VALUES (
            :id, :plan_id, :name, :tariff_type, :connector_type,
            :power_min, :power_max, :price, :currency,
            :time_start, :time_end, :is_weekend, :days_of_week,
            :min_dur, :max_dur,
            :valid_from, :valid_until, :priority
        )
    """), {
        "id": rule_id,
        "plan_id": tariff_id,
        "name": body.name,
        "tariff_type": body.tariff_type,
        "connector_type": body.connector_type,
        "power_min": body.power_range_min,
        "power_max": body.power_range_max,
        "price": body.price,
        "currency": body.currency,
        "time_start": parse_time(body.time_start),
        "time_end": parse_time(body.time_end),
        "is_weekend": body.is_weekend,
        "days_of_week": body.days_of_week,
        "min_dur": body.min_duration_minutes,
        "max_dur": body.max_duration_minutes,
        "valid_from": body.valid_from,
        "valid_until": body.valid_until,
        "priority": body.priority,
    })

    db.execute(text(
        "UPDATE tariff_plans SET updated_at = NOW() WHERE id = :id"
    ), {"id": tariff_id})
    db.commit()

    return {"success": True, "data": {"id": rule_id, "name": body.name}}


@router.put("/{tariff_id}/rules/{rule_id}", summary="Update tariff rule", description="Updates an existing pricing rule.", responses=ADMIN_RESPONSES)
async def update_rule(
    request: Request,
    tariff_id: str,
    rule_id: str,
    body: TariffRuleUpdate,
    db: Session = Depends(get_db),
):
    """Обновить правило тарификации."""
    get_current_admin(request, db)
    existing = db.execute(text(
        "SELECT id FROM tariff_rules WHERE id = :id AND tariff_plan_id = :plan_id"
    ), {"id": rule_id, "plan_id": tariff_id}).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Tariff rule not found")

    updates = []
    params = {"id": rule_id}

    if body.name is not None:
        updates.append("name = :name")
        params["name"] = body.name
    if body.tariff_type is not None:
        updates.append("tariff_type = :tariff_type")
        params["tariff_type"] = body.tariff_type
    if body.connector_type is not None:
        updates.append("connector_type = :connector_type")
        params["connector_type"] = body.connector_type
    if body.power_range_min is not None:
        updates.append("power_range_min = :power_min")
        params["power_min"] = body.power_range_min
    if body.power_range_max is not None:
        updates.append("power_range_max = :power_max")
        params["power_max"] = body.power_range_max
    if body.price is not None:
        updates.append("price = :price")
        params["price"] = body.price
    if body.currency is not None:
        updates.append("currency = :currency")
        params["currency"] = body.currency
    if body.time_start is not None:
        updates.append("time_start = :time_start")
        params["time_start"] = parse_time(body.time_start)
    if body.time_end is not None:
        updates.append("time_end = :time_end")
        params["time_end"] = parse_time(body.time_end)
    if body.is_weekend is not None:
        updates.append("is_weekend = :is_weekend")
        params["is_weekend"] = body.is_weekend
    if body.days_of_week is not None:
        updates.append("days_of_week = :days_of_week")
        params["days_of_week"] = body.days_of_week
    if body.priority is not None:
        updates.append("priority = :priority")
        params["priority"] = body.priority
    if body.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = body.is_active

    if updates:
        db.execute(text(f"""
            UPDATE tariff_rules SET {', '.join(updates)} WHERE id = :id
        """), params)
        db.execute(text(
            "UPDATE tariff_plans SET updated_at = NOW() WHERE id = :plan_id"
        ), {"plan_id": tariff_id})
        db.commit()

    return {"success": True}


@router.delete("/{tariff_id}/rules/{rule_id}", summary="Delete tariff rule", description="Permanently deletes a pricing rule.", responses=ADMIN_RESPONSES)
async def delete_rule(
    request: Request,
    tariff_id: str,
    rule_id: str,
    db: Session = Depends(get_db),
):
    """Удалить правило (soft delete)."""
    get_current_admin(request, db)
    db.execute(text("""
        UPDATE tariff_rules SET is_active = false WHERE id = :id AND tariff_plan_id = :plan_id
    """), {"id": rule_id, "plan_id": tariff_id})
    db.execute(text(
        "UPDATE tariff_plans SET updated_at = NOW() WHERE id = :id"
    ), {"id": tariff_id})
    db.commit()
    return {"success": True}


# ============================================
# Assign tariff to stations
# ============================================

@router.post("/{tariff_id}/assign", response_model=AssignTariffResponse, summary="Assign tariff to stations", description="Assigns a tariff plan to one or more stations.", responses=ADMIN_RESPONSES)
async def assign_tariff_to_stations(
    request: Request,
    tariff_id: str,
    body: AssignTariffRequest,
    db: Session = Depends(get_db),
):
    """Привязать тарифный план к станциям."""
    get_current_admin(request, db)
    plan = db.execute(text(
        "SELECT id FROM tariff_plans WHERE id = :id AND is_active = true"
    ), {"id": tariff_id}).fetchone()
    if not plan:
        raise HTTPException(status_code=404, detail="Tariff plan not found or inactive")

    updated = 0
    for sid in body.station_ids:
        result = db.execute(text("""
            UPDATE stations SET tariff_plan_id = :plan_id WHERE id = :station_id
        """), {"plan_id": tariff_id, "station_id": sid})
        updated += result.rowcount

    db.commit()
    return {"success": True, "updated_stations": updated}


# ============================================
# AI Pricing Intelligence
# ============================================

class CompetitorPriceCreate(BaseModel):
    competitor_name: str
    station_name: Optional[str] = None
    location_description: Optional[str] = None
    price_per_kwh: float
    currency: str = "KGS"
    connector_type: Optional[str] = None
    power_kw: Optional[float] = None
    charging_type: Optional[str] = None
    source: str = "manual"
    source_url: Optional[str] = None
    notes: Optional[str] = None


class CompetitorPriceUpdate(BaseModel):
    competitor_name: Optional[str] = None
    station_name: Optional[str] = None
    location_description: Optional[str] = None
    price_per_kwh: Optional[float] = None
    currency: Optional[str] = None
    connector_type: Optional[str] = None
    power_kw: Optional[float] = None
    charging_type: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    notes: Optional[str] = None


@router.post("/ai/analyze", summary="Run AI pricing analysis", description="Analyzes competitors, demand, and revenue to generate pricing recommendation.", responses=ADMIN_RESPONSES)
async def ai_analyze(
    request: Request,
    station_id: Optional[str] = Query(None, description="Station ID (null for global)"),
    date_range_days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
):
    """Запустить AI анализ ценообразования."""
    get_current_admin(request, db)
    from app.services.ai_pricing_service import AIPricingService
    svc = AIPricingService(db)
    result = svc.analyze_and_recommend(station_id=station_id, date_range_days=date_range_days)
    return {"success": True, "data": result}


@router.get("/ai/competitors", summary="List competitor prices", description="Returns active competitor price entries.", responses=ADMIN_RESPONSES)
async def list_competitors(
    request: Request,
    charging_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Список цен конкурентов."""
    get_current_admin(request, db)
    where = "WHERE cp.is_active = true"
    params: dict = {}
    if charging_type:
        where += " AND cp.charging_type = :charging_type"
        params["charging_type"] = charging_type

    rows = db.execute(text(f"""
        SELECT id, competitor_name, station_name, location_description,
               price_per_kwh, currency, connector_type, power_kw, charging_type,
               source, source_url, verified_at, notes, created_at, updated_at
        FROM competitor_prices cp
        {where}
        ORDER BY competitor_name, charging_type, power_kw
    """), params).fetchall()

    data = []
    for r in rows:
        data.append({
            "id": str(r[0]),
            "competitor_name": r[1],
            "station_name": r[2],
            "location_description": r[3],
            "price_per_kwh": float(r[4]),
            "currency": r[5],
            "connector_type": r[6],
            "power_kw": float(r[7]) if r[7] else None,
            "charging_type": r[8],
            "source": r[9],
            "source_url": r[10],
            "verified_at": r[11].isoformat() if r[11] else None,
            "notes": r[12],
            "created_at": r[13].isoformat() if r[13] else None,
            "updated_at": r[14].isoformat() if r[14] else None,
        })

    return {"success": True, "data": data}


@router.post("/ai/competitors", summary="Add competitor price", description="Adds a new competitor price entry.", responses=ADMIN_RESPONSES)
async def add_competitor(
    request: Request,
    body: CompetitorPriceCreate,
    db: Session = Depends(get_db),
):
    """Добавить цену конкурента."""
    get_current_admin(request, db)
    new_id = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO competitor_prices (
            id, competitor_name, station_name, location_description,
            price_per_kwh, currency, connector_type, power_kw, charging_type,
            source, source_url, notes, verified_at
        ) VALUES (
            :id, :name, :station, :location,
            :price, :currency, :connector, :power, :type,
            :source, :url, :notes, NOW()
        )
    """), {
        "id": new_id,
        "name": body.competitor_name,
        "station": body.station_name,
        "location": body.location_description,
        "price": body.price_per_kwh,
        "currency": body.currency,
        "connector": body.connector_type,
        "power": body.power_kw,
        "type": body.charging_type,
        "source": body.source,
        "url": body.source_url,
        "notes": body.notes,
    })
    db.commit()
    return {"success": True, "data": {"id": new_id}}


@router.put("/ai/competitors/{competitor_id}", summary="Update competitor price", description="Updates an existing competitor price entry.", responses=ADMIN_RESPONSES)
async def update_competitor(
    request: Request,
    competitor_id: str,
    body: CompetitorPriceUpdate,
    db: Session = Depends(get_db),
):
    """Обновить цену конкурента."""
    get_current_admin(request, db)
    existing = db.execute(text(
        "SELECT id FROM competitor_prices WHERE id = :id AND is_active = true"
    ), {"id": competitor_id}).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Competitor price not found")

    updates = []
    params: dict = {"id": competitor_id}
    for field in ["competitor_name", "station_name", "location_description",
                   "price_per_kwh", "currency", "connector_type", "power_kw",
                   "charging_type", "source", "source_url", "notes"]:
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field} = :{field}")
            params[field] = val

    if updates:
        updates.append("updated_at = NOW()")
        updates.append("verified_at = NOW()")
        db.execute(text(f"""
            UPDATE competitor_prices SET {', '.join(updates)} WHERE id = :id
        """), params)
        db.commit()

    return {"success": True}


@router.delete("/ai/competitors/{competitor_id}", summary="Delete competitor price", description="Soft-deletes a competitor price entry.", responses=ADMIN_RESPONSES)
async def delete_competitor(
    request: Request,
    competitor_id: str,
    db: Session = Depends(get_db),
):
    """Удалить цену конкурента (soft delete)."""
    get_current_admin(request, db)
    db.execute(text("""
        UPDATE competitor_prices SET is_active = false, updated_at = NOW() WHERE id = :id
    """), {"id": competitor_id})
    db.commit()
    return {"success": True}


@router.post("/ai/apply/{recommendation_id}", summary="Apply AI recommendation", description="Applies a pricing recommendation to the station.", responses=ADMIN_RESPONSES)
async def apply_recommendation(
    request: Request,
    recommendation_id: str,
    db: Session = Depends(get_db),
):
    """Применить AI-рекомендацию к станции."""
    admin = get_current_admin(request, db)
    rec = db.execute(text("""
        SELECT id, station_id, recommended_price_per_kwh, status
        FROM ai_pricing_recommendations WHERE id = :id
    """), {"id": recommendation_id}).fetchone()

    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if rec[3] == "applied":
        raise HTTPException(status_code=400, detail="Already applied")

    station_id = rec[1]
    new_price = float(rec[2])

    if station_id:
        db.execute(text("""
            UPDATE stations SET tariff_per_kwh = :price WHERE id = :station_id
        """), {"price": new_price, "station_id": station_id})
    else:
        # Глобальная рекомендация — обновляем все станции без индивидуального тарифа
        db.execute(text("""
            UPDATE stations SET tariff_per_kwh = :price WHERE status = 'active'
        """), {"price": new_price})

    db.execute(text("""
        UPDATE ai_pricing_recommendations
        SET status = 'applied', applied_at = NOW(), applied_by = :admin_id
        WHERE id = :id
    """), {"id": recommendation_id, "admin_id": admin["id"]})

    db.commit()
    return {"success": True, "applied_price": new_price}


@router.get("/ai/recommendations", summary="List AI recommendations", description="Returns AI pricing recommendations history.", responses=ADMIN_RESPONSES)
async def list_recommendations(
    request: Request,
    station_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """История AI-рекомендаций."""
    get_current_admin(request, db)
    where_parts = []
    params: dict = {"limit": limit}

    if station_id:
        where_parts.append("r.station_id = :station_id")
        params["station_id"] = station_id
    if status:
        where_parts.append("r.status = :status")
        params["status"] = status

    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""

    rows = db.execute(text(f"""
        SELECT r.id, r.station_id, r.recommended_price_per_kwh, r.recommended_price_night,
               r.current_price_per_kwh, r.avg_competitor_price, r.min_competitor_price,
               r.max_competitor_price, r.electricity_cost_per_kwh,
               r.estimated_revenue_change_percent, r.estimated_monthly_revenue,
               r.confidence_level, r.reasoning, r.factors,
               r.status, r.applied_at, r.applied_by, r.created_at,
               s.serial_number
        FROM ai_pricing_recommendations r
        LEFT JOIN stations s ON s.id = r.station_id
        {where}
        ORDER BY r.created_at DESC
        LIMIT :limit
    """), params).fetchall()

    data = []
    for r in rows:
        factors_raw = r[13]
        if isinstance(factors_raw, str):
            try:
                factors_raw = json.loads(factors_raw)
            except Exception:
                factors_raw = {}

        data.append({
            "id": str(r[0]),
            "station_id": r[1],
            "station_serial": r[18],
            "recommended_price_per_kwh": float(r[2]) if r[2] else None,
            "recommended_price_night": float(r[3]) if r[3] else None,
            "current_price_per_kwh": float(r[4]) if r[4] else None,
            "avg_competitor_price": float(r[5]) if r[5] else None,
            "min_competitor_price": float(r[6]) if r[6] else None,
            "max_competitor_price": float(r[7]) if r[7] else None,
            "electricity_cost_per_kwh": float(r[8]) if r[8] else None,
            "estimated_revenue_change_percent": float(r[9]) if r[9] else None,
            "estimated_monthly_revenue": float(r[10]) if r[10] else None,
            "confidence_level": r[11],
            "reasoning": r[12],
            "factors": factors_raw,
            "status": r[14],
            "applied_at": r[15].isoformat() if r[15] else None,
            "applied_by": r[16],
            "created_at": r[17].isoformat() if r[17] else None,
        })

    return {"success": True, "data": data}


# ============================================
# Batch Analysis & Selective Apply & Auto-Optimize
# ============================================

class BatchAnalyzeRequest(BaseModel):
    station_ids: Optional[List[str]] = None
    location_ids: Optional[List[str]] = None
    date_range_days: int = Field(30, ge=7, le=365)


class ApplySelectiveRequest(BaseModel):
    recommendation_id: str
    station_ids: Optional[List[str]] = None
    location_ids: Optional[List[str]] = None


class AutoOptimizeRequest(BaseModel):
    station_ids: Optional[List[str]] = None
    location_ids: Optional[List[str]] = None
    date_range_days: int = Field(30, ge=7, le=365)


@router.post("/ai/analyze-batch", summary="Batch AI analysis", description="Analyzes all or selected stations, returns per-station recommendations grouped by location.", responses=ADMIN_RESPONSES)
async def ai_analyze_batch(
    request: Request,
    body: BatchAnalyzeRequest,
    db: Session = Depends(get_db),
):
    """Batch анализ: per-station рекомендации по станциям/локациям."""
    get_current_admin(request, db)
    from app.services.ai_pricing_service import AIPricingService
    svc = AIPricingService(db)
    result = svc.analyze_batch(
        station_ids=body.station_ids,
        location_ids=body.location_ids,
        date_range_days=body.date_range_days,
    )
    return {"success": True, "data": result}


@router.post("/ai/apply-selective", summary="Apply recommendation selectively", description="Apply a recommendation to selected stations or all stations in selected locations.", responses=ADMIN_RESPONSES)
async def ai_apply_selective(
    request: Request,
    body: ApplySelectiveRequest,
    db: Session = Depends(get_db),
):
    """Применить рекомендацию выборочно к станциям или локациям."""
    admin = get_current_admin(request, db)
    from app.services.ai_pricing_service import AIPricingService
    svc = AIPricingService(db)
    result = svc.apply_selective(
        recommendation_id=body.recommendation_id,
        station_ids=body.station_ids,
        location_ids=body.location_ids,
        admin_id=admin["id"],
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Apply failed"))
    return {"success": True, "data": result}


@router.post("/ai/auto-optimize", summary="Auto-optimize prices", description="Automatically analyzes all stations and applies optimal prices where deviation > 5%.", responses=ADMIN_RESPONSES)
async def ai_auto_optimize(
    request: Request,
    body: AutoOptimizeRequest,
    db: Session = Depends(get_db),
):
    """Авто-оптимизация: анализ всех станций + автоматическое применение оптимальных цен."""
    admin = get_current_admin(request, db)
    from app.services.ai_pricing_service import AIPricingService
    svc = AIPricingService(db)
    result = svc.auto_optimize(
        station_ids=body.station_ids,
        location_ids=body.location_ids,
        date_range_days=body.date_range_days,
        admin_id=admin["id"],
    )
    return {"success": True, "data": result}
