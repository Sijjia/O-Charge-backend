"""Notifications endpoint response schemas."""
from pydantic import BaseModel
from typing import Optional


class TestPushResponse(BaseModel):
    success: bool = True
    sent_to: int = 0
    message: str = ""

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "sent_to": 1, "message": "Test push sent"}
    ]}}
