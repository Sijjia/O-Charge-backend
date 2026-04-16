"""Admin equipment catalog response schemas."""
from pydantic import BaseModel, Field
from typing import Optional, List


class ManufacturerItem(BaseModel):
    id: str = ""
    name: str = ""
    name_cn: Optional[str] = None
    country: Optional[str] = None
    website: Optional[str] = None
    logo_url: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True
    model_count: int = 0
    created_at: Optional[str] = None


class ManufacturerListResponse(BaseModel):
    success: bool = True
    data: List[ManufacturerItem] = []
    total: int = 0

    model_config = {"json_schema_extra": {"examples": [{"success": True, "data": [], "total": 0}]}}


class ModelItem(BaseModel):
    id: str = ""
    manufacturer_id: str = ""
    manufacturer_name: Optional[str] = None
    name: str = ""
    type: str = "DC"
    power_kw: Optional[float] = None
    connector_types: List[str] = []
    num_connectors: Optional[int] = None
    voltage_range: Optional[str] = None
    image_url: Optional[str] = None
    price_min_usd: Optional[int] = None
    price_max_usd: Optional[int] = None
    ocpp_versions: List[str] = []
    ip_rating: Optional[str] = None
    dimensions: Optional[str] = None
    weight_kg: Optional[float] = None
    operating_temp: Optional[str] = None
    efficiency_percent: Optional[float] = None
    display_size: Optional[str] = None
    is_active: bool = True
    created_at: Optional[str] = None


class ManufacturerDetailResponse(BaseModel):
    success: bool = True
    manufacturer: ManufacturerItem = ManufacturerItem()
    models: List[ModelItem] = []

    model_config = {"json_schema_extra": {"examples": [{"success": True, "manufacturer": {}, "models": []}]}}


class ModelListResponse(BaseModel):
    success: bool = True
    data: List[ModelItem] = []
    total: int = 0

    model_config = {"json_schema_extra": {"examples": [{"success": True, "data": [], "total": 0}]}}


class ModelDetailResponse(BaseModel):
    success: bool = True
    model: ModelItem = ModelItem()

    model_config = {"json_schema_extra": {"examples": [{"success": True, "model": {}}]}}


class EquipmentMutationResponse(BaseModel):
    success: bool = True
    id: Optional[str] = None
    message: str = ""

    model_config = {"json_schema_extra": {"examples": [{"success": True, "id": "abc123", "message": "Created"}]}}
