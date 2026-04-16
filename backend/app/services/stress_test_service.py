"""
Stress Test Service — симуляция нагрузки на собственный API
"""
import asyncio
import time
import random
import logging
import json
from typing import Optional

import httpx

from app.core.config import settings
from ocpp_ws_server.redis_manager import redis_manager

logger = logging.getLogger(__name__)

REDIS_STATUS_KEY = "stress_test:status"
REDIS_RESULTS_KEY = "stress_test:results"
REDIS_TTL = 3600  # 1 час


class StressTestService:
    """Сервис стресс-тестирования — симулирует параллельные HTTP-запросы к API"""

    BASE_URL = "http://127.0.0.1:9210"

    SCENARIOS = {
        "auth_flow": [
            ("GET", "/health"),
            ("GET", "/api/v1/locations"),
            ("GET", "/readyz"),
        ],
        "charging_flow": [
            ("GET", "/health"),
            ("GET", "/api/v1/locations"),
            ("GET", "/readyz"),
            ("GET", "/version"),
        ],
        "balance_check": [
            ("GET", "/health"),
            ("GET", "/api/v1/locations"),
        ],
        "mixed": [
            ("GET", "/health"),
            ("GET", "/api/v1/locations"),
            ("GET", "/readyz"),
            ("GET", "/version"),
            ("GET", "/health"),
        ],
    }

    async def get_status(self) -> dict:
        try:
            data = await redis_manager.redis.get(REDIS_STATUS_KEY)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"Redis get status failed: {e}")
        return {"running": False, "progress": 0.0}

    async def _set_status(self, running: bool, progress: float = 0.0, **kwargs) -> None:
        try:
            payload = {"running": running, "progress": progress, **kwargs}
            await redis_manager.redis.set(REDIS_STATUS_KEY, json.dumps(payload), ex=REDIS_TTL)
        except Exception as e:
            logger.warning(f"Redis set status failed: {e}")

    async def get_results(self) -> Optional[dict]:
        try:
            data = await redis_manager.redis.get(REDIS_RESULTS_KEY)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"Redis get results failed: {e}")
        return None

    async def _set_results(self, results: dict) -> None:
        try:
            await redis_manager.redis.set(REDIS_RESULTS_KEY, json.dumps(results), ex=REDIS_TTL)
        except Exception as e:
            logger.warning(f"Redis set results failed: {e}")

    async def run_test(
        self,
        concurrent_users: int,
        duration_seconds: int,
        scenario: str,
    ) -> None:
        """Запуск стресс-теста"""
        logger.info(f"🔥 Stress test START: {concurrent_users} users, {duration_seconds}s, scenario={scenario}")

        await self._set_status(
            running=True,
            progress=0.0,
            scenario=scenario,
            concurrent_users=concurrent_users,
        )

        endpoints = self.SCENARIOS.get(scenario, self.SCENARIOS["mixed"])
        latencies: list[float] = []
        errors = 0
        total = 0

        start_time = time.monotonic()
        end_time = start_time + duration_seconds

        # Используем semaphore для ограничения параллельности
        # Для больших чисел (>10000) ограничиваем реальную параллельность
        real_concurrency = min(concurrent_users, 500)
        semaphore = asyncio.Semaphore(real_concurrency)

        async def make_request(client: httpx.AsyncClient) -> None:
            nonlocal errors, total
            method, path = random.choice(endpoints)
            url = f"{self.BASE_URL}{path}"
            req_start = time.monotonic()

            async with semaphore:
                try:
                    if method == "GET":
                        resp = await client.get(url, timeout=10.0)
                    else:
                        resp = await client.post(url, timeout=10.0)
                    latency = (time.monotonic() - req_start) * 1000
                    latencies.append(latency)
                    total += 1
                    if resp.status_code >= 500:
                        errors += 1
                except Exception:
                    total += 1
                    errors += 1
                    latencies.append((time.monotonic() - req_start) * 1000)

        try:
            async with httpx.AsyncClient() as client:
                batch_size = min(concurrent_users, 200)
                iteration = 0

                while time.monotonic() < end_time:
                    # Запускаем пачку запросов
                    tasks = [make_request(client) for _ in range(batch_size)]
                    await asyncio.gather(*tasks, return_exceptions=True)

                    iteration += 1
                    elapsed = time.monotonic() - start_time
                    progress = min(elapsed / duration_seconds * 100, 100)

                    # Обновляем прогресс каждые 5 итераций
                    if iteration % 5 == 0:
                        await self._set_status(
                            running=True,
                            progress=round(progress, 1),
                            scenario=scenario,
                            concurrent_users=concurrent_users,
                        )

        except Exception as e:
            logger.error(f"Stress test error: {e}", exc_info=True)

        # Calculate results
        elapsed_total = time.monotonic() - start_time
        sorted_latencies = sorted(latencies) if latencies else [0]

        def percentile(data: list[float], pct: float) -> float:
            if not data:
                return 0.0
            idx = int(len(data) * pct / 100)
            return data[min(idx, len(data) - 1)]

        results = {
            "total_requests": total,
            "successful_requests": total - errors,
            "failed_requests": errors,
            "rps": round(total / elapsed_total, 2) if elapsed_total > 0 else 0,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            "p50_latency_ms": round(percentile(sorted_latencies, 50), 2),
            "p95_latency_ms": round(percentile(sorted_latencies, 95), 2),
            "p99_latency_ms": round(percentile(sorted_latencies, 99), 2),
            "error_rate": round(errors / total * 100, 2) if total > 0 else 0,
            "duration_seconds": round(elapsed_total, 2),
            "scenario": scenario,
            "concurrent_users": concurrent_users,
        }

        await self._set_results(results)
        await self._set_status(running=False, progress=100.0)

        logger.info(f"🔥 Stress test DONE: {total} requests, RPS={results['rps']}, errors={errors}")
