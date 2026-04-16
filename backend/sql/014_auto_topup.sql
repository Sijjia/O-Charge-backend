-- Auto-topup settings for clients
-- Договор Приложение 3: "Автопополнение баланса"

ALTER TABLE clients ADD COLUMN IF NOT EXISTS auto_topup_enabled BOOLEAN DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS auto_topup_threshold NUMERIC(10,2) DEFAULT 100;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS auto_topup_amount NUMERIC(10,2) DEFAULT 500;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS saved_payment_method JSONB;
