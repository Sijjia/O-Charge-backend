-- 020: Map platform integrations — global settings + per-location sync
-- Infrastructure for syncing EV locations to external map platforms

CREATE TABLE IF NOT EXISTS map_integrations (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    platform VARCHAR(50) NOT NULL UNIQUE,
    display_name VARCHAR(100) NOT NULL,
    icon VARCHAR(100),
    api_key VARCHAR(500),
    api_secret VARCHAR(500),
    oauth_token TEXT,
    oauth_expires_at TIMESTAMPTZ,
    config JSONB DEFAULT '{}',
    is_enabled BOOLEAN DEFAULT false,
    sync_interval_minutes INTEGER DEFAULT 60,
    last_global_sync_at TIMESTAMPTZ,
    locations_synced INTEGER DEFAULT 0,
    locations_error INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS location_platform_sync (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    location_id VARCHAR NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL,
    is_enabled BOOLEAN DEFAULT true,
    external_id VARCHAR(200),
    external_url VARCHAR(500),
    last_sync_at TIMESTAMPTZ,
    sync_status VARCHAR(20) DEFAULT 'pending',
    sync_error TEXT,
    sync_data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(location_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_location_platform_sync_location ON location_platform_sync(location_id);
CREATE INDEX IF NOT EXISTS idx_location_platform_sync_platform ON location_platform_sync(platform);

-- Seed default platforms
INSERT INTO map_integrations (id, platform, display_name, icon, config) VALUES
('mi-ocm', 'open_charge_map', 'Open Charge Map', 'solar:earth-bold-duotone', '{"base_url":"https://api.openchargemap.io/v3"}'),
('mi-gbp', 'google_business', 'Google Business Profile', 'solar:map-point-search-bold-duotone', '{}'),
('mi-2gis', '2gis', '2GIS', 'solar:map-bold-duotone', '{"region":"kg"}'),
('mi-yandex', 'yandex_maps', 'Яндекс Карты', 'solar:compass-bold-duotone', '{}')
ON CONFLICT (platform) DO NOTHING;
