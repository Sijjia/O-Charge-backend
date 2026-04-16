"""
Alert Service — генерация критических алертов для дашборда
"""
import logging

logger = logging.getLogger(__name__)


class AlertService:
    """Сервис алертов — генерация уведомлений из статусов станций и сессий"""

    SEVERITY_LEVELS = ("critical", "warning", "info")

    @staticmethod
    def classify_station_alert(status: str) -> str:
        """Определить severity по статусу станции"""
        if status == "Faulted":
            return "critical"
        if status in ("Unavailable", "Reserved"):
            return "warning"
        return "info"

    @staticmethod
    def classify_session_alert(status: str) -> str:
        """Определить severity по статусу сессии"""
        if status == "error":
            return "critical"
        return "info"
