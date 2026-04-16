-- 012: Add error tracking fields to charging_sessions
-- stop_reason already exists in ocpp_transactions, adding to sessions for quick access
-- error_details stores structured error info for debugging

ALTER TABLE charging_sessions ADD COLUMN IF NOT EXISTS stop_reason VARCHAR;
ALTER TABLE charging_sessions ADD COLUMN IF NOT EXISTS error_details TEXT;
