"""
Менеджер для автоматического управления статусами станций
Обновляет статусы на основе heartbeat для корректного отображения в PWA
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# Import push service for notifications
from app.services.push_service import push_service, get_station_owner_id
from app.services.webhook_service import webhook_service

class StationStatusManager:
    """Управление статусами станций на основе heartbeat"""
    
    # Таймауты в минутах
    HEARTBEAT_TIMEOUT_MINUTES = 5  # Станция считается offline после 5 минут без heartbeat
    WARNING_TIMEOUT_MINUTES = 3    # Предупреждение после 3 минут
    
    @staticmethod
    def update_all_station_statuses(db: Session) -> Dict[str, List[str]]:
        """
        Обновляет статусы всех станций на основе последнего heartbeat
        
        Returns:
            Dict со списками станций по категориям изменений
        """
        result = {
            "activated": [],
            "deactivated": [],
            "warning": [],
            "total_active": 0,
            "total_inactive": 0
        }
        
        try:
            # 1. Делаем недоступными станции без heartbeat более 5 минут
            deactivate_query = text("""
                UPDATE stations s
                SET is_available = false, updated_at = NOW()
                FROM ocpp_station_status oss
                WHERE s.id = oss.station_id
                  AND s.is_available = true
                  AND (oss.last_heartbeat IS NULL OR oss.last_heartbeat < NOW() - INTERVAL :timeout)
                RETURNING s.id, s.serial_number
            """)
            
            deactivated = db.execute(
                deactivate_query,
                {"timeout": f"{StationStatusManager.HEARTBEAT_TIMEOUT_MINUTES} minutes"}
            ).fetchall()
            
            result["deactivated"] = [(s[0], s[1]) for s in deactivated]
            
            # 2. Делаем доступными станции с недавним heartbeat
            activate_query = text("""
                UPDATE stations s
                SET is_available = true, updated_at = NOW()
                FROM ocpp_station_status oss
                WHERE s.id = oss.station_id
                  AND s.is_available = false
                  AND oss.last_heartbeat >= NOW() - INTERVAL :timeout
                RETURNING s.id, s.serial_number
            """)
            
            activated = db.execute(
                activate_query,
                {"timeout": f"{StationStatusManager.HEARTBEAT_TIMEOUT_MINUTES} minutes"}
            ).fetchall()
            
            result["activated"] = [(s[0], s[1]) for s in activated]
            
            # 3. Делаем недоступными станции без записи в ocpp_station_status
            no_status_query = text("""
                UPDATE stations
                SET is_available = false, updated_at = NOW()
                WHERE is_available = true
                  AND id NOT IN (
                    SELECT station_id 
                    FROM ocpp_station_status 
                    WHERE last_heartbeat >= NOW() - INTERVAL :timeout
                  )
                RETURNING id, serial_number
            """)
            
            no_status = db.execute(
                no_status_query,
                {"timeout": f"{StationStatusManager.HEARTBEAT_TIMEOUT_MINUTES} minutes"}
            ).fetchall()
            
            result["deactivated"].extend([(s[0], s[1]) for s in no_status])
            
            # 4. Находим станции с предупреждением (3-5 минут без heartbeat)
            warning_query = text("""
                SELECT s.id, s.serial_number, 
                       EXTRACT(EPOCH FROM (NOW() - oss.last_heartbeat))/60 as minutes_ago
                FROM stations s
                JOIN ocpp_station_status oss ON s.id = oss.station_id
                WHERE s.status = 'active'
                  AND oss.last_heartbeat < NOW() - INTERVAL :warning
                  AND oss.last_heartbeat >= NOW() - INTERVAL :timeout
            """)
            
            warnings = db.execute(
                warning_query,
                {
                    "warning": f"{StationStatusManager.WARNING_TIMEOUT_MINUTES} minutes",
                    "timeout": f"{StationStatusManager.HEARTBEAT_TIMEOUT_MINUTES} minutes"
                }
            ).fetchall()
            
            result["warning"] = [(s[0], s[1], round(s[2], 1)) for s in warnings]
            
            # 5. Получаем статистику
            stats_query = text("""
                SELECT 
                    COUNT(*) FILTER (WHERE status = 'active') as active_count,
                    COUNT(*) FILTER (WHERE status = 'inactive') as inactive_count
                FROM stations
            """)
            
            stats = db.execute(stats_query).fetchone()
            result["total_active"] = stats[0]
            result["total_inactive"] = stats[1]
            
            # Коммитим изменения
            db.commit()
            
            # Логирование изменений
            if result["deactivated"]:
                logger.warning(f"🔴 Деактивированы станции (нет heartbeat): {[s[1] for s in result['deactivated']]}")

                # Push notifications владельцам об offline станциях (graceful degradation)
                for station_id, serial_number in result["deactivated"]:
                    try:
                        owner_id = get_station_owner_id(db, station_id)
                        if owner_id:
                            import asyncio
                            # Получаем время когда станция ушла в offline
                            offline_since_query = text("""
                                SELECT last_heartbeat
                                FROM ocpp_station_status
                                WHERE station_id = :station_id
                            """)
                            offline_result = db.execute(offline_since_query, {"station_id": station_id}).fetchone()
                            offline_since = offline_result[0].isoformat() if offline_result and offline_result[0] else None

                            asyncio.create_task(
                                push_service.send_to_owner(
                                    db=db,
                                    owner_id=owner_id,
                                    event_type="station_offline",
                                    station_id=station_id,
                                    station_name=serial_number,
                                    offline_since=offline_since
                                )
                            )
                            logger.info(f"Push notification scheduled for owner {owner_id} (station {serial_number} offline)")
                    except Exception as e:
                        logger.warning(f"Failed to send station offline push for {station_id}: {e}")

                    # Webhook: station offline
                    try:
                        webhook_service.dispatch_event("station.offline", {
                            "station_id": station_id,
                            "serial_number": serial_number,
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                        })
                    except Exception as e:
                        logger.warning(f"Failed to dispatch station.offline webhook for {station_id}: {e}")

            if result["activated"]:
                logger.info(f"🟢 Активированы станции: {[s[1] for s in result['activated']]}")
                for station_id, serial_number in result["activated"]:
                    try:
                        webhook_service.dispatch_event("station.online", {
                            "station_id": station_id,
                            "serial_number": serial_number,
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                        })
                    except Exception as e:
                        logger.warning(f"Failed to dispatch station.online webhook for {station_id}: {e}")

            if result["warning"]:
                logger.warning(f"⚠️ Станции близки к деактивации: {[(s[1], f'{s[2]}мин') for s in result['warning']]}")

            logger.info(f"📊 Статус станций - Активные: {result['total_active']}, Неактивные: {result['total_inactive']}")
            
        except Exception as e:
            logger.error(f"Ошибка обновления статусов станций: {e}", exc_info=True)
            db.rollback()
            raise
        
        return result
    
    @staticmethod
    def get_station_health_status(db: Session, station_id: str) -> Dict:
        """
        Получает детальный статус здоровья станции
        
        Args:
            db: Сессия БД
            station_id: ID станции
            
        Returns:
            Dict с информацией о состоянии станции
        """
        query = text("""
            SELECT 
                s.id,
                s.serial_number,
                s.status,
                oss.last_heartbeat,
                oss.is_online,
                EXTRACT(EPOCH FROM (NOW() - oss.last_heartbeat))/60 as minutes_since_heartbeat,
                CASE 
                    WHEN oss.last_heartbeat IS NULL THEN 'never_connected'
                    WHEN oss.last_heartbeat < NOW() - INTERVAL '5 minutes' THEN 'offline'
                    WHEN oss.last_heartbeat < NOW() - INTERVAL '3 minutes' THEN 'warning'
                    ELSE 'online'
                END as health_status,
                (SELECT COUNT(*) FROM connectors WHERE station_id = s.id AND status = 'available') as available_connectors,
                (SELECT COUNT(*) FROM connectors WHERE station_id = s.id) as total_connectors
            FROM stations s
            LEFT JOIN ocpp_station_status oss ON s.id = oss.station_id
            WHERE s.id = :station_id
        """)
        
        result = db.execute(query, {"station_id": station_id}).fetchone()
        
        if not result:
            return None
        
        return {
            "station_id": result[0],
            "serial_number": result[1],
            "status": result[2],
            "last_heartbeat": result[3].isoformat() if result[3] else None,
            "is_online": result[4],
            "minutes_since_heartbeat": round(result[5], 1) if result[5] else None,
            "health_status": result[6],
            "available_connectors": result[7],
            "total_connectors": result[8],
            "should_be_active": result[6] in ['online', 'warning']
        }
    
    @staticmethod
    def force_deactivate_station(db: Session, station_id: str, reason: str = "manual") -> bool:
        """
        Принудительно деактивирует станцию
        
        Args:
            db: Сессия БД
            station_id: ID станции
            reason: Причина деактивации
            
        Returns:
            True если успешно
        """
        try:
            query = text("""
                UPDATE stations 
                SET status = 'inactive', 
                    updated_at = NOW()
                WHERE id = :station_id
                RETURNING id
            """)
            
            result = db.execute(query, {"station_id": station_id}).fetchone()
            
            if result:
                logger.info(f"⛔ Станция {station_id} принудительно деактивирована. Причина: {reason}")
                db.commit()
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Ошибка деактивации станции {station_id}: {e}", exc_info=True)
            db.rollback()
            return False