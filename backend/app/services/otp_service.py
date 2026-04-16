"""
OTP Service
Сервис для генерации и верификации OTP кодов
"""
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.wappi_service import wappi_service
from app.services.sms_service import sms_service

logger = logging.getLogger(__name__)


class OTPService:
    """Сервис для работы с OTP кодами"""

    # Лимиты для защиты от SMS-бомбинга
    MAX_OTP_PER_PHONE_PER_DAY = 10
    MAX_OTP_PER_IP_PER_MINUTE = 10

    def __init__(self):
        self.code_length = settings.OTP_CODE_LENGTH
        self.ttl_seconds = settings.OTP_TTL_SECONDS
        self.max_attempts = settings.OTP_MAX_ATTEMPTS
        self.rate_limit_seconds = settings.OTP_RATE_LIMIT_SECONDS

    def _generate_code(self) -> str:
        """Генерация случайного 6-значного кода"""
        # Используем secrets для криптографически безопасной генерации
        return "".join(secrets.choice("0123456789") for _ in range(self.code_length))

    def _normalize_phone(self, phone: str) -> str:
        """Нормализация номера телефона"""
        # Убираем пробелы и дефисы
        phone = "".join(c for c in phone if c.isdigit() or c == "+")
        # Добавляем + если нет
        if not phone.startswith("+"):
            phone = "+" + phone
        return phone

    async def check_rate_limit(self, db: AsyncSession, phone: str) -> Tuple[bool, Optional[int]]:
        """
        Проверка rate limit для номера телефона

        Returns:
            (can_send, seconds_until_next): можно ли отправить и сколько секунд до следующей отправки
        """
        phone = self._normalize_phone(phone)

        query = text("""
            SELECT created_at
            FROM otp_codes
            WHERE phone = :phone
            ORDER BY created_at DESC
            LIMIT 1
        """)

        result = await db.execute(query, {"phone": phone})
        row = result.fetchone()

        if not row:
            return True, None

        last_created = row[0]
        if last_created.tzinfo is None:
            last_created = last_created.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        elapsed = (now - last_created).total_seconds()

        if elapsed < self.rate_limit_seconds:
            remaining = int(self.rate_limit_seconds - elapsed)
            return False, remaining

        return True, None

    async def check_daily_limit(self, db: AsyncSession, phone: str) -> Tuple[bool, Optional[str]]:
        """
        Проверка дневного лимита OTP на номер телефона.
        Защита от SMS-бомбинга одного номера.

        Returns:
            (can_send, error_message): можно ли отправить
        """
        query = text("""
            SELECT COUNT(*)
            FROM otp_codes
            WHERE phone = :phone
              AND created_at > NOW() - INTERVAL '24 hours'
        """)
        result = await db.execute(query, {"phone": phone})
        count = result.scalar() or 0

        if count >= self.MAX_OTP_PER_PHONE_PER_DAY:
            logger.warning(f"[OTP] Daily limit reached for {phone}: {count}/{self.MAX_OTP_PER_PHONE_PER_DAY}")
            return False, "Превышен дневной лимит кодов. Попробуйте завтра."

        return True, None

    async def check_ip_rate_limit(self, client_ip: str) -> Tuple[bool, Optional[str]]:
        """
        Проверка rate limit по IP адресу через Redis.
        Защита от массовой рассылки OTP с одного IP (SMS-бомбинг).

        Returns:
            (can_send, error_message): можно ли отправить
        """
        try:
            from ocpp_ws_server.redis_manager import redis_manager
            if not redis_manager.redis:
                return True, None  # fail-open если Redis недоступен

            import time
            now = time.time()
            key = f"otp_ip_limit:{client_ip}"
            window = 60  # 1 минута

            await redis_manager.redis.zremrangebyscore(key, '-inf', now - window)
            count = await redis_manager.redis.zcard(key)

            if count >= self.MAX_OTP_PER_IP_PER_MINUTE:
                logger.warning(f"[OTP] IP rate limit reached for {client_ip}: {count}/{self.MAX_OTP_PER_IP_PER_MINUTE}")
                return False, "Слишком много запросов. Подождите минуту."

            import random
            member = f"{now}:{random.randint(0, 999999)}"
            await redis_manager.redis.zadd(key, {member: now})
            await redis_manager.redis.expire(key, window + 10)

            return True, None
        except Exception as e:
            logger.error(f"[OTP] IP rate limit check error: {e}. Allowing (fail-open)")
            return True, None

    async def create(
        self, db: AsyncSession, phone: str, purpose: str = "auth",
        channel: str = "whatsapp", client_ip: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Создать и отправить OTP код

        Args:
            db: Сессия базы данных
            phone: Номер телефона
            purpose: Цель OTP (auth, phone_change)
            channel: Канал отправки ("sms" или "whatsapp")
            client_ip: IP адрес клиента (для IP rate limiting)

        Returns:
            (success, message): Успех и сообщение
        """
        phone = self._normalize_phone(phone)

        # Проверка IP rate limit (защита от SMS-бомбинга)
        if client_ip:
            ip_ok, ip_msg = await self.check_ip_rate_limit(client_ip)
            if not ip_ok:
                return False, ip_msg

        # Проверка rate limit по номеру (1 код в 60 сек)
        can_send, wait_seconds = await self.check_rate_limit(db, phone)
        if not can_send:
            return False, f"Подождите {wait_seconds} секунд перед повторной отправкой"

        # Проверка дневного лимита (max 10 кодов в день на номер)
        daily_ok, daily_msg = await self.check_daily_limit(db, phone)
        if not daily_ok:
            return False, daily_msg

        # Генерация кода
        code = self._generate_code()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)

        # Сохранение в БД
        try:
            insert_query = text("""
                INSERT INTO otp_codes (id, phone, code, purpose, max_attempts, expires_at, created_at)
                VALUES (:id, :phone, :code, :purpose, :max_attempts, :expires_at, NOW())
            """)

            await db.execute(
                insert_query,
                {
                    "id": str(uuid4()),
                    "phone": phone,
                    "code": code,
                    "purpose": purpose,
                    "max_attempts": self.max_attempts,
                    "expires_at": expires_at,
                },
            )
            await db.commit()

            # Отправка через выбранный канал
            if channel == "sms":
                sent = await sms_service.send_otp(phone, code)
                channel_label = "SMS"
            else:
                sent = await wappi_service.send_otp(phone, code)
                channel_label = "WhatsApp"

            if sent:
                logger.info(f"[OTP] Код отправлен на {phone} через {channel_label}")
                return True, f"Код отправлен через {channel_label}"
            else:
                logger.warning(f"[OTP] Не удалось отправить код на {phone} через {channel_label}")
                return True, f"Код создан (проверьте {channel_label})"

        except Exception as e:
            await db.rollback()
            logger.exception(f"[OTP] Ошибка создания кода: {e}")
            return False, "Ошибка при создании кода"

    async def verify(
        self, db: AsyncSession, phone: str, code: str, purpose: str = "auth"
    ) -> Tuple[bool, str]:
        """
        Проверить OTP код

        Args:
            db: Сессия базы данных
            phone: Номер телефона
            code: Введенный код
            purpose: Цель OTP

        Returns:
            (success, message): Успех и сообщение
        """
        phone = self._normalize_phone(phone)

        # Найти действительный код
        query = text("""
            SELECT id, code, attempts, max_attempts, expires_at
            FROM otp_codes
            WHERE phone = :phone
              AND purpose = :purpose
              AND verified_at IS NULL
              AND expires_at > NOW()
            ORDER BY created_at DESC
            LIMIT 1
        """)

        result = await db.execute(query, {"phone": phone, "purpose": purpose})
        row = result.fetchone()

        if not row:
            return False, "Код не найден или истек. Запросите новый код."

        otp_id, stored_code, attempts, max_attempts, expires_at = row

        # Проверка количества попыток
        if attempts >= max_attempts:
            return False, "Превышено количество попыток. Запросите новый код."

        # Увеличиваем счетчик попыток
        update_attempts = text("""
            UPDATE otp_codes
            SET attempts = attempts + 1
            WHERE id = :id
        """)
        await db.execute(update_attempts, {"id": otp_id})

        # Проверка кода (constant-time comparison для защиты от timing attacks)
        if not hmac.compare_digest(code, stored_code):
            remaining = max_attempts - attempts - 1
            await db.commit()
            if remaining > 0:
                return False, f"Неверный код. Осталось попыток: {remaining}"
            else:
                return False, "Неверный код. Превышено количество попыток."

        # Код верный - помечаем как использованный
        mark_verified = text("""
            UPDATE otp_codes
            SET verified_at = NOW()
            WHERE id = :id
        """)
        await db.execute(mark_verified, {"id": otp_id})
        await db.commit()

        logger.info(f"[OTP] Код успешно верифицирован для {phone}")
        return True, "Код подтвержден"

    async def cleanup_expired(self, db: AsyncSession) -> int:
        """
        Удалить истекшие OTP коды

        Returns:
            Количество удаленных записей
        """
        try:
            # Удаляем коды старше 24 часов (независимо от статуса)
            delete_query = text("""
                DELETE FROM otp_codes
                WHERE created_at < NOW() - INTERVAL '24 hours'
            """)

            result = await db.execute(delete_query)
            await db.commit()

            deleted = result.rowcount
            if deleted > 0:
                logger.info(f"[OTP] Удалено {deleted} истекших кодов")

            return deleted

        except Exception as e:
            await db.rollback()
            logger.exception(f"[OTP] Ошибка очистки: {e}")
            return 0


# Singleton instance
otp_service = OTPService()
