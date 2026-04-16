"""
Namba One Payment Service — Real API Integration

Интеграция с Namba One Merchant Web API (merchant-api-docs.rps.kg).

Механизм:
- Создаём одноразовую ссылку на оплату (one-time payment link)
- Клиент оплачивает через приложение Namba One
- Получаем webhook с финальным статусом (COMPLETED/CANCELED/FAILED/EXPIRED)
- Можем проверить статус вручную через GET endpoint

Аутентификация: HMAC-SHA512 (path + body + salt)
Суммы: в тийинах (1 KGS = 100 тийинов), передаются строками
"""
import hashlib
import hmac
import base64
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any, Optional
from uuid import uuid4

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def kgs_to_tiyins(amount: Decimal) -> str:
    """Конвертация KGS → тийины (строка). 1 KGS = 100 тийинов."""
    return str(int(amount * 100))


def tiyins_to_kgs(tiyins) -> float:
    """Конвертация тийины → KGS (float)."""
    return int(tiyins) / 100


class NambaOneService:
    """Сервис для работы с Namba One Merchant Web API"""

    def __init__(self):
        self.base_url = settings.NAMBA_ONE_BASE_URL.rstrip("/")
        self.merchant_guid = settings.NAMBA_ONE_MERCHANT_GUID
        self.secret = settings.NAMBA_ONE_SECRET
        self.webhook_secret = settings.NAMBA_ONE_WEBHOOK_SECRET

        # Mock режим если нет credentials
        self.mock_mode = not self.merchant_guid or not self.secret
        if self.mock_mode:
            logger.info("NambaOneService: MOCK режим (нет NAMBA_ONE_MERCHANT_GUID / NAMBA_ONE_SECRET)")
            self._mock_payments: Dict[str, Dict] = {}
        else:
            logger.info(f"NambaOneService: PRODUCTION режим, merchant={self.merchant_guid[:8]}...")

    # ==================== HMAC-SHA512 подпись ====================

    def _sign(self, path: str, body: str, salt: str) -> str:
        """
        Генерация HMAC-SHA512 подписи.

        message = URI_PATH + JSON_BODY + SALT
        Для GET запросов body = "" (пустая строка).
        """
        message = path + body + salt
        signer = hmac.new(
            self.secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha512,
        )
        return base64.b64encode(signer.digest()).decode("utf-8")

    def _build_headers(self, path: str, body: str = "") -> Dict[str, str]:
        """Построить заголовки запроса с подписью."""
        salt = uuid4().hex
        signature = self._sign(path, body, salt)
        return {
            "Content-Type": "application/json",
            "x-merchant-api-salt": salt,
            "x-merchant-api-signature": signature,
        }

    def _payment_path(self, suffix: str) -> str:
        """Базовый путь: /public/merchant/payment/v1/{merchantAccountGuid}/{suffix}"""
        return f"/public/merchant/payment/v1/{self.merchant_guid}/{suffix}"

    # ==================== Основные методы ====================

    async def create_payment(
        self,
        amount: Decimal,
        order_id: str,
        webhook_url: str,
        description: str = "Пополнение баланса Red Petroleum EV",
        phone: Optional[str] = None,
        expire_minutes: int = 30,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Создать одноразовую ссылку на оплату.

        Args:
            amount: Сумма в KGS (сомах)
            order_id: Уникальный externalId (наш internal ID)
            webhook_url: URL для webhook уведомлений
            description: Описание платежа
            phone: Телефон клиента (для справки)
            expire_minutes: Время жизни ссылки

        Returns:
            {"success": True, "payment_url": "...", "invoice_id": "...", "provider": "NAMBA_ONE"}
        """
        if self.mock_mode:
            return await self._mock_create_payment(amount, order_id, description)

        path = self._payment_path("one-time")
        body_dict: Dict[str, Any] = {
            "externalId": order_id,
            "webhookUrl": webhook_url,
            "amount": kgs_to_tiyins(amount),
            "amountCanBeChanged": False,
            "comment": description,
        }

        # Добавляем custom webhook headers для верификации
        if self.webhook_secret:
            body_dict["webhookHeaders"] = {
                "X-RP-Webhook-Secret": self.webhook_secret,
            }

        import json
        body_json = json.dumps(body_dict, ensure_ascii=False)
        headers = self._build_headers(path, body_json)
        url = self.base_url + path

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, content=body_json, headers=headers)
                data = response.json()

            if data.get("status") == "OK":
                token_data = data["data"]["token"]
                payment_url = token_data.get("token", "")
                link_guid = token_data.get("guid", "")

                logger.info(
                    f"[NambaOne] Payment link created: externalId={order_id}, "
                    f"guid={link_guid}, amount={amount} KGS"
                )
                return {
                    "success": True,
                    "payment_url": payment_url,
                    "invoice_id": order_id,  # externalId = наш order_id
                    "link_guid": link_guid,
                    "provider": "NAMBA_ONE",
                    "status": "pending",
                }
            else:
                error = data.get("error", {})
                error_code = error.get("errorCode", "UNKNOWN")
                logger.error(f"[NambaOne] Payment creation failed: {error_code} — {data}")
                return {
                    "success": False,
                    "error": error_code,
                    "message": error.get("message", "Payment creation failed"),
                    "provider": "NAMBA_ONE",
                }

        except httpx.TimeoutException:
            logger.error(f"[NambaOne] Timeout creating payment for order {order_id}", exc_info=True)
            return {"success": False, "error": "timeout", "provider": "NAMBA_ONE"}
        except Exception as e:
            logger.error(f"[NambaOne] Error creating payment: {e}", exc_info=True)
            return {"success": False, "error": str(e), "provider": "NAMBA_ONE"}

    async def check_payment_status(
        self, invoice_id: str, order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Проверить статус платежа по externalId.

        Args:
            invoice_id: externalId (= order_id, переданный при создании)
            order_id: Алиас для invoice_id (обратная совместимость)

        Returns:
            {"success": True, "status": "approved"|"processing"|"canceled", ...}
        """
        ext_id = invoice_id or order_id
        if self.mock_mode:
            return await self._mock_check_status(ext_id)

        path = self._payment_path(f"one-time/{ext_id}")
        headers = self._build_headers(path, "")
        url = self.base_url + path

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, headers=headers)
                data = response.json()

            if data.get("status") == "OK" and data.get("data"):
                order_data = data["data"]
                namba_status = order_data.get("status", "CREATED")
                mapped = self._map_status(namba_status)
                paid_amount = tiyins_to_kgs(order_data["paymentAmount"]) if order_data.get("paymentAmount") else None

                return {
                    "success": True,
                    "status": mapped["status"],
                    "numeric_status": mapped["numeric"],
                    "paid_amount": paid_amount,
                    "namba_status": namba_status,
                    "provider": "NAMBA_ONE",
                }
            else:
                # Нет данных — платёж ещё не оплачен
                return {
                    "success": True,
                    "status": "processing",
                    "numeric_status": 0,
                    "paid_amount": None,
                    "provider": "NAMBA_ONE",
                }

        except Exception as e:
            logger.error(f"[NambaOne] Error checking status for {ext_id}: {e}", exc_info=True)
            return {
                "success": False,
                "status": "processing",
                "numeric_status": 0,
                "error": str(e),
                "provider": "NAMBA_ONE",
            }

    async def create_refund(
        self,
        payment_order_guid: str,
        amount: Decimal,
        ext_refund_id: str,
        webhook_url: str,
        comment: str = "Возврат средств",
    ) -> Dict[str, Any]:
        """
        Создать возврат средств.

        Args:
            payment_order_guid: GUID исходного платежа (из webhook data.guid)
            amount: Сумма возврата в KGS
            ext_refund_id: Наш уникальный ID возврата
            webhook_url: URL для webhook о статусе возврата
            comment: Причина возврата
        """
        if self.mock_mode:
            return {"success": True, "status": "COMPLETED", "mock": True, "provider": "NAMBA_ONE"}

        path = self._payment_path(f"refund/{ext_refund_id}")
        body_dict = {
            "parentType": "PAYMENT_MERCHANT",
            "paymentOrderGuid": payment_order_guid,
            "webhookUrl": webhook_url,
            "amount": kgs_to_tiyins(amount),
            "comment": comment,
        }

        if self.webhook_secret:
            body_dict["webhookHeaders"] = {"X-RP-Webhook-Secret": self.webhook_secret}

        import json
        body_json = json.dumps(body_dict, ensure_ascii=False)
        headers = self._build_headers(path, body_json)
        url = self.base_url + path

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, content=body_json, headers=headers)
                data = response.json()

            if data.get("status") == "OK":
                refund_data = data["data"]
                logger.info(f"[NambaOne] Refund created: {ext_refund_id}, amount={amount} KGS")
                return {
                    "success": True,
                    "refund_guid": refund_data.get("guid"),
                    "status": refund_data.get("status", "CREATED"),
                    "provider": "NAMBA_ONE",
                }
            else:
                error = data.get("error", {})
                logger.error(f"[NambaOne] Refund failed: {error}")
                return {
                    "success": False,
                    "error": error.get("errorCode", "UNKNOWN"),
                    "message": error.get("message", "Refund failed"),
                    "provider": "NAMBA_ONE",
                }

        except Exception as e:
            logger.error(f"[NambaOne] Refund error: {e}", exc_info=True)
            return {"success": False, "error": str(e), "provider": "NAMBA_ONE"}

    async def get_merchant_info(self) -> Dict[str, Any]:
        """Получить информацию о мерчанте (баланс, статус)."""
        if self.mock_mode:
            return {"success": True, "balance": 0, "status": "AVAILABLE", "mock": True}

        path = self._payment_path("").rstrip("/")
        # Путь без суффикса: /public/merchant/payment/v1/{guid}
        path = f"/public/merchant/payment/v1/{self.merchant_guid}"
        headers = self._build_headers(path, "")
        url = self.base_url + path

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, headers=headers)
                data = response.json()

            if data.get("status") == "OK":
                return {"success": True, "data": data["data"], "provider": "NAMBA_ONE"}
            return {"success": False, "error": data, "provider": "NAMBA_ONE"}
        except Exception as e:
            logger.error(f"[NambaOne] Get merchant info error: {e}", exc_info=True)
            return {"success": False, "error": str(e), "provider": "NAMBA_ONE"}

    # ==================== Webhook обработка ====================

    def verify_webhook(self, headers: Dict[str, str]) -> bool:
        """
        Верифицировать входящий webhook от Namba One.

        Namba One не подписывает webhooks — вместо этого мы передаём
        webhookHeaders при создании ссылки, и Namba One отправляет их обратно.
        Проверяем наш секрет в заголовке X-RP-Webhook-Secret.
        """
        if not self.webhook_secret:
            logger.warning("[NambaOne] Webhook secret не настроен — пропускаем верификацию")
            return True

        received_secret = headers.get("x-rp-webhook-secret", "")
        if hmac.compare_digest(received_secret, self.webhook_secret):
            return True

        logger.warning("[NambaOne] Webhook verification failed: invalid secret")
        return False

    def parse_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Распарсить webhook payload от Namba One.

        Payment webhook:
        {
            "type": "PAYMENT_ORDER",
            "data": {
                "externalId": "...",
                "status": "COMPLETED",
                "amount": "10000",  // тийины
                "paymentAmount": "10000",
                "guid": "...",
                ...
            }
        }

        Refund webhook:
        {
            "type": "REFUND_ORDER",
            "data": {
                "externalGuid": "...",
                "status": "COMPLETED",
                "amount": "5000",
                ...
            }
        }
        """
        webhook_type = payload.get("type", "")
        data = payload.get("data", {})

        if webhook_type == "PAYMENT_ORDER":
            namba_status = data.get("status", "CREATED")
            mapped = self._map_status(namba_status)
            paid_tiyins = data.get("paymentAmount") or data.get("amount")
            paid_amount = tiyins_to_kgs(paid_tiyins) if paid_tiyins else None

            return {
                "type": "payment",
                "order_id": data.get("externalId", ""),
                "order_guid": data.get("guid", ""),
                "status": mapped["status"],
                "numeric_status": mapped["numeric"],
                "namba_status": namba_status,
                "amount": paid_amount,
                "channel": data.get("channel", ""),  # BANK_CARD | BALANCE
                "customer_phone": data.get("maskedPhoneNumber", ""),
            }

        elif webhook_type == "REFUND_ORDER":
            refund_status = data.get("status", "CREATED")
            return {
                "type": "refund",
                "refund_id": data.get("externalGuid", ""),
                "refund_guid": data.get("guid", ""),
                "parent_payment_guid": data.get("parentPaymentGuid", ""),
                "status": "approved" if refund_status == "COMPLETED" else "canceled",
                "numeric_status": 3 if refund_status == "COMPLETED" else 2,
                "amount": tiyins_to_kgs(data["amount"]) if data.get("amount") else None,
            }

        else:
            logger.warning(f"[NambaOne] Unknown webhook type: {webhook_type}")
            return {"type": "unknown", "raw": payload}

    # ==================== Маппинг статусов ====================

    @staticmethod
    def _map_status(namba_status: str) -> Dict[str, Any]:
        """Маппинг статусов Namba One → наши внутренние."""
        mapping = {
            "CREATED": {"status": "processing", "numeric": 0},
            "PAYER_DEBIT": {"status": "processing", "numeric": 0},
            "PAYER_DEBIT_SUCCESSFUL": {"status": "processing", "numeric": 0},
            "PROCESSING": {"status": "processing", "numeric": 0},
            "COMPLETED": {"status": "approved", "numeric": 1},
            "CANCELED": {"status": "canceled", "numeric": 2},
            "REFUNDED": {"status": "refunded", "numeric": 3},
            "FAILED": {"status": "canceled", "numeric": 2},
            "EXPIRED": {"status": "canceled", "numeric": 2},
            "CANCELLATION_ATTEMPTED": {"status": "processing", "numeric": 0},
            "CANCELLATION_FAILED": {"status": "processing", "numeric": 0},
        }
        return mapping.get(namba_status, {"status": "processing", "numeric": 0})

    # ==================== MOCK режим ====================

    async def _mock_create_payment(self, amount: Decimal, order_id: str, description: str) -> Dict[str, Any]:
        """Mock: создаёт фейковый платёж, автоматически помечается как approved."""
        invoice_id = order_id
        mock_guid = f"mock-guid-{uuid4().hex[:12]}"

        self._mock_payments[invoice_id] = {
            "order_id": order_id,
            "guid": mock_guid,
            "amount": float(amount),
            "status": "approved",
            "namba_status": "COMPLETED",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        mock_url = f"https://pay.rps.kg/mock/{mock_guid}?amount={kgs_to_tiyins(amount)}"
        logger.info(f"[NambaOne MOCK] Payment created: {invoice_id}, amount={amount} KGS")

        return {
            "success": True,
            "payment_url": mock_url,
            "invoice_id": invoice_id,
            "link_guid": mock_guid,
            "provider": "NAMBA_ONE",
            "status": "approved",
            "mock": True,
        }

    async def _mock_check_status(self, ext_id: str) -> Dict[str, Any]:
        """Mock: возвращает статус фейкового платежа (всегда approved)."""
        payment = self._mock_payments.get(ext_id)

        if payment:
            return {
                "success": True,
                "status": payment["status"],
                "numeric_status": 1 if payment["status"] == "approved" else 0,
                "paid_amount": payment["amount"],
                "namba_status": payment.get("namba_status", "COMPLETED"),
                "provider": "NAMBA_ONE",
                "mock": True,
            }

        return {
            "success": True,
            "status": "approved",
            "numeric_status": 1,
            "paid_amount": None,
            "namba_status": "COMPLETED",
            "provider": "NAMBA_ONE",
            "mock": True,
        }


# Singleton
namba_one_service = NambaOneService()
