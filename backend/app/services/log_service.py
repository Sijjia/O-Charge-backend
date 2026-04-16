"""
OCPP Event Log Service — запись и чтение OCPP событий из БД.
P1 #10: Логирование для админки (фильтрация по станции, типу, времени, severity).
"""
import logging
import os
from datetime import datetime
from typing import Optional, Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class OCPPLogService:
    """Сервис логирования OCPP событий в БД."""

    @staticmethod
    def log_event(
        db: Session,
        station_id: str,
        event_type: str,
        direction: str = "inbound",
        connector_id: Optional[int] = None,
        message_id: Optional[str] = None,
        request_payload: Optional[dict] = None,
        response_payload: Optional[dict] = None,
        severity: str = "info",
        processing_time_ms: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """INSERT OCPP события в ocpp_event_logs + broadcast подписчикам SSE."""
        # В STRESS_TEST_MODE пропускаем INSERT в БД для LOAD-SIM станций (broadcast SSE оставляем)
        _stress_skip_db = (
            os.environ.get("STRESS_TEST_MODE", "0") == "1"
            and station_id.startswith("LOAD-SIM")
        )
        try:
            if not _stress_skip_db:
                db.execute(
                    text("""
                        INSERT INTO ocpp_event_logs
                        (station_id, connector_id, event_type, direction,
                         message_id, request_payload, response_payload,
                         severity, processing_time_ms, error_message)
                    VALUES
                        (:station_id, :connector_id, :event_type, :direction,
                         :message_id, CAST(:request_payload AS jsonb), CAST(:response_payload AS jsonb),
                         :severity, :processing_time_ms, :error_message)
                    """),
                    {
                        "station_id": station_id,
                        "connector_id": connector_id,
                        "event_type": event_type,
                        "direction": direction,
                        "message_id": message_id,
                        "request_payload": _to_json(request_payload),
                        "response_payload": _to_json(response_payload),
                        "severity": severity,
                        "processing_time_ms": processing_time_ms,
                        "error_message": error_message,
                    },
                )
                db.commit()
        except Exception as e:
            logger.error(f"Failed to log OCPP event: {e}", exc_info=True)
            try:
                db.rollback()
            except Exception as e:
                logger.debug(f"Rollback failed in log_event: {e}")

        # Broadcast to per-station SSE subscribers (non-blocking)
        try:
            from app.api.v1.admin.logs import broadcast_station_event
            broadcast_station_event(station_id, {
                "id": str(datetime.now().timestamp()),
                "timestamp": datetime.now().isoformat(),
                "station_id": station_id,
                "connector_id": connector_id,
                "event_type": event_type,
                "direction": direction,
                "severity": severity,
                "request_payload": request_payload,
                "response_payload": response_payload,
                "processing_time_ms": processing_time_ms,
                "error_message": error_message,
            })
        except Exception:
            pass  # SSE broadcast is best-effort

    @staticmethod
    def get_logs(
        db: Session,
        station_id: Optional[str] = None,
        event_type: Optional[str] = None,
        direction: Optional[str] = None,
        severity: Optional[str] = None,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        """SELECT с фильтрацией и пагинацией для Admin API."""
        conditions = []
        params: dict[str, Any] = {}

        if station_id:
            conditions.append("station_id = :station_id")
            params["station_id"] = station_id
        if event_type:
            conditions.append("event_type = :event_type")
            params["event_type"] = event_type
        if direction:
            conditions.append("direction = :direction")
            params["direction"] = direction
        if severity:
            conditions.append("severity = :severity")
            params["severity"] = severity
        if from_dt:
            conditions.append("created_at >= :from_dt")
            params["from_dt"] = from_dt
        if to_dt:
            conditions.append("created_at <= :to_dt")
            params["to_dt"] = to_dt
        if search:
            conditions.append(
                "(error_message ILIKE :search OR request_payload::text ILIKE :search)"
            )
            params["search"] = f"%{search}%"

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        # Total count
        count_row = db.execute(
            text(f"SELECT COUNT(*) FROM ocpp_event_logs {where}"), params
        ).scalar()

        # Data
        offset = (page - 1) * per_page
        params["limit"] = per_page
        params["offset"] = offset

        rows = db.execute(
            text(f"""
                SELECT id, station_id, connector_id, event_type, direction,
                       message_id, request_payload, response_payload,
                       severity, processing_time_ms, error_message, created_at
                FROM ocpp_event_logs
                {where}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        ).fetchall()

        items = [
            {
                "id": str(r[0]),
                "station_id": r[1],
                "connector_id": r[2],
                "event_type": r[3],
                "direction": r[4],
                "message_id": r[5],
                "request_payload": r[6],
                "response_payload": r[7],
                "severity": r[8],
                "processing_time_ms": r[9],
                "error_message": r[10],
                "created_at": r[11].isoformat() if r[11] else None,
            }
            for r in rows
        ]

        return {
            "items": items,
            "total": count_row or 0,
            "page": page,
            "per_page": per_page,
            "pages": max(1, -(-count_row // per_page)) if count_row else 1,
        }

    @staticmethod
    def get_stats(
        db: Session,
        station_id: Optional[str] = None,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> dict:
        """Агрегированная статистика по event_type и severity."""
        conditions = []
        params: dict[str, Any] = {}

        if station_id:
            conditions.append("station_id = :station_id")
            params["station_id"] = station_id
        if from_dt:
            conditions.append("created_at >= :from_dt")
            params["from_dt"] = from_dt
        if to_dt:
            conditions.append("created_at <= :to_dt")
            params["to_dt"] = to_dt

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        # По event_type
        by_type = db.execute(
            text(f"""
                SELECT event_type, COUNT(*) as cnt
                FROM ocpp_event_logs {where}
                GROUP BY event_type ORDER BY cnt DESC
            """),
            params,
        ).fetchall()

        # По severity
        by_severity = db.execute(
            text(f"""
                SELECT severity, COUNT(*) as cnt
                FROM ocpp_event_logs {where}
                GROUP BY severity ORDER BY cnt DESC
            """),
            params,
        ).fetchall()

        # Total
        total = db.execute(
            text(f"SELECT COUNT(*) FROM ocpp_event_logs {where}"), params
        ).scalar()

        return {
            "total": total or 0,
            "by_event_type": {r[0]: r[1] for r in by_type},
            "by_severity": {r[0]: r[1] for r in by_severity},
        }


def _to_json(obj: Any) -> Optional[str]:
    """Конвертация dict в JSON строку для JSONB параметра."""
    if obj is None:
        return None
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)
