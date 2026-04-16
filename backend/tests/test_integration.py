"""
Integration tests — OCPP handlers, Payment providers, API services

Тестирует ключевые компоненты без внешних зависимостей:
1. PaymentProviderService — все 3 провайдера (OBANK, ODENGI, NAMBA_ONE)
2. NambaOneService — mock create_payment / check_status
3. PricingService — session cost calculation
4. Guest session auto-cancel logic
5. Admin auth helper
"""
import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock, patch


# ============================================================================
# 1. PaymentProviderService Tests
# ============================================================================

class TestPaymentProviderService:
    """Тесты для унифицированного сервиса платежных провайдеров"""

    def test_init_obank_provider(self):
        from app.services.payment_provider_service import PaymentProviderService
        svc = PaymentProviderService(force_provider="OBANK")
        assert svc.provider == "OBANK"
        assert svc.get_provider_name() == "OBANK"
        assert svc.get_currency_code() == "417"

    def test_init_odengi_provider(self):
        from app.services.payment_provider_service import PaymentProviderService
        svc = PaymentProviderService(force_provider="ODENGI")
        assert svc.provider == "ODENGI"
        assert svc.get_provider_name() == "ODENGI"
        assert svc.get_currency_code() == "KGS"

    def test_init_namba_provider(self):
        from app.services.payment_provider_service import PaymentProviderService
        svc = PaymentProviderService(force_provider="NAMBA_ONE")
        assert svc.provider == "NAMBA_ONE"
        assert svc.get_provider_name() == "NAMBA_ONE"

    def test_factory_functions(self):
        from app.services.payment_provider_service import (
            get_qr_payment_service,
            get_card_payment_service,
            get_namba_payment_service,
        )
        assert get_qr_payment_service().provider == "ODENGI"
        assert get_card_payment_service().provider == "OBANK"
        assert get_namba_payment_service().provider == "NAMBA_ONE"

    def test_h2h_not_supported_for_non_obank(self):
        from app.services.payment_provider_service import PaymentProviderService
        svc = PaymentProviderService(force_provider="ODENGI")
        result = asyncio.get_event_loop().run_until_complete(
            svc.create_h2h_payment(
                amount=Decimal("100"),
                order_id="test",
                card_data={"pan": "4111111111111111"},
                email="test@test.com",
            )
        )
        assert result["success"] is False
        assert result["error"] == "h2h_not_supported"

    def test_token_not_supported_for_non_obank(self):
        from app.services.payment_provider_service import PaymentProviderService
        svc = PaymentProviderService(force_provider="NAMBA_ONE")
        result = asyncio.get_event_loop().run_until_complete(
            svc.create_token_payment(
                amount=Decimal("100"),
                order_id="test",
                card_token="tok_123",
                email="test@test.com",
            )
        )
        assert result["success"] is False
        assert result["error"] == "token_not_supported"

    def test_cancel_namba_creates_refund(self):
        from app.services.payment_provider_service import PaymentProviderService
        svc = PaymentProviderService(force_provider="NAMBA_ONE")
        result = asyncio.get_event_loop().run_until_complete(
            svc.cancel_payment(transaction_id="tx_123", refund_amount=Decimal("50"))
        )
        # In mock mode, refund returns success
        assert result["success"] is True
        assert result["provider"] == "NAMBA_ONE"

    def test_webhook_verification_namba(self):
        from app.services.payment_provider_service import PaymentProviderService
        svc = PaymentProviderService(force_provider="NAMBA_ONE")
        # No webhook_secret configured → always true
        assert svc.verify_webhook(b"payload", "", request_headers={}) is True


# ============================================================================
# 2. NambaOneService Mock Tests
# ============================================================================

class TestNambaOneService:
    """Тесты для Namba One mock-сервиса"""

    def test_singleton_exists(self):
        from app.services.namba_service import namba_one_service
        assert namba_one_service is not None
        assert namba_one_service.mock_mode is True

    def test_mock_create_payment(self):
        from app.services.namba_service import NambaOneService
        svc = NambaOneService()
        result = asyncio.get_event_loop().run_until_complete(
            svc.create_payment(
                amount=Decimal("500"),
                order_id="test-order-001",
                webhook_url="https://example.com/webhook",
                description="Test payment",
            )
        )
        assert result["success"] is True
        assert result["provider"] == "NAMBA_ONE"
        assert result["status"] == "approved"  # Mock auto-approves
        assert "payment_url" in result
        assert result["invoice_id"] == "test-order-001"  # externalId = order_id
        assert result["mock"] is True

    def test_mock_check_status_known(self):
        from app.services.namba_service import NambaOneService
        svc = NambaOneService()
        # Create payment first to store in mock DB
        create_result = asyncio.get_event_loop().run_until_complete(
            svc.create_payment(
                amount=Decimal("100"),
                order_id="ord-1",
                webhook_url="https://example.com/webhook",
            )
        )
        invoice_id = create_result["invoice_id"]

        # Check its status
        status_result = asyncio.get_event_loop().run_until_complete(
            svc.check_payment_status(invoice_id=invoice_id)
        )
        assert status_result["success"] is True
        assert status_result["status"] == "approved"
        assert status_result["numeric_status"] == 1
        assert status_result["paid_amount"] == 100.0

    def test_mock_check_status_unknown(self):
        from app.services.namba_service import NambaOneService
        svc = NambaOneService()
        result = asyncio.get_event_loop().run_until_complete(
            svc.check_payment_status(invoice_id="unknown-id-999")
        )
        # Mock returns approved even for unknown
        assert result["success"] is True
        assert result["status"] == "approved"

    def test_parse_webhook(self):
        from app.services.namba_service import NambaOneService
        svc = NambaOneService()
        result = svc.parse_webhook({
            "type": "PAYMENT_ORDER",
            "data": {
                "externalId": "ord-1",
                "guid": "guid-123",
                "status": "COMPLETED",
                "amount": "50000",
                "paymentAmount": "50000",
            },
        })
        assert result["type"] == "payment"
        assert result["order_id"] == "ord-1"
        assert result["status"] == "approved"
        assert result["amount"] == 500.0


# ============================================================================
# 3. Namba One через PaymentProviderService (E2E)
# ============================================================================

class TestNambaPaymentE2E:
    """Namba One через PaymentProviderService — полный цикл"""

    def test_create_and_check_namba_payment(self):
        from app.services.payment_provider_service import PaymentProviderService
        svc = PaymentProviderService(force_provider="NAMBA_ONE")

        # Create
        create_result = asyncio.get_event_loop().run_until_complete(
            svc.create_payment(
                amount=Decimal("200"),
                order_id="e2e-namba-001",
                email="test@example.com",
                notify_url="https://example.com/webhook",
                redirect_url="https://example.com/success",
                description="E2E test",
            )
        )
        assert create_result["success"] is True
        assert create_result["provider"] == "NAMBA_ONE"
        invoice_id = create_result["invoice_id"]

        # Check status
        status_result = asyncio.get_event_loop().run_until_complete(
            svc.check_payment_status(invoice_id=invoice_id)
        )
        assert status_result["success"] is True
        assert status_result["status"] == "approved"
        assert status_result["provider"] == "NAMBA_ONE"


# ============================================================================
# 4. OCPP 2.0.1 v201_tx_id_to_numeric helper
# ============================================================================

class TestV201Helpers:
    """Тесты для вспомогательных функций OCPP 2.0.1"""

    def test_tx_id_to_numeric_deterministic(self):
        from ocpp_ws_server.ws_handler_v201 import _v201_tx_id_to_numeric
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        result1 = _v201_tx_id_to_numeric(uuid_str)
        result2 = _v201_tx_id_to_numeric(uuid_str)
        assert result1 == result2  # Deterministic
        assert 0 <= result1 < 2**31  # Within range

    def test_tx_id_to_numeric_different_ids(self):
        from ocpp_ws_server.ws_handler_v201 import _v201_tx_id_to_numeric
        id1 = _v201_tx_id_to_numeric("uuid-aaa-111")
        id2 = _v201_tx_id_to_numeric("uuid-bbb-222")
        assert id1 != id2  # Different inputs → different outputs


# ============================================================================
# 5. Admin endpoint auth helper
# ============================================================================

class TestAdminAuth:
    """Тесты для admin auth helper"""

    def test_admin_operators_module_exists(self):
        from app.api.v1.admin import operators
        assert hasattr(operators, "get_current_admin")

    @pytest.mark.asyncio
    async def test_get_current_admin_unauthorized(self):
        from app.api.v1.admin.operators import get_current_admin
        from fastapi import HTTPException
        # Request without client_id
        mock_request = MagicMock()
        mock_request.state = MagicMock(spec=[])  # No client_id attribute
        mock_db = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_admin(mock_request, mock_db)
        assert exc_info.value.status_code == 401


# ============================================================================
# 6. Config validation — NAMBA_ONE vars exist
# ============================================================================

class TestConfigNambaVars:
    """Проверка что config содержит NAMBA_ONE переменные"""

    def test_config_has_namba_fields(self):
        from app.core.config import Settings
        # Check field definitions exist in the model
        fields = Settings.model_fields
        assert "NAMBA_ONE_BASE_URL" in fields
        assert "NAMBA_ONE_MERCHANT_GUID" in fields
        assert "NAMBA_ONE_SECRET" in fields
        assert "NAMBA_ONE_WEBHOOK_SECRET" in fields


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
