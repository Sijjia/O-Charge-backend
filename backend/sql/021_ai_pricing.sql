-- ============================================
-- 021_ai_pricing.sql
-- AI Pricing Intelligence: competitor prices + AI recommendations
-- ============================================

-- Таблица цен конкурентов
CREATE TABLE IF NOT EXISTS competitor_prices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competitor_name VARCHAR(200) NOT NULL,
    station_name VARCHAR(300),
    location_description TEXT,
    price_per_kwh NUMERIC(10,2) NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'KGS',
    connector_type VARCHAR(50),           -- CCS2, Type2, CHAdeMO, GBT
    power_kw NUMERIC(10,1),               -- мощность станции
    charging_type VARCHAR(10),            -- AC или DC
    source VARCHAR(50) NOT NULL DEFAULT 'manual',  -- manual / api / scrape
    source_url TEXT,
    verified_at TIMESTAMPTZ,
    notes TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_competitor_prices_active ON competitor_prices (is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_competitor_prices_type ON competitor_prices (charging_type, connector_type);

-- Таблица AI-рекомендаций по ценообразованию
CREATE TABLE IF NOT EXISTS ai_pricing_recommendations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    station_id VARCHAR(100) REFERENCES stations(id) ON DELETE SET NULL,  -- NULL = глобальная рекомендация
    recommended_price_per_kwh NUMERIC(10,2) NOT NULL,
    recommended_price_night NUMERIC(10,2),
    current_price_per_kwh NUMERIC(10,2),
    avg_competitor_price NUMERIC(10,2),
    min_competitor_price NUMERIC(10,2),
    max_competitor_price NUMERIC(10,2),
    electricity_cost_per_kwh NUMERIC(10,2) DEFAULT 1.50,
    estimated_revenue_change_percent NUMERIC(5,1),
    estimated_monthly_revenue NUMERIC(12,2),
    confidence_level VARCHAR(20) NOT NULL DEFAULT 'medium',  -- low / medium / high
    reasoning TEXT,
    factors JSONB DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending / applied / dismissed
    applied_at TIMESTAMPTZ,
    applied_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_pricing_rec_station ON ai_pricing_recommendations (station_id);
CREATE INDEX IF NOT EXISTS idx_ai_pricing_rec_status ON ai_pricing_recommendations (status);

-- ============================================
-- Seed данные: цены конкурентов в КР
-- ============================================

INSERT INTO competitor_prices (competitor_name, station_name, location_description, price_per_kwh, currency, connector_type, power_kw, charging_type, source, verified_at, notes)
VALUES
  ('Nol', 'Nol DC 60kW', 'Бишкек, различные локации', 14.00, 'KGS', 'CCS2', 60, 'DC', 'manual', NOW(), 'Основной конкурент DC зарядки в Бишкеке'),
  ('Nol', 'Nol AC 22kW', 'Бишкек, различные локации', 12.00, 'KGS', 'Type2', 22, 'AC', 'manual', NOW(), 'AC зарядка Nol'),
  ('Nol', 'Nol DC 120kW', 'Бишкек, ТРЦ', 15.00, 'KGS', 'CCS2', 120, 'DC', 'manual', NOW(), 'Быстрая DC зарядка'),
  ('Nol', 'Nol DC CHAdeMO', 'Бишкек', 14.00, 'KGS', 'CHAdeMO', 60, 'DC', 'manual', NOW(), 'CHAdeMO коннектор'),
  ('EV Power KG', 'EV Power DC', 'Бишкек, Южная магистраль', 13.50, 'KGS', 'CCS2', 60, 'DC', 'manual', NOW(), 'Новый оператор'),
  ('EV Power KG', 'EV Power AC', 'Бишкек, Южная магистраль', 11.00, 'KGS', 'Type2', 22, 'AC', 'manual', NOW(), 'AC зарядка')
ON CONFLICT DO NOTHING;
