-- 099_demo_data.sql
-- Realistic demo data for Red Petroleum EV presentation (198 locations, ~400 stations, ~500 sessions)
-- Run on VPS: psql -U evpower -d evpower -f 099_demo_data.sql

-- Each section runs independently (no single transaction wrapper)

-- ============================================
-- 0. Corporate tables (if not exist)
-- ============================================
CREATE TABLE IF NOT EXISTS corporate_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name VARCHAR(200) NOT NULL,
    inn VARCHAR(20),
    legal_address TEXT,
    contact_person VARCHAR(100),
    contact_phone VARCHAR(20),
    contact_email VARCHAR(100),
    balance DECIMAL(12,2) DEFAULT 0,
    credit_limit DECIMAL(12,2) DEFAULT 0,
    monthly_limit DECIMAL(12,2),
    billing_type VARCHAR(20) DEFAULT 'prepaid',
    current_month_spent DECIMAL(12,2) DEFAULT 0,
    current_month_start DATE DEFAULT (date_trunc('month', CURRENT_DATE))::date,
    contract_number VARCHAR(50),
    contract_date DATE,
    contract_expires DATE,
    is_active BOOLEAN DEFAULT true,
    blocked_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_corporate_groups_inn ON corporate_groups(inn);
CREATE INDEX IF NOT EXISTS idx_corporate_groups_active ON corporate_groups(is_active);

CREATE TABLE IF NOT EXISTS corporate_employees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corporate_group_id UUID NOT NULL REFERENCES corporate_groups(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    role VARCHAR(20) DEFAULT 'employee',
    position VARCHAR(100),
    monthly_limit DECIMAL(12,2),
    daily_limit DECIMAL(12,2),
    current_month_spent DECIMAL(12,2) DEFAULT 0,
    current_day_spent DECIMAL(12,2) DEFAULT 0,
    last_day_reset DATE DEFAULT CURRENT_DATE,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(corporate_group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_corp_employees_group ON corporate_employees(corporate_group_id);
CREATE INDEX IF NOT EXISTS idx_corp_employees_user ON corporate_employees(user_id);

CREATE TABLE IF NOT EXISTS corporate_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corporate_group_id UUID NOT NULL REFERENCES corporate_groups(id),
    invoice_number VARCHAR(50) UNIQUE NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    total_amount DECIMAL(12,2) NOT NULL,
    total_energy_kwh DECIMAL(12,3) NOT NULL,
    sessions_count INTEGER NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    due_date DATE NOT NULL,
    paid_at TIMESTAMPTZ,
    pdf_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_corp_invoices_group ON corporate_invoices(corporate_group_id);
CREATE INDEX IF NOT EXISTS idx_corp_invoices_status ON corporate_invoices(status);

CREATE TABLE IF NOT EXISTS corporate_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corporate_group_id UUID NOT NULL REFERENCES corporate_groups(id),
    type VARCHAR(20) NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    balance_before DECIMAL(12,2) NOT NULL,
    balance_after DECIMAL(12,2) NOT NULL,
    charging_session_id UUID,
    invoice_id UUID REFERENCES corporate_invoices(id),
    employee_id UUID REFERENCES corporate_employees(id),
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_corp_transactions_group ON corporate_transactions(corporate_group_id);
CREATE INDEX IF NOT EXISTS idx_corp_transactions_date ON corporate_transactions(created_at);

-- ============================================
-- 1. Clean up old demo data (order matters for FK constraints)
-- ============================================

-- Payment transactions referencing demo sessions
DELETE FROM payment_transactions_odengi WHERE charging_session_id IN (
    SELECT id FROM charging_sessions WHERE station_id LIKE 'st-%'
);

-- OCPP transactions referencing demo sessions/stations
DELETE FROM ocpp_transactions WHERE station_id LIKE 'st-%';
DELETE FROM ocpp_meter_values WHERE station_id LIKE 'st-%';
DELETE FROM ocpp_station_status WHERE station_id LIKE 'st-%';

-- Sessions
DELETE FROM charging_sessions WHERE station_id LIKE 'st-%';

-- Partner_stations link table
DO $$ BEGIN
    DELETE FROM partner_stations WHERE station_id LIKE 'st-%';
EXCEPTION WHEN undefined_table THEN NULL;
END $$;

-- Bookings
DELETE FROM bookings WHERE station_id LIKE 'st-%';

-- Connectors (CASCADE would handle, but explicit is safer)
DELETE FROM connectors WHERE station_id LIKE 'st-%';

-- Stations
DELETE FROM stations WHERE id LIKE 'st-%';

-- Locations
DELETE FROM locations WHERE id LIKE 'loc-%';

-- Partners
DELETE FROM partners WHERE id LIKE 'partner-demo-%';

-- ============================================
-- 2. Partner users + partners
-- ============================================

-- Ensure partner users exist in users table (role=operator since 'partner' not in DB enum)
INSERT INTO users (id, email, hashed_password, role, is_active) VALUES
('test-partner-001', 'partner1@redpetroleum.kg', 'not-a-real-hash', 'operator', true),
('test-partner-002', 'partner2@redpetroleum.kg', 'not-a-real-hash', 'operator', true),
('test-partner-003', 'partner3@redpetroleum.kg', 'not-a-real-hash', 'operator', true),
('test-partner-004', 'partner4@redpetroleum.kg', 'not-a-real-hash', 'operator', true),
('test-partner-005', 'partner5@redpetroleum.kg', 'not-a-real-hash', 'operator', true),
('api-client-bester', 'api@bester.kg', 'not-a-real-hash', 'admin', true)
ON CONFLICT (id) DO NOTHING;

-- Ensure partner users exist in clients table (for dev-login compatibility)
INSERT INTO clients (id, name, phone, balance, status) VALUES
('test-partner-001', 'Партнёр ЭлектроДрайв', '+996555000002', 0, 'active'),
('test-partner-002', 'Партнёр ГринЧардж',    '+996555000003', 0, 'active'),
('test-partner-003', 'Партнёр Барс Энерджи',  '+996555000004', 0, 'active'),
('test-partner-004', 'Партнёр Омурбеков',     '+996555000005', 0, 'active'),
('test-partner-005', 'Партнёр ЭкоСтанция',    '+996555000006', 0, 'active')
ON CONFLICT (id) DO NOTHING;

-- Create partner records
INSERT INTO partners (id, user_id, company_name, inn, contract_number, contract_date, revenue_share_percent, contact_name, contact_phone, contact_email, status) VALUES
('partner-demo-001', 'test-partner-001', 'ОсОО ЭлектроДрайв',  '11111111111111', 'RP-2025-P-001', '2025-01-15', 75.00, 'Алмаз Касымов',    '+996555000002', 'info@electrodrive.kg', 'active'),
('partner-demo-002', 'test-partner-002', 'ОсОО ГринЧардж',     '22222222222222', 'RP-2025-P-002', '2025-03-01', 80.00, 'Нурбек Жумабаев',  '+996555000003', 'info@greencharge.kg',  'active'),
('partner-demo-003', 'test-partner-003', 'ОсОО Барс Энерджи',  '33333333333333', 'RP-2025-P-003', '2025-04-10', 70.00, 'Бакыт Токтосунов', '+996555000004', 'info@barsenergy.kg',   'active'),
('partner-demo-004', 'test-partner-004', 'ИП Омурбеков',       '44444444444444', 'RP-2025-P-004', '2025-06-20', 85.00, 'Эрлан Омурбеков',  '+996555000005', 'erlan@omurbekov.kg',   'active'),
('partner-demo-005', 'test-partner-005', 'ОсОО ЭкоСтанция',    '55555555555555', 'RP-2025-P-005', '2025-08-01', 78.00, 'Гулнара Сатыбалдиева', '+996555000006', 'info@ecostation.kg', 'active')
ON CONFLICT (id) DO NOTHING;

-- ============================================
-- 3. Locations (198), Stations (~400), Connectors
-- ============================================
DO $$
DECLARE
    -- City config arrays (parallel arrays)
    v_cities TEXT[] := ARRAY[
        'Бишкек','Ош','Жалал-Абад','Каракол','Токмок',
        'Балыкчы','Нарын','Талас','Кара-Балта','Кызыл-Кия',
        'Узген','Чолпон-Ата','Кант','Бостери','Кемин',
        'Трасса Бишкек-Ош','Трасса Бишкек-Каракол','Трасса Бишкек-Нарын'
    ];
    v_counts INT[] := ARRAY[
        65, 25, 15, 12, 8,
        6, 6, 5, 5, 4,
        3, 5, 4, 3, 3,
        12, 10, 7
    ];
    v_base_lat FLOAT[] := ARRAY[
        42.87, 40.53, 41.03, 42.49, 42.76,
        42.46, 41.43, 42.52, 42.81, 40.26,
        40.77, 42.65, 42.89, 42.65, 42.79,
        40.70, 42.60, 41.90
    ];
    v_base_lon FLOAT[] := ARRAY[
        74.59, 72.80, 73.00, 78.39, 75.30,
        76.19, 76.00, 72.24, 73.85, 72.13,
        73.30, 77.08, 74.85, 77.32, 75.69,
        73.50, 76.80, 75.80
    ];
    v_spread FLOAT[] := ARRAY[
        0.05, 0.03, 0.02, 0.02, 0.01,
        0.01, 0.02, 0.01, 0.01, 0.01,
        0.01, 0.01, 0.01, 0.01, 0.01,
        0.50, 0.40, 0.30
    ];

    -- Station models (parallel arrays)
    v_models TEXT[] := ARRAY[
        'Terra 54 CJG',
        'Terra 184',
        'EVlink City',
        'EVlink Parking',
        'VersiCharge AC',
        'MaxiCharger DC',
        'AC Max',
        'FusionCharge 120'
    ];
    v_manufacturers TEXT[] := ARRAY[
        'ABB',
        'ABB',
        'Schneider',
        'Schneider',
        'Siemens',
        'Autel',
        'Delta',
        'Huawei'
    ];
    v_vendors TEXT[] := ARRAY[
        'ABB',
        'ABB',
        'Schneider Electric',
        'Schneider Electric',
        'Siemens',
        'Autel',
        'Delta Electronics',
        'Huawei'
    ];
    v_powers FLOAT[] := ARRAY[
        50.0,
        180.0,
        22.0,
        22.0,
        22.0,
        150.0,
        22.0,
        120.0
    ];
    -- connector_types per model (as TEXT, we parse later)
    v_conn_types TEXT[][] := ARRAY[
        ARRAY['CCS2','CHAdeMO'],
        ARRAY['CCS2','CCS2'],
        ARRAY['Type2','_none_'],
        ARRAY['Type2','Type2'],
        ARRAY['Type2','_none_'],
        ARRAY['CCS2','CCS2'],
        ARRAY['Type2','Type2'],
        ARRAY['CCS2','CCS2']
    ];
    v_conn_counts INT[] := ARRAY[2, 2, 1, 2, 1, 2, 2, 2];
    v_tariffs FLOAT[] := ARRAY[14.0, 16.0, 10.0, 10.0, 10.0, 15.0, 10.0, 14.0];

    -- Partners array for assignment
    v_partner_ids TEXT[] := ARRAY['partner-demo-001','partner-demo-002','partner-demo-003','partner-demo-004','partner-demo-005'];
    v_partner_shares FLOAT[] := ARRAY[75.0, 80.0, 70.0, 85.0, 78.0];

    -- Counters
    v_loc_num INT := 0;
    v_station_num INT := 0;
    v_loc_id TEXT;
    v_loc_name TEXT;
    v_loc_address TEXT;
    v_lat FLOAT;
    v_lon FLOAT;
    v_city TEXT;
    v_loc_partner_id TEXT;

    v_st_id TEXT;
    v_serial TEXT;
    v_model_idx INT;
    v_stations_at_loc INT;
    v_st_partner_id TEXT;
    v_conn_id TEXT;
    v_conn_type TEXT;
    v_conn_num INT;
    v_power FLOAT;

    v_city_idx INT;
    v_city_count INT;
    v_partner_counter INT := 0; -- counts partner locations (total ~60)
    v_partner_idx INT;
    v_override_counter INT := 0; -- counts partner override stations

    -- Address prefixes for variety
    v_bishkek_streets TEXT[] := ARRAY[
        'пр. Чуй', 'ул. Ахунбаева', 'ул. Киевская', 'ул. Московская',
        'пр. Молодая Гвардия', 'ул. Жибек Жолу', 'ул. Токтогула', 'ул. Советская',
        'ул. Абдрахманова', 'ул. Юнусалиева', 'ул. Медерова', 'пр. Мира',
        'ул. Байтик Баатыра', 'ул. Льва Толстого', 'ул. Фрунзе', 'ул. Анкара',
        'ул. Исанова', 'ул. Боконбаева', 'ул. Тыныстанова', 'ул. Горького',
        'ул. Манаса', 'ул. Панфилова', 'ул. Раззакова', 'ул. Суюмбаева',
        'ул. Шопокова', 'ул. Коенкозова', 'ул. Токомбаева', 'пр. Жибек Жолу',
        'ул. Курманжан Датки', 'ул. Чокморова'
    ];

BEGIN
    -- Loop over each city
    FOR v_city_idx IN 1..array_length(v_cities, 1) LOOP
        v_city := v_cities[v_city_idx];
        v_city_count := v_counts[v_city_idx];

        FOR j IN 1..v_city_count LOOP
            v_loc_num := v_loc_num + 1;
            v_loc_id := 'loc-' || lpad(v_loc_num::text, 3, '0');
            v_loc_name := 'АЗС Red Petroleum #' || lpad(v_loc_num::text, 3, '0');

            -- Coordinates with random spread
            v_lat := v_base_lat[v_city_idx] + (random() * 2 - 1) * v_spread[v_city_idx];
            v_lon := v_base_lon[v_city_idx] + (random() * 2 - 1) * v_spread[v_city_idx];

            -- Address
            IF v_city = 'Бишкек' THEN
                v_loc_address := v_bishkek_streets[1 + floor(random() * array_length(v_bishkek_streets, 1))::int % array_length(v_bishkek_streets, 1)]
                    || ' ' || (1 + floor(random() * 250))::int::text;
            ELSIF v_city LIKE 'Трасса%' THEN
                v_loc_address := v_city || ', км ' || (10 + floor(random() * 300))::int::text;
            ELSE
                v_loc_address := 'ул. Центральная ' || (1 + floor(random() * 150))::int::text;
            END IF;

            -- Partner assignment: ~30% locations get a partner (every ~3rd location after first 2)
            v_loc_partner_id := NULL;
            IF v_loc_num > 5 AND (v_loc_num % 3 = 0) AND v_partner_counter < 60 THEN
                v_partner_counter := v_partner_counter + 1;
                v_partner_idx := 1 + ((v_partner_counter - 1) % 5);
                v_loc_partner_id := v_partner_ids[v_partner_idx];
            END IF;

            -- Use actual city name for highway locations display
            INSERT INTO locations (id, name, address, city, latitude, longitude, user_id, partner_id)
            VALUES (v_loc_id, v_loc_name, v_loc_address,
                    CASE WHEN v_city LIKE 'Трасса%' THEN v_city ELSE v_city END,
                    ROUND(v_lat::numeric, 6), ROUND(v_lon::numeric, 6),
                    'test-admin-001', v_loc_partner_id)
            ON CONFLICT (id) DO NOTHING;

            -- Generate 1-3 stations per location
            v_stations_at_loc := 1 + floor(random() * 3)::int;
            -- Ensure first 30 locations get at least 2 stations for density
            IF v_loc_num <= 30 THEN
                v_stations_at_loc := 2 + floor(random() * 2)::int;
            END IF;

            FOR k IN 1..v_stations_at_loc LOOP
                v_station_num := v_station_num + 1;
                v_st_id := 'st-' || lpad(v_station_num::text, 3, '0');
                v_serial := 'RP-' || UPPER(LEFT(REPLACE(v_city, ' ', ''), 3)) || '-' || lpad(v_station_num::text, 4, '0');

                -- Pick random station model
                v_model_idx := 1 + floor(random() * array_length(v_models, 1))::int;
                IF v_model_idx > array_length(v_models, 1) THEN v_model_idx := array_length(v_models, 1); END IF;

                -- Station partner: inherit from location by default
                v_st_partner_id := v_loc_partner_id;

                -- ~10 stations get their OWN partner override (different from location)
                IF v_loc_partner_id IS NULL AND v_override_counter < 10 AND v_station_num % 40 = 0 THEN
                    v_override_counter := v_override_counter + 1;
                    v_st_partner_id := v_partner_ids[1 + (v_override_counter % 5)];
                END IF;

                v_power := v_powers[v_model_idx];

                INSERT INTO stations (
                    id, serial_number, model, manufacturer, location_id,
                    power_capacity, connector_types, status, user_id, connectors_count,
                    price_per_kwh, tariff_plan_id,
                    partner_id
                ) VALUES (
                    v_st_id,
                    v_serial,
                    v_models[v_model_idx],
                    v_manufacturers[v_model_idx],
                    v_loc_id,
                    v_power,
                    CASE v_model_idx
                        WHEN 1 THEN '{CCS2,CHAdeMO}'::text[]
                        WHEN 2 THEN '{CCS2,CCS2}'::text[]
                        WHEN 3 THEN '{Type2}'::text[]
                        WHEN 4 THEN '{Type2,Type2}'::text[]
                        WHEN 5 THEN '{Type2}'::text[]
                        WHEN 6 THEN '{CCS2,CCS2}'::text[]
                        WHEN 7 THEN '{Type2,Type2}'::text[]
                        WHEN 8 THEN '{CCS2,CCS2}'::text[]
                    END,
                    CASE
                        WHEN random() < 0.05 THEN 'maintenance'::station_status
                        WHEN random() < 0.03 THEN 'inactive'::station_status
                        ELSE 'active'::station_status
                    END,
                    'test-admin-001',
                    v_conn_counts[v_model_idx],
                    v_tariffs[v_model_idx],
                    'test-tariff-001',
                    v_st_partner_id
                )
                ON CONFLICT (id) DO NOTHING;

                -- Generate connectors for this station
                FOR c IN 1..v_conn_counts[v_model_idx] LOOP
                    v_conn_id := 'c-' || lpad(v_station_num::text, 3, '0') || '-' || c::text;
                    v_conn_type := v_conn_types[v_model_idx][c];
                    IF v_conn_type = '_none_' THEN
                        CONTINUE;
                    END IF;

                    INSERT INTO connectors (id, station_id, connector_number, status, connector_type, power_kw)
                    VALUES (v_conn_id, v_st_id, c, 'available', v_conn_type, v_power)
                    ON CONFLICT (id) DO NOTHING;
                END LOOP;

            END LOOP; -- stations per location
        END LOOP; -- locations in city
    END LOOP; -- cities

    RAISE NOTICE 'Created % locations, % stations with connectors', v_loc_num, v_station_num;
END $$;

-- ============================================
-- 4. Clients (20 users with Kyrgyz names)
-- ============================================
INSERT INTO clients (id, name, phone, balance, status) VALUES
('cl-001', 'Азамат Токтоналиев',  '+996700111001', 2500.00, 'active'),
('cl-002', 'Айгуль Мамбетова',    '+996555222002', 1800.00, 'active'),
('cl-003', 'Бекболот Асанов',     '+996772333003', 3200.00, 'active'),
('cl-004', 'Гулнура Жумабекова',  '+996550444004', 950.00,  'active'),
('cl-005', 'Данияр Кожобеков',    '+996703555005', 4100.00, 'active'),
('cl-006', 'Элнура Шаршенова',    '+996559666006', 1200.00, 'active'),
('cl-007', 'Жаныбек Турусбеков',  '+996771777007', 6500.00, 'active'),
('cl-008', 'Зарина Абдыкеримова', '+996550888008', 800.00,  'active'),
('cl-009', 'Ислам Бегалиев',      '+996776999009', 2100.00, 'active'),
('cl-010', 'Камила Осмонова',     '+996312110010', 15000.00,'active'),
('cl-011', 'Лейла Байболотова',   '+996707111011', 3800.00, 'active'),
('cl-012', 'Марат Сыдыков',       '+996556112012', 700.00,  'active'),
('cl-013', 'Нурай Касымова',      '+996773113013', 2900.00, 'active'),
('cl-014', 'Орозбек Тилеков',     '+996505114014', 1500.00, 'active'),
('cl-015', 'Перизат Эралиева',    '+996501115015', 5200.00, 'active'),
-- Corporate employees (will be linked to corporate group)
('c0c00001-0001-4000-a000-000000000001', 'Руслан Алымбеков',   '+996700100001', 0, 'active'),
('c0c00001-0002-4000-a000-000000000002', 'Айжан Суйунбекова',  '+996700100002', 0, 'active'),
('c0c00001-0003-4000-a000-000000000003', 'Тимур Жолдошев',     '+996700100003', 0, 'active'),
-- More regular users
('cl-016', 'Самат Калмурзаев',    '+996555116016', 1100.00, 'active'),
('cl-017', 'Умут Арзыматова',     '+996772117017', 4500.00, 'active')
ON CONFLICT (id) DO NOTHING;

-- ============================================
-- 5. Corporate Groups
-- ============================================
INSERT INTO corporate_groups (id, company_name, inn, legal_address, contact_person, contact_phone, contact_email, balance, credit_limit, monthly_limit, billing_type, current_month_spent, contract_number, contract_date, contract_expires, is_active) VALUES
('a1b2c3d4-0001-4000-a000-000000000001', 'ОсОО Электромобиль KG',    '01234567890123', 'г. Бишкек, ул. Киевская 77',       'Руслан Алымбеков',   '+996700100001', 'fleet@elektromobil.kg',  45000.00,     0, 100000.00, 'prepaid',  8400.00, 'RP-2026-CORP-001', '2025-06-01', '2027-06-01', true),
('a1b2c3d4-0002-4000-a000-000000000002', 'ОсОО Азия Карго',          '02345678901234', 'г. Бишкек, ул. Льва Толстого 14',  'Бегмат Сулайманов',  '+996312555002', 'ev@asiacargo.kg',            0,  30000.00,  50000.00, 'postpaid', 12100.00, 'RP-2026-CORP-002', '2025-07-01', '2027-07-01', true),
('a1b2c3d4-0003-4000-a000-000000000003', 'ГП Авиация Кыргызстана',   '03456789012345', 'г. Бишкек, пр. Манас 12',          'Аскар Бакиров',      '+996312555003', 'transport@ka.kg',       120000.00,     0, 200000.00, 'prepaid',  31200.00, 'RP-2026-CORP-003', '2025-08-01', '2027-08-01', true)
ON CONFLICT (id) DO NOTHING;

-- ============================================
-- 6. Corporate Employees
-- ============================================
INSERT INTO corporate_employees (id, corporate_group_id, user_id, role, position, monthly_limit, daily_limit, current_month_spent, is_active) VALUES
-- ОсОО Электромобиль KG
('e0000001-0001-4000-a000-000000000001', 'a1b2c3d4-0001-4000-a000-000000000001', 'c0c00001-0001-4000-a000-000000000001', 'admin',    'Директор',              NULL, NULL,  2800.00, true),
('e0000001-0002-4000-a000-000000000002', 'a1b2c3d4-0001-4000-a000-000000000001', 'c0c00001-0002-4000-a000-000000000002', 'employee', 'Менеджер по логистике',  15000.00, 3000.00, 3200.00, true),
('e0000001-0003-4000-a000-000000000003', 'a1b2c3d4-0001-4000-a000-000000000001', 'c0c00001-0003-4000-a000-000000000003', 'employee', 'Водитель',              10000.00, 2000.00, 2400.00, true)
ON CONFLICT (corporate_group_id, user_id) DO NOTHING;

-- ============================================
-- 7. Charging Sessions (~500 sessions over last 60 days)
-- ============================================
DO $$
DECLARE
    v_session_id TEXT;
    v_client_id TEXT;
    v_station_id TEXT;
    v_connector_id INT;
    v_start TIMESTAMPTZ;
    v_stop TIMESTAMPTZ;
    v_energy FLOAT;
    v_amount FLOAT;
    v_tariff FLOAT;
    v_duration INT; -- minutes
    v_day INT;
    v_hour INT;
    v_partner_id TEXT;
    v_partner_share DECIMAL;
    v_platform_share DECIMAL;
    v_partner_pct FLOAT;

    v_clients TEXT[] := ARRAY[
        'cl-001','cl-002','cl-003','cl-004','cl-005',
        'cl-006','cl-007','cl-008','cl-009','cl-010',
        'cl-011','cl-012','cl-013','cl-014','cl-015',
        'cl-016','cl-017',
        'c0c00001-0001-4000-a000-000000000001',
        'c0c00001-0002-4000-a000-000000000002',
        'c0c00001-0003-4000-a000-000000000003'
    ];

    v_st_count INT;
    v_cidx INT;
    v_st_num INT;
    v_power FLOAT;
    v_st_tariff FLOAT;
BEGIN
    -- Get max sequential station number (st-001..st-NNN, excluding st-pXX overrides)
    SELECT MAX(substring(id FROM 'st-(\d+)')::int) INTO v_st_count FROM stations WHERE id ~ '^st-\d+$';
    IF v_st_count IS NULL OR v_st_count = 0 THEN
        RAISE NOTICE 'No stations found, skipping session generation';
        RETURN;
    END IF;

    FOR i IN 1..500 LOOP
        -- Pick random station number (1 to v_st_count) among sequential IDs only
        v_st_num := 1 + floor(random() * v_st_count)::int;
        IF v_st_num > v_st_count THEN v_st_num := v_st_count; END IF;
        v_station_id := 'st-' || lpad(v_st_num::text, 3, '0');

        -- Pick random client
        v_cidx := 1 + floor(random() * array_length(v_clients, 1))::int;
        IF v_cidx > array_length(v_clients, 1) THEN v_cidx := array_length(v_clients, 1); END IF;
        v_client_id := v_clients[v_cidx];

        v_connector_id := 1;

        -- Get station power and tariff
        SELECT s.power_capacity, s.price_per_kwh
        INTO v_power, v_st_tariff
        FROM stations s WHERE s.id = v_station_id;

        IF v_power IS NULL THEN
            v_power := 22.0;
            v_st_tariff := 12.0;
        END IF;
        v_tariff := COALESCE(v_st_tariff, 12.0);

        -- Random day in last 60 days, random hour 6-23
        v_day := floor(random() * 60)::int;
        v_hour := 6 + floor(random() * 17)::int;
        v_start := NOW() - (v_day || ' days')::interval
                        - (24 - v_hour || ' hours')::interval
                        + (floor(random() * 59) || ' minutes')::interval;

        -- Duration: DC fast=15-60 min, AC=30-180 min
        IF v_power >= 50 THEN
            v_duration := 15 + floor(random() * 45)::int;
        ELSE
            v_duration := 30 + floor(random() * 150)::int;
        END IF;
        v_stop := v_start + (v_duration || ' minutes')::interval;

        -- Energy based on power and duration (60-90% efficiency)
        v_energy := ROUND((v_power * (v_duration / 60.0) * (0.6 + random() * 0.3))::numeric, 2);
        -- Cap energy at reasonable values
        IF v_energy > 80 THEN v_energy := 30 + random() * 50; END IF;

        v_amount := ROUND((v_energy * v_tariff)::numeric, 2);

        -- Partner share: look up from station or location
        v_partner_id := NULL;
        v_partner_share := NULL;
        v_platform_share := NULL;

        SELECT COALESCE(s.partner_id, l.partner_id)
        INTO v_partner_id
        FROM stations s
        LEFT JOIN locations l ON l.id = s.location_id
        WHERE s.id = v_station_id;

        IF v_partner_id IS NOT NULL THEN
            -- Look up actual revenue share percent
            SELECT p.revenue_share_percent INTO v_partner_pct
            FROM partners p WHERE p.id = v_partner_id;
            v_partner_pct := COALESCE(v_partner_pct, 75.0);
            v_partner_share := ROUND((v_amount * v_partner_pct / 100.0)::numeric, 2);
            v_platform_share := ROUND((v_amount * (100.0 - v_partner_pct) / 100.0)::numeric, 2);
        END IF;

        v_session_id := gen_random_uuid()::text;

        INSERT INTO charging_sessions (
            id, user_id, station_id, connector_id, start_time, stop_time,
            energy, amount, status, tariff_rate, final_amount, payment_processed,
            partner_id, partner_share, platform_share
        ) VALUES (
            v_session_id, v_client_id, v_station_id, v_connector_id, v_start, v_stop,
            v_energy, v_amount, 'stopped', v_tariff, v_amount, true,
            v_partner_id, v_partner_share, v_platform_share
        );

        -- Create matching payment transaction
        INSERT INTO payment_transactions_odengi (
            client_id, transaction_type, amount, balance_before, balance_after,
            charging_session_id, description, created_at
        ) VALUES (
            v_client_id, 'charge_payment', v_amount,
            1000 + random() * 5000, 1000 + random() * 4000,
            v_session_id, 'Зарядка на ' || v_station_id,
            v_stop
        );
    END LOOP;

    RAISE NOTICE 'Created 500 demo charging sessions with payment transactions';
END $$;

-- ============================================
-- 8. Balance topups for some clients
-- ============================================
DELETE FROM balance_topups WHERE order_id LIKE 'ORD-DEMO-%';
DO $$
DECLARE
    v_clients TEXT[] := ARRAY['cl-001','cl-002','cl-003','cl-005','cl-007','cl-010','cl-011','cl-015','cl-017'];
    v_amounts DECIMAL[] := ARRAY[500, 1000, 2000, 3000, 5000];
    v_cid TEXT;
    v_amt DECIMAL;
    v_day INT;
BEGIN
    FOR i IN 1..30 LOOP
        v_cid := v_clients[1 + floor(random() * array_length(v_clients, 1))::int];
        IF v_cid IS NULL THEN v_cid := v_clients[1]; END IF;
        v_amt := v_amounts[1 + floor(random() * array_length(v_amounts, 1))::int];
        IF v_amt IS NULL THEN v_amt := 1000; END IF;
        v_day := floor(random() * 30)::int;

        INSERT INTO balance_topups (
            invoice_id, order_id, client_id, requested_amount, paid_amount,
            currency, status, odengi_status, payment_provider,
            created_at, paid_at, completed_at, description
        ) VALUES (
            'INV-DEMO-' || lpad(i::text, 6, '0'),
            'ORD-DEMO-' || lpad(i::text, 6, '0'),
            v_cid, v_amt, v_amt,
            'KGS', 'approved', 0, 'odengi',
            NOW() - (v_day || ' days')::interval,
            NOW() - (v_day || ' days')::interval + interval '2 minutes',
            NOW() - (v_day || ' days')::interval + interval '3 minutes',
            'Пополнение баланса через О!Деньги'
        );
    END LOOP;

    RAISE NOTICE 'Created 30 demo balance topups';
END $$;

-- ============================================
-- 9. Corporate invoices
-- ============================================
INSERT INTO corporate_invoices (id, corporate_group_id, invoice_number, period_start, period_end, total_amount, total_energy_kwh, sessions_count, status, due_date, paid_at) VALUES
-- ОсОО Электромобиль KG
('f0000001-0001-4000-b000-000000000001', 'a1b2c3d4-0001-4000-a000-000000000001', 'INV-CORP-2026-01-001', '2026-01-01', '2026-01-31', 12500.00, 890.5, 42, 'paid',   '2026-02-15', '2026-02-10 10:00:00+06'),
('f0000001-0002-4000-b000-000000000002', 'a1b2c3d4-0001-4000-a000-000000000001', 'INV-CORP-2026-02-001', '2026-02-01', '2026-02-28',  8400.00, 620.3, 35, 'paid',   '2026-03-15', '2026-03-01 09:00:00+06'),
-- ОсОО Азия Карго
('f0000001-0003-4000-b000-000000000003', 'a1b2c3d4-0002-4000-a000-000000000002', 'INV-CORP-2026-01-002', '2026-01-01', '2026-01-31', 18200.00, 1340.2, 67, 'paid',   '2026-02-15', '2026-02-12 14:00:00+06'),
('f0000001-0004-4000-b000-000000000004', 'a1b2c3d4-0002-4000-a000-000000000002', 'INV-CORP-2026-02-002', '2026-02-01', '2026-02-28', 12100.00, 920.1, 48, 'pending', '2026-03-15', NULL),
-- ГП Авиация Кыргызстана
('f0000001-0005-4000-b000-000000000005', 'a1b2c3d4-0003-4000-a000-000000000003', 'INV-CORP-2026-01-003', '2026-01-01', '2026-01-31', 42000.00, 2800.5, 120, 'paid',   '2026-02-15', '2026-02-08 11:00:00+06'),
('f0000001-0006-4000-b000-000000000006', 'a1b2c3d4-0003-4000-a000-000000000003', 'INV-CORP-2026-02-003', '2026-02-01', '2026-02-28', 31200.00, 2150.8,  95, 'pending', '2026-03-15', NULL)
ON CONFLICT (id) DO NOTHING;

-- ============================================
-- 10. Update partner_stations link (legacy)
-- ============================================
DO $$
BEGIN
    CREATE TABLE IF NOT EXISTS partner_stations (
        partner_id VARCHAR NOT NULL REFERENCES partners(id),
        station_id VARCHAR NOT NULL REFERENCES stations(id),
        PRIMARY KEY (partner_id, station_id)
    );

    -- Link all partner-owned stations to partner_stations table
    INSERT INTO partner_stations (partner_id, station_id)
    SELECT s.partner_id, s.id
    FROM stations s
    WHERE s.partner_id IS NOT NULL AND s.id LIKE 'st-%'
    ON CONFLICT DO NOTHING;

    -- Also link via location inheritance
    INSERT INTO partner_stations (partner_id, station_id)
    SELECT l.partner_id, s.id
    FROM stations s
    JOIN locations l ON l.id = s.location_id
    WHERE l.partner_id IS NOT NULL AND s.partner_id IS NULL AND s.id LIKE 'st-%'
    ON CONFLICT DO NOTHING;

EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'partner_stations setup: %', SQLERRM;
END $$;

-- ============================================
-- 11. Update location station/connector counts
-- ============================================
UPDATE locations l SET
    stations_count = sub.sc,
    connectors_count = sub.cc
FROM (
    SELECT
        s.location_id,
        COUNT(DISTINCT s.id) as sc,
        COALESCE(SUM(s.connectors_count), 0) as cc
    FROM stations s
    WHERE s.id LIKE 'st-%'
    GROUP BY s.location_id
) sub
WHERE l.id = sub.location_id;

-- ============================================
-- Final summary
-- ============================================
DO $$
DECLARE
    v_count BIGINT;
BEGIN
    SELECT COUNT(*) INTO v_count FROM locations WHERE id LIKE 'loc-%';
    RAISE NOTICE 'Locations: %', v_count;
    SELECT COUNT(*) INTO v_count FROM stations WHERE id LIKE 'st-%';
    RAISE NOTICE 'Stations: %', v_count;
    SELECT COUNT(*) INTO v_count FROM connectors WHERE station_id LIKE 'st-%';
    RAISE NOTICE 'Connectors: %', v_count;
    SELECT COUNT(*) INTO v_count FROM clients;
    RAISE NOTICE 'Clients: %', v_count;
    SELECT COUNT(*) INTO v_count FROM charging_sessions WHERE station_id LIKE 'st-%';
    RAISE NOTICE 'Charging sessions: %', v_count;
    SELECT COUNT(*) INTO v_count FROM payment_transactions_odengi;
    RAISE NOTICE 'Payment transactions: %', v_count;
    SELECT COUNT(*) INTO v_count FROM partners;
    RAISE NOTICE 'Partners: %', v_count;
    SELECT COUNT(*) INTO v_count FROM corporate_groups;
    RAISE NOTICE 'Corporate groups: %', v_count;
    SELECT COUNT(*) INTO v_count FROM corporate_employees;
    RAISE NOTICE 'Corporate employees: %', v_count;
    SELECT COUNT(*) INTO v_count FROM corporate_invoices;
    RAISE NOTICE 'Corporate invoices: %', v_count;
    SELECT COUNT(*) INTO v_count FROM balance_topups;
    RAISE NOTICE 'Balance topups: %', v_count;
    SELECT COUNT(*) INTO v_count FROM locations WHERE partner_id IS NOT NULL;
    RAISE NOTICE 'Partner locations: %', v_count;
    SELECT COUNT(*) INTO v_count FROM stations WHERE partner_id IS NOT NULL AND id LIKE 'st-%';
    RAISE NOTICE 'Partner stations (direct): %', v_count;
END $$;
