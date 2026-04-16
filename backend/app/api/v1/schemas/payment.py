"""Payment endpoint response schemas."""
from pydantic import BaseModel
from typing import Optional


class ForceStatusCheckResponse(BaseModel):
    success: bool = True
    message: str = ""
    invoice_id: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "Status check initiated", "invoice_id": "INV-123456"}
    ]}}


class H2hStatusResponse(BaseModel):
    success: bool = True
    transaction_id: Optional[str] = None
    invoice_id: Optional[str] = None
    status: Optional[str] = None
    client_id: Optional[str] = None
    amount: Optional[float] = None

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "transaction_id": "TXN-123", "invoice_id": "INV-456", "status": "approved", "client_id": "uuid-1234", "amount": 500.0}
    ]}}


class CancelPaymentResponse(BaseModel):
    success: bool = True
    message: str = ""
    invoice_id: Optional[str] = None
    status: str = "canceled"

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "message": "Payment cancelled", "invoice_id": "INV-123456", "status": "canceled"}
    ]}}


class WebhookResponse(BaseModel):
    status: str = "received"
    invoice_id: Optional[str] = None
    provider: Optional[str] = None

    model_config = {"json_schema_extra": {"examples": [
        {"status": "received", "invoice_id": "INV-123456", "provider": "odengi"}
    ]}}
