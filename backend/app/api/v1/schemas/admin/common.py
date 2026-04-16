"""Common admin response schemas."""
from pydantic import BaseModel


class AdminMessageResponse(BaseModel):
    success: bool = True
    message: str = ""

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "Operation completed"}
    ]}}
