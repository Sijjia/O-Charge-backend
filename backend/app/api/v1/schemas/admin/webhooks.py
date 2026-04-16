"""Pydantic schemas for Webhook admin API"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List


class WebhookCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Human-readable name")
    url: str = Field(..., min_length=10, max_length=2000, description="POST target URL (https://...)")
    secret: str = Field(..., min_length=8, max_length=500, description="HMAC-SHA256 signing key")
    events: List[str] = Field(..., min_length=1, description="Event types to subscribe to")

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        if not v.startswith(('http://', 'https://')):
            raise ValueError('URL must start with http:// or https://')
        return v

    @field_validator('events')
    @classmethod
    def deduplicate_events(cls, v):
        return list(dict.fromkeys(v))  # deduplicate preserving order


class WebhookUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    url: Optional[str] = Field(None, max_length=2000)
    secret: Optional[str] = Field(None, min_length=8, max_length=500)
    events: Optional[List[str]] = None
    is_active: Optional[bool] = None

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        if v is not None and not v.startswith(('http://', 'https://')):
            raise ValueError('URL must start with http:// or https://')
        return v

    @field_validator('events')
    @classmethod
    def deduplicate_events(cls, v):
        if v is not None:
            return list(dict.fromkeys(v))
        return v


class WebhookItem(BaseModel):
    id: str
    name: str
    url: str
    events: List[str]
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class WebhookListResponse(BaseModel):
    success: bool = True
    data: List[WebhookItem] = []
    total: int = 0


class WebhookDetailResponse(BaseModel):
    success: bool = True
    webhook: WebhookItem


class WebhookMutationResponse(BaseModel):
    success: bool = True
    webhook_id: Optional[str] = None
    message: str = ""


class WebhookLogItem(BaseModel):
    id: str
    event_type: str
    status_code: Optional[int] = None
    success: bool
    attempt: int
    error: Optional[str] = None
    created_at: Optional[str] = None


class WebhookLogResponse(BaseModel):
    success: bool = True
    logs: List[WebhookLogItem] = []
    total: int = 0
