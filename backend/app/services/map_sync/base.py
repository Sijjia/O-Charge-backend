"""Base class for map platform connectors."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MapPlatformConnector:
    """Base connector for map platform sync operations."""

    platform: str = ""
    display_name: str = ""

    def __init__(self, api_key: str | None = None, config: dict | None = None):
        self.api_key = api_key
        self.config = config or {}

    async def sync_location(self, location: dict, stations: list, connectors: list) -> dict:
        """Sync a single location to the platform.

        Returns: {"ok": bool, "external_id": str|None, "external_url": str|None, "message": str}
        """
        raise NotImplementedError

    async def update_availability(self, external_id: str, status_data: dict) -> dict:
        """Update real-time availability status on the platform.

        Returns: {"ok": bool, "message": str}
        """
        raise NotImplementedError

    async def test_connection(self) -> dict:
        """Test connectivity to the platform API.

        Returns: {"ok": bool, "message": str}
        """
        raise NotImplementedError
