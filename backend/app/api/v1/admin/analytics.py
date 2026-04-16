"""
Admin API: Revenue analytics — аналитика выручки и обзорная статистика
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import date, timedelta
import logging

from app.db.session import get_db
from .operators import get_current_admin, get_current_owner
from app.api.v1.schemas.admin.analytics import RevenueResponse, RevenueByPartnerResponse, RevenueByLocationResponse, OverviewResponse, HeatmapResponse, UserGrowthResponse, UptimeResponse
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/analytics")
logger = logging.getLogger(__name__)


@router.get("/revenue", summary="Revenue analytics", description="Returns revenue data grouped by day/week/month with optional station/partner filtering.", response_model=RevenueResponse, responses=ADMIN_RESPONSES)
async def get_revenue(
    request: Request,
    db: Session = Depends(get_db),
    date_from: Optional[date] = Query(None, description="Начало периода"),
    date_to: Optional[date] = Query(None, description="Конец периода"),
    group_by: str = Query("day", description="Группировка: day, week, month"),
    station_id: Optional[str] = Query(None, max_length=50),
    partner_id: Optional[str] = Query(None, max_length=100),
):
    """Аналитика выручки за период с группировкой"""
    admin = get_current_admin(request, db)

    if group_by not in ("day", "week", "month"):
        raise HTTPException(status_code=400, detail="group_by: day, week, month")

    # Defaults: последние 30 дней
    if not date_from:
        date_from = date.today() - timedelta(days=30)
    if not date_to:
        date_to = date.today()

    where_clauses = [
        "cs.status = 'stopped'",
        "cs.stop_time >= :date_from",
        "cs.stop_time < :date_to + INTERVAL '1 day'"
    ]
    params = {"date_from": date_from, "date_to": date_to}

    if station_id:
        where_clauses.append("cs.station_id = :station_id")
        params["station_id"] = station_id
    if partner_id:
        where_clauses.append("cs.partner_id = :partner_id")
        params["partner_id"] = partner_id

    where_sql = " AND ".join(where_clauses)

    # Определяем SQL trunc для группировки
    trunc_map = {"day": "day", "week": "week", "month": "month"}
    trunc = trunc_map[group_by]

    rows = db.execute(text(f"""
        SELECT
            date_trunc(:trunc, cs.stop_time) as period,
            COUNT(*) as sessions,
            COALESCE(SUM(cs.amount), 0) as total_revenue,
            COALESCE(SUM(cs.energy), 0) as total_energy_kwh,
            COALESCE(SUM(cs.partner_share), 0) as total_partner_share,
            COALESCE(SUM(cs.platform_share), 0) as total_platform_share,
            COALESCE(AVG(cs.amount), 0) as avg_session_revenue
        FROM charging_sessions cs
        WHERE {where_sql}
        GROUP BY period
        ORDER BY period
    """), {**params, "trunc": trunc}).fetchall()

    data = []
    for r in rows:
        data.append({
            "period": r.period.isoformat() if r.period else None,
            "sessions": r.sessions,
            "total_revenue": float(r.total_revenue),
            "total_energy_kwh": float(r.total_energy_kwh),
            "total_partner_share": float(r.total_partner_share),
            "total_platform_share": float(r.total_platform_share),
            "avg_session_revenue": round(float(r.avg_session_revenue), 2),
        })

    # Итоговые числа
    totals = {
        "sessions": sum(d["sessions"] for d in data),
        "revenue": sum(d["total_revenue"] for d in data),
        "energy_kwh": sum(d["total_energy_kwh"] for d in data),
        "partner_share": sum(d["total_partner_share"] for d in data),
        "platform_share": sum(d["total_platform_share"] for d in data),
    }

    return {
        "success": True,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "group_by": group_by,
        "data": data,
        "totals": totals,
    }


@router.get("/revenue/by-partner", summary="Revenue by partner", description="Returns revenue breakdown by partner.", response_model=RevenueByPartnerResponse, responses=ADMIN_RESPONSES)
async def get_revenue_by_partner(
    request: Request,
    db: Session = Depends(get_db),
    date_from: Optional[date] = Query(None, description="Начало периода"),
    date_to: Optional[date] = Query(None, description="Конец периода"),
):
    """Выручка в разрезе партнёров"""
    admin = get_current_admin(request, db)

    if not date_from:
        date_from = date.today() - timedelta(days=30)
    if not date_to:
        date_to = date.today()

    rows = db.execute(text("""
        SELECT
            p.id as partner_id,
            COALESCE(p.company_name, p.contact_name, 'Без имени') as partner_name,
            COUNT(cs.id) as sessions,
            COALESCE(SUM(cs.amount), 0) as total_revenue,
            COALESCE(SUM(cs.energy), 0) as total_energy_kwh,
            COALESCE(SUM(cs.partner_share), 0) as total_partner_share,
            COALESCE(SUM(cs.platform_share), 0) as total_platform_share,
            COUNT(DISTINCT cs.station_id) as station_count
        FROM partners p
        LEFT JOIN charging_sessions cs
            ON cs.partner_id = p.id
            AND cs.status = 'stopped'
            AND cs.stop_time >= :date_from
            AND cs.stop_time < :date_to + INTERVAL '1 day'
        WHERE p.status = 'active'
        GROUP BY p.id, partner_name
        ORDER BY total_revenue DESC
    """), {"date_from": date_from, "date_to": date_to}).fetchall()

    data = []
    for r in rows:
        data.append({
            "partner_id": str(r.partner_id),
            "partner_name": r.partner_name,
            "sessions": r.sessions,
            "total_revenue": float(r.total_revenue),
            "total_energy_kwh": float(r.total_energy_kwh),
            "total_partner_share": float(r.total_partner_share),
            "total_platform_share": float(r.total_platform_share),
            "station_count": r.station_count,
        })

    totals = {
        "sessions": sum(d["sessions"] for d in data),
        "revenue": sum(d["total_revenue"] for d in data),
        "energy_kwh": sum(d["total_energy_kwh"] for d in data),
        "partner_share": sum(d["total_partner_share"] for d in data),
        "platform_share": sum(d["total_platform_share"] for d in data),
    }

    return {
        "success": True,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "data": data,
        "totals": totals,
    }


@router.get("/revenue/by-location", summary="Revenue by location", description="Returns revenue breakdown by location.", response_model=RevenueByLocationResponse, responses=ADMIN_RESPONSES)
async def get_revenue_by_location(
    request: Request,
    db: Session = Depends(get_db),
    date_from: Optional[date] = Query(None, description="Начало периода"),
    date_to: Optional[date] = Query(None, description="Конец периода"),
    partner_id: Optional[str] = Query(None, max_length=100),
):
    """Выручка в разрезе локаций"""
    admin = get_current_admin(request, db)

    if not date_from:
        date_from = date.today() - timedelta(days=30)
    if not date_to:
        date_to = date.today()

    where_clauses = [
        "cs.status = 'stopped'",
        "cs.stop_time >= :date_from",
        "cs.stop_time < :date_to + INTERVAL '1 day'"
    ]
    params: dict = {"date_from": date_from, "date_to": date_to}

    if partner_id:
        where_clauses.append("cs.partner_id = :partner_id")
        params["partner_id"] = partner_id

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(text(f"""
        SELECT
            l.id as location_id,
            l.name as location_name,
            COALESCE(l.address, '') as location_address,
            COALESCE(l.city, '') as location_city,
            l.partner_id,
            COALESCE(p.company_name, 'Red Petroleum') as partner_name,
            COUNT(DISTINCT s.id) as station_count,
            COUNT(cs.id) as sessions,
            COALESCE(SUM(cs.amount), 0) as total_revenue,
            COALESCE(SUM(cs.energy), 0) as total_energy_kwh,
            COALESCE(SUM(cs.partner_share), 0) as total_partner_share,
            COALESCE(SUM(cs.platform_share), 0) as total_platform_share
        FROM locations l
        LEFT JOIN partners p ON l.partner_id = p.id
        LEFT JOIN stations s ON s.location_id = l.id
        LEFT JOIN charging_sessions cs
            ON cs.station_id = s.id
            AND {where_sql}
        GROUP BY l.id, l.name, l.address, l.city, l.partner_id, p.company_name
        ORDER BY total_revenue DESC
    """), params).fetchall()

    data = []
    for r in rows:
        data.append({
            "location_id": str(r.location_id),
            "location_name": r.location_name,
            "location_address": r.location_address,
            "location_city": r.location_city,
            "partner_id": r.partner_id,
            "partner_name": r.partner_name,
            "station_count": r.station_count,
            "sessions": r.sessions,
            "total_revenue": float(r.total_revenue),
            "total_energy_kwh": float(r.total_energy_kwh),
            "total_partner_share": float(r.total_partner_share),
            "total_platform_share": float(r.total_platform_share),
        })

    totals = {
        "sessions": sum(d["sessions"] for d in data),
        "revenue": sum(d["total_revenue"] for d in data),
        "energy_kwh": sum(d["total_energy_kwh"] for d in data),
    }

    return {
        "success": True,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "data": data,
        "totals": totals,
    }


@router.get("/overview", summary="Dashboard overview", description="Returns infrastructure counts, active sessions, and revenue summaries (today/week/month).", response_model=OverviewResponse, responses=ADMIN_RESPONSES)
async def get_overview(
    request: Request,
    db: Session = Depends(get_db),
):
    """Обзорная статистика (dashboard) — один запрос вместо четырёх"""
    admin = get_current_owner(request, db)

    row = db.execute(text("""
        SELECT
            -- infrastructure
            (SELECT COUNT(*) FROM stations WHERE status = 'active') as total_stations,
            (SELECT COUNT(*) FROM stations WHERE is_available = true) as online_stations,
            (SELECT COUNT(*) FROM locations) as total_locations,
            (SELECT COUNT(*) FROM clients) as total_clients,
            (SELECT COUNT(*) FROM charging_sessions WHERE status = 'started') as active_sessions,
            (SELECT COUNT(*) FROM partners WHERE status = 'active') as total_partners,
            -- revenue today
            COUNT(*) FILTER (WHERE status = 'stopped' AND stop_time >= CURRENT_DATE) as today_sessions,
            COALESCE(SUM(amount) FILTER (WHERE status = 'stopped' AND stop_time >= CURRENT_DATE), 0) as today_revenue,
            COALESCE(SUM(energy) FILTER (WHERE status = 'stopped' AND stop_time >= CURRENT_DATE), 0) as today_energy,
            -- revenue week
            COUNT(*) FILTER (WHERE status = 'stopped' AND stop_time >= date_trunc('week', CURRENT_DATE)) as week_sessions,
            COALESCE(SUM(amount) FILTER (WHERE status = 'stopped' AND stop_time >= date_trunc('week', CURRENT_DATE)), 0) as week_revenue,
            COALESCE(SUM(energy) FILTER (WHERE status = 'stopped' AND stop_time >= date_trunc('week', CURRENT_DATE)), 0) as week_energy,
            -- revenue month
            COUNT(*) FILTER (WHERE status = 'stopped' AND stop_time >= date_trunc('month', CURRENT_DATE)) as month_sessions,
            COALESCE(SUM(amount) FILTER (WHERE status = 'stopped' AND stop_time >= date_trunc('month', CURRENT_DATE)), 0) as month_revenue,
            COALESCE(SUM(energy) FILTER (WHERE status = 'stopped' AND stop_time >= date_trunc('month', CURRENT_DATE)), 0) as month_energy
        FROM charging_sessions
    """)).fetchone()

    return {
        "success": True,
        "infrastructure": {
            "total_stations": row.total_stations,
            "online_stations": row.online_stations,
            "total_locations": row.total_locations,
            "total_clients": row.total_clients,
            "active_sessions": row.active_sessions,
            "total_partners": row.total_partners,
        },
        "revenue_today": {
            "sessions": row.today_sessions,
            "revenue": float(row.today_revenue),
            "energy_kwh": float(row.today_energy),
        },
        "revenue_week": {
            "sessions": row.week_sessions,
            "revenue": float(row.week_revenue),
            "energy_kwh": float(row.week_energy),
        },
        "revenue_month": {
            "sessions": row.month_sessions,
            "revenue": float(row.month_revenue),
            "energy_kwh": float(row.month_energy),
        },
    }


# ============================================================================
# STAT-01: Тепловая карта загруженности станций
# ============================================================================

@router.get("/heatmap", summary="Usage heatmap", description="Returns a 7x24 matrix of charging session counts by day-of-week and hour.", response_model=HeatmapResponse, responses=ADMIN_RESPONSES)
async def get_heatmap(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
):
    """Тепловая карта загруженности: матрица [день_недели][час] → количество сессий"""
    admin = get_current_admin(request, db)

    rows = db.execute(text("""
        SELECT
            EXTRACT(DOW FROM start_time)::int as day_of_week,
            EXTRACT(HOUR FROM start_time)::int as hour,
            COUNT(*) as session_count
        FROM charging_sessions
        WHERE start_time > now() - make_interval(days => :days)
        GROUP BY 1, 2
        ORDER BY 1, 2
    """), {"days": days}).fetchall()

    # Build 7x24 matrix (0=Sunday..6=Saturday)
    matrix: list[list[int]] = [[0] * 24 for _ in range(7)]
    for r in rows:
        matrix[r.day_of_week][r.hour] = r.session_count

    return {
        "success": True,
        "days": days,
        "matrix": matrix,
        "day_labels": ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"],
    }


# ============================================================================
# STAT-03: График роста пользователей
# ============================================================================

@router.get("/user-growth", summary="User growth", description="Returns new user registrations over time with cumulative totals.", response_model=UserGrowthResponse, responses=ADMIN_RESPONSES)
async def get_user_growth(
    request: Request,
    db: Session = Depends(get_db),
    group_by: str = Query("day", description="day, week, month"),
):
    """Кумулятивный рост пользователей"""
    admin = get_current_admin(request, db)

    if group_by not in ("day", "week", "month"):
        raise HTTPException(status_code=400, detail="group_by: day, week, month")

    rows = db.execute(text("""
        SELECT
            date_trunc(:trunc, created_at) as period,
            COUNT(*) as new_users,
            SUM(COUNT(*)) OVER (ORDER BY date_trunc(:trunc, created_at)) as cumulative
        FROM clients
        GROUP BY 1
        ORDER BY 1
    """), {"trunc": group_by}).fetchall()

    data = []
    for r in rows:
        data.append({
            "period": r.period.isoformat() if r.period else None,
            "new_users": r.new_users,
            "cumulative": r.cumulative,
        })

    return {
        "success": True,
        "group_by": group_by,
        "data": data,
    }


# ============================================================================
# STAT-04: Дашборд Uptime/SLA по станциям
# ============================================================================

@router.get("/uptime", summary="Station uptime", description="Returns uptime percentages and downtime minutes for each station.", response_model=UptimeResponse, responses=ADMIN_RESPONSES)
async def get_uptime(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=90),
):
    """Uptime/SLA по каждой станции за период"""
    admin = get_current_admin(request, db)

    total_minutes = days * 24 * 60

    # Get all stations from ocpp_station_status (stations that have reported status)
    stations = db.execute(text("""
        SELECT station_id FROM ocpp_station_status
        ORDER BY station_id
    """)).fetchall()

    results = []
    total_uptime_sum = 0.0

    for st in stations:
        sid = st.station_id

        # Estimate downtime from current status and last_heartbeat
        downtime_row = db.execute(text("""
            SELECT
                CASE WHEN status IN ('Faulted', 'Unavailable') THEN 1 ELSE 0 END as fault_events,
                CASE WHEN status IN ('Faulted', 'Unavailable')
                    THEN EXTRACT(EPOCH FROM (now() - COALESCE(updated_at, created_at))) / 60
                    ELSE 0
                END as downtime_minutes
            FROM ocpp_station_status
            WHERE station_id = :sid
        """), {"sid": sid}).fetchone()

        downtime_min = float(downtime_row.downtime_minutes) if downtime_row else 0
        incidents = downtime_row.fault_events if downtime_row else 0

        # Cap downtime at total period
        downtime_min = min(downtime_min, total_minutes)
        uptime_pct = round((1 - downtime_min / total_minutes) * 100, 2) if total_minutes > 0 else 100.0
        total_uptime_sum += uptime_pct

        results.append({
            "station_id": sid,
            "uptime_pct": uptime_pct,
            "downtime_minutes": round(downtime_min, 1),
            "incidents_count": incidents,
        })

    avg_uptime = round(total_uptime_sum / len(results), 2) if results else 100.0

    return {
        "success": True,
        "days": days,
        "total_minutes": total_minutes,
        "avg_uptime_pct": avg_uptime,
        "stations": results,
    }
