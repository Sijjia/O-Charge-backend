"""
Outgoing Webhook Dispatcher — sends HTTP POST to registered URLs on charging events.

Events:
  - connector.status_changed
  - charging.started
  - charging.progress
  - charging.completed
  - charging.error
  - station.online
  - station.offline
"""
import asyncio
import hashlib
import hmac as hmac_mod
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, OperationalError

logger = logging.getLogger(__name__)

WEBHOOK_EVENTS = [
    "connector.status_changed",
    "charging.started",
    "charging.progress",
    "charging.completed",
    "charging.error",
    "station.online",
    "station.offline",
]


class WebhookService:
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self.max_retries = 3
        self.base_delay = 1.0
        # Throttle: (event_type, station_id) -> last_dispatch_ts
        self._throttle: Dict[str, float] = {}
        self._throttle_lock: Optional[asyncio.Lock] = None
        self.throttle_interval = 30.0  # seconds for charging.progress

    async def _get_lock(self) -> asyncio.Lock:
        """Lazy-init lock inside running event loop."""
        if self._throttle_lock is None:
            self._throttle_lock = asyncio.Lock()
        return self._throttle_lock

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def dispatch_event(self, event_type: str, payload: dict):
        """Fire-and-forget dispatch. Works from sync context with running event loop."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._dispatch(event_type, payload))
        except RuntimeError:
            # No running loop (e.g., called from APScheduler thread) — schedule via new loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(self._dispatch(event_type, payload), loop)
                else:
                    logger.debug(f"No event loop for webhook dispatch: {event_type}")
            except Exception:
                logger.debug(f"Cannot dispatch webhook (no loop): {event_type}")

    async def _dispatch(self, event_type: str, payload: dict):
        """Fetch matching subscriptions and send to each."""
        try:
            # Throttle charging.progress (async-safe)
            if event_type == "charging.progress":
                key = f"{event_type}:{payload.get('station_id')}"
                now = time.time()
                lock = await self._get_lock()
                async with lock:
                    if key in self._throttle and (now - self._throttle[key]) < self.throttle_interval:
                        return
                    self._throttle[key] = now
                    # Cleanup stale entries to prevent memory leak
                    if len(self._throttle) > 500:
                        cutoff = now - (self.throttle_interval * 3)
                        self._throttle = {k: v for k, v in self._throttle.items() if v > cutoff}

            from app.db.session import get_db
            db = next(get_db())
            try:
                subs = db.execute(text("""
                    SELECT id, url, secret FROM webhook_subscriptions
                    WHERE is_active = true AND :event = ANY(events)
                """), {"event": event_type}).fetchall()
            except (ProgrammingError, OperationalError) as e:
                # Table doesn't exist yet (pre-migration) or DB error
                logger.debug(f"Webhook table not ready, skipping: {e}")
                return
            finally:
                try:
                    db.close()
                except Exception:
                    pass

            if not subs:
                return

            for sub in subs:
                asyncio.create_task(
                    self._deliver(sub.id, sub.url, sub.secret, event_type, payload)
                )
        except Exception as e:
            logger.error(f"Webhook dispatch error ({event_type}): {e}")

    async def _deliver(self, sub_id: str, url: str, secret: str, event_type: str, payload: dict):
        """Send POST with retries and log each attempt."""
        envelope = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }
        body = json.dumps(envelope, default=str, ensure_ascii=False)
        signature = hmac_mod.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": event_type,
            "X-Webhook-Signature-256": f"sha256={signature}",
        }
        client = await self._get_client()

        for attempt in range(1, self.max_retries + 1):
            status_code = None
            response_body = None
            error = None
            success = False
            try:
                resp = await client.post(url, content=body, headers=headers)
                status_code = resp.status_code
                response_body = resp.text[:500]
                success = 200 <= resp.status_code < 300
            except Exception as e:
                error = str(e)[:500]

            self._log(sub_id, event_type, envelope, status_code, response_body, attempt, success, error)

            if success:
                logger.info(f"Webhook delivered: {event_type} -> {url} (attempt {attempt})")
                return

            if attempt < self.max_retries:
                delay = self.base_delay * (5 ** (attempt - 1))
                await asyncio.sleep(delay)

        logger.warning(f"Webhook failed after {self.max_retries} attempts: {event_type} -> {url}")

    def _log(self, sub_id, event_type, payload, status_code, response_body, attempt, success, error=None):
        """Write to webhook_delivery_log."""
        try:
            from app.db.session import get_db
            db = next(get_db())
            try:
                db.execute(text("""
                    INSERT INTO webhook_delivery_log
                        (subscription_id, event_type, payload, status_code, response_body, attempt, success, error)
                    VALUES
                        (:sub_id, :event, CAST(:payload AS jsonb), :code, :resp, :attempt, :success, :error)
                """), {
                    "sub_id": sub_id,
                    "event": event_type,
                    "payload": json.dumps(payload, default=str, ensure_ascii=False),
                    "code": status_code,
                    "resp": response_body,
                    "attempt": attempt,
                    "success": success,
                    "error": error,
                })
                db.commit()
            finally:
                try:
                    db.close()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to log webhook delivery: {e}")

    async def send_test(self, url: str, secret: str) -> dict:
        """Send a test event to verify URL is reachable."""
        payload = {
            "event": "test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {"message": "This is a test webhook from Red Petroleum EV"},
        }
        body = json.dumps(payload, default=str, ensure_ascii=False)
        signature = hmac_mod.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": "test",
            "X-Webhook-Signature-256": f"sha256={signature}",
        }
        client = await self._get_client()
        try:
            resp = await client.post(url, content=body, headers=headers)
            return {
                "success": 200 <= resp.status_code < 300,
                "status_code": resp.status_code,
                "response": resp.text[:500],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


webhook_service = WebhookService()
