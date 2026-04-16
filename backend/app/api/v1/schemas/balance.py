"""Balance endpoint response schemas."""
from pydantic import BaseModel, Field
from typing import Optional


class AutoTopupSettingsResponse(BaseModel):
    success: bool = True
    auto_topup_enabled: bool = False
    auto_topup_threshold: float = 100.0
    auto_topup_amount: float = 500.0

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "auto_topup_enabled": True, "auto_topup_threshold": 100.0, "auto_topup_amount": 500.0}
    ]}}
