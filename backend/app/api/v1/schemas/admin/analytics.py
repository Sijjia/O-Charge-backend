"""Admin analytics response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class RevenueDataItem(BaseModel):
    period: Optional[str] = None
    sessions: int = 0
    total_revenue: float = 0
    total_energy_kwh: float = 0
    total_partner_share: float = 0
    total_platform_share: float = 0
    avg_session_revenue: float = 0


class RevenueTotals(BaseModel):
    sessions: int = 0
    revenue: float = 0
    energy_kwh: float = 0
    partner_share: float = 0
    platform_share: float = 0


class RevenueResponse(BaseModel):
    success: bool = True
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    group_by: str = "day"
    data: List[RevenueDataItem] = []
    totals: Optional[RevenueTotals] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "date_from": "2025-01-01", "date_to": "2025-01-31", "group_by": "day", "data": [{"period": "2025-01-15", "sessions": 25, "total_revenue": 3000.0}], "totals": {"sessions": 25, "revenue": 3000.0}}
    ]}}


class RevenueByPartnerResponse(BaseModel):
    success: bool = True
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    data: List[dict] = []
    totals: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"partner_name": "EcoCharge", "sessions": 100, "total_revenue": 12000.0}]}
    ]}}


class RevenueByLocationResponse(BaseModel):
    success: bool = True
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    data: List[dict] = []
    totals: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"location_name": "Bishkek Mall", "sessions": 50, "total_revenue": 6000.0}]}
    ]}}


class OverviewResponse(BaseModel):
    success: bool = True
    infrastructure: Optional[dict] = None
    revenue_today: Optional[dict] = None
    revenue_week: Optional[dict] = None
    revenue_month: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "infrastructure": {"total_stations": 20, "online_stations": 15, "total_locations": 8, "total_clients": 500}, "revenue_today": {"sessions": 10, "revenue": 1200.0}}
    ]}}


class HeatmapResponse(BaseModel):
    success: bool = True
    days: int = 30
    matrix: Optional[List[List[int]]] = None
    day_labels: Optional[List[str]] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "days": 30, "matrix": [[0, 0, 1, 2, 5, 8, 12, 15, 10, 8, 6, 4, 3, 5, 8, 12, 15, 18, 14, 10, 6, 3, 1, 0]], "day_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
    ]}}


class UserGrowthResponse(BaseModel):
    success: bool = True
    group_by: str = "day"
    data: List[dict] = []

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "group_by": "day", "data": [{"period": "2025-01-15", "new_users": 5, "cumulative": 500}]}
    ]}}


class UptimeResponse(BaseModel):
    success: bool = True
    days: int = 30
    total_minutes: Optional[int] = None
    avg_uptime_pct: Optional[float] = None
    stations: List[dict] = []

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "days": 30, "avg_uptime_pct": 98.5, "stations": [{"station_id": "CHR-BGK-001", "uptime_pct": 99.2}]}
    ]}}
