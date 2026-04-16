-- 016_partner_location_link.sql
-- Прямая привязка партнёра к локации и станции (вместо косвенной через user_id)
-- partner_id NULL = Red Petroleum владеет 100%
-- Станция наследует partner от локации, но может переопределить (override)

-- Прямая привязка партнёра к локации
ALTER TABLE locations ADD COLUMN IF NOT EXISTS partner_id VARCHAR REFERENCES partners(id);
CREATE INDEX IF NOT EXISTS idx_locations_partner ON locations(partner_id);

-- Прямая привязка партнёра к станции (override)
ALTER TABLE stations ADD COLUMN IF NOT EXISTS partner_id VARCHAR REFERENCES partners(id);
CREATE INDEX IF NOT EXISTS idx_stations_partner ON stations(partner_id);

-- Миграция существующих данных из косвенной связи (stations.user_id → partners.user_id)
UPDATE stations s SET partner_id = p.id::text
FROM partners p
WHERE s.user_id = p.user_id::text AND p.status = 'active' AND s.partner_id IS NULL;

-- Наследование: если все станции локации имеют одного партнёра → присвоить локации
UPDATE locations l SET partner_id = sub.partner_id
FROM (
    SELECT s.location_id, s.partner_id
    FROM stations s
    WHERE s.partner_id IS NOT NULL
    GROUP BY s.location_id, s.partner_id
    HAVING COUNT(DISTINCT s.partner_id) = 1
) sub
WHERE l.id = sub.location_id AND l.partner_id IS NULL;
