"""
OBANK payment service implementation with proper client SSL certificate authentication
"""
import httpx
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional
import asyncio
from datetime import datetime
import uuid
import logging
import ssl
import tempfile
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from app.core.config import settings

logger = logging.getLogger(__name__)

class OBankService:
    def __init__(self):
        self.base_url = settings.current_obank_api_url
        self.point_id = int(settings.current_obank_point_id)
        self.service_id = int(settings.current_obank_service_id)
        self.cert_path = Path(settings.OBANK_CERT_PATH) if settings.OBANK_CERT_PATH else Path(__file__).parent.parent.parent / "certificates" / "obank_client.p12"
        self.cert_password = settings.OBANK_CERT_PASSWORD
        
    def _load_pkcs12_certificate(self):
        """Load PKCS12 certificate and extract cert + key"""
        try:
            if not self.cert_path.exists():
                logger.error(f"🚨 SSL certificate NOT FOUND: {self.cert_path}")
                logger.error(f"🚨 Certificate directory contents:")
                cert_dir = self.cert_path.parent
                if cert_dir.exists():
                    for file in cert_dir.iterdir():
                        logger.error(f"🚨   - {file.name}")
                else:
                    logger.error(f"🚨 Certificate directory does not exist: {cert_dir}")
                raise FileNotFoundError(f"SSL certificate not found: {self.cert_path}")
                
            logger.info(f"✅ SSL certificate found: {self.cert_path}")
            
            with open(self.cert_path, 'rb') as cert_file:
                p12_data = cert_file.read()
            
            logger.info(f"✅ PKCS12 data read: {len(p12_data)} bytes")
            
            # Parse PKCS12
            private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
                p12_data, 
                self.cert_password.encode('utf-8')
            )
            
            logger.info(f"✅ PKCS12 parsed successfully")
            
            # Convert to PEM format
            cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
            key_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            
            logger.info(f"✅ Certificate converted to PEM format")
            
            return cert_pem, key_pem
            
        except Exception as e:
            logger.error(f"🚨 Failed to load PKCS12 certificate: {e}", exc_info=True)
            logger.error(f"🚨 Exception type: {type(e).__name__}")
            raise

    async def _make_request(self, endpoint: str, xml_data: str) -> Dict[str, Any]:
        """
        Make authenticated request to OBANK API with client SSL certificate
        """
        import tempfile
        import os
        
        cert_file_path = None
        key_file_path = None
        
        try:
            logger.info(f"🔍 OBANK request: {self.base_url}{endpoint}")
            logger.info(f"🔍 SSL cert path: {self.cert_path}")
            logger.info(f"🔍 SSL cert exists: {self.cert_path.exists()}")
            
            # Проверяем наличие сертификата (обязательно для PCI DSS Requirement 4.1)
            if not self.cert_path.exists():
                error_msg = (
                    f"🚨 SSL certificate required at {self.cert_path}. "
                    "HTTP fallback disabled for PCI DSS compliance. "
                    "Please configure OBANK_CERT_PATH and OBANK_CERT_PASSWORD."
                )
                logger.error(error_msg)
                raise ValueError(error_msg)

            # Сертификат найден, продолжаем
            if True:
                logger.info(f"✅ SSL certificate found: {self.cert_path}")
                
                # Загружаем сертификат и ключ
                cert_data, key_data = self._load_pkcs12_certificate()
                
                logger.info(f"🔍 SSL cert loaded: {len(cert_data)} bytes")
                logger.info(f"🔍 SSL key loaded: {len(key_data)} bytes")
                
                # ✅ ИСПРАВЛЕНО: Создаём временные файлы для httpx
                with tempfile.NamedTemporaryFile(mode='wb', suffix='.crt', delete=False) as cert_file:
                    cert_file.write(cert_data)
                    cert_file_path = cert_file.name
                    
                with tempfile.NamedTemporaryFile(mode='wb', suffix='.key', delete=False) as key_file:
                    key_file.write(key_data)
                    key_file_path = key_file.name
                
                logger.info(f"🔍 Created temp cert file: {cert_file_path}")
                logger.info(f"🔍 Created temp key file: {key_file_path}")
                
                # ✅ ДИАГНОСТИКА ОТКЛЮЧЕНА: Рабочий endpoint найден - base URL
                logger.info(f"🔍 Using confirmed working endpoint: {self.base_url}{endpoint}")
                
                # Основной запрос с SSL сертификатом
                async with httpx.AsyncClient(
                    cert=(cert_file_path, key_file_path),
                    verify=True,  # SSL verification включен для production безопасности
                    timeout=30.0
                ) as client:
                    response = await client.post(
                        f"{self.base_url}{endpoint}",
                        content=xml_data,
                        headers={
                            "Content-Type": "application/xml; charset=utf-8",
                            "Accept": "application/xml"
                        }
                    )
                    
                    logger.info(f"🔍 OBANK response status: {response.status_code}")
                    logger.info(f"🔍 OBANK response headers: {dict(response.headers)}")
                    logger.info(f"🔍 OBANK response content: '{response.text}'")
                    logger.info(f"🔍 OBANK response length: {len(response.text)} chars")
                    
                    if response.status_code != 200:
                        logger.error(f"❌ OBANK API error: {response.status_code}")
                        logger.error(f"❌ OBANK response: '{response.text}'")
                        logger.error(f"❌ OBANK headers: {dict(response.headers)}")
                        return {"error": f"HTTP {response.status_code}", "details": response.text}
                    
                    return self._parse_xml_response(response.text)
                    
        except Exception as e:
            logger.error(f"❌ OBANK request failed: {str(e)}", exc_info=True)
            return {"error": str(e)}
        finally:
            # ✅ ОЧИСТКА: Удаляем временные файлы
            try:
                if cert_file_path and os.path.exists(cert_file_path):
                    os.unlink(cert_file_path)
                    logger.info(f"🧹 Cleaned up temp cert file: {cert_file_path}")
                if key_file_path and os.path.exists(key_file_path):
                    os.unlink(key_file_path)
                    logger.info(f"🧹 Cleaned up temp key file: {key_file_path}")
            except Exception as cleanup_error:
                logger.warning(f"⚠️ Cleanup failed: {str(cleanup_error)}")

    def _parse_xml_response(self, xml_text: str) -> Dict[str, Any]:
        """Parse XML response from OBANK API"""
        import xml.etree.ElementTree as ET
        
        try:
            root = ET.fromstring(xml_text)
            result = {}
            
            # Парсинг result элемента
            result_elem = root.find("result")
            if result_elem is not None:
                result.update(result_elem.attrib)
                
                # Парсинг data элементов
                data_elem = result_elem.find("data")
                if data_elem is not None:
                    result["data"] = []
                    for input_elem in data_elem.findall("input"):
                        result["data"].append(input_elem.attrib)
            
            return result
        except Exception as e:
            logger.error(f"❌ XML parsing failed: {str(e)}", exc_info=True)
            return {"error": "XML parsing failed", "details": str(e)}
    
    def _create_h2h_xml(self, amount_tyiyn: int, client_id: str, card_data: Dict[str, str]) -> str:
        """Create XML for H2H payment request"""
        transaction_id = int(datetime.now().timestamp())
        current_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0600")
        
        # Используем переданные email и phone или значения по умолчанию
        email = card_data.get('email', 'test@charge.redpay.kg')
        phone = card_data.get('phone', '+996700000000')
        
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<request point="{self.point_id}">
    <payment
        id="{transaction_id}"
        sum="{amount_tyiyn}"
        check="0"
        service="{self.service_id}"
        date="{current_time}"
        account="{card_data['number']}">
        <attribute name="amount_currency" value="417"/>
        <attribute name="notify_url" value="{settings.DOMAIN}/api/payment/obank/notify"/>
        <attribute name="redirect_url" value="{settings.DOMAIN}/payment/success"/>
        <attribute name="card_pan" value="{card_data['number']}"/>
        <attribute name="card_name" value="{card_data['holder_name']}"/>
        <attribute name="card_cvv" value="{card_data['cvv']}"/>
        <attribute name="card_year" value="{card_data['exp_year']}"/>
        <attribute name="card_month" value="{card_data['exp_month']}"/>
        <attribute name="email" value="{email}"/>
        <attribute name="phone_number" value="{phone}"/>
        <attribute name="city" value="BISHKEK"/>
        <attribute name="country_code" value="KGZ"/>
    </payment>
</request>"""
        
        return xml
    
    def _create_token_xml(self, days: int = 14) -> str:
        """Create XML for card tokenization request"""
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<request point="{self.point_id}">
    <advanced service="{self.service_id}" function="stored-cards">
        <attribute name="days" value="{days}"/>
    </advanced>
</request>"""
        
        return xml
    
    def _create_token_payment_xml(self, amount_tyiyn: int, client_id: str, card_token: str) -> str:
        """Create XML for token payment request"""
        transaction_id = int(datetime.now().timestamp())
        current_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0600")
        
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<request point="{self.point_id}">
    <payment
        id="{transaction_id}"
        sum="{amount_tyiyn}"
        check="0"
        service="{self.service_id}"
        date="{current_time}"
        account="">
        <attribute name="amount_currency" value="417"/>
        <attribute name="notify_url" value="{settings.DOMAIN}/api/payment/obank/notify"/>
        <attribute name="redirect_url" value="{settings.DOMAIN}/payment/success"/>
        <attribute name="email" value="test@charge.redpay.kg"/>
        <attribute name="card-token" value="{card_token}"/>
    </payment>
</request>"""
        
        return xml
    
    def _create_status_xml(self, transaction_id: str) -> str:
        """Create XML for status check request"""
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<request point="{self.point_id}">
    <status id="{transaction_id}"/>
</request>"""
        
        return xml

    async def create_h2h_payment(self, amount_kgs: float, client_id: str, card_data: Dict[str, str]) -> Dict[str, Any]:
        """
        Create Host-to-Host card payment
        """
        try:
            amount_tyiyn = int(amount_kgs * 100)  # KGS to tyiyn
            
            xml_data = self._create_h2h_xml(amount_tyiyn, client_id, card_data)
            
            # ✅ PCI DSS COMPLIANT: Логируем только метаданные, БЕЗ данных карт
            # ЗАПРЕЩЕНО логировать: номера карт, CVV, email, phone, holder_name
            logger.info(
                "H2H payment initiated",
                extra={
                    "client_id": client_id,
                    "amount_kgs": amount_kgs,
                    "has_card_data": bool(card_data),
                    "has_email": bool(card_data.get('email')),
                    "has_phone": bool(card_data.get('phone'))
                }
            )
            
            # ✅ ИСПРАВЛЕНО: Используем base URL (диагностика показала что endpoint = "")
            result = await self._make_request("", xml_data)
            
            if "error" not in result:
                logger.info(f"✅ H2H payment successful!")
                
                # ✅ ИСПРАВЛЕНО: Правильные поля для mobile.py
                # OBANK возвращает: id, trans, state, code, final
                # Mobile API ожидает: auth_key, transaction_id, status
                payment_id = result.get("id")  # ID платежа
                trans_id = result.get("trans")  # Транзакционный ID банка
                state = result.get("state", "0")  # Статус платежа
                
                return {
                    "success": True,
                    "auth_key": payment_id,  # ID платежа как auth_key для invoice_id
                    "transaction_id": trans_id,  # Банковский transaction ID
                    "payment_id": payment_id,  # Дублируем для совместимости
                    "status": "processing" if state == "0" else "completed",
                    "message": "H2H payment created successfully",
                    "result": result
                }
            else:
                logger.error(f"❌ H2H payment failed: {result.get('error')}")
                return {
                    "success": False,
                    "auth_key": None,
                    "transaction_id": None,
                    "payment_id": None,
                    "status": "failed",
                    "message": result.get('error', 'H2H payment failed'),
                    "result": result
                }
            
        except Exception as e:
            logger.error(f"H2H payment failed: {str(e)}", exc_info=True)
            return {
                "success": False, 
                "auth_key": None,
                "transaction_id": None,
                "error": str(e)
            }

    async def create_token_payment(self, amount_kgs: float, client_id: str, card_token: str) -> Dict[str, Any]:
        """
        Create payment using saved card token
        """
        try:
            amount_tyiyn = int(amount_kgs * 100)  # KGS to tyiyn
            
            xml_data = self._create_token_payment_xml(amount_tyiyn, client_id, card_token)
            
            # ✅ Используем правильный эндпоинт для токен платежей
            result = await self._make_request("/token-payment", xml_data)
            
            return {
                "success": "error" not in result,
                "payment_id": result.get("id"),
                "status": result.get("state"),
                "result": result
            }
            
        except Exception as e:
            logger.error(f"Token payment failed: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def create_token(self, days: int = 14) -> Dict[str, Any]:
        """
        Create card storage token
        """
        try:
            xml_data = self._create_token_xml(days)
            
            # ✅ Используем правильный эндпоинт для создания токена
            result = await self._make_request("/token-Create", xml_data)
            
            return {
                "success": "error" not in result,
                "result": result
            }
            
        except Exception as e:
            logger.error(f"Token creation failed: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def check_h2h_status(self, transaction_id: str) -> Dict[str, Any]:
        """
        Check H2H payment status
        """
        try:
            xml_data = self._create_status_xml(transaction_id)
            
            # ✅ Используем правильный эндпоинт для проверки статуса H2H
            result = await self._make_request("/h2hstatus", xml_data)
            
            return {
                "success": "error" not in result,
                "status": result.get("state"),
                "final": result.get("final") == "1",
                "result": result
            }
            
        except Exception as e:
            logger.error(f"Status check failed: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

# Global instance
obank_service = OBankService() 