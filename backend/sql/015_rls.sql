-- Row Level Security
-- ТЗ 5.6, 6: безопасность данных на уровне БД

-- Enable RLS on key tables
ALTER TABLE charging_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;

-- Enable RLS on reviews (if table exists)
DO $$ BEGIN
  ALTER TABLE reviews ENABLE ROW LEVEL SECURITY;
EXCEPTION WHEN undefined_table THEN NULL;
END $$;

-- Policies for clients: can only see own data
CREATE POLICY client_own_sessions ON charging_sessions
  FOR SELECT USING (
    current_setting('app.current_user_id', true) IS NOT NULL
    AND client_id = current_setting('app.current_user_id')::uuid
  );

CREATE POLICY client_own_transactions ON transactions
  FOR SELECT USING (
    current_setting('app.current_user_id', true) IS NOT NULL
    AND client_id = current_setting('app.current_user_id')::uuid
  );

CREATE POLICY client_own_bookings ON bookings
  FOR SELECT USING (
    current_setting('app.current_user_id', true) IS NOT NULL
    AND client_id = current_setting('app.current_user_id')::uuid
  );

-- Service role bypass (backend uses this)
CREATE POLICY service_bypass_sessions ON charging_sessions
  FOR ALL USING (current_setting('app.role', true) = 'service');

CREATE POLICY service_bypass_transactions ON transactions
  FOR ALL USING (current_setting('app.role', true) = 'service');

CREATE POLICY service_bypass_bookings ON bookings
  FOR ALL USING (current_setting('app.role', true) = 'service');

-- Reviews: clients can insert own, read all
CREATE POLICY client_insert_reviews ON reviews
  FOR INSERT WITH CHECK (
    current_setting('app.current_user_id', true) IS NOT NULL
    AND client_id = current_setting('app.current_user_id')::uuid
  );

CREATE POLICY public_read_reviews ON reviews
  FOR SELECT USING (true);

CREATE POLICY service_bypass_reviews ON reviews
  FOR ALL USING (current_setting('app.role', true) = 'service');
