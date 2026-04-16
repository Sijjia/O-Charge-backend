-- Migration 003: Поля для PDF чеков (Red Petroleum §4.4)
-- Дата: 2026-02-13
-- Автор: Эрмек

-- 1. Флаг генерации чека
ALTER TABLE charging_sessions
    ADD COLUMN IF NOT EXISTS receipt_generated BOOLEAN DEFAULT false;

-- 2. Номер коннектора (для отображения в чеке)
ALTER TABLE charging_sessions
    ADD COLUMN IF NOT EXISTS connector_id INTEGER;

-- 3. Комментарии
COMMENT ON COLUMN charging_sessions.receipt_generated IS 'Был ли сгенерирован PDF чек для этой сессии';
COMMENT ON COLUMN charging_sessions.connector_id IS 'Номер коннектора (1-10), используется в чеке';
