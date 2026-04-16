"""Google Business Profile connector — OAuth 2.0 based."""
import logging
from .base import MapPlatformConnector

logger = logging.getLogger(__name__)


class GoogleBusinessConnector(MapPlatformConnector):
    platform = "google_business"
    display_name = "Google Business Profile"

    async def test_connection(self) -> dict:
        """Test Google Business Profile API access."""
        # Phase 3: Implement OAuth 2.0 token validation
        if not self.config.get("oauth_refresh_token"):
            return {"ok": False, "message": "Требуется OAuth авторизация через Google"}
        return {"ok": False, "message": "Google Business connector в разработке (Phase 3)"}

    async def sync_location(self, location: dict, stations: list, connectors: list) -> dict:
        """Sync location to Google Business Profile."""
        # Phase 3: Implement GBP CRUD via Business Profile API
        return {
            "ok": False,
            "external_id": None,
            "external_url": None,
            "message": "Google Business sync в разработке (Phase 3)",
        }

    async def update_availability(self, external_id: str, status_data: dict) -> dict:
        """Update business hours/status on GBP."""
        return {"ok": False, "message": "Google Business availability update в разработке"}
