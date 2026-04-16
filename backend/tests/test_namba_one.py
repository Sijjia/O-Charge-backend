"""
Tests for Namba One Payment Service — Real API integration

Тесты покрывают:
1. HMAC-SHA512 подпись (корректность генерации)
2. Конвертация KGS ↔ тийины
3. create_payment (mock mode)
4. check_payment_status (mock mode)
5. Webhook parsing и verification
6. Маппинг статусов Namba One → внутренние
7. Refund flow (mock)
"""
import pytest
import hashlib
import hmac
import base64
from decimal import Decimal
from unittest.mock import patch, AsyncMock

from app.services.namba_service import (
    NambaOneService,
    kgs_to_tiyins,
    tiyins_to_kgs,
)


# ============================================================================
# 1. Конвертация валют
# ============================================================================

class TestCurrencyConversion:
    """Тесты конвертации KGS ↔ тийины"""

    def test_kgs_to_tiyins_integer(self):
        assert kgs_to_tiyins(Decimal("100")) == "10000"

    def test_kgs_to_tiyins_decimal(self):
        assert kgs_to_tiyins(Decimal("50.50")) == "5050"

    def test_kgs_to_tiyins_small(self):
        assert kgs_to_tiyins(Decimal("1")) == "100"

    def test_kgs_to_tiyins_large(self):
        assert kgs_to_tiyins(Decimal("10000")) == "1000000"

    def test_tiyins_to_kgs_string(self):
        assert tiyins_to_kgs("10000") == 100.0

    def test_tiyins_to_kgs_int(self):
        assert tiyins_to_kgs(5050) == 50.5

    def test_tiyins_to_kgs_small(self):
        assert tiyins_to_kgs("100") == 1.0

    def test_roundtrip(self):
        """KGS → тийины → KGS должно давать то же значение"""
        original = Decimal("250.50")
        tiyins = kgs_to_tiyins(original)
        back = tiyins_to_kgs(tiyins)
        assert back == float(original)


# ============================================================================
# 2. HMAC-SHA512 подпись
# ============================================================================

class TestHmacSignature:
    """Тесты HMAC-SHA512 подписи"""

    def setup_method(self):
        self.service = NambaOneService()
        # Принудительно задаём secret для тестов
        self.service.secret = "test-secret-key-12345"

    def test_sign_basic(self):
        """Подпись должна быть валидным Base64"""
        signature = self.service._sign("/test/path", '{"key":"value"}', "salt123")
        # Должна быть Base64 строкой
        decoded = base64.b64decode(signature)
        assert len(decoded) == 64  # SHA-512 = 64 bytes

    def test_sign_deterministic(self):
        """Одинаковые входные данные → одинаковая подпись"""
        sig1 = self.service._sign("/path", '{"a":1}', "salt")
        sig2 = self.service._sign("/path", '{"a":1}', "salt")
        assert sig1 == sig2

    def test_sign_different_path(self):
        """Разные пути → разные подписи"""
        sig1 = self.service._sign("/path1", '{"a":1}', "salt")
        sig2 = self.service._sign("/path2", '{"a":1}', "salt")
        assert sig1 != sig2

    def test_sign_different_body(self):
        """Разные body → разные подписи"""
        sig1 = self.service._sign("/path", '{"a":1}', "salt")
        sig2 = self.service._sign("/path", '{"a":2}', "salt")
        assert sig1 != sig2

    def test_sign_different_salt(self):
        """Разные salt → разные подписи"""
        sig1 = self.service._sign("/path", '{"a":1}', "salt1")
        sig2 = self.service._sign("/path", '{"a":1}', "salt2")
        assert sig1 != sig2

    def test_sign_empty_body_for_get(self):
        """GET запросы: body = пустая строка"""
        signature = self.service._sign("/path", "", "salt")
        assert signature  # Не пустая

    def test_sign_matches_manual_computation(self):
        """Проверка что подпись совпадает с ручным вычислением"""
        path = "/public/merchant/payment/v1/test-guid/one-time"
        body = '{"externalId":"123"}'
        salt = "test-salt-uuid"

        message = path + body + salt
        expected = hmac.new(
            b"test-secret-key-12345",
            message.encode("utf-8"),
            hashlib.sha512,
        )
        expected_sig = base64.b64encode(expected.digest()).decode("utf-8")

        actual_sig = self.service._sign(path, body, salt)
        assert actual_sig == expected_sig

    def test_build_headers(self):
        """_build_headers возвращает все необходимые заголовки"""
        headers = self.service._build_headers("/path", '{"test":true}')
        assert "Content-Type" in headers
        assert headers["Content-Type"] == "application/json"
        assert "x-merchant-api-salt" in headers
        assert "x-merchant-api-signature" in headers
        assert len(headers["x-merchant-api-salt"]) >= 6  # UUID hex = 32 chars

    def test_build_headers_unique_salt(self):
        """Каждый вызов _build_headers генерирует уникальный salt"""
        h1 = self.service._build_headers("/path", "body")
        h2 = self.service._build_headers("/path", "body")
        assert h1["x-merchant-api-salt"] != h2["x-merchant-api-salt"]


# ============================================================================
# 3. Mock create_payment
# ============================================================================

class TestMockCreatePayment:
    """Тесты создания платежа в mock режиме"""

    def setup_method(self):
        self.service = NambaOneService()
        assert self.service.mock_mode is True

    @pytest.mark.asyncio
    async def test_create_payment_success(self):
        result = await self.service.create_payment(
            amount=Decimal("500"),
            order_id="test-order-001",
            webhook_url="https://example.com/webhook",
        )
        assert result["success"] is True
        assert result["provider"] == "NAMBA_ONE"
        assert result["invoice_id"] == "test-order-001"
        assert result["status"] == "approved"  # Mock: сразу approved
        assert result["mock"] is True
        assert "payment_url" in result
        assert "link_guid" in result

    @pytest.mark.asyncio
    async def test_create_payment_url_contains_amount(self):
        result = await self.service.create_payment(
            amount=Decimal("100"),
            order_id="test-002",
            webhook_url="https://example.com/webhook",
        )
        # URL должен содержать сумму в тийинах
        assert "10000" in result["payment_url"]

    @pytest.mark.asyncio
    async def test_create_payment_stores_in_mock(self):
        order_id = "test-store-003"
        await self.service.create_payment(
            amount=Decimal("250"),
            order_id=order_id,
            webhook_url="https://example.com/webhook",
        )
        assert order_id in self.service._mock_payments
        assert self.service._mock_payments[order_id]["amount"] == 250.0


# ============================================================================
# 4. Mock check_payment_status
# ============================================================================

class TestMockCheckStatus:
    """Тесты проверки статуса в mock режиме"""

    def setup_method(self):
        self.service = NambaOneService()

    @pytest.mark.asyncio
    async def test_check_existing_payment(self):
        await self.service.create_payment(
            amount=Decimal("300"),
            order_id="check-001",
            webhook_url="https://example.com/webhook",
        )
        result = await self.service.check_payment_status("check-001")
        assert result["success"] is True
        assert result["status"] == "approved"
        assert result["numeric_status"] == 1
        assert result["paid_amount"] == 300.0
        assert result["mock"] is True

    @pytest.mark.asyncio
    async def test_check_unknown_payment(self):
        """Неизвестный ID → approved в mock режиме"""
        result = await self.service.check_payment_status("nonexistent-id")
        assert result["success"] is True
        assert result["status"] == "approved"
        assert result["numeric_status"] == 1
        assert result["mock"] is True


# ============================================================================
# 5. Webhook parsing
# ============================================================================

class TestWebhookParsing:
    """Тесты парсинга webhook payload от Namba One"""

    def setup_method(self):
        self.service = NambaOneService()

    def test_parse_payment_completed(self):
        payload = {
            "createdAt": "2026-02-15T10:00:00.000Z",
            "type": "PAYMENT_ORDER",
            "version": "1",
            "data": {
                "guid": "payment-guid-123",
                "externalId": "order-001",
                "channel": "BANK_CARD",
                "paymentLinkGuid": "link-guid-123",
                "merchantAccountGuid": "merchant-guid",
                "currency": "KGS",
                "amount": "50000",
                "paymentAmount": "50000",
                "refundAmount": "0",
                "status": "COMPLETED",
                "maskedPhoneNumber": "+996***456",
                "createdAt": "2026-02-15T10:00:00.000Z",
                "settledAt": "2026-02-15T10:00:05.000Z",
            },
        }
        result = self.service.parse_webhook(payload)
        assert result["type"] == "payment"
        assert result["order_id"] == "order-001"
        assert result["order_guid"] == "payment-guid-123"
        assert result["status"] == "approved"
        assert result["numeric_status"] == 1
        assert result["amount"] == 500.0  # 50000 тийинов = 500 KGS
        assert result["channel"] == "BANK_CARD"

    def test_parse_payment_failed(self):
        payload = {
            "type": "PAYMENT_ORDER",
            "data": {
                "guid": "guid-456",
                "externalId": "order-002",
                "status": "FAILED",
                "amount": "10000",
            },
        }
        result = self.service.parse_webhook(payload)
        assert result["status"] == "canceled"
        assert result["numeric_status"] == 2

    def test_parse_payment_canceled(self):
        payload = {
            "type": "PAYMENT_ORDER",
            "data": {
                "guid": "guid-789",
                "externalId": "order-003",
                "status": "CANCELED",
                "amount": "20000",
            },
        }
        result = self.service.parse_webhook(payload)
        assert result["status"] == "canceled"
        assert result["numeric_status"] == 2

    def test_parse_payment_expired(self):
        payload = {
            "type": "PAYMENT_ORDER",
            "data": {
                "guid": "guid-exp",
                "externalId": "order-004",
                "status": "EXPIRED",
                "amount": "30000",
            },
        }
        result = self.service.parse_webhook(payload)
        assert result["status"] == "canceled"
        assert result["numeric_status"] == 2

    def test_parse_payment_processing(self):
        payload = {
            "type": "PAYMENT_ORDER",
            "data": {
                "guid": "guid-proc",
                "externalId": "order-005",
                "status": "PROCESSING",
                "amount": "15000",
            },
        }
        result = self.service.parse_webhook(payload)
        assert result["status"] == "processing"
        assert result["numeric_status"] == 0

    def test_parse_refund_completed(self):
        payload = {
            "type": "REFUND_ORDER",
            "data": {
                "guid": "refund-guid-123",
                "externalGuid": "ext-refund-001",
                "parentPaymentGuid": "payment-guid-123",
                "currency": "KGS",
                "amount": "25000",
                "status": "COMPLETED",
            },
        }
        result = self.service.parse_webhook(payload)
        assert result["type"] == "refund"
        assert result["refund_id"] == "ext-refund-001"
        assert result["status"] == "approved"
        assert result["numeric_status"] == 3
        assert result["amount"] == 250.0

    def test_parse_refund_failed(self):
        payload = {
            "type": "REFUND_ORDER",
            "data": {
                "guid": "refund-guid-456",
                "externalGuid": "ext-refund-002",
                "status": "FAILED",
                "amount": "10000",
            },
        }
        result = self.service.parse_webhook(payload)
        assert result["type"] == "refund"
        assert result["status"] == "canceled"
        assert result["numeric_status"] == 2

    def test_parse_unknown_type(self):
        payload = {"type": "SOMETHING_ELSE", "data": {"foo": "bar"}}
        result = self.service.parse_webhook(payload)
        assert result["type"] == "unknown"


# ============================================================================
# 6. Webhook verification
# ============================================================================

class TestWebhookVerification:
    """Тесты верификации webhook через custom headers"""

    def test_verify_with_correct_secret(self):
        service = NambaOneService()
        service.webhook_secret = "my-webhook-secret-123"
        headers = {"x-rp-webhook-secret": "my-webhook-secret-123"}
        assert service.verify_webhook(headers) is True

    def test_verify_with_wrong_secret(self):
        service = NambaOneService()
        service.webhook_secret = "my-webhook-secret-123"
        headers = {"x-rp-webhook-secret": "wrong-secret"}
        assert service.verify_webhook(headers) is False

    def test_verify_without_secret_header(self):
        service = NambaOneService()
        service.webhook_secret = "my-webhook-secret-123"
        headers = {}
        assert service.verify_webhook(headers) is False

    def test_verify_no_secret_configured(self):
        """Если webhook_secret не настроен — пропускаем верификацию"""
        service = NambaOneService()
        service.webhook_secret = ""
        headers = {}
        assert service.verify_webhook(headers) is True


# ============================================================================
# 7. Маппинг статусов
# ============================================================================

class TestStatusMapping:
    """Тесты маппинга статусов Namba One → внутренние"""

    def test_completed_to_approved(self):
        result = NambaOneService._map_status("COMPLETED")
        assert result["status"] == "approved"
        assert result["numeric"] == 1

    def test_canceled_to_canceled(self):
        result = NambaOneService._map_status("CANCELED")
        assert result["status"] == "canceled"
        assert result["numeric"] == 2

    def test_failed_to_canceled(self):
        result = NambaOneService._map_status("FAILED")
        assert result["status"] == "canceled"
        assert result["numeric"] == 2

    def test_expired_to_canceled(self):
        result = NambaOneService._map_status("EXPIRED")
        assert result["status"] == "canceled"
        assert result["numeric"] == 2

    def test_processing_statuses(self):
        """Все промежуточные статусы → processing"""
        for status in ["CREATED", "PAYER_DEBIT", "PAYER_DEBIT_SUCCESSFUL", "PROCESSING"]:
            result = NambaOneService._map_status(status)
            assert result["status"] == "processing", f"Failed for {status}"
            assert result["numeric"] == 0

    def test_refunded(self):
        result = NambaOneService._map_status("REFUNDED")
        assert result["status"] == "refunded"
        assert result["numeric"] == 3

    def test_unknown_status(self):
        result = NambaOneService._map_status("SOMETHING_NEW")
        assert result["status"] == "processing"
        assert result["numeric"] == 0


# ============================================================================
# 8. Mock refund
# ============================================================================

class TestMockRefund:
    """Тесты refund в mock режиме"""

    def setup_method(self):
        self.service = NambaOneService()

    @pytest.mark.asyncio
    async def test_refund_mock(self):
        result = await self.service.create_refund(
            payment_order_guid="payment-guid-123",
            amount=Decimal("100"),
            ext_refund_id="refund-001",
            webhook_url="https://example.com/webhook",
        )
        assert result["success"] is True
        assert result["mock"] is True
        assert result["provider"] == "NAMBA_ONE"


# ============================================================================
# 9. Payment path building
# ============================================================================

class TestPaymentPath:
    """Тесты построения URL путей"""

    def setup_method(self):
        self.service = NambaOneService()
        self.service.merchant_guid = "test-merchant-guid-1234"

    def test_one_time_path(self):
        path = self.service._payment_path("one-time")
        assert path == "/public/merchant/payment/v1/test-merchant-guid-1234/one-time"

    def test_refund_path(self):
        path = self.service._payment_path("refund/ext-ref-001")
        assert path == "/public/merchant/payment/v1/test-merchant-guid-1234/refund/ext-ref-001"

    def test_status_path(self):
        path = self.service._payment_path("one-time/order-123")
        assert path == "/public/merchant/payment/v1/test-merchant-guid-1234/one-time/order-123"
