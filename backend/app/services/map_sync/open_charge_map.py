"""Open Charge Map connector — largest open EV charging database."""
import httpx
import logging
from .base import MapPlatformConnector

logger = logging.getLogger(__name__)

OCM_BASE_URL = "https://api.openchargemap.io/v3"


class OpenChargeMapConnector(MapPlatformConnector):
    platform = "open_charge_map"
    display_name = "Open Charge Map"

    async def test_connection(self) -> dict:
        """Test API availability with a simple search request."""
        try:
            params = {"output": "json", "maxresults": 1, "countrycode": "KG"}
            if self.api_key:
                params["key"] = self.api_key

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{OCM_BASE_URL}/poi", params=params)
                if resp.status_code == 200:
                    return {"ok": True, "message": f"OCM API доступен (HTTP {resp.status_code})"}
                return {"ok": False, "message": f"OCM API вернул HTTP {resp.status_code}"}
        except Exception as e:
            return {"ok": False, "message": f"Ошибка подключения: {str(e)}"}

    async def sync_location(self, location: dict, stations: list, connectors: list) -> dict:
        """Submit/update a POI to Open Charge Map."""
        try:
            if not self.api_key:
                return {"ok": False, "external_id": None, "external_url": None,
                        "message": "API key не настроен для OCM"}

            # Build OCM POI payload
            connections = []
            for conn in connectors:
                connections.append({
                    "ConnectionTypeID": _map_connector_type(conn.get("connector_type", "")),
                    "PowerKW": conn.get("max_power"),
                    "StatusTypeID": 50 if conn.get("status") == "Available" else 100,
                })

            poi_data = {
                "DataProviderID": 1,
                "OperatorInfo": {"Title": "Red Petroleum EV"},
                "UsageCost": "",
                "AddressInfo": {
                    "Title": location.get("name", ""),
                    "AddressLine1": location.get("address", ""),
                    "Town": location.get("city", ""),
                    "CountryID": 117,  # Kyrgyzstan
                    "Latitude": location.get("lat"),
                    "Longitude": location.get("lng"),
                },
                "Connections": connections,
                "NumberOfPoints": len(stations),
                "StatusTypeID": 50 if location.get("status") == "active" else 100,
            }

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{OCM_BASE_URL}/poi",
                    json=poi_data,
                    params={"key": self.api_key},
                )
                if resp.status_code in (200, 201):
                    result = resp.json()
                    ext_id = str(result.get("ID", ""))
                    return {
                        "ok": True,
                        "external_id": ext_id,
                        "external_url": f"https://openchargemap.org/site/poi/details/{ext_id}" if ext_id else None,
                        "message": "Синхронизировано с Open Charge Map",
                    }
                return {"ok": False, "external_id": None, "external_url": None,
                        "message": f"OCM вернул HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            logger.error(f"[OCM] sync_location error: {e}")
            return {"ok": False, "external_id": None, "external_url": None, "message": str(e)}

    async def update_availability(self, external_id: str, status_data: dict) -> dict:
        """Update availability status for a POI."""
        # OCM doesn't have a dedicated real-time status update API
        # Status updates are done via full POI submission
        return {"ok": False, "message": "OCM не поддерживает обновление статуса в реальном времени"}


def _map_connector_type(ct: str) -> int:
    """Map internal connector type to OCM ConnectionTypeID."""
    mapping = {
        "Type2": 25,
        "CCS2": 33,
        "CHAdeMO": 2,
        "CCS1": 32,
        "Type1": 1,
        "GBT": 30,
    }
    return mapping.get(ct, 0)
