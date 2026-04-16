"""Admin map integrations response schemas."""
from pydantic import BaseModel
from typing import Optional, List


class MapIntegrationItem(BaseModel):
    id: str
    platform: str
    display_name: str
    icon: Optional[str] = None
    api_key_masked: Optional[str] = None
    is_enabled: bool = False
    sync_interval_minutes: int = 60
    last_global_sync_at: Optional[str] = None
    locations_synced: int = 0
    locations_error: int = 0
    config: Optional[dict] = None


class MapIntegrationListResponse(BaseModel):
    success: bool = True
    integrations: List[MapIntegrationItem] = []


class MapIntegrationOverview(BaseModel):
    success: bool = True
    total_locations: int = 0
    platforms: List[dict] = []


class LocationSyncRow(BaseModel):
    location_id: str
    location_name: str
    platforms: dict = {}
