-- Migration 002: Ночной тариф и резервирование (Red Petroleum §4.1, §4.3)
-- Дата: 2026-02-12
-- Автор: Эрмек

-- 1. Добавляем поле ночного тарифа в charging_sessions
ALTER TABLE charging_sessions
    ADD COLUMN IF NOT EXISTS night_tariff_applied BOOLEAN DEFAULT false;

-- 2. Фиксируем тариф на момент старта (для корректного расчёта при изменении тарифа)
ALTER TABLE charging_sessions
    ADD COLUMN IF NOT EXISTS tariff_rate DECIMAL(10,2);

-- 3. Добавляем причину остановки
ALTER TABLE charging_sessions
    ADD COLUMN IF NOT EXISTS stop_reason VARCHAR(50);

-- 4. Поле для суммы возврата
ALTER TABLE charging_sessions
    ADD COLUMN IF NOT EXISTS refund_amount DECIMAL(12,2) DEFAULT 0;

-- 5. Комментарии
COMMENT ON COLUMN charging_sessions.night_tariff_applied IS 'Был ли применён ночной тариф -20% (23:00-06:00)';
COMMENT ON COLUMN charging_sessions.tariff_rate IS 'Тариф сом/кВтч на момент старта зарядки';
COMMENT ON COLUMN charging_sessions.stop_reason IS 'Причина остановки: user_stopped, limit_reached, low_balance, station_error';
COMMENT ON COLUMN charging_sessions.refund_amount IS 'Сумма возврата неиспользованного резерва';
