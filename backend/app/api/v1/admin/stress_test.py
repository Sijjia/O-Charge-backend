"""
Admin API: Stress Test — симуляция нагрузки (SBX-01..07)
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional
import logging

from app.db.session import get_db
from .operators import get_current_admin
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/stress-test")
logger = logging.getLogger(__name__)


class StressTestRequest(BaseModel):
    concurrent_users: int = Field(100, ge=1, le=1000000)
    duration_seconds: int = Field(30, ge=5, le=300)
    scenario: str = Field("mixed", pattern="^(auth_flow|charging_flow|balance_check|mixed)$")


class StressTestStatus(BaseModel):
    running: bool = False
    progress: float = 0.0
    scenario: Optional[str] = None
    concurrent_users: Optional[int] = None


class StressTestResults(BaseModel):
    success: bool = True
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rps: float = 0.0
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    error_rate: float = 0.0
    duration_seconds: float = 0.0
    scenario: str = ""
    concurrent_users: int = 0


@router.post("/run", summary="Run stress test", description="Launches a background stress test with configurable concurrent users.", responses=ADMIN_RESPONSES)
async def run_stress_test(
    request: Request,
    body: StressTestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Запуск стресс-теста (симуляция N одновременных пользователей)"""
    admin = get_current_admin(request, db)

    from app.services.stress_test_service import StressTestService

    service = StressTestService()

    # Check if test is already running
    status = await service.get_status()
    if status.get("running"):
        raise HTTPException(status_code=409, detail="Стресс-тест уже запущен")

    # Start test in background
    background_tasks.add_task(
        service.run_test,
        concurrent_users=body.concurrent_users,
        duration_seconds=body.duration_seconds,
        scenario=body.scenario,
    )

    return {
        "success": True,
        "message": f"Стресс-тест запущен: {body.concurrent_users} юзеров, {body.duration_seconds}с, сценарий '{body.scenario}'",
    }


@router.get("/status", summary="Get stress test status", description="Returns current stress test progress.", responses=ADMIN_RESPONSES)
async def get_stress_test_status(
    request: Request,
    db: Session = Depends(get_db),
):
    """Текущий статус стресс-теста"""
    admin = get_current_admin(request, db)

    from app.services.stress_test_service import StressTestService

    service = StressTestService()
    status = await service.get_status()

    return {"success": True, **status}


@router.get("/results", summary="Get stress test results", description="Returns results from the last completed stress test.", responses=ADMIN_RESPONSES)
async def get_stress_test_results(
    request: Request,
    db: Session = Depends(get_db),
):
    """Результаты последнего стресс-теста"""
    admin = get_current_admin(request, db)

    from app.services.stress_test_service import StressTestService

    service = StressTestService()
    results = await service.get_results()

    if not results:
        raise HTTPException(status_code=404, detail="Результатов нет — запустите тест")

    return {"success": True, **results}
