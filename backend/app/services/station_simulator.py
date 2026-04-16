"""
Station Simulator Service — interactive OCPP 1.6J virtual station.

Connects via WebSocket to the local OCPP server (`ws://127.0.0.1:9210/ws/{station_id}`)
and sends real OCPP messages: BootNotification, StatusNotification, Authorize,
StartTransaction, MeterValues, StopTransaction.

All data goes through the real ws_handler → real DB writes → visible on every admin page.
"""
import asyncio
import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import websockets

from sqlalchemy import text as sa_text

from ocpp_ws_server.redis_manager import redis_manager

logger = logging.getLogger(__name__)

REDIS_KEY = "simulator:status"
REDIS_TTL = 3600  # 1 hour

# Module-level singleton
_instance: Optional["StationSimulator"] = None


def get_simulator() -> Optional["StationSimulator"]:
    return _instance


class StationSimulator:
    """Interactive OCPP 1.6J virtual station."""

    def __init__(self, station_id: str, num_connectors: int = 2):
        self.station_id = station_id
        self.num_connectors = num_connectors
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.connectors: list[dict] = []
        self.event_log: list[dict] = []
        self._pending: dict[str, asyncio.Future] = {}
        self._listen_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._meter_tasks: dict[int, asyncio.Task] = {}
        self._connected = False
        self._boot_time: Optional[float] = None
        self._msg_count = 0

        for i in range(1, num_connectors + 1):
            self.connectors.append({
                "id": i,
                "status": "Available",
                "transaction_id": None,
                "meter_start": 0,
                "meter_current": 0,
                "energy_kwh": 0.0,
                "power_kw": 0.0,
                "start_time": None,
                "cost": 0.0,
            })

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_db_records(self) -> None:
        """Create temporary station + id_tag records so OCPP handlers work."""
        from app.db.session import get_db

        # Insert station record (DO NOTHING on conflict to avoid Supabase
        # audit trigger log_station_changes() which requires auth.uid())
        try:
            with next(get_db()) as db:
                db.execute(sa_text("""
                    INSERT INTO stations (id, serial_number, model, manufacturer,
                        power_capacity, connector_types, status, connectors_count,
                        ocpp_version, created_at, updated_at)
                    VALUES (:id, :id, 'Simulator', 'RedCharge',
                        22.0, ARRAY['Type2','CCS2'], 'active', :conns,
                        '1.6', NOW(), NOW())
                    ON CONFLICT (id) DO NOTHING
                """), {"id": self.station_id, "conns": self.num_connectors})
                db.commit()
                logger.info(f"[Simulator] Station record ensured: {self.station_id}")
        except Exception as e:
            logger.warning(f"[Simulator] Station DB setup failed: {e}")

        # Insert id_tag record (independent transaction)
        try:
            with next(get_db()) as db:
                db.execute(sa_text("""
                    INSERT INTO ocpp_authorization (id_tag, status, created_at)
                    VALUES ('SIM-USER-001', 'Accepted', NOW())
                    ON CONFLICT (id_tag) DO NOTHING
                """))
                db.commit()
                logger.info("[Simulator] Authorization record ensured: SIM-USER-001")
        except Exception as e:
            logger.warning(f"[Simulator] Authorization DB setup failed: {e}")

    def _cleanup_db_records(self) -> None:
        """Remove temporary DB records on disconnect."""
        from app.db.session import get_db
        try:
            with next(get_db()) as db:
                db.execute(sa_text(
                    "DELETE FROM ocpp_station_status WHERE station_id = :id"
                ), {"id": self.station_id})
                db.execute(sa_text(
                    "DELETE FROM stations WHERE id = :id"
                ), {"id": self.station_id})
                db.execute(sa_text(
                    "DELETE FROM ocpp_authorization WHERE id_tag = 'SIM-USER-001'"
                ))
                db.commit()
                logger.info(f"[Simulator] DB records cleaned up for {self.station_id}")
        except Exception as e:
            logger.warning(f"[Simulator] DB cleanup failed (non-fatal): {e}")

    async def connect(self) -> dict:
        global _instance
        port = int(os.environ.get("APP_PORT", "9210"))
        ws_url = f"ws://127.0.0.1:{port}/ws/{self.station_id}"
        logger.info(f"[Simulator] Connecting to {ws_url}")

        # Create DB records before WS connect so OCPP handlers find the station
        self._ensure_db_records()

        try:
            self.ws = await websockets.connect(
                ws_url,
                subprotocols=["ocpp1.6"],
                open_timeout=10,
            )
        except Exception as e:
            logger.error(f"[Simulator] WS connect failed: {e}")
            self._cleanup_db_records()
            raise RuntimeError(f"WebSocket connection failed: {e}")

        self._connected = True
        self._boot_time = time.monotonic()
        _instance = self

        # Start listener
        self._listen_task = asyncio.create_task(self._listen_loop())

        # Send BootNotification
        boot_resp = await self._send("BootNotification", {
            "chargePointVendor": "RedCharge",
            "chargePointModel": "Simulator",
            "chargePointSerialNumber": self.station_id,
            "firmwareVersion": "1.0.0-sim",
        })

        # Send StatusNotification per connector
        for conn in self.connectors:
            await self._send("StatusNotification", {
                "connectorId": conn["id"],
                "errorCode": "NoError",
                "status": "Available",
            })

        # Start heartbeat loop
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        await self._save_state()
        return {"status": "connected", "boot_response": boot_resp}

    async def disconnect(self) -> None:
        global _instance
        logger.info(f"[Simulator] Disconnecting {self.station_id}")

        # Cancel meter tasks
        for cid, task in list(self._meter_tasks.items()):
            task.cancel()
        self._meter_tasks.clear()

        # Cancel heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        # Cancel listener
        if self._listen_task:
            self._listen_task.cancel()
            self._listen_task = None

        # Close WS
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

        self._connected = False
        _instance = None

        # Clean up DB records
        self._cleanup_db_records()

        # Clear Redis state
        try:
            await redis_manager.redis.delete(REDIS_KEY)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # OCPP Actions
    # ------------------------------------------------------------------

    async def plug_in(self, connector_id: int) -> dict:
        conn = self._get_connector(connector_id)
        if conn["status"] != "Available":
            raise ValueError(f"Connector {connector_id} is {conn['status']}, expected Available")

        resp = await self._send("StatusNotification", {
            "connectorId": connector_id,
            "errorCode": "NoError",
            "status": "Preparing",
        })
        conn["status"] = "Preparing"
        await self._save_state()
        return {"connector_id": connector_id, "status": "Preparing"}

    async def start_charging(self, connector_id: int, id_tag: str = "SIM-USER-001") -> dict:
        conn = self._get_connector(connector_id)
        if conn["status"] != "Preparing":
            raise ValueError(f"Connector {connector_id} is {conn['status']}, expected Preparing")

        # Authorize
        auth_resp = await self._send("Authorize", {"idTag": id_tag})

        # StartTransaction
        meter_start = random.randint(100000, 999999)
        now = datetime.now(timezone.utc).isoformat()
        start_resp = await self._send("StartTransaction", {
            "connectorId": connector_id,
            "idTag": id_tag,
            "meterStart": meter_start,
            "timestamp": now,
        })

        transaction_id = None
        if isinstance(start_resp, dict):
            transaction_id = start_resp.get("transactionId")

        conn["status"] = "Charging"
        conn["transaction_id"] = transaction_id
        conn["meter_start"] = meter_start
        conn["meter_current"] = meter_start
        conn["energy_kwh"] = 0.0
        conn["power_kw"] = 0.0
        conn["start_time"] = time.monotonic()
        conn["cost"] = 0.0
        conn["soc"] = random.randint(15, 35)  # Starting SoC 15-35%

        # Send StatusNotification Charging
        await self._send("StatusNotification", {
            "connectorId": connector_id,
            "errorCode": "NoError",
            "status": "Charging",
        })

        # Start meter values loop
        self._meter_tasks[connector_id] = asyncio.create_task(
            self._meter_values_loop(connector_id)
        )

        await self._save_state()
        return {
            "connector_id": connector_id,
            "status": "Charging",
            "transaction_id": transaction_id,
        }

    async def stop_charging(self, connector_id: int) -> dict:
        conn = self._get_connector(connector_id)
        if conn["status"] != "Charging":
            raise ValueError(f"Connector {connector_id} is {conn['status']}, expected Charging")

        # Cancel meter task
        if connector_id in self._meter_tasks:
            self._meter_tasks[connector_id].cancel()
            del self._meter_tasks[connector_id]

        # StopTransaction
        now = datetime.now(timezone.utc).isoformat()
        stop_resp = await self._send("StopTransaction", {
            "transactionId": conn["transaction_id"],
            "meterStop": conn["meter_current"],
            "timestamp": now,
            "reason": "Local",
        })

        energy_kwh = conn["energy_kwh"]

        # StatusNotification Available
        await self._send("StatusNotification", {
            "connectorId": connector_id,
            "errorCode": "NoError",
            "status": "Available",
        })

        conn["status"] = "Available"
        conn["transaction_id"] = None
        conn["power_kw"] = 0.0
        conn["start_time"] = None

        await self._save_state()
        return {
            "connector_id": connector_id,
            "status": "Available",
            "energy_kwh": round(energy_kwh, 2),
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        uptime_s = 0.0
        if self._boot_time:
            uptime_s = time.monotonic() - self._boot_time

        connectors_info = []
        for conn in self.connectors:
            duration_s = 0.0
            if conn["start_time"]:
                duration_s = time.monotonic() - conn["start_time"]
            connectors_info.append({
                "id": conn["id"],
                "status": conn["status"],
                "energy_kwh": round(conn["energy_kwh"], 3),
                "power_kw": round(conn["power_kw"], 1),
                "duration_s": round(duration_s),
                "cost": round(conn["cost"], 2),
                "transaction_id": conn["transaction_id"],
            })

        return {
            "active": self._connected,
            "station_id": self.station_id,
            "connectors": connectors_info,
            "uptime_s": round(uptime_s),
            "messages_sent": self._msg_count,
        }

    def get_log(self) -> list[dict]:
        return self.event_log[-200:]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_connector(self, connector_id: int) -> dict:
        for c in self.connectors:
            if c["id"] == connector_id:
                return c
        raise ValueError(f"Connector {connector_id} not found (have 1..{self.num_connectors})")

    async def _send(self, action: str, payload: dict) -> dict:
        if not self.ws:
            raise RuntimeError("WebSocket not connected")

        msg_id = str(uuid.uuid4())[:8]
        call = [2, msg_id, action, payload]
        raw = json.dumps(call)

        self._log_event("out", action, payload)
        self._msg_count += 1
        await self.ws.send(raw)

        # Wait for CALLRESULT
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            logger.warning(f"[Simulator] Timeout waiting for {action} response")
            return {}

        self._log_event("in", f"{action}Response", result)
        return result if isinstance(result, dict) else {}

    async def _listen_loop(self) -> None:
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if not isinstance(msg, list) or len(msg) < 3:
                    continue

                msg_type = msg[0]

                if msg_type == 3:
                    # CALLRESULT [3, msgId, payload]
                    msg_id = msg[1]
                    payload = msg[2] if len(msg) > 2 else {}
                    fut = self._pending.pop(msg_id, None)
                    if fut and not fut.done():
                        fut.set_result(payload)

                elif msg_type == 4:
                    # CALLERROR [4, msgId, errorCode, errorDescription, errorDetails]
                    msg_id = msg[1]
                    error_code = msg[2] if len(msg) > 2 else "Unknown"
                    error_desc = msg[3] if len(msg) > 3 else ""
                    self._log_event("in", f"ERROR:{error_code}", {"description": error_desc})
                    fut = self._pending.pop(msg_id, None)
                    if fut and not fut.done():
                        fut.set_result({"error": error_code, "description": error_desc})

                elif msg_type == 2:
                    # CALL from server [2, msgId, action, payload]
                    server_msg_id = msg[1]
                    action = msg[2]
                    payload = msg[3] if len(msg) > 3 else {}
                    self._log_event("in", action, payload)

                    # Respond to known server commands
                    response = self._handle_server_call(action, payload)
                    resp_raw = json.dumps([3, server_msg_id, response])
                    await self.ws.send(resp_raw)
                    self._log_event("out", f"{action}Response", response)

        except websockets.exceptions.ConnectionClosed:
            logger.info("[Simulator] WS connection closed")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[Simulator] Listen loop error: {e}")

    def _handle_server_call(self, action: str, payload: dict) -> dict:
        if action == "RemoteStartTransaction":
            # Auto plug-in + start charging when client requests remotely
            connector_id = payload.get("connectorId", 1)
            id_tag = payload.get("idTag", "SIM-USER-001")
            asyncio.ensure_future(self._auto_start_charging(connector_id, id_tag))
            return {"status": "Accepted"}
        elif action == "RemoteStopTransaction":
            transaction_id = payload.get("transactionId")
            asyncio.ensure_future(self._auto_stop_charging(transaction_id))
            return {"status": "Accepted"}
        elif action == "Reset":
            return {"status": "Accepted"}
        elif action == "GetConfiguration":
            return {"configurationKey": [], "unknownKey": []}
        elif action == "ChangeConfiguration":
            return {"status": "Accepted"}
        elif action == "TriggerMessage":
            return {"status": "Accepted"}
        else:
            return {}

    async def _auto_start_charging(self, connector_id: int, id_tag: str) -> None:
        """Auto plug-in + start charging when RemoteStartTransaction received"""
        try:
            await asyncio.sleep(1)  # Small delay to simulate real station
            conn = self._get_connector(connector_id)
            if conn["status"] == "Available":
                await self.plug_in(connector_id)
                await asyncio.sleep(1)
                await self.start_charging(connector_id, id_tag)
                logger.info(f"[Simulator] Auto-started charging on connector {connector_id}")
            elif conn["status"] == "Preparing":
                await self.start_charging(connector_id, id_tag)
                logger.info(f"[Simulator] Auto-started charging on connector {connector_id} (was Preparing)")
            else:
                logger.warning(f"[Simulator] Cannot auto-start: connector {connector_id} is {conn['status']}")
        except Exception as e:
            logger.error(f"[Simulator] Auto-start failed: {e}")

    async def _auto_stop_charging(self, transaction_id: int) -> None:
        """Auto stop charging when RemoteStopTransaction received"""
        try:
            for conn in self.connectors:
                if conn.get("transaction_id") == transaction_id:
                    await self.stop_charging(conn["id"])
                    logger.info(f"[Simulator] Auto-stopped charging on connector {conn['id']}")
                    return
            logger.warning(f"[Simulator] No connector found for transaction {transaction_id}")
        except Exception as e:
            logger.error(f"[Simulator] Auto-stop failed: {e}")

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                if self.ws:
                    await self._send("Heartbeat", {})
                    await self._save_state()
        except asyncio.CancelledError:
            pass

    async def _meter_values_loop(self, connector_id: int) -> None:
        conn = self._get_connector(connector_id)
        try:
            while True:
                await asyncio.sleep(10)

                if conn["status"] != "Charging" or not conn["transaction_id"]:
                    break

                # Random power 7-22 kW
                power_kw = round(random.uniform(7.0, 22.0), 1)
                conn["power_kw"] = power_kw

                # Energy increment: power_kw * (10s / 3600) kWh → in Wh for OCPP
                energy_increment_kwh = power_kw * (10.0 / 3600.0)
                energy_increment_wh = int(energy_increment_kwh * 1000)
                conn["meter_current"] += energy_increment_wh
                conn["energy_kwh"] += energy_increment_kwh

                # Estimate cost (~12 сом/кВтч)
                conn["cost"] = round(conn["energy_kwh"] * 12.0, 2)

                # SoC increment: ~1-3% per meter cycle
                if "soc" not in conn:
                    conn["soc"] = random.randint(15, 35)
                conn["soc"] = min(100, conn["soc"] + random.randint(1, 3))

                now = datetime.now(timezone.utc).isoformat()
                await self._send("MeterValues", {
                    "connectorId": connector_id,
                    "transactionId": conn["transaction_id"],
                    "meterValue": [{
                        "timestamp": now,
                        "sampledValue": [
                            {
                                "value": str(conn["meter_current"]),
                                "context": "Sample.Periodic",
                                "measurand": "Energy.Active.Import.Register",
                                "unit": "Wh",
                            },
                            {
                                "value": str(int(power_kw * 1000)),
                                "context": "Sample.Periodic",
                                "measurand": "Power.Active.Import",
                                "unit": "W",
                            },
                            {
                                "value": str(conn["soc"]),
                                "context": "Sample.Periodic",
                                "measurand": "SoC",
                                "unit": "Percent",
                            },
                        ],
                    }],
                })

                await self._save_state()

        except asyncio.CancelledError:
            pass

    def _log_event(self, direction: str, action: str, payload: dict) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "action": action,
            "payload": payload,
        }
        self.event_log.append(entry)
        # Keep last 200
        if len(self.event_log) > 200:
            self.event_log = self.event_log[-200:]

    async def _save_state(self) -> None:
        try:
            state = json.dumps(self.get_status())
            await redis_manager.redis.set(REDIS_KEY, state, ex=REDIS_TTL)
        except Exception as e:
            logger.debug(f"[Simulator] Redis save state failed: {e}")
