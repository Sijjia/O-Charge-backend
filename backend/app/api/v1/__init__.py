"""
API v1 модули
"""
from fastapi import APIRouter

# Импортируем роутеры из модулей
from .charging import start_router, stop_router, status_router, receipt_router
from . import balance, payment, station, locations, notifications
from .auth import session as auth_session
from .auth import otp as auth_otp
from .auth import sms_otp as auth_sms_otp
from .auth import dev_login as auth_dev_login
from .auth import sso as auth_sso
from . import profile as profile_module
from . import history as history_module
from . import favorites as favorites_module
from . import admin as admin_module
from . import guest as guest_module
from . import booking as booking_module
from . import partner as partner_module
from . import corporate as corporate_module
from . import stations as stations_module

# Создаем общий роутер для v1
router = APIRouter(prefix="/api/v1")

# Подключаем модули
router.include_router(start_router, tags=["charging"])
router.include_router(stop_router, tags=["charging"])
router.include_router(status_router, tags=["charging"])
router.include_router(receipt_router, tags=["charging"])

# Подключаем новые модульные маршруты
router.include_router(balance.router, tags=["balance"])
router.include_router(payment.router, tags=["payment"])
router.include_router(station.router, tags=["station"])
router.include_router(locations.router, tags=["locations"])
router.include_router(profile_module.router, tags=["profile"])
router.include_router(notifications.router, tags=["notifications"])  # Push Notifications
router.include_router(auth_session.router, tags=["auth"])
router.include_router(auth_otp.router, tags=["auth-otp"])  # Phone OTP Auth (WhatsApp)
router.include_router(auth_sms_otp.router, tags=["auth-sms-otp"])  # Phone OTP Auth (SMS)
router.include_router(auth_dev_login.router, tags=["auth-dev"])  # Dev Login (non-production only)
router.include_router(auth_sso.router, tags=["auth-sso"])  # Keycloak SSO (staff login)
router.include_router(history_module.router, tags=["history"])
router.include_router(favorites_module.router, tags=["favorites"])
router.include_router(admin_module.router)  # Admin endpoints (superadmin only)
router.include_router(guest_module.router, tags=["guest-charging"])  # Guest Charging (без авторизации)
router.include_router(booking_module.router, tags=["booking"])  # Booking API
router.include_router(partner_module.router, tags=["partner"])  # Partner Cabinet API
router.include_router(corporate_module.router, tags=["corporate"])  # Corporate Panel API
router.include_router(stations_module.router, tags=["mobile-stations"])  # Mobile Stations API

__all__ = ["router"]