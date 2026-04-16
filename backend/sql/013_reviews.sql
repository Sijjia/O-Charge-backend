-- Reviews and Ratings system
-- ТЗ Приложение 3: "Система отзывов и рейтингов"

CREATE TABLE IF NOT EXISTS reviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  station_id TEXT REFERENCES ocpp_charge_points(charge_point_id) ON DELETE CASCADE,
  session_id UUID REFERENCES charging_sessions(id) ON DELETE SET NULL,
  rating INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
  comment TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reviews_station ON reviews(station_id);
CREATE INDEX IF NOT EXISTS idx_reviews_client ON reviews(client_id);
CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews(created_at DESC);
