-- 000_base_schema_local.sql
-- Базовая схема для ЛОКАЛЬНОГО тестирования (NOT production)
-- Создаёт все таблицы, необходимые для OCPP E2E теста
--
-- Usage: psql -h localhost -p 5433 -U evpower -d evpower -f 000_base_schema_local.sql

-- ============================================================================
-- ENUM TYPES
-- ============================================================================

DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('operator', 'admin', 'superadmin');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE client_status AS ENUM ('active', 'inactive', 'blocked');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE station_status AS ENUM ('active', 'inactive', 'maintenance');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE maintenance_status AS ENUM ('pending', 'in_progress', 'completed', 'cancelled');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE chargingsession_status AS ENUM ('started', 'stopped', 'error');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE limit_type AS ENUM ('none', 'energy', 'amount');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE tariff_type AS ENUM ('per_kwh', 'per_minute', 'session_fee', 'parking_fee');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE payment_status AS ENUM ('processing', 'approved', 'canceled', 'refunded', 'partial_refund');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE payment_type AS ENUM ('balance_topup', 'charge_reserve', 'charge_refund', 'charge_payment');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ============================================================================
-- CORE TABLES
-- ============================================================================

-- Users (admin/operator accounts)
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR PRIMARY KEY,
    email VARCHAR NOT NULL,
    hashed_password VARCHAR NOT NULL,
    role user_role NOT NULL DEFAULT 'operator',
    is_active BOOLEAN DEFAULT true,
    admin_id VARCHAR,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Clients (end-user customers)
CREATE TABLE IF NOT EXISTS clients (
    id VARCHAR PRIMARY KEY,
    name VARCHAR,
    phone VARCHAR,
    balance DECIMAL(12,2) DEFAULT 0.00,
    status client_status NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Locations (physical locations grouping stations)
CREATE TABLE IF NOT EXISTS locations (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    address VARCHAR NOT NULL,
    city VARCHAR,
    country VARCHAR DEFAULT 'KG',
    latitude FLOAT,
    longitude FLOAT,
    user_id VARCHAR REFERENCES users(id),
    admin_id VARCHAR,
    stations_count INTEGER DEFAULT 0,
    connectors_count INTEGER DEFAULT 0,
    status VARCHAR DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tariff Plans
CREATE TABLE IF NOT EXISTS tariff_plans (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name VARCHAR NOT NULL,
    description TEXT,
    is_default BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tariff Rules
CREATE TABLE IF NOT EXISTS tariff_rules (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tariff_plan_id VARCHAR REFERENCES tariff_plans(id),
    name VARCHAR NOT NULL,
    tariff_type tariff_type NOT NULL DEFAULT 'per_kwh',
    connector_type VARCHAR DEFAULT 'ALL',
    power_range_min DECIMAL DEFAULT 0,
    power_range_max DECIMAL DEFAULT 1000,
    price DECIMAL NOT NULL,
    currency VARCHAR DEFAULT 'KGS',
    time_start TEXT DEFAULT '00:00:00',
    time_end TEXT DEFAULT '23:59:59',
    is_weekend BOOLEAN DEFAULT false,
    priority INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Stations (charging stations)
CREATE TABLE IF NOT EXISTS stations (
    id VARCHAR PRIMARY KEY,
    serial_number VARCHAR NOT NULL UNIQUE,
    model VARCHAR NOT NULL,
    manufacturer VARCHAR NOT NULL,
    location_id VARCHAR REFERENCES locations(id),
    power_capacity FLOAT NOT NULL DEFAULT 22.0,
    connector_types TEXT[] NOT NULL DEFAULT '{Type2}',
    installation_date VARCHAR,
    firmware_version VARCHAR,
    status station_status NOT NULL DEFAULT 'active',
    user_id VARCHAR REFERENCES users(id),
    connectors_count INTEGER DEFAULT 1,
    tariff_plan_id VARCHAR REFERENCES tariff_plans(id),
    price_per_kwh DECIMAL DEFAULT 12.00,
    session_fee DECIMAL DEFAULT 0,
    currency VARCHAR DEFAULT 'KGS',
    is_available BOOLEAN DEFAULT true,
    last_heartbeat_at TIMESTAMPTZ,
    ocpp_version VARCHAR DEFAULT '1.6',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Connectors
CREATE TABLE IF NOT EXISTS connectors (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    station_id VARCHAR REFERENCES stations(id) ON DELETE CASCADE,
    connector_number INTEGER NOT NULL DEFAULT 1,
    status VARCHAR DEFAULT 'available',
    error_code VARCHAR DEFAULT 'NoError',
    connector_type VARCHAR DEFAULT 'Type2',
    power_kw FLOAT DEFAULT 22.0,
    last_status_update TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(station_id, connector_number)
);

-- Charging Sessions
CREATE TABLE IF NOT EXISTS charging_sessions (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id VARCHAR REFERENCES clients(id),
    station_id VARCHAR REFERENCES stations(id),
    connector_id INTEGER DEFAULT 1,
    start_time TIMESTAMPTZ DEFAULT NOW(),
    stop_time TIMESTAMPTZ,
    energy FLOAT DEFAULT 0,
    amount FLOAT DEFAULT 0,
    status chargingsession_status NOT NULL DEFAULT 'started',
    transaction_id VARCHAR,
    limit_type limit_type DEFAULT 'none',
    limit_value FLOAT,
    reserved_amount DECIMAL(12,2) DEFAULT 0,
    base_amount DECIMAL(12,2) DEFAULT 0,
    final_amount DECIMAL(12,2),
    payment_processed BOOLEAN DEFAULT false,
    night_tariff_applied BOOLEAN DEFAULT false,
    tariff_rate DECIMAL(10,2),
    pricing_history_id VARCHAR,
    refund_amount DECIMAL(12,2) DEFAULT 0,
    stop_reason VARCHAR,
    partner_id VARCHAR,
    partner_share DECIMAL(12,2),
    platform_share DECIMAL(12,2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Maintenance
CREATE TABLE IF NOT EXISTS maintenance (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    station_id VARCHAR REFERENCES stations(id),
    request_date VARCHAR,
    description VARCHAR,
    assigned_to VARCHAR,
    notes VARCHAR,
    status maintenance_status NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- OCPP TABLES
-- ============================================================================

-- OCPP Station Status
CREATE TABLE IF NOT EXISTS ocpp_station_status (
    station_id VARCHAR PRIMARY KEY REFERENCES stations(id) ON DELETE CASCADE,
    status VARCHAR NOT NULL DEFAULT 'Available',
    error_code VARCHAR,
    info VARCHAR,
    vendor_id VARCHAR,
    vendor_error_code VARCHAR,
    last_heartbeat TIMESTAMPTZ DEFAULT NOW(),
    firmware_version VARCHAR,
    boot_notification_sent BOOLEAN DEFAULT false,
    is_online BOOLEAN DEFAULT false,
    connector_status JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- OCPP Transactions
CREATE TABLE IF NOT EXISTS ocpp_transactions (
    id BIGSERIAL PRIMARY KEY,
    transaction_id INTEGER NOT NULL,
    station_id VARCHAR NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    connector_id INTEGER NOT NULL DEFAULT 1,
    id_tag VARCHAR NOT NULL,
    meter_start DECIMAL NOT NULL DEFAULT 0,
    meter_stop DECIMAL,
    start_timestamp TIMESTAMPTZ NOT NULL,
    stop_timestamp TIMESTAMPTZ,
    stop_reason VARCHAR,
    charging_session_id VARCHAR REFERENCES charging_sessions(id),
    status VARCHAR NOT NULL DEFAULT 'Started',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ocpp_tx_station ON ocpp_transactions(station_id);
CREATE INDEX IF NOT EXISTS idx_ocpp_tx_session ON ocpp_transactions(charging_session_id);

-- OCPP Meter Values
CREATE TABLE IF NOT EXISTS ocpp_meter_values (
    id BIGSERIAL PRIMARY KEY,
    transaction_id INTEGER,
    ocpp_transaction_id INTEGER REFERENCES ocpp_transactions(id),
    station_id VARCHAR NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    connector_id INTEGER NOT NULL DEFAULT 1,
    timestamp TIMESTAMPTZ NOT NULL,
    sampled_values JSONB NOT NULL,
    energy_active_import_register DECIMAL,
    power_active_import DECIMAL,
    current_import DECIMAL,
    voltage DECIMAL,
    temperature DECIMAL,
    soc DECIMAL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ocpp_mv_station ON ocpp_meter_values(station_id);
CREATE INDEX IF NOT EXISTS idx_ocpp_mv_tx ON ocpp_meter_values(ocpp_transaction_id);

-- OCPP Authorization
CREATE TABLE IF NOT EXISTS ocpp_authorization (
    id_tag VARCHAR PRIMARY KEY,
    parent_id_tag VARCHAR,
    expiry_date TIMESTAMPTZ,
    status VARCHAR NOT NULL DEFAULT 'Accepted',
    user_id VARCHAR REFERENCES users(id),
    client_id VARCHAR REFERENCES clients(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- OCPP Configuration
CREATE TABLE IF NOT EXISTS ocpp_configuration (
    id BIGSERIAL PRIMARY KEY,
    station_id VARCHAR NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    key VARCHAR NOT NULL,
    value VARCHAR,
    readonly BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- PAYMENT TABLES
-- ============================================================================

-- Balance Topups
CREATE TABLE IF NOT EXISTS balance_topups (
    id BIGSERIAL PRIMARY KEY,
    invoice_id VARCHAR(256) UNIQUE,
    order_id VARCHAR(128) UNIQUE,
    merchant_id VARCHAR(32),
    client_id VARCHAR NOT NULL REFERENCES clients(id),
    requested_amount DECIMAL NOT NULL,
    paid_amount DECIMAL,
    currency VARCHAR(3) DEFAULT 'KGS',
    status payment_status DEFAULT 'processing',
    odengi_status INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    paid_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    qr_expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '5 minutes',
    invoice_expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '10 minutes',
    description TEXT,
    qr_code_url VARCHAR(500),
    app_link VARCHAR(500),
    last_webhook_at TIMESTAMPTZ,
    webhook_count INTEGER DEFAULT 0,
    last_status_check_at TIMESTAMPTZ,
    status_check_count INTEGER DEFAULT 0,
    needs_status_check BOOLEAN DEFAULT true,
    payment_provider VARCHAR(20) DEFAULT 'ODENGI'
);

-- Payment Transactions (balance ledger)
CREATE TABLE IF NOT EXISTS payment_transactions_odengi (
    id BIGSERIAL PRIMARY KEY,
    client_id VARCHAR NOT NULL REFERENCES clients(id),
    transaction_type payment_type NOT NULL,
    amount DECIMAL NOT NULL,
    balance_before DECIMAL NOT NULL DEFAULT 0,
    balance_after DECIMAL NOT NULL DEFAULT 0,
    currency VARCHAR(3) DEFAULT 'KGS',
    balance_topup_id INTEGER REFERENCES balance_topups(id),
    charging_session_id VARCHAR REFERENCES charging_sessions(id),
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- OCPP EVENT LOGS (from migration 006)
-- ============================================================================

CREATE TABLE IF NOT EXISTS ocpp_event_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    station_id VARCHAR(50) NOT NULL,
    connector_id INTEGER,
    event_type VARCHAR(50) NOT NULL,
    direction VARCHAR(10) NOT NULL DEFAULT 'inbound',
    message_id VARCHAR(50),
    request_payload JSONB,
    response_payload JSONB,
    severity VARCHAR(20) DEFAULT 'info',
    processing_time_ms INTEGER,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ocpp_logs_station ON ocpp_event_logs(station_id);
CREATE INDEX IF NOT EXISTS idx_ocpp_logs_created ON ocpp_event_logs(created_at);

-- ============================================================================
-- OCPP 2.0.1 TX MAP (from migration 007)
-- ============================================================================

CREATE TABLE IF NOT EXISTS ocpp_v201_tx_map (
    v201_transaction_id VARCHAR(36) PRIMARY KEY,
    numeric_transaction_id INTEGER NOT NULL,
    station_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE SEQUENCE IF NOT EXISTS ocpp_v201_tx_seq START 10000;

-- ============================================================================
-- PARTNERS (from migration 005)
-- ============================================================================

CREATE TABLE IF NOT EXISTS partners (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id VARCHAR UNIQUE NOT NULL,
    company_name VARCHAR(200),
    inn VARCHAR(20),
    contract_number VARCHAR(50),
    contract_date DATE,
    revenue_share_percent DECIMAL(5,2) DEFAULT 80.00,
    contact_name VARCHAR(100),
    contact_phone VARCHAR(20),
    contact_email VARCHAR(100),
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS partner_payouts (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    partner_id VARCHAR NOT NULL,
    period_from DATE NOT NULL,
    period_to DATE NOT NULL,
    total_revenue DECIMAL(12,2) NOT NULL,
    partner_share DECIMAL(12,2) NOT NULL,
    platform_share DECIMAL(12,2) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    paid_at TIMESTAMPTZ,
    payment_reference VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- BOOKINGS (from migration 004)
-- ============================================================================

CREATE TABLE IF NOT EXISTS bookings (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id VARCHAR NOT NULL,
    station_id VARCHAR NOT NULL,
    connector_id INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(20) DEFAULT 'active',
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '15 minutes'),
    used_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    session_id VARCHAR,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- IDEMPOTENCY KEYS (middleware)
-- ============================================================================

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key VARCHAR PRIMARY KEY,
    response_status INTEGER,
    response_body JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- PRICING HISTORY (for tariff audit)
-- ============================================================================

CREATE TABLE IF NOT EXISTS pricing_history (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    station_id VARCHAR,
    session_id VARCHAR,
    tariff_plan_id VARCHAR,
    rule_id VARCHAR,
    calculation_time TIMESTAMPTZ DEFAULT NOW(),
    rate_per_kwh DECIMAL,
    rate_per_minute DECIMAL,
    session_fee DECIMAL,
    parking_fee_per_minute DECIMAL,
    rule_name VARCHAR,
    rule_details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- TEST DATA — тестовые данные для E2E
-- ============================================================================

-- Тестовый admin/operator
INSERT INTO users (id, email, hashed_password, role, is_active)
VALUES ('test-admin-001', 'admin@test.local', 'not-a-real-hash', 'admin', true)
ON CONFLICT (id) DO NOTHING;

-- Тестовый клиент с балансом 5000 сом
INSERT INTO clients (id, name, phone, balance, status)
VALUES ('test-client-001', 'Test User', '+996700000001', 5000.00, 'active')
ON CONFLICT (id) DO NOTHING;

-- Тестовая локация
INSERT INTO locations (id, name, address, city, latitude, longitude, user_id)
VALUES ('test-loc-001', 'Test Location', 'Бишкек, ул. Тестовая 1', 'Бишкек', 42.8746, 74.5698, 'test-admin-001')
ON CONFLICT (id) DO NOTHING;

-- Тестовая станция SIM-TEST (для симулятора)
INSERT INTO stations (id, serial_number, model, manufacturer, location_id, power_capacity, connector_types, status, user_id, price_per_kwh, connectors_count)
VALUES ('SIM-TEST', 'SIM-TEST-SN001', 'VirtualChargePoint', 'TestVendor', 'test-loc-001', 22.0, '{Type2,CCS2}', 'active', 'test-admin-001', 12.00, 2)
ON CONFLICT (id) DO NOTHING;

-- Коннекторы для тестовой станции
INSERT INTO connectors (id, station_id, connector_number, status, connector_type, power_kw)
VALUES
    ('sim-conn-1', 'SIM-TEST', 1, 'available', 'Type2', 22.0),
    ('sim-conn-2', 'SIM-TEST', 2, 'available', 'CCS2', 50.0)
ON CONFLICT (id) DO NOTHING;

-- OCPP авторизация для тестового RFID тега
INSERT INTO ocpp_authorization (id_tag, status, client_id)
VALUES ('TEST-RFID-001', 'Accepted', 'test-client-001')
ON CONFLICT (id_tag) DO NOTHING;

-- Тестовый тариф
INSERT INTO tariff_plans (id, name, description, is_default, is_active)
VALUES ('test-tariff-001', 'Стандартный тариф', 'Тестовый тариф 12 сом/кВт·ч', true, true)
ON CONFLICT (id) DO NOTHING;

INSERT INTO tariff_rules (id, tariff_plan_id, name, tariff_type, price, priority)
VALUES ('test-rule-001', 'test-tariff-001', 'Базовая ставка', 'per_kwh', 12.00, 0)
ON CONFLICT (id) DO NOTHING;

-- Привязываем тариф к станции
UPDATE stations SET tariff_plan_id = 'test-tariff-001' WHERE id = 'SIM-TEST';

-- Вторая тестовая станция для OCPP 2.0.1
INSERT INTO stations (id, serial_number, model, manufacturer, location_id, power_capacity, connector_types, status, user_id, price_per_kwh, connectors_count, ocpp_version)
VALUES ('SIM-TEST-201', 'SIM-TEST-201-SN', 'VirtualChargePoint', 'TestVendor', 'test-loc-001', 50.0, '{CCS2}', 'active', 'test-admin-001', 15.00, 1, '2.0.1')
ON CONFLICT (id) DO NOTHING;

INSERT INTO connectors (id, station_id, connector_number, status, connector_type, power_kw)
VALUES ('sim-201-conn-1', 'SIM-TEST-201', 1, 'available', 'CCS2', 50.0)
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- PUSH SUBSCRIPTIONS (Web Push notifications)
-- ============================================================================

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR NOT NULL,
    user_type VARCHAR NOT NULL CHECK (user_type IN ('client', 'owner')),
    endpoint TEXT NOT NULL,
    p256dh_key TEXT NOT NULL,
    auth_key TEXT NOT NULL,
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    UNIQUE(user_id, user_type, endpoint)
);

-- Готово!
SELECT 'Base schema created + test data inserted' AS result;
