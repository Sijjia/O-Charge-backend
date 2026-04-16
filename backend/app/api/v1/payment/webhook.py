from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging
import time
import defusedxml.ElementTree as ET

from app.core.config import settings
from app.db.session import get_db
from app.services.payment_provider_service import get_payment_provider_service
from app.schemas.ocpp import PaymentWebhookData
from app.services.push_service import push_service
from app.api.v1.schemas.payment import WebhookResponse

router = APIRouter()
logger = logging.getLogger(__name__)

# Rate limiting for webhook endpoint: max 30 requests per minute per IP
_webhook_rate: dict[str, list[float]] = defaultdict(list)


def _webhook_rate_check(client_ip: str) -> bool:
    now = time.time()
    _webhook_rate[client_ip] = [t for t in _webhook_rate[client_ip] if now - t < 60]
    if len(_webhook_rate[client_ip]) >= 30:
        return False
    _webhook_rate[client_ip].append(now)
    return True

def verify_webhook_ip(client_ip: str, provider: str) -> bool:
    """
    Проверка IP адреса webhook запроса по whitelist

    Args:
        client_ip: IP адрес клиента
        provider: Название провайдера (OBANK/ODENGI)

    Returns:
        bool: True если IP в whitelist или проверка отключена
    """
    if not settings.WEBHOOK_IP_WHITELIST_ENABLED:
        if settings.is_production:
            logger.critical(f"WEBHOOK_IP_WHITELIST_ENABLED is False in production — rejecting {client_ip}")
            return False
        logger.warning(f"Webhook IP whitelist disabled - accepting from {client_ip}")
        return True

    # Получаем whitelist для провайдера
    if provider == "OBANK":
        whitelist_str = settings.OBANK_WEBHOOK_IPS
    elif provider == "NAMBA_ONE":
        whitelist_str = settings.NAMBA_ONE_WEBHOOK_IPS
    else:  # ODENGI
        whitelist_str = settings.ODENGI_WEBHOOK_IPS

    if not whitelist_str:
        # В production должен быть whitelist
        if settings.is_production:
            logger.error(f"❌ Webhook IP whitelist not configured for {provider} in production")
            return False
        else:
            logger.warning(f"⚠️ Webhook IP whitelist not configured for {provider} - accepting in development")
            return True

    # Парсим whitelist
    allowed_ips = [ip.strip() for ip in whitelist_str.split(",") if ip.strip()]

    if client_ip in allowed_ips:
        logger.info(f"✅ Webhook IP {client_ip} verified for {provider}")
        return True
    else:
        logger.warning(f"⚠️ Webhook from unauthorized IP {client_ip} for {provider}. Allowed: {allowed_ips}")
        return False

async def process_balance_topup(topup_id: int, client_id: str, amount: float, invoice_id: str, provider_name: str):
    """
    Фоновая обработка пополнения баланса с защитой от race conditions.

    Использует SELECT FOR UPDATE для блокировки строки клиента на время транзакции,
    предотвращая race conditions при одновременных webhook запросах.
    """
    from app.db.session import get_session_local
    SessionLocal = get_session_local()
    db = SessionLocal()

    try:
        # Начинаем транзакцию с SERIALIZABLE isolation для критических операций с балансом
        db.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))

        # Проверяем, не был ли платёж уже обработан (идемпотентность)
        topup_check = db.execute(text("""
            SELECT status FROM balance_topups WHERE id = :topup_id FOR UPDATE
        """), {"topup_id": topup_id})

        topup_row = topup_check.fetchone()
        if not topup_row:
            logger.error(f"Topup {topup_id} not found during processing")
            return

        if topup_row[0] == 'approved':
            logger.info(f"⚠️ Topup {topup_id} already processed (idempotency check)")
            return

        # Обновляем статус платежа
        db.execute(text("""
            UPDATE balance_topups
            SET status = 'approved', paid_amount = :amount, paid_at = NOW(),
                completed_at = NOW(), needs_status_check = false
            WHERE id = :topup_id AND status != 'approved'
        """), {"topup_id": topup_id, "amount": amount})

        # Получаем текущий баланс клиента С БЛОКИРОВКОЙ (FOR UPDATE)
        # Это предотвращает race conditions при одновременных транзакциях
        client_result = db.execute(text("""
            SELECT balance FROM clients WHERE id = :client_id FOR UPDATE
        """), {"client_id": client_id})

        client = client_result.fetchone()
        if not client:
            logger.error(f"Client {client_id} not found during balance topup")
            db.rollback()
            return

        old_balance = float(client[0])
        new_balance = old_balance + amount

        # Обновляем баланс клиента
        db.execute(text("""
            UPDATE clients SET balance = :new_balance WHERE id = :client_id
        """), {"new_balance": new_balance, "client_id": client_id})

        # Создаем запись о транзакции
        db.execute(text("""
            INSERT INTO payment_transactions_odengi
            (client_id, transaction_type, amount, balance_before, balance_after, description, balance_topup_id)
            VALUES (:client_id, 'balance_topup', :amount, :balance_before, :balance_after, :description, :topup_id)
        """), {
            "client_id": client_id,
            "amount": amount,
            "balance_before": old_balance,
            "balance_after": new_balance,
            "description": f"Пополнение баланса через {provider_name}, invoice {invoice_id}",
            "topup_id": topup_id
        })

        db.commit()
        logger.info(f"✅ Пополнение выполнено: {client_id}, +{amount} сом, баланс: {old_balance} → {new_balance}")

        # Push notification клиенту о пополнении баланса (graceful degradation)
        try:
            import asyncio
            asyncio.create_task(
                push_service.send_to_client(
                    db=db,
                    client_id=client_id,
                    event_type="payment_confirmed",
                    amount=amount,
                    new_balance=new_balance
                )
            )
            logger.info(f"Push notification scheduled for client {client_id} (payment confirmed)")
        except Exception as e:
            logger.warning(f"Failed to schedule push notification for payment: {e}")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Ошибка обработки пополнения {invoice_id}: {e}")
    finally:
        db.close()

@router.post("/payment/webhook", summary="Payment provider webhook", description="Receives payment status callbacks from O!Dengi. Validates HMAC signature.", response_model=WebhookResponse)
async def handle_payment_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    🔔 Обработка webhook уведомлений от платежных провайдеров

    Безопасность:
    - IP whitelist проверка
    - HMAC signature verification
    - Timestamp validation (защита от replay attacks)
    """
    try:
        # 1. Получение клиентского IP (с учётом reverse proxy)
        client_ip = (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-real-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )

        # 1.5 Rate limiting
        if not _webhook_rate_check(client_ip):
            logger.warning(f"⚠️ Webhook rate limited: {client_ip}")
            raise HTTPException(status_code=429, detail="Too many webhook requests")

        # 2. Определяем провайдера из характеристик запроса (а не из глобального конфига)
        content_type = request.headers.get("content-type", "")
        if request.headers.get("x-rp-webhook-secret"):
            provider_name = "NAMBA_ONE"
        elif "xml" in content_type.lower():
            provider_name = "OBANK"
        elif request.headers.get("x-o-dengi-signature"):
            provider_name = "ODENGI"
        else:
            # Fallback: глобальный конфиг
            provider_name = get_payment_provider_service().get_provider_name()

        # 3. IP Whitelist проверка (первый уровень защиты)
        if not verify_webhook_ip(client_ip, provider_name):
            logger.error(f"❌ Webhook rejected: IP {client_ip} not in whitelist for {provider_name}")
            raise HTTPException(status_code=403, detail="Forbidden: IP not whitelisted")

        # 4. Получение сырых данных (с лимитом размера)
        payload = await request.body()
        if len(payload) > 1_048_576:  # 1MB max
            logger.warning(f"⚠️ Webhook payload too large: {len(payload)} bytes from {client_ip}")
            raise HTTPException(status_code=413, detail="Payload too large")

        # 5. Timestamp validation - защита от replay attacks (второй уровень)
        ts_header = request.headers.get('X-Timestamp', '')
        try:
            ts = int(ts_header) if ts_header else 0
        except Exception:
            ts = 0

        if ts and abs(int(time.time()) - ts) > 300:  # 5 минут
            logger.warning(f"⚠️ Stale webhook from {client_ip}: timestamp drift > 5 minutes")
            raise HTTPException(status_code=400, detail="stale_timestamp")

        # 6. Signature verification (третий уровень защиты)
        if provider_name == "OBANK":
            # ⚠️ OBANK временно отключен
            if not settings.OBANK_ENABLED:
                logger.warning(f"OBANK webhook received while OBANK disabled (from {client_ip})")
                raise HTTPException(status_code=503, detail="OBANK temporarily disabled")

            # OBANK requires SSL client certificate verification
            # Until implemented, reject in production to prevent forged webhooks
            if settings.is_production:
                logger.error("❌ OBANK webhook rejected: SSL certificate verification not implemented in production")
                raise HTTPException(status_code=501, detail="OBANK webhook verification not implemented")

            logger.warning("⚠️ OBANK webhook: SSL client certificate verification not implemented (dev mode)")
            is_valid = True

        elif provider_name == "NAMBA_ONE":
            # Namba One: верификация через custom webhook headers (X-RP-Webhook-Secret)
            request_headers = dict(request.headers)
            is_valid = get_payment_provider_service().verify_webhook(
                payload, "", request_headers=request_headers
            )

        else:  # O!Dengi
            webhook_signature = request.headers.get('X-O-Dengi-Signature', '')

            if not webhook_signature:
                if settings.is_production:
                    logger.error(f"❌ Webhook rejected: Missing signature in production from {client_ip}")
                    raise HTTPException(status_code=401, detail="Missing webhook signature")
                else:
                    logger.warning(f"⚠️ Webhook without signature in development from {client_ip} — accepting with IP check only")

            if settings.is_production and not settings.ODENGI_WEBHOOK_SECRET:
                logger.critical("❌ ODENGI_WEBHOOK_SECRET not configured in production!")
                raise HTTPException(status_code=500, detail="Webhook verification not configured")

            # In dev without signature, skip verify_webhook but still require IP whitelist
            if webhook_signature:
                is_valid = get_payment_provider_service().verify_webhook(payload, webhook_signature)
            else:
                is_valid = not settings.is_production  # Only allow in dev

        if not is_valid:
            logger.error(f"❌ Invalid webhook signature from {client_ip} for {provider_name}")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        # 3. Парсинг данных в зависимости от провайдера
        if provider_name == "OBANK":
            # Для OBANK парсим XML
            root = ET.fromstring(payload.decode('utf-8'))

            invoice_id = root.find('.//invoice_id').text if root.find('.//invoice_id') is not None else None
            status = root.find('.//status').text if root.find('.//status') is not None else None
            amount = root.find('.//sum').text if root.find('.//sum') is not None else None

            status_mapping = {"completed": 1, "failed": 2, "cancelled": 2}
            numeric_status = status_mapping.get(status, 0)
            paid_amount = float(amount) / 1000 if amount and status == "completed" else None

        elif provider_name == "NAMBA_ONE":
            # Namba One: JSON с type + data
            import json
            from app.services.namba_service import namba_one_service
            webhook_payload = json.loads(payload.decode("utf-8"))
            parsed = namba_one_service.parse_webhook(webhook_payload)

            if parsed["type"] == "refund":
                logger.info(f"[NambaOne] Refund webhook: {parsed}")
                return {"status": "refund_received", "provider": "NAMBA_ONE"}

            invoice_id = parsed.get("order_id")
            numeric_status = parsed.get("numeric_status", 0)
            paid_amount = parsed.get("amount")

        else:  # O!Dengi
            webhook_data = PaymentWebhookData.parse_raw(payload)
            invoice_id = webhook_data.invoice_id
            numeric_status = webhook_data.status
            paid_amount = webhook_data.paid_amount / 100 if webhook_data.paid_amount else None
        
        # 4. Поиск платежа в базе
        topup_check = db.execute(text("""
            SELECT id, client_id, requested_amount, status, payment_provider FROM balance_topups 
            WHERE invoice_id = :invoice_id
        """), {"invoice_id": invoice_id})
        
        topup = topup_check.fetchone()
        
        if not topup:
            logger.warning(f"Payment not found for webhook: {invoice_id}")
            return {"status": "payment_not_found"}
        
        # 5. Проверяем что провайдер соответствует записи в БД
        if topup[4] != provider_name:
            logger.warning(f"Provider mismatch for payment {invoice_id}: expected {topup[4]}, got {provider_name}")
            return {"status": "provider_mismatch"}
        
        # 6. Обработка пополнения баланса
        if numeric_status == 1 and topup[3] != "approved":  # Оплачено
            background_tasks.add_task(
                process_balance_topup,
                topup[0],  # topup_id
                topup[1],  # client_id
                paid_amount if paid_amount else topup[2],  # amount
                invoice_id,
                provider_name
            )
        
        return {"status": "received", "invoice_id": invoice_id, "provider": provider_name}
        
    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is (rate limit, auth, etc.)
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        # Не раскрываем внутренние детали ошибки клиенту
        raise HTTPException(status_code=500, detail="Internal server error")