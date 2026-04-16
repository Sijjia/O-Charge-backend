# redis_manager.py
# Асинхронный менеджер для работы с Redis: хранение подключённых станций и Pub/Sub для команд
import redis.asyncio as redis_async
import redis as redis_sync  # Синхронный клиент для OCPP handlers
import json
import os
import logging
import asyncio
from typing import Optional, Set, Dict, AsyncGenerator

logger = logging.getLogger(__name__)

# Константы
STATION_TTL_SECONDS = 600  # 10 минут TTL для онлайн-статуса станции (как Voltera)
# Heartbeat каждые 5 минут, TTL 10 минут = 2 пропущенных heartbeat до offline


class RedisOcppManager:
    def __init__(self):
        # Получаем настройки из config
        try:
            from app.core.config import settings
            redis_url = settings.REDIS_URL
            redis_password = settings.REDIS_PASSWORD
        except ImportError:
            # Fallback если config недоступен
            redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
            redis_password = os.getenv("REDIS_PASSWORD", None)

        # Логируем конфигурацию без секретных данных
        logger.info(f"Redis manager: Initializing (password: {'Yes' if redis_password else 'No'})")

        # Создаем асинхронное соединение
        self.redis = redis_async.from_url(redis_url, decode_responses=True)

        # Создаем синхронное соединение для OCPP handlers (которые синхронные)
        self.redis_sync = redis_sync.from_url(redis_url, decode_responses=True)
        logger.info("Redis manager: Sync client initialized for OCPP handlers")

        # Активные pubsub подписки (для диагностики и корректного закрытия)
        self._active_pubsubs: Dict[str, redis_async.client.PubSub] = {}

    async def ping(self) -> bool:
        """Проверка соединения с Redis"""
        try:
            result = await self.redis.ping()
            logger.debug(f"Redis ping: {result}")
            return True
        except Exception as e:
            logger.error(f"Redis ping failed: {e}", exc_info=True)
            return False

    # ============================================================
    # СТАНЦИИ: TTL-based регистрация (вместо SET)
    # ============================================================

    async def register_station(self, station_id: str):
        """
        Регистрация станции с TTL.
        Ключ автоматически истечёт через 5 минут если не будет продлён.
        """
        key = f"ocpp:station:{station_id}"
        await self.redis.setex(key, STATION_TTL_SECONDS, "online")
        logger.info(f"✅ Station {station_id} registered (TTL: {STATION_TTL_SECONDS}s)")

    async def refresh_station_ttl(self, station_id: str):
        """
        Продление TTL станции (вызывается при каждом Heartbeat).
        """
        key = f"ocpp:station:{station_id}"
        # Проверяем существование и продлеваем
        exists = await self.redis.exists(key)
        if exists:
            await self.redis.expire(key, STATION_TTL_SECONDS)
            logger.debug(f"🔄 Station {station_id} TTL refreshed")
        else:
            # Станция не была зарегистрирована - регистрируем
            await self.register_station(station_id)

    async def unregister_station(self, station_id: str):
        """
        Явное удаление станции (при disconnect).
        """
        key = f"ocpp:station:{station_id}"
        await self.redis.delete(key)

        # Закрываем pubsub если есть
        if station_id in self._active_pubsubs:
            try:
                pubsub = self._active_pubsubs.pop(station_id)
                await pubsub.unsubscribe()
                await pubsub.close()
            except Exception as e:
                logger.warning(f"Error closing pubsub for {station_id}: {e}")

        logger.info(f"🔌 Station {station_id} unregistered")

    async def is_station_online(self, station_id: str) -> bool:
        """
        Проверка онлайн-статуса станции.
        """
        key = f"ocpp:station:{station_id}"
        return await self.redis.exists(key) == 1

    async def get_stations(self) -> Set[str]:
        """
        Получение списка всех онлайн станций.
        Сканирует ключи ocpp:station:* (работает с TTL подходом).
        """
        stations = set()
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="ocpp:station:*", count=100)
            for key in keys:
                # Извлекаем station_id из ключа "ocpp:station:{station_id}"
                station_id = key.replace("ocpp:station:", "")
                stations.add(station_id)
            if cursor == 0:
                break
        return stations

    # ============================================================
    # PUB/SUB: команды для станций
    # ============================================================

    async def publish_command(self, station_id: str, command: dict) -> int:
        """
        Публикация команды для станции (без retry, как Voltera).

        ВАЖНО: Перед вызовом проверять is_station_online()!
        Команда публикуется один раз. Если 0 подписчиков - логируется ошибка.

        Args:
            station_id: ID станции
            command: Команда для отправки

        Returns:
            Количество подписчиков, получивших сообщение
        """
        import time
        channel = f"ocpp:cmd:{station_id}"
        message = json.dumps(command)
        action = command.get('action', 'unknown')

        # Диагностика активных подписок (как Voltera)
        active_pubsubs = getattr(self, '_active_pubsubs', {})
        has_local_pubsub = station_id in active_pubsubs

        logger.info(f"📊 PUBLISH ДИАГНОСТИКА:")
        logger.info(f"   - station_id запроса: '{station_id}' (тип: {type(station_id).__name__})")
        logger.info(f"   - канал: {channel}")
        logger.info(f"   - активные подписки: {list(active_pubsubs.keys())}")
        logger.info(f"   - station_id в активных: {has_local_pubsub}")

        subscribers = await self.redis.publish(channel, message)
        logger.info(f"📤 Опубликовано в {channel}: {action} (подписчиков: {subscribers})")

        if subscribers == 0:
            # Детальная диагностика при 0 подписчиков
            logger.error(f"❌ 0 ПОДПИСЧИКОВ для {station_id}! Проверка:")
            for sub_station_id in active_pubsubs.keys():
                logger.error(f"   - Подписка '{sub_station_id}' (тип: {type(sub_station_id).__name__})")
                logger.error(f"   - Совпадает с '{station_id}': {sub_station_id == station_id}")
                logger.error(f"   - repr подписки: {repr(sub_station_id)}")
                logger.error(f"   - repr запроса: {repr(station_id)}")

        return subscribers

    async def listen_commands(self, station_id: str) -> AsyncGenerator[dict, None]:
        """
        Подписка на команды для станции.

        ВАЖНО (Вариант B от Voltera): Явное ожидание подтверждения подписки от Redis
        перед добавлением в _active_pubsubs. Это решает race condition когда
        publish() видит 0 подписчиков.
        """
        channel = f"ocpp:cmd:{station_id}"

        pubsub = self.redis.pubsub()
        # НЕ добавляем в _active_pubsubs здесь - ждём подтверждения от Redis!

        try:
            await pubsub.subscribe(channel)
            logger.info(f"📡 Subscribed to commands channel: {channel}")

            # ВАРИАНТ B: Ждём подтверждения подписки от Redis перед продолжением
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=False, timeout=1.0)
                if message and message.get('type') == 'subscribe':
                    logger.info(f"✅ Subscription CONFIRMED by Redis for {channel}")
                    break
                elif message is None:
                    # Timeout - продолжаем ждать
                    logger.debug(f"⏳ Waiting for subscription confirmation for {channel}...")

            # Теперь подписка ТОЧНО активна в Redis - можно добавлять в словарь
            self._active_pubsubs[station_id] = pubsub
            logger.info(f"📊 Added to active pubsubs: {station_id}, total: {list(self._active_pubsubs.keys())}")

            # Теперь слушаем команды
            async for message in pubsub.listen():
                msg_type = message.get("type")

                # Пропускаем служебные сообщения
                if msg_type == "subscribe":
                    continue

                if msg_type == "message":
                    try:
                        command = json.loads(message["data"])
                        logger.info(f"📥 Received command for {station_id}: {command.get('action', 'unknown')}")
                        yield command
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON in command: {e}")
        except asyncio.CancelledError:
            logger.info(f"🛑 Command listener cancelled for {station_id}")
            raise
        except Exception as e:
            logger.error(f"Error in command listener for {station_id}: {e}")
            raise
        finally:
            # Очистка при завершении
            if station_id in self._active_pubsubs:
                del self._active_pubsubs[station_id]
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception as e:
                logger.warning(f"Error cleaning up pubsub for {station_id}: {e}")

    # ============================================================
    # ТРАНЗАКЦИИ: кэширование OCPP транзакций
    # ============================================================

    async def add_transaction(self, station_id: str, transaction: dict):
        key = f"ocpp:transactions:{station_id}"
        await self.redis.rpush(key, json.dumps(transaction))

    async def get_transactions(self, station_id: str = None):
        if station_id:
            key = f"ocpp:transactions:{station_id}"
            txs = await self.redis.lrange(key, 0, -1)
            return [json.loads(tx) for tx in txs]
        else:
            # Получить все ключи транзакций
            keys = await self.redis.keys("ocpp:transactions:*")
            all_txs = []
            for key in keys:
                txs = await self.redis.lrange(key, 0, -1)
                all_txs.extend([json.loads(tx) for tx in txs])
            return all_txs

    # ============================================================
    # КЭШИРОВАНИЕ: общие методы
    # ============================================================

    async def cache_data(self, key: str, value: str, ttl: int = 30):
        """Кэширование данных с TTL"""
        await self.redis.setex(key, ttl, value)

    async def get_cached_data(self, key: str) -> Optional[str]:
        """Получение данных из кэша"""
        return await self.redis.get(key)

    async def delete(self, key: str):
        """Удаление ключа из кэша"""
        await self.redis.delete(key)

    # ============================================================
    # СИНХРОННЫЕ МЕТОДЫ: для использования в OCPP handlers
    # ============================================================

    def get_sync(self, key: str) -> Optional[str]:
        """Синхронное получение данных (для OCPP handlers)"""
        try:
            return self.redis_sync.get(key)
        except Exception as e:
            logger.error(f"Redis sync get error for {key}: {e}", exc_info=True)
            return None

    def set_sync(self, key: str, value: str, ttl: int = None):
        """Синхронная запись данных (для OCPP handlers)"""
        try:
            if ttl:
                self.redis_sync.setex(key, ttl, value)
            else:
                self.redis_sync.set(key, value)
        except Exception as e:
            logger.error(f"Redis sync set error for {key}: {e}", exc_info=True)

    def delete_sync(self, key: str):
        """Синхронное удаление ключа (для OCPP handlers)"""
        try:
            self.redis_sync.delete(key)
        except Exception as e:
            logger.error(f"Redis sync delete error for {key}: {e}", exc_info=True)

    # ============================================================
    # GRACEFUL SHUTDOWN: сохранение/восстановление состояния
    # ============================================================

    async def save_active_sessions(self, sessions: dict):
        """Сохранение активных сессий в Redis перед shutdown.

        Сохраняет active_sessions dict из ws_handler.py чтобы при рестарте
        сервер знал о текущих зарядках и мог восстановить мониторинг.
        """
        if not sessions:
            return

        key = "ocpp:active_sessions_backup"
        try:
            await self.redis.setex(key, 600, json.dumps(sessions, default=str))
            logger.info(f"💾 Сохранено {len(sessions)} активных сессий в Redis")
        except Exception as e:
            logger.error(f"Ошибка сохранения активных сессий: {e}")

    async def restore_active_sessions(self) -> dict:
        """Восстановление активных сессий из Redis после рестарта."""
        key = "ocpp:active_sessions_backup"
        try:
            data = await self.redis.get(key)
            if data:
                sessions = json.loads(data)
                await self.redis.delete(key)
                logger.info(f"♻️ Восстановлено {len(sessions)} активных сессий из Redis")
                return sessions
        except Exception as e:
            logger.error(f"Ошибка восстановления активных сессий: {e}")
        return {}

    async def set_shutdown_flag(self):
        """Установить флаг shutdown — станции могут проверить при reconnect."""
        await self.redis.setex("ocpp:server_restarting", 60, "1")
        logger.info("🔄 Установлен флаг перезапуска сервера (60с TTL)")

    async def clear_shutdown_flag(self):
        """Убрать флаг shutdown после успешного старта."""
        await self.redis.delete("ocpp:server_restarting")
        logger.info("✅ Флаг перезапуска сервера снят")

    # ============================================================
    # ДИАГНОСТИКА
    # ============================================================

    async def get_diagnostics(self) -> dict:
        """
        Диагностическая информация о состоянии Redis.
        """
        try:
            stations = await self.get_stations()
            active_pubsubs = list(self._active_pubsubs.keys())

            return {
                "redis_connected": await self.ping(),
                "online_stations": list(stations),
                "online_stations_count": len(stations),
                "active_pubsubs": active_pubsubs,
                "active_pubsubs_count": len(active_pubsubs)
            }
        except Exception as e:
            logger.error(f"Error getting diagnostics: {e}", exc_info=True)
            return {"error": str(e)}

    # ============================================================
    # PUB/SUB: общие методы для realtime обновлений
    # ============================================================

    async def publish(self, channel: str, message: str):
        """Публикация сообщения в канал"""
        result = await self.redis.publish(channel, message)
        logger.info(f"📢 Published to {channel}, subscribers: {result}")

    async def subscribe_and_listen(self, *channels) -> AsyncGenerator[dict, None]:
        """
        Подписка и прослушивание нескольких каналов через Pub/Sub.
        Используется для WebSocket клиентов (location updates).

        Args:
            *channels: Названия каналов для подписки

        Yields:
            dict: Сообщения с полями 'channel' и 'data'
        """
        pubsub = self.redis.pubsub()
        try:
            await pubsub.subscribe(*channels)
            logger.info(f"📡 Subscribed to channels: {channels}")

            async for message in pubsub.listen():
                logger.debug(f"📨 RAW MESSAGE: {message}")
                if message["type"] == "message":
                    logger.info(f"📩 Pub/Sub message received on {message['channel']}")
                    yield {
                        "channel": message["channel"],
                        "data": message["data"]
                    }
        except asyncio.CancelledError:
            logger.info(f"🛑 Pub/Sub listener cancelled for channels: {channels}")
            raise
        finally:
            try:
                await pubsub.unsubscribe(*channels)
                await pubsub.close()
            except Exception as e:
                logger.warning(f"Error cleaning up pubsub: {e}")


# Глобальный экземпляр менеджера
redis_manager = RedisOcppManager()
