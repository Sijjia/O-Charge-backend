-- 019: Location images — main photo + gallery
-- Adds image_url to locations and creates location_images gallery table

ALTER TABLE locations ADD COLUMN IF NOT EXISTS image_url VARCHAR(500);

CREATE TABLE IF NOT EXISTS location_images (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    location_id VARCHAR NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    url VARCHAR(500) NOT NULL,
    caption VARCHAR(200),
    sort_order INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_location_images_location ON location_images(location_id);
