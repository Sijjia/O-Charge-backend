"""
OCPP 2.0.1 WebSocket Handler
Dual-protocol support: v201 messages mapped to the same DB tables and services as v1.6

Supported messages:
- BootNotification
- Heartbeat
- StatusNotification
- Authorize
- TransactionEvent (Started / Updated / Ended)

Remote commands (via Redis):
- RequestStartTransaction
- RequestStopTransaction
- Reset
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from ocpp.v201 import ChargePoint as CP201
from ocpp.routing import on
from ocpp.v201 import call_result as call_result_201

from .redis_manager import redis_manager
from .ws_handler import active_sessions, _classify_error  # Shared state + error classifier
from app.db.session import get_db
from app.crud.ocpp_service import (
    OCPPStationService,
    OCPPTransactionService,
    OCPPMeterService,
    OCPPAuthorizationService,
    OCPPConfigurationService
)
from app.db.models.ocpp import OCPPTransaction
from sqlalchemy import text
from decimal import Decimal
from app.services.push_service import push_service
from app.services.realtime_service import RealtimeService
from app.services.pricing_service import PricingService
from app.services.log_service import OCPPLogService

logger = logging.getLogger(__name__)


def _v201_tx_id_to_numeric(v201_tx_id: str) -> int:
    """Convert v201 string transaction_id (UUID) to numeric for DB compatibility.
    Uses deterministic hash: abs(hash(uuid)) % (2^31).
    Collisions are practically impossible within a single station context.
    """
    return abs(hash(v201_tx_id)) % (2**31)


class OCPP201ChargePoint(CP201):
    """
    OCPP 2.0.1 ChargePoint handler.
    Maps v201 messages to the same DB tables and services as v1.6.
    """

    def __init__(self, id: str, connection):
        super().__init__(id, connection)
        self.logger = logging.getLogger(f"OCPP201.{id}")

    # ========================================================================
    # BootNotification
    # ========================================================================

    @on('BootNotification')
    def on_boot_notification(self, charging_station, reason, **kwargs):
        """Station boot — same logic as v16 + update ocpp_version to '2.0.1'"""
        model = charging_station.get('model', 'Unknown')
        vendor = charging_station.get('vendor_name', 'Unknown')
        firmware = charging_station.get('firmware_version')

        self.logger.info(f"BootNotification v201: model={model}, vendor={vendor}, reason={reason}")

        try:
            asyncio.create_task(self._handle_boot_notification_background(firmware))

            return call_result_201.BootNotification(
                current_time=datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S') + 'Z',
                interval=300,
                status='Accepted'
            )
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in BootNotification v201 [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result_201.BootNotification(
                current_time=datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S') + 'Z',
                interval=300,
                status='Rejected'
            )

    async def _handle_boot_notification_background(self, firmware_version: str = None):
        """Background processing for BootNotification — ported from v16 with ocpp_version update"""
        try:
            self.logger.info(f"Background BootNotification v201 for {self.id}")

            # OCPP DB log
            try:
                with next(get_db()) as log_db:
                    OCPPLogService.log_event(
                        db=log_db,
                        station_id=self.id,
                        event_type="BootNotification",
                        direction="inbound",
                        request_payload={"firmware_version": firmware_version, "protocol": "2.0.1"},
                        severity="info",
                    )
            except Exception as e:
                self.logger.warning(f"Failed to log OCPP event (BootNotification): {e}", exc_info=True)

            with next(get_db()) as db:
                # Mark station as booted
                OCPPStationService.mark_boot_notification_sent(
                    db, self.id, firmware_version
                )

                # Update ocpp_version to 2.0.1
                db.execute(text("""
                    UPDATE stations SET ocpp_version = '2.0.1' WHERE id = :station_id
                """), {"station_id": self.id})

                # Basic configuration
                OCPPConfigurationService.set_configuration(
                    db, self.id, "HeartbeatInterval", "300", readonly=True
                )
                OCPPConfigurationService.set_configuration(
                    db, self.id, "MeterValueSampleInterval", "60", readonly=True
                )

                # Cleanup hanging sessions (same as v16)
                hanging_sessions_query = text("""
                    SELECT id, user_id, amount
                    FROM charging_sessions
                    WHERE station_id = :station_id
                    AND status = 'started'
                    AND transaction_id IS NULL
                """)
                hanging_sessions = db.execute(hanging_sessions_query, {"station_id": self.id}).fetchall()

                for session in hanging_sessions:
                    session_id, user_id, reserved_amount = session
                    self.logger.warning(
                        f"Hanging session {session_id} found at v201 BootNotification. "
                        f"Closing with refund."
                    )

                    db.execute(text("""
                        UPDATE charging_sessions
                        SET status = 'error', stop_time = NOW(), energy = 0, amount = 0,
                            error_details = 'Station rebooted before transaction started (v201)'
                        WHERE id = :session_id
                    """), {"session_id": session_id})

                    if reserved_amount and float(reserved_amount) > 0:
                        db.execute(text("""
                            UPDATE clients SET balance = balance + :refund_amount WHERE id = :user_id
                        """), {"refund_amount": float(reserved_amount), "user_id": user_id})

                        db.execute(text("""
                            INSERT INTO payment_transactions_odengi
                            (client_id, transaction_type, amount, description, charging_session_id)
                            VALUES (:client_id, 'charge_refund', :amount, :description, :session_id)
                        """), {
                            "client_id": user_id,
                            "amount": float(reserved_amount),
                            "description": f"Refund: station rebooted (session {session_id})",
                            "session_id": session_id
                        })

                    db.execute(text("""
                        UPDATE connectors
                        SET status = 'available', error_code = 'NoError', last_status_update = NOW()
                        WHERE station_id = :station_id AND status = 'occupied'
                    """), {"station_id": self.id})

                if hanging_sessions:
                    self.logger.info(f"Closed {len(hanging_sessions)} hanging sessions at v201 Boot")

                db.commit()

                asyncio.create_task(
                    RealtimeService.broadcast_station_update(db, self.id)
                )

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in BootNotification v201 background [{err_cat}]: station={self.id}: {e}", exc_info=True)

    # ========================================================================
    # Heartbeat
    # ========================================================================

    @on('Heartbeat')
    def on_heartbeat(self, **kwargs):
        """Periodic heartbeat — identical to v16"""
        self.logger.debug(f"Heartbeat v201 from {self.id}")

        try:
            asyncio.create_task(redis_manager.refresh_station_ttl(self.id))

            with next(get_db()) as db:
                OCPPStationService.update_heartbeat(db, self.id)

                location_query = text("""
                    SELECT location_id FROM stations WHERE id = :station_id
                """)
                location_result = db.execute(location_query, {"station_id": self.id}).fetchone()

                if location_result:
                    location_id = location_result[0]
                    from app.services.location_status_service import LocationStatusService
                    asyncio.create_task(LocationStatusService.invalidate_cache(location_id))

            return call_result_201.Heartbeat(
                current_time=datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S') + 'Z'
            )

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in Heartbeat v201 [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result_201.Heartbeat(
                current_time=datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S') + 'Z'
            )

    # ========================================================================
    # StatusNotification
    # ========================================================================

    @on('StatusNotification')
    def on_status_notification(self, timestamp, connector_status, evse_id, connector_id, **kwargs):
        """Status notification — v201 uses evse_id as connector_number, no error_code field.
        Faulted status maps to error_code='OtherError'.
        """
        # In our simplified model: evse_id = connector_number
        connector_number = evse_id

        self.logger.info(
            f"StatusNotification v201: evse={evse_id}, connector={connector_id}, "
            f"status={connector_status}"
        )

        # Map v201 connector_status to v16-compatible status
        v201_to_v16_status = {
            'Available': 'Available',
            'Occupied': 'Occupied',
            'Reserved': 'Reserved',
            'Unavailable': 'Unavailable',
            'Faulted': 'Faulted',
        }
        mapped_status = v201_to_v16_status.get(connector_status, 'Unavailable')

        # v201 has no error_code field; infer from status
        error_code = 'OtherError' if connector_status == 'Faulted' else 'NoError'

        # OCPP DB log
        try:
            with next(get_db()) as log_db:
                _sev = "error" if connector_status in ("Faulted", "Unavailable") else "info"
                OCPPLogService.log_event(
                    db=log_db,
                    station_id=self.id,
                    event_type="StatusNotification",
                    direction="inbound",
                    connector_id=connector_number,
                    request_payload={
                        "connector_status": connector_status,
                        "evse_id": evse_id,
                        "connector_id": connector_id,
                        "protocol": "2.0.1"
                    },
                    severity=_sev,
                    error_message=error_code if error_code != "NoError" else None,
                )
        except Exception as e:
            self.logger.warning(f"Failed to log OCPP event (StatusNotification): {e}", exc_info=True)

        try:
            with next(get_db()) as db:
                # Update station status (same as v16)
                OCPPStationService.update_station_status(
                    db, self.id, mapped_status, error_code, None, None, None
                )

                # Map to our connector status format
                connector_status_mapping = {
                    'Available': 'available',
                    'Occupied': 'occupied',
                    'Reserved': 'occupied',
                    'Unavailable': 'unavailable',
                    'Faulted': 'faulted',
                }
                new_status = connector_status_mapping.get(mapped_status, 'unavailable')

                db.execute(text("""
                    UPDATE connectors
                    SET status = :status, error_code = :error_code, last_status_update = NOW()
                    WHERE station_id = :station_id AND connector_number = :connector_id
                """), {
                    "status": new_status,
                    "error_code": error_code,
                    "station_id": self.id,
                    "connector_id": connector_number
                })

                # Invalidate location cache + broadcast
                location_query = text("""
                    SELECT location_id FROM stations WHERE id = :station_id
                """)
                location_result = db.execute(location_query, {"station_id": self.id}).fetchone()

                if location_result:
                    location_id = location_result[0]
                    from app.services.location_status_service import LocationStatusService
                    asyncio.create_task(LocationStatusService.invalidate_cache(location_id))
                    asyncio.create_task(
                        RealtimeService.broadcast_connector_update(db, self.id, connector_number)
                    )

                db.commit()

            return call_result_201.StatusNotification()

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in StatusNotification v201 [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result_201.StatusNotification()

    # ========================================================================
    # Authorize
    # ========================================================================

    @on('Authorize')
    def on_authorize(self, id_token, **kwargs):
        """Authorization — id_token.id_token maps to v16 id_tag"""
        # v201 id_token is a dict: {"idToken": "...", "type": "ISO14443"}
        id_tag = id_token.get('id_token', '') if isinstance(id_token, dict) else str(id_token)

        self.logger.info(f"Authorize v201: id_token={id_tag}")

        try:
            with next(get_db()) as db:
                auth_result = OCPPAuthorizationService.authorize_id_tag(db, id_tag)

            # Map v16 status to v201 format
            v16_to_v201_status = {
                'Accepted': 'Accepted',
                'Blocked': 'Blocked',
                'Expired': 'Expired',
                'Invalid': 'Invalid',
                'ConcurrentTx': 'ConcurrentTx',
            }
            v201_status = v16_to_v201_status.get(auth_result.get('status', 'Invalid'), 'Invalid')

            # OCPP DB log
            try:
                with next(get_db()) as log_db:
                    OCPPLogService.log_event(
                        db=log_db,
                        station_id=self.id,
                        event_type="Authorize",
                        direction="inbound",
                        request_payload={"id_token": id_tag, "protocol": "2.0.1"},
                        response_payload={"status": v201_status},
                        severity="info",
                    )
            except Exception as e:
                self.logger.warning(f"Failed to log OCPP event (Authorize): {e}", exc_info=True)

            return call_result_201.Authorize(
                id_token_info={'status': v201_status}
            )

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in Authorize v201 [{err_cat}]: station={self.id}: {e}", exc_info=True)
            return call_result_201.Authorize(
                id_token_info={'status': 'Invalid'}
            )

    # ========================================================================
    # TransactionEvent — THE KEY HANDLER
    # ========================================================================

    @on('TransactionEvent')
    async def on_transaction_event(
        self, event_type, timestamp, trigger_reason, seq_no,
        transaction_info, **kwargs
    ):
        """
        Unified transaction handler for v201. Routes to:
        - Started  → equivalent of v16 StartTransaction
        - Updated  → equivalent of v16 MeterValues
        - Ended    → equivalent of v16 StopTransaction
        """
        v201_tx_id = transaction_info.get('transaction_id', '') if isinstance(transaction_info, dict) else ''
        self.logger.info(
            f"TransactionEvent v201: type={event_type}, trigger={trigger_reason}, "
            f"seq={seq_no}, tx_id={v201_tx_id}"
        )

        # OCPP DB log
        try:
            with next(get_db()) as log_db:
                OCPPLogService.log_event(
                    db=log_db,
                    station_id=self.id,
                    event_type=f"TransactionEvent.{event_type}",
                    direction="inbound",
                    request_payload={
                        "event_type": event_type,
                        "trigger_reason": trigger_reason,
                        "seq_no": seq_no,
                        "transaction_info": transaction_info,
                        "protocol": "2.0.1"
                    },
                    severity="info",
                )
        except Exception as e:
            self.logger.warning(f"Failed to log OCPP event (TransactionEvent): {e}", exc_info=True)

        try:
            if event_type == 'Started':
                return await self._handle_transaction_started(
                    v201_tx_id, timestamp, trigger_reason, transaction_info, kwargs
                )
            elif event_type == 'Updated':
                return await self._handle_transaction_updated(
                    v201_tx_id, timestamp, trigger_reason, transaction_info, kwargs
                )
            elif event_type == 'Ended':
                return await self._handle_transaction_ended(
                    v201_tx_id, timestamp, trigger_reason, transaction_info, kwargs
                )
            else:
                self.logger.warning(f"Unknown TransactionEvent type: {event_type}")
                return call_result_201.TransactionEvent(total_cost=0)

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in TransactionEvent v201 [{err_cat}] ({event_type}): station={self.id}: {e}", exc_info=True)
            try:
                with next(get_db()) as err_db:
                    OCPPLogService.log_event(
                        db=err_db, station_id=self.id,
                        event_type=f"TransactionEvent.{event_type}",
                        direction="inbound", severity="error",
                        error_message=f"[{err_cat}] {e}",
                    )
            except Exception as log_err:
                self.logger.debug(f"Failed to log TransactionEvent error to DB: {log_err}")
            return call_result_201.TransactionEvent(total_cost=0)

    # ========================================================================
    # TransactionEvent.Started
    # ========================================================================

    async def _handle_transaction_started(self, v201_tx_id, timestamp, trigger_reason, transaction_info, extra):
        """Equivalent of v16 StartTransaction"""
        numeric_tx_id = _v201_tx_id_to_numeric(v201_tx_id)
        evse = extra.get('evse', {}) if isinstance(extra.get('evse'), dict) else {}
        connector_id = evse.get('id', 1)

        # Extract id_tag from id_token if present
        id_token = extra.get('id_token', {})
        id_tag = id_token.get('id_token', 'system') if isinstance(id_token, dict) else 'system'

        # Extract meter_start from meter_value if present
        meter_start = 0
        meter_value = extra.get('meter_value', [])
        if meter_value:
            meter_start = self._extract_energy_from_meter_values(meter_value)

        self.logger.info(
            f"TransactionEvent.Started: v201_tx={v201_tx_id}, numeric_tx={numeric_tx_id}, "
            f"connector={connector_id}, id_tag={id_tag}, meter_start={meter_start}"
        )

        try:
            with next(get_db()) as db:
                # Save v201 -> numeric mapping
                db.execute(text("""
                    INSERT INTO ocpp_v201_tx_map (v201_transaction_id, numeric_transaction_id, station_id)
                    VALUES (:v201_id, :numeric_id, :station_id)
                    ON CONFLICT (v201_transaction_id, station_id) DO NOTHING
                """), {
                    "v201_id": v201_tx_id,
                    "numeric_id": numeric_tx_id,
                    "station_id": self.id
                })

                # === SESSION LOOKUP (same 3-method approach as v16) ===
                charging_session_id = None
                client_id = None

                # Method 1: Redis pending session
                pending_key = f"pending:{self.id}:{connector_id}"
                try:
                    pending_session = redis_manager.get_sync(pending_key)
                    if pending_session:
                        charging_session_id = pending_session
                        self.logger.info(f"Found pending session: {pending_key} -> {charging_session_id}")
                        redis_manager.delete_sync(pending_key)

                        session_row = db.execute(text("""
                            SELECT user_id FROM charging_sessions WHERE id = :session_id
                        """), {"session_id": charging_session_id}).fetchone()
                        if session_row:
                            client_id = session_row[0]

                        db.execute(text("""
                            UPDATE charging_sessions SET transaction_id = :tx WHERE id = :sid
                        """), {"tx": str(numeric_tx_id), "sid": charging_session_id})
                except Exception as redis_err:
                    self.logger.warning(f"Redis pending error: {redis_err}")

                # Method 2: Phone lookup
                if not charging_session_id:
                    phone_result = db.execute(text("""
                        SELECT c.id as client_id, cs.id as session_id
                        FROM clients c
                        JOIN charging_sessions cs ON cs.user_id = c.id
                        WHERE REPLACE(REPLACE(c.phone, '+', ''), ' ', '') = :id_tag
                        AND cs.status = 'started'
                        AND cs.station_id = :station_id
                        ORDER BY cs.start_time DESC LIMIT 1
                    """), {"id_tag": id_tag, "station_id": self.id}).fetchone()

                    if phone_result:
                        client_id = phone_result[0]
                        charging_session_id = phone_result[1]
                        db.execute(text("""
                            UPDATE charging_sessions SET transaction_id = :tx WHERE id = :sid
                        """), {"tx": str(numeric_tx_id), "sid": charging_session_id})
                        self.logger.info(f"Found session by phone: client={client_id}, session={charging_session_id}")

                # Method 3: Fallback via ocpp_authorization
                if not charging_session_id:
                    auth_row = db.execute(text("""
                        SELECT client_id FROM ocpp_authorization
                        WHERE id_tag = :id_tag AND client_id IS NOT NULL LIMIT 1
                    """), {"id_tag": id_tag}).fetchone()

                    if auth_row:
                        client_id = auth_row[0]
                        session_row = db.execute(text("""
                            SELECT id FROM charging_sessions
                            WHERE user_id = :client_id AND status = 'started' AND station_id = :station_id
                            ORDER BY start_time DESC LIMIT 1
                        """), {"client_id": client_id, "station_id": self.id}).fetchone()

                        if session_row:
                            charging_session_id = session_row[0]
                            db.execute(text("""
                                UPDATE charging_sessions SET transaction_id = :tx WHERE id = :sid
                            """), {"tx": str(numeric_tx_id), "sid": charging_session_id})

                # Parse timestamp
                ts = timestamp
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts.replace('Z', ''))

                # Create OCPP transaction
                OCPPTransactionService.start_transaction(
                    db, self.id, numeric_tx_id, connector_id, id_tag,
                    float(meter_start), ts, charging_session_id
                )

                # Load session limits
                session_limit_type = None
                session_limit_value = None
                if charging_session_id:
                    limits_result = db.execute(text("""
                        SELECT limit_type, limit_value FROM charging_sessions WHERE id = :session_id
                    """), {"session_id": charging_session_id}).fetchone()
                    if limits_result:
                        session_limit_type = limits_result[0]
                        session_limit_value = float(limits_result[1]) if limits_result[1] else None

                # Store in active_sessions (shared with v16)
                active_sessions[self.id] = {
                    'transaction_id': numeric_tx_id,
                    'v201_transaction_id': v201_tx_id,
                    'charging_session_id': charging_session_id,
                    'meter_start': meter_start,
                    'energy_delivered': 0.0,
                    'connector_id': connector_id,
                    'id_tag': id_tag,
                    'client_id': client_id,
                    'limit_type': session_limit_type,
                    'limit_value': session_limit_value,
                    'ocpp_version': '2.0.1',
                }

                self.logger.info(
                    f"Limits loaded: {session_limit_type} = {session_limit_value} "
                    f"for session {charging_session_id}"
                )

                # Mark connector as occupied
                db.execute(text("""
                    UPDATE connectors
                    SET status = 'occupied', last_status_update = NOW()
                    WHERE station_id = :station_id AND connector_number = :connector_id
                """), {"station_id": self.id, "connector_id": connector_id})

                db.commit()

            self.logger.info(
                f"Transaction started v201: numeric_tx={numeric_tx_id}, "
                f"v201_tx={v201_tx_id}, session={charging_session_id}"
            )

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in TransactionEvent.Started [{err_cat}]: station={self.id}, tx={v201_tx_id}: {e}", exc_info=True)
            try:
                with next(get_db()) as err_db:
                    OCPPLogService.log_event(
                        db=err_db, station_id=self.id, event_type="TransactionEvent.Started",
                        direction="inbound", severity="error",
                        error_message=f"[{err_cat}] tx={v201_tx_id}: {e}",
                    )
            except Exception as log_err:
                self.logger.debug(f"Failed to log TransactionEvent.Started error to DB: {log_err}")

        return call_result_201.TransactionEvent(total_cost=0)

    # ========================================================================
    # TransactionEvent.Updated
    # ========================================================================

    async def _handle_transaction_updated(self, v201_tx_id, timestamp, trigger_reason, transaction_info, extra):
        """Equivalent of v16 MeterValues — energy tracking + limit enforcement"""
        meter_value = extra.get('meter_value', [])

        if not meter_value:
            return call_result_201.TransactionEvent(total_cost=0)

        try:
            with next(get_db()) as db:
                # Parse v201 meter values into v16-compatible format
                sampled_values = self._parse_v201_meter_values(meter_value)

                # Get connector_id from active session
                session = active_sessions.get(self.id, {})
                connector_id = session.get('connector_id', 1)

                # Parse timestamp
                ts = timestamp
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts.replace('Z', ''))

                # Save meter values using existing service
                if sampled_values:
                    OCPPMeterService.add_meter_values(
                        db, self.id, connector_id, ts, sampled_values,
                        session.get('transaction_id')
                    )

                # Energy tracking and limit enforcement (same logic as v16 MeterValues)
                if session and sampled_values:
                    for sample in sampled_values:
                        if sample.get('measurand') == 'Energy.Active.Import.Register':
                            try:
                                current_energy = float(sample['value'])
                                meter_start = session.get('meter_start', 0.0)
                                energy_delivered_wh = current_energy - meter_start
                                energy_delivered_kwh = energy_delivered_wh / 1000.0

                                session['energy_delivered'] = energy_delivered_kwh

                                # Update energy in charging_sessions
                                if session.get('charging_session_id'):
                                    db.execute(text("""
                                        UPDATE charging_sessions
                                        SET energy = :energy_consumed
                                        WHERE id = :session_id AND status = 'started'
                                    """), {
                                        "energy_consumed": energy_delivered_kwh,
                                        "session_id": session['charging_session_id']
                                    })
                                    db.commit()

                                self.logger.info(
                                    f"ENERGY UPDATE v201: {energy_delivered_kwh:.3f} kWh "
                                    f"(session {session.get('charging_session_id')})"
                                )

                                # Limit enforcement
                                await self._check_limits(session, energy_delivered_kwh)

                            except (ValueError, TypeError) as e:
                                self.logger.warning(f"Error processing energy v201: {e}")
                                break

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in TransactionEvent.Updated [{err_cat}]: station={self.id}: {e}", exc_info=True)

        return call_result_201.TransactionEvent(total_cost=0)

    # ========================================================================
    # TransactionEvent.Ended
    # ========================================================================

    async def _handle_transaction_ended(self, v201_tx_id, timestamp, trigger_reason, transaction_info, extra):
        """Equivalent of v16 StopTransaction — finalize session with billing"""
        numeric_tx_id = _v201_tx_id_to_numeric(v201_tx_id)
        reason = trigger_reason or 'Local'

        # Extract final meter value
        meter_value = extra.get('meter_value', [])
        meter_stop = 0
        if meter_value:
            meter_stop = self._extract_energy_from_meter_values(meter_value)

        # If no meter_stop from event, try to calculate from session
        session = active_sessions.get(self.id, {})
        if meter_stop == 0 and session:
            meter_stop = session.get('meter_start', 0) + (session.get('energy_delivered', 0) * 1000)

        self.logger.info(
            f"TransactionEvent.Ended: v201_tx={v201_tx_id}, numeric_tx={numeric_tx_id}, "
            f"meter_stop={meter_stop}, reason={reason}"
        )

        try:
            with next(get_db()) as db:
                # Get transaction info for connector
                transaction = db.query(OCPPTransaction).filter(
                    OCPPTransaction.station_id == self.id,
                    OCPPTransaction.transaction_id == numeric_tx_id
                ).first()

                connector_id = transaction.connector_id if transaction else session.get('connector_id')

                # Parse timestamp
                ts = timestamp
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts.replace('Z', ''))

                # Stop the OCPP transaction
                transaction = OCPPTransactionService.stop_transaction(
                    db, self.id, numeric_tx_id, float(meter_stop), ts, reason
                )

                # Finalize charging session (billing, refund — direct port from v16)
                if transaction and transaction.charging_session_id:
                    self._finalize_charging_session(
                        db, transaction, meter_stop
                    )

                # Release connector
                if connector_id:
                    db.execute(text("""
                        UPDATE connectors
                        SET status = 'available', error_code = 'NoError', last_status_update = NOW()
                        WHERE station_id = :station_id AND connector_number = :connector_id
                    """), {"station_id": self.id, "connector_id": connector_id})

                db.commit()

                # Clear active session
                if self.id in active_sessions:
                    del active_sessions[self.id]

            self.logger.info(
                f"Transaction ended v201: numeric_tx={numeric_tx_id}, "
                f"connector {connector_id} released"
            )

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in TransactionEvent.Ended [{err_cat}]: station={self.id}, tx={v201_tx_id}: {e}", exc_info=True)
            try:
                with next(get_db()) as err_db:
                    OCPPLogService.log_event(
                        db=err_db, station_id=self.id, event_type="TransactionEvent.Ended",
                        direction="inbound", severity="error",
                        error_message=f"[{err_cat}] tx={v201_tx_id}: {e}",
                    )
            except Exception as log_err:
                self.logger.debug(f"Failed to log TransactionEvent.Ended error to DB: {log_err}")

        return call_result_201.TransactionEvent(total_cost=0)

    # ========================================================================
    # Financial finalization — direct port from v16 StopTransaction
    # ========================================================================

    def _finalize_charging_session(self, db, transaction, meter_stop: float):
        """Finalize billing for charging session. Direct port from v16 for safety."""
        session_id = transaction.charging_session_id

        try:
            # Calculate energy consumed (Wh -> kWh)
            energy_consumed = (float(meter_stop) - float(transaction.meter_start)) / 1000.0

            # Get base tariff
            tariff_result = db.execute(text("""
                SELECT price_per_kwh FROM stations WHERE id = :station_id
            """), {"station_id": self.id}).fetchone()
            base_rate = float(tariff_result[0]) if tariff_result and tariff_result[0] else 12.0

            # Apply night discount if applicable
            rate_per_kwh, is_night = PricingService.get_effective_rate(base_rate)
            if is_night:
                self.logger.info(f"Night tariff applied: {base_rate} -> {rate_per_kwh} som/kWh")

            actual_cost = energy_consumed * rate_per_kwh

            # Get session data
            session_result = db.execute(text("""
                SELECT user_id, reserved_amount, payment_processed, status
                FROM charging_sessions WHERE id = :session_id
            """), {"session_id": session_id}).fetchone()

            if not session_result:
                return

            user_id = session_result[0]
            reserved_amount = float(session_result[1]) if session_result[1] else 0
            payment_processed = session_result[2] or False
            session_status = session_result[3]

            # Double-payment protection
            if payment_processed:
                self.logger.info(
                    f"Session {session_id} already processed (payment_processed=True), "
                    f"skipping financial operations"
                )
                if session_status != 'stopped':
                    db.execute(text("""
                        UPDATE charging_sessions
                        SET stop_time = NOW(), status = 'stopped',
                            energy = :energy_consumed, amount = :actual_cost
                        WHERE id = :session_id
                    """), {
                        "energy_consumed": energy_consumed,
                        "actual_cost": actual_cost,
                        "session_id": session_id
                    })
                return

            # Additional charge if actual_cost exceeds reserved
            if actual_cost > reserved_amount:
                additional_charge = actual_cost - reserved_amount
                self.logger.warning(
                    f"RESERVE EXCEEDED: actual_cost={actual_cost} > reserved={reserved_amount}. "
                    f"Additional charge: {additional_charge} som"
                )

                db.execute(text("""
                    UPDATE clients SET balance = balance - :additional_charge WHERE id = :user_id
                """), {"additional_charge": additional_charge, "user_id": user_id})

                db.execute(text("""
                    INSERT INTO payment_transactions_odengi
                    (client_id, transaction_type, amount, description)
                    VALUES (:client_id, 'balance_topup', :amount, :description)
                """), {
                    "client_id": user_id,
                    "amount": f"-{additional_charge:.2f}",
                    "description": f"Additional charge for session {session_id} (reserve exceeded)"
                })

                refund_amount = 0
            else:
                refund_amount = reserved_amount - actual_cost

            # Refund unused funds
            if refund_amount > 0:
                db.execute(text("""
                    UPDATE clients SET balance = balance + :refund_amount WHERE id = :user_id
                """), {"refund_amount": refund_amount, "user_id": user_id})

                self.logger.info(f"Refund {refund_amount} som to client {user_id}")

            # Update session with actual data + set payment_processed=TRUE
            db.execute(text("""
                UPDATE charging_sessions
                SET stop_time = NOW(), status = 'stopped',
                    energy = :energy_consumed, amount = :actual_cost,
                    payment_processed = TRUE
                WHERE id = :session_id
            """), {
                "energy_consumed": energy_consumed,
                "actual_cost": actual_cost,
                "session_id": session_id
            })

            self.logger.info(
                f"Session {session_id} finalized v201: "
                f"{energy_consumed:.3f} kWh, {actual_cost:.2f} som"
            )

        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error finalizing session [{err_cat}]: station={self.id}, session={session_id}: {e}", exc_info=True)
            try:
                with next(get_db()) as err_db:
                    OCPPLogService.log_event(
                        db=err_db, station_id=self.id, event_type="SessionFinalize",
                        direction="internal", severity="error",
                        error_message=f"[{err_cat}] session={session_id}: {e}",
                    )
                    err_db.execute(text("""
                        UPDATE charging_sessions SET error_details = :err
                        WHERE id = :sid
                    """), {"err": f"[{err_cat}] {e}", "sid": session_id})
                    err_db.commit()
            except Exception as log_err:
                self.logger.debug(f"Failed to log error details to DB: {log_err}")

    # ========================================================================
    # Device Management stubs (v201-only messages)
    # ========================================================================

    @on('NotifyEvent')
    def on_notify_event(self, generated_at, seq_no, event_data, **kwargs):
        """Handle device monitoring events from station."""
        self.logger.info(f"NotifyEvent: seq={seq_no}, events={len(event_data)}")
        try:
            with next(get_db()) as db:
                OCPPLogService.log_event(
                    db, self.id, None, "NotifyEvent", "inbound",
                    request_payload={"seq_no": seq_no, "event_data": event_data}
                )
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in NotifyEvent v201 [{err_cat}]: station={self.id}: {e}", exc_info=True)
        return call_result_201.NotifyEvent()

    @on('NotifyReport')
    def on_notify_report(self, request_id, generated_at, seq_no, **kwargs):
        """Handle configuration/monitoring report data from station."""
        report_data = kwargs.get('report_data', [])
        tbc = kwargs.get('tbc', False)
        self.logger.info(f"NotifyReport: req={request_id}, seq={seq_no}, items={len(report_data)}, tbc={tbc}")
        try:
            with next(get_db()) as db:
                OCPPLogService.log_event(
                    db, self.id, None, "NotifyReport", "inbound",
                    request_payload={"request_id": request_id, "seq_no": seq_no, "items": len(report_data)}
                )
        except Exception as e:
            err_cat = _classify_error(e)
            self.logger.error(f"Error in NotifyReport v201 [{err_cat}]: station={self.id}: {e}", exc_info=True)
        return call_result_201.NotifyReport()

    @on('LogStatusNotification')
    def on_log_status_notification(self, status, **kwargs):
        """Handle log upload status from station."""
        request_id = kwargs.get('request_id')
        self.logger.info(f"LogStatusNotification: status={status}, request_id={request_id}")
        return call_result_201.LogStatusNotification()

    @on('FirmwareStatusNotification')
    def on_firmware_status_notification(self, status, **kwargs):
        """Handle firmware update status from station."""
        request_id = kwargs.get('request_id')
        self.logger.info(f"FirmwareStatusNotification: status={status}, request_id={request_id}")
        return call_result_201.FirmwareStatusNotification()

    # ========================================================================
    # Limit enforcement — same thresholds as v16
    # ========================================================================

    async def _check_limits(self, session: Dict[str, Any], energy_delivered_kwh: float):
        """Check energy/amount limits and send stop command if exceeded.
        Thresholds: energy=95%, amount=95%, unlimited=90%.
        """
        limit_type = session.get('limit_type')
        limit_value = session.get('limit_value')
        transaction_id = session.get('transaction_id')

        if not transaction_id:
            return

        # Energy limit (95% threshold)
        if limit_type == 'energy' and limit_value and energy_delivered_kwh >= limit_value * 0.95:
            self.logger.warning(
                f"ENERGY LIMIT (95%): {energy_delivered_kwh:.3f} >= "
                f"{limit_value * 0.95:.3f} kWh. Stopping!"
            )
            await redis_manager.publish_command(self.id, {
                "action": "RemoteStopTransaction",
                "transaction_id": transaction_id,
                "reason": "EnergyLimitReached"
            })
            return

        # Amount limit (95% threshold) with night tariff support
        if limit_type == 'amount' and limit_value:
            session_id = session.get('charging_session_id')
            if session_id:
                try:
                    with next(get_db()) as check_db:
                        tariff_result = check_db.execute(text("""
                            SELECT price_per_kwh FROM stations WHERE id = :station_id
                        """), {"station_id": self.id}).fetchone()
                        base_rate = float(tariff_result[0]) if tariff_result and tariff_result[0] else 12.0

                        rate_per_kwh, is_night = PricingService.get_effective_rate(base_rate)
                        current_cost = energy_delivered_kwh * rate_per_kwh
                        limit_amount = float(limit_value)
                        stop_threshold = limit_amount * 0.95

                        if current_cost >= stop_threshold:
                            night_info = " (night tariff)" if is_night else ""
                            self.logger.warning(
                                f"AMOUNT LIMIT{night_info}: {current_cost:.2f} >= "
                                f"95% of {limit_amount} som. STOP!"
                            )
                            await redis_manager.publish_command(self.id, {
                                "action": "RemoteStopTransaction",
                                "transaction_id": transaction_id,
                                "reason": "AmountLimitReached"
                            })
                except Exception as e:
                    err_cat = _classify_error(e)
                    self.logger.error(f"Error in _check_limits amount [{err_cat}]: station={self.id}: {e}", exc_info=True)
                    try:
                        with next(get_db()) as err_db:
                            OCPPLogService.log_event(
                                db=err_db, station_id=self.id, event_type="LimitCheck",
                                direction="internal", severity="error",
                                error_message=f"[{_classify_error(e)}] amount limit: {e}",
                            )
                    except Exception as log_err:
                        self.logger.debug(f"Failed to log amount limit error to DB: {log_err}")
            return

        # Unlimited charging (90% threshold)
        if limit_type is None or limit_type == 'none':
            session_id = session.get('charging_session_id')
            if session_id:
                try:
                    with next(get_db()) as check_db:
                        session_result = check_db.execute(text("""
                            SELECT cs.user_id, cs.amount, s.price_per_kwh
                            FROM charging_sessions cs
                            JOIN stations s ON cs.station_id = s.id
                            WHERE cs.id = :session_id
                        """), {"session_id": session_id}).fetchone()

                        if session_result:
                            user_id, reserved_amount, base_rate = session_result
                            base_rate = float(base_rate) if base_rate else 12.0

                            rate_per_kwh, is_night = PricingService.get_effective_rate(base_rate)
                            current_cost = energy_delivered_kwh * rate_per_kwh
                            reserved_amount_float = float(reserved_amount)
                            stop_threshold = reserved_amount_float * 0.90

                            if current_cost >= stop_threshold:
                                night_info = " (night tariff)" if is_night else ""
                                self.logger.warning(
                                    f"UNLIMITED (90%){night_info}: {current_cost:.2f} >= "
                                    f"90% of {reserved_amount_float} som. STOP!"
                                )
                                await redis_manager.publish_command(self.id, {
                                    "action": "RemoteStopTransaction",
                                    "transaction_id": transaction_id,
                                    "reason": "AmountLimitReached"
                                })
                except Exception as e:
                    err_cat = _classify_error(e)
                    self.logger.error(f"Error in _check_limits funds [{err_cat}]: station={self.id}: {e}", exc_info=True)
                    try:
                        with next(get_db()) as err_db:
                            OCPPLogService.log_event(
                                db=err_db, station_id=self.id, event_type="LimitCheck",
                                direction="internal", severity="error",
                                error_message=f"[{_classify_error(e)}] funds limit: {e}",
                            )
                    except Exception as log_err:
                        self.logger.debug(f"Failed to log funds limit error to DB: {log_err}")

    # ========================================================================
    # Helper: extract energy from v201 meter values
    # ========================================================================

    def _extract_energy_from_meter_values(self, meter_value) -> float:
        """Extract Energy.Active.Import.Register from v201 meter_value list.
        Returns value in Wh (same unit as v16 meter_start/meter_stop).
        """
        if not meter_value:
            return 0.0

        for mv in meter_value:
            sampled_values = mv.get('sampled_value', []) if isinstance(mv, dict) else []
            for sample in sampled_values:
                measurand = sample.get('measurand', '')
                if measurand == 'Energy.Active.Import.Register' or not measurand:
                    try:
                        return float(sample.get('value', 0))
                    except (ValueError, TypeError):
                        continue
        return 0.0

    # ========================================================================
    # Helper: parse v201 meter values to v16-compatible format
    # ========================================================================

    def _parse_v201_meter_values(self, meter_value) -> list:
        """Convert v201 meter_value format to v16-compatible sampled_values list
        for use with OCPPMeterService.add_meter_values().
        """
        sampled_values = []
        if not meter_value:
            return sampled_values

        for mv in meter_value:
            if not isinstance(mv, dict):
                continue
            sv_list = mv.get('sampled_value', [])
            for sample in sv_list:
                if not isinstance(sample, dict):
                    continue
                sampled_values.append({
                    'measurand': sample.get('measurand', ''),
                    'value': sample.get('value'),
                    'unit': sample.get('unit_of_measure', {}).get('unit', '') if isinstance(sample.get('unit_of_measure'), dict) else sample.get('unit', ''),
                    'context': sample.get('context', ''),
                    'format': '',
                    'location': sample.get('location', ''),
                })

        return sampled_values
