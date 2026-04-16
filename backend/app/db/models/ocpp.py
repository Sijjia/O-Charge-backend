from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Enum as SqlEnum, Boolean, Integer, Text, Numeric, ARRAY, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum
import uuid
from app.db.base_class import Base

# Enums
class UserRole(str, enum.Enum):
    operator = 'operator'
    admin = 'admin'
    superadmin = 'superadmin'

class ClientStatus(str, enum.Enum):
    active = 'active'
    inactive = 'inactive'
    blocked = 'blocked'

class StationStatus(str, enum.Enum):
    active = 'active'
    inactive = 'inactive'
    maintenance = 'maintenance'

class MaintenanceStatus(str, enum.Enum):
    pending = 'pending'
    in_progress = 'in_progress'
    completed = 'completed'
    cancelled = 'cancelled'

class ChargingSessionStatus(str, enum.Enum):
    started = 'started'
    stopped = 'stopped'
    error = 'error'

class LimitType(str, enum.Enum):
    none = 'none'
    energy = 'energy'
    amount = 'amount'

class TariffType(str, enum.Enum):
    per_kwh = 'per_kwh'
    per_minute = 'per_minute'
    session_fee = 'session_fee'
    parking_fee = 'parking_fee'

# Модели
class User(Base):
    __tablename__ = 'users'
    
    id = Column(String, primary_key=True)
    email = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(SqlEnum(UserRole), nullable=False)
    is_active = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    admin_id = Column(String, nullable=True)
    
    # Убираем неправильную связь с charging_sessions (они связаны с clients, не users)

class Client(Base):
    __tablename__ = 'clients'
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=True)  # В БД nullable=True
    phone = Column(String, nullable=True)
    balance = Column(Float, nullable=True, default=0.0)  # ДОБАВЛЕНО: отсутствующее поле
    status = Column(SqlEnum(ClientStatus), nullable=False, default=ClientStatus.active)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    charging_sessions = relationship("ChargingSession", back_populates="client")

class Location(Base):
    __tablename__ = 'locations'

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    city = Column(String, nullable=True)
    country = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    user_id = Column(String, ForeignKey('users.id'), nullable=False)
    partner_id = Column(String, ForeignKey('partners.id'), nullable=True)
    stations_count = Column(Integer, default=0)
    connectors_count = Column(Integer, default=0)
    status = Column(String, default='active')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    stations = relationship("Station", back_populates="location")
    partner = relationship("Partner", back_populates="locations", foreign_keys=[partner_id])

class TariffPlan(Base):
    __tablename__ = 'tariff_plans'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tariff_rules = relationship("TariffRule", back_populates="tariff_plan")
    stations = relationship("Station", back_populates="tariff_plan")

class TariffRule(Base):
    __tablename__ = 'tariff_rules'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tariff_plan_id = Column(String, ForeignKey('tariff_plans.id'), nullable=True)
    name = Column(String, nullable=False)
    tariff_type = Column(SqlEnum(TariffType), default=TariffType.per_kwh, nullable=False)
    connector_type = Column(String, default='ALL')
    power_range_min = Column(Numeric, default=0)
    power_range_max = Column(Numeric, default=1000)
    price = Column(Numeric, nullable=False)
    currency = Column(String, default='KGS')
    time_start = Column(Text, default='00:00:00')
    time_end = Column(Text, default='23:59:59')
    is_weekend = Column(Boolean, default=False)
    priority = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tariff_plan = relationship("TariffPlan", back_populates="tariff_rules")

class Station(Base):
    __tablename__ = 'stations'

    id = Column(String, primary_key=True)
    serial_number = Column(String, nullable=False, unique=True)
    model = Column(String, nullable=False)
    manufacturer = Column(String, nullable=False)
    location_id = Column(String, ForeignKey('locations.id'), nullable=False)
    power_capacity = Column(Float, nullable=False)
    connector_types = Column(ARRAY(String), nullable=False)
    installation_date = Column(String, nullable=True)
    firmware_version = Column(String, nullable=True)
    status = Column(SqlEnum(StationStatus), nullable=False)
    user_id = Column(String, ForeignKey('users.id'), nullable=False)
    partner_id = Column(String, ForeignKey('partners.id'), nullable=True)
    connectors_count = Column(Integer, default=1)
    tariff_plan_id = Column(String, ForeignKey('tariff_plans.id'), nullable=True)
    price_per_kwh = Column(Numeric, default=0)
    session_fee = Column(Numeric, default=0)
    currency = Column(String, default='KGS')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    location = relationship("Location", back_populates="stations")
    tariff_plan = relationship("TariffPlan", back_populates="stations")
    partner = relationship("Partner", back_populates="stations", foreign_keys=[partner_id])
    charging_sessions = relationship("ChargingSession", back_populates="station")
    maintenance_records = relationship("Maintenance", back_populates="station")
    ocpp_status = relationship("OCPPStationStatus", back_populates="station")

class Maintenance(Base):
    __tablename__ = 'maintenance'
    
    id = Column(String, primary_key=True)
    station_id = Column(String, ForeignKey('stations.id'), nullable=False)
    request_date = Column(String, nullable=True)
    description = Column(String, nullable=True)
    assigned_to = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    status = Column(SqlEnum(MaintenanceStatus), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    station = relationship("Station", back_populates="maintenance_records")

class ChargingSession(Base):
    __tablename__ = 'charging_sessions'
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('clients.id'), nullable=False)  # ИСПРАВЛЕНО: clients.id вместо users.id
    station_id = Column(String, ForeignKey('stations.id'), nullable=False)
    start_time = Column(DateTime(timezone=True), server_default=func.now())
    stop_time = Column(DateTime(timezone=True), nullable=True)
    energy = Column(Float, nullable=True)  # kWh
    amount = Column(Float, nullable=True)  # стоимость
    status = Column(SqlEnum(ChargingSessionStatus), default=ChargingSessionStatus.started, nullable=False)
    transaction_id = Column(String, nullable=True)  # OCPP transaction id
    stop_reason = Column(String, nullable=True)  # OCPP stop reason (Local, Remote, etc.)
    error_details = Column(Text, nullable=True)  # Error details for debugging
    limit_type = Column(SqlEnum(LimitType), default=LimitType.none, nullable=False)
    limit_value = Column(Float, nullable=True)  # значение лимита
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships - ИСПРАВЛЕНО: связь с Client, а не User
    client = relationship("Client", back_populates="charging_sessions")
    station = relationship("Station", back_populates="charging_sessions")

class OCPPStationStatus(Base):
    """OCPP Station Status - отслеживание состояния станций в реальном времени"""
    __tablename__ = "ocpp_station_status"

    station_id = Column(String, ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True)
    status = Column(String, nullable=False, default="Available")  # Available, Preparing, Charging, etc.
    error_code = Column(String)  # NoError, ConnectorLockFailure, etc.
    info = Column(String)
    vendor_id = Column(String)
    vendor_error_code = Column(String)
    last_heartbeat = Column(DateTime(timezone=True), server_default=func.now())
    firmware_version = Column(String)
    boot_notification_sent = Column(Boolean, default=False)
    is_online = Column(Boolean, default=False)
    connector_status = Column(JSON, default=[])  # Array of connector statuses
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationship
    station = relationship("Station", back_populates="ocpp_status")

class OCPPTransaction(Base):
    """OCPP Transactions - отслеживание OCPP транзакций"""
    __tablename__ = "ocpp_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, nullable=False)
    station_id = Column(String, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    connector_id = Column(Integer, nullable=False, default=1)
    id_tag = Column(String, nullable=False)  # RFID/NFC tag
    meter_start = Column(Numeric, nullable=False, default=0)
    meter_stop = Column(Numeric)
    start_timestamp = Column(DateTime(timezone=True), nullable=False)
    stop_timestamp = Column(DateTime(timezone=True))
    stop_reason = Column(String)  # EmergencyStop, EVDisconnected, etc.
    charging_session_id = Column(String, ForeignKey("charging_sessions.id"))
    status = Column(String, nullable=False, default="Started")  # Started, Stopped
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    station = relationship("Station")
    charging_session = relationship("ChargingSession")
    meter_values = relationship("OCPPMeterValue", back_populates="transaction")

class OCPPMeterValue(Base):
    """OCPP Meter Values - показания счетчиков"""
    __tablename__ = "ocpp_meter_values"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, nullable=True)  # OCPP transaction_id (not FK)
    ocpp_transaction_id = Column(Integer, ForeignKey("ocpp_transactions.id"), nullable=True)  # FK to OCPPTransaction.id
    station_id = Column(String, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    connector_id = Column(Integer, nullable=False, default=1)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    sampled_values = Column(JSON, nullable=False)  # Raw OCPP data
    energy_active_import_register = Column(Numeric)  # kWh delivered
    power_active_import = Column(Numeric)  # W current power
    current_import = Column(Numeric)  # A current
    voltage = Column(Numeric)  # V voltage
    temperature = Column(Numeric)  # °C temperature
    soc = Column(Numeric)  # % State of Charge
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    station = relationship("Station")
    transaction = relationship("OCPPTransaction", back_populates="meter_values", 
                             foreign_keys=[ocpp_transaction_id])

class OCPPAuthorization(Base):
    """OCPP Authorization - управление RFID/NFC тегами"""
    __tablename__ = "ocpp_authorization"

    id_tag = Column(String, primary_key=True)
    parent_id_tag = Column(String)
    expiry_date = Column(DateTime(timezone=True))
    status = Column(String, nullable=False, default="Accepted")  # Accepted, Blocked, Expired, etc.
    user_id = Column(String, ForeignKey("users.id"), nullable=True)  # Связь с сотрудниками
    client_id = Column(String, ForeignKey("clients.id"), nullable=True)  # ДОБАВЛЕНО: связь с клиентами
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User")
    client = relationship("Client")

class OCPPConfiguration(Base):
    """OCPP Configuration - конфигурация станций"""
    __tablename__ = "ocpp_configuration"

    id = Column(Integer, primary_key=True, autoincrement=True)
    station_id = Column(String, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    key = Column(String, nullable=False)
    value = Column(String)
    readonly = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    station = relationship("Station")

# ============================================================================
# ПАРТНЁРЫ
# ============================================================================

class Partner(Base):
    """Партнёр — владелец станций с revenue share"""
    __tablename__ = 'partners'

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('users.id'), unique=True, nullable=False)
    company_name = Column(String(200), nullable=True)
    inn = Column(String(20), nullable=True)
    contract_number = Column(String(50), nullable=True)
    contract_date = Column(DateTime, nullable=True)
    revenue_share_percent = Column(Numeric(5, 2), default=80.00)
    contact_name = Column(String(100), nullable=True)
    contact_phone = Column(String(20), nullable=True)
    contact_email = Column(String(100), nullable=True)
    status = Column(String(20), default='active')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User")
    locations = relationship("Location", back_populates="partner", foreign_keys="Location.partner_id")
    stations = relationship("Station", back_populates="partner", foreign_keys="Station.partner_id")


# ============================================================================
# ПЛАТЕЖНАЯ СИСТЕМА O!DENGI
# ============================================================================

class PaymentStatus(str, enum.Enum):
    processing = "processing"     # 0 - В процессе оплаты
    approved = "approved"         # 1 - Платеж зачислен/оплачен
    canceled = "canceled"         # 2 - Закончилось время жизни счета или плательщик отменил
    refunded = "refunded"         # 3 - Возврат
    partial_refund = "partial_refund"  # 4 - Частичный возврат

class PaymentType(str, enum.Enum):
    balance_topup = "balance_topup"     # Пополнение баланса
    charge_reserve = "charge_reserve"   # Резерв при зарядке
    charge_refund = "charge_refund"     # Возврат после зарядки
    charge_payment = "charge_payment"   # Доплата за превышение резерва

class BalanceTopup(Base):
    """Пополнения баланса клиентов через O!Dengi"""
    __tablename__ = "balance_topups"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # O!Dengi данные
    invoice_id = Column(String(12), unique=True, index=True)
    order_id = Column(String(128), unique=True, index=True)
    merchant_id = Column(String(32))
    
    # Клиент и сумма
    client_id = Column(String, ForeignKey('clients.id'), nullable=False)
    requested_amount = Column(Numeric, nullable=False)  # Запрошенная сумма
    paid_amount = Column(Numeric, nullable=True)  # Фактически оплаченная сумма
    currency = Column(String(3), default="KGS")
    
    # Статусы и временные метки
    status = Column(SqlEnum(PaymentStatus), default=PaymentStatus.processing)
    odengi_status = Column(Integer, default=0)  # Статус от O!Dengi API
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    paid_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # 🕐 Время жизни платежа
    qr_expires_at = Column(DateTime(timezone=True), nullable=False)  # QR код истекает через 5 минут
    invoice_expires_at = Column(DateTime(timezone=True), nullable=False)  # Invoice истекает через 10 минут
    
    # Дополнительные данные
    description = Column(Text)
    qr_code_url = Column(String(500))
    app_link = Column(String(500))
    
    # Webhook данные
    last_webhook_at = Column(DateTime(timezone=True), nullable=True)
    webhook_count = Column(Integer, default=0)
    
    # Status check данные
    last_status_check_at = Column(DateTime(timezone=True), nullable=True)
    status_check_count = Column(Integer, default=0)
    needs_status_check = Column(Boolean, default=True)  # Флаг для фоновой проверки
    
    # Relationships
    client = relationship("Client")

class PaymentTransactionOdengi(Base):
    """Лог всех операций с балансом и платежами (реальная таблица в БД)"""
    __tablename__ = "payment_transactions_odengi"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    client_id = Column(String, ForeignKey('clients.id'), nullable=False)
    transaction_type = Column(SqlEnum(PaymentType), nullable=False)
    
    # Суммы
    amount = Column(Numeric, nullable=False)
    balance_before = Column(Numeric, nullable=False, default=0)
    balance_after = Column(Numeric, nullable=False, default=0)
    currency = Column(String(3), default="KGS")
    
    # Связанные объекты
    balance_topup_id = Column(Integer, ForeignKey('balance_topups.id'), nullable=True)
    charging_session_id = Column(String, ForeignKey('charging_sessions.id'), nullable=True)
    
    # Метаданные
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    client = relationship("Client")
    balance_topup = relationship("BalanceTopup")
    charging_session = relationship("ChargingSession")

# PaymentTransaction удален - используется только PaymentTransactionOdengi
