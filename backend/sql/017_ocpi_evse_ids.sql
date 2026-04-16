-- 017: OCPI EVSE ID — стандартизированные идентификаторы станций
-- Формат: KG*RPE*E{location_seq:3}{station_seq:2}
-- Коннектор добавляется на уровне отображения: KG*RPE*E04301*2

-- location_seq — порядковый номер локации для OCPI ID
ALTER TABLE locations ADD COLUMN IF NOT EXISTS location_seq INTEGER;

-- Проставить существующим локациям
WITH numbered AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY created_at) AS seq
    FROM locations WHERE location_seq IS NULL
)
UPDATE locations l SET location_seq = n.seq FROM numbered n WHERE l.id = n.id;

-- Последовательность для новых локаций
CREATE SEQUENCE IF NOT EXISTS location_seq_counter START 1;
SELECT setval('location_seq_counter', COALESCE((SELECT MAX(location_seq) FROM locations), 0));

-- station_seq — порядковый номер станции в рамках локации
ALTER TABLE stations ADD COLUMN IF NOT EXISTS station_seq INTEGER;

WITH numbered AS (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY location_id ORDER BY created_at) AS seq
    FROM stations WHERE station_seq IS NULL
)
UPDATE stations s SET station_seq = n.seq FROM numbered n WHERE s.id = n.id;

-- evse_id — отображаемый OCPI код (KG*RPE*E{loc3}{st2})
ALTER TABLE stations ADD COLUMN IF NOT EXISTS evse_id VARCHAR(30);

UPDATE stations s SET evse_id =
    'KG*RPE*E' || LPAD(COALESCE(l.location_seq,0)::text,3,'0') || LPAD(COALESCE(s.station_seq,0)::text,2,'0')
FROM locations l WHERE s.location_id = l.id AND s.evse_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_stations_evse_id ON stations(evse_id);
