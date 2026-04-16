-- 011_namba_one.sql
-- Миграция для поддержки Namba One:
-- 1. Расширение invoice_id до VARCHAR(256) (Namba One order_id длинные)
-- 2. Добавление payment_provider для маршрутизации webhook/status check

-- Расширяем invoice_id (Namba One externalId может быть 40+ символов)
ALTER TABLE balance_topups ALTER COLUMN invoice_id TYPE VARCHAR(256);

-- Добавляем колонку payment_provider если не существует
DO $$ BEGIN
    ALTER TABLE balance_topups ADD COLUMN payment_provider VARCHAR(20) DEFAULT 'ODENGI';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
