-- 009_tariffs.sql
-- Tariff plans, rules, pricing history, promo codes
-- Используется PricingService: app/services/pricing_service.py

-- ============================================
-- 1. Тарифные планы
-- ============================================
CREATE TABLE IF NOT EXISTS tariff_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    is_default BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Только один тариф может быть default
CREATE UNIQUE INDEX IF NOT EXISTS idx_tariff_plans_default
    ON tariff_plans(is_default) WHERE is_default = true;

-- ============================================
-- 2. Правила тарификации
-- ============================================
CREATE TABLE IF NOT EXISTS tariff_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tariff_plan_id UUID NOT NULL REFERENCES tariff_plans(id) ON DELETE CASCADE,

    name VARCHAR(100) NOT NULL,           -- "Ночной", "Пиковый"
    tariff_type VARCHAR(20) NOT NULL DEFAULT 'per_kwh',  -- per_kwh, per_minute, session_fee, parking_fee

    -- Условия применения
    connector_type VARCHAR(20) DEFAULT 'ALL',  -- CCS2, Type2, CHAdeMO, ALL
    power_range_min INTEGER,                    -- Мин мощность кВт
    power_range_max INTEGER,                    -- Макс мощность кВт

    -- Цена
    price DECIMAL(10,2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'KGS',

    -- Временной период
    time_start TIME,                      -- 00:00
    time_end TIME,                        -- 06:00

    -- День/выходные
    is_weekend BOOLEAN,                   -- NULL = любой, true = только выходные
    days_of_week INTEGER[],               -- {1,2,3,4,5} = пн-пт (ISO weekday)

    -- Длительность сессии
    min_duration_minutes INTEGER,
    max_duration_minutes INTEGER,

    -- Валидность правила
    valid_from DATE,
    valid_until DATE,

    -- Приоритет (выше = важнее)
    priority INTEGER DEFAULT 10,

    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tariff_rules_plan ON tariff_rules(tariff_plan_id);
CREATE INDEX IF NOT EXISTS idx_tariff_rules_active ON tariff_rules(tariff_plan_id, is_active);

-- ============================================
-- 3. Привязка тарифа к станции
-- ============================================
ALTER TABLE stations ADD COLUMN IF NOT EXISTS tariff_plan_id UUID REFERENCES tariff_plans(id);
CREATE INDEX IF NOT EXISTS idx_stations_tariff ON stations(tariff_plan_id);

-- ============================================
-- 4. История расчёта тарифов (аналитика)
-- ============================================
CREATE TABLE IF NOT EXISTS pricing_history (
    id BIGSERIAL PRIMARY KEY,
    station_id VARCHAR(100),
    tariff_plan_id UUID,
    rule_id UUID,
    calculation_time TIMESTAMPTZ NOT NULL,
    rate_per_kwh DECIMAL(10,2),
    rate_per_minute DECIMAL(10,2),
    session_fee DECIMAL(10,2),
    parking_fee_per_minute DECIMAL(10,2),
    currency VARCHAR(3) DEFAULT 'KGS',
    rule_name VARCHAR(200),
    rule_details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pricing_history_station ON pricing_history(station_id);
CREATE INDEX IF NOT EXISTS idx_pricing_history_time ON pricing_history(calculation_time);

-- ============================================
-- 5. Клиентские тарифы (VIP / корпоративные)
-- ============================================
CREATE TABLE IF NOT EXISTS client_tariffs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,
    tariff_plan_id UUID REFERENCES tariff_plans(id),
    discount_percent DECIMAL(5,2),         -- Скидка в %
    fixed_rate_per_kwh DECIMAL(10,2),      -- Фиксированная цена
    is_active BOOLEAN DEFAULT true,
    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_client_tariffs_client ON client_tariffs(client_id);

-- ============================================
-- 6. Промо-коды
-- ============================================
CREATE TABLE IF NOT EXISTS promo_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50) UNIQUE NOT NULL,
    discount_type VARCHAR(10) NOT NULL,     -- percent, fixed
    discount_value DECIMAL(10,2) NOT NULL,
    max_discount_amount DECIMAL(10,2),      -- Макс скидка в сомах
    min_charge_amount DECIMAL(10,2),        -- Мин сумма зарядки
    usage_limit INTEGER,                    -- Общий лимит использований
    usage_count INTEGER DEFAULT 0,
    client_usage_limit INTEGER,             -- Лимит на клиента
    is_active BOOLEAN DEFAULT true,
    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_promo_codes_code ON promo_codes(code);

-- Использование промо-кодов
CREATE TABLE IF NOT EXISTS promo_code_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promo_code_id UUID NOT NULL REFERENCES promo_codes(id),
    client_id UUID NOT NULL,
    session_id UUID,
    discount_amount DECIMAL(10,2) NOT NULL,
    used_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_promo_usage_promo ON promo_code_usage(promo_code_id);
CREATE INDEX IF NOT EXISTS idx_promo_usage_client ON promo_code_usage(client_id);
