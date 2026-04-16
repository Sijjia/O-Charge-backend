-- Guest Sessions - таблица для гостевой зарядки без регистрации
-- Выполнить в Supabase SQL Editor

CREATE TABLE IF NOT EXISTS guest_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone VARCHAR(20) NOT NULL,
    station_id VARCHAR(50) NOT NULL REFERENCES stations(id),
    connector_id INTEGER NOT NULL DEFAULT 1,
    amount_kgs DECIMAL(10,2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    payment_id VARCHAR(100),
    charging_session_id UUID REFERENCES charging_sessions(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_guest_sessions_phone ON guest_sessions(phone);
CREATE INDEX IF NOT EXISTS idx_guest_sessions_status ON guest_sessions(status);
CREATE INDEX IF NOT EXISTS idx_guest_sessions_station ON guest_sessions(station_id);

-- Комментарии
COMMENT ON TABLE guest_sessions IS 'Гостевые сессии зарядки (без регистрации)';
COMMENT ON COLUMN guest_sessions.status IS 'pending -> paid -> charging -> completed | cancelled | failed';
COMMENT ON COLUMN guest_sessions.amount_kgs IS 'Сумма оплаты в сомах (KGS)';
COMMENT ON COLUMN guest_sessions.payment_id IS 'ID платежа от платёжного провайдера';
COMMENT ON COLUMN guest_sessions.charging_session_id IS 'Связь с основной сессией зарядки после запуска';
