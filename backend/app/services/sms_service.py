"""
Nikita SMS Service (smspro.nikita.kg)
Сервис для отправки SMS через Nikita SMS API
"""
import logging
from typing import Optional
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class NikitaSmsService:
    """Клиент для Nikita SMS API (smspro.nikita.kg)"""

    API_URL = "https://smspro.nikita.kg/api/"

    def __init__(self):
        self.login = settings.NIKITA_SMS_LOGIN
        self.password = settings.NIKITA_SMS_PASSWORD
        self.sender = settings.NIKITA_SMS_SENDER

    @property
    def is_configured(self) -> bool:
        return bool(self.login and self.password and self.sender)

    def _build_xml(self, phone: str, text: str, msg_id: Optional[str] = None) -> str:
        """Формирует XML-запрос для Nikita SMS API"""
        root = Element("message")
        SubElement(root, "id").text = msg_id or str(uuid4())[:32]
        SubElement(root, "login").text = self.login
        SubElement(root, "pwd").text = self.password
        SubElement(root, "sender").text = self.sender
        SubElement(root, "text").text = text

        phones_el = SubElement(root, "phones")
        # Nikita ожидает номер без +
        SubElement(phones_el, "phone").text = phone.lstrip("+")

        return tostring(root, encoding="unicode", xml_declaration=False)

    async def send_sms(self, phone: str, text: str) -> bool:
        """
        Отправить SMS через Nikita SMS API

        Args:
            phone: Номер телефона (+996XXXXXXXXX)
            text: Текст сообщения

        Returns:
            True если SMS отправлена успешно
        """
        if not self.is_configured:
            logger.warning("[NikitaSMS] Сервис не настроен (NIKITA_SMS_LOGIN/PASSWORD/SENDER)")
            if not settings.is_production:
                logger.info(f"[NikitaSMS] DEV MODE - SMS для {phone}: {text}")
                return True
            return False

        xml_body = self._build_xml(phone, text)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.API_URL,
                    content=xml_body,
                    headers={"Content-Type": "application/xml"},
                )

                if response.status_code == 200:
                    body = response.text
                    # Nikita возвращает XML с status
                    if "accepted" in body.lower() or "<status>" in body.lower():
                        logger.info(f"[NikitaSMS] SMS отправлена на {phone}")
                        return True
                    else:
                        logger.error(f"[NikitaSMS] Ответ API: {body}")
                        return False
                else:
                    logger.error(
                        f"[NikitaSMS] HTTP {response.status_code}: {response.text}"
                    )
                    return False

        except httpx.TimeoutException:
            logger.error(f"[NikitaSMS] Таймаут при отправке на {phone}")
            return False
        except Exception as e:
            logger.exception(f"[NikitaSMS] Ошибка при отправке: {e}")
            return False

    async def send_otp(self, phone: str, code: str) -> bool:
        """
        Отправить OTP код через SMS

        Args:
            phone: Номер телефона
            code: 6-значный OTP код

        Returns:
            True если код отправлен успешно
        """
        message = f"Red Petroleum: {code} - kod dlya vhoda. Deystvitelen 5 minut."
        return await self.send_sms(phone, message)


# Singleton
sms_service = NikitaSmsService()
