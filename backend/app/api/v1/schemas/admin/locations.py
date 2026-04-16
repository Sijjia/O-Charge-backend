"""Admin locations response schemas."""
from pydantic import BaseModel, Field
from typing import Optional, List


class AdminLocationItem(BaseModel):
    id: str = ""
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    station_count: Optional[int] = None
    available_stations: Optional[int] = None
    partner_id: Optional[str] = None
    partner_name: Optional[str] = None
    revenue_share_percent: Optional[float] = None
    image_url: Optional[str] = None


class AdminLocationListResponse(BaseModel):
    success: bool = True
    data: List[AdminLocationItem] = []
    total: int = 0
    limit: int = 50
    offset: int = 0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "data": [{"id": "loc-001", "name": "Bishkek Mall", "address": "Chui 150", "city": "Bishkek", "station_count": 4}], "total": 1, "limit": 50, "offset": 0}
    ]}}


class LocationImageItem(BaseModel):
    id: str
    url: str
    caption: Optional[str] = None
    sort_order: int = 0


class LocationSyncStatusItem(BaseModel):
    platform: str
    display_name: str
    icon: Optional[str] = None
    is_enabled: bool = False
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    sync_status: str = "pending"
    sync_error: Optional[str] = None
    last_sync_at: Optional[str] = None


class AdminLocationDetailResponse(BaseModel):
    success: bool = True
    location: Optional[dict] = None
    stations: Optional[List[dict]] = None
    revenue: Optional[dict] = None
    images: List[LocationImageItem] = []
    sync_platforms: List[LocationSyncStatusItem] = []

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "location": {"id": "loc-001", "name": "Bishkek Mall"}, "stations": [], "revenue": {"total_revenue": 50000.0, "session_count": 150}, "images": [], "sync_platforms": []}
    ]}}


class LocationCreateRequest(BaseModel):
    name: str = Field(..., description="Location name")
    address: str = Field(..., description="Street address")
    city: Optional[str] = Field(None, description="City name")
    lat: Optional[float] = Field(None, description="Latitude")
    lng: Optional[float] = Field(None, description="Longitude")
    status: Optional[str] = Field("active", description="Location status")
    partner_id: Optional[str] = Field(None, description="Partner UUID (for partner locations)")

    model_config = {"json_schema_extra": {"examples": [
        {"name": "Bishkek Mall", "address": "Chui 150", "city": "Bishkek", "lat": 42.874, "lng": 74.612}
    ]}}


class LocationUpdateRequest(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    status: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"name": "Bishkek Mall Updated"}
    ]}}


class LocationMutationResponse(BaseModel):
    success: bool = True
    location_id: Optional[str] = None
    message: str = ""

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "location_id": "loc-001", "message": "Location created"}
    ]}}
