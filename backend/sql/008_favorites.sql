-- 008_favorites.sql
-- User favorites (избранные локации/станции)
-- API уже реализован: app/api/v1/favorites/__init__.py

CREATE TABLE IF NOT EXISTS user_favorites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    location_id VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(user_id, location_id)
);

CREATE INDEX IF NOT EXISTS idx_user_favorites_user ON user_favorites(user_id);
CREATE INDEX IF NOT EXISTS idx_user_favorites_location ON user_favorites(location_id);
