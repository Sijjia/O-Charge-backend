-- 018_equipment_catalog.sql
-- Справочник оборудования ЭЗС: производители → модели → характеристики
-- Используется для автоподстановки при создании/редактировании станций

-- ========== Таблицы ==========

CREATE TABLE IF NOT EXISTS equipment_manufacturers (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name VARCHAR(200) NOT NULL UNIQUE,
    name_cn VARCHAR(200),
    country VARCHAR(100),
    website VARCHAR(500),
    logo_url VARCHAR(500),
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS equipment_models (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
    manufacturer_id VARCHAR NOT NULL REFERENCES equipment_manufacturers(id),
    name VARCHAR(200) NOT NULL,
    type VARCHAR(10) NOT NULL DEFAULT 'DC',
    power_kw FLOAT,
    connector_types TEXT[] DEFAULT '{}',
    num_connectors INTEGER,
    voltage_range VARCHAR(50),
    image_url VARCHAR(500),
    price_min_usd INTEGER,
    price_max_usd INTEGER,
    ocpp_versions TEXT[] DEFAULT '{1.6}',
    ip_rating VARCHAR(10),
    dimensions VARCHAR(100),
    weight_kg FLOAT,
    operating_temp VARCHAR(50),
    efficiency_percent FLOAT,
    display_size VARCHAR(50),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(manufacturer_id, name)
);

-- Опциональная привязка станции к модели из справочника
ALTER TABLE stations ADD COLUMN IF NOT EXISTS equipment_model_id VARCHAR
    REFERENCES equipment_models(id);

-- ========== Seed: Производители ==========

INSERT INTO equipment_manufacturers (id, name, name_cn, country, website) VALUES
('mfr-star-charge',   'Star Charge',       '星星充电',     'Китай',       'https://www.star-charge.com'),
('mfr-teld',          'TELD',              '特来电',       'Китай',       'https://www.teld.cn'),
('mfr-autel',         'Autel Energy',      '道通科技',     'Китай',       'https://www.autelenergy.com'),
('mfr-enplus',        'EN+',               '恩加智能',     'Китай',       'https://www.enplus-tech.com'),
('mfr-sinexcel',      'Sinexcel',          '英威腾能源',   'Китай',       'https://www.sinexcel.com'),
('mfr-setec',         'SETEC Power',       '思迪科',       'Китай',       'https://www.setec-power.com'),
('mfr-byd',           'BYD',               '比亚迪',       'Китай',       'https://www.byd.com'),
('mfr-huawei',        'Huawei',            '华为',         'Китай',       'https://www.huawei.com'),
('mfr-uugreenpower',  'UUGreenPower',      '优优绿能',     'Китай',       'https://www.uugreenpower.com'),
('mfr-tonhe',         'Tonhe',             '通合科技',     'Китай',       'https://www.tonhe.net'),
('mfr-abb',           'ABB',               NULL,           'Швейцария',   'https://www.abb.com'),
('mfr-kempower',      'Kempower',          NULL,           'Финляндия',   'https://www.kempower.com'),
('mfr-schneider',     'Schneider Electric', NULL,          'Франция',     'https://www.se.com'),
('mfr-wallbox',       'Wallbox',           NULL,           'Испания',     'https://www.wallbox.com')
ON CONFLICT (id) DO NOTHING;

-- ========== Seed: Модели ==========

-- 1. Star Charge (6 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-sc-titan-360',   'mfr-star-charge', 'Titan 360',        'DC', 360,  '{CCS2,CHAdeMO}',     2, '200-1000V', 18000, 25000, '{1.6,2.0.1}', 'IP54', '-30~55°C'),
('mdl-sc-titan-240',   'mfr-star-charge', 'Titan 240',        'DC', 240,  '{CCS2,CHAdeMO}',     2, '200-1000V', 14000, 19000, '{1.6,2.0.1}', 'IP54', '-30~55°C'),
('mdl-sc-titan-120',   'mfr-star-charge', 'Titan 120',        'DC', 120,  '{CCS2,CHAdeMO}',     2, '200-750V',  8000,  12000, '{1.6,2.0.1}', 'IP54', '-30~55°C'),
('mdl-sc-venus-60',    'mfr-star-charge', 'Venus 60',         'DC', 60,   '{CCS2}',             1, '200-750V',  5000,  7500,  '{1.6}',       'IP54', '-30~50°C'),
('mdl-sc-mercury-22',  'mfr-star-charge', 'Mercury 22',       'AC', 22,   '{Type2}',            1, '230V',      800,   1200,  '{1.6}',       'IP54', '-30~50°C'),
('mdl-sc-mercury-7',   'mfr-star-charge', 'Mercury 7',        'AC', 7,    '{Type2}',            1, '230V',      400,   600,   '{1.6}',       'IP54', '-25~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 2. TELD (6 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-teld-dc-480',    'mfr-teld', 'SuperCharger 480',  'DC', 480,  '{CCS2,CHAdeMO}',     4, '200-1000V', 30000, 45000, '{1.6,2.0.1}', 'IP55', '-35~55°C'),
('mdl-teld-dc-360',    'mfr-teld', 'SuperCharger 360',  'DC', 360,  '{CCS2,CHAdeMO}',     2, '200-1000V', 20000, 28000, '{1.6,2.0.1}', 'IP55', '-35~55°C'),
('mdl-teld-dc-240',    'mfr-teld', 'SuperCharger 240',  'DC', 240,  '{CCS2,CHAdeMO}',     2, '200-1000V', 14000, 20000, '{1.6,2.0.1}', 'IP55', '-35~55°C'),
('mdl-teld-dc-120',    'mfr-teld', 'FastCharger 120',   'DC', 120,  '{CCS2,CHAdeMO}',     2, '200-750V',  8000,  12000, '{1.6}',       'IP54', '-30~55°C'),
('mdl-teld-dc-60',     'mfr-teld', 'SmartCharger 60',   'DC', 60,   '{CCS2}',             1, '200-750V',  4500,  7000,  '{1.6}',       'IP54', '-30~50°C'),
('mdl-teld-ac-22',     'mfr-teld', 'WallCharger 22',    'AC', 22,   '{Type2}',            1, '230V',      700,   1100,  '{1.6}',       'IP54', '-25~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 3. Autel Energy (7 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-autel-mc-600',   'mfr-autel', 'MaxiCharger DC 600',   'DC', 600,  '{CCS2,CHAdeMO}',     4, '150-1000V', 40000, 60000, '{1.6,2.0.1}', 'IP55', '-35~55°C'),
('mdl-autel-mc-480',   'mfr-autel', 'MaxiCharger DC 480',   'DC', 480,  '{CCS2,CHAdeMO}',     3, '150-1000V', 28000, 42000, '{1.6,2.0.1}', 'IP55', '-35~55°C'),
('mdl-autel-mc-240',   'mfr-autel', 'MaxiCharger DC 240',   'DC', 240,  '{CCS2,CHAdeMO}',     2, '150-1000V', 15000, 22000, '{1.6,2.0.1}', 'IP55', '-30~55°C'),
('mdl-autel-mc-160',   'mfr-autel', 'MaxiCharger DC 160',   'DC', 160,  '{CCS2,CHAdeMO}',     2, '150-920V',  10000, 15000, '{1.6,2.0.1}', 'IP55', '-30~55°C'),
('mdl-autel-mc-80',    'mfr-autel', 'MaxiCharger DC Compact','DC', 80,  '{CCS2}',             1, '150-920V',  6000,  9000,  '{1.6}',       'IP55', '-30~55°C'),
('mdl-autel-ac-22',    'mfr-autel', 'MaxiCharger AC Elite',  'AC', 22,  '{Type2}',            1, '230V',      900,   1400,  '{1.6}',       'IP66', '-30~55°C'),
('mdl-autel-ac-11',    'mfr-autel', 'MaxiCharger AC Home',   'AC', 11,  '{Type2}',            1, '230V',      500,   800,   '{1.6}',       'IP66', '-30~55°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 4. EN+ (7 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-enp-dc-600',     'mfr-enplus', 'EN+ DC 600',          'DC', 600,  '{CCS2,CHAdeMO}',     4, '200-1000V', 35000, 55000, '{1.6,2.0.1}', 'IP55', '-35~60°C'),
('mdl-enp-dc-360',     'mfr-enplus', 'EN+ DC 360',          'DC', 360,  '{CCS2,CHAdeMO}',     2, '200-1000V', 18000, 26000, '{1.6,2.0.1}', 'IP55', '-35~60°C'),
('mdl-enp-dc-240',     'mfr-enplus', 'EN+ DC 240',          'DC', 240,  '{CCS2,CHAdeMO}',     2, '200-1000V', 13000, 19000, '{1.6,2.0.1}', 'IP55', '-35~55°C'),
('mdl-enp-dc-120',     'mfr-enplus', 'EN+ DC 120',          'DC', 120,  '{CCS2,CHAdeMO}',     2, '200-750V',  8000,  12000, '{1.6}',       'IP55', '-30~55°C'),
('mdl-enp-dc-60',      'mfr-enplus', 'EN+ DC 60',           'DC', 60,   '{CCS2}',             1, '200-750V',  4500,  7000,  '{1.6}',       'IP54', '-30~50°C'),
('mdl-enp-ac-22',      'mfr-enplus', 'EN+ AC 22',           'AC', 22,   '{Type2}',            1, '230V',      700,   1100,  '{1.6}',       'IP65', '-30~55°C'),
('mdl-enp-ac-7',       'mfr-enplus', 'EN+ AC 7',            'AC', 7,    '{Type2}',            1, '230V',      350,   550,   '{1.6}',       'IP65', '-25~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 5. Sinexcel (5 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-sin-dc-480',     'mfr-sinexcel', 'SPC 480',            'DC', 480,  '{CCS2,CHAdeMO}',     4, '200-1000V', 28000, 40000, '{1.6,2.0.1}', 'IP55', '-35~55°C'),
('mdl-sin-dc-240',     'mfr-sinexcel', 'SPC 240',            'DC', 240,  '{CCS2,CHAdeMO}',     2, '200-1000V', 14000, 20000, '{1.6,2.0.1}', 'IP55', '-30~55°C'),
('mdl-sin-dc-120',     'mfr-sinexcel', 'SPC 120',            'DC', 120,  '{CCS2}',             2, '200-750V',  7500,  11000, '{1.6}',       'IP54', '-30~55°C'),
('mdl-sin-dc-60',      'mfr-sinexcel', 'SPC 60',             'DC', 60,   '{CCS2}',             1, '200-750V',  4000,  6500,  '{1.6}',       'IP54', '-30~50°C'),
('mdl-sin-ac-22',      'mfr-sinexcel', 'SPC AC 22',          'AC', 22,   '{Type2}',            1, '230V',      700,   1000,  '{1.6}',       'IP54', '-25~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 6. SETEC Power (6 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-set-dc-360',     'mfr-setec', 'Power 360',           'DC', 360,  '{CCS2,CHAdeMO}',     2, '200-1000V', 18000, 26000, '{1.6,2.0.1}', 'IP54', '-30~55°C'),
('mdl-set-dc-240',     'mfr-setec', 'Power 240',           'DC', 240,  '{CCS2,CHAdeMO}',     2, '200-1000V', 13000, 18000, '{1.6,2.0.1}', 'IP54', '-30~55°C'),
('mdl-set-dc-120',     'mfr-setec', 'Power 120',           'DC', 120,  '{CCS2,CHAdeMO}',     2, '200-750V',  7500,  11000, '{1.6}',       'IP54', '-30~55°C'),
('mdl-set-dc-60',      'mfr-setec', 'Power 60',            'DC', 60,   '{CCS2}',             1, '200-750V',  4500,  7000,  '{1.6}',       'IP54', '-30~50°C'),
('mdl-set-ac-22',      'mfr-setec', 'Home AC 22',          'AC', 22,   '{Type2}',            1, '230V',      650,   1000,  '{1.6}',       'IP54', '-25~50°C'),
('mdl-set-ac-7',       'mfr-setec', 'Home AC 7',           'AC', 7,    '{Type2}',            1, '230V',      300,   500,   '{1.6}',       'IP54', '-25~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 7. BYD (6 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-byd-dc-480',     'mfr-byd', 'BYD DC 480',          'DC', 480,  '{CCS2,CHAdeMO}',     4, '200-1000V', 30000, 45000, '{1.6,2.0.1}', 'IP55', '-30~55°C'),
('mdl-byd-dc-240',     'mfr-byd', 'BYD DC 240',          'DC', 240,  '{CCS2,CHAdeMO}',     2, '200-1000V', 15000, 22000, '{1.6,2.0.1}', 'IP55', '-30~55°C'),
('mdl-byd-dc-120',     'mfr-byd', 'BYD DC 120',          'DC', 120,  '{CCS2,CHAdeMO}',     2, '200-750V',  8000,  12000, '{1.6}',       'IP55', '-30~55°C'),
('mdl-byd-dc-60',      'mfr-byd', 'BYD DC 60',           'DC', 60,   '{CCS2}',             1, '200-750V',  5000,  7500,  '{1.6}',       'IP54', '-30~50°C'),
('mdl-byd-ac-22',      'mfr-byd', 'BYD AC 22',           'AC', 22,   '{Type2}',            1, '230V',      800,   1200,  '{1.6}',       'IP55', '-25~50°C'),
('mdl-byd-ac-7',       'mfr-byd', 'BYD AC 7',            'AC', 7,    '{Type2}',            1, '230V',      350,   550,   '{1.6}',       'IP54', '-25~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 8. Huawei (5 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-hw-dc-600',      'mfr-huawei', 'FusionCharge 600',   'DC', 600,  '{CCS2,CHAdeMO}',     6, '200-1000V', 45000, 65000, '{1.6,2.0.1}', 'IP55', '-35~60°C'),
('mdl-hw-dc-360',      'mfr-huawei', 'FusionCharge 360',   'DC', 360,  '{CCS2,CHAdeMO}',     2, '200-1000V', 22000, 32000, '{1.6,2.0.1}', 'IP55', '-35~60°C'),
('mdl-hw-dc-240',      'mfr-huawei', 'FusionCharge 240',   'DC', 240,  '{CCS2,CHAdeMO}',     2, '200-1000V', 15000, 22000, '{1.6,2.0.1}', 'IP55', '-35~55°C'),
('mdl-hw-dc-120',      'mfr-huawei', 'SmartCharge 120',    'DC', 120,  '{CCS2}',             2, '200-750V',  9000,  14000, '{1.6}',       'IP55', '-30~55°C'),
('mdl-hw-ac-22',       'mfr-huawei', 'SmartCharge AC 22',  'AC', 22,   '{Type2}',            1, '230V',      900,   1400,  '{1.6}',       'IP65', '-30~55°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 9. UUGreenPower (5 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-uu-dc-360',      'mfr-uugreenpower', 'UU DC 360',        'DC', 360,  '{CCS2,CHAdeMO}',     2, '200-1000V', 17000, 24000, '{1.6,2.0.1}', 'IP54', '-30~55°C'),
('mdl-uu-dc-240',      'mfr-uugreenpower', 'UU DC 240',        'DC', 240,  '{CCS2,CHAdeMO}',     2, '200-1000V', 12000, 17000, '{1.6,2.0.1}', 'IP54', '-30~55°C'),
('mdl-uu-dc-120',      'mfr-uugreenpower', 'UU DC 120',        'DC', 120,  '{CCS2}',             2, '200-750V',  7000,  10000, '{1.6}',       'IP54', '-30~50°C'),
('mdl-uu-dc-60',       'mfr-uugreenpower', 'UU DC 60',         'DC', 60,   '{CCS2}',             1, '200-750V',  4000,  6000,  '{1.6}',       'IP54', '-30~50°C'),
('mdl-uu-ac-22',       'mfr-uugreenpower', 'UU AC 22',         'AC', 22,   '{Type2}',            1, '230V',      600,   900,   '{1.6}',       'IP54', '-25~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 10. Tonhe (4 модели)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-ton-dc-360',     'mfr-tonhe', 'TH-DC 360',          'DC', 360,  '{CCS2,CHAdeMO}',     2, '200-1000V', 16000, 23000, '{1.6,2.0.1}', 'IP54', '-30~55°C'),
('mdl-ton-dc-240',     'mfr-tonhe', 'TH-DC 240',          'DC', 240,  '{CCS2,CHAdeMO}',     2, '200-1000V', 12000, 17000, '{1.6,2.0.1}', 'IP54', '-30~55°C'),
('mdl-ton-dc-120',     'mfr-tonhe', 'TH-DC 120',          'DC', 120,  '{CCS2}',             2, '200-750V',  7000,  10000, '{1.6}',       'IP54', '-30~50°C'),
('mdl-ton-ac-22',      'mfr-tonhe', 'TH-AC 22',           'AC', 22,   '{Type2}',            1, '230V',      600,   900,   '{1.6}',       'IP54', '-25~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 11. ABB (7 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-abb-terra-360',  'mfr-abb', 'Terra 360',           'DC', 360,  '{CCS2,CHAdeMO}',     4, '150-920V',  40000, 55000, '{1.6,2.0.1}', 'IP55', '-35~50°C'),
('mdl-abb-terra-184',  'mfr-abb', 'Terra HP 184',        'DC', 184,  '{CCS2,CHAdeMO}',     2, '150-920V',  18000, 26000, '{1.6,2.0.1}', 'IP55', '-35~50°C'),
('mdl-abb-terra-124',  'mfr-abb', 'Terra 124',           'DC', 124,  '{CCS2,CHAdeMO}',     2, '150-920V',  12000, 18000, '{1.6,2.0.1}', 'IP55', '-35~50°C'),
('mdl-abb-terra-54',   'mfr-abb', 'Terra 54',            'DC', 54,   '{CCS2}',             1, '150-920V',  7000,  11000, '{1.6}',       'IP55', '-35~50°C'),
('mdl-abb-terra-24',   'mfr-abb', 'Terra AC 24',         'AC', 24,   '{Type2}',            1, '400V',      1200,  1800,  '{1.6}',       'IP54', '-35~50°C'),
('mdl-abb-terra-11',   'mfr-abb', 'Terra AC 11',         'AC', 11,   '{Type2}',            1, '230V',      800,   1200,  '{1.6}',       'IP54', '-35~50°C'),
('mdl-abb-evlunic-22', 'mfr-abb', 'EVLunic Pro 22',      'AC', 22,   '{Type2}',            1, '400V',      1000,  1500,  '{1.6}',       'IP54', '-30~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 12. Kempower (4 модели)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-kp-s-600',       'mfr-kempower', 'S-Series 600',      'DC', 600,  '{CCS2,CHAdeMO}',     6, '50-1000V',  50000, 75000, '{1.6,2.0.1}', 'IP55', '-40~50°C'),
('mdl-kp-s-240',       'mfr-kempower', 'S-Series 240',      'DC', 240,  '{CCS2,CHAdeMO}',     2, '50-1000V',  25000, 35000, '{1.6,2.0.1}', 'IP55', '-40~50°C'),
('mdl-kp-t-400',       'mfr-kempower', 'T-Series 400',      'DC', 400,  '{CCS2,CHAdeMO}',     4, '50-1000V',  35000, 50000, '{1.6,2.0.1}', 'IP55', '-40~50°C'),
('mdl-kp-c-50',        'mfr-kempower', 'C-Series 50',       'DC', 50,   '{CCS2}',             1, '50-1000V',  8000,  12000, '{1.6}',       'IP55', '-40~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 13. Schneider Electric (4 модели)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-se-evlink-240',  'mfr-schneider', 'EVlink Fast 240',   'DC', 240,  '{CCS2,CHAdeMO}',     2, '150-920V',  20000, 30000, '{1.6,2.0.1}', 'IP55', '-30~50°C'),
('mdl-se-evlink-120',  'mfr-schneider', 'EVlink Fast 120',   'DC', 120,  '{CCS2,CHAdeMO}',     2, '150-920V',  12000, 18000, '{1.6}',       'IP55', '-30~50°C'),
('mdl-se-evlink-22',   'mfr-schneider', 'EVlink Pro AC 22',  'AC', 22,   '{Type2}',            2, '400V',      1500,  2200,  '{1.6}',       'IP55', '-30~50°C'),
('mdl-se-evlink-7',    'mfr-schneider', 'EVlink Home 7',     'AC', 7,    '{Type2}',            1, '230V',      500,   800,   '{1.6}',       'IP54', '-25~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- 14. Wallbox (6 моделей)
INSERT INTO equipment_models (id, manufacturer_id, name, type, power_kw, connector_types, num_connectors, voltage_range, price_min_usd, price_max_usd, ocpp_versions, ip_rating, operating_temp) VALUES
('mdl-wb-hyper-350',   'mfr-wallbox', 'Hypernova 350',     'DC', 350,  '{CCS2,CHAdeMO}',     2, '150-1000V', 30000, 45000, '{1.6,2.0.1}', 'IP55', '-30~50°C'),
('mdl-wb-supernova',   'mfr-wallbox', 'Supernova 150',     'DC', 150,  '{CCS2,CHAdeMO}',     2, '150-920V',  14000, 20000, '{1.6,2.0.1}', 'IP55', '-30~50°C'),
('mdl-wb-supernova-65','mfr-wallbox', 'Supernova 65',      'DC', 65,   '{CCS2}',             1, '150-920V',  7000,  10000, '{1.6}',       'IP55', '-30~50°C'),
('mdl-wb-copper-22',   'mfr-wallbox', 'Copper SB 22',      'AC', 22,   '{Type2}',            1, '400V',      1100,  1700,  '{1.6}',       'IP55', '-25~50°C'),
('mdl-wb-pulsar-22',   'mfr-wallbox', 'Pulsar Plus 22',    'AC', 22,   '{Type2}',            1, '230V',      700,   1000,  '{1.6}',       'IP54', '-25~50°C'),
('mdl-wb-pulsar-7',    'mfr-wallbox', 'Pulsar Plus 7',     'AC', 7,    '{Type2}',            1, '230V',      400,   600,   '{1.6}',       'IP54', '-25~50°C')
ON CONFLICT (manufacturer_id, name) DO NOTHING;

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_equipment_models_manufacturer ON equipment_models(manufacturer_id);
CREATE INDEX IF NOT EXISTS idx_equipment_models_type ON equipment_models(type);
CREATE INDEX IF NOT EXISTS idx_equipment_models_active ON equipment_models(is_active);
CREATE INDEX IF NOT EXISTS idx_equipment_manufacturers_active ON equipment_manufacturers(is_active);
CREATE INDEX IF NOT EXISTS idx_stations_equipment_model ON stations(equipment_model_id);
