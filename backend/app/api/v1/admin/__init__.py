"""
Admin API модули
- users: управление пользователями (superadmin only)
- operators: управление операторами (admin/superadmin)
- logs: OCPP логирование
- tariffs: управление тарифными планами
- corporate: управление корпоративными клиентами
- stations: CRUD станций
- locations: CRUD локаций
- sessions: управление сессиями зарядки
- analytics: аналитика выручки
- stress_test: стресс-тестирование API (SBX-01..07)
- simulator: интерактивный OCPP симулятор станции
- alerts: критические алерты (DASH-06)
- connectors: CRUD портов (разъёмов) станций
"""
from fastapi import APIRouter
from . import users
from . import operators
from . import logs
from . import tariffs
from . import corporate
from . import stations
from . import locations
from . import connectors
from . import sessions
from . import analytics
from . import stress_test
from . import simulator
from . import alerts
from . import clients
from . import bookings
from . import partners
from . import equipment
from . import integrations
from . import webhooks

router = APIRouter(prefix="/admin")
router.include_router(users.router, tags=["admin-users"])
router.include_router(operators.router, tags=["admin-users"])
router.include_router(logs.router, tags=["admin-logs"])
router.include_router(tariffs.router, tags=["admin-tariffs"])
router.include_router(corporate.router, tags=["admin-corporate"])
router.include_router(stations.router, tags=["admin-stations"])
router.include_router(locations.router, tags=["admin-locations"])
router.include_router(connectors.router, tags=["admin-connectors"])
router.include_router(sessions.router, tags=["admin-sessions"])
router.include_router(analytics.router, tags=["admin-analytics"])
router.include_router(stress_test.router, tags=["admin-stress-test"])
router.include_router(simulator.router, tags=["admin-simulator"])
router.include_router(alerts.router, tags=["admin-alerts"])
router.include_router(clients.router, tags=["admin-clients"])
router.include_router(bookings.router, tags=["admin-bookings"])
router.include_router(partners.router, tags=["admin-partners"])
router.include_router(equipment.router, tags=["admin-equipment"])
router.include_router(integrations.router, tags=["admin-integrations"])
router.include_router(webhooks.router, tags=["admin-webhooks"])

__all__ = ["router"]
