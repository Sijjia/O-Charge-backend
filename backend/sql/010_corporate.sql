-- 010_corporate.sql
-- Corporate clients module: компании с общим балансом и лимитами для сотрудников

-- ============================================
-- 1. Корпоративные группы (компании)
-- ============================================
CREATE TABLE IF NOT EXISTS corporate_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Информация о компании
    company_name VARCHAR(200) NOT NULL,
    inn VARCHAR(20),
    legal_address TEXT,
    contact_person VARCHAR(100),
    contact_phone VARCHAR(20),
    contact_email VARCHAR(100),

    -- Финансы
    balance DECIMAL(12,2) DEFAULT 0,
    credit_limit DECIMAL(12,2) DEFAULT 0,
    monthly_limit DECIMAL(12,2),
    billing_type VARCHAR(20) DEFAULT 'prepaid',  -- prepaid | postpaid

    -- Статистика текущего месяца
    current_month_spent DECIMAL(12,2) DEFAULT 0,
    current_month_start DATE DEFAULT (date_trunc('month', CURRENT_DATE))::date,

    -- Договор
    contract_number VARCHAR(50),
    contract_date DATE,
    contract_expires DATE,

    -- Статус
    is_active BOOLEAN DEFAULT true,
    blocked_reason TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_corporate_groups_inn ON corporate_groups(inn);
CREATE INDEX IF NOT EXISTS idx_corporate_groups_active ON corporate_groups(is_active);

-- ============================================
-- 2. Корпоративные сотрудники
-- ============================================
CREATE TABLE IF NOT EXISTS corporate_employees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    corporate_group_id UUID NOT NULL REFERENCES corporate_groups(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,

    -- Роль и должность
    role VARCHAR(20) DEFAULT 'employee',  -- admin | employee
    position VARCHAR(100),

    -- Лимиты
    monthly_limit DECIMAL(12,2),          -- NULL = безлимит (в рамках компании)
    daily_limit DECIMAL(12,2),

    -- Статистика
    current_month_spent DECIMAL(12,2) DEFAULT 0,
    current_day_spent DECIMAL(12,2) DEFAULT 0,
    last_day_reset DATE DEFAULT CURRENT_DATE,

    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(corporate_group_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_corp_employees_group ON corporate_employees(corporate_group_id);
CREATE INDEX IF NOT EXISTS idx_corp_employees_user ON corporate_employees(user_id);

-- ============================================
-- 3. Корпоративные счета (invoices)
-- ============================================
CREATE TABLE IF NOT EXISTS corporate_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    corporate_group_id UUID NOT NULL REFERENCES corporate_groups(id),

    invoice_number VARCHAR(50) UNIQUE NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,

    total_amount DECIMAL(12,2) NOT NULL,
    total_energy_kwh DECIMAL(12,3) NOT NULL,
    sessions_count INTEGER NOT NULL,

    status VARCHAR(20) DEFAULT 'pending',  -- pending | paid | overdue | cancelled
    due_date DATE NOT NULL,
    paid_at TIMESTAMPTZ,

    pdf_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_corp_invoices_group ON corporate_invoices(corporate_group_id);
CREATE INDEX IF NOT EXISTS idx_corp_invoices_status ON corporate_invoices(status);

-- ============================================
-- 4. Корпоративные транзакции (баланс)
-- ============================================
CREATE TABLE IF NOT EXISTS corporate_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    corporate_group_id UUID NOT NULL REFERENCES corporate_groups(id),

    type VARCHAR(20) NOT NULL,  -- topup | charge | refund | adjustment
    amount DECIMAL(12,2) NOT NULL,
    balance_before DECIMAL(12,2) NOT NULL,
    balance_after DECIMAL(12,2) NOT NULL,

    charging_session_id UUID,
    invoice_id UUID REFERENCES corporate_invoices(id),
    employee_id UUID REFERENCES corporate_employees(id),

    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_corp_transactions_group ON corporate_transactions(corporate_group_id);
CREATE INDEX IF NOT EXISTS idx_corp_transactions_date ON corporate_transactions(created_at);
