-- 005_partners.sql
-- Partner revenue share + Partner API
-- Таблица партнёров (бизнес-данные владельцев станций)
-- Колонки revenue в charging_sessions
-- Таблица выплат

-- Таблица партнёров (бизнес-данные владельцев станций)
CREATE TABLE IF NOT EXISTS partners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID UNIQUE NOT NULL,  -- FK на users (владелец станций)
    company_name VARCHAR(200),
    inn VARCHAR(20),
    contract_number VARCHAR(50),
    contract_date DATE,
    revenue_share_percent DECIMAL(5,2) DEFAULT 80.00,  -- % партнёра (80% по умолчанию)
    contact_name VARCHAR(100),
    contact_phone VARCHAR(20),
    contact_email VARCHAR(100),
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_partners_user ON partners(user_id);

-- Колонки revenue в charging_sessions
ALTER TABLE charging_sessions
    ADD COLUMN IF NOT EXISTS partner_id UUID,
    ADD COLUMN IF NOT EXISTS partner_share DECIMAL(12,2),
    ADD COLUMN IF NOT EXISTS platform_share DECIMAL(12,2);

CREATE INDEX IF NOT EXISTS idx_sessions_partner ON charging_sessions(partner_id);

-- Таблица выплат партнёрам
CREATE TABLE IF NOT EXISTS partner_payouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_payouts_partner ON partner_payouts(partner_id);
